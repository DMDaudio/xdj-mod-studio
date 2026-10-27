/* dtab self-test: every opcode the decode-once table sends to fast_step()
 * is run through BOTH fast_step() and the reference step() from identical
 * random states (fresh fake memory, logging fake devices) and every
 * observable is compared: registers, SR/T, MACH/MACL/PR/GBR, the RAM
 * windows, the device-access log, the exception taken (offset/code/TEA),
 * the cycle count and the branch decision/target.
 * Build + run: sh tools/dtab/run_selftest.sh   (exit 0 = PASS) */
#include "../../emu/fastcore.c"
#include <stdio.h>
#include <stdlib.h>

#define WIN 0x4000u
static uint32_t lg[256][3];
static int lgn;
static void logio(uint32_t kind, uint32_t a, uint32_t v)
{
    if (lgn < 256) { lg[lgn][0] = kind; lg[lgn][1] = a; lg[lgn][2] = v; }
    lgn++;
}
static uint32_t s_rd(uint32_t a) { logio(1, a, 0); return (a * 2654435761u) >> 7; }
static void s_wr(uint32_t a, uint32_t v) { logio(2, a, v); }
static uint32_t s_rds(uint32_t a, uint32_t n) { logio(3, a, n); return (a * 2654435761u) >> 3; }
static void s_wrs(uint32_t a, uint32_t v, uint32_t n) { logio(4, a, v ^ (n << 28)); }

static uint32_t x = 0x9E3779B9u;
static uint32_t rng(void) { x ^= x << 13; x ^= x >> 17; x ^= x << 5; return x; }

static uint32_t pick(int r0)
{
    uint32_t c = rng() % 10;
    if (c < (r0 ? 5u : 2u)) return rng() & 0x7FF;                  /* small */
    if (c < (r0 ? 7u : 6u)) {                                       /* window ptr */
        uint32_t p = 0x0C000000u + (rng() & 0x1FFC);
        if (!(rng() & 7)) p += rng() & 3;                           /* misaligned */
        if (!(rng() & 3)) p |= 0x80000000u;                         /* P1 view */
        return p;
    }
    return rng();                                                   /* anything */
}

typedef struct {
    Core c; uint8_t w0[WIN], w1[WIN]; int exc; uint32_t xo, xc, xt;
    uint32_t log[256][3]; int logn; uint32_t cyc; int br; uint32_t tgt; Res rs;
} Out;

int main(int argc, char **argv)
{
    int rounds = argc > 1 ? atoi(argv[1]) : 64;
    Core base; memset(&base, 0, sizeof base);
    base.ram0 = calloc(1, 0x04000000); base.ram1 = calloc(1, 0x04000000);
    base.fb0 = calloc(1, FBSZ); base.fb1 = calloc(1, FBSZ);
    base.misc = calloc(1, 0x10000); base.sq = calloc(1, 0x40);
    base.flash = calloc(1, 0x10000); base.flash_len = 0; base.low_is_ram = 1;
    base.io_rd = s_rd; base.io_wr = s_wr; base.io_rds = s_rds; base.io_wrs = s_wrs;
    dtab_build();
    static uint8_t s0[WIN], s1[WIN];
    static Out A, B;
    uint64_t cases = 0, bad = 0, excs = 0, ioc = 0;
    uint32_t perk[256] = {0}, badk[256] = {0};
    for (uint32_t op = 0; op < 65536; op++) {
        uint32_t k = g_cls[op];
        if (!k) continue;
        for (int r = 0; r < rounds; r++) {
            Core st = base;
            for (int i = 0; i < 16; i++) st.r[i] = pick(i == 0);
            st.gbr = 0x0C000000u + (rng() & 0x7FC);
            st.mach = rng(); st.macl = rng(); st.pr = rng();
            st.sr = ((rng() & 3) ? MD : 0) | ((rng() & 1) ? TB : 0) | 0xF0u;
            st.pc = 0x0C001000u + (rng() & 0x7FE);
            for (uint32_t i = 0; i < WIN; i++) { s0[i] = (uint8_t)rng(); s1[i] = (uint8_t)rng(); }
            for (int side = 0; side < 2; side++) {
                Out *o = side ? &B : &A;
                memcpy(base.ram0, s0, WIN); memcpy(base.ram1, s1, WIN);
                o->c = st; lgn = 0; o->exc = 0; o->br = 0; o->tgt = 0; o->cyc = 0;
                memset(&o->rs, 0, sizeof o->rs);
                g_dtlb_gen++;
                if (setjmp(g_jb)) {
                    o->exc = 1; o->xo = x_off; o->xc = x_code; o->xt = x_hastea ? x_tea : 0;
                } else if (!side) {
                    o->cyc = fast_step(&o->c, op, k, &o->br, &o->tgt);
                } else {
                    step(&o->c, op, o->c.pc + 2, &o->rs);
                }
                memcpy(o->w0, base.ram0, WIN); memcpy(o->w1, base.ram1, WIN);
                o->logn = lgn < 256 ? lgn : 256;
                memcpy(o->log, lg, sizeof lg);
            }
            int ok = A.exc == B.exc && !memcmp(A.c.r, B.c.r, sizeof A.c.r)
                && A.c.sr == B.c.sr && A.c.mach == B.c.mach && A.c.macl == B.c.macl
                && A.c.pr == B.c.pr && A.c.gbr == B.c.gbr
                && A.c.fb_writes == B.c.fb_writes
                && !memcmp(A.w0, B.w0, WIN) && !memcmp(A.w1, B.w1, WIN)
                && A.logn == B.logn && !memcmp(A.log, B.log, sizeof(uint32_t) * 3 * A.logn);
            if (ok && A.exc)
                ok = A.xo == B.xo && A.xc == B.xc && A.xt == B.xt;
            if (ok && !A.exc) {
                int kind = B.rs.kind;
                uint32_t want = (kind == K_TIMED || kind == K_BR) ? B.rs.cyc : 1;
                ok = (kind == K_NONE || kind == K_TIMED || kind == K_BR)
                    && A.cyc == want && A.br == (kind == K_BR) && (!A.br || A.tgt == B.rs.tgt);
            }
            cases++; perk[k]++; excs += A.exc; ioc += A.logn > 0;
            if (!ok) {
                bad++; badk[k]++;
                if (badk[k] <= 3)
                    printf("MISMATCH op %04x class %u exc %d/%d cyc %u kind %d br %d tgt %x/%x\n",
                           op, k, A.exc, B.exc, A.cyc, B.rs.kind, A.br, A.tgt, B.rs.tgt);
            }
        }
    }
    int classes = 0;
    for (int k = 1; k < 256; k++) classes += perk[k] > 0;
    printf("cases %llu, classes %d, with exception %llu, with device access %llu, mismatches %llu\n",
           (unsigned long long)cases, classes, (unsigned long long)excs,
           (unsigned long long)ioc, (unsigned long long)bad);
    printf("RESULT: %s\n", bad ? "FAIL" : "PASS");
    return bad ? 1 : 0;
}
