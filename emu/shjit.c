/* shjit.c -- SH-4 block translator, stage 2: native arm64 code per block.
 * #included by fastcore.c (uses its Core, Blk, Dtlb, the g_cls classes,
 * fast_step / step and the data-page cache).
 *
 * A block (blk_* in fastcore.c) is a straight run of decode-table
 * instructions ("body"), optionally closed by a delayed branch + its slot
 * ("terminator"). Once a block has been seen a few times it is compiled
 * into one arm64 function  uint32_t fn(Core *c)  that executes it with the
 * interpreter's exact semantics, stores the next pc in c->pc and the
 * interpreter's cur_pc in c->cur_pc, and returns what core_run adds:
 *      it | steps << 8 | cycles << 16 | io_break << 31
 * (all compile-time constants per exit point). core_run calls it only when
 * no COMMIT boundary falls inside and the slice budget covers the longest
 * path, so the counters the DSP clock reads mid-run are the interpreter's.
 *
 * Exactness rules the generated code follows:
 *  - SH registers stay in memory (c->r, c->sr, ...): nothing is cached
 *    across instructions, so a fault or callback sees the true state.
 *  - Memory: inline only the data-page cache HIT path of vrd/vwr (aligned,
 *    entry valid for this generation + key; stores also not framebuffer /
 *    flash). An instruction computes its address and looks it up before
 *    it changes anything; on a miss the WHOLE instruction runs through the
 *    reference handler (fast_step: faults, devices, cache fill) and the
 *    block ends after it (the stage-1 rule after a slow access), returning
 *    io_break when a device asked for it.
 *  - A store into the block's own code page ends the block after it.
 *  - Classes not emitted natively run through the reference handler too.
 *  - Delayed branches follow core_run's K_DBR path: the target is taken
 *    before the slot runs, the slot runs with c->pc = slot and cur_pc =
 *    the branch, the pair counts it 1 / steps 2 / the branch's cycles; a
 *    not-taken bt/s, bf/s is an ordinary instruction and its slot an
 *    ordinary next instruction.
 * Proof: tools/jit/run_selftest.sh (encoder vs assembler; random blocks vs
 * the interpreter's semantics), differ / dsp_ab on whole machines. */
#if defined(__aarch64__) && defined(__APPLE__)
#define SHJIT 1
#include <sys/mman.h>
#include <pthread.h>
#include <libkern/OSCacheControl.h>
#include <stddef.h>
#include "a64enc.h"

#define JIT_WORDS (16u << 20)            /* 64 MB of code */
#define JIT_HOT 2                         /* entries before compiling */
static uint32_t *g_jit_mem;
static uint32_t g_jit_pos;
static int g_jit_on = 1, g_jit_ok = -1;
static uint64_t g_jit_compiles, g_jit_calls, g_jit_flushes, g_jit_fail, g_jit_ops, g_jit_its;
void core_set_jit(int on) { g_jit_on = on; g_blk_epoch++; }
uint64_t core_jit_stat(int k)
{
    switch (k) {
    case 0: return g_jit_compiles; case 1: return g_jit_calls;
    case 2: return g_jit_flushes; case 3: return g_jit_pos * 4ull;
    case 4: return g_jit_fail;
    case 5: return g_jit_ops;
    default: return g_jit_its;
    }
}

static int jit_init(void)
{
    if (g_jit_ok >= 0) return g_jit_ok;
    void *p = mmap(0, JIT_WORDS * 4u, PROT_READ | PROT_WRITE | PROT_EXEC,
                   MAP_PRIVATE | MAP_ANON | MAP_JIT, -1, 0);
    g_jit_ok = p != MAP_FAILED;
    if (g_jit_ok) { g_jit_mem = (uint32_t *)p; g_jit_pos = 0; }
    return g_jit_ok;
}

/* an ordinary instruction through the reference handler. Returns flags:
 * 1 = end the block after it (slow access / own page), 2 = io_break */
static uint32_t jit_op(Core *c, uint32_t op, uint32_t k, uint32_t pc, const uint8_t *host)
{
    c->pc = pc; c->cur_pc = pc;
    int br = 0; uint32_t tgt = 0;
    g_wslow = 0; g_wlast = 0;
    g_jit_ops++;
    fast_step(c, op, k, &br, &tgt);
    uint32_t f = 0;
    if (c->io_break) f |= 2;
    if (g_wslow || (uintptr_t)(g_wlast - host) < 0x1000u) f |= 1;
    return f;
}
/* a delay slot through the reference handler (core_run: c->pc = slot,
 * cur_pc stays at the branch). Returns 2 = io_break. */
static uint32_t jit_slot(Core *c, uint32_t op, uint32_t k, uint32_t spc, uint32_t bpc)
{
    c->pc = spc; c->cur_pc = bpc;
    int br = 0; uint32_t tgt = 0;
    g_jit_ops++;
    fast_step(c, op, k, &br, &tgt);
    return c->io_break ? 2u : 0u;
}

