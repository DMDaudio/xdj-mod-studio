/* decode_prov.c -- LZSS decode with per-output-byte provenance.
 *
 * Decodes a raw LZSS stream under a selectable match semantics and records,
 * for every output byte:
 *      kind[o]  0 = emitted as a LITERAL   (survives ANY match semantics)
 *               1 = emitted as a MATCH     (value depends on the semantics)
 *      root[o]  stream offset of the literal this byte ultimately descends
 *               from, or 0xFFFFFFFF if the chain bottoms out in the
 *               pre-filled ring (i.e. pure invention).
 *      hops[o]  number of match hops back to that literal (0 = literal).
 *
 * Why this matters here: literals decode correctly no matter how wrong the
 * codec is; match bytes do not. A disassembly is only trustworthy where the
 * instruction halfwords are literal-derived.
 *
 *   usage: decode_prov <stream.lz> <out.bin> <prov.bin> [v1|oku]
 *   prov.bin layout: per output byte { u8 kind; u8 hops(sat 255); u32 root; }
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define N 4096
#define F 18
#define THR 2
#define R0 (N - F)

int main(int argc, char **argv)
{
    if (argc < 4) { fprintf(stderr, "usage: %s stream out prov [v1|oku]\n", argv[0]); return 2; }
    int oku = (argc > 4 && strcmp(argv[4], "v1") == 0) ? 0 : 1;

    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 1; }
    fseek(f, 0, SEEK_END); size_t n = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *s = malloc(n);
    if (fread(s, 1, n, f) != n) { perror("read"); return 1; }
    fclose(f);

    /* generous upper bound: worst case 18 bytes out per 2 bytes in */
    size_t cap = n * 10 + (1 << 20);
    uint8_t  *out  = malloc(cap);
    uint8_t  *kind = malloc(cap);
    uint8_t  *hops = malloc(cap);
    uint32_t *root = malloc(cap * 4);
    if (!out || !kind || !hops || !root) { fprintf(stderr, "oom\n"); return 1; }

    uint8_t rb[N];
    int32_t rbsrc[N];                    /* ring slot -> output index that filled it */
    memset(rb, 0x20, R0); memset(rb + R0, 0, N - R0);
    for (int i = 0; i < N; i++) rbsrc[i] = -1;

    int r = R0; size_t p = 0, o = 0;
    while (p < n) {
        uint8_t fl = s[p++];
        for (int b = 0; b < 8; b++) {
            if (p >= n) goto done;
            if (fl >> b & 1) {
                uint8_t c = s[p];
                out[o] = c; kind[o] = 0; hops[o] = 0; root[o] = (uint32_t)p;
                rb[r] = c; rbsrc[r] = (int32_t)o;
                p++; o++; r = (r + 1) & (N - 1);
            } else {
                if (p + 1 >= n) goto done;
                uint8_t b1 = s[p], b2 = s[p + 1]; p += 2;
                int i, j;
                if (oku) { i = b1 | ((b2 & 0xF0) << 4); j = (b2 & 0x0F) + THR + 1; }
                else     { i = b2 | ((b1 & 0xF0) << 4); j = (b1 & 0x0F) + THR + 1; }
                for (int k = 0; k < j; k++) {
                    int q = (i + k) & (N - 1);
                    uint8_t c = rb[q];
                    int32_t sp = rbsrc[q];
                    out[o] = c; kind[o] = 1;
                    if (sp >= 0) {
                        root[o] = root[sp];
                        hops[o] = hops[sp] < 255 ? hops[sp] + 1 : 255;
                    } else {
                        root[o] = 0xFFFFFFFFu;   /* descends from the ring pre-fill */
                        hops[o] = 255;
                    }
                    rb[r] = c; rbsrc[r] = (int32_t)o;
                    o++; r = (r + 1) & (N - 1);
                    if (o >= cap) { fprintf(stderr, "overflow\n"); return 1; }
                }
            }
        }
    }
done:
    { FILE *g = fopen(argv[2], "wb"); fwrite(out, 1, o, g); fclose(g); }
    { FILE *g = fopen(argv[3], "wb");
      for (size_t i = 0; i < o; i++) {
          fputc(kind[i], g); fputc(hops[i], g);
          fwrite(&root[i], 4, 1, g);
      }
      fclose(g); }

    size_t lit = 0; for (size_t i = 0; i < o; i++) lit += (kind[i] == 0);
    fprintf(stderr, "%s: %zu -> %zu bytes, %zu literal (%.1f%%)\n",
            oku ? "oku" : "v1", n, o, lit, 100.0 * lit / o);
    return 0;
}
