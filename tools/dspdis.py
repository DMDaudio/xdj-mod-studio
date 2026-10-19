#!/usr/bin/env python3
"""dspdis.py -- disassemble the C674x DSP program embedded in blk1.

    .venv/bin/python3 tools/dspdis.py 0x56054 0x200     (blk1 offset, length)
    .venv/bin/python3 tools/dspdis.py --all > work/dsp.s

The DSP program occupies blk1 0x0-0x70000 (TI C6000, little-endian). C64x+
compact instructions are grouped in 32-byte fetch packets whose alignment is
the DSP load address, not the blk1 offset: in the areas checked
(0x12000-0x60000) the code aligns with blk1 offset + 16 (mod 32). This tool
slices from a true packet start and relabels, so printed addresses ARE
blk1 offsets.
Areas with a different load alignment will decode as garbage -- check for
runs of <undefined instruction>.

Needs work/tic6x/objdump (tools/build_tic6x_objdump.sh).
"""
import os, re, subprocess, sys, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OD = os.path.join(ROOT, 'work', 'tic6x', 'objdump')
ALIGN = 16      # fetch packets start at blk1 offsets == 16 (mod 32)


def disasm(lo=0, n=0x70000):
    d = open(os.path.join(ROOT, 'cache', 'blk1.bin'), 'rb').read()
    lo_al = max(ALIGN, ((lo - ALIGN) & ~31) + ALIGN)
    hi = ((lo + n - ALIGN + 31) & ~31) + ALIGN
    blob = d[lo_al:hi]
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as f:
        f.write(blob)
    out = subprocess.run([OD, '-D', '-EL', '-b', 'binary', '-m', 'tic6x', f.name],
                         capture_output=True, text=True).stdout
    os.unlink(f.name)
    for l in out.splitlines():
        m = re.match(r'^\s*([0-9a-f]+):(.*)', l)
        if not m:
            continue
        a = int(m.group(1), 16) + lo_al
        # objdump prints branch/call/addkpc targets in blob coordinates too;
        # constants are printed in decimal, so every 0x.. operand is an address
        body = m.group(2).split('\t', 2)[-1] if '\t' in m.group(2) else m.group(2)
        if '<' not in body:     # skip <fetch packet header ..>, <undefined ..>
            body = re.sub(r'0x([0-9a-f]+)',
                          lambda t: hex((int(t.group(1), 16) + lo_al) & 0xFFFFFFFF), body)
        raw = m.group(2).split('\t')[1] if m.group(2).count('\t') >= 2 else ''
        if a >= lo:
            yield f'{a:8x}:\t{raw}\t{body}' if raw else f'{a:8x}:{m.group(2)}'


if __name__ == '__main__':
    if sys.argv[1:] == ['--all']:
        for l in disasm():
            print(l)
    else:
        for l in disasm(int(sys.argv[1], 0), int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x100):
            print(l)