/* ---- emitter state -------------------------------------------------- */
static uint32_t *jp, *jend;
static int jfail;
static uint32_t jkeybits;
static inline void E(uint32_t w) { if (jp < jend) *jp++ = w; else jfail = 1; }
enum { XC = 19, XT = 20, WG = 21, XH = 22, WTGT = 23 };
#define OFF(f) ((uint32_t)offsetof(Core, f))
static inline void ldR(int t, int n)  { E(a_ldrw_i(t, XC, OFF(r) + 4u * (uint32_t)n)); }
static inline void stR(int t, int n)  { E(a_strw_i(t, XC, OFF(r) + 4u * (uint32_t)n)); }
static inline void ldF(int t, uint32_t off) { E(a_ldrw_i(t, XC, off)); }
static inline void stF(int t, uint32_t off) { E(a_strw_i(t, XC, off)); }
static inline void movw32(int d, uint32_t v)
{
    E(a_movzw(d, v & 0xFFFF, 0));
    if (v >> 16) E(a_movkw(d, v >> 16, 1));
}
static inline void movx64(int d, uint64_t v)
{
    E(a_movzx(d, (uint32_t)(v & 0xFFFF), 0));
    for (int h = 1; h < 4; h++)
        if ((v >> (16 * h)) & 0xFFFF) E(a_movkx(d, (uint32_t)(v >> (16 * h)) & 0xFFFF, h));
}
static inline void call(void *fn)
{
    E(a_movx(0, XC));
    movx64(16, (uint64_t)(uintptr_t)fn);
    E(a_blr(16));
}
static inline uint32_t *fwd(uint32_t w) { uint32_t *p = jp; E(w); return p; }
static inline void patch_cc(uint32_t *at, uint32_t *to)
{ if (!jfail && at) *at = (*at & ~(0x7FFFFu << 5)) | (((uint32_t)(to - at) & 0x7FFFFu) << 5); }
static inline void patch_b(uint32_t *at, uint32_t *to)
{ if (!jfail && at) *at = (*at & ~0x3FFFFFFu) | ((uint32_t)(to - at) & 0x3FFFFFFu); }

/* ---- exits and chaining ---------------------------------------------- *
 * Native code runs inside jit_enter (generated once): x24/x25/x26 = the
 * it / steps / cycles core_run counts (uncommitted), x27 = the slice's
 * max_iter, x28 = &g_jg (vgen, epoch). An exit adds its constants, stores
 * c->pc / c->cur_pc, then either chains straight into the next block's
 * code (when that block is compiled, current and the pc is not one the
 * run loop watches) or returns to core_run (w0 = 1 for io_break). Every
 * block re-checks on entry that its longest path fits the slice and does
 * not cross a COMMIT point; if not it returns before doing anything, so
 * core_run takes over at exactly that pc. */
typedef struct { uint32_t it, st, cy; } Acc;
static uint32_t *g_jit_epi;                      /* shared epilogue */
static Core *jcore;                              /* the compiling core */
static int g_jit_chain = 1;
static inline void jret(uint32_t flags) { movw32(0, flags); uint32_t *p = jp; E(a_b(0)); patch_b(p, g_jit_epi); }
static inline void jacc(Acc a)
{
    if (a.it) E(a_addix(24, 24, a.it));
    if (a.st) E(a_addix(25, 25, a.st));
    if (a.cy) E(a_addix(26, 26, a.cy));
}
/* x9 = slot of the block for pc w12; checks pc, key, epoch, idle, vgen,
 * tracking; branches out (ret0 sites) on any mismatch, else jumps */
