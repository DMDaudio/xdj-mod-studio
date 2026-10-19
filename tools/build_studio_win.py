#!/usr/bin/env python3
"""Build the PUBLIC Windows app: dist/win/XDJ Mod Studio/ + a zip.

Runs on Windows (GitHub Actions windows-latest, .github/workflows/
windows.yml). Same rules as `PUBLIC=1 tools/build_studio.sh` on the Mac:
no firmware and nothing derived from it -- the user adds their own
official XDJ-700 v1.15 update on first run (emu/fwhome.py).

Inputs (made by the CI's Linux job, all our own code or open-source tools):
  build/win-natives/_fastcore.dll   emu/fastcore.c   (MinGW-w64)
  build/win-natives/_c6xcore.dll    emu/c6x/c6xcore.c
  build/win-natives/objdump.exe     GNU binutils, --target=tic6x-elf
  build/win-natives/hidapi.dll      libusb/hidapi release (deck USB)
  prebuilt/deck/<mod>/*.bin|.syms   our SH-4 / C674x deck code, built on
                                    the Mac (tools/make_public_repo.sh)
Output layout (the app finds repo/ next to its exe, studio/app.py):
  XDJ Mod Studio/XDJ Mod Studio.exe, _internal/, repo/, READ ME.txt
"""
import compileall
import glob
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAT = os.path.join(ROOT, "build", "win-natives")
DIST = os.path.join(ROOT, "dist", "win")
APP = os.path.join(DIST, "XDJ Mod Studio")
VERSION = os.environ.get("XDJ_VERSION", "dev")

README = """XDJ Mod Studio for Windows ({ver})
=================================

Start: double-click "XDJ Mod Studio.exe". A black window opens (that is
the app's engine - leave it open; closing it quits the app) and the app
itself opens in Microsoft Edge or Chrome as its own window. If Windows
SmartScreen warns about an unknown publisher: More info -> Run anyway
(the app is not code-signed).

First run: Home -> Firmware: Add -> pick your own official XDJ-700 v1.15
update file (XDJ700.UPD from Pioneer DJ's support site). The app checks
it and builds what it needs (about 5-10 minutes, once).

What is tested on Windows: the app starts, its pages load and its
built-in checks pass on a clean Windows machine (automated). Installing
mods to a real deck over USB has NOT been tried on Windows yet - on a Mac
it is hardware-proven. If you try it, please report what happens.

Not on Windows yet: emulator sound (the emulator runs silent).
Track Builder needs ffmpeg (and rubberband for key versions) on PATH.

Your settings and firmware files live in
%LOCALAPPDATA%\\XDJ Mod Studio. Deleting that folder resets the app.

Everything here goes in the deck's memory: switch the deck off and on and
it is stock again. Not affiliated with Pioneer DJ / AlphaTheta.
"""


def need(path):
    if not os.path.exists(path):
        sys.exit(f"missing build input: {path}")
    return path


def main():
    for n in ("_fastcore.dll", "_c6xcore.dll", "objdump.exe", "hidapi.dll"):
        need(os.path.join(NAT, n))
    need(os.path.join(ROOT, "prebuilt", "deck"))
    os.environ["PUBLIC"] = "1"
    os.environ["XDJ_WIN_NATIVES"] = NAT
    # icon from our own artwork
    try:
        from PIL import Image
        Image.open(os.path.join(ROOT, "emu", "icon_1024.png")).save(
            os.path.join(NAT, "icon.ico"),
            sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception as e:
        print("icon skipped:", e)
    shutil.rmtree(DIST, ignore_errors=True)
    subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm",
                           "XDJStudio.spec", "--distpath", DIST,
                           "--workpath", os.path.join(ROOT, "build", "studio-win")],
                          cwd=ROOT)
    assert os.path.exists(os.path.join(APP, "XDJ Mod Studio.exe"))

    # third-party test data (numpy ships .npy/.pkl fixtures): not needed,
    # and it would trip the firmware-like file scan below
    for dp, ds, _ in os.walk(os.path.join(APP, "_internal")):
        for d in list(ds):
            if d == "tests":
                shutil.rmtree(os.path.join(dp, d))
                ds.remove(d)

    # portable repo copy (tools run from here, --run-tool)
    repo = os.path.join(APP, "repo")
    for d in ("tools", "mods", "emu", "studio"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(repo, d),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store",
                                                      "*.bak", "*.BACKUP", "*.pre-*",
                                                      "*.removed", "icon.iconset"))
    os.makedirs(os.path.join(repo, "cache"), exist_ok=True)
    shutil.copytree(os.path.join(ROOT, "prebuilt", "deck"), os.path.join(repo, "build"))
    # theme-switch layout (tables come from the user's firmware, fwhome.py)
    with open(os.path.join(repo, "build", "themesw", "layout.txt"), "w") as f:
        f.write("0x9770000 0x9776000\n")
    os.makedirs(os.path.join(repo, "work", "tic6x"))
    shutil.copy(os.path.join(NAT, "objdump.exe"), os.path.join(repo, "work", "tic6x"))
    shutil.copy(os.path.join(NAT, "_c6xcore.dll"), os.path.join(repo, "emu", "c6x"))
    shutil.copy(os.path.join(NAT, "_fastcore.dll"), os.path.join(repo, "emu"))
    # sourceless, like the Mac app (mods/themes.py stays: the Theme Builder
    # appends user themes to it)
    compileall.compile_dir(repo, quiet=1, legacy=True)
    keep_py = os.path.join(repo, "mods", "themes.py")
    for p in glob.glob(os.path.join(repo, "**", "*.py"), recursive=True):
        if os.path.normcase(p) != os.path.normcase(keep_py):
            os.remove(p)
    tp = os.path.join(repo, "mods", "themes.pyc")
    if os.path.exists(tp):
        os.remove(tp)
    for pat in ("*.h", "*.swift", "*.sh", "*.spec", "*.md", "*.S", "*.c"):
        for p in glob.glob(os.path.join(repo, "**", pat), recursive=True):
            if os.path.basename(p) != "c6xcore.c":     # cfast reads its OP list
                os.remove(p)
    # public build: no firmware pixels / brand logos
    for pat in ("tools/data/*.npy", "tools/data/*.npz", "studio/assets/screens.npz",
                "emu/assets/pioneer_dj_white.png", "emu/assets/rekordbox_white.png"):
        for p in glob.glob(os.path.join(repo, pat)):
            os.remove(p)
    with open(os.path.join(APP, "READ ME.txt"), "w", newline="\r\n") as f:
        f.write(README.format(ver=VERSION))

    # safety scan, like make_public_repo.sh: refuse on firmware-like files
    bad = []
    for dp, _, fs in os.walk(APP):
        for n in fs:
            low = n.lower()
            if low.endswith((".upd", ".img", ".pkl", ".pkl.gz", ".npz", ".npy")):
                bad.append(os.path.join(dp, n))
    if bad:
        sys.exit("REFUSED: firmware-like files in the Windows app:\n" + "\n".join(bad))

    zp = os.path.join(DIST, "XDJ-Mod-Studio-Windows.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, _, fs in os.walk(APP):
            for n in fs:
                full = os.path.join(dp, n)
                z.write(full, os.path.relpath(full, DIST))
    print(f"built {zp} ({os.path.getsize(zp) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
