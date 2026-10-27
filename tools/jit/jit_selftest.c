/* jit_selftest.c -- random blocks: native code (emu/shjit.c) vs the
 * stage-1 block runner (fast_step per instruction, same end rules), from
 * identical random states over fake memory with logging fake devices.
 * Blocks may end in a delayed branch + slot; the reference runs those
 * exactly as core_run's K_DBR path does (copied below). Both sides run the
 * block "to completion" (a side that ends the block early continues the
 * remaining instructions in the reference, exactly as core_run would
 * dispatch them), then everything is compared: registers, SR, GBR/MACH/
 * MACL/PR, pc, cur_pc, RAM windows, the device log, io_break, fb_writes,
 * exceptions, and the it / steps / cycles core_run would count.
 *   sh tools/jit/run_selftest.sh [blocks]      (exit 0 = PASS) */
#include "../../emu/fastcore.c"
#include <stdio.h>
#include <stdlib.h>

#ifndef SHJIT
int main(void) { printf("no JIT on this platform\nRESULT: SKIP\n"); return 0; }
#else
#define WIN 0x4000u
#define CODE 0x0C002000u
static uint32_t lg[512][3]; static int lgn;
static Core *g_cur;
static void logio(uint32_t kind, uint32_t a, uint32_t v)
{
    if (lgn < 512) { lg[lgn][0] = kind; lg[lgn][1] = a; lg[lgn][2] = v; }
    lgn++;
    if (((a * 2654435761u) >> 28) == 7) g_cur->io_break = 1;   /* sometimes */
}
static uint32_t s_rd(uint32_t a) { logio(1, a, 0); return (a * 2654435761u) >> 7; }
static void s_wr(uint32_t a, uint32_t v) { logio(2, a, v); }
static uint32_t s_rds(uint32_t a, uint32_t n) { logio(3, a, n); return (a * 2654435761u) >> 3; }
static void s_wrs(uint32_t a, uint32_t v, uint32_t n) { logio(4, a, v ^ (n << 28)); }

static uint32_t x = 0x2545F491u;
static uint32_t rng(void) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; return x; }
static int g_clean;      /* argv[2] == "clean": aligned pointers, all pages cached */
static uint32_t pick(void)
{
    if (g_clean) {
        uint32_t c = rng() % 10;
        if (c < 2) return rng() & 0x7FC;
        if (c < 9) {                                  /* not the code page */
            uint32_t p = 0x0C000000u + (rng() & 0x1FFC);
            return (rng() & 3) ? p : p + 0x3000u - 0x1000u * (rng() & 1);
        }
        return rng() & 0xFFFF;                         /* plain numbers */
    }
    uint32_t c = rng() % 20;
    if (c < 4) return rng() & 0x7FC;                 /* small (low RAM window) */
    if (c < 18) {                                     /* window pointer */
        uint32_t p = 0x0C000000u + (rng() & 0x3FFC);
        if (!(rng() & 31)) p += rng() & 3;            /* sometimes misaligned */
        if (!(rng() & 3)) p |= 0x80000000u;           /* P1 view */
        return p;
    }
    return rng();                                     /* anything */
}

/* the stage-1 runner on b from instruction j0 (pc = b->pc + 2*j0) until the
 * block's end / a taken branch / io_break; returns instructions executed,
 * adds cycles; sets *stop = 1 when the run must not continue (branch or
 * io), and *endpc */
static uint32_t ref_run(Core *c, Blk *b, uint32_t j0, uint64_t *cyc, int *stop, int *io)
{
    uint32_t bp = b->pc + 2 * j0, ex = 0;
    *stop = 0; *io = 0;
    for (uint32_t j = j0; j < b->n; j++) {
        c->pc = bp; c->cur_pc = bp;
        int br = 0; uint32_t tgt = 0;
        int mem = blk_is_mem(b->k[j]);
        if (mem) { g_wslow = 0; g_wlast = 0; }
        *cyc += fast_step(c, b->ops[j], b->k[j], &br, &tgt);
        ex++;
        if (br) { bp = tgt; *stop = 1; break; }
        bp += 2;
        if (c->io_break) { *io = 1; *stop = 1; break; }
        if (mem && (g_wslow || (uintptr_t)(g_wlast - b->host) < 0x1000u)) break;
    }
    c->pc = bp;
    return ex;
}