static void chain_tail(uint32_t keybits, uint32_t **ret0, int *nr)
{
    /* x9 = &g_blk[slot], w12 = target pc */
    E(a_ldpw(10, 11, 9, 0));                     /* pc, key */
    E(a_cmpw(10, 12)); ret0[(*nr)++] = fwd(a_bcc(A_NE, 0));
    E(a_andiw_ones(13, 12, 20, 20));
    if (keybits == 1) E(a_orriw_ones(13, 13, 1, 0));
    else if (keybits == 2) E(a_orriw_ones(13, 13, 1, 31));
    else if (keybits == 3) E(a_orriw_ones(13, 13, 2, 0));
    E(a_cmpw(11, 13)); ret0[(*nr)++] = fwd(a_bcc(A_NE, 0));
    E(a_ldpw(10, 11, 9, 8));                     /* epoch, idle */
    E(a_ldrw_i(13, 28, 4)); E(a_cmpw(10, 13)); ret0[(*nr)++] = fwd(a_bcc(A_NE, 0));
    ldF(13, OFF(idle_pc)); E(a_cmpw(11, 13)); ret0[(*nr)++] = fwd(a_bcc(A_NE, 0));
    /* no code-change check here: every block verifies its own opcodes */
    E(a_ldrx_i(16, 9, (uint32_t)offsetof(Blk, fn))); ret0[(*nr)++] = fwd(a_cbzx(16, 0));
    E(a_br(16));
}
static inline void special_out(int r, uint32_t **ret0, int *nr)
{
    /* w(r) = pc: never chain into a pc the run loop handles itself */
    E(a_andiw_ones(13, r, 29, 0)); ldF(14, OFF(idle_pc));
    E(a_cmpw(13, 14)); ret0[(*nr)++] = fwd(a_bcc(A_EQ, 0));
    static const uint32_t sp[3] = { 0x08DC40B8u, 0x08E1D994u, 0xA8000800u };
    for (int q = 0; q < 3; q++) {
        movw32(14, sp[q]); E(a_cmpw(r, 14)); ret0[(*nr)++] = fwd(a_bcc(A_EQ, 0));
    }
}
static void chain_const(uint32_t next)
{
    if (!g_jit_chain || (next & 1u) || blk_special(jcore, next)) { jret(0); return; }
    uint32_t *ret0[16]; int nr = 0;
    movw32(12, next);
    special_out(12, ret0, &nr);                  /* idle pc may have moved */
    movx64(9, (uint64_t)(uintptr_t)&g_blk[BLK_IDX(next)]);
    chain_tail(jkeybits, ret0, &nr);
    for (int q = 0; q < nr; q++) patch_cc(ret0[q], jp);
    jret(0);
}
static void chain_dyn(void)
{
    if (!g_jit_chain) { jret(0); return; }
    uint32_t *ret0[16]; int nr = 0;
    E(a_tstiw_ones(WTGT, 1, 0)); ret0[nr++] = fwd(a_bcc(A_NE, 0));
    E(a_movw(12, WTGT));
    special_out(12, ret0, &nr);
    /* w9 = BLK_IDX(w12) */
    E(a_lsrw(9, 12, 1)); E(a_lsrw(13, 12, 1 + BLK_BITS));
    E(a_eorw(9, 9, 13)); E(a_andiw_ones(9, 9, BLK_BITS, 0));
    movx64(10, (uint64_t)(uintptr_t)g_blk);
    movw32(11, (uint32_t)sizeof(Blk));
    E(a_maddx(9, 9, 11, 10));
    chain_tail(jkeybits, ret0, &nr);
    for (int q = 0; q < nr; q++) patch_cc(ret0[q], jp);
    jret(0);
}
/* exit with constant next pc; chain = may jump on to the next block */
static inline void jexit(Acc a, uint32_t io, uint32_t next, uint32_t last, int chain)
{
    jacc(a);
    movw32(1, next); stF(1, OFF(pc));
    movw32(1, last); stF(1, OFF(cur_pc));
    if (io) jret(1); else if (chain) chain_const(next); else jret(0);
}
/* exit with next pc = w23 (taken delayed branch) */
static inline void jexit_tgt(Acc a, uint32_t io, uint32_t last, int chain)
{
    jacc(a);
    stF(WTGT, OFF(pc));
    movw32(1, last); stF(1, OFF(cur_pc));
    if (io) jret(1); else if (chain) chain_dyn(); else jret(0);
}
static inline void setT(int t) { ldF(2, OFF(sr)); E(a_bfi0w(2, t)); stF(2, OFF(sr)); }

/* ---- slow stubs (emitted after the body) ----------------------------- *
 * kind 0: instruction miss -> jit_op, exit after it (next/last constants)
 * kind 1: own code page store -> exit after the instruction
 * kind 2: delay slot miss -> jit_slot, then the taken pair's exit (w23)  */
#define MAXSTUB (BMAX * 4 + 8)
typedef struct { uint32_t *site[4]; int ns, kind; uint32_t op, k, pc, bpc, next; Acc a; } Stub;
static Stub jstub[MAXSTUB];
static int jnstub;
static int new_stub(int kind, uint32_t op, uint32_t k, uint32_t pc, uint32_t bpc, uint32_t next, Acc a)
{
    if (jnstub >= MAXSTUB) { jfail = 1; return 0; }
    Stub *s = &jstub[jnstub];
    s->ns = 0; s->kind = kind; s->op = op; s->k = k; s->pc = pc; s->bpc = bpc;
    s->next = next; s->a = a;
    return jnstub++;
}
static inline void stub_site(int s, uint32_t *p) { if (jstub[s].ns < 4) jstub[s].site[jstub[s].ns++] = p; else jfail = 1; }

/* address in w0 -> x6 = host page, x7 = page offset; misses go to stub s.
 * Clobbers w1..w5 (w8 for stores). */
