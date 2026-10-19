/* c6xcore.c -- the C674x DSP core of emu/c6x/cpu.py + isa.py, in C.
 *
 * Same machine model, cycle for cycle (TI SPRUFE8B; see cpu.py docstring):
 *   - an execute packet issues in one cycle; members read before writes;
 *     results land at the END of cycle issue+w;
 *   - loads read memory at the end of issue+3, register at issue+4;
 *     stores reach memory at the end of issue+2; within a cycle: stores,
 *     then loads, then register/control writes;
 *   - branches redirect pc at the end of issue+5 (several in flight);
 *     a landing branch ends a multi-cycle NOP;
 *   - maskable interrupts INT4..15 at EP boundaries;
 *   - SPLOOP/SPLOOPD/SPLOOPW loop buffer as isa.SPLoop.
 * Instructions are pre-decoded by emu/c6x/cfast.py into Ins records.
 * Anything not handled here is O_py: C calls back into Python, which runs
 * the isa.py handler against a proxy of this core (rare instructions).
 * cfast.py reads the OP(...) names below in order (one source of truth).
 */
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>

typedef uint32_t u32;
typedef int32_t s32;
typedef uint64_t u64;
typedef int64_t s64;

#define OPS \
 OP(py) OP(nop) OP(mvk) OP(mvkh) OP(mv) OP(mvpair) OP(dmv) OP(zero) OP(zeropair) \
 OP(mvc_to) OP(mvc_from) OP(add) OP(sub) OP(addk) OP(adda) OP(suba) \
 OP(and) OP(or) OP(xor) OP(andn) OP(sadd) OP(ssub) OP(add2) OP(sub2) \
 OP(pack2) OP(packh2) OP(packhl2) OP(packlh2) OP(packl4) OP(packh4) \
 OP(neg) OP(not) OP(abs) OP(cmp) OP(shl) OP(shr) OP(shru) OP(sshl) \
 OP(ext) OP(extu) OP(set) OP(clr) OP(lmbd) OP(norm) OP(mpy16) OP(mpy32) \
 OP(mpy32p) OP(ld) OP(st) OP(b) OP(bnop) OP(callp) OP(addkpc) OP(bdec) \
 OP(sploop) OP(spctl) OP(dint) OP(rint) OP(idle) \
 OP(fadd) OP(fsub) OP(fmul) OP(intsp) OP(spint) OP(spdp) OP(dpsp) \
 OP(dadd) OP(dsub) OP(dmul) OP(fcmp) OP(dcmp) OP(abssp) OP(rcpsp) OP(swap2) \
 OP(mvd)

#define OP(n) O_##n,
enum { OPS O_COUNT };
#undef OP

/* pre-decoded instruction; layout mirrored by cfast.Ins (ctypes) */
typedef struct {
    u32 addr;
    s32 k[4];
    u32 py;            /* Python fallback id */
    uint16_t op;
    uint8_t size, par, header, prot;
    int8_t creg;       /* predicate register or -1 */
    uint8_t cneg;
    uint8_t unit;      /* 0..7 L1 L2 S1 S2 M1 M2 D1 D2, 255 none */
    uint8_t side;      /* 1 A, 2 B, 0 none */
    uint8_t w;         /* write delay */
    uint8_t sub;       /* op variant */
    uint8_t ra, rb, rd, rah, rbh, rdh;
    uint8_t ca, cb;    /* srcA/srcB constant (k0/k1) */
    uint8_t msz, mmode, mbase, mofk, moff, mscale, msign, malign;
    uint8_t ctrl;
    uint8_t spmask;    /* SPMASK unit bits */
    uint8_t spk;       /* 1 spmask, 2 spkernel, 3 nested-reload forms */
    uint8_t pad;
} Ins;

enum { M_PLAIN, M_PADD, M_PSUB, M_PREINC, M_PREDEC, M_POSTINC, M_POSTDEC };

enum { C_AMR, C_CSR, C_IFR, C_ISR, C_ICR, C_IER, C_ISTP, C_IRP, C_NRP, C_ILC,
       C_RILC, C_SSR, C_TSR, C_ITSR, C_NTSR, C_ECR, C_EFR, C_IERR, C_REP,
       C_TSCL, C_TSCH, C_DNUM, C_GFPGFR, C_GPLYA, C_GPLYB, C_FADCR, C_FAUCR,
       C_FMCR, C_DIER, C_PCE1, C_NCTRL };

enum { E_ST, E_LD, E_REG, E_CTRL, E_PYLD };
typedef struct { uint8_t type, size, lo, hi, sx; u32 addr; u64 val; } Ev;
#define RING 16
#define EVMAX 128
typedef struct { int n; Ev e[EVMAX]; } Bucket;

typedef u32 (*mmio_rd_t)(u32 addr, int size);
typedef void (*mmio_wr_t)(u32 addr, int size, u32 val);
typedef int (*pyop_t)(u32 id, u64 cyc);
typedef void (*pyld_t)(u32 id, u32 lo, u32 hi);
typedef struct { u32 lo; u64 hi; uint8_t *buf; } Region;

#define SPBUF 1024
#define SPLC 1024
typedef struct {
    int kind, ii, wcreg, wcneg, loading, fetch_prog, lc, lbc, dynlen, drain,
        post_delay, pc_after, since, initial_term, prog_stall, refill;
    int resume, int_drain; u32 addr;
    int nbuf;
    int bl[SPBUF], bi[SPBUF], bu[SPBUF];
    uint8_t valid[SPLC];
    int pv[64], npv;
    uint8_t hist[4]; int nhist;
} SPLoop;

enum { K_SPLOOP, K_SPLOOPD, K_SPLOOPW };
#define SPLX (1u << 9)          /* TSR.SPLX */
enum { ST_OK, ST_NEED, ST_ERR, ST_STOP, ST_IDLE };

