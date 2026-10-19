import sys, hashlib
from xdj700 import *

import os; UPD=os.path.expanduser('~/Downloads/cdj3k-mods-0.1.0/XDJ700.UPD')
raw=open(UPD,'rb').read()
text=raw.decode('latin1')

# --- 1. S-record layer: re-emit every record and compare bytes ---
ok=fail=0
for ln in text.split('\r\n'):
    if not ln.startswith('S2'): continue
    body=ln[2:]; cnt=int(body[:2],16)
    addr=int(body[2:8],16); data=bytes.fromhex(body[8:8+(cnt-4)*2])
    if srec_emit(addr,data)==ln: ok+=1
    else:
        fail+=1
        if fail<3: print("  MISMATCH\n   orig:",ln,"\n   ours:",srec_emit(addr,data))
print(f"[1] S-record re-emit: {ok} identical, {fail} mismatched")

# --- 2. LZSS: decompress -> compress -> decompress ---
chunks,start=srec_parse(text[:raw.find(b'XDJ-700     PANL')])
img,base=flatten(chunks)
print(f"[2] MAIN image {len(img)} bytes @ {base:#x}, start={start}")
for hdr,ln in find_blocks(img):
    comp=img[hdr+8:hdr+8+ln]
    dec=lzss_decompress(comp)
    re_comp=lzss_compress(dec)
    re_dec=lzss_decompress(re_comp)
    tag='OK ' if re_dec==dec else 'BAD'
    print(f"    block @{hdr:#08x} comp={ln} dec={len(dec)} "
          f"recomp={len(re_comp)} ({len(re_comp)/ln*100:.1f}% of orig) roundtrip={tag}")
