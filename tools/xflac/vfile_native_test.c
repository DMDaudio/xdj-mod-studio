/* Mac unit test of vfile.c: mock the deck's stock file functions with
 * stdio, read the virtual WAV like the deck does, dump it to stdout.
 * usage: vfile_native_test FILE [seek_off len]...   (tools/xflac_vfile_test.py) */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
extern uintptr_t o_fread, o_seek, o_tell, o_eof, o_size, o_close, vf_read_prev;
int vf_fread(void *, int, int, void *);
int vf_seek(void *, int, int);
int vf_tell(void *);
int vf_size(void *, unsigned *);
static int m_fread(void *b, int s, int c, void *fp) { return (int)fread(b, s, c, fp); }
static int m_seek(void *fp, int o, int w) { return fseek(fp, o, w); }
static int m_tell(void *fp) { return (int)ftell(fp); }
static int m_eof(void *fp) { return feof(fp); }
static int m_size(void *fp, unsigned *o) { long p = ftell(fp); fseek(fp, 0, SEEK_END); *o = ftell(fp); fseek(fp, p, SEEK_SET); return 0; }
static int m_close(void *fp) { return fclose(fp); }
int main(int argc, char **argv)
{
    o_fread = vf_read_prev = (uintptr_t)m_fread; o_seek = (uintptr_t)m_seek;
    o_tell = (uintptr_t)m_tell; o_eof = (uintptr_t)m_eof; o_size = (uintptr_t)m_size;
    o_close = (uintptr_t)m_close;
    FILE *fp = fopen(argv[1], "rb");
    unsigned sz = 0;
    vf_size(fp, &sz);
    fprintf(stderr, "virtual size %u\n", sz);
    static unsigned char buf[0x24C0];
    if (argc == 2) {                       /* whole file, deck-sized reads */
        int k;
        vf_seek(fp, 0, 0);
        while ((k = vf_fread(buf, 1, sizeof buf, fp)) > 0) fwrite(buf, 1, k, stdout);
        return 0;
    }
    for (int i = 2; i + 1 < argc; i += 2) {   /* seek/len pairs */
        int off = atoi(argv[i]), len = atoi(argv[i + 1]);
        vf_seek(fp, off, 0);
        unsigned char *b = malloc(len);
        int k = vf_fread(b, 1, len, fp);
        fwrite(b, 1, k, stdout);
        free(b);
    }
    return 0;
}