#define NPEND 16
typedef struct {
    u32 r[64];
    u32 c[C_NCTRL];
    u32 pc;
    u32 idle;
    u64 cycle;
    int stall;
    u64 ep_count, insn_count, interrupts;
    int npend;
    u64 pend_cyc[NPEND];
    u32 pend_tgt[NPEND];
    Bucket ring[RING];
    int evcount;
    int sp_on;
    SPLoop sp;
    Ins *ins;
    int nins, cap;
    u32 *hkey; int32_t *hval; u32 hmask;
    Region reg[16];
    int nreg;
    mmio_rd_t mrd; mmio_wr_t mwr; pyop_t pyop; pyld_t pyld;
    int status;
    u32 need;
    int err; u32 err_pc;
    u64 py_calls;
    int splx_irq; u32 splx_after;
    u32 idle_head, idle_lo, idle_hi; int idle_armed, idle_dirty; u32 idle_r[64];
    u32 wlo, whi; int wn; u32 wpc[256], wad[256], wv[256]; u64 wcy[256];
} Core;

/* ================================================== code table ======== */
static u32 hslot(Core *c, u32 a)
{
    u32 h = (a * 2654435761u) & c->hmask;
    while (c->hkey[h] != 0xFFFFFFFFu && c->hkey[h] != a)
        h = (h + 1) & c->hmask;
    return h;
}

static Ins *find(Core *c, u32 a)
{
    u32 h = hslot(c, a);
    return c->hkey[h] == a ? &c->ins[c->hval[h]] : 0;
}

Core *c6x_new(int hbits)
{
    Core *c = calloc(1, sizeof(Core));
    c->hmask = (1u << hbits) - 1;
    c->hkey = malloc(sizeof(u32) << hbits);
    c->hval = malloc(sizeof(int32_t) << hbits);
    memset(c->hkey, 0xFF, sizeof(u32) << hbits);
    c->c[C_CSR] = 0x14000100;
    return c;
}

void c6x_free(Core *c) { free(c->ins); free(c->hkey); free(c->hval); free(c); }
int c6x_sizeof_ins(void) { return sizeof(Ins); }
int c6x_sizeof_core(void) { return sizeof(Core); }
u32 *c6x_regs(Core *c) { return c->r; }
u32 *c6x_ctrl(Core *c) { return c->c; }

static void rehash(Core *c)
{
    memset(c->hkey, 0xFF, sizeof(u32) * (c->hmask + 1));
    for (int i = 0; i < c->nins; i++) {
        u32 h = hslot(c, c->ins[i].addr);
        c->hkey[h] = c->ins[i].addr;
        c->hval[h] = i;
    }
}

int c6x_add_code(Core *c, const Ins *v, int n)
{
    for (int i = 0; i < n; i++) {
        u32 h = hslot(c, v[i].addr);
        if (c->hkey[h] == v[i].addr) { c->ins[c->hval[h]] = v[i]; continue; }
        if (c->nins == c->cap) {
            c->cap = c->cap ? c->cap * 2 : 65536;
            c->ins = realloc(c->ins, sizeof(Ins) * c->cap);
        }
        if ((u32)(c->nins + 1) * 2 > c->hmask) return -1;
        c->ins[c->nins] = v[i];
        c->hkey[h] = v[i].addr;
        c->hval[h] = c->nins++;
    }
    return c->nins;
}

void c6x_drop_code(Core *c, u32 lo, u32 hi)
{
    int w = 0;
    for (int i = 0; i < c->nins; i++)
        if (!(c->ins[i].addr >= lo && c->ins[i].addr < hi))
            c->ins[w++] = c->ins[i];
    c->nins = w;
    rehash(c);
}

void c6x_clear_code(Core *c) { c->nins = 0; rehash(c); }

/* ================================================== memory ============ */
void c6x_set_cb(Core *c, mmio_rd_t r, mmio_wr_t w, pyop_t p, pyld_t l)
{ c->mrd = r; c->mwr = w; c->pyop = p; c->pyld = l; }

void c6x_add_region(Core *c, u32 lo, u32 size, uint8_t *buf)
{
    c->reg[c->nreg].lo = lo; c->reg[c->nreg].hi = (u64)lo + size;
    c->reg[c->nreg].buf = buf; c->nreg++;
}

static inline uint8_t *ram(Core *c, u32 a, int n)
{
    for (int i = 0; i < c->nreg; i++)
        if (a >= c->reg[i].lo && (u64)a + n <= c->reg[i].hi)
            return c->reg[i].buf + (a - c->reg[i].lo);
    return 0;
}

static u64 mread(Core *c, u32 a, int n)
{
    uint8_t *p = ram(c, a, n);
    if (p) { u64 v = 0; memcpy(&v, p, n); return v; }
    c->idle_dirty = 1;
    if (n == 8) return (u64)c->mrd(a, 4) | (u64)c->mrd(a + 4, 4) << 32;
    return c->mrd(a, n);
}

static void mwrite(Core *c, u32 a, int n, u64 v)
{
    uint8_t *p = ram(c, a, n);
    if (p) {
        u32 g = (a >= 0x00800000u && a < 0x00840000u) ? a + 0x11000000u : a;   /* L2 alias */
        if (!c->idle_dirty && (g < c->idle_lo || g >= c->idle_hi) && memcmp(p, &v, n))
            c->idle_dirty = 1;               /* a real change outside the stack */
        memcpy(p, &v, n);
        return;
    }
    c->idle_dirty = 1;
    if (n == 8) { c->mwr(a, 4, (u32)v); c->mwr(a + 4, 4, (u32)(v >> 32)); return; }
    c->mwr(a, n, (u32)(n == 4 ? v : (v & ((1u << (8 * n)) - 1))));
}

/* ================================================== scheduling ======== */
static Ev *ev_new(Core *c, u64 w)
{
    Bucket *b = &c->ring[w % RING];
    if (b->n == EVMAX) { c->err = 3; c->status = ST_ERR; return &b->e[0]; }
    c->evcount++;
    return &b->e[b->n++];
}

static inline void wr(Core *c, u64 w, int idx, u32 v)
{ Ev *e = ev_new(c, w); e->type = E_REG; e->lo = (uint8_t)idx; e->val = v; }

static inline void wrc(Core *c, u64 w, int idx, u32 v)
{ Ev *e = ev_new(c, w); e->type = E_CTRL; e->lo = (uint8_t)idx; e->val = v; }

