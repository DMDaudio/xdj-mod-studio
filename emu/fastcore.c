/* fastcore.c -- SH-4 interpreter core for the XDJ-700 emulator, in C.
 *
 * A line-for-line port of emu/sh4.py + emu/sh4fpu.py (same semantics, same
 * memory map as emu/machine.py MachineMem._loc), so checkpoints move freely
 * between the two cores. Python keeps the peripherals: every access outside
 * RAM / flash / framebuffers / store queues / the P4 MMU registers calls
 * back into Python one byte at a time (as MachineMem does), and Python
 * delivers interrupts and ticks timers between slices (emu/fastcpu.py).
 *
 * Not handled here, by design (core_run returns FALLBACK and Python runs the
 * Python core for a while): MMU address translation (MMUCR.AT=1) and LDTLB.
 * The firmware runs with the MMU off.
 *
 * Build: cc -O2 -shared -fPIC -o _fastcore.so fastcore.c   (fastcpu.py does it)
 */
#include <math.h>
#include <setjmp.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define MD (1u << 30)
#define RB (1u << 29)
#define BL (1u << 28)
#define FD (1u << 15)
#define MB (1u << 9)
#define QB (1u << 8)
#define SB (1u << 1)
#define TB 1u

#define FB0 0x0E780000u
#define FB1 0x0E83B800u
#define FBSZ (800u * 480u * 2u)

enum { R_LIMIT = 0, R_IDLE = 1, R_SLEEP = 2, R_IO = 3, R_FALLBACK = 4 };

typedef uint32_t (*io_rd_t)(uint32_t addr);
typedef void (*io_wr_t)(uint32_t addr, uint32_t val);
/* sized variants: one Python crossing per access instead of one per byte.
 * Same bytes as n x byte accesses (the Python side loops identically);
 * only used for aligned accesses fully inside device space. */
typedef uint32_t (*io_rds_t)(uint32_t addr, uint32_t n);
typedef void (*io_wrs_t)(uint32_t addr, uint32_t val, uint32_t n);

typedef struct {
    uint32_t r[16];         /* current view: r[0..7] = active bank */
    uint32_t alt[8];        /* inactive R0-R7 bank */
    uint32_t pc, pr, gbr, vbr, mach, macl, sr, ssr, spc, sgr, dbr;
    uint32_t tra, expevt, intevt, tea, pteh, ptel, ptea, ttb, mmucr, ccr;
    uint32_t fpscr, fpul;
    uint32_t fr[32];        /* raw bits, fr[bank*16 + i] */
    uint64_t steps, cycles;
    uint32_t sleeping, low_is_ram;
    uint8_t *flash;
    uint32_t flash_len, pad0;
    uint8_t *ram0, *ram1, *fb0, *fb1, *misc, *sq;
    uint64_t fb_writes;
    io_rd_t io_rd;
    io_wr_t io_wr;
    /* everything through pad1 MUST match the pre-fastcore struct layout
     * exactly (older reference builds bind these fields by offset) */
    uint32_t idle_pc, idle_skip;
    uint32_t io_break, exc_n;       /* exc_n: entries in exc[] since drain */
    uint32_t exc[32][7];            /* spc, offset, code, tea, sr, r15, pr */
    uint32_t exc_lost, pad1;
    /* --- appended fields (fastcpu.py Core mirrors this layout; sizes
     * checked by core_sizeof). NEVER move these before pad1: the layout
     * above is shared with older reference builds. --- */
    io_rds_t io_rds;
    io_wrs_t io_wrs;
    /* CS6 DSP-bridge register file (mirror of SH7724.cs6; synced with the
     * Python device at run boundaries by fastcpu.py, which owns save/load).
     * Served here so the firmware's HPI polling never crosses to Python. */
    uint32_t cs6_addr, cs6_ctrl, cs6_run;
    uint64_t cs6_bulk;
    /* HPID upload log: (dsp_addr u32, value u32) pairs appended on every
     * completed 4-byte write to the CS6 data port 0x180C0000. Read back by
     * fastcpu.py. */
    uint32_t *hpid_buf;
    uint64_t hpid_n, hpid_cap;
    uint32_t hpid_acc, hpid_addr;
    uint8_t hpid_cnt;
    uint8_t hpid_pad[3];
    uint64_t hpid_bytes;
    /* cs6_py: route EVERY CS6 access to the Python device (a DSP model /
     * HLE / boot capture is attached there); C then holds no CS6 state. */
    uint32_t cs6_py, cs6_pad;
    /* --- appended fields (fastcpu.py Core mirrors this layout; sizes
     * checked by core_sizeof) --- */
    /* 2DG register-file mirror (emu/tdg.py TDG.reg keys are 16-bit offsets
     * into 0x04680000; only 0x000-0x0FF is mirrored, the rest crosses). */
    uint32_t tdg_reg[64];       /* words: (off & 0xFF) >> 2 */
    uint32_t tdg_ssr;           /* mirror of TDG.ssr */
    uint64_t tdg_dirty;         /* bit w set = tdg_reg[w] written since sync */
    uint64_t tdg_dirty_ssr;
    uint32_t tdg_py;            /* route ALL 2DG traffic to Python (rare) */
    uint32_t tdg_pad;
} Core;

/* ------------------------------------------------------------------ */
static jmp_buf g_jb;
static uint32_t x_off, x_code, x_tea, x_hastea;

static void raise_exc(uint32_t off, uint32_t code, int hastea, uint32_t tea)
{
    x_off = off; x_code = code; x_hastea = hastea; x_tea = tea;
    longjmp(g_jb, 1);
}
#define ILLEGAL() raise_exc(0x100, 0x180, 0, 0)

static void set_sr(Core *c, uint32_t v)
{
    if ((c->sr ^ v) & RB) {
        uint32_t t[8];
        memcpy(t, c->r, 32); memcpy(c->r, c->alt, 32); memcpy(c->alt, t, 32);
    }
    c->sr = v;
}
#define T() (c->sr & TB)
#define SETT(x) do { if (x) c->sr |= TB; else c->sr &= ~TB; } while (0)

static void log_exc(Core *c, uint32_t spc, uint32_t off, uint32_t code)
{
    if (c->exc_n < 32) {
        uint32_t *e = c->exc[c->exc_n++];
        e[0] = spc; e[1] = off; e[2] = code; e[3] = c->tea; e[4] = c->sr;
        e[5] = c->r[15]; e[6] = c->pr;
    } else {
        c->exc_lost++;
    }
}

static void enter_exc(Core *c, uint32_t off, uint32_t code, uint32_t spc,
                      int hastea, uint32_t tea)
{
    log_exc(c, spc, off, code);
    c->spc = spc;
    c->ssr = c->sr;
    c->sgr = c->r[15];
    c->expevt = code;
    if (hastea) c->tea = tea;
    set_sr(c, c->sr | MD | RB | BL);
    c->pc = c->vbr + off;
}