static void lookup(int n, int store, int s)
{
    if (n > 1) { E(a_tstiw_ones(0, n == 4 ? 2 : 1, 0)); stub_site(s, fwd(a_bcc(A_NE, 0))); }
    E(a_ubfxw(1, 0, 12, 8));
    E(a_addx_lsl(2, XT, 1, 5));
    E(a_ldpw(3, 4, 2, 0));
    E(a_andiw_ones(5, 0, 20, 20));
    if (jkeybits == 1) E(a_orriw_ones(5, 5, 1, 0));
    else if (jkeybits == 2) E(a_orriw_ones(5, 5, 1, 31));
    else if (jkeybits == 3) E(a_orriw_ones(5, 5, 2, 0));
    E(a_cmpw(3, 5)); stub_site(s, fwd(a_bcc(A_NE, 0)));
    E(a_cmpw(4, WG)); stub_site(s, fwd(a_bcc(A_NE, 0)));
    E(a_ldrx_i(6, 2, 8));
    if (store) { E(a_ldrw_i(8, 2, 16)); stub_site(s, fwd(a_cbnzw(8, 0))); }   /* fb | ro | code */
    E(a_andiw_ones(7, 0, 12, 0));
}
static inline void load(int n) { E(n == 4 ? a_ldrw_r(0, 6, 7) : n == 2 ? a_ldrhw_r(0, 6, 7) : a_ldrbw_r(0, 6, 7)); }
static inline void store(int n) { E(n == 4 ? a_strw_r(9, 6, 7) : n == 2 ? a_strhw_r(9, 6, 7) : a_strbw_r(9, 6, 7)); }

/* how the instruction being emitted ends: a body / not-taken-slot
 * instruction (kind 0 stubs, own-page check) or a taken delay slot */
static struct { int slot; uint32_t bpc, next; Acc a; } jctx;

static inline int memstub(uint32_t op, uint32_t k, uint32_t pc)
{
    return jctx.slot ? new_stub(2, op, k, pc, jctx.bpc, 0, jctx.a)
                     : new_stub(0, op, k, pc, 0, jctx.next, jctx.a);
}
/* after a store instruction's last effect: x6 still = the stored page */
static inline void own_check(uint32_t pc)
{
    if (jctx.slot) return;                       /* the pair ends the block */
    E(a_cmpx(6, XH));
    int o = new_stub(1, 0, 0, pc, 0, jctx.next, jctx.a);
    stub_site(o, fwd(a_bcc(A_EQ, 0)));
}

