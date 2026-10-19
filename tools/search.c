/* search.c -- sweep LZSS codec variants, scored by the SH-4 mov.l oracle.
 *
 * Oracle: SH-4 loads a 32-bit constant only via mov.l @(disp,PC),Rn (0xDnxx),
 * targeting a 4-byte-aligned literal within +1020 bytes. Peripheral register
 * addresses are fixed by silicon, so in a CORRECT decode nearly every aligned
 * register constant must be the target of some mov.l. Calibrated at 100% on
 * the uncompressed boot stub; the current codec scores 12%.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define LIMIT (20u<<20)
static uint8_t *src; static size_t srcn;
static uint32_t *regs; static int nregs;
static uint8_t *out, *ptd;

static int regcmp(const void*a,const void*b){
    uint32_t x=*(const uint32_t*)a,y=*(const uint32_t*)b; return x<y?-1:x>y; }
static int isreg(uint32_t v){
    int lo=0,hi=nregs-1; while(lo<=hi){int m=(lo+hi)/2;
        if(regs[m]==v)return 1; if(regs[m]<v)lo=m+1; else hi=m-1;} return 0; }

typedef struct { int lensrc, offmode, thr, esc, dist, N, r0z, scale; } P;

static size_t decode(P p){
    int N=p.N, F=18, r0 = p.r0z ? 0 : N-F;
    static uint8_t rb[65536]; memset(rb,0x20,N-F); memset(rb+N-F,0,F);
    int r=r0%N; size_t o=0,q=0;
    while(q<srcn && o<LIMIT){
        uint8_t fl=src[q++];
        for(int b=0;b<8;b++){
            if(q>=srcn||o>=LIMIT) return o;
            if((fl>>b)&1){ uint8_t c=src[q++]; out[o++]=c; rb[r]=c; r=(r+1)%N; }
            else{
                if(q+1>=srcn) return o;
                uint8_t b1=src[q],b2=src[q+1]; q+=2;
                int nib, i, j, rest;
                switch(p.lensrc){
                  case 0: nib=b1&0xF;  rest=(b1>>4)|(b2<<4); break;   /* 12b: b1hi|b2 */
                  case 1: nib=b1>>4;   rest=(b1&0xF)|(b2<<4); break;
                  case 2: nib=b2&0xF;  rest=b1|((b2>>4)<<8);  break;   /* 12b: b1|b2hi */
                  default:nib=b2>>4;   rest=b1|((b2&0xF)<<8);  break;
                }
                if(p.esc && nib==0xF){
                    if(q>=srcn) return o;
                    uint8_t e=src[q++];
                    j = p.esc==1 ? e+p.thr+1 : (e? e:256);
                } else j = nib+p.thr+1;
                switch(p.offmode){
                  case 0: i=rest; break;
                  case 1: i=((rest&0xFF)<<4)|(rest>>8); break;
                  case 2: i=(rest<<4)&0xFFF; break;
                  default:i=rest; break;
                }
                i=(i*p.scale)&(N-1);
                if(p.dist) i=(r-i)&(N-1);
                for(int k=0;k<j&&o<LIMIT;k++){ uint8_t c=rb[(i+k)&(N-1)];
                    out[o++]=c; rb[r]=c; r=(r+1)%N; }
            }
        }
    }
    return o;
}

static void score(size_t n,int*consts,int*hit){
    memset(ptd,0,LIMIT/4+8);
    for(size_t p=0;p+1<n;p+=2){
        uint16_t w=out[p]|(out[p+1]<<8);
        if((w&0xF000)==0xD000){ size_t t=((p+4)&~3ull)+(w&0xFF)*4;
            if(t+3<n) ptd[t/4]=1; }
    }
    int c=0,h=0;
    for(size_t o=0;o+3<n;o+=4){
        uint32_t v=out[o]|(out[o+1]<<8)|(out[o+2]<<16)|((uint32_t)out[o+3]<<24);
        if(isreg(v)){ c++; if(ptd[o/4]) h++; }
    }
    *consts=c;*hit=h;
}

int main(void){
    FILE*f=fopen("/tmp/blk1.lz","rb"); fseek(f,0,SEEK_END); srcn=ftell(f);
    fseek(f,0,SEEK_SET); src=malloc(srcn); if(fread(src,1,srcn,f)!=srcn)return 1; fclose(f);
    f=fopen("/tmp/regs.txt","r"); regs=malloc(4*4096); char L[64];
    while(fgets(L,sizeof L,f)) regs[nregs++]=(uint32_t)strtoul(L,0,16);
    fclose(f); qsort(regs,nregs,4,regcmp);
    out=malloc(LIMIT); ptd=malloc(LIMIT/4+8);
    printf("%-6s%-8s%-5s%-6s%-6s%-7s%-5s%9s%8s%6s\n",
           "len","off","thr","scale","dist","N","r0","consts","hit","pct");
    const char*LN[]={"b1lo","b1hi","b2lo","b2hi"};
    const char*OF[]={"direct","swap","shl4","direct"};
    int NS[]={2048,4096,8192};
    int SC[]={1};
    for(int ls=2;ls<3;ls++)for(int om=0;om<1;om++)for(int th=0;th<=4;th++)
    for(int di=0;di<2;di++)
    for(int nn=0;nn<3;nn++)for(int rz=0;rz<2;rz++)for(int sc=0;sc<1;sc++){
        P p={ls,om,th,0,di,NS[nn],rz,SC[sc]};
        size_t n=decode(p); int c,h; score(n,&c,&h);
        if(c>=25)
            printf("%-6s%-8s%-5d%-6d%-6d%-7d%-5d%9d%8d%5d%%\n",
                   LN[ls],OF[om],th,SC[sc],di,p.N,rz?0:p.N-18,c,h,h*100/c);
    }
    fprintf(stderr,"sweep done\n");
    return 0;
}
