/* xopus.c -- libopus fixed-point DECODER wrapped for the XDJ-700's SH-4.
 *
 * Same source builds natively (Mac test oracle, tools/xopus/build.sh) and
 * for the deck (sh-elf-gcc, freestanding). The deck build must be
 * bit-identical to the Mac build on the same packets (tools/xopus_test.py).
 *
 * No heap, no stack VLAs: libopus is built with NONTHREADSAFE_PSEUDOSTACK
 * (one static scratch area), decoder state lives in a caller-supplied
 * buffer (xo_state_size()). Integer only (FIXED_POINT): never touches the
 * SH FPU, so it is safe to call from any firmware task.
 *
 * API (all ints, SH calling convention r4..r7 -> r0):
 *   xo_state_size(ch)                       bytes for one decoder
 *   xo_init(st, ch)                         48 kHz decoder, 0 = ok
 *   xo_decode(st, pkt, len, pcm)            samples per channel, <0 error
 *                                           (pcm = interleaved int16, room
 *                                           for 5760 frames = 120 ms)
 */
#include <stddef.h>
#include "opus.h"

#ifdef XO_FREESTANDING
/* the few libc symbols libopus needs */
void *memcpy(void *d, const void *s, size_t n)
{
    unsigned char *dp = d; const unsigned char *sp = s;
    while (n--) *dp++ = *sp++;
    return d;
}
void *memmove(void *d, const void *s, size_t n)
{
    unsigned char *dp = d; const unsigned char *sp = s;
    if (dp < sp) while (n--) *dp++ = *sp++;
    else { dp += n; sp += n; while (n--) *--dp = *--sp; }
    return d;
}
void *memset(void *d, int c, size_t n)
{
    unsigned char *dp = d;
    while (n--) *dp++ = (unsigned char)c;
    return d;
}
int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = a, *y = b;
    for (; n; n--, x++, y++) if (*x != *y) return *x - *y;
    return 0;
}
void abort(void) { for (;;) ; }
/* libopus' NONTHREADSAFE_PSEUDOSTACK grabs its scratch area once via
 * malloc; serve that from a static arena (bump allocator, never freed) */
static unsigned char xo_arena[GLOBAL_STACK_SIZE + 256] __attribute__((aligned(8)));
static size_t xo_arena_used;
void *malloc(size_t n)
{
    n = (n + 7) & ~(size_t)7;
    if (xo_arena_used + n > sizeof xo_arena) return 0;
    void *p = xo_arena + xo_arena_used;
    xo_arena_used += n;
    return p;
}
void free(void *p) { (void)p; }
#endif

int xo_state_size(int ch) { return opus_decoder_get_size(ch); }

int xo_init(void *st, int ch)
{
    return opus_decoder_init((OpusDecoder *)st, 48000, ch);
}

/* four args: all in r4..r7 under the SH ABI (a 5th would go on the stack) */
int xo_decode(void *st, const unsigned char *pkt, int len, short *pcm)
{
    return opus_decode((OpusDecoder *)st, pkt, len, pcm, 5760, 0);
}

#ifdef XO_FREESTANDING
/* fixed entry table at the image start: the sandbox (and later the deck
 * cave) calls through it without needing a symbol table */
__attribute__((section(".entry"), used))
const void *const xo_entry[4] = {
    (const void *)xo_state_size, (const void *)xo_init,
    (const void *)xo_decode, 0
};
#else
/* native test CLI: packets file = repeated {u32 LE len, bytes}; writes
 * interleaved s16 LE PCM to stdout */
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv)
{
    int ch = argc > 2 ? atoi(argv[2]) : 2;
    FILE *f = fopen(argv[1], "rb");
    static unsigned char pkt[4000];
    static short pcm[5760 * 2];
    void *st = malloc(xo_state_size(ch));
    if (!f || xo_init(st, ch)) return 1;
    unsigned char hdr[4];
    while (fread(hdr, 1, 4, f) == 4) {
        int len = hdr[0] | hdr[1] << 8 | hdr[2] << 16 | hdr[3] << 24;
        if (len > (int)sizeof pkt || fread(pkt, 1, len, f) != (size_t)len) return 2;
        int n = xo_decode(st, pkt, len, pcm);
        if (n < 0) return 3;
        fwrite(pcm, 2, (size_t)n * ch, stdout);
    }
    return 0;
}
#endif