static inline void st(Core *c, u64 w, u32 a, int n, u64 v)
{ Ev *e = ev_new(c, w); e->type = E_ST; e->addr = a; e->size = (uint8_t)n; e->val = v; }

static inline void ld(Core *c, u64 issue, u32 a, int n, int lo, int hi, int sx)
{
    Ev *e = ev_new(c, issue + 3);
    e->type = E_LD; e->addr = a; e->size = (uint8_t)n;
    e->lo = (uint8_t)lo; e->hi = (uint8_t)hi; e->sx = (uint8_t)sx;
}

static void branch(Core *c, u64 issue, u32 t)
{
    u64 w = issue + 5;
    for (int i = 0; i < c->npend; i++)
        if (c->pend_cyc[i] == w) { c->pend_tgt[i] = t; return; }
    if (c->npend < NPEND) { c->pend_cyc[c->npend] = w; c->pend_tgt[c->npend++] = t; }
    else { c->err = 4; c->status = ST_ERR; }
}

/* exported for the Python fallback proxy */
void c6x_wr(Core *c, u64 w, int idx, u32 v) { wr(c, w, idx, v); }
void c6x_wrc(Core *c, u64 w, int idx, u32 v) { wrc(c, w, idx, v); }
void c6x_st(Core *c, u64 w, u32 a, int n, u64 v) { st(c, w, a, n, v); }
void c6x_branch(Core *c, u64 issue, u32 t) { branch(c, issue, t); }
void c6x_ldpy(Core *c, u64 issue, u32 a, int n, u32 id)
{
    Ev *e = ev_new(c, issue + 3);
    e->type = E_PYLD; e->addr = a; e->size = (uint8_t)n; e->val = id;
}
void c6x_stop(Core *c) { c->status = ST_STOP; }

static void ctrl_set(Core *c, int i, u32 v)
{
    c->c[i] = v;
    if (i == C_CSR) c->c[C_TSR] = (c->c[C_TSR] & ~1u) | (v & 1);
    if (i == C_TSR) c->c[C_CSR] = (c->c[C_CSR] & ~1u) | (v & 1);
}

static void commit(Core *c, u64 cyc)
{
    Bucket *b = &c->ring[cyc % RING];
    int n = b->n;
    if (!n) return;
    for (int i = 0; i < n; i++)
        if (b->e[i].type == E_ST) {
            Ev *e = &b->e[i];
            if (e->addr >= c->wlo && e->addr < c->whi) {   /* debug store watch */
                int k = c->wn++ & 255;
                c->wpc[k] = c->pc; c->wad[k] = e->addr; c->wv[k] = (u32)e->val; c->wcy[k] = cyc;
            }
            mwrite(c, e->addr, e->size, e->val);
        }
    for (int i = 0; i < n; i++) {
        Ev *e = &b->e[i];
        if (e->type == E_LD) {
            u64 v = mread(c, e->addr, e->size);
            if (e->size == 8) {
                wr(c, cyc + 1, e->lo, (u32)v);
                wr(c, cyc + 1, e->hi, (u32)(v >> 32));
            } else {
                u32 x = (u32)v;
                if (e->sx == 1) x = (u32)(s32)(int8_t)x;
                else if (e->sx == 2) x = (u32)(s32)(int16_t)x;
                wr(c, cyc + 1, e->lo, x);
            }
        } else if (e->type == E_PYLD) {
            u64 v = mread(c, e->addr, e->size);
            c->pyld((u32)e->val, (u32)v, (u32)(v >> 32));
        }
    }
    n = b->n;
    for (int i = 0; i < n; i++) {
        Ev *e = &b->e[i];
        if (e->type == E_REG) c->r[e->lo] = (u32)e->val;
        else if (e->type == E_CTRL) ctrl_set(c, e->lo, (u32)e->val);
    }
    c->evcount -= n;
    b->n = 0;
}

/* ================================================== helpers =========== */
static inline s32 sat32(s64 v)
{ return v > 0x7FFFFFFFLL ? 0x7FFFFFFF : (v < -0x80000000LL ? (s32)0x80000000 : (s32)v); }
static inline u32 h2(u32 lo, u32 hi) { return (lo & 0xFFFF) | (hi << 16); }
static inline int bitlen(u32 v) { return v ? 32 - __builtin_clz(v) : 0; }

static inline int cond(Core *c, int r, int neg)
{
    if (r < 0) return 1;
    return neg ? c->r[r] == 0 : c->r[r] != 0;
}

static inline u32 A(Core *c, const Ins *i) { return i->ca ? (u32)i->k[0] : c->r[i->ra]; }
static inline u32 B(Core *c, const Ins *i) { return i->cb ? (u32)i->k[1] : c->r[i->rb]; }

static inline u32 f2b(float f) { u32 b; memcpy(&b, &f, 4); return b; }
static inline double fin(u32 b)
{
    float f;
    if (((b >> 23) & 0xFF) == 0) return (b >> 31) ? -0.0 : 0.0;
    memcpy(&f, &b, 4);
    return f;
}
static inline u32 fout(double x)
{
    u32 b = f2b((float)x);
    if (((b >> 23) & 0xFF) == 0) b &= 0x80000000u;
    return b;
}
static inline double din(u32 lo, u32 hi)
{
    u64 b = (u64)hi << 32 | lo; double d;
    if (((b >> 52) & 0x7FF) == 0) return (b >> 63) ? -0.0 : 0.0;
    memcpy(&d, &b, 8);
    return d;
}
static inline u64 dout(double x)
{
    u64 b; memcpy(&b, &x, 8);
    if (((b >> 52) & 0x7FF) == 0) b &= 1ULL << 63;
    return b;
}
static u32 to_int(double x, int mode)
{
    if (isnan(x)) return 0x80000000u;
    double r = mode == 0 ? nearbyint(x) : mode == 1 ? trunc(x) : mode == 2 ? ceil(x) : floor(x);
    if (r > 2147483647.0) return 0x7FFFFFFF;
    if (r < -2147483648.0) return 0x80000000u;
    return (u32)(s32)r;
}
static inline int round_mode(Core *c, int side)
{ return (c->c[C_FADCR] >> (side == 2 ? 25 : 9)) & 3; }

