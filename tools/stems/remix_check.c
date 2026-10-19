/* remix_check -- prove sm.c's re-mix window: sequential deck-style reads
 * (advancing file position AND advancing dest buffers, like read-ahead)
 * under MASK_A, then a mask change to MASK_B. Dumps the arena (what the
 * deck would still hold buffered) so the caller can verify every byte now
 * carries the new mask's mix (or the original bytes for MASK_B == 0).
 * usage: remix_check FILE A maskA maskB chunk nchunks
 * stdout: arena[0..used). stderr: "used=.. remix=.. remixb=.. pos=..".
 * exit 2 on file-position discipline failure. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8; typedef unsigned int u32; typedef unsigned short u16; typedef unsigned long uptr;
u8 n_mask;
u8 n_vsel; u32 n_vfp, n_vtbl[12];   /* version table: set by version mode */
static u8 *F; static long FL; static long P;
u32 n_fread(void *b, u32 sz, u32 cnt, u32 fp)
{ (void)fp; long n = (long)sz * cnt; if (P + n > FL) n = FL - P; if (n < 0) n = 0;
  memcpy(b, F + P, n); P += n; return sz ? (u32)(n / sz) : 0; }
int n_fseek(u32 fp, int off, int wh)
{ (void)fp; long p = wh == 0 ? off : wh == 1 ? P + off : FL + off;
  if (p < 0 || p > FL) return -1; P = p; return 0; }
u32 n_pos(u32 fp) { (void)fp; return (u32)P; }
u32 sm_fread(void *, u32, u32, u32);
extern u32 sm_remix, sm_remixb;
static u8 arena[12 << 20];
int main(int c, char **v)
{
    FILE *f = fopen(v[1], "rb"); fseek(f, 0, 2); FL = ftell(f); fseek(f, 0, 0);
    F = malloc(FL); fread(F, 1, FL, f); fclose(f);
    if (!strcmp(v[2], "V")) {                   /* version mode */
        u8 *tb = F + FL - 128;
        u32 doff = *(u32 *)(tb + 4), dlen = *(u32 *)(tb + 8);
        u32 n = *(u16 *)(tb + 12);
        n_vfp = 1;
        n_vtbl[0] = doff; n_vtbl[1] = dlen; n_vtbl[2] = n;
        memcpy(n_vtbl + 3, tb + 16, 8 * 4);
        long A = (long)doff + 65536;
        n_vsel = (u8)strtol(v[3], 0, 0);
        u8 vsB = (u8)strtol(v[4], 0, 0);
        long chunk = atol(v[5]); int nch = atoi(v[6]);
        fprintf(stderr, "n=%u doff=%u dlen=%u\n", n, doff, dlen);
        P = A;
        long off = 0;
        for (int i = 0; i < nch; i++) {
            long want = chunk;
            if (A + off + want > FL) want = FL - (A + off);
            if (want <= 0) break;
            long p0 = P;
            u32 got = sm_fread(arena + off, 1, (u32)want, 1);
            if (P != p0 + got) { fprintf(stderr, "POS WRONG chunk %d\n", i); return 2; }
            off += got;
            if (got < (u32)want) break;
        }
        n_vsel = vsB;
        long p1 = P;
        long trig_off = off;
        u32 trig = sm_fread(arena + off, 1, 4096, 1);
        if (P != p1 + trig) { fprintf(stderr, "POS WRONG trigger\n"); return 2; }
        off += trig;
        fwrite(arena, 1, (size_t)off, stdout);
        fprintf(stderr, "used=%ld trigoff=%ld remix=%u remixb=%u pos=%ld\n", off, trig_off,
                sm_remix, sm_remixb, P);
        return 0;
    }
    long A = atol(v[2]);
    n_mask = (u8)strtol(v[3], 0, 0);
    u8 maskB = (u8)strtol(v[4], 0, 0);
    long chunk = atol(v[5]); int nch = atoi(v[6]);
    P = A;                                  /* deck starts reading at A */
    long off = 0;
    for (int i = 0; i < nch; i++) {
        long want = chunk;
        if (A + off + want > FL) want = FL - (A + off);
        if (want <= 0) break;
        long p0 = P;
        u32 got = sm_fread(arena + off, 1, (u32)want, 1);
        if (P != p0 + got) { fprintf(stderr, "POS WRONG chunk %d\n", i); return 2; }
        off += got;
        if (got < (u32)want) break;
    }
    n_mask = maskB;                         /* the pad tap */
    long p1 = P;
    u32 trig = sm_fread(arena + off, 1, 4096, 1);
    if (P != p1 + trig) { fprintf(stderr, "POS WRONG trigger\n"); return 2; }
    off += trig;
    fwrite(arena, 1, (size_t)off, stdout);
    fprintf(stderr, "used=%ld remix=%u remixb=%u pos=%ld\n", off, sm_remix, sm_remixb, P);
    return 0;
}
