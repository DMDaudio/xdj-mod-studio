/* fastcore_bench.c -- standalone speed test for the SH interpreter in
 * emu/fastcore.c, no Python. Runs two synthetic loops taken off the real
 * workload (tools/dsp_sh_hist.py PC histogram during WAV play):
 *
 *   fill : mov.l r7,@-r2 x4 ; add #-1,r5 ; cmp/pl r5 ; bt   (waveform fill)
 *   alu  : mov.l @r2,r13 ; add/sub/shll/tst ; mov.l r12,@r2 ; bra
 *
 * Build:  cc -O2 -I.. -o /tmp/fastcore_bench tools/fastcore_bench.c
 * Run:    /tmp/fastcore_bench [seconds]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>

#include "fastcore.c"    /* -Iemu / -I.. : the interpreter itself */

static double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static uint32_t dummy_rd(uint32_t a) { (void)a; return 0; }
static void dummy_wr(uint32_t a, uint32_t v) { (void)a; (void)v; }
static uint32_t dummy_rds(uint32_t a, uint32_t n) { (void)a; (void)n; return 0; }
static void dummy_wrs(uint32_t a, uint32_t v, uint32_t n) { (void)a; (void)v; (void)n; }

#define RAM_BYTES (64u * 1024 * 1024)
#define BASE 0x09000000u

static void setup(Core *c, const uint16_t *prog, int n)
{
    memset(c, 0, sizeof(*c));
    c->ram0 = calloc(RAM_BYTES, 1);
    c->ram1 = calloc(RAM_BYTES, 1);
    c->flash = calloc(4, 1);
    c->flash_len = 0;
    c->sq = calloc(0x40, 1);
    c->misc = calloc(0x10000, 1);
    c->fb0 = calloc(800 * 480 * 2, 1);
    c->fb1 = calloc(800 * 480 * 2, 1);
    c->io_rd = dummy_rd;
    c->io_wr = dummy_wr;
    c->io_rds = dummy_rds;
    c->io_wrs = dummy_wrs;
    c->low_is_ram = 1;
    c->sr = 0x40000000u;             /* MD set: P1/P2 reachable */
    for (int i = 0; i < n; i++)
        ((uint16_t *)(c->ram0 + (BASE - 0x08000000u)))[i] = prog[i];
}

static double run_case(const char *name, const uint16_t *prog, int n,
                       uint32_t count, double secs)
{
    Core *c = calloc(1, sizeof(Core));
    setup(c, prog, n);
    c->pc = BASE;
    c->r[5] = count;
    c->r[2] = 0x0B000000u;           /* store pointer */
    c->r[9] = 0x0A000000u;           /* load pointer (alu) */
    c->r[15] = 0x0BFFFFF0u;
    uint64_t done = 0;
    double t0 = now();
    while (now() - t0 < secs) {
        core_run(c, 1 << 20, &done);
        if (done < (1u << 20)) {     /* loop fell through: re-arm */
            c->pc = BASE;
            c->r[5] = count;
            c->r[2] = 0x0B000000u;
        }
    }
    double dt = now() - t0;
    printf("%-5s %8.1f Mcyc/s  (%.2f ns/instr, steps=%llu)\n",
           name, c->cycles / dt / 1e6,
           dt / (double)(c->steps ? c->steps : 1) * 1e9,
           (unsigned long long)c->steps);
    return c->cycles / dt / 1e6;
}

int main(int argc, char **argv)
{
    double secs = argc > 1 ? atof(argv[1]) : 2.0;

    /* fill loop, straight from 0x08E1D994 (top histogram hit); ends in a
     * bra-self so a fallen-through round can never run off into zeros */
    static const uint16_t fill[] = {
        0x2276, 0x2276, 0x2276, 0x2276,   /* mov.l r7,@-r2 x4 */
        0x75FF,                            /* add #-1,r5 */
        0x4515,                            /* cmp/pl r5 */
        0x89F8,                            /* bt -8 (loop head) */
        0xAFFD,                            /* bra self (safety net) */
        0x0009,                            /* nop (delay slot) */
    };
    /* generic mix: load, arith, shift, test, store, branch */
    static const uint16_t alu[] = {
        0x6D23, /* mov.l @r2,r13 */
        0x3CDC, /* add r13,r12 */
        0x3CB8, /* sub r11,r12 */
        0x400C, /* shll r12 */
        0x2CC8, /* tst r12,r12 */
        0x22C2, /* mov.l r12,@r2 */
        0x4515, /* cmp/pl r5 */
        0x75FF, /* add #-1,r5 */
        0x8906, /* bt +3 -> re-arm bra-self below */
        0xA001, /* bra +1 (self) */
        0x0009, /* nop */
        0xAFFE, /* bra self (round fell through: land here, wait re-arm) */
        0x0009, /* nop */
    };
    run_case("fill", fill, 7, 1 << 20, secs);
    run_case("alu", alu, 9, 1 << 20, secs);
    return 0;
}