/* next instruction address (headers skipped), as cpu._next_addr */
static u32 next_addr(Core *c, u32 a)
{
    Ins *i = find(c, a);
    u32 n = a + (i ? i->size : 4);
    Ins *h = find(c, n);
    while (h && h->header) { n += 4; h = find(c, n); }
    return n;
}

/* ================================================== execute =========== */
static void sp_start(Core *c, const Ins *i);

/* one instruction; returns nop cycles (0 = none). Predicate included. */
static int exec1(Core *c, const Ins *i, u64 cyc)
{
    if (i->op == O_py) {
        c->py_calls++;
        c->idle_dirty = 1;
        return c->pyop(i->py, cyc);
    }
    if (!cond(c, i->creg, i->cneg) && !(i->op == O_sploop && i->sub == K_SPLOOPW)) {
        switch (i->op) {
        case O_bnop: case O_addkpc: return i->k[1] + 1;
        case O_callp: return 6;
        default: return 0;
        }
    }
    u64 w = cyc + i->w;
    switch (i->op) {
    case O_nop: return i->k[0];
    case O_mvk: wr(c, cyc, i->rd, (u32)i->k[0]); return 0;
    case O_mvkh: wr(c, cyc, i->rd, (u32)i->k[0] | (c->r[i->rd] & 0xFFFF)); return 0;
    case O_mv: wr(c, cyc, i->rd, A(c, i)); return 0;
    case O_mvd: wr(c, cyc + 3, i->rd, A(c, i)); return 0;
    case O_mvpair: wr(c, cyc, i->rd, c->r[i->ra]); wr(c, cyc, i->rdh, c->r[i->rah]); return 0;
    case O_dmv: { u32 a = A(c, i), b = B(c, i); wr(c, cyc, i->rdh, a); wr(c, cyc, i->rd, b); return 0; }
    case O_zero: wr(c, cyc, i->rd, 0); return 0;
    case O_zeropair: wr(c, cyc, i->rd, 0); wr(c, cyc, i->rdh, 0); return 0;
    case O_mvc_to: {
        u32 v = c->r[i->ra];
        if (i->ctrl == C_ILC || i->ctrl == C_RILC) wrc(c, cyc + 3, i->ctrl, v);
        else if (i->ctrl == C_ICR) c->c[C_IFR] &= ~v;
        else if (i->ctrl == C_ISR) c->c[C_IFR] |= v;
        else wrc(c, cyc, i->ctrl, v);
        return 0;
    }
    case O_mvc_from: {
        u32 v;
        if (i->ctrl == C_TSCL) { c->idle_dirty = 1; v = (u32)c->cycle; c->c[C_TSCH] = (u32)(c->cycle >> 32); }
        else v = c->c[i->ctrl];
        wr(c, cyc, i->rd, v);
        return 0;
    }
    case O_add: wr(c, w, i->rd, A(c, i) + B(c, i)); return 0;
    case O_sub: wr(c, w, i->rd, A(c, i) - B(c, i)); return 0;
    case O_addk: wr(c, cyc, i->rd, c->r[i->rd] + (u32)i->k[0]); return 0;
    case O_adda: wr(c, cyc, i->rd, A(c, i) + (B(c, i) << i->sub)); return 0;
    case O_suba: wr(c, cyc, i->rd, A(c, i) - (B(c, i) << i->sub)); return 0;
    case O_and: wr(c, w, i->rd, A(c, i) & B(c, i)); return 0;
    case O_or: wr(c, w, i->rd, A(c, i) | B(c, i)); return 0;
    case O_xor: wr(c, w, i->rd, A(c, i) ^ B(c, i)); return 0;
    case O_andn: wr(c, w, i->rd, A(c, i) & ~B(c, i)); return 0;
    case O_sadd: wr(c, w, i->rd, (u32)sat32((s64)(s32)A(c, i) + (s32)B(c, i))); return 0;
    case O_ssub: wr(c, w, i->rd, (u32)sat32((s64)(s32)A(c, i) - (s32)B(c, i))); return 0;
    case O_add2: { u32 a = A(c, i), b = B(c, i); wr(c, w, i->rd, h2(a + b, (a >> 16) + (b >> 16))); return 0; }
    case O_sub2: { u32 a = A(c, i), b = B(c, i); wr(c, w, i->rd, h2(a - b, (a >> 16) - (b >> 16))); return 0; }
    case O_pack2: wr(c, w, i->rd, h2(B(c, i), A(c, i))); return 0;
    case O_packh2: wr(c, w, i->rd, h2(B(c, i) >> 16, A(c, i) >> 16)); return 0;
    case O_packhl2: wr(c, w, i->rd, h2(B(c, i), A(c, i) >> 16)); return 0;
    case O_packlh2: wr(c, w, i->rd, h2(B(c, i) >> 16, A(c, i))); return 0;
    case O_packl4: { u32 a = A(c, i), b = B(c, i);
        wr(c, w, i->rd, (b & 0xFF) | ((b >> 16) & 0xFF) << 8 | (a & 0xFF) << 16 | ((a >> 16) & 0xFF) << 24); return 0; }
    case O_packh4: { u32 a = A(c, i), b = B(c, i);
        wr(c, w, i->rd, ((b >> 8) & 0xFF) | ((b >> 24) & 0xFF) << 8 | ((a >> 8) & 0xFF) << 16 | ((a >> 24) & 0xFF) << 24); return 0; }
    case O_swap2: { u32 a = A(c, i); wr(c, w, i->rd, (a >> 16) | (a << 16)); return 0; }
    case O_neg: wr(c, w, i->rd, (u32)(-(s64)(s32)A(c, i))); return 0;
    case O_not: wr(c, w, i->rd, ~A(c, i)); return 0;
    case O_abs: { s64 v = (s32)A(c, i); wr(c, w, i->rd, (u32)sat32(v < 0 ? -v : v)); return 0; }
    case O_cmp: {   /* sub: 0 eq 1 gt 2 lt 3 gtu 4 ltu */
        u32 a = A(c, i), b = B(c, i); int r;
        switch (i->sub) {
        case 0: r = (s32)a == (s32)b; break;
        case 1: r = (s32)a > (s32)b; break;
        case 2: r = (s32)a < (s32)b; break;
        case 3: r = a > b; break;
        default: r = a < b;
        }
        wr(c, w, i->rd, r);
        return 0;
    }
    case O_shl: { u32 n = B(c, i) & 0x3F, x = A(c, i); wr(c, w, i->rd, n >= 32 ? 0 : x << n); return 0; }
    case O_shr: { u32 n = B(c, i) & 0x3F; s32 x = (s32)A(c, i); wr(c, w, i->rd, (u32)(n >= 32 ? (x >> 31) : (x >> n))); return 0; }
    case O_shru: { u32 n = B(c, i) & 0x3F, x = A(c, i); wr(c, w, i->rd, n >= 32 ? 0 : x >> n); return 0; }
    case O_sshl: {
        u32 n = B(c, i) & 0x3F; s32 v = (s32)A(c, i); u32 r;
        if (v == 0) r = 0;
        else if (n >= 32) r = v < 0 ? 0x80000000u : 0x7FFFFFFF;
        else r = (u32)sat32((s64)v << n);
        wr(c, w, i->rd, r);
        return 0;
    }
    case O_ext: case O_extu: case O_set: case O_clr: {
        u32 a = A(c, i), csta, cstb;
        if (i->cb) { csta = i->k[1] & 31; cstb = i->k[2] & 31; }
        else { u32 b = c->r[i->rb]; csta = (b >> 5) & 31; cstb = b & 31; }
        u32 r;
        if (i->op == O_ext) r = (u32)((s32)(a << csta) >> cstb);
        else if (i->op == O_extu) r = (a << csta) >> cstb;
        else {
            r = a;
            if (cstb >= csta) {
                u32 m = (u32)((((u64)1 << (cstb - csta + 1)) - 1) << csta);
                r = i->op == O_set ? (a | m) : (a & ~m);
            }
        }
        wr(c, w, i->rd, r);
        return 0;
    }
    case O_lmbd: { u32 v = B(c, i); if (!(A(c, i) & 1)) v = ~v; wr(c, w, i->rd, v ? __builtin_clz(v) : 32); return 0; }
    case O_norm: { s32 v = (s32)A(c, i); if (v < 0) v = ~v; wr(c, w, i->rd, v == 0 ? 31 : 31 - bitlen((u32)v)); return 0; }
    case O_mpy16: {   /* sub bits: 1 a-hi, 2 a-unsigned, 4 b-hi, 8 b-unsigned, 16 smpy */
        u32 a = A(c, i), b = B(c, i);
        u32 ah = i->sub & 1 ? a >> 16 : a & 0xFFFF, bh = i->sub & 4 ? b >> 16 : b & 0xFFFF;
        s64 x = i->sub & 2 ? (s64)ah : (s64)(int16_t)ah;
        s64 y = i->sub & 8 ? (s64)bh : (s64)(int16_t)bh;
        s64 p = x * y;
        if (i->sub & 16) { p <<= 1; if (p == 0x80000000LL) p = 0x7FFFFFFF; }
        wr(c, w, i->rd, (u32)p);
        return 0;
    }
    case O_mpy32: case O_mpy32p: {   /* sub: 1 a signed, 2 b signed */
        u32 a = A(c, i), b = B(c, i);
        s64 x = i->sub & 1 ? (s64)(s32)a : (s64)a, y = i->sub & 2 ? (s64)(s32)b : (s64)b;
        u64 p = (u64)x * (u64)y;
        wr(c, cyc + 3, i->rd, (u32)p);
        if (i->op == O_mpy32p) wr(c, cyc + 3, i->rdh, (u32)(p >> 32));
        return 0;
    }
    case O_ld: case O_st: {
        u32 bv = c->r[i->mbase], o = 0, addr, nb = 0; int upd = 0;
        if (i->mofk == 1) o = c->r[i->moff] * i->mscale;
        else if (i->mofk == 2) o = (u32)i->k[2];
        switch (i->mmode) {
        case M_PLAIN: addr = bv; break;
        case M_PADD: addr = bv + o; break;
        case M_PSUB: addr = bv - o; break;
        case M_PREINC: addr = nb = bv + o; upd = 1; break;
        case M_PREDEC: addr = nb = bv - o; upd = 1; break;
        case M_POSTINC: addr = bv; nb = bv + o; upd = 1; break;
        default: addr = bv; nb = bv - o; upd = 1; break;
        }
        if (upd) wr(c, cyc, i->mbase, nb);
        addr &= ~(u32)i->malign;
        if (i->op == O_ld) {
            ld(c, cyc, addr, i->msz, i->rd, i->rdh, i->msign);
            return i->prot ? 5 : 0;
        }
        u64 v = i->msz == 8 ? ((u64)c->r[i->rdh] << 32 | c->r[i->rd])
              : i->msz == 4 ? c->r[i->rd] : (c->r[i->rd] & ((1u << (8 * i->msz)) - 1));
        st(c, cyc + 2, addr, i->msz, v);
        return 0;
    }
    case O_b: case O_bnop: {   /* sub: 0 const k0, 1 reg ra, 2 irp, 3 nrp */
        u32 t = i->sub == 0 ? (u32)i->k[0] : i->sub == 1 ? c->r[i->ra]
              : i->sub == 2 ? c->c[C_IRP] : c->c[C_NRP];
        if (i->sub == 2) {
            u32 g = (c->c[C_CSR] >> 1) & 1;
            c->c[C_CSR] = (c->c[C_CSR] & ~1u) | g;
            c->c[C_TSR] = (c->c[C_ITSR] & ~1u) | g;
        }
        branch(c, cyc, t);
        return i->op == O_bnop && i->k[1] ? i->k[1] + 1 : 0;
    }
    case O_callp: {
        u32 a = i->addr, n;
        for (;;) {
            n = next_addr(c, a);
            Ins *j = find(c, n);
            if (!j || !j->par) break;
            a = n;
        }
        wr(c, cyc, i->rd, n);
        branch(c, cyc, (u32)i->k[0]);
        return 6;
    }
    case O_addkpc: wr(c, cyc, i->rd, (u32)i->k[0]); return i->k[1] ? i->k[1] + 1 : 0;
    case O_bdec: {
        s32 v = (s32)c->r[i->rd];
        if (v >= 0) { branch(c, cyc, (u32)i->k[0]); if (i->sub) wr(c, cyc, i->rd, (u32)(v - 1)); }
        return 0;
    }
    case O_sploop: sp_start(c, i); return 0;
    case O_spctl: return 0;
    case O_dint: { u32 csr = c->c[C_CSR];
        c->c[C_TSR] = (c->c[C_TSR] & ~1u) | (csr & 1); c->c[C_CSR] = csr & ~1u; return 0; }
    case O_rint: c->c[C_CSR] = (c->c[C_CSR] & ~1u) | (c->c[C_TSR] & 1); return 0;
    case O_idle: c->idle = 1; return 0;
    case O_fadd: wr(c, w, i->rd, fout(fin(A(c, i)) + fin(B(c, i)))); return 0;
    case O_fsub: wr(c, w, i->rd, fout(fin(A(c, i)) - fin(B(c, i)))); return 0;
    case O_fmul: wr(c, w, i->rd, fout(fin(A(c, i)) * fin(B(c, i)))); return 0;
    case O_intsp: { u32 a = A(c, i); wr(c, w, i->rd, fout(i->sub ? (double)a : (double)(s32)a)); return 0; }
    case O_spint: wr(c, w, i->rd, to_int(fin(A(c, i)), i->sub ? 1 : round_mode(c, i->side))); return 0;
    case O_spdp: { u64 b = dout(fin(A(c, i))); wr(c, cyc, i->rd, (u32)b); wr(c, cyc + 1, i->rdh, (u32)(b >> 32)); return 0; }
    case O_dpsp: wr(c, cyc + 3, i->rd, fout(din(c->r[i->ra], c->r[i->rah]))); return 0;
    case O_dadd: case O_dsub: case O_dmul: {
        /* sub bit0: src1 single, bit1: src2 single; k2 lo delay, k3 hi delay */
        double x = i->sub & 1 ? fin(A(c, i)) : din(c->r[i->ra], c->r[i->rah]);
        double y = i->sub & 2 ? fin(B(c, i)) : din(c->r[i->rb], c->r[i->rbh]);
        double r = i->op == O_dadd ? x + y : i->op == O_dsub ? x - y : x * y;
        u64 b = dout(r);
        wr(c, cyc + i->k[2], i->rd, (u32)b);
        wr(c, cyc + i->k[3], i->rdh, (u32)(b >> 32));
        return 0;
    }
    case O_fcmp: case O_dcmp: {
        double x, y; int r;
        if (i->op == O_fcmp) { x = fin(A(c, i)); y = fin(B(c, i)); }
        else { x = din(c->r[i->ra], c->r[i->rah]); y = din(c->r[i->rb], c->r[i->rbh]); }
        r = i->sub == 0 ? x == y : i->sub == 1 ? x > y : x < y;
        wr(c, w, i->rd, r);
        return 0;
    }
    case O_abssp: wr(c, cyc, i->rd, fout(fabs(fin(A(c, i))))); return 0;
    case O_rcpsp: {
        double x = fin(A(c, i)), v;
        int special = x == 0 || (i->sub && x < 0);
        if (special) v = x >= 0 ? INFINITY : NAN;
        else v = i->sub ? 1.0 / sqrt(x) : 1.0 / x;
        u32 b = f2b((float)v);
        if (!special) b &= ~0x7FFFu;
        if (((b >> 23) & 0xFF) == 0) b &= 0x80000000u;
        wr(c, cyc, i->rd, b);
        return 0;
    }
    }
    c->err = 5; c->err_pc = i->addr; c->status = ST_ERR;
    return 0;
}

