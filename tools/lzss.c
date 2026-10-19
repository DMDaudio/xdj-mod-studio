/* NOTE 2026-09-05: the 0x20 pre-fill below is WRONG. The deck's boot stub at
 * MAIN.img 0x0ABC clears its 0x1011-byte ring (N=4096, F=18, confirmed) to
 * ZERO, not 0x20. See BOOTSTUB.md. Affects ~5.3 KB of early output. Left as-is
 * for now so existing offsets stay valid; fix before trusting early decode.
 *
 * XDJ-700 LZSS (Okumura variant): 4096-byte ring pre-filled 0x20 for the
 * first N-F bytes, r starts at N-F, control byte LSB-first,
 * 1 = literal, 0 = (offset:12, len-3:4) little-endian pair. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define N 4096
#define F 18
#define THR 2
#define R0 (N - F)
#define MAXD (N - F)

static uint8_t *rd(const char *p, size_t *n){
    FILE *f=fopen(p,"rb"); if(!f){perror(p);exit(1);}
    fseek(f,0,SEEK_END); *n=ftell(f); fseek(f,0,SEEK_SET);
    uint8_t *b=malloc(*n); if(fread(b,1,*n,f)!=*n){perror("read");exit(1);}
    fclose(f); return b;
}

static void decompress(const uint8_t *s, size_t n, FILE *out){
    uint8_t rb[N]; memset(rb,0x20,R0); memset(rb+R0,0,N-R0);
    int r=R0; size_t p=0;
    while(p<n){
        uint8_t fl=s[p++];
        for(int b=0;b<8;b++){
            if(p>=n) return;
            if(fl>>b & 1){ uint8_t c=s[p++]; fputc(c,out); rb[r]=c; r=(r+1)&(N-1); }
            else{
                if(p+1>=n) return;
                uint8_t b1=s[p],b2=s[p+1]; p+=2;
                /* b1: offset bits 8-11 in the high nibble, (len-3) in the low
                 * nibble. b2: offset bits 0-7. */
                int i=b2 | ((b1&0xF0)<<4), j=(b1&0x0F)+THR+1;
                for(int k=0;k<j;k++){ uint8_t c=rb[(i+k)&(N-1)];
                    fputc(c,out); rb[r]=c; r=(r+1)&(N-1); }
            }
        }
    }
}

/* hash chain over the source, distances limited to MAXD */
#define HB 16
#define HS (1<<HB)
static int head[HS], *prevv;
static inline int hsh(const uint8_t *d){ return ((d[0]<<10)^(d[1]<<5)^d[2]) & (HS-1); }

static void find_match(const uint8_t *d, size_t n, size_t t, int *bl_, int *bd_){
    int bl=0, bd=0;
    if(t+2<n){
        int h=hsh(d+t), c=head[h], depth=0;
        while(c>=0 && (long)t-c<=MAXD && depth++<1024){
            int l=0, lim=(n-t<F)?(int)(n-t):F;
            while(l<lim && d[c+l]==d[t+l]) l++;
            if(l>bl){ bl=l; bd=(int)(t-c); if(l==F) break; }
            c=prevv[c];
        }
    }
    *bl_=bl; *bd_=bd;
}

/* Prepend the decompressor's initial ring so matches can reference it, exactly
 * as Pioneer's encoder does. Ring slot for src index q is (R0+q) & (N-1);
 * INIT_rot[j] = rb_init[(R0+j)&(N-1)] = 0 for j<F, else 0x20. */
static void compress(const uint8_t *raw, size_t rawn, FILE *out){
    size_t n = rawn + N;
    uint8_t *d = malloc(n);
    for(size_t j=0;j<N;j++) d[j] = (j < F) ? 0x00 : 0x20;
    memcpy(d+N, raw, rawn);
    prevv=malloc(sizeof(int)*(n+1));
    for(int i=0;i<HS;i++) head[i]=-1;
    /* index the seeded prefix without emitting it */
    for(size_t q=0;q+2<N;q++){ int h=hsh(d+q); prevv[q]=head[h]; head[h]=(int)q; }
    uint8_t buf[8*2]; int nb=0, ctrl=0, bit=0;
    size_t t=N;
    int pl=0, pd=0, have=0;                 /* lazily carried match */
    while(t<n){
        int bl, bd;
        if(have){ bl=pl; bd=pd; have=0; }
        else find_match(d,n,t,&bl,&bd);
        /* lazy: if the next position matches longer, emit a literal here */
        if(bl>THR && t+1<n){
            int nl,nd; find_match(d,n,t+1,&nl,&nd);
            if(nl>bl){ pl=nl; pd=nd; have=1; bl=0; }
        }
        if(bl>THR){
            int i=(R0 + (int)(t - bd)) & (N-1);
            buf[nb++]= ((i>>4)&0xF0) | (bl-THR-1);
            buf[nb++]= i & 0xFF;
        } else { bl=1; ctrl |= 1<<bit; buf[nb++]=d[t]; }
        for(int k=0;k<bl;k++){
            size_t q=t+k;
            if(q+2<n){ int h=hsh(d+q); prevv[q]=head[h]; head[h]=(int)q; }
        }
        t+=bl;
        if(++bit==8){ fputc(ctrl,out); fwrite(buf,1,nb,out); ctrl=0; bit=0; nb=0; }
    }
    if(bit){ fputc(ctrl,out); fwrite(buf,1,nb,out); }
    free(prevv); free(d);
}

int main(int c, char **v){
    if(c!=4){ fprintf(stderr,"usage: lzss d|c in out\n"); return 2; }
    size_t n; uint8_t *b=rd(v[2],&n);
    FILE *o=fopen(v[3],"wb"); if(!o){perror(v[3]);return 1;}
    if(*v[1]=='d') decompress(b,n,o); else compress(b,n,o);
    fclose(o); return 0;
}