/* core_run's delayed-branch handling for the terminator (fastcore.c
 * core_run, case K_DBR), then the not-taken bt/s, bf/s slot as the next
 * ordinary instruction */
static void ref_term(Core *c, Blk *b, uint64_t *it, uint64_t *st, uint64_t *cy, int *io)
{
    uint32_t bpc = b->pc + 2 * b->n, spc = bpc + 2;
    c->pc = bpc; c->cur_pc = bpc;
    Res r;
    step(c, b->ops[b->n], bpc + 2, &r);
    (*st)++;
    if (r.kind == K_DBR) {
        *cy += r.cyc;
        uint32_t dop = RD16(spc);
        c->pc = spc;
        Res d;
        step(c, dop, spc + 2, &d);
        if (d.kind == K_BR || d.kind == K_DBR || d.kind == K_RTE || d.kind == K_EXC)
            enter_exc(c, 0x100, 0x1A0, spc, 0, 0);
        else { (*st)++; c->pc = r.tgt; }
        (*it)++;
        if (c->io_break) *io = 1;
        return;
    }
    /* K_NONE: not taken */
    c->pc = spc; *cy += 1; (*it)++;
    if (c->io_break) { *io = 1; return; }
    c->pc = spc; c->cur_pc = spc;
    int br = 0; uint32_t tgt = 0;
    *cy += fast_step(c, b->ops[b->n + 1], b->k[b->n + 1], &br, &tgt);
    (*st)++; (*it)++;
    c->pc = br ? tgt : spc + 2;
    if (c->io_break) *io = 1;
}

/* the reference from body position j: stage-1 body runs (continuing after
 * early ends, as core_run's next dispatch would), then the terminator */
static void ref_from(Core *c, Blk *b, uint32_t j, uint64_t *it, uint64_t *st, uint64_t *cy, int *io)
{
    while (j < b->n) {
        int stop, i2;
        uint64_t y = 0;
        uint32_t ex = ref_run(c, b, j, &y, &stop, &i2);
        *it += ex; *st += ex; *cy += y; j += ex;
        if (i2) { *io = 1; return; }
        if (stop || c->pc != b->pc + 2 * j) return;
    }
    if (j == b->n && b->tkind && c->pc == b->pc + 2 * b->n) ref_term(c, b, it, st, cy, io);
}

typedef struct { Core c; uint8_t w0[WIN], w1[WIN]; int exc; uint32_t xo, xc, xt;
                 uint32_t log[512][3]; int logn; uint64_t ins, st, cyc; int io; } Out;

static uint32_t rand_branch(void)
{
    uint32_t n = (rng() & 0xF) << 8;
    switch (rng() % 9) {
    case 0: return 0xA000u | (rng() & 0xFFF);
    case 1: return 0xB000u | (rng() & 0xFFF);
    case 2: return 0x000Bu | n;
    case 3: return 0x400Bu | n;
    case 4: return 0x402Bu | n;
    case 5: return 0x0003u | n;
    case 6: return 0x0023u | n;
    case 7: return 0x8D00u | (rng() & 0xFF);
    default: return 0x8F00u | (rng() & 0xFF);
    }
}

