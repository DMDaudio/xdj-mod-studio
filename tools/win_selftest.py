#!/usr/bin/env python3
"""Built-in checks for a shipped app (run by CI on the Windows build):
    "XDJ Mod Studio.exe" --run-tool tools/win_selftest.py
Needs no firmware and no deck. Exit code 0 = every check passed."""
import os
import sys
import tempfile
import traceback

# run from the project folder (the app's repo/ copy) whatever the cwd
os.chdir(os.environ.get("XDJ_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [".", "tools"]
FAILS = []


def check(name):
    def wrap(fn):
        try:
            msg = fn()
            print(f"PASS {name}" + (f": {msg}" if msg else ""), flush=True)
        except Exception:
            FAILS.append(name)
            print(f"FAIL {name}\n" + traceback.format_exc(), flush=True)
        return fn
    return wrap


@check("SH-4 emulator core (C library)")
def _():
    from emu import fastcpu
    lib = fastcpu.load_lib()
    assert lib is not None, "fastcore library did not load"
    return os.path.basename(fastcpu.LIB)


@check("audio-chip (C674x) core (C library)")
def _():
    from emu.c6x import cfast
    lib = cfast.load_lib()
    assert lib.c6x_sizeof_ins() > 0
    return f"{len(cfast.OPS)} ops"


@check("audio-chip decoder (tic6x objdump)")
def _():
    from emu.c6x import decode
    # 8 x NOP (0x00000000) in one fetch packet
    res = decode.decode_bytes(bytes(32), 0)
    assert len(res) >= 1, "objdump returned nothing"
    return f"{len(res)} insns via {os.path.basename(decode.OBJDUMP)}"


@check("deck USB library (hidapi)")
def _():
    import hid
    n = len(hid.enumerate())
    return f"{n} HID devices visible"


@check("deck shell lock + pacing files")
def _():
    d = tempfile.mkdtemp()
    os.environ["XDJ_SHELL_LOCK"] = os.path.join(d, "lock")
    os.environ["XDJ_SHELL_STAMP"] = os.path.join(d, "stamp")
    import importlib
    import xdj700_usb_shell as S
    importlib.reload(S)
    S._lock()
    S._stamp()
    assert os.path.exists(os.environ["XDJ_SHELL_STAMP"])


@check("PERFORM pad fonts")
def _():
    import aafont
    a = aafont._render("A", aafont.NEUE, 10, 14)
    assert a.shape[0] == 14 and a.max() > 0.5
    return aafont._font_file(aafont.NEUE, 10)[0]


@check("prebuilt deck code")
def _():
    outs = ["build/hotcue8/hc.bin", "build/stems/sm.bin", "build/xflac/vfile.bin",
            "build/themesw/ts.bin", "build/themesw/ts.syms", "build/centre/cw.bin",
            "build/keyshift/ksh.bin", "build/phasemeter/pm.bin"]
    missing = [o for o in outs if not os.path.exists(o)]
    assert not missing, f"missing {missing}"
    return f"{len(outs)} modules"


@check("deck installer plans (no deck needed)")
def _():
    import rgb_overview as RO
    import perform_pads as PP          # noqa: F401  (assembles with shasm)
    import mysettings as MS            # noqa: F401
    return f"overview mode byte @ {RO.MODE_ADDR:#x}"


@check("firmware home")
def _():
    from emu import fwhome
    return fwhome.home()


print("RESULT:", "PASS" if not FAILS else "FAIL " + ", ".join(FAILS), flush=True)
sys.exit(1 if FAILS else 0)