/* ================================================== SPLOOP ============ */
static void sp_start(Core *c, const Ins *i)
{
    SPLoop *s = &c->sp;
    memset(s, 0, sizeof(*s));
    s->kind = i->sub; s->ii = i->k[0];
    s->wcreg = i->sub == K_SPLOOPW ? i->creg : -1; s->wcneg = i->cneg;
    s->loading = s->fetch_prog = 1;
    s->dynlen = s->drain = s->post_delay = s->pc_after = s->refill = -1;
    s->resume = (c->c[C_TSR] & SPLX) != 0;          /* §7.13.2 */
    c->c[C_TSR] |= SPLX;
    if (s->resume && s->kind == K_SPLOOPD) s->kind = K_SPLOOP;
    s->addr = i->addr;
    c->sp_on = 1;
    if (s->kind == K_SPLOOP) {
        if (c->c[C_ILC] == 0) s->initial_term = 1;
        else c->c[C_ILC]--;
    }
}

int c6x_refill = 6;      /* fetch refill after SPLOOP (isa.REFILL) */
void c6x_set_refill(int n) { c6x_refill = n; }

static void sp_exit_fetch(Core *c)
{
    SPLoop *s = &c->sp;
    if (c6x_refill) s->refill = c6x_refill;
    else { s->fetch_prog = 1; c->pc = s->pc_after; }
}

