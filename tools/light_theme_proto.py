#!/usr/bin/env python3
import sys, colorsys, numpy as np; sys.path[:0]=['.','tools','mods']
from emu.machine import Machine
from ghosttap_emu import fb_png
import theme_switch as TS, theme_deck as TD
m=Machine.load(sys.argv[1] if len(sys.argv)>1 else "cache/emu_dsp_playing7.pkl"); mem=m.mem
w=TS.with_own_art(TD.stock_words()); regs=TS.regions(w)
import json
for off,st,sx,sy,ww,hh in json.load(open('mods/sprite_map_light.json'))['rects']:   # plain white/grey pictures
    for j in range(hh): regs.append((0x08000000+off+2*((sy+j)*st+sx), ww))
# luminance-flip map for every colour
allc=np.arange(65536)
r=((allc>>11)&31)/31; g=((allc>>5)&63)/63; b=(allc&31)/31
out=np.zeros(65536,np.uint16)
for c in range(65536):
    h,l,s=colorsys.rgb_to_hls(r[c],g[c],b[c])
    l2 = (1 - l) ** 3.0 if s < 0.25 else 1 - l*0.88; s = s*0.8   # neutrals: flip + contrast; colours: flip
    rr,gg,bb=colorsys.hls_to_rgb(h,min(max(l2,0),1),s)
    out[c]=(round(rr*31)<<11)|(round(gg*63)<<5)|round(bb*31)
out[0xF81F]=0xF81F
ram=mem.ram0
mask=np.zeros(len(w),bool)                         # union: each pixel once
for a,n in regs:
    o=(a-0x08000000)//2
    if 0<=o and o+n<=len(mask): mask[o:o+n]=True
v=np.frombuffer(bytes(ram[:2*len(w)]),'<u2').copy()
v[mask]=out[v[mask]]
ram[:2*len(w)]=v.astype('<u2').tobytes()
t=mem.dev.tdg; F=t.fill
BG=out[0]
def fill(flags,colour,x,y,ww,hh, F=F): return F(flags, int(out[colour&0xFFFF]) if (colour&0xFFFF) in (0,) else colour, x,y,ww,hh)
t.fill=fill
# light-mode text wrapper (tools/lighttext)
ls={l.split()[2].lstrip('_'):int(l.split()[0],16) for l in open('build/lighttext/lt.syms')}
for i,b in enumerate(open('build/lighttext/lt.bin','rb').read()): mem.w8(ls['lt_head']+i,b)
mem.w32(ls['lt_on'],1)
for pool in (0x08C2F8FC,0x08C31FC4): mem.w32(pool, ls['lt_entry'])
for pool in (0x08DC3B44,0x08DC2808): mem.w32(pool, ls['lt_mset'])   # waveform windows
for pool in (0x08DC2A24,0x08DC2C74,0x08DC2EA4,0x08DC31A0,0x08DC32B0,0x08DC3DA8,0x08DC3FF4,
             0x08DC417C,0x08DC4470,0x08DC4758,0x08DC4A24):              # waveform colours
    assert mem.r32(pool)==0x08DB6CD4; mem.w32(pool, ls['lt_rgb'])
m.cpu.run(limit=80_000_000)
print("text calls", mem.r32(ls['lt_calls']))
fb_png(m,"screenshots/light_proto_main.png")
print("bg", hex(BG))
import os
if os.environ.get("PERFORM"):
    from emu import panl
    mem.dev.panel_frame=panl.build_frame(touch=(718,38)); m.cpu.run(limit=6_000_000)
    mem.dev.panel_frame=panl.build_frame(); m.cpu.run(limit=80_000_000)
    fb_png(m,"screenshots/light_proto_perform.png")