/* ---- one non-branch instruction (BT/BF handled by the caller) ---------- */
static void emit_insn(uint32_t op, uint32_t k, uint32_t pc)
{
    uint32_t n = (op >> 8) & 0xF, m = (op >> 4) & 0xF, i8 = op & 0xFF;
    int s;
#define MS() (s = memstub(op, k, pc))
    switch (k) {
    case F_MOV: ldR(1, m); stR(1, n); return;
    case F_ADD: ldR(1, n); ldR(2, m); E(a_addw(1, 1, 2)); stR(1, n); return;
    case F_SUB: ldR(1, n); ldR(2, m); E(a_subw(1, 1, 2)); stR(1, n); return;
    case F_ADDI: ldR(1, n); movw32(2, S8(i8)); E(a_addw(1, 1, 2)); stR(1, n); return;
    case F_MOVI: movw32(1, S8(i8)); stR(1, n); return;
    case F_MOVA: movw32(1, ((pc + 4) & ~3u) + i8 * 4); stR(1, 0); return;
    case F_EXTUB: ldR(1, m); E(a_uxtbw(1, 1)); stR(1, n); return;
    case F_EXTUW: ldR(1, m); E(a_uxthw(1, 1)); stR(1, n); return;
    case F_EXTSB: ldR(1, m); E(a_sxtbw(1, 1)); stR(1, n); return;
    case F_EXTSW: ldR(1, m); E(a_sxthw(1, 1)); stR(1, n); return;
    case F_NOT: ldR(1, m); E(a_mvnw(1, 1)); stR(1, n); return;
    case F_NEG: ldR(1, m); E(a_negw(1, 1)); stR(1, n); return;
    case F_AND: ldR(1, n); ldR(2, m); E(a_andw(1, 1, 2)); stR(1, n); return;
    case F_OR:  ldR(1, n); ldR(2, m); E(a_orrw(1, 1, 2)); stR(1, n); return;
    case F_XOR: ldR(1, n); ldR(2, m); E(a_eorw(1, 1, 2)); stR(1, n); return;
    case F_ANDI: ldR(1, 0); movw32(2, i8); E(a_andw(1, 1, 2)); stR(1, 0); return;
    case F_ORI:  ldR(1, 0); movw32(2, i8); E(a_orrw(1, 1, 2)); stR(1, 0); return;
    case F_TST: ldR(1, n); ldR(2, m); E(a_tstw(1, 2)); E(a_csetw(3, A_EQ)); setT(3); return;
    case F_TSTI: ldR(1, 0); movw32(2, i8); E(a_tstw(1, 2)); E(a_csetw(3, A_EQ)); setT(3); return;
    case F_CMPEQ: ldR(1, n); ldR(2, m); E(a_cmpw(1, 2)); E(a_csetw(3, A_EQ)); setT(3); return;
    case F_CMPHS: ldR(1, n); ldR(2, m); E(a_cmpw(1, 2)); E(a_csetw(3, A_HS)); setT(3); return;
    case F_CMPGE: ldR(1, n); ldR(2, m); E(a_cmpw(1, 2)); E(a_csetw(3, A_GE)); setT(3); return;
    case F_CMPHI: ldR(1, n); ldR(2, m); E(a_cmpw(1, 2)); E(a_csetw(3, A_HI)); setT(3); return;
    case F_CMPGT: ldR(1, n); ldR(2, m); E(a_cmpw(1, 2)); E(a_csetw(3, A_GT)); setT(3); return;
    case F_CMPEQI: ldR(1, 0); movw32(2, S8(i8)); E(a_cmpw(1, 2)); E(a_csetw(3, A_EQ)); setT(3); return;
    case F_CMPPZ: ldR(1, n); E(a_cmpiw(1, 0)); E(a_csetw(3, A_GE)); setT(3); return;
    case F_CMPPL: ldR(1, n); E(a_cmpiw(1, 0)); E(a_csetw(3, A_GT)); setT(3); return;
    case F_SHLL: ldR(1, n); E(a_lsrw(3, 1, 31)); E(a_lslw(1, 1, 1)); stR(1, n); setT(3); return;
    case F_SHLR: ldR(1, n); E(a_movw(3, 1)); E(a_lsrw(1, 1, 1)); stR(1, n); setT(3); return;
    case F_SHAR: ldR(1, n); E(a_movw(3, 1)); E(a_asrw(1, 1, 1)); stR(1, n); setT(3); return;
    case F_SHLL2: ldR(1, n); E(a_lslw(1, 1, 2)); stR(1, n); return;
    case F_SHLL8: ldR(1, n); E(a_lslw(1, 1, 8)); stR(1, n); return;
    case F_SHLL16: ldR(1, n); E(a_lslw(1, 1, 16)); stR(1, n); return;
    case F_SHLR2: ldR(1, n); E(a_lsrw(1, 1, 2)); stR(1, n); return;
    case F_SHLR8: ldR(1, n); E(a_lsrw(1, 1, 8)); stR(1, n); return;
    case F_SHLR16: ldR(1, n); E(a_lsrw(1, 1, 16)); stR(1, n); return;
    case F_DT: ldR(1, n); E(a_subiw(1, 1, 1)); stR(1, n); E(a_cmpiw(1, 0)); E(a_csetw(3, A_EQ)); setT(3); return;
    case F_STSMACH: ldF(1, OFF(mach)); stR(1, n); return;
    case F_STSMACL: ldF(1, OFF(macl)); stR(1, n); return;
    case F_MULL: ldR(1, n); ldR(2, m); E(a_mulw(1, 1, 2)); stF(1, OFF(macl)); return;
    /* ---- memory */
    case F_MOVLPC: MS(); movw32(0, ((pc + 4) & ~3u) + i8 * 4); lookup(4, 0, s); load(4); stR(0, n); return;
    case F_MOVWPC: MS(); movw32(0, pc + 4 + i8 * 2); lookup(2, 0, s); load(2); E(a_sxthw(0, 0)); stR(0, n); return;
    case F_MOVL_LD: MS(); ldR(0, m); lookup(4, 0, s); load(4); stR(0, n); return;
    case F_MOVW_LD: MS(); ldR(0, m); lookup(2, 0, s); load(2); E(a_sxthw(0, 0)); stR(0, n); return;
    case F_MOVB_LD: MS(); ldR(0, m); lookup(1, 0, s); load(1); E(a_sxtbw(0, 0)); stR(0, n); return;
    case F_MOVL_LDD: MS(); ldR(0, m); E(a_addiw(0, 0, (op & 0xF) * 4)); lookup(4, 0, s); load(4); stR(0, n); return;
    case F_MOVL_LD0: MS(); ldR(0, 0); ldR(1, m); E(a_addw(0, 0, 1)); lookup(4, 0, s); load(4); stR(0, n); return;
    case F_MOVB_LD0: MS(); ldR(0, 0); ldR(1, m); E(a_addw(0, 0, 1)); lookup(1, 0, s); load(1); E(a_sxtbw(0, 0)); stR(0, n); return;
    case F_MOVLGBR: MS(); ldF(0, OFF(gbr)); E(a_addiw(0, 0, i8 * 4)); lookup(4, 0, s); load(4); stR(0, 0); return;
    case F_MOVL_POP: case F_MOVW_POP: case F_MOVB_POP: {
        int sz = k == F_MOVL_POP ? 4 : k == F_MOVW_POP ? 2 : 1;
        MS(); ldR(0, m); lookup(sz, 0, s); load(sz);
        if (sz == 2) E(a_sxthw(0, 0)); else if (sz == 1) E(a_sxtbw(0, 0));
        stR(0, n);
        if (n != m) { ldR(1, m); E(a_addiw(1, 1, (uint32_t)sz)); }
        else E(a_addiw(1, 0, (uint32_t)sz));
        stR(1, m);
        return;
    }
    case F_LDSPR_POP: MS(); ldR(0, n); lookup(4, 0, s); load(4); stF(0, OFF(pr));
        ldR(1, n); E(a_addiw(1, 1, 4)); stR(1, n); return;
    case F_MOVL_ST: case F_MOVW_ST: case F_MOVB_ST: {
        int sz = k == F_MOVL_ST ? 4 : k == F_MOVW_ST ? 2 : 1;
        MS(); ldR(0, n); ldR(9, m); lookup(sz, 1, s); store(sz); own_check(pc); return;
    }
    case F_MOVL_STD: MS(); ldR(0, n); E(a_addiw(0, 0, (op & 0xF) * 4)); ldR(9, m);
        lookup(4, 1, s); store(4); own_check(pc); return;
    case F_MOVL_ST0: MS(); ldR(0, 0); ldR(1, n); E(a_addw(0, 0, 1)); ldR(9, m);
        lookup(4, 1, s); store(4); own_check(pc); return;
    case F_MOVL_PUSH: MS(); ldR(0, n); E(a_subiw(0, 0, 4));
        if (m == n) E(a_movw(9, 0)); else ldR(9, m);
        lookup(4, 1, s); store(4); stR(0, n); own_check(pc); return;
    case F_STSPR_PUSH: MS(); ldR(0, n); E(a_subiw(0, 0, 4)); ldF(9, OFF(pr));
        lookup(4, 1, s); store(4); stR(0, n); own_check(pc); return;
    case F_STSMACL_PUSH: MS(); ldR(0, n); E(a_subiw(0, 0, 4)); ldF(9, OFF(macl));
        lookup(4, 1, s); store(4); stR(0, n); own_check(pc); return;
    case F_LDSMACL_POP: MS(); ldR(0, n); lookup(4, 0, s); load(4); stF(0, OFF(macl));
        ldR(1, n); E(a_addiw(1, 1, 4)); stR(1, n); return;
    case F_MOVW_LD0: MS(); ldR(0, 0); ldR(1, m); E(a_addw(0, 0, 1)); lookup(2, 0, s); load(2); E(a_sxthw(0, 0)); stR(0, n); return;
    case F_MOVB_LDD: MS(); ldR(0, m); E(a_addiw(0, 0, op & 0xF)); lookup(1, 0, s); load(1); E(a_sxtbw(0, 0)); stR(0, 0); return;
    case F_MOVW_LDD: MS(); ldR(0, m); E(a_addiw(0, 0, (op & 0xF) * 2)); lookup(2, 0, s); load(2); E(a_sxthw(0, 0)); stR(0, 0); return;
    case F_MOVB_STD8: case F_MOVW_STD8: {
        int sz = k == F_MOVW_STD8 ? 2 : 1;
        MS(); ldR(0, m); E(a_addiw(0, 0, (op & 0xF) * (uint32_t)sz)); ldR(9, 0);
        lookup(sz, 1, s); store(sz); own_check(pc); return;
    }
    case F_MOVB_PUSH: case F_MOVW_PUSH: {
        int sz = k == F_MOVW_PUSH ? 2 : 1;
        MS(); ldR(0, n); E(a_subiw(0, 0, (uint32_t)sz));
        if (m == n) E(a_movw(9, 0)); else ldR(9, m);
        lookup(sz, 1, s); store(sz); stR(0, n); own_check(pc); return;
    }
    case F_STSFPSCR: ldF(1, OFF(fpscr)); stR(1, n); return;
    case F_LDSFPSCR: ldR(1, n); stF(1, OFF(fpscr)); return;
    }
#undef MS
    /* everything else (no memory: see blk_is_mem): the reference handler */
    movw32(1, op); movw32(2, k); movw32(3, pc);
    if (jctx.slot) { movw32(4, jctx.bpc); call((void *)jit_slot); }
    else { E(a_movx(4, XH)); call((void *)jit_op); }
}