static void sp_leave(Core *c)
{
    SPLoop *s = &c->sp;
    c->sp_on = 0;
    c->c[C_TSR] &= ~SPLX;
    if (!s->fetch_prog) {
        c->pc = s->pc_after;
        int left = s->refill < 0 ? c6x_refill : s->refill;
        if (left > 0) c->stall = left;
    }
}

static int sp_want_int(Core *c)       /* §7.13.1 */
{
    SPLoop *s = &c->sp;
    u32 *r = c->c;
    if (s->loading || s->initial_term) return 0;
    if (!(r[C_CSR] & 1) || !(r[C_IER] & 2) || !(r[C_IFR] & r[C_IER] & 0xFFF0)) return 0;
    if (c->npend) return 0;
    if ((s->kind == K_SPLOOPD || s->kind == K_SPLOOPW) && s->since <= 3) return 0;
    if (s->kind == K_SPLOOPW)
        return !(s->nhist >= 4 && !s->hist[(s->nhist - 4) & 3]);
    int dl = s->dynlen < 0 ? 0 : s->dynlen;
    return (int)r[C_ILC] >= (dl + s->ii - 1) / s->ii;
}

static void sp_boundary(Core *c)
{
    SPLoop *s = &c->sp;
    s->since++;
    if (s->refill >= 0 && !s->fetch_prog) {
        if (--s->refill <= 0) { s->fetch_prog = 1; c->pc = s->pc_after; }
    }
    if (s->drain >= 0) {
        s->drain++;
        if (s->post_delay >= 0 && !s->fetch_prog && !s->int_drain && s->refill < 0 &&
            s->drain >= s->post_delay)
            sp_exit_fetch(c);
        if (s->dynlen >= 0 && s->drain >= s->dynlen - s->ii) {
            memset(s->valid, 0, sizeof s->valid);
            if (s->int_drain) {                     /* §7.13.4 */
                c->sp_on = 0;
                c->c[C_TSR] &= ~SPLX;
                c->pc = s->addr;
                c->splx_irq = 1;
                c->splx_after = (u32)s->pc_after;
                return;
            }
            sp_leave(c);
            return;
        }
    }
    if (s->wcreg >= 0) {
        s->hist[s->nhist & 3] = (uint8_t)cond(c, s->wcreg, s->wcneg);
        s->nhist++;
    }
    if (++s->lbc < s->ii) return;
    s->lbc = 0;
    if (s->initial_term) {
        if (!s->loading) sp_leave(c);
        return;
    }
    if (s->drain >= 0) return;
    if (sp_want_int(c)) { s->drain = 0; s->int_drain = 1; return; }
    if (s->kind == K_SPLOOPW) {
        int term = s->nhist >= 4 ? !s->hist[(s->nhist - 4) & 3] : 0;   /* exit when FALSE (7.10.2) */
        if (term) {
            memset(s->valid, 0, sizeof s->valid);
            if (!s->loading) sp_leave(c);
            else { c->sp_on = 0; c->c[C_TSR] &= ~SPLX; }
        }
        return;
    }
    if (s->kind == K_SPLOOPD && s->since <= 3) return;
    if (c->c[C_ILC] == 0) {
        s->drain = 0;
        if (s->post_delay == 0) sp_exit_fetch(c);
    } else c->c[C_ILC]--;
}

