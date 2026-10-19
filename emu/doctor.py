#!/usr/bin/env python3
"""XDJ-700 emulator doctor: one command that says what is broken.

    python3 -m emu.doctor        # any python3; stdlib only, never crashes

Prints PASS/FAIL per check and exits 0 only if everything needed to run
and rebuild is present. Paste the whole output when reporting a problem.
"""
import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def try_import(mod):
    try:
        m = __import__(mod)
        return True, getattr(m, "__version__", "?")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main():
    print(f"python: {sys.version.split()[0]} ({sys.executable})")
    print(f"macos: {platform.mac_ver()[0] or '?'}  arch: {platform.machine()}")
    print(f"repo: {ROOT}")
    print()

    ok, v = try_import("pygame")
    check("pygame installed", ok, f"v{v}" if ok else v)
    if ok:
        try:
            import pygame.font  # noqa
            check("pygame.font works", True, "native text")
        except Exception as e:
            try:
                import PIL  # noqa
                check("pygame.font works", True,
                      f"broken ({type(e).__name__}), Pillow fallback active")
            except Exception:
                check("pygame.font works", False, "broken and no Pillow fallback")
    ok, v = try_import("numpy")
    check("numpy installed", ok, f"v{v}" if ok else v)
    ok, v = try_import("PIL")
    check("pillow installed", ok, f"v{v}" if ok else v)
    ok, v = try_import("PyInstaller")
    check("pyinstaller installed (rebuild only)", ok, f"v{v}" if ok else v)

    sys.path.insert(0, ROOT)
    for mod in ("emu.bus", "emu.cpu", "emu.panl", "emu.fw", "emu.ui", "emu.main"):
        ok, detail = try_import(mod)
        check(f"import {mod}", ok, "" if ok else detail)

    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from emu import fw as F
        flash, info = F.load_flash(None)
        check("firmware found", flash is not None,
              info.get("source", info.get("error", "?")) or "")
    except Exception as e:
        check("firmware found", False, f"{type(e).__name__}: {e}")

    app = os.path.join(ROOT, "dist", "XDJ-700 Emulator.app")
    exe = os.path.join(app, "Contents", "MacOS", "XDJ-700 Emulator")
    check("app bundle exists", os.path.isdir(app), app if os.path.isdir(app) else "missing: rebuild (see emu/README.md)")
    if os.path.isfile(exe):
        try:
            r = subprocess.run(["codesign", "--verify", "--deep", "--strict", app],
                               capture_output=True, text=True, timeout=120)
            check("app signature valid", r.returncode == 0,
                  "ok" if r.returncode == 0 else (r.stderr.strip() or r.stdout.strip()))
        except Exception as e:
            check("app signature valid", False, str(e))
        try:
            r = subprocess.run(["xattr", "-p", "com.apple.quarantine", app],
                               capture_output=True, text=True, timeout=30)
            check("no Gatekeeper quarantine flag", r.returncode != 0,
                  "quarantined: run xattr -cr on the .app" if r.returncode == 0 else "clean")
        except Exception as e:
            check("no Gatekeeper quarantine flag", False, str(e))
    print()
    nfail = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results) - nfail}/{len(results)} checks pass")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
