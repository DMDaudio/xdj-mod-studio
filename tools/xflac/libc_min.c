/* the few libc symbols dr_flac needs on the deck (no heap: callbacks) */
#include <stddef.h>
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
/* libopus' pseudostack takes ONE 64 KB scratch block via malloc (dr_flac
 * uses the per-slot arenas); serve it from a static area, never freed */
static unsigned char scratch[65536 + 64] __attribute__((aligned(8)));
static size_t scratch_used;
void *malloc(size_t n)
{
    n = (n + 7) & ~(size_t)7;
    if (scratch_used + n > sizeof scratch) return 0;
    void *p = scratch + scratch_used;
    scratch_used += n;
    return p;
}
void *realloc(void *p, size_t n) { (void)p; (void)n; return 0; }
void free(void *p) { (void)p; }
