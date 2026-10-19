#!/usr/bin/env python3
"""rgb_overview_repaint_test.py MODE OUT.png -- overview cave vs model on a
snapshot that ALREADY has a track loaded (cache/emu_rgb_daveamstrong.pkl,
Olav Basoski remix): install the cave, force a fresh snapshot, open+close
Browse to make the stock painter repaint, compare all 500x34 px.
(The old emu() fixture no longer loads a track: fake DSP -> E-8302.)
3band needs a track with PWV6 (this one has none)."""
import os,sys; sys.path[:0]=[".","tools"]
from emu.machine import Machine; from emu import panl; from ghosttap_emu import fb_png
import rgb_overview as R
mode=R.MODES[sys.argv[1]]
EXT=os.path.expanduser("~/Downloads/Rekordbox stuff/PIONEER/USBANLZ/P025/00003141/ANLZ0000.EXT")
m=Machine.load("cache/emu_rgb_daveamstrong.pkl",core="py"); c,mem=m.cpu,m.mem
code,_=R.plan(mode)
for i,b in enumerate(code): mem.w8(R.CAVE+i,b)
for a,b in R.extra_writes(): mem.w8(a,b)
for i in range(16): mem.w8(R.SNAP_HASH+i,0)          # force a fresh snapshot
mem.w8(R.MODE_ADDR,mode); mem.w32(R.HOOK_POOL,R.CAVE)
seen=[]
def tr(pc,op):
    if pc==R.CAVE: seen.append((mem.r32(c.gr(15)),mem.r32(c.gr(15)+4)))
c.trace=tr
for k in ("Browse","Browse"):
    mem.dev.panel_frame=panl.build_frame(keys=[k]); c.run(limit=3_000_000)
    mem.dev.panel_frame=panl.build_frame(); c.run(limit=20_000_000)
c.trace=None
fb_png(m,sys.argv[2]); print("cave calls",len(seen))
if not seen: sys.exit("no repaint")
stride,layer=seen[-1]
if mode==0:   # stock blue = the cave returns before drawing; inspect the PNG
    print(f"blue: stock painter output left as-is (see {sys.argv[2]})"); sys.exit(0)
want=R.model_pixels(R.section_from_file(EXT,b"PWV6" if mode==2 else b"PWV4"),mode)
bad=sum(1 for col in range(R.COLS) for k in range(R.ROWS) if mem.r16((layer+((33-k)*stride+col)*2)&0x1FFFFFFF)!=want[col][k])
print(f"{sys.argv[1]}: {R.COLS*R.ROWS-bad}/{R.COLS*R.ROWS} px match model")