/* the taken pair's exit after a native slot */
static inline void pair_exit(void) { jexit_tgt(jctx.a, 0, jctx.bpc, 1); }

/* the shared entry / exit (at the start of the code buffer):
 *   uint32 jit_enter(Core *c, void *block_code, JCtx *x) */
typedef struct { uint64_t it, steps, cycles, max; uint32_t flags, pad; } JCtx;
static uint32_t (*jit_enter)(Core *, void *, JCtx *);
static uint32_t g_jit_base;                    /* first word after the trampoline */
static void jit_trampoline(void)
{
    jp = g_jit_mem; jend = g_jit_mem + JIT_WORDS; jfail = 0;
    E(a_stpx_pre(29, 30, -112)); E(a_mov_fp_sp());
    E(a_stpx(19, 20, 31, 16)); E(a_stpx(21, 22, 31, 32)); E(a_stpx(23, 24, 31, 48));
    E(a_stpx(25, 26, 31, 64)); E(a_stpx(27, 28, 31, 80));
    E(a_strx_i(2, 31, 96));
    E(a_movx(XC, 0));
    movx64(XT, (uint64_t)(uintptr_t)g_dtlb_t);
    movx64(9, (uint64_t)(uintptr_t)&g_dtlb_gen); E(a_ldrw_i(WG, 9, 0));
    movx64(28, (uint64_t)(uintptr_t)g_jg);
    E(a_ldpx(24, 25, 2, 0)); E(a_ldpx(26, 27, 2, 16));
    E(a_br(1));
    g_jit_epi = jp;
    E(a_ldrx_i(2, 31, 96));
    E(a_stpx(24, 25, 2, 0)); E(a_strx_i(26, 2, 16)); E(a_strw_i(0, 2, 32));
    E(a_ldpx(27, 28, 31, 80)); E(a_ldpx(25, 26, 31, 64)); E(a_ldpx(23, 24, 31, 48));
    E(a_ldpx(21, 22, 31, 32)); E(a_ldpx(19, 20, 31, 16));
    E(a_ldpx_post(29, 30, 112)); E(a_ret());
    jit_enter = (uint32_t (*)(Core *, void *, JCtx *))(void *)g_jit_mem;
    g_jit_base = g_jit_pos = (uint32_t)((jp - g_jit_mem + 15) & ~15);
}

