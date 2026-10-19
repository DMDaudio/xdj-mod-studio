#!/usr/bin/env python3
"""pcm_catch.py -- snapshot DMAC address registers while a track plays.

READ-ONLY. Each window goes through a FRESH shell-tool process (the deck
answers one full BR per fresh session reliably; rapid re-use truncates).
Takes ~2-3 minutes. Play a track first: SARn with a RAM address = live
PCM buffer; DARn = destination.
"""
import re
import struct
import subprocess
import sys
import time

DMACS = (0xFE008000, 0xFE009000)
DMAC1 = 0xFDC08000       # second DMA unit (approved: reads only)
SPAN = 0x80
TOOL = ["./.venv/bin/python", "tools/xdj700_usb_shell.py"]
PACE = 25                # seconds between opens: the deck wedges on rapid
                         # successive sessions, recovers after minutes quiet


def br(addr, count):
    time.sleep(PACE)
    p = subprocess.run(
        TOOL + ["--handshake", "--br", f"{addr:x}", f"{count:x}"],
        capture_output=True, timeout=120)
    out = p.stdout.decode("ascii", "replace")
    # reply bytes render as "XX XX 20" (two hex chars + ASCII space);
    # padding zeros never form that triple, so this is exact.
    vals = [int(bytes([int(a, 16), int(b, 16)]).decode(), 16)
            for a, b in re.findall(r"([0-9a-fA-F]{2}) ([0-9a-fA-F]{2}) 20 ", out)]
    return bytes(vals[:count])


def dump_unit(base, name):
    """Read + decode one 6-channel DMA unit (ch0-3, DMAOR, ch4-5)."""
    print(f"===== {name} {base:#x} =====", flush=True)
    blob = bytearray()
    for off in range(0, SPAN, 32):
        got = br(base + 0x20 + off, 32)
        print(f"  @{base + 0x20 + off:#x}: {len(got)}/32 values",
              flush=True)
        blob.extend(got)
    if len(blob) < SPAN:
        print(f"  short reply ({len(blob)}B, want {SPAN})")
        return
    for ch in (0, 1, 2, 3):
        sar, dar, tcr, chcr = struct.unpack_from("<IIII", blob, ch * 16)
        print(f"  ch{ch}: SAR={sar:#010x} DAR={dar:#010x} "
              f"TCR={tcr:#x} CHCR={chcr:#08x}{tag(sar)}", flush=True)
    (dmaor,) = struct.unpack_from("<I", blob, 0x40)
    print(f"  DMAOR={dmaor:#010x} (DME bit0 = unit on)", flush=True)
    for ch in (4, 5):
        sar, dar, tcr, chcr = struct.unpack_from("<IIII", blob,
                                                0x50 + (ch - 4) * 16)
        print(f"  ch{ch}: SAR={sar:#010x} DAR={dar:#010x} "
              f"TCR={tcr:#x} CHCR={chcr:#08x}{tag(sar)}", flush=True)


def tag(sar):
    return " <- RAM SOURCE (pcm?)" if 0x08000000 <= sar < 0x10000000 else ""


def main():
    dump_unit(DMACS[0], "DMA0")
    dump_unit(DMAC1, "DMA1")
    print("===== DMARS (DMA0 request wiring) =====", flush=True)
    got = br(DMACS[1], 12)
    print(f"  {got.hex() if got else 'no reply'}", flush=True)
    print("done - reads only, deck untouched")


if __name__ == "__main__":
    sys.exit(main())
