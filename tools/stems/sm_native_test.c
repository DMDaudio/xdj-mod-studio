/* Mac test of sm.c's mixing with deck-sized reads: the file lives in
 * memory, fread/fseek are mocks with a position per handle (fp = 1).
 * usage: sm_native_test FILE MASK pos:n [pos:n ...] -> concatenated reads */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8; typedef unsigned int u32;
u8 n_mask;
u8 n_vsel; u32 n_vfp, n_vtbl[12];   /* version table (unused here: zeros) */
static u8 *F; static long FL; static long P;
u32 n_fread(void *b, u32 sz, u32 cnt, u32 fp)
{ (void)fp; long n = (long)sz * cnt; if (P + n > FL) n = FL - P; if (n < 0) n = 0;
  memcpy(b, F + P, n); P += n; return sz ? (u32)(n / sz) : 0; }
int n_fseek(u32 fp, int off, int wh)
{ (void)fp; long p = wh == 0 ? off : wh == 1 ? P + off : FL + off;
  if (p < 0 || p > FL) return -1; P = p; return 0; }
u32 n_pos(u32 fp) { (void)fp; return (u32)P; }
u32 sm_fread(void *, u32, u32, u32);
int main(int c, char **v)
{
    FILE *f = fopen(v[1], "rb"); fseek(f, 0, 2); FL = ftell(f); fseek(f, 0, 0);
    F = malloc(FL); fread(F, 1, FL, f); fclose(f);
    n_mask = (u8)strtol(v[2], 0, 0);
    static u8 buf[1 << 21];
    for (int i = 3; i < c; i++) {
        long pos, n; sscanf(v[i], "%ld:%ld", &pos, &n);
        P = pos;
        u32 got = sm_fread(buf, 1, (u32)n, 1);
        if (P != pos + got) { fprintf(stderr, "POS WRONG after read %d\n", i); return 2; }
        fwrite(buf, 1, got, stdout);
    }
    return 0;
}