void core_enter_interrupt(Core *c, uint32_t level, uint32_t code)
{
    c->spc = c->pc;
    c->ssr = c->sr;
    c->sgr = c->r[15];
    c->intevt = code;
    set_sr(c, ((c->sr | MD | RB | BL) & ~0xF0u) | ((level & 0xF) << 4));
    c->pc = c->vbr + 0x600;
    c->sleeping = 0;
}

void core_set_sr(Core *c, uint32_t v) { set_sr(c, v); }

/* ---------------- memory map (mirror of MachineMem._loc) --------------- */
static inline uint8_t *loc(Core *c, uint32_t a, int *fb, int *ro)
{
    *fb = 0; *ro = 0;
    if (a < c->flash_len && !c->low_is_ram) { *ro = 1; return c->flash + a; }
    if (a - FB0 < FBSZ) { *fb = 1; return c->fb0 + (a - FB0); }
    if (a - FB1 < FBSZ) { *fb = 1; return c->fb1 + (a - FB1); }
    if (a - 0x0FFFD000u < 0x10000u) return c->misc + (a - 0x0FFFD000u);
    if (a - 0x08000000u < 0x10000000u) {
        uint32_t b = 0x08000000u | (a & 0x07FFFFFFu);
        if (b < 0x0C000000u) return c->ram0 + (b - 0x08000000u);
        return c->ram1 + (b - 0x0C000000u);
    }
    if (a - 0xE0000000u < 0x40u) return c->sq + (a - 0xE0000000u);
    if (a < 0x04000000u) return c->ram0 + a;
    return 0;
}

static uint32_t *mmu_field(Core *c, uint32_t base)
{
    switch (base) {
    case 0xFF000000u: return &c->pteh;   case 0xFF000004u: return &c->ptel;
    case 0xFF000008u: return &c->ttb;    case 0xFF00000Cu: return &c->tea;
    case 0xFF000010u: return &c->mmucr;  case 0xFF000014u: return &c->ptea;
    case 0xFF00001Cu: return &c->ccr;    case 0xFF000020u: return &c->tra;
    case 0xFF000024u: return &c->expevt; case 0xFF000028u: return &c->intevt;
    }
    return 0;
}

/* CS6 DSP bridge (physical 0x18000000, 1 MB window): control/status,
 * DSP-address latch, bulk FIFO (count only), data port. Byte-exact copy
 * of SH7724._cs6_read/_cs6_write. */
#define CS6BASE 0x18000000u
#define CS6SPAN 0x100000u

/* ------------------------- 2DG accelerator ---------------------------- */
/* Mirror of emu/tdg.py TDG: register file 0x000-0x0FF served in C from a
 * 64-word image, everything else (and the SCLR start handshake, which runs
 * the display list inside the Python device) crosses via io_wrs/io_rds.
 * Dirty words are pushed Python-ward by fastcpu.py at run boundaries.
 */
#define TDG_BASE 0x04680000u
#define TDG_SPAN 0x10000u

static uint32_t tdg_r8(Core *c, uint32_t a)
{
    uint32_t off = a - TDG_BASE;
    if (off < 0x100u) {
        uint32_t w = c->tdg_reg[off >> 2];
        return (w >> ((off & 3u) * 8u)) & 0xFFu;
    }
    return c->io_rd(a) & 0xFFu;             /* off-mirror: rare */
}

static void tdg_wr8(Core *c, uint32_t a, uint32_t v)
{
    uint32_t off = a - TDG_BASE;
    if (off >= 0x100u) {                    /* off-mirror: rare crossing */
        c->io_wr(a, v & 0xFFu);
        return;
    }
    uint32_t sh = (off & 3u) * 8u;
    uint32_t w = off >> 2;
    uint32_t m = 0xFFu << sh;
    uint32_t cur = c->tdg_reg[w];
    uint32_t new = (cur & ~m) | ((v << sh) & m);
    if (w == 2) {                           /* 0x08 SSCR: write-1-to-clear */
        c->tdg_ssr &= ~((v << sh) & m);
        c->tdg_reg[w] = new;
        c->tdg_dirty |= 1ull << w;
        c->tdg_dirty_ssr = 1;
        return;
    }
    c->tdg_reg[w] = new;
    c->tdg_dirty |= 1ull << w;
    if (w == 0 && (new & 1u)) {             /* SCLR start: run the list in
        Python (side effects live there).  fastcpu's io_wr wrapper syncs
        the mirror both ways around the real tdg.write. */
        c->io_wr(a, v & 0xFFu);
        c->io_break = 1;
        return;
    }
}

/* ------------------------------------------------------------------ */
static uint32_t cs6_rd8(Core *c, uint32_t a)
{
    if (c->cs6_py) return c->io_rd(a) & 0xFFu;
    uint32_t off = a - CS6BASE;
    if (off < 4u)
        return (c->cs6_ctrl >> (off * 8u)) & 0xFFu;
    if (off - 0xC0000u < 4u) {
        uint32_t v = (c->cs6_addr == 0x1183FFF4u) ? 1u : 0u;
        return (v >> ((off - 0xC0000u) * 8u)) & 0xFFu;
    }
    return 0;
}

static void cs6_wr8(Core *c, uint32_t a, uint32_t v)
{
    if (c->cs6_py) { c->io_wr(a, v & 0xFFu); return; }    uint32_t off = a - CS6BASE;
    if (off < 4u) {
        uint32_t sh = off * 8u;
        c->cs6_ctrl = (c->cs6_ctrl & ~(0xFFu << sh)) | ((v & 0xFFu) << sh);
        return;
    }
    if (off - 0x40000u < 4u) {
        uint32_t sh = (off - 0x40000u) * 8u;
        c->cs6_addr = (c->cs6_addr & ~(0xFFu << sh)) | ((v & 0xFFu) << sh);
        return;
    }
    if (off - 0x80000u < 0x100u) { c->cs6_bulk++; return; }
    if (off - 0xC0000u < 4u) {
        uint32_t sh = (off - 0xC0000u) * 8u;
        if (c->cs6_addr == 0x1183FFF0u) c->cs6_run = v & 0xFFu;
        c->hpid_bytes++;
        /* accumulate byte-split HPID writes into 32-bit words */
        if (c->hpid_cnt == 0) { c->hpid_addr = c->cs6_addr; c->hpid_acc = 0; }
        c->hpid_acc |= (v & 0xFFu) << sh;
        c->hpid_cnt++;
        if (c->hpid_cnt == 4) {
            c->hpid_cnt = 0;
            if (c->hpid_n == c->hpid_cap) {
                uint64_t nc = c->hpid_cap ? c->hpid_cap * 2 : 65536;
                uint32_t *nb = realloc(c->hpid_buf, nc * 8);
                if (!nb) return;
                c->hpid_buf = nb; c->hpid_cap = nc;
            }
            if (c->hpid_buf) {
                c->hpid_buf[c->hpid_n * 2] = c->hpid_addr;
                c->hpid_buf[c->hpid_n * 2 + 1] = c->hpid_acc;
                c->hpid_n++;
            }
        }
        return;
    }
}