/* execute packet at pc; returns member count (0 = no code: ST_NEED) */
static int fetch_ep(Core *c, u32 pc, Ins **ep, u32 *nxt)
{
    Ins *i = find(c, pc);
    if (i && i->header) { pc += 4; i = find(c, pc); }
    if (!i) { c->need = pc; c->status = ST_NEED; return 0; }
    int n = 0; ep[n++] = i;
    u32 a = pc;
    for (;;) {
        u32 nx = next_addr(c, a);
        Ins *j = find(c, nx);
        if (!j || !j->par || j->header || n == 16) { *nxt = nx; return n; }
        ep[n++] = j;
        a = nx;
    }
}

static int sp_cycle(Core *c, u64 cyc)
{
    SPLoop *s = &c->sp;
    Ins *ep[16]; int nep = 0; u32 nxt = 0, mask = 0;
    if (s->fetch_prog && s->prog_stall == 0) {
        nep = fetch_ep(c, c->pc, ep, &nxt);
        if (!nep) return -1;
        for (int k = 0; k < nep; k++) {
            if (ep[k]->spk == 3) { c->err = 6; c->err_pc = ep[k]->addr; c->status = ST_ERR; return -1; }
            if (ep[k]->spk == 1) mask |= ep[k]->spmask;
        }
        c->pc = nxt;
    } else if (s->prog_stall) s->prog_stall--;
    if (s->drain >= 0 && s->drain < SPLC) s->valid[s->drain] = 0;
    static int bx[SPBUF];
    int nbx = 0;
    for (int k = 0; k < s->nbuf; k++) {
        int lc = s->bl[k];
        if (s->valid[lc] && lc % s->ii == s->lbc &&
            (s->resume || !(s->bu[k] < 8 && (mask >> s->bu[k] & 1))))
            bx[nbx++] = s->bi[k];
    }
    int nop = 1, kernel = -1;
    s->npv = 0;
    for (int k = 0; k < nep; k++) {
        Ins *i = ep[k];
        if (i->spk == 1) continue;
        if (i->spk == 2) { if (s->loading) kernel = k; continue; }
        int masked = (i->unit < 8 && (mask >> i->unit & 1)) ||
                     (i->op == O_bnop && i->sub == 0) || i->op == O_nop;
        if (s->resume && ((i->unit < 8 && (mask >> i->unit & 1)) || (i->op == O_bnop && i->sub == 0))) {
            int n = i->op == O_bnop ? i->k[1] + 1 : 0;      /* §7.13.2 */
            c->insn_count++;
            if (n > nop) nop = n;
            continue;
        }
        int n = (s->initial_term && !masked) ? 0 : exec1(c, i, cyc);
        if (i->op == O_nop) n = i->k[0];
        c->insn_count++;
        if (n > nop) nop = n;
        if (s->loading && !masked && !s->initial_term) {
            if (s->nbuf >= SPBUF || s->npv >= 64 || s->lc >= SPLC) {
                c->err = 7; c->status = ST_ERR; return -1;
            }
            s->bl[s->nbuf] = s->lc; s->bi[s->nbuf] = (int)(i - c->ins);
            s->bu[s->nbuf++] = i->unit;
            s->pv[s->npv++] = s->lc;
        }
    }
    for (int k = 0; k < nbx; k++) { exec1(c, &c->ins[bx[k]], cyc); c->insn_count++; }
    c->ep_count++;
    if (kernel >= 0) {
        Ins *ki = ep[kernel];
        s->loading = 0; s->fetch_prog = 0;
        s->dynlen = s->lc + 1;
        s->pc_after = nxt;
        s->post_delay = ki->k[0] * s->ii + ki->k[1];
    }
    if (nop > 1) { s->prog_stall = nop - 1; c->stall = 0; }
    for (int k = 0; k < s->npv; k++) s->valid[s->pv[k]] = 1;
    s->npv = 0;
    if (s->loading) s->lc++;
    sp_boundary(c);
    return 0;
}

