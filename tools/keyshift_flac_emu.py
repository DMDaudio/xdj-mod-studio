"""key shift (MT on) on a 96 kHz FLAC via the virtual-WAV layer, real DSP"""
import collections, hashlib, os, sys
sys.path[:0] = [".", "tools"]
from emu.machine import Machine
from emu import panl
from emu.ui import EmuApp
from ghosttap_emu import fb_png
import vfile_emu_test as V
import keyshift_dsp_emu as K

def fbh(m):
    fb_png(m, "/tmp/ksf.png"); return hashlib.md5(open("/tmp/ksf.png","rb").read()).hexdigest()[:8]

m = Machine.load(sys.argv[1] if len(sys.argv) > 1 else "cache/vfile_on_flac.pkl")
mem = m.mem; d = mem.dev.dspemu
EmuApp._install_keyshift(None, m)
m.cpu.run(limit=60_000_000)
ks = {l.split()[2].lstrip('_'): int(l.split()[0],16) for l in open("build/keyshift/ksh.syms")}
print("ksh status", hex(mem.r32(ks["ksh_status"])), "BD8 store", hex(mem.r16(0x08D36B3A)))
V.key(m, "RotaryPush", 5_000_000)            # load the FLAC
for _ in range(20): m.cpu.run(limit=10_000_000)
print("loaded: mode", mem.r32(0x0B6C4264), "dsp", d.bus.read(0x11837BF8,4))
K.mt_on(m)
print("MT", mem.r8(0x0B6C4308))
seen=[]
for step, semis in enumerate([2, 4, -3, 0, 6, -6]):
    mem.w32(K.SH_SLOT, 0x40000000 | K.q20(2 ** (semis / 12)) if semis else 0x00100000)
    e0 = m.exc_total; pcs = collections.Counter()
    for _ in range(15):
        m.cpu.run(limit=10_000_000); pcs[m.cpu.pc & ~0xFF] += 1
    h = fbh(m); seen.append(h)
    print(f"{semis:+d} st: mode {mem.r32(0x0B6C4264)} dsp {d.bus.read(0x11837BF8,4)} cnt {d.bus.read(0x11837CCC,4)} "
          f"err {getattr(d,'error',None)} exc +{m.exc_total-e0} screen {h} hot {[hex(p) for p,_ in pcs.most_common(2)]}", flush=True)
fb_png(m, "screenshots/ks_flac_after.png")
print("RESULT:", "FROZE" if len(set(seen[-3:])) == 1 else "alive")
