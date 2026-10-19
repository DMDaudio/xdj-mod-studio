#!/usr/bin/env python3
"""app_usb_probe.py -- time the USER's USB experience through the real
app front-end (EmuApp headless: same realtime pacing, bursts and
XDJ_USB_FAST the served app runs).

Phases timed (wall clock):
  boot    snapshot restore to state==running
  mount   insert_usb(his stick) -> disk ops plateau (FAT walk done)
  browse  DevUSB press -> ops plateau (DB read + list)
  load    rotary push -> deck mode 3 with a position (track loaded)

Env: STICK (default his library), NOLOAD=1 to stop after browse.
"""
import os
import sys
import time

os.environ.setdefault("XDJ_USB_FAST", "1")
sys.path[:0] = [".", "tools"]

STICK = os.environ.get("STICK", os.path.expanduser("~/Downloads/Rekordbox stuff"))
MODE = 0x0B6C4264
SH_POS = 0x09D246E0


def plateau(fn, stable=4.0, idle=600.0, log=1.0):
    """Wait until fn() stops changing for `stable` seconds."""
    t0 = time.time()
    last_v, last_t, last_log = fn(), t0, t0
    while True:
        time.sleep(0.25)
        v, now = fn(), time.time()
        if v != last_v:
            last_v, last_t = v, now
        if now - last_log >= log:
            last_log = now
            print(f"    [{now-t0:6.1f}s] {v}", flush=True)
        if now - last_t >= stable:
            return now - t0
        if now - t0 > idle:
            return now - t0


def main():
    import threading
    import numpy as np
    from PIL import Image
    from emu import ui as emu_ui
    from emu.choices import firmware_choices
    from ghosttap_emu import fb_png
    t0 = time.time()
    app = emu_ui.EmuApp(headless=True)
    label, kind, path = firmware_choices()[0]
    print(f"booting {label} ({kind})", flush=True)
    app._boot(label, kind, path)
    while time.time() - t0 < 180:
        if app.info().get("state") == "running":
            break
        time.sleep(0.5)
    print(f"BOOT {time.time()-t0:.1f}s", flush=True)

    # the hosted app's render loop calls tick() 30x/s -- that is what
    # carries st.held into the deck's panel. Replicate it or NO input
    # reaches the firmware (learned the hard way: byte-identical screens).
    stop = [False]

    def ticker():
        while not stop[0]:
            app.tick(1 / 30.0)
            time.sleep(1 / 30.0)
    threading.Thread(target=ticker, daemon=True).start()

    def red(name):
        p = f"screenshots/usbprobe_{name}.png"
        fb_png(app.machine, p)
        a = np.array(Image.open(p).convert("RGB")).astype(int)
        r = a[398:428, 170:600]
        return int(((r[..., 0] > 180) & (r[..., 1] < 90)
                    & (r[..., 2] < 90)).sum())

    def dev():
        m = app.machine
        d = getattr(m.mem.dev, "usb_disk", None) if m else None
        ops = getattr(d, "ops", None) if d else None
        if ops is None:
            ops = len(getattr(d, "access", [])) if d else 0
        return ops, m.cpu.steps if m else 0

    def press(k, hold=0.25):
        # the served app reaches the deck via key_event/pointer_down, which
        # open the input burst; st.held bypasses those, so fire it here too
        app._input_burst()
        app.st.held.add(k)
        time.sleep(hold)
        app._input_burst()
        app.st.held.discard(k)

    t_ins = time.time()
    app.insert_usb(STICK)
    print(f"MOUNT insert at {t_ins-t0:.1f}s; waiting for FAT walk plateau",
          flush=True)
    dt = plateau(lambda: dev()[0])
    m = app.machine
    print(f"MOUNT DONE {dt:.1f}s (total {time.time()-t0:.1f}s, "
          f"steps {m.cpu.steps/1e9:.2f}G)", flush=True)

    press("DevUSB", 0.4)
    time.sleep(2)
    print(f"BROWSE DevUSB pressed red={red('browse')} "
          f"ops={dev()[0]}", flush=True)
    dt = plateau(lambda: dev()[0])
    print(f"BROWSE DONE {dt:.1f}s (total {time.time()-t0:.1f}s, "
          f"red={red('browse2')})", flush=True)
    if os.environ.get("NOLOAD"):
        stop[0] = True
        return

    for i in range(1, 12):
        t_push = time.time()
        press("RotaryPush", 0.2)
        t_load = time.time()
        m = app.machine
        mode0 = m.mem.r8(MODE)
        burst = app.bursting()
        r = red(f"push{i}")
        print(f"PUSH {i} mode={mode0} red={r} burst={burst}", flush=True)
        last_log = t_load
        while time.time() - t_load < 60:
            time.sleep(0.5)
            m = app.machine
            mode = m.mem.r8(MODE)
            pos = m.mem.r32(SH_POS)
            ops = dev()[0]
            now = time.time()
            if now - last_log >= 10:
                last_log = now
                print(f"    [{now-t0:6.1f}s] mode={mode} pos={pos} "
                      f"ops={ops} burst={app.bursting()}", flush=True)
            if mode == 3 and pos != 0xFFFFFFFF:
                print(f"LOAD DONE push={i} {now-t_push:.1f}s "
                      f"(total {now-t0:.1f}s)", flush=True)
                if os.environ.get("PLAY"):
                    app.st.held.add("PlayPause")
                    time.sleep(0.3)
                    app._input_burst()
                    app.st.held.discard("PlayPause")
                    t_p = time.time()
                    snd = app.info().get("sound") or {}
                    while time.time() - t_p < 45:
                        time.sleep(1)
                        m = app.machine
                        d = getattr(m.mem.dev, "dspemu", None)
                        dac = len(d.asp1.out) if d else -1
                        snd = app.info().get("sound") or {}
                        if int(time.time() - t_p) % 5 == 0:
                            print(f"    play[{time.time()-t_p:4.0f}s] "
                                  f"mode={m.mem.r8(MODE)} dac={dac} "
                                  f"snd={snd.get('playing')}", flush=True)
                        if m.mem.r8(MODE) == 5 and dac > 0:
                            time.sleep(10)
                            d2 = m.mem.dev.dspemu
                            print(f"PLAY OK mode=5 dac {len(d2.asp1.out)} "
                                  f"snd={snd.get('playing')} "
                                  f"muted={snd.get('muted')}", flush=True)
                            break
                    else:
                        print("PLAY never reached mode 5", flush=True)
                stop[0] = True
                return
        print(f"    push {i}: no load in 60 s", flush=True)
    print("LOAD TIMEOUT (no push loaded)", flush=True)
    stop[0] = True


if __name__ == "__main__":
    main()