/* ================================================== the machine ======= */
static int take_interrupt(Core *c)
{
    u32 *r = c->c;
    if (!(r[C_CSR] & 1) || !(r[C_IER] & 2) || c->npend || c->stall || c->sp_on || c->evcount)
        return 0;
    u32 p = r[C_IFR] & r[C_IER] & 0xFFF0;
    if (!p) return 0;
    int n = __builtin_ctz(p);
    r[C_IFR] &= ~(1u << n);
    r[C_IRP] = c->pc;
    r[C_CSR] = (r[C_CSR] & ~3u) | ((r[C_CSR] & 1) << 1);
    r[C_ITSR] = r[C_TSR];
    r[C_TSR] &= ~1u;
    c->pc = (r[C_ISTP] & ~0x3FFu) + 32 * n;
    c->interrupts++;
    c->idle_dirty = 1;
    return 1;
}

static int step(Core *c)
{
    u64 cyc = c->cycle;
    if (c->splx_irq) {             /* §7.13.4: writes complete, then the interrupt */
        if (!c->evcount && !c->npend) {
            c->splx_irq = 0;
            if (take_interrupt(c)) c->c[C_ITSR] |= SPLX;
            else c->pc = c->splx_after;
        } else {
            commit(c, cyc);
            c->cycle = cyc + 1;
            return c->status ? -1 : 0;
        }
    }
    if (!c->stall && !c->sp_on && (c->c[C_IFR] & c->c[C_IER] & 0xFFF0))
        take_interrupt(c);
    if (c->stall) c->stall--;
    else if (c->sp_on) { if (sp_cycle(c, cyc) < 0) return -1; }
    else {
        if (c->idle_head && c->pc == c->idle_head) {
            /* busy-wait skip (as cdj-nxs2-qemu): a whole pass of the idle
             * loop that touched no hardware, changed no memory outside its
             * stack and left the registers as they were is a fixed point:
             * nothing changes until an interrupt. */
            int quiet = !c->evcount && !c->npend && !c->splx_irq &&
                        !(c->c[C_IFR] & c->c[C_IER] & 0xFFF0);
            if (c->idle_armed && !c->idle_dirty && quiet &&
                !memcmp(c->idle_r, c->r, sizeof c->r)) {
                c->status = ST_IDLE;
                return -1;
            }
            memcpy(c->idle_r, c->r, sizeof c->r);
            c->idle_armed = quiet;
            c->idle_dirty = 0;
        }
        Ins *ep[16]; u32 nxt;
        int n = fetch_ep(c, c->pc, ep, &nxt);
        if (!n) return -1;
        c->ep_count++;
        int nop = 1;
        if (ep[0]->op == O_sploop && (c->c[C_TSR] & SPLX)) n = 1;   /* §7.13.2 */
        for (int k = 0; k < n; k++) {
            int r = exec1(c, ep[k], cyc);
            c->insn_count++;
            if (r > nop) nop = r;
        }
        if (nop > 1) c->stall = nop - 1;
        c->pc = nxt;
    }
    if (c->status == ST_ERR) return -1;
    commit(c, cyc);
    for (int k = 0; k < c->npend; k++)
        if (c->pend_cyc[k] == cyc) {
            c->pc = c->pend_tgt[k];
            c->npend--;
            c->pend_cyc[k] = c->pend_cyc[c->npend];
            c->pend_tgt[k] = c->pend_tgt[c->npend];
            c->stall = 0;
            if (c->sp_on) c->c[C_TSR] &= ~SPLX;
            c->sp_on = 0;
            break;
        }
    c->cycle = cyc + 1;
    return c->status ? -1 : 0;
}

/* run up to n cycles; returns cycles completed; c6x_status() says why */
s64 c6x_run(Core *c, s64 n)
{
    c->status = ST_OK;
    s64 k = 0;
    while (k < n) {
        if (step(c) < 0) {
            if (c->status == ST_STOP) k++;      /* that cycle completed */
            break;
        }
        k++;
    }
    return k;
}

int c6x_status(Core *c) { return c->status; }
u32 c6x_need(Core *c) { return c->need; }
int c6x_err(Core *c) { return c->err; }
u32 c6x_err_pc(Core *c) { return c->err_pc; }
u32 c6x_pc(Core *c) { return c->pc; }
void c6x_set_pc(Core *c, u32 pc) { c->pc = pc; }
u64 c6x_cycle(Core *c) { return c->cycle; }
void c6x_set_cycle(Core *c, u64 v) { c->cycle = v; }
int c6x_stall(Core *c) { return c->stall; }
int c6x_evcount(Core *c) { return c->evcount; }
int c6x_npend(Core *c) { return c->npend; }
int c6x_sp_on(Core *c) { return c->sp_on; }
u32 c6x_idle(Core *c) { return c->idle; }
void c6x_set_idle(Core *c, u32 v) { c->idle = v; }
u64 c6x_counts(Core *c, int k)
{ return k == 0 ? c->ep_count : k == 1 ? c->insn_count : k == 2 ? c->interrupts : c->py_calls; }

void c6x_watch(Core *c, u32 lo, u32 hi) { c->wlo = lo; c->whi = hi; c->wn = 0; }
int c6x_watch_get(Core *c, int k, u32 *out)
{
    int i = k & 255;
    out[0] = c->wpc[i]; out[1] = c->wad[i]; out[2] = c->wv[i]; out[3] = (u32)c->wcy[i];
    return c->wn;
}

/* idle skip (replay of cdj-nxs2-qemu recordings): writes in flight land
 * as if the cycles ran, then the clock jumps */
void c6x_skip(Core *c, u64 n)
{
    for (int k = 0; k < RING && n; k++, n--) { commit(c, c->cycle); c->cycle++; }
    c->cycle += n;
}
int c6x_splx_pending(Core *c) { return c->splx_irq; }

void c6x_set_idle_loop(Core *c, u32 head, u32 lo, u32 hi)
{ c->idle_head = head; c->idle_lo = lo; c->idle_hi = hi; c->idle_armed = 0; }
