#!/usr/bin/env python3
"""public_app_test.py [APP] [UPD] -- the PUBLIC Mod Studio works for a new
user who brings their own firmware.

Runs the packaged server of dist/public/XDJ Mod Studio.app with an EMPTY
HOME (no firmware home, no settings), then:
  /api/state says fw_ready = false;
  /api/firmware/setup with the official XDJ-700 v1.15 update file -> the
  first-run job finishes (progress reported) -> fw_ready = true;
  a WRONG file is refused;
  the emulator starts and streams frames; mods apply without error.
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

APP = sys.argv[1] if len(sys.argv) > 1 else "dist/public/XDJ Mod Studio.app"
UPD = sys.argv[2] if len(sys.argv) > 2 else "FIRMWARE/XDJ700_stock_v1.15.UPD"
PORT = 8798
U = f"http://127.0.0.1:{PORT}"


def get(p, t=10):
    with urllib.request.urlopen(U + p, timeout=t) as r:
        return r.read()


def post(p, body, t=60):
    req = urllib.request.Request(U + p, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read())


def wait_job(jid, limit=900):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = [x for x in json.loads(get("/api/jobs")) if x["id"] == jid][0]
        if j["state"] != "running":
            return j
        time.sleep(5)
    return j


def main():
    home = tempfile.mkdtemp()
    srv = os.path.join(os.path.abspath(APP), "Contents", "Resources", "server",
                       "XDJ Mod Studio")
    log = open(os.path.join(home, "srv.log"), "w")
    p = subprocess.Popen([srv, "--no-browser", "--port", str(PORT)], cwd=home,
                         env=dict(os.environ, HOME=home), stdout=log,
                         stderr=subprocess.STDOUT)
    ok = True

    def chk(label, cond):
        nonlocal ok
        print(("  ok   " if cond else "  FAIL ") + label, flush=True)
        ok &= bool(cond)
    try:
        for _ in range(60):
            try:
                st = json.loads(get("/api/state"))
                break
            except Exception:
                time.sleep(1)
        chk("fresh user: firmware not set up", st.get("fw_ready") is False)
        bad = os.path.join(home, "not_firmware.UPD")
        open(bad, "wb").write(b"\0" * 4096)
        j = wait_job(post("/api/firmware/setup", {"path": bad})["job"], 60)
        chk("wrong file refused", j["state"] == "failed")
        t0 = time.time()
        jid = post("/api/firmware/setup", {"path": os.path.abspath(UPD)})["job"]
        notes = set()
        while True:
            j = [x for x in json.loads(get("/api/jobs")) if x["id"] == jid][0]
            notes.add(j.get("note"))
            if j["state"] != "running" or time.time() - t0 > 900:
                break
            time.sleep(5)
        print(f"  setup took {time.time() - t0:.0f} s; steps: {sorted(n for n in notes if n)}")
        if j["state"] != "done":
            print("  job error:", (j.get("error") or "")[:500])
        chk("firmware setup job done", j["state"] == "done")
        st = json.loads(get("/api/state"))
        chk("fw_ready after setup", st.get("fw_ready") is True)
        fwdir = os.path.join(home, "Library", "Application Support",
                             "XDJ Mod Studio", "firmware")
        chk("firmware home in the user's own Library",
            os.path.exists(os.path.join(fwdir, "mainscreen.pkl.gz")))
        post("/api/emu/cmd", {"cmd": "start"})
        t1 = time.time()
        while time.time() - t1 < 180:
            es = json.loads(get("/api/emu/state"))
            if es.get("state") == "running" or es.get("error"):
                break
            time.sleep(2)
        chk(f"emulator running ({es.get('state')}, err {es.get('error')})",
            es.get("state") == "running" and not es.get("error"))
        r = post("/api/emu/cmd", {"cmd": "mods", "wave": "rgb"})
        time.sleep(3)
        es = json.loads(get("/api/emu/state"))
        chk("mods apply", r.get("ok") and not es.get("error"))
        chk("theme preview renders", len(get("/api/preview?theme=hot-pink&screen=main", 60)) > 1000)
        chk("server alive", p.poll() is None)
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
            print(open(os.path.join(home, "srv.log")).read()[-3000:])
        shutil.rmtree(home, ignore_errors=True)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
