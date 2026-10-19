#!/usr/bin/env python3
"""panl_oracle.py CHECKPOINT OUT.json -- run the firmware's own PANL parser (0x8D51A64) on synthetic payloads and
record which key-bitmap bits (0x0B6C7A28 + 84..107) it sets."""
import sys,json
sys.path.insert(0,'.')
from emu.machine import Machine
from emu.sh4 import PcHalt
m=Machine.load(sys.argv[1], core="py"); c=m.cpu; mem=m.mem  # edits register lists in place: Python core
PAY=0x0B6C7A30; KEYS=0x0B6C7A28
MAGIC=0x08000118   # inside vector nop-area; we halt when pc reaches it
snap_ram0=bytes(mem.ram0); snap_ram1=bytes(mem.ram1)
regs=(list(c.b0),list(c.b1),c.sr,c.pc,c.pr,c.gbr)
def restore():
    mem.ram0[:]=snap_ram0; mem.ram1[:]=snap_ram1
    c.b0[:],c.b1[:]=regs[0],regs[1]; c.sr,c.pc,c.pr,c.gbr=regs[2],regs[3],regs[4],regs[5]
def call(payload):
    restore()
    for i,b in enumerate(payload): mem.w8(PAY+i,b)
    c.sr|=0x10000000   # BL: no interrupts during the call
    c.pr=MAGIC; c.pc=0x08D51A64
    def tr(pc,op):
        if pc==MAGIC: raise PcHalt(pc)
    c.trace=tr
    res=c.run(limit=200000); c.trace=None
    return bytes(mem.r8(KEYS+84+k) for k in range(24)), res
base,res=call(bytes(30)); print('baseline',base.hex(),res)
out={}
for byte in range(2,30):
    for bit in range(8):
        p=bytearray(30); p[byte]|=1<<bit
        k,res=call(p)
        diff=[(84+i,a^b) for i,(a,b) in enumerate(zip(base,k)) if a!=b]
        if diff: out[f'{byte}.{bit}']=diff; print(f'payload[{byte}] bit{bit} ->',[(o,hex(x)) for o,x in diff])
json.dump(out,open(sys.argv[2],'w'))
