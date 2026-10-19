#!/usr/bin/env python3
"""tester_pack_test.py [zip] -- does the tester pack actually work?

Unzips dist/XDJ700-noob.zip into an empty folder, runs the PACKAGED
server with an empty HOME (no saved settings, like a fresh tester Mac),
then: UI loads, /api/state healthy and pointing at the BUNDLED files,
emulator starts and reaches "running", a real screen frame streams
(saved to screenshots/tester_pack_frame.jpg), Browse press changes the
screen, waveform mods apply without error. Kills the server at the end.
(Earlier packs were only checked up to /api/state -- the emulator
start crashed on SDL3, 2026-10-11. Never ship without this PASS.)
"""
import os as _os
_os.environ.setdefault("XDJ_DECK_SOUND", "0")   # gates run silent (no stutter through the speakers)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ZIP = sys.argv[1] if len(sys.argv) > 1 else "dist/XDJ700-noob.zip"
PORT = 8799
U = f"http://127.0.0.1:{PORT}"


def get(path, timeout=10):
    with urllib.request.urlopen(U + path, timeout=timeout) as r:
        return r.read()


def post(path, body, timeout=60):
    req = urllib.request.Request(U + path, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def frame():
    """one JPEG from the multipart emulator stream"""
    with urllib.request.urlopen(U + "/api/emu/stream", timeout=20) as r:
        buf = b""
        while True:
            buf += r.read(65536)
            a = buf.find(b"\xff\xd8")
            b = buf.find(b"\xff\xd9", a + 2)
            if a >= 0 and b > a:
                return buf[a:b + 2]


def main():
    zp = os.path.abspath(ZIP)
    tmp, home = tempfile.mkdtemp(), tempfile.mkdtemp()
    subprocess.run(["unzip", "-q", zp, "-d", tmp], check=True)
    srv = os.path.join(tmp, "XDJ Mod Studio.app", "Contents", "Resources",
                       "server", "XDJ Mod Studio")
    env = dict(os.environ, HOME=home)
    log = open(os.path.join(tmp, "srv.log"), "w")
    p = subprocess.Popen([srv, "--no-browser", "--port", str(PORT)], cwd=tmp,
                         env=env, stdout=log, stderr=subprocess.STDOUT)
    ok = True
    try:
        for _ in range(60):
            try:
                st = json.loads(get("/api/state"))
                break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("server never answered")
        bundled = os.path.realpath(st.get("project", "")).startswith(os.path.realpath(tmp))
        print("server up; project = bundled copy:", bundled)
        ok &= bundled and st.get("project_ok")
        ok &= b"<html" in get("/").lower()
        post("/api/emu/cmd", {"cmd": "start"})
        t0 = time.time()
        while time.time() - t0 < 180:
            es = json.loads(get("/api/emu/state"))
            if es.get("state") == "running" or es.get("error"):
                break
            time.sleep(2)
        print("emulator:", es.get("state"), "error:", es.get("error"),
              f"({time.time() - t0:.0f}s)")
        ok &= es.get("state") == "running" and not es.get("error")
        time.sleep(4)
        f1 = frame()
        os.makedirs("screenshots", exist_ok=True)
        open("screenshots/tester_pack_frame.jpg", "wb").write(f1)
        post("/api/emu/input", {"events": [{"t": "key", "k": "b", "d": 1}]})
        time.sleep(0.3)
        post("/api/emu/input", {"events": [{"t": "key", "k": "b", "d": 0}]})
        time.sleep(4)
        f2 = frame()
        open("screenshots/tester_pack_frame_browse.jpg", "wb").write(f2)
        changed = f1 != f2
        print("frame:", len(f1), "bytes; after Browse key changed:", changed)
        ok &= len(f1) > 10000 and changed
        r = post("/api/emu/cmd", {"cmd": "mods", "wave": "rgb"})
        time.sleep(3)
        es = json.loads(get("/api/emu/state"))
        print("mods:", r, "state:", es.get("state"), "error:", es.get("error"))
        ok &= r.get("ok") and not es.get("error")
        alive = p.poll() is None
        print("server still alive:", alive)
        ok &= alive
    except Exception as e:
        print("ERROR:", type(e).__name__, e)
        ok = False
    finally:
        p.terminate()
        try:
            p.wait(10)
        except Exception:
            p.kill()
        if not ok:
            print("--- server log tail ---")
            print(open(os.path.join(tmp, "srv.log")).read()[-3000:])
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
