/* xflac.c -- dr_flac (public domain / MIT-0, work/drflac, pinned commit
 * dfe8377, sha256 111144e7...f945c) wrapped for the XDJ-700's SH-4.
 *
 * Same source builds natively (Mac oracle) and for the deck (sh-elf-gcc,
 * freestanding, integer only). The deck build must be bit-identical to
 * the Mac build AND to ffmpeg's FLAC decode (tools/xflac_test.py).
 * No heap: dr_flac's allocations come from a caller-supplied arena.
 *
 * API (<= 4 args, SH r4..r7 -> r0):
 *   xf_open_mem(arena, arena_len, data, len)  handle or 0
 *   xf_info(h, what)            0 rate, 1 channels, 2 bits, 3 frames lo, 4 frames hi
 *   xf_read16(h, pcm, frames)   frames decoded (interleaved int16)
 *   xf_seek(h, frame)           1 ok
 * (The deck build will add xf_open(arena, len, io) with firmware
 *  fread/fseek callbacks for the read hook; memory open = test path.)
 */
#define DR_FLAC_NO_STDIO
#define DR_FLAC_NO_OGG
#define DR_FLAC_NO_SIMD
#define DR_FLAC_NO_WCHAR
#define DR_FLAC_IMPLEMENTATION
#include <stddef.h>

#ifdef XF_FREESTANDING
void *memcpy(void *d, const void *s, size_t n)
{ unsigned char *dp = d; const unsigned char *sp = s; while (n--) *dp++ = *sp++; return d; }
void *memmove(void *d, const void *s, size_t n)
{
    unsigned char *dp = d; const unsigned char *sp = s;
    if (dp < sp) while (n--) *dp++ = *sp++;
    else { dp += n; sp += n; while (n--) *--dp = *--sp; }
    return d;
}
void *memset(void *d, int c, size_t n)
{ unsigned char *dp = d; while (n--) *dp++ = (unsigned char)c; return d; }
int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = a, *y = b;
    for (; n; n--, x++, y++) if (*x != *y) return *x - *y;
    return 0;
}
void abort(void) { for (;;) ; }
void *malloc(size_t n) { (void)n; return 0; }      /* never used: callbacks */
void *realloc(void *p, size_t n) { (void)p; (void)n; return 0; }
void free(void *p) { (void)p; }
#endif

#include "dr_flac.h"

/* bump arena inside the caller's buffer; each block remembers its size so
 * realloc can copy. The arena header lives at the buffer start. */
typedef struct { unsigned char *base; size_t len, used; drflac *f; } arena_t;

static void *a_malloc(size_t n, void *u)
{
    arena_t *a = u;
    n = (n + 15) & ~(size_t)15;
    if (a->used + n + 16 > a->len) return 0;
    unsigned char *p = a->base + a->used;
    *(size_t *)p = n;
    a->used += n + 16;
    return p + 16;
}
static void a_free(void *p, void *u) { (void)p; (void)u; }
static void *a_realloc(void *p, size_t n, void *u)
{
    if (!p) return a_malloc(n, u);
    size_t old = *(size_t *)((unsigned char *)p - 16);
    if (n <= old) return p;
    void *q = a_malloc(n, u);
    if (q) memcpy(q, p, old);
    return q;
}

void *xf_open_mem(void *arena, int arena_len, const void *data, int len)
{
    arena_t *a = arena;
    a->base = (unsigned char *)arena + sizeof(arena_t);
    a->len = (size_t)arena_len - sizeof(arena_t);
    a->used = 0;
    drflac_allocation_callbacks cb = { a, a_malloc, a_realloc, a_free };
    a->f = drflac_open_memory(data, (size_t)len, &cb);
    return a->f ? a : 0;
}

int xf_info(void *h, int what)
{
    drflac *f = ((arena_t *)h)->f;
    switch (what) {
    case 0: return (int)f->sampleRate;
    case 1: return f->channels;
    case 2: return f->bitsPerSample;
    case 3: return (int)(f->totalPCMFrameCount & 0xFFFFFFFFu);
    case 4: return (int)(f->totalPCMFrameCount >> 32);
    }
    return -1;
}

int xf_read16(void *h, short *pcm, int frames)
{
    return (int)drflac_read_pcm_frames_s16(((arena_t *)h)->f, (drflac_uint64)frames, pcm);
}

int xf_seek(void *h, int frame)
{
    return drflac_seek_to_pcm_frame(((arena_t *)h)->f, (drflac_uint64)(unsigned)frame);
}

#ifdef XF_FREESTANDING
__attribute__((section(".entry"), used))
const void *const xf_entry[4] = {
    (const void *)xf_open_mem, (const void *)xf_info,
    (const void *)xf_read16, (const void *)xf_seek
};
#else
/* native CLI: xflac_native FILE [seek_frame] -> s16 interleaved on stdout */
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv)
{
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) return 1;
    fseek(fp, 0, SEEK_END); long n = ftell(fp); fseek(fp, 0, SEEK_SET);
    void *d = malloc(n);
    if (fread(d, 1, n, fp) != (size_t)n) return 2;
    static unsigned char arena[1 << 20];
    void *h = xf_open_mem(arena, sizeof arena, d, (int)n);
    if (!h) return 3;
    if (argc > 2) xf_seek(h, atoi(argv[2]));
    static short pcm[4096 * 8];
    int ch = xf_info(h, 1), k;
    while ((k = xf_read16(h, pcm, 4096)) > 0) fwrite(pcm, 2, (size_t)k * ch, stdout);
    fprintf(stderr, "rate %d ch %d bits %d frames %d arena %zu\n", xf_info(h, 0), ch,
            xf_info(h, 2), xf_info(h, 3), ((arena_t *)h)->used);
    return 0;
}
#endif