/* compile block b; 0 on failure (block keeps running in stage 1) */
static int jit_compile(Core *c, Blk *b)
{
    if (!jit_init()) return 0;
    pthread_jit_write_protect_np(0);
    if (!jit_enter) jit_trampoline();
    if (g_jit_pos + 8192u > JIT_WORDS) {          /* full: start over */
        g_jit_pos = g_jit_base; g_blk_epoch++; g_jit_flushes++;
        pthread_jit_write_protect_np(1);
        return 0;
    }
    uint32_t *start = g_jit_mem + g_jit_pos;
    jp = start; jend = g_jit_mem + JIT_WORDS; jfail = 0; jnstub = 0;
    jkeybits = b->key & 3u; jcore = c;
    /* entry: the longest path must fit the slice and stay before the next
     * COMMIT point, else return untouched (core_run continues here) */
    uint32_t U = blk_len(b);
    uint32_t *bail[2];
    E(a_andiw_ones(9, 24, 6, 0)); E(a_addiw(9, 9, U)); E(a_cmpiw(9, 64)); bail[0] = fwd(a_bcc(A_HS, 0));
    E(a_addix(10, 24, U)); E(a_cmpx(10, 27)); bail[1] = fwd(a_bcc(A_HI, 0));
    movx64(XH, (uint64_t)(uintptr_t)b->host);
    /* the opcodes this code was made from must still be in memory (any
     * writer: stores, DMA, Python, mods): compare them, else return
     * flags 2 and core_run rebuilds the block. Ops never cross the page. */
    {
        uint32_t *stale[24]; int ns = 0;
        uint32_t off = b->pc & 0xFFFu, nh = U, q = 0;
        E(a_addix(9, XH, off));
        for (; q + 4 <= nh; q += 4) {
            uint64_t v = 0;
            for (int h = 0; h < 4; h++) v |= (uint64_t)b->ops[q + h] << (16 * h);
            E(a_ldrx_i(10, 9, q * 2)); movx64(11, v); E(a_cmpx(10, 11)); stale[ns++] = fwd(a_bcc(A_NE, 0));
        }
        if (q + 2 <= nh) {
            uint32_t v = (uint32_t)b->ops[q] | (uint32_t)b->ops[q + 1] << 16;
            E(a_ldrw_i(10, 9, q * 2)); movw32(11, v); E(a_cmpw(10, 11)); stale[ns++] = fwd(a_bcc(A_NE, 0));
            q += 2;
        }
        if (q < nh) {
            E(a_ldrhw_i2(10, 9, q * 2)); movw32(11, b->ops[q]); E(a_cmpw(10, 11)); stale[ns++] = fwd(a_bcc(A_NE, 0));
        }
        uint32_t *over = fwd(a_b(0));
        for (int z = 0; z < ns; z++) patch_cc(stale[z], jp);
        /* stale: unhook this code from its slot (core_run then rebuilds the
         * block at c->pc, which is this block's pc) */
        movx64(9, (uint64_t)(uintptr_t)b);
        E(a_strx_i(31, 9, (uint32_t)offsetof(Blk, fn)));
        jret(2);
        patch_b(over, jp);
    }
    /* body */
    uint32_t pc = b->pc, n = b->n, cy = 0;
    for (uint32_t j = 0; j < n; j++, pc += 2) {
        uint32_t op = b->ops[j], k = b->k[j];
        uint32_t c1 = cls_cyc(k);
        Acc a = { j + 1, j + 1, cy + c1 };
        if (k == F_BT || k == F_BF) {             /* last body instruction */
            uint32_t i8 = op & 0xFF;
            ldF(2, OFF(sr)); E(a_tstiw_ones(2, 1, 0));
            uint32_t *nt = fwd(a_bcc(k == F_BT ? A_EQ : A_NE, 0));
            jexit(a, 0, pc + S8(i8) * 2 + 4, pc, 1);
            patch_cc(nt, jp);
            jexit(a, 0, pc + 2, pc, 1);
            goto stubs;
        }
        jctx.slot = 0; jctx.next = pc + 2; jctx.a = a;
        emit_insn(op, k, pc);
        cy += c1;
    }
    if (!b->tkind) {
        Acc a = { n, n, cy };
        jexit(a, 0, pc, pc - 2, 1);
        goto stubs;
    }
    /* terminator: delayed branch at pc, slot at pc + 2 */
    {
        uint32_t bop = b->ops[n], sop = b->ops[n + 1], sk = b->k[n + 1];
        uint32_t bpc = pc, spc = pc + 2, rn = (bop >> 8) & 0xF;
        uint32_t scy = cls_cyc(sk), bcy = 2;
        uint32_t *nt = 0;
        int32_t d12 = (int32_t)(bop & 0xFFF); if (d12 & 0x800) d12 -= 0x1000;
        switch (b->tkind) {
        case T_BRA:  movw32(WTGT, bpc + 4 + (uint32_t)(d12 * 2)); break;
        case T_BSR:  movw32(1, bpc + 4); stF(1, OFF(pr)); movw32(WTGT, bpc + 4 + (uint32_t)(d12 * 2)); break;
        case T_RTS:  ldF(WTGT, OFF(pr)); break;
        case T_JSR:  movw32(1, bpc + 4); stF(1, OFF(pr)); ldR(WTGT, (int)rn); break;
        case T_JMP:  ldR(WTGT, (int)rn); break;
        case T_BSRF: movw32(1, bpc + 4); stF(1, OFF(pr)); ldR(WTGT, (int)rn);
                     movw32(2, bpc + 4); E(a_addw(WTGT, WTGT, 2)); break;
        case T_BRAF: ldR(WTGT, (int)rn); movw32(2, bpc + 4); E(a_addw(WTGT, WTGT, 2)); break;
        case T_BTS: case T_BFS:
            bcy = 1;
            movw32(WTGT, bpc + S8(bop & 0xFF) * 2 + 4);
            ldF(2, OFF(sr)); E(a_tstiw_ones(2, 1, 0));
            nt = fwd(a_bcc(b->tkind == T_BTS ? A_EQ : A_NE, 0));
            break;
        }
        /* taken: slot runs with the pair's accounting */
        jctx.slot = 1; jctx.bpc = bpc; jctx.a = (Acc){ n + 1, n + 2, cy + bcy };
        emit_insn(sop, sk, spc);
        if (sk == F_SHAD || sk == F_SHLD) {      /* went through jit_slot: io? */
            E(a_tstiw_ones(0, 1, 31));
            uint32_t *io = fwd(a_bcc(A_NE, 0));
            pair_exit();
            patch_cc(io, jp);
            jexit_tgt(jctx.a, 1, bpc, 0);
        } else pair_exit();
        if (nt) {                                 /* not taken: two ordinary steps */
            patch_cc(nt, jp);
            jctx.slot = 0; jctx.next = spc + 2;
            jctx.a = (Acc){ n + 2, n + 2, cy + 1 + scy };
            emit_insn(sop, sk, spc);
            if (sk == F_SHAD || sk == F_SHLD) {  /* jit_op: io? */
                E(a_tstiw_ones(0, 1, 31));
                uint32_t *io = fwd(a_bcc(A_NE, 0));
                jexit(jctx.a, 0, spc + 2, spc, 1);
                patch_cc(io, jp);
                jexit(jctx.a, 1, spc + 2, spc, 0);
            } else jexit(jctx.a, 0, spc + 2, spc, 1);
        }
    }
stubs:
    for (int s = 0; s < jnstub; s++) {
        Stub *t = &jstub[s];
        for (int q = 0; q < t->ns; q++) patch_cc(t->site[q], jp);
        if (t->kind == 1) { jexit(t->a, 0, t->next, t->pc, 0); continue; }
        movw32(1, t->op); movw32(2, t->k); movw32(3, t->pc);
        if (t->kind == 0) { E(a_movx(4, XH)); call((void *)jit_op); }
        else { movw32(4, t->bpc); call((void *)jit_slot); }
        E(a_tstiw_ones(0, 1, 31));                /* tst w0, #2: io_break */
        uint32_t *io = fwd(a_bcc(A_NE, 0));
        if (t->kind == 0) jexit(t->a, 0, t->next, t->pc, 0); else jexit_tgt(t->a, 0, t->bpc, 0);
        patch_cc(io, jp);
        if (t->kind == 0) jexit(t->a, 1, t->next, t->pc, 0); else jexit_tgt(t->a, 1, t->bpc, 0);
    }
    patch_cc(bail[0], jp); patch_cc(bail[1], jp);
    jret(0);
    pthread_jit_write_protect_np(1);
    if (jfail) { g_jit_fail++; return 0; }
    sys_icache_invalidate(start, (size_t)(jp - start) * 4u);
    g_jit_pos = (uint32_t)(jp - g_jit_mem);
    g_jit_pos = (g_jit_pos + 3u) & ~3u;
    b->fn = (uint32_t (*)(Core *))(void *)start;
    g_jit_compiles++;
    return 1;
}
#endif