int main(int argc, char **argv)
{
    int blocks = argc > 1 ? atoi(argv[1]) : 20000;
    g_clean = argc > 2 && !strcmp(argv[2], "clean");
    if (!jit_init()) { printf("JIT memory unavailable\nRESULT: FAIL\n"); return 1; }
    g_jit_chain = 0;          /* blocks are tested alone; chaining = machine A/B */
    /* each side has its own memory: equal semantics keep the two in step
     * everywhere, not just in the compared windows */
    static Core bases[2];
    for (int q = 0; q < 2; q++) {
        Core *bq = &bases[q]; memset(bq, 0, sizeof *bq);
        bq->ram0 = calloc(1, 0x04000000); bq->ram1 = calloc(1, 0x04000000);
        bq->fb0 = calloc(1, FBSZ); bq->fb1 = calloc(1, FBSZ);
        bq->misc = calloc(1, 0x10000); bq->sq = calloc(1, 0x40);
        bq->flash = calloc(1, 0x10000); bq->flash_len = 0; bq->low_is_ram = 1;
        bq->io_rd = s_rd; bq->io_wr = s_wr; bq->io_rds = s_rds; bq->io_wrs = s_wrs;
        bq->idle_pc = 0xFFFFFFF0u;
    }
    Core base = bases[0];
    dtab_build();
    uint32_t pool[65536]; int np = 0;
    for (uint32_t op = 0; op < 65536; op++) if (g_cls[op]) pool[np++] = op;
    static uint8_t s0[WIN], s1[WIN];
    static Out A, B;
    static Dtlb dsave[2][DTLB_N];
    static volatile uint64_t compiles, nat_ins, nat_ops, terms;
    static volatile uint64_t bad, natives, excs, ios, total, early;   /* survive longjmp */
    for (int t = 0; t < blocks; t++) {
        /* random block code at CODE + off */
        uint32_t off = (rng() & 0x3FE) * 2, len = rng() % BMAX;
        for (uint32_t i = 0; i < WIN; i++) { s0[i] = (uint8_t)rng(); s1[i] = (uint8_t)rng(); }
        for (uint32_t i = 0; i < len + 2; i++) {
            uint16_t op = (uint16_t)pool[rng() % np];
            if (i == len && (rng() & 1)) op = (uint16_t)rand_branch();
            memcpy(s1 + (CODE - 0x0C000000u) + off + 2 * i, &op, 2);
        }
        Core st = base;
        for (int i = 0; i < 16; i++) st.r[i] = pick();
        st.r[0] = (rng() & 1) ? (rng() & 0x7FC) : st.r[0];
        st.gbr = 0x0C000000u + (rng() & 0x7FC);
        if (g_clean) st.r[0] = rng() & 0x7FC;
        st.mach = rng(); st.macl = rng(); st.pr = rng();
        st.sr = ((g_clean || (rng() & 15)) ? MD : 0) | ((rng() & 1) ? TB : 0) | 0xF0u;
        uint32_t pc = CODE + off;
        /* data-page cache: the same random subset of window pages per side */
        /* most window pages, both views, are cached; some are left cold */
        uint32_t fa[16]; int nfa = 0;
        for (int q = 0; q < 4; q++)
            for (int v = 0; v < 2; v++) {
                if (!g_clean && rng() % 4 == 0) continue;
                uint32_t a = (v ? 0x0C000000u : 0) + 0x1000u * (uint32_t)q;
                if (!g_clean && (rng() & 1)) a |= (st.sr & MD) ? 0x80000000u : 0;
                fa[nfa++] = a;
                if (v) fa[nfa++] = (a & ~0x80000000u) | ((st.sr & MD) && (rng() & 1) ? 0x80000000u : 0);
            }
        Blk blks[2];
        g_dtlb_gen++;                       /* one generation for both sides */
        for (int q = 0; q < 2; q++) {
            memcpy(bases[q].ram0, s0, WIN); memcpy(bases[q].ram1, s1, WIN);
            Core sq = st;
            sq.ram0 = bases[q].ram0; sq.ram1 = bases[q].ram1; sq.fb0 = bases[q].fb0;
            sq.fb1 = bases[q].fb1; sq.misc = bases[q].misc; sq.sq = bases[q].sq; sq.flash = bases[q].flash;
            memset(g_dtlb_t, 0, sizeof g_dtlb_t);
            for (int k = 0; k < nfa; k++) if (setjmp(g_jb) == 0) dtlb_get(&sq, fa[k]);
            memcpy(dsave[q], g_dtlb_t, sizeof dsave[q]);
            memset(&blks[q], 0, sizeof blks[q]);
            const uint16_t *page = (const uint16_t *)(bases[q].ram1 + (CODE - 0x0C000000u));
            uint32_t key = (pc & ~0xFFFu) | ((st.sr & MD) ? 1u : 0u) | 2u;
            blk_build(&sq, &blks[q], pc, key, page);
        }
        Blk blk = blks[1];
        if (blk_len(&blk) == 0) continue;
        if (blk.tkind) terms++;
        total++;
        for (int side = 0; side < 2; side++) {
            Out *o = side ? &B : &A;
            Core *bq = &bases[side];
            memcpy(bq->ram0, s0, WIN); memcpy(bq->ram1, s1, WIN);
            memcpy(g_dtlb_t, dsave[side], sizeof dsave[side]);
            o->c = st; o->c.pc = pc; g_cur = &o->c; lgn = 0;
            o->c.ram0 = bq->ram0; o->c.ram1 = bq->ram1; o->c.fb0 = bq->fb0; o->c.fb1 = bq->fb1;
            o->c.misc = bq->misc; o->c.sq = bq->sq; o->c.flash = bq->flash;
            o->exc = 0; o->ins = 0; o->st = 0; o->cyc = 0; o->io = 0;
            Blk *bs = &blks[side];
            if (setjmp(g_jb)) {
                o->exc = 1; o->xo = x_off; o->xc = x_code; o->xt = x_hastea ? x_tea : 0;
            } else if (side == 0) {
                Blk *bb = bs;
                bb->fn = 0;
                compiles++;
                if (jit_compile(&o->c, bb)) {
                    natives++;
                    uint64_t ops0 = g_jit_ops;
                    JCtx jx = { 0, 0, 0, 1u << 30, 0, 0 };
                    jit_enter(&o->c, (void *)bb->fn, &jx);
                    uint32_t e = (uint32_t)jx.it;
                    nat_ins += e; nat_ops += g_jit_ops - ops0;
                    o->ins += e; o->st += jx.steps; o->cyc += jx.cycles;
                    o->io = (int)jx.flags;
                    if (!o->io) {
                        if (e < bb->n && o->c.pc == pc + 2 * e) {
                            early++;
                            ref_from(&o->c, bb, e, &o->ins, &o->st, &o->cyc, &o->io);
                        } else if (e == bb->n && bb->tkind && o->c.pc == pc + 2 * e) {
                            early++;
                            ref_term(&o->c, bb, &o->ins, &o->st, &o->cyc, &o->io);
                        }
                    }
                } else ref_from(&o->c, bs, 0, &o->ins, &o->st, &o->cyc, &o->io);
            } else {
                ref_from(&o->c, bs, 0, &o->ins, &o->st, &o->cyc, &o->io);
            }
            memcpy(o->w0, bq->ram0, WIN); memcpy(o->w1, bq->ram1, WIN);
            o->logn = lgn < 512 ? lgn : 512; memcpy(o->log, lg, sizeof lg);
        }
        int ok = A.exc == B.exc && !memcmp(A.c.r, B.c.r, sizeof A.c.r)
            && A.c.sr == B.c.sr && A.c.gbr == B.c.gbr && A.c.mach == B.c.mach
            && A.c.macl == B.c.macl && A.c.pr == B.c.pr && A.c.fb_writes == B.c.fb_writes
            && !memcmp(A.w0, B.w0, WIN) && !memcmp(A.w1, B.w1, WIN)
            && A.logn == B.logn && !memcmp(A.log, B.log, sizeof(uint32_t) * 3 * A.logn)
            && A.c.io_break == B.c.io_break;
        if (ok && A.exc) ok = A.xo == B.xo && A.xc == B.xc && A.xt == B.xt;
        if (ok && !A.exc) ok = A.c.pc == B.c.pc && A.c.cur_pc == B.c.cur_pc
                            && A.ins == B.ins && A.st == B.st && A.cyc == B.cyc && A.io == B.io;
        excs += A.exc; ios += A.io;
        if (!ok) {
            bad++;
            if (bad <= 5) {
                printf("MISMATCH block len %u term %u pc %08x exc %d/%d pc %08x/%08x cur %08x/%08x ins %llu/%llu io %d/%d\n",
                       blk.n, blk.tkind, pc, A.exc, B.exc, A.c.pc, B.c.pc, A.c.cur_pc, B.c.cur_pc,
                       (unsigned long long)A.ins, (unsigned long long)B.ins, A.io, B.io);
                for (uint32_t i = 0; i < blk_len(&blk); i++) printf(" %04x(k%u)", blk.ops[i], blk.k[i]);
                printf("  st %llu/%llu cyc %llu/%llu", (unsigned long long)A.st, (unsigned long long)B.st,
                       (unsigned long long)A.cyc, (unsigned long long)B.cyc);
                printf("\n");
                for (int i = 0; i < 16; i++) if (A.c.r[i] != B.c.r[i]) printf("  r%d %08x vs %08x\n", i, A.c.r[i], B.c.r[i]);
                if (A.c.sr != B.c.sr) printf("  sr %08x vs %08x\n", A.c.sr, B.c.sr);
                if (memcmp(A.w1, B.w1, WIN)) printf("  ram1 window differs\n");
                if (memcmp(A.w0, B.w0, WIN)) printf("  ram0 window differs\n");
                printf("  start regs:"); for (int i = 0; i < 16; i++) printf(" r%d=%08x", i, st.r[i]); printf(" sr=%08x gbr=%08x\n", st.sr, st.gbr);
            }
        }
        if (g_jit_pos > JIT_WORDS - 8192u) g_jit_pos = g_jit_base;
    }
    /* code-change detection: compile, change one opcode in memory, run:
     * the code must refuse (flags 2, nothing executed, nothing changed)
     * and unhook itself */
    {
        uint64_t smc_ok = 0, smc_bad = 0;
        for (int t = 0; t < 3000; t++) {
            Core *bq = &bases[0];
            uint32_t off = (rng() & 0x3FE) * 2, len = 2 + rng() % (BMAX - 2);
            for (uint32_t i = 0; i < len + 2; i++) {
                uint16_t op = (uint16_t)pool[rng() % np];
                if (i == len && (rng() & 1)) op = (uint16_t)rand_branch();
                memcpy(bq->ram1 + (CODE - 0x0C000000u) + off + 2 * i, &op, 2);
            }
            Core st = base; st.ram0 = bq->ram0; st.ram1 = bq->ram1;
            st.sr = MD; for (int i = 0; i < 16; i++) st.r[i] = rng();
            uint32_t pc = CODE + off;
            Blk bb; memset(&bb, 0, sizeof bb);
            const uint16_t *page = (const uint16_t *)(bq->ram1 + (CODE - 0x0C000000u));
            blk_build(&st, &bb, pc, (pc & ~0xFFFu) | 3u, page);
            uint32_t L = blk_len(&bb);
            if (L < 1 || !jit_compile(&st, &bb)) continue;
            uint32_t q = rng() % L;
            uint16_t *w = (uint16_t *)(bq->ram1 + (CODE - 0x0C000000u) + off + 2 * q);
            *w ^= (uint16_t)(1u << (rng() % 16));
            Core before = st;
            JCtx jx = { 0, 0, 0, 1u << 30, 0, 0 };
            jit_enter(&st, (void *)bb.fn, &jx);
            if (jx.flags == 2 && jx.it == 0 && jx.steps == 0 && jx.cycles == 0 && bb.fn == 0 &&
                !memcmp(st.r, before.r, sizeof st.r) && st.sr == before.sr) smc_ok++;
            else smc_bad++;
            if (g_jit_pos > JIT_WORDS - 8192u) g_jit_pos = g_jit_base;
        }
        printf("code-change detection: %llu refused correctly, %llu wrong\n",
               (unsigned long long)smc_ok, (unsigned long long)smc_bad);
        bad += smc_bad;
    }
    printf("native instructions %llu, of which via reference handler %llu\n",
           (unsigned long long)nat_ins, (unsigned long long)nat_ops);
    printf("jit compile calls %llu, failures %llu, flushes %llu\n", (unsigned long long)compiles,
           (unsigned long long)g_jit_fail, (unsigned long long)g_jit_flushes);
    printf("blocks ending in a delayed branch: %llu\n", (unsigned long long)terms);
    printf("blocks %llu (native %llu, ended early %llu, faulted %llu, io %llu), mismatches %llu\n",
           (unsigned long long)total, (unsigned long long)natives, (unsigned long long)early,
           (unsigned long long)excs, (unsigned long long)ios, (unsigned long long)bad);
    printf("RESULT: %s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
#endif
