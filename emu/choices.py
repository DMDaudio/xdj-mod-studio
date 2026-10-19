"""Firmware / theme choices for the emulator (no pygame needed)."""
import glob
import os
import sys

from .paths import repo_root


def theme_names():
    root = repo_root()
    if os.path.join(root, "mods") not in sys.path:
        sys.path.insert(0, os.path.join(root, "mods"))
    import themes as T
    return list(T.THEMES)


def firmware_choices():
    """(label, kind, path): the stock snapshot, then the shippable
    XDJ700*.UPD builds only. Dev probe/recovery/test builds
    (*probe*, RECOVER_*, TEST_*, PURPLE_SKIN) are hidden from the UI."""
    root = repo_root()
    out = []
    from . import fwhome
    # Real-DSP snapshot first (the default boot): the modelled C6x DSP runs
    # Pioneer's own DSP program, so tracks LOAD and PLAY with real audio
    # (E-8302 gone). Slower to restore than the plain snapshot; same stick
    # flow.
    dsp = fwhome.path("dspsound.pkl.gz")
    if dsp and os.path.exists(dsp):
        out.append(("Stock v1.15 (real audio DSP)", "ckpt-dsp", dsp))
    snap = fwhome.path("mainscreen.pkl.gz")    # user's firmware home or dev
    for p in (snap,):
        if os.path.exists(p):
            out.append(("Stock v1.15 (instant, no playback)", "ckpt", p))
            break
    out.append(("Stock v1.15 (boot from reset)", "upd", None))
    for p in sorted(glob.glob(os.path.join(root, "build", "XDJ700*.UPD"))):
        out.append((os.path.basename(p)[:-4], "upd", p))
    return out
