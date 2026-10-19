#!/bin/bash
# rebuild a pristine work/ from the stock firmware
set -e
UPD="${1:-$HOME/Downloads/cdj3k-mods-0.1.0/XDJ700.UPD}"
W="${2:-../work}"
rm -rf "$W"; mkdir -p "$W"
python3 updtool.py unpack "$UPD" "$W" >/dev/null
python3 - "$UPD" "$W" <<'PY'
import json,re,hashlib,sys
from xdj700 import srec_parse, flatten
raw=open(sys.argv[1],'rb').read(); w=sys.argv[2]
e1=raw.index(b'\r\n')+2; e2=raw.index(b'\r\n',e1)+2
lens=[int(raw[:e1-2]),int(raw[e1:e2-2])]; off=e2
man={'hdr_len':e2,'sections':[]}
for L in lens:
    blob=raw[off:off+L]; off+=L; text=blob.decode('latin1')
    name=re.search(r'XDJ-700\s+(\w+)Ver',text).group(1)
    lines=text.split('\r\n')
    f2=next(i for i,l in enumerate(lines) if l.startswith('S2'))
    pre='\r\n'.join(lines[:f2])
    post='\r\n'.join(lines[next(i for i,l in enumerate(lines) if l[:2] in ('S7','S8','S9')):])
    layout=[(int(l[4:10],16), int(l[2:4],16)-4) for l in lines[f2:] if l.startswith('S2')]
    chunks,st=srec_parse(text); img,base=flatten(chunks)
    man['sections'].append(dict(name=name,base=base,img_len=len(img),
        preamble=pre,postamble=post,layout=layout,
        sha=hashlib.sha256(img).hexdigest()))
json.dump(man,open(f'{w}/manifest.json','w'),indent=1)
PY
echo "work/ rebuilt from $UPD"
