#!/usr/bin/env python3
"""app_drive.py -- drive the PACKAGED app like the browser UI does
(pointer events at deck coordinates), load a track, press PLAY, and
report exactly what the sound status (the app's sound pill) says --
hunting 'says not playing while the deck plays'.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8793
APP = sys.argv[2] if len(sys.argv) > 2 else "dist/public/XDJ Mod Studio.app"
U = f"http://127.0.0.1:{PORT}"

# deck image geometry (emu/deck.py runtime values)
DECK_W, DECK_H = 1368, 1614
SCR_X, SCR_Y = 284, 110
ROT = (1208, 317)
PLAY = (182, 1462)

srv = os.path.abspath(os.path.join(APP, "Contents/Resources/server/XDJ Mod Studio"))
log = open("build/app_drive_srv.log", "w")
p = subprocess.Popen([srv, "--no-browser", "--port", str(PORT)],
                     env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
print("server pid", p.pid, flush=True)


def get(path, t=8):
    with urllib.request.urlopen(U + path, timeout=t) as r:
        return r.read()


def post(path, body, t=15):
    req = urllib.request.Request(U + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read())


def state():
    try:
        return json.loads(get("/api/emu/state"))
    except Exception as e:
        return {"err": str(e)}


def tap(dx, dy, hold=0.15):
    x, y = dx / DECK_W, dy / DECK_H
    post("/api/emu/input", {"events": [
        {"t": "down", "x": x, "y": y}, {"t": "release"}]})


def tap_devusb():
    tap(SCR_X + 43 / SCR_W * SCR_W + 0, SCR_Y + 278)   # screen-tap mapping


def snd(s):
    return s.get("sound") or {}


t0 = time.time()
while time.time() - t0 < 120:
    s = state()
    if s.get("state") == "running":
        break
    time.sleep(1)
print(f"[{time.time()-t0:5.1f}s] running; waiting for stick", flush=True)
while time.time() - t0 < 180:
    s = state()
    if s.get("usb"):
        break
    time.sleep(1)
print(f"[{time.time()-t0:5.1f}s] usb={s.get('usb')} "
      f"snd={snd(s).get('on')}", flush=True)
time.sleep(10)

# open browse via a tap on the on-screen USB/source button (screen px 43,278)
tap(SCR_X + 43, SCR_Y + 278)
time.sleep(8)
for i in range(1, 9):
    tap(*ROT)
    print(f"[{time.time()-t0:5.1f}s] push {i} snd={snd(state())}",
          flush=True)
    time.sleep(12)
    s = state()
    if snd(s).get("playing") or snd(s).get("on"):
        pass
# press PLAY
tap(*PLAY)
print(f"[{time.time()-t0:5.1f}s] PLAY pressed", flush=True)
for k in range(12):
    time.sleep(5)
    s = state()
    so = snd(s)
    print(f"[{time.time()-t0:5.1f}s] sound: on={so.get('on')} "
          f"playing={so.get('playing')} muted={so.get('muted')} "
          f"status={so.get('status')!r} served={so.get('served_frames')}",
          flush=True)
try:
    data = get("/api/emu/stream?w=800", t=8)
except Exception:
    pass
p.terminate()
try:
    p.wait(timeout=5)
except Exception:
    p.kill()
print("done", flush=True)