static uint32_t rd8p(Core *c, uint32_t a)
{
    int fb, ro;
    if (a - 0xFF000000u < 0x2Cu) {
        uint32_t *f = mmu_field(c, a & ~3u);
        if (f) return (*f >> ((a & 3) * 8)) & 0xFF;
    }
    uint8_t *h = loc(c, a, &fb, &ro);
    if (h) return *h;
    if (a - CS6BASE < CS6SPAN) return cs6_rd8(c, a);
    return c->io_rd(a) & 0xFF;
}

static void wr8p(Core *c, uint32_t a, uint32_t v)
{
    int fb, ro;
    if (a - 0xFF000000u < 0x2Cu) {
        uint32_t *f = mmu_field(c, a & ~3u);
        if (f) {
            uint32_t sh = (a & 3) * 8;
            *f = (*f & ~(0xFFu << sh)) | ((v & 0xFF) << sh);
            if (f == &c->mmucr) c->io_break = 1;
            return;
        }
    }
    uint8_t *h = loc(c, a, &fb, &ro);
    if (h) {
        if (ro) return;
        *h = (uint8_t)v;
        if (fb) c->fb_writes++;
        return;
    }
    if (a - CS6BASE < CS6SPAN) { cs6_wr8(c, a, v); return; }
    c->io_wr(a, v & 0xFF);   /* the callback sets io_break if it matters */
}

/* n in {1,2,4}; p physical. Aligned accesses never straddle a region. */
static inline uint32_t prd(Core *c, uint32_t p, int n)
{
    int fb, ro;
    if (p - 0xFF000000u >= 0x2Cu) {
        uint8_t *h = loc(c, p, &fb, &ro);
        if (h) {
            if (n == 4) { uint32_t v; memcpy(&v, h, 4); return v; }
            if (n == 2) { uint16_t v; memcpy(&v, h, 2); return v; }
            return *h;
        }
        if (n > 1 && (p & (uint32_t)(n - 1)) == 0) {
            /* aligned device access fully outside RAM: one callback.
             * Both ends must miss RAM (a straddling access falls back
             * to bytes, like before). CS6 stays in C (cs6_rd8) on the
             * byte path, or io_rds once when the Python DSP model is
             * attached; 2DG reads are served from the mirror. */
            int fb2, ro2;
            uint32_t dp = p - CS6BASE, tp = p - TDG_BASE;
            if (dp < CS6SPAN && c->cs6_py && p + (uint32_t)n <= CS6BASE + CS6SPAN)
                return n == 4 ? c->io_rds(p, 4) & 0xFFFFFFFFu
                              : c->io_rds(p, 2) & 0xFFFFu;
            if (!loc(c, p + (uint32_t)n - 1u, &fb2, &ro2) &&
                (dp >= CS6SPAN || p + (uint32_t)n <= CS6BASE + CS6SPAN))
                return n == 4 ? c->io_rds(p, 4) & 0xFFFFFFFFu
                              : c->io_rds(p, 2) & 0xFFFFu;
        }
    }
    uint32_t v = 0;
    for (int i = 0; i < n; i++) v |= rd8p(c, p + i) << (8 * i);
    return v;
}

static inline void pwr(Core *c, uint32_t p, int n, uint32_t v)
{
    int fb, ro;
    if (p - 0xFF000000u >= 0x2Cu) {
        uint8_t *h = loc(c, p, &fb, &ro);
        if (h) {
            if (ro) return;
            if (n == 4) memcpy(h, &v, 4);
            else if (n == 2) { uint16_t s = (uint16_t)v; memcpy(h, &s, 2); }
            else *h = (uint8_t)v;
            if (fb) c->fb_writes += n;
            return;
        }
        if (n > 1 && (p & (uint32_t)(n - 1)) == 0) {
            int fb2, ro2;
            uint32_t dp = p - CS6BASE, tp = p - TDG_BASE;
            if (!loc(c, p + (uint32_t)n - 1u, &fb2, &ro2)) {
                if (n == 4) c->io_wrs(p, v, 4);
                else c->io_wrs(p, v & 0xFFFFu, 2);
                return;
            }
        }
    }
    for (int i = 0; i < n; i++) wr8p(c, p + i, (v >> (8 * i)) & 0xFF);
}

/* address translation, MMU off (sh4.py translate) */
static inline uint32_t xlat(Core *c, uint32_t a)
{
    if (a - 0x80000000u < 0x40000000u) {           /* P1 / P2 */
        if (!(c->sr & MD)) raise_exc(0x100, 0x0E0, 1, a);
        return a & 0x1FFFFFFFu;
    }
    if (a >= 0xE0000000u) {                         /* P4 */
        if (!(c->sr & MD)) raise_exc(0x100, 0x0E0, 1, a);
        return a;
    }
    if (c->sr & MD) {
        if (a < 0x20000000u) return a;
        if (a - 0xC0000000u < 0x20000000u) return a & 0x1FFFFFFFu;
        raise_exc(0x100, 0x0E0, 1, a);
    }
    if (a < 0x20000000u) return a;
    raise_exc(0x100, 0x0E0, 1, a);
    return 0;
}

static inline uint32_t vrd(Core *c, uint32_t a, int n)
{
    uint32_t p = xlat(c, a);
    if (n == 2 && (a & 1)) raise_exc(0x100, 0x0E0, 1, a);
    if (n == 4 && (a & 3)) raise_exc(0x100, 0x0E0, 1, a);
    return prd(c, p, n);
}

static inline void vwr(Core *c, uint32_t a, int n, uint32_t v)
{
    uint32_t p = xlat(c, a);
    if (n == 2 && (a & 1)) raise_exc(0x100, 0x0E0, 1, a);
    if (n == 4 && (a & 3)) raise_exc(0x100, 0x0E0, 1, a);
    pwr(c, p, n, v);
}

#define RD8(a)  vrd(c, (a), 1)
#define RD16(a) vrd(c, (a), 2)
#define RD32(a) vrd(c, (a), 4)
#define WR8(a, v)  vwr(c, (a), 1, (v))
#define WR16(a, v) vwr(c, (a), 2, (v))
#define WR32(a, v) vwr(c, (a), 4, (v))
#define S8(x)  ((uint32_t)(int32_t)(int8_t)(x))
#define S16(x) ((uint32_t)(int32_t)(int16_t)(x))

