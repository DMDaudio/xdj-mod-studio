#!/usr/bin/env python3
"""scrub_app.py APP -- keep the builder's name and folders out of a built app.

Compilers record build paths (a .so's own output path, objdump's source
paths in its error messages). This replaces the builder's home folder
(/Users/<name>) with a neutral path of the SAME length in every file, so
binaries stay valid, re-signs every Mach-O it touched, then checks every
file -- including the frozen server's compressed Python modules -- and
exits 1 if the user name is still anywhere.
"""
import os
import subprocess
import sys
import tempfile

HOME = os.path.expanduser("~").encode()           # e.g. b"/Users/JamesG"
USER = os.path.basename(os.path.expanduser("~")).encode()
NEUTRAL = (b"/Users/" + b"x" * max(1, len(HOME) - 7))[:len(HOME)]
MACHO = (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe")


def files(app):
    for d, _, fs in os.walk(app):
        for f in fs:
            p = os.path.join(d, f)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p


def scrub(app):
    signed = []
    for p in files(app):
        b = open(p, "rb").read()
        if HOME not in b:
            continue
        open(p, "wb").write(b.replace(HOME, NEUTRAL))
        if b[:4] in MACHO:
            signed.append(p)
        print("scrubbed", os.path.relpath(p, app))
    for p in signed:
        subprocess.run(["codesign", "--force", "-s", "-", p], check=True,
                       capture_output=True)
    return signed


def pyz_hits(app):
    """User name inside the frozen server's compressed modules."""
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except ImportError:
        return []
    hits = []
    server = os.path.join(app, "Contents", "Resources", "server")
    for p in files(server) if os.path.isdir(server) else []:
        if open(p, "rb").read(4) not in MACHO or "_internal" in p:
            continue
        try:
            car = CArchiveReader(p)
        except Exception:
            continue
        for name in car.toc:
            try:
                data = car.extract(name)
            except Exception:
                continue
            if not data:
                continue
            if USER in data:
                hits.append(f"{os.path.basename(p)}:{name}")
            if "PYZ" in name:
                t = tempfile.mktemp()
                open(t, "wb").write(data)
                try:
                    z = ZlibArchiveReader(t)
                    for mod in z.toc:
                        try:
                            raw = z.extract(mod, raw=True)
                        except TypeError:
                            raw = z.extract(mod)
                        if isinstance(raw, tuple):
                            raw = raw[1]
                        if isinstance(raw, (bytes, bytearray)) and USER in raw:
                            hits.append(f"compressed module {mod}")
                finally:
                    os.remove(t)
    return hits


def main(app):
    scrub(app)
    left = [os.path.relpath(p, app) for p in files(app)
            if USER.lower() in open(p, "rb").read().lower()]
    left += pyz_hits(app)
    if left:
        print("REFUSED: the builder's name is still in the app:")
        for x in left:
            print("  ", x)
        sys.exit(1)
    print(f"scrub: no '{USER.decode()}' anywhere in {os.path.basename(app)}")


if __name__ == "__main__":
    main(sys.argv[1])
