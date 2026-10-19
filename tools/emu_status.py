#!/usr/bin/env python3
"""emu_status.py -- read-only phone dashboard for the XDJ-700 session.

Serves project status + result images (RGB waveforms, emulator tabs,
deck screenshots) on the LAN. No commands, no writes, no deck traffic.

Usage: .venv/bin/python tools/emu_status.py [port]   (default 4096)
"""
import http.server
import os
import socketserver
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMGS = {
    "rgb_drop": "/tmp/rgb_drop.png",
    "rgb_full0": "/tmp/rgb_full0.png",
    "rgb_full1": "/tmp/rgb_full1.png",
    "rgb_test": "/tmp/rgb_test.png",
    "wave_tab": "/tmp/stems_tab.png",
    "stems_tab": "/tmp/stems_tab.png",
    "deck_main": "/tmp/emufb2.png",
    "deck_browse": "/tmp/emufb_browseusb.png",
}

STATUS = """<h1>XDJ-700 mods</h1>
<h2>Deck link</h2><p id="link">resting (paced reads only)</p>
<h2>Done &amp; proven</h2><ul>
<li>PERFORM pads (BEAT JUMP / HOT CUE / KEY SHIFT / STEMS) deck-tested</li>
<li>Touch injection flashes Vinyl live (306-volley runs)</li>
<li>USB mounts in emulation (reads + writes, zero errors)</li>
<li>RGB pipeline: parser 2/2, renderer exact, your tracks in color</li>
<li>Stems mixer 4/4 exact on emulated chip</li>
<li>Decompiler built, framework mapped</li></ul>
<h2>Queued</h2><ul>
<li>DMA1 upper channels (audio handoff hunt)</li>
<li>Zone map (4 mockup spots + hot-cue re-capture)</li>
<li>Ghost-tap v3 on fresh coords</li>
<li>UI-task wake (select/load/playback in emulation)</li></ul>
<h2>Pictures</h2><ul>
<li><a href="/img/rgb_drop">RGB drop detail (your track)</a></li>
<li><a href="/img/rgb_full0">Full track 1</a></li>
<li><a href="/img/rgb_full1">Full track 2</a></li>
<li><a href="/img/wave_tab">Emulator RGB Wave tab</a></li>
<li><a href="/img/stems_tab">Emulator STEMS tab</a></li>
<li><a href="/img/deck_main">Emulated deck main screen</a></li>
<li><a href="/img/deck_browse">Emulated browse + stick</a></li></ul>
<p>Read-only. Refresh for updates.</p>"""


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/img/"):
            p = IMGS.get(self.path[5:])
            if p and os.path.exists(p):
                body = open(p, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()
            return
        body = ("<html><meta name=viewport content='width=device-width'>"
                + STATUS + "</html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4096
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", port), H) as srv:
        print(f"serving on 0.0.0.0:{port}", flush=True)
        srv.serve_forever()
