#!/usr/bin/env python3
"""app_watch.py -- run the PACKAGED app's server with the user's real
HOME and watch the deck: state polls + a frame grab every N seconds.
Hunts the 'rekordbox Database not found!' screen the user saw.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
SECS = int(sys.argv[2]) if len(sys.argv) > 2 else 150
APP = sys.argv[3] if len(sys.argv) > 3 else "dist/public/XDJ Mod Studio.app"
U = f"http://127.0.0.1:{PORT}"

srv = os.path.abspath(os.path.join(APP, "Contents/Resources/server/XDJ Mod Studio"))
log = open("build/app_watch_srv.log", "w")
p = subprocess.Popen([srv, "--no-browser", "--port", str(PORT)],
                     env=dict(os.environ), stdout=log, stderr=subprocess.STDOUT)
print("server pid", p.pid, flush=True)


def get(path, t=8):
    with urllib.request.urlopen(U + path, timeout=t) as r:
        return r.read()


def state():
    try:
        return json.loads(get("/api/emu/state"))
    except Exception as e:
        return {"err": str(e)}


def frame(name):
    """First JPEG from the MJPEG stream."""
    try:
        with urllib.request.urlopen(U + "/api/emu/stream?w=800", timeout=8) as r:
            buf = b""
            t0 = time.time()
            while time.time() - t0 < 6:
                chunk = r.read(65536)
                if not chunk:
                    break
                buf += chunk
                i = buf.find(b"\xff\xd8")
                j = buf.find(b"\xff\xd9", i + 2) if i != -1 else -1
                if i != -1 and j != -1:
                    open(f"screenshots/watch_{name}.jpg", "wb").write(
                        buf[i:j + 2])
                    return True
    except Exception as e:
        print("frame err", e, flush=True)
    return False


t0 = time.time()
last = None
n = 0
try:
    while time.time() - t0 < SECS:
        s = state()
        now = time.time()
        key = (s.get("state"), s.get("usb"), s.get("status"),
               s.get("firmware_id"))
        if key != last:
            print(f"[{now-t0:6.1f}s] {key} sound={bool(s.get('sound'))}",
                  flush=True)
            last = key
        n += 1
        frame(f"{n:03d}")
        time.sleep(2)
finally:
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    print("server stopped", flush=True)
