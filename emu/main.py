#!/usr/bin/env python3
"""XDJ-700 emulator for macOS.

  .venv/bin/python -m emu.main [--upd XDJ700.UPD] [--mock] [--ckpt FILE]

Modes:
  firmware  load MAIN flash into Machine (real SH-4 boot) in a background
            thread while the XDJ-700 UI runs (live framebuffer when drawn).
            Resumes --ckpt if it exists, else boots fresh.
  mock      no firmware: UI simulation only.

Controls: Space play, C cue, arrows jog/nudge, I/O loop, V vinyl,
  B browse, F firmware-fb toggle, +/- tempo.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--upd", default=None, help="path to XDJ700.UPD")
    ap.add_argument("--mock", action="store_true", help="skip firmware load")
    from .paths import repo_root
    from . import fwhome
    _ck = fwhome.path("mainscreen.pkl.gz")     # user's firmware home or dev
    ap.add_argument("--ckpt", default=_ck,
                    help="checkpoint file (resume if present, save on exit)")
    ap.add_argument("--save-on-exit", action="store_true",
                    help="write the session back to --ckpt when closing "
                         "(default: keep the checkpoint as a clean snapshot)")
    ap.add_argument("--check", action="store_true",
                    help="headless self-test (no window): boot a slice, "
                         "validate a PANL frame, exit 0 on success")
    a = ap.parse_args()

    machine = None
    fw_info = {}
    if not a.mock:
        from .machine import Machine
        from . import fw as F
        if a.ckpt and os.path.exists(a.ckpt):
            print(f"resuming {a.ckpt}")
            machine = Machine.load(a.ckpt)
        else:
            flash, info = F.load_flash(a.upd)
            fw_info = info
            if flash is None:
                print(f"no firmware: {info.get('error')}; mock UI only")
            else:
                print(f"loaded {info.get('source')} ({len(flash):,} B)")
                machine = Machine(flash)
                print("  reset vector -> 0xA0000000 (background thread boots)")
    else:
        print("mock mode: no firmware")

    if a.check:
        from . import panl as P
        if machine is not None:
            res, _ = machine.run(limit=50000)
            print(f"  machine: {res} steps={machine.cpu.steps:,} "
                  f"core={type(machine.cpu).__name__}")
        f = P.build_frame(["PlayPause", "Quantize"], fader_raw=250)
        assert P.validate(f), "PANL self-check failed"
        print("  panl: frame builds + validates")
        print("CHECK OK")
        return

    from .ui import EmuApp
    resumed = machine is not None and a.ckpt and os.path.exists(a.ckpt)
    app = EmuApp(machine=machine, fw_info=fw_info,
                 ckpt=a.ckpt if resumed else None)
    try:
        app.run()
    finally:
        # autosave only the session resumed from the checkpoint; a mod
        # booted from the firmware menu never overwrites it
        if app.machine is not None and app.ckpt and a.save_on_exit:
            print(f"saving {app.ckpt}")
            app.machine.save(app.ckpt)


if __name__ == "__main__":
    main()
