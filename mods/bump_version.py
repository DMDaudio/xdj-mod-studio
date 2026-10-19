#!/usr/bin/env python3
"""mod: bump_version -- raise the version the UPDATER sees.

    python3 mods/bump_version.py work 1.16

The banner "XDJ-700     MAINVer1.15" lives in the .UPD's S-record PREAMBLE,
which is plain uncompressed ASCII. No LZSS is involved, so this is the safest
possible edit -- and it is almost certainly what decides whether the deck
bothers to flash at all, since updaters normally refuse to install the version
already running.

Replacement must be the same length (4 chars), so the section length is
unchanged; repack.py recomputes the section CRC.
"""
import json, re, sys


def apply(work, new="1.16"):
    p = f"{work}/manifest.json"
    man = json.load(open(p))
    for s in man["sections"]:
        if s["name"] != "MAIN":
            continue
        m = re.search(r"(MAINVer)(\d\.\d\d)", s["preamble"])
        if not m:
            sys.exit("version banner not found in preamble")
        old = m.group(2)
        if len(new) != len(old):
            sys.exit(f"version must be {len(old)} chars like {old!r}")
        s["preamble"] = s["preamble"].replace(m.group(0), m.group(1) + new, 1)
        print(f"  MAIN banner {old} -> {new}  (uncompressed preamble, no LZSS)")
    json.dump(man, open(p, "w"), indent=1)


if __name__ == "__main__":
    apply(sys.argv[1] if len(sys.argv) > 1 else "work",
          sys.argv[2] if len(sys.argv) > 2 else "1.16")