/* ------------------------------ FPU ---------------------------------- */
static inline float f_of(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }
static inline uint32_t u_of(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
#define FBANK ((c->fpscr >> 21) & 1)
#define FSZ ((c->fpscr >> 20) & 1)
#define FPR ((c->fpscr >> 19) & 1)
#define FRRAW(b, i) (c->fr[((b) & 1) * 16 + ((i) & 15)])

static inline float getf(Core *c, int i) { return f_of(FRRAW(FBANK, i)); }
static inline void setf(Core *c, int i, double v) { FRRAW(FBANK, i) = u_of((float)v); }
static inline double getd(Core *c, int pair, int bank)
{
    uint64_t u = ((uint64_t)FRRAW(bank, pair * 2) << 32) | FRRAW(bank, pair * 2 + 1);
    double d; memcpy(&d, &u, 8); return d;
}
static inline void setd(Core *c, int pair, int bank, double d)
{
    uint64_t u; memcpy(&u, &d, 8);
    FRRAW(bank, pair * 2) = (uint32_t)(u >> 32);
    FRRAW(bank, pair * 2 + 1) = (uint32_t)u;
}

typedef struct { int kind; uint32_t tgt, cyc, rssr; } Res;
enum { K_NONE, K_TIMED, K_BR, K_DBR, K_RTE, K_EXC, K_ADV4 };

static void fpu_step(Core *c, uint32_t op, Res *res)
{
    if (c->sr & FD) raise_exc(0x100, 0x120, 0, 0);
    int n = (op >> 8) & 0xF, m = (op >> 4) & 0xF, lo4 = op & 0xF;
    int sz = FSZ, pr = FPR, bk = FBANK;
    uint32_t *R = c->r;

    if (lo4 <= 0x5) {
        if (pr) {
            if (op & 0x0110) ILLEGAL();
            int dp = (op >> 9) & 7, sp = (op >> 5) & 7;
            double a = getd(c, sp, bk), b = getd(c, dp, bk);
            if (lo4 == 4) { SETT(b == a); return; }
            if (lo4 == 5) { SETT(b > a); return; }
            static const uint32_t cy[4] = {4, 4, 5, 18};
            double v = lo4 == 0 ? b + a : lo4 == 1 ? b - a : lo4 == 2 ? b * a : b / a;
            setd(c, dp, bk, v);
            res->kind = K_TIMED; res->cyc = cy[lo4];
            return;
        }
        double a = getf(c, m), b = getf(c, n);
        if (lo4 == 4) { SETT(b == a); return; }
        if (lo4 == 5) { SETT(b > a); return; }
        static const uint32_t cy[4] = {3, 3, 3, 12};
        double v = lo4 == 0 ? b + a : lo4 == 1 ? b - a : lo4 == 2 ? b * a : b / a;
        setf(c, n, v);
        res->kind = K_TIMED; res->cyc = cy[lo4];
        return;
    }
    if (lo4 == 0xE) {                               /* FMAC */
        if (pr) ILLEGAL();
        setf(c, n, (double)getf(c, 0) * getf(c, m) + getf(c, n));
        res->kind = K_TIMED; res->cyc = 3;
        return;
    }
    if (lo4 == 0xC) {                               /* FMOV reg-reg */
        if (sz) {
            int dp = (op >> 9) & 7, db = (op >> 8) & 1, sp = (op >> 5) & 7, sb = (op >> 4) & 1;
            uint32_t hi = FRRAW(bk ^ sb, sp * 2), lo = FRRAW(bk ^ sb, sp * 2 + 1);
            FRRAW(bk ^ db, dp * 2) = hi; FRRAW(bk ^ db, dp * 2 + 1) = lo;
        } else {
            FRRAW(bk, n) = FRRAW(bk, m);
        }
        return;
    }
    if (lo4 >= 0x6 && lo4 <= 0xB) {                 /* FMOV memory */
        if (sz) {
            if (lo4 == 8 || lo4 == 9 || lo4 == 6) {
                int dp = (op >> 9) & 7, db = (op >> 8) & 1;
                uint32_t a = lo4 != 6 ? R[m] : R[0] + R[m];
                uint32_t lo = RD32(a), hi = RD32(a + 4);
                FRRAW(bk ^ db, dp * 2) = hi; FRRAW(bk ^ db, dp * 2 + 1) = lo;
                if (lo4 == 9) R[m] += 8;
            } else {
                int sp = (op >> 5) & 7, sb = (op >> 4) & 1;
                uint32_t hi = FRRAW(bk ^ sb, sp * 2), lo = FRRAW(bk ^ sb, sp * 2 + 1);
                if (lo4 == 0xB) R[n] -= 8;
                uint32_t a = lo4 != 7 ? R[n] : R[0] + R[n];
                WR32(a, lo); WR32(a + 4, hi);
            }
            return;
        }
        switch (lo4) {
        case 0x8: FRRAW(bk, n) = RD32(R[m]); break;
        case 0x9: FRRAW(bk, n) = RD32(R[m]); R[m] += 4; break;
        case 0xA: WR32(R[n], FRRAW(bk, m)); break;
        case 0xB: R[n] -= 4; WR32(R[n], FRRAW(bk, m)); break;
        case 0x6: FRRAW(bk, n) = RD32(R[0] + R[m]); break;
        default:  WR32(R[0] + R[n], FRRAW(bk, m)); break;
        }
        return;
    }
    if (op == 0xF3FD) { c->fpscr ^= 1u << 20; return; }
    if (op == 0xFBFD) { c->fpscr ^= 1u << 21; return; }
    if (op == 0xF7FD) { c->fpscr ^= 1u << 19; return; }
    if (lo4 == 0xD) {
        int sub = (op >> 4) & 0xF, dn = (n >> 1) & 7;
        switch (sub) {
        case 0x0: FRRAW(bk, n) = c->fpul; return;
        case 0x1: c->fpul = FRRAW(bk, n); return;
        case 0x2:
            if (pr) setd(c, dn, bk, (double)(int32_t)c->fpul);
            else setf(c, n, (double)(int32_t)c->fpul);
            res->kind = K_TIMED; res->cyc = 2; return;
        case 0x3: {
            double v = pr ? getd(c, dn, bk) : (double)getf(c, n);
            if (isnan(v)) c->fpul = 0x80000000u;
            else if (v >= 2147483647.0) c->fpul = 0x7FFFFFFFu;
            else if (v <= -2147483648.0) c->fpul = 0x80000000u;
            else c->fpul = (uint32_t)(int32_t)v;
            res->kind = K_TIMED; res->cyc = 2; return;
        }
        case 0x4: case 0x5: {
            int i = pr ? dn * 2 : n;
            uint32_t u = FRRAW(bk, i);
            FRRAW(bk, i) = sub == 4 ? (u ^ 0x80000000u) : (u & 0x7FFFFFFFu);
            return;
        }
        case 0x6:
            if (pr) { double v = getd(c, dn, bk); setd(c, dn, bk, v >= 0 ? sqrt(v) : NAN); }
            else { double v = getf(c, n); setf(c, n, v >= 0 ? sqrt(v) : NAN); }
            res->kind = K_TIMED; res->cyc = 8; return;
        case 0x7: {
            double v = getf(c, n);
            setf(c, n, v > 0 ? 1.0 / sqrt(v) : (v == 0 ? INFINITY : NAN));
            res->kind = K_TIMED; res->cyc = 6; return;
        }
        case 0x8: FRRAW(bk, n) = 0; return;
        case 0x9: FRRAW(bk, n) = 0x3F800000u; return;
        case 0xA: setd(c, dn, bk, (double)f_of(c->fpul)); res->kind = K_TIMED; res->cyc = 3; return;
        case 0xB: c->fpul = u_of((float)getd(c, dn, bk)); res->kind = K_TIMED; res->cyc = 3; return;
        case 0xE: {
            int vn = (op >> 10) & 3, vm = (op >> 8) & 3;
            double s = 0;
            for (int k = 0; k < 4; k++) s += (double)getf(c, vn * 4 + k) * getf(c, vm * 4 + k);
            setf(c, vn * 4 + 3, s);
            res->kind = K_TIMED; res->cyc = 6; return;
        }
        case 0xF:
            if ((op & 0x0300) == 0x0100) {          /* FTRV XMTRX,FVn */
                int vn = (op >> 10) & 3;
                double vec[4], out[4];
                for (int k = 0; k < 4; k++) vec[k] = getf(c, vn * 4 + k);
                for (int i = 0; i < 4; i++) {
                    double s = 0;
                    for (int k = 0; k < 4; k++) s += (double)f_of(FRRAW(bk ^ 1, k * 4 + i)) * vec[k];
                    out[i] = s;
                }
                for (int i = 0; i < 4; i++) setf(c, vn * 4 + i, out[i]);
                res->kind = K_TIMED; res->cyc = 8; return;
            }
            if (!(op & 0x0100)) {                   /* FSCA FPUL,DRn */
                double ang = (c->fpul & 0xFFFF) * (2 * M_PI / 65536.0);
                int dp = (op >> 9) & 7;
                setf(c, dp * 2, sin(ang));
                setf(c, dp * 2 + 1, cos(ang));
                res->kind = K_TIMED; res->cyc = 8; return;
            }
            ILLEGAL();
        }
        ILLEGAL();
    }
    ILLEGAL();
}

static void fmov32(Core *c, uint32_t w1, uint32_t w2, Res *res)
{
    int dbl = !(w1 & 0x0100);
    int n = (w1 >> 8) & (dbl ? 7 : 0xF), m = (w1 >> 4) & 0xF;
    int bk = FBANK;
    if ((w2 & 0xF000) == 0x7000) {
        uint32_t a = c->r[m] + (w2 & 0xFFF) * (dbl ? 8 : 4);
        if (dbl) {
            uint32_t lo = 0, hi = 0;
            for (int k = 0; k < 4; k++) lo |= prd(c, xlat(c, a + k), 1) << (8 * k);
            for (int k = 0; k < 4; k++) hi |= prd(c, xlat(c, a + 4 + k), 1) << (8 * k);
            FRRAW(0, n * 2) = hi; FRRAW(0, n * 2 + 1) = lo;
        } else {
            FRRAW(bk, n) = RD32(a);
        }
    } else if ((w2 & 0xF000) == 0x3000) {
        uint32_t a = c->r[n] + (w2 & 0xFFF) * (dbl ? 8 : 4);
        if (dbl) {
            uint32_t hi = FRRAW(0, n * 2), lo = FRRAW(0, n * 2 + 1);
            for (int k = 0; k < 4; k++) pwr(c, xlat(c, a + k), 1, (lo >> (8 * k)) & 0xFF);
            for (int k = 0; k < 4; k++) pwr(c, xlat(c, a + 4 + k), 1, (hi >> (8 * k)) & 0xFF);
        } else {
            WR32(a, FRRAW(bk, m));
        }
    } else {
        ILLEGAL();
    }
    res->kind = K_ADV4; res->cyc = 2;
}

/* ------------------------- one instruction ---------------------------- */
static int g_fallback;      /* set by an op we leave to the Python core */

static void step(Core *c, uint32_t op, uint32_t nxt, Res *res)
{
    uint32_t *R = c->r;
    uint32_t n = (op >> 8) & 0xF, m = (op >> 4) & 0xF, i8 = op & 0xFF;
    res->kind = K_NONE;

    switch (op >> 12) {
    case 0x0: {
        uint32_t lo = op & 0xFF;
        if ((op & 0xF) == 0x7) { c->macl = R[n] * R[m]; res->kind = K_TIMED; res->cyc = 2; return; }
        if ((op & 0xF) == 0xF) {                    /* MAC.L */
            int32_t a = (int32_t)RD32(R[m]), b = (int32_t)RD32(R[n]);
            R[m] += 4; R[n] += 4;
            int64_t acc = (int64_t)(((uint64_t)c->mach << 32) | c->macl);
            acc = (int64_t)((uint64_t)acc + (uint64_t)((int64_t)a * b));
            c->mach = (uint32_t)((uint64_t)acc >> 32); c->macl = (uint32_t)acc;
            res->kind = K_TIMED; res->cyc = 3; return;
        }
        switch (lo) {
        case 0x02: R[n] = c->sr; return;
        case 0x03: c->pr = nxt + 2; res->kind = K_DBR; res->tgt = c->pc + 4 + R[n]; res->cyc = 2; return;
        case 0x08: c->sr &= ~TB; return;
        case 0x09: return;
        case 0x0B: res->kind = K_DBR; res->tgt = c->pr; res->cyc = 2; return;
        case 0x0A: R[n] = c->mach; return;
        case 0x18: c->sr |= TB; return;
        case 0x19: c->sr &= ~(MB | QB | TB); return;
        case 0x1A: R[n] = c->macl; return;
        case 0x1B: c->sleeping = 1; return;
        case 0x23: res->kind = K_DBR; res->tgt = c->pc + 4 + R[n]; res->cyc = 2; return;
        case 0x28: c->mach = c->macl = 0; return;
        case 0x29: R[n] = T() ? 1 : 0; return;
        case 0x2A: R[n] = c->pr; return;
        case 0x5A: R[n] = c->fpul; return;
        case 0x6A: R[n] = c->fpscr; return;
        case 0x2B:
            if (!(c->sr & MD)) ILLEGAL();
            res->kind = K_RTE; res->tgt = c->spc; res->rssr = c->ssr; res->cyc = 4; return;
        case 0x38:
            if (!(c->sr & MD)) ILLEGAL();
            g_fallback = 1; return;                 /* LDTLB: Python core */
        case 0x48: c->sr &= ~SB; return;
        case 0x58: c->sr |= SB; return;
        }
        switch (op & 0xF) {
        case 0xC: R[n] = S8(RD8(R[0] + R[m])); return;
        case 0xD: R[n] = S16(RD16(R[0] + R[m])); return;
        case 0xE: R[n] = RD32(R[0] + R[m]); return;
        case 0x4: WR8(R[0] + R[n], R[m] & 0xFF); return;
        case 0x5: WR16(R[0] + R[n], R[m]); return;
        case 0x6: WR32(R[0] + R[n], R[m]); return;
        }
        switch (lo) {
        case 0x12: R[n] = c->gbr; return;
        case 0x22: R[n] = c->vbr; return;
        case 0x32: R[n] = c->ssr; return;
        case 0x42: R[n] = c->spc; return;
        case 0x3A: R[n] = c->sgr; return;
        case 0xFA: R[n] = c->dbr; return;
        }
        if ((op & 0x008F) == 0x0082) { R[n] = c->alt[(op >> 4) & 7]; return; }
        if (lo == 0xC3) { WR32(R[n], R[0]); return; }
        if (lo == 0x83 || lo == 0x93 || lo == 0xA3 || lo == 0xB3 || lo == 0xD3 || lo == 0xE3) return;
        if (lo == 0xAB) return;
        ILLEGAL();
    }
    case 0x1: WR32(R[n] + (op & 0xF) * 4, R[m]); return;
    case 0x5: R[n] = RD32(R[m] + (op & 0xF) * 4); return;
    case 0x2:
        switch (op & 0xF) {
        case 0x0: WR8(R[n], R[m] & 0xFF); return;
        case 0x1: WR16(R[n], R[m]); return;
        case 0x2: WR32(R[n], R[m]); return;
        case 0x3: ILLEGAL();
        case 0x4: R[n] -= 1; WR8(R[n], R[m] & 0xFF); return;
        case 0x5: R[n] -= 2; WR16(R[n], R[m]); return;
        case 0x6: R[n] -= 4; WR32(R[n], R[m]); return;
        case 0x7: {
            uint32_t q = R[n] >> 31, mm = R[m] >> 31;
            if (q) c->sr |= QB; else c->sr &= ~QB;
            if (mm) c->sr |= MB; else c->sr &= ~MB;
            SETT(mm ^ q); return;
        }
        case 0x8: SETT((R[n] & R[m]) == 0); return;
        case 0x9: R[n] &= R[m]; return;
        case 0xA: R[n] ^= R[m]; return;
        case 0xB: R[n] |= R[m]; return;
        case 0xC: {
            uint32_t x = R[n] ^ R[m];
            SETT(!(x & 0xFF) || !(x & 0xFF00) || !(x & 0xFF0000) || !(x & 0xFF000000u));
            return;
        }
        case 0xD: R[n] = (R[n] >> 16) | (R[m] << 16); return;
        case 0xE: c->macl = (R[n] & 0xFFFF) * (R[m] & 0xFFFF); res->kind = K_TIMED; res->cyc = 3; return;
        case 0xF: c->macl = (uint32_t)((int32_t)(int16_t)R[n] * (int32_t)(int16_t)R[m]); res->kind = K_TIMED; res->cyc = 3; return;
        }
        return;
    case 0x3:
        switch (op & 0xF) {
        case 0x1: {
            uint32_t w2 = RD16(nxt);
            fmov32(c, op, w2, res); return;
        }
        case 0x0: SETT(R[n] == R[m]); return;
        case 0x2: SETT(R[n] >= R[m]); return;
        case 0x3: SETT((int32_t)R[n] >= (int32_t)R[m]); return;
        case 0x4: {                                 /* DIV1 */
            uint32_t old_q = (c->sr & QB) ? 1 : 0, mm = (c->sr & MB) ? 1 : 0;
            uint32_t q_new = R[n] >> 31;
            uint32_t rn = (R[n] << 1) | (T() ? 1 : 0), tmp, cy;
            if (old_q == mm) { tmp = rn - R[m]; cy = tmp > rn; }
            else { tmp = rn + R[m]; cy = tmp < rn; }
            uint32_t q = (mm == q_new) ? cy : !cy;
            if (q) c->sr |= QB; else c->sr &= ~QB;
            SETT(q == mm);
            R[n] = tmp; return;
        }
        case 0x5: {
            uint64_t p = (uint64_t)R[n] * R[m];
            c->macl = (uint32_t)p; c->mach = (uint32_t)(p >> 32);
            res->kind = K_TIMED; res->cyc = 3; return;
        }
        case 0x6: SETT(R[n] > R[m]); return;
        case 0x7: SETT((int32_t)R[n] > (int32_t)R[m]); return;
        case 0x8: R[n] -= R[m]; return;
        case 0xA: {
            uint64_t tmp = (uint64_t)R[n] - R[m] - (T() ? 1 : 0);
            SETT((int64_t)((int64_t)(uint64_t)R[n] - (int64_t)R[m] - (T() ? 1 : 0)) < 0);
            R[n] = (uint32_t)tmp; return;
        }
        case 0xB: {
            int64_t d = (int64_t)(int32_t)R[n] - (int32_t)R[m];
            SETT(d < -0x80000000LL || d > 0x7FFFFFFFLL);
            R[n] -= R[m]; return;
        }
        case 0xC: R[n] += R[m]; return;
        case 0xD: {
            int64_t p = (int64_t)(int32_t)R[n] * (int32_t)R[m];
            c->macl = (uint32_t)p; c->mach = (uint32_t)((uint64_t)p >> 32);
            res->kind = K_TIMED; res->cyc = 3; return;
        }
        case 0xE: {
            uint64_t tmp = (uint64_t)R[n] + R[m] + (T() ? 1 : 0);
            SETT(tmp > 0xFFFFFFFFull);
            R[n] = (uint32_t)tmp; return;
        }
        case 0xF: {
            int64_t d = (int64_t)(int32_t)R[n] + (int32_t)R[m];
            SETT(d < -0x80000000LL || d > 0x7FFFFFFFLL);
            R[n] += R[m]; return;
        }
        }
        ILLEGAL();
    case 0x4: {
        uint32_t lo = op & 0xFF, v;
        switch (lo) {
        case 0x00: case 0x20: SETT(R[n] >> 31); R[n] <<= 1; return;
        case 0x01: SETT(R[n] & 1); R[n] >>= 1; return;
        case 0x21: SETT(R[n] & 1); R[n] = (uint32_t)((int32_t)R[n] >> 1); return;
        case 0x04: v = R[n]; SETT(v >> 31); R[n] = (v << 1) | (v >> 31); return;
        case 0x05: v = R[n]; SETT(v & 1); R[n] = (v >> 1) | (v << 31); return;
        case 0x06: c->mach = RD32(R[n]); R[n] += 4; return;
        case 0x07:
            if (!(c->sr & MD)) ILLEGAL();
            v = RD32(R[n]); R[n] += 4; set_sr(c, v); SETT(v & 1); return;
        case 0x08: R[n] <<= 2; return;
        case 0x09: R[n] >>= 2; return;
        case 0x0A: c->mach = R[n]; return;
        case 0x0B: c->pr = nxt + 2; res->kind = K_DBR; res->tgt = R[n]; res->cyc = 2; return;
        case 0x0E:
            if (!(c->sr & MD)) ILLEGAL();
            v = R[n]; set_sr(c, v); SETT(v & 1); return;
        case 0x10: R[n] -= 1; SETT(R[n] == 0); return;
        case 0x11: SETT(!(R[n] >> 31)); return;
        case 0x15: SETT(R[n] != 0 && !(R[n] >> 31)); return;
        case 0x16: c->macl = RD32(R[n]); R[n] += 4; return;
        case 0x17: c->gbr = RD32(R[n]); R[n] += 4; return;
        case 0x18: R[n] <<= 8; return;
        case 0x19: R[n] >>= 8; return;
        case 0x1A: c->macl = R[n]; return;
        case 0x1B:
            v = RD8(R[n]); SETT(v == 0); WR8(R[n], v | 0x80);
            res->kind = K_TIMED; res->cyc = 4; return;
        case 0x1E: c->gbr = R[n]; return;
        case 0x24: { v = R[n]; uint32_t t = v >> 31; R[n] = (v << 1) | (T() ? 1 : 0); SETT(t); return; }
        case 0x25: { v = R[n]; uint32_t t = v & 1; R[n] = (v >> 1) | ((T() ? 1u : 0u) << 31); SETT(t); return; }
        case 0x26: c->pr = RD32(R[n]); R[n] += 4; return;
        case 0x27:
            if (!(c->sr & MD)) ILLEGAL();
            c->vbr = RD32(R[n]); R[n] += 4; return;
        case 0x28: R[n] <<= 16; return;
        case 0x29: R[n] >>= 16; return;
        case 0x2A: c->pr = R[n]; return;
        case 0x2B: res->kind = K_DBR; res->tgt = R[n]; res->cyc = 2; return;
        case 0x2E: if (!(c->sr & MD)) ILLEGAL(); c->vbr = R[n]; return;
        case 0x36: if (!(c->sr & MD)) ILLEGAL(); c->sgr = RD32(R[n]); R[n] += 4; return;
        case 0x37: if (!(c->sr & MD)) ILLEGAL(); c->ssr = RD32(R[n]); R[n] += 4; return;
        case 0x3A: if (!(c->sr & MD)) ILLEGAL(); c->sgr = R[n]; return;
        case 0x3E: if (!(c->sr & MD)) ILLEGAL(); c->ssr = R[n]; return;
        case 0x47: if (!(c->sr & MD)) ILLEGAL(); c->spc = RD32(R[n]); R[n] += 4; return;
        case 0x4E: if (!(c->sr & MD)) ILLEGAL(); c->spc = R[n]; return;
        case 0x56: c->fpul = RD32(R[n]); R[n] += 4; return;
        case 0x5A: c->fpul = R[n]; return;
        case 0x66: c->fpscr = RD32(R[n]); R[n] += 4; return;
        case 0x6A: c->fpscr = R[n]; return;
        case 0xE9: {                                /* MOVUA.L @Rm,R0 */
            uint32_t a = R[n], x = 0;
            for (int k = 0; k < 4; k++) x |= prd(c, xlat(c, a + k), 1) << (8 * k);
            R[0] = x; return;
        }
        }
        if ((op & 0xF00F) == 0x400C) {              /* SHAD */
            uint32_t s = R[m];
            if (!(s >> 31)) R[n] <<= (s & 0x1F);
            else if (!(s & 0x1F)) R[n] = ((int32_t)R[n] < 0) ? 0xFFFFFFFFu : 0;
            else R[n] = (uint32_t)((int32_t)R[n] >> (((~s) & 0x1F) + 1));
            return;
        }
        if ((op & 0xF00F) == 0x400D) {              /* SHLD */
            uint32_t s = R[m];
            if (!(s >> 31)) R[n] <<= (s & 0x1F);
            else if (!(s & 0x1F)) R[n] = 0;
            else R[n] >>= (((~s) & 0x1F) + 1);
            return;
        }
        if ((op & 0xF00F) == 0x400F) {              /* MAC.W */
            int32_t a = (int16_t)RD16(R[m]), b = (int16_t)RD16(R[n]);
            R[m] += 2; R[n] += 2;
            int64_t acc = (int64_t)(((uint64_t)c->mach << 32) | c->macl);
            acc += (int64_t)a * b;
            if (c->sr & SB) {
                if (acc > 0x7FFFFFFFFFFFLL) acc = 0x7FFFFFFFFFFFLL;
                else if (acc < -0x800000000000LL) acc = -0x800000000000LL;
            }
            c->mach = (uint32_t)((uint64_t)acc >> 32); c->macl = (uint32_t)acc;
            res->kind = K_TIMED; res->cyc = 3; return;
        }
        if (lo == 0x02 || lo == 0x12 || lo == 0x22) {
            R[n] -= 4; WR32(R[n], lo == 0x02 ? c->mach : lo == 0x12 ? c->macl : c->pr); return;
        }
        if (lo == 0x62 || lo == 0x52) { R[n] -= 4; WR32(R[n], lo == 0x62 ? c->fpscr : c->fpul); return; }
        if (lo == 0x13) { R[n] -= 4; WR32(R[n], c->gbr); return; }
        if (lo == 0x03 || lo == 0x23 || lo == 0x33 || lo == 0x43 || lo == 0x32) {
            uint32_t sv = lo == 0x03 ? c->sr : lo == 0x23 ? c->vbr : lo == 0x33 ? c->ssr
                        : lo == 0x43 ? c->spc : c->sgr;
            R[n] -= 4; WR32(R[n], sv); return;
        }
        if ((op & 0x008F) == 0x0083) {
            if (!(c->sr & MD)) ILLEGAL();
            v = c->alt[(op >> 4) & 7]; R[n] -= 4; WR32(R[n], v); return;
        }
        if ((op & 0x008F) == 0x008E) {
            if (!(c->sr & MD)) ILLEGAL();
            c->alt[(op >> 4) & 7] = R[n]; return;
        }
        if ((op & 0x008F) == 0x0087) {
            if (!(c->sr & MD)) ILLEGAL();
            c->alt[(op >> 4) & 7] = RD32(R[n]); R[n] += 4; return;
        }
        ILLEGAL();
    }
    case 0x6:
        switch (op & 0xF) {
        case 0x0: R[n] = S8(RD8(R[m])); return;
        case 0x1: R[n] = S16(RD16(R[m])); return;
        case 0x2: R[n] = RD32(R[m]); return;
        case 0x3: R[n] = R[m]; return;
        case 0x4: { uint32_t v = S8(RD8(R[m])); R[n] = v; if (n != m) R[m] += 1; else R[m] = v + 1; return; }
        case 0x5: { uint32_t v = S16(RD16(R[m])); R[n] = v; if (n != m) R[m] += 2; else R[m] = v + 2; return; }
        case 0x6: { uint32_t v = RD32(R[m]); R[n] = v; if (n != m) R[m] += 4; else R[m] = v + 4; return; }
        case 0x7: R[n] = ~R[m]; return;
        case 0x8: { uint32_t v = R[m]; R[n] = (v & 0xFFFF0000u) | ((v & 0xFF) << 8) | ((v >> 8) & 0xFF); return; }
        case 0x9: { uint32_t v = R[m]; R[n] = (v >> 16) | (v << 16); return; }
        case 0xA: {
            int64_t tmp = 0 - (int64_t)R[m] - (T() ? 1 : 0);
            SETT(tmp < 0); R[n] = (uint32_t)tmp; return;
        }
        case 0xB: R[n] = 0 - R[m]; return;
        case 0xC: R[n] = R[m] & 0xFF; return;
        case 0xD: R[n] = R[m] & 0xFFFF; return;
        case 0xE: R[n] = S8(R[m]); return;
        case 0xF: R[n] = S16(R[m]); return;
        }
        return;
    case 0x7: R[n] += S8(i8); return;
    case 0x8: {
        uint32_t sub = (op >> 8) & 0xF, disp4 = op & 0xF;
        uint32_t disp = S8(i8) * 2 + 4;
        switch (sub) {
        case 0x0: WR8(R[m] + disp4, R[0] & 0xFF); return;
        case 0x1: WR16(R[m] + disp4 * 2, R[0]); return;
        case 0x4: R[0] = S8(RD8(R[m] + disp4)); return;
        case 0x5: R[0] = S16(RD16(R[m] + disp4 * 2)); return;
        case 0x8: SETT(R[0] == S8(i8)); return;
        case 0x9: if (T()) { res->kind = K_BR; res->tgt = c->pc + disp; res->cyc = 1; } return;
        case 0xB: if (!T()) { res->kind = K_BR; res->tgt = c->pc + disp; res->cyc = 1; } return;
        case 0xD: if (T()) { res->kind = K_DBR; res->tgt = c->pc + disp; res->cyc = 1; } return;
        case 0xF: if (!T()) { res->kind = K_DBR; res->tgt = c->pc + disp; res->cyc = 1; } return;
        }
        ILLEGAL();
    }
    case 0x9: R[n] = S16(RD16(c->pc + 4 + i8 * 2)); return;
    case 0xA: { int32_t d = op & 0xFFF; if (d & 0x800) d -= 0x1000;
                res->kind = K_DBR; res->tgt = c->pc + 4 + d * 2; res->cyc = 2; return; }
    case 0xB: { int32_t d = op & 0xFFF; if (d & 0x800) d -= 0x1000;
                c->pr = nxt + 2; res->kind = K_DBR; res->tgt = c->pc + 4 + d * 2; res->cyc = 2; return; }
    case 0xC: {
        uint32_t a;
        switch (op & 0xFF00) {
        case 0xC700: R[0] = ((c->pc + 4) & ~3u) + i8 * 4; return;
        case 0xC300:
            c->tra = i8 << 2;
            enter_exc(c, 0x100, 0x160, c->pc + 2, 0, 0);
            res->kind = K_EXC; res->cyc = 8; return;
        case 0xC000: WR8(c->gbr + i8, R[0] & 0xFF); return;
        case 0xC100: WR16(c->gbr + i8 * 2, R[0]); return;
        case 0xC200: WR32(c->gbr + i8 * 4, R[0]); return;
        case 0xC400: R[0] = S8(RD8(c->gbr + i8)); return;
        case 0xC500: R[0] = S16(RD16(c->gbr + i8 * 2)); return;
        case 0xC600: R[0] = RD32(c->gbr + i8 * 4); return;
        case 0xC800: SETT((R[0] & i8) == 0); return;
        case 0xC900: R[0] &= i8; return;
        case 0xCA00: R[0] ^= i8; return;
        case 0xCB00: R[0] |= i8; return;
        case 0xCC00: SETT((RD8(R[0] + c->gbr) & i8) == 0); return;
        case 0xCD00: a = R[0] + c->gbr; WR8(a, RD8(a) & i8); return;
        case 0xCE00: a = R[0] + c->gbr; WR8(a, RD8(a) ^ i8); return;
        case 0xCF00: a = R[0] + c->gbr; WR8(a, RD8(a) | i8); return;
        }
        ILLEGAL();
    }
    case 0xD: R[n] = RD32(((c->pc + 4) & ~3u) + i8 * 4); return;
    case 0xE: R[n] = S8(i8); return;
    case 0xF: fpu_step(c, op, res); return;
    }
    ILLEGAL();
}

/* --------------------------------------------------------------------- *
 * Run up to max_iter loop iterations (a branch + its delay slot is one, as
 * in sh4.py). *done receives the count. Stops early for: idle-task stack
 * checker (R_IDLE, pc left there), SLEEP (R_SLEEP), a device write or MMU
 * register write (R_IO, so Python can deliver interrupts promptly),
 * LDTLB / MMU on (R_FALLBACK, pc left at the instruction).
 * --------------------------------------------------------------------- */
int core_run(Core *c, uint64_t max_iter, uint64_t *done)
{
    volatile uint64_t it = 0;
    volatile uint32_t ipc = 0;
    int reason = R_LIMIT;
    c->io_break = 0;
    g_fallback = 0;
    if (c->mmucr & 1) { *done = 0; return R_FALLBACK; }

    if (setjmp(g_jb)) {
        /* a precise fault from the instruction at ipc (or its slot) */
        enter_exc(c, x_off, x_code, ipc, x_hastea, x_tea);
        it++;
        if (c->io_break || (c->mmucr & 1)) { *done = it; return R_IO; }
    }
    while (it < max_iter) {
        uint32_t pc = c->pc;
        if (!c->low_is_ram && pc == 0xA8000800u) c->low_is_ram = 1;
        if (c->idle_skip && (pc & 0x1FFFFFFFu) == c->idle_pc) { reason = R_IDLE; break; }
        ipc = pc;
        uint32_t op = RD16(pc);
        uint32_t nxt = pc + 2;
        Res r;
        step(c, op, nxt, &r);
        if (g_fallback) { c->pc = pc; reason = R_FALLBACK; break; }
        c->steps++;
        switch (r.kind) {
        case K_NONE: c->pc = nxt; c->cycles += 1; break;
        case K_TIMED: c->pc = nxt; c->cycles += r.cyc; break;
        case K_BR: c->pc = r.tgt; c->cycles += r.cyc; break;
        case K_EXC: c->cycles += r.cyc; break;
        case K_ADV4: c->pc = pc + 4; c->cycles += r.cyc; break;
        case K_DBR: case K_RTE: {
            c->cycles += r.cyc;
            uint32_t dop = RD16(nxt);
            c->pc = nxt;
            Res d;
            step(c, dop, nxt + 2, &d);
            if (g_fallback) { c->pc = pc; reason = R_FALLBACK; goto out; }
            if (d.kind == K_BR || d.kind == K_DBR || d.kind == K_RTE || d.kind == K_EXC) {
                enter_exc(c, 0x100, 0x1A0, nxt, 0, 0);
            } else {
                c->steps++;
                if (r.kind == K_RTE) { set_sr(c, r.rssr); c->pc = r.tgt; }
                else c->pc = r.tgt;
            }
            break;
        }
        }
        it++;
        if (c->sleeping) { reason = R_SLEEP; break; }
        if (c->io_break) { reason = R_IO; break; }
    }
out:
    *done = it;
    return reason;
}

/* bulk helpers for Python-side tools */
uint32_t core_read_virt(Core *c, uint32_t a, int n, int *ok)
{
    if (setjmp(g_jb)) { *ok = 0; return 0; }
    uint32_t v = vrd(c, a, n);
    *ok = 1;
    return v;
}

/* HPID log accessor: returns a pointer to 2*n uint32s (addr, value pairs)
 * written through the CS6 data port. Caller must not free. */
uint32_t *core_hpid(Core *c, uint64_t *n)
{
    if (n) *n = c->hpid_n;
    return c->hpid_buf;
}

int core_sizeof(void) { return (int)sizeof(Core); }
