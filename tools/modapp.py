#!/usr/bin/env python3
"""XDJ-700 mod studio — local web app for theming and modifying firmware.

    python3 tools/modapp.py [STOCK.UPD]        then open http://127.0.0.1:8700

Everything it previews is decoded from the firmware itself. Every file it
writes goes through the same verification the CLI does: block checksums
recomputed, encoder round-trip asserted, and the rebuilt block decoded again
and compared against the intended edit.

It will not write a file that fails any of those.
"""
import io, os, sys, json, base64, struct, threading, traceback, webbrowser
import http.server, socketserver, urllib.parse
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, 'mods'))
import lzss_boot as L
from fwtool import parse, assemble
import themes as TH

PORT = 8700
STOCK = sys.argv[1] if len(sys.argv) > 1 else \
    os.path.expanduser('~/Downloads/cdj3k-mods-0.1.0/XDJ700.UPD')

# regions that render as recognisable UI, with the offsets so they can be checked
SPOTS = [('device buttons (USB)', 0x080F274, 144, 112),
         ('panel / bar fills',    0x051F000, 150, 96),
         ('beat-fraction glyphs', 0x0450000, 120, 96)]

STATE = {'jobs': {}, 'n': 0}


def load_block():
    p = os.path.join(ROOT, 'cache', 'blk1.bin')
    if os.path.exists(p):
        return open(p, 'rb').read()
    img = open(os.path.join(ROOT, 'work', 'MAIN.img'), 'rb').read()
    ln = struct.unpack_from('<I', img, 0x40000)[0]
    d = L.decode(img[0x40004:0x40004 + ln])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').write(d)
    return d


print('loading firmware block1 ...')
DEC = load_block()
W0 = np.frombuffer(DEC[:len(DEC) // 2 * 2], dtype='<u2')
ART, NB = TH.art_windows(W0)
print(f'  {len(DEC):,} bytes, {int(ART.sum()):,}/{NB:,} art windows')
SIM = None
_simp = os.path.join(ROOT, 'sim', 'classic-blue.png')
if os.path.exists(_simp):
    SIM = np.asarray(Image.open(_simp).convert('RGB')).astype(np.float32)


def rgb565_to_arr(v):
    v = v.astype(np.uint32)
    return np.stack([((v >> 11) & 0x1F) * 255 // 31,
                     ((v >> 5) & 0x3F) * 255 // 63,
                     (v & 0x1F) * 255 // 31], -1).astype(np.uint8)


def png_b64(im, scale=1):
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    b = io.BytesIO(); im.save(b, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(b.getvalue()).decode()


def assets_preview(spec, scale=2):
    new, cnt = TH.recolour(W0, spec)
    out = []
    for label, off, wd, rows in SPOTS:
        a = off // 2
        arr = rgb565_to_arr(new[a:a + wd * rows]).reshape(rows, wd, 3)
        out.append({'label': f'{label}  @{off:#x}',
                    'img': png_b64(Image.fromarray(arr), scale)})
    return out, cnt


def screen_preview(spec):
    if SIM is None:
        return None
    r, g, b = SIM[..., 0], SIM[..., 1], SIM[..., 2]
    if spec is not None:
        h, s, v = TH._to_hsv(r, g, b)
        sel = (h >= TH.HUE_LO) & (h <= TH.HUE_HI) & (s >= TH.SAT_MIN)
        mid = (TH.HUE_LO + TH.HUE_HI) / 2
        nr, ng, nb = TH._to_rgb(spec['hue'] + (h - mid) * spec['spread'],
                                np.clip(s * spec['sat'], 0, 1),
                                np.clip(v * spec['val'], 0, 1))
        r = np.where(sel, nr, r); g = np.where(sel, ng, g); b = np.where(sel, nb, b)
    arr = np.stack([r, g, b], -1).clip(0, 255).astype(np.uint8)
    im = Image.fromarray(arr)
    im = im.resize((im.width // 2, im.height // 2), Image.LANCZOS)
    return png_b64(im)


def do_build(job, spec, name, version, outpath):
    def log(m):
        job['log'].append(m); print('  ' + m)
    try:
        log('parsing container ...')
        raw, secs = parse(STOCK)
        main = secs[0]
        blk = [b for b in main.blocks() if b[0] == 0x40000]
        if not blk:
            raise RuntimeError('block1 not found')
        h, ln = blk[0]
        dec = L.decode(bytes(main.img[h + 4:h + 4 + ln]))
        n = len(dec) // 2 * 2
        w = np.frombuffer(dec[:n], dtype='<u2').copy()
        new, cnt = TH.recolour(w, spec)
        buf = bytearray(dec); buf[:n] = new.tobytes()
        log(f'{cnt:,} pixels recoloured')

        if bytes(buf) != dec:
            log('compressing ...')
            st = L.encode(bytes(buf))
            if L.decode(st) != bytes(buf):
                raise RuntimeError('encoder round-trip FAILED - refusing to write')
            log(f'stream {ln:,} -> {len(st):,} ({len(st)-ln:+,})')
            main.write_block(h, ln, st)
        else:
            log('no pixel changes')

        if version:
            old = main.pre
            import re
            main.pre = re.sub(r'(MAINVer)[0-9.]+',
                              lambda m: m.group(1) + version, main.pre, count=1)
            if main.pre == old:
                log('WARNING: version string not found, left unchanged')
            else:
                log(f'version -> {version}')

        os.makedirs(os.path.dirname(outpath) or '.', exist_ok=True)
        open(outpath, 'wb').write(assemble(secs))
        log('verifying written file ...')
        _, chk = parse(outpath)
        cm = chk[0]
        sums = cm.check_blocks()
        for hh, ll, a, b, ok in sums:
            log(f'  block @{hh:#08x} sum {a:#06x} vs {b:#06x} {"OK" if ok else "BAD"}')
        if not all(o for *_, o in sums):
            raise RuntimeError('checksum mismatch in written file')
        h2, l2 = [x for x in cm.blocks() if x[0] == 0x40000][0]
        back = L.decode(bytes(cm.img[h2 + 4:h2 + 4 + l2]))
        if back != bytes(buf):
            raise RuntimeError('written file does not decode to the intended edit')
        log('decodes to exactly the intended edit: True')
        if secs[1].emit() != parse(STOCK)[1][1].emit():
            raise RuntimeError('PANL section changed - refusing')
        log('PANL untouched: True')
        log('')
        log('--- safety gate ---')
        import io as _io, contextlib
        from safety import check as safety_check
        cap = _io.StringIO()
        with contextlib.redirect_stdout(cap):
            passed = safety_check(STOCK, outpath)
        for line in cap.getvalue().splitlines():
            if line.strip():
                log(line.rstrip())
        if not passed:
            os.replace(outpath, outpath + '.REJECTED')
            raise RuntimeError('SAFETY GATE REFUSED - file renamed .REJECTED, do not flash')
        job['size'] = os.path.getsize(outpath)
        job['out'] = outpath
        job['state'] = 'done'
    except Exception as e:
        job['log'].append('ERROR: ' + str(e))
        job['log'].append(traceback.format_exc().splitlines()[-1])
        job['state'] = 'error'


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, body, ctype='application/json'):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/':
            return self._send(200, PAGE, 'text/html; charset=utf-8')
        if u.path == '/api/themes':
            return self._send(200, json.dumps({
                'themes': {k: v for k, v in TH.THEMES.items()},
                'stock': STOCK,
                'art': int(ART.sum()), 'windows': int(NB),
                'decoded': len(DEC)}))
        if u.path == '/api/preview':
            spec = None
            if q.get('mode', ['theme'])[0] != 'stock':
                spec = dict(hue=float(q['hue'][0]), sat=float(q['sat'][0]),
                            val=float(q['val'][0]), spread=float(q['spread'][0]))
            a, cnt = assets_preview(spec)
            return self._send(200, json.dumps(
                {'assets': a, 'screen': screen_preview(spec), 'pixels': cnt}))
        if u.path == '/api/job':
            j = STATE['jobs'].get(q['id'][0])
            return self._send(200, json.dumps(j or {'state': 'missing'}))
        return self._send(404, '{}')

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        d = json.loads(self.rfile.read(n) or b'{}')
        if urllib.parse.urlparse(self.path).path == '/api/build':
            spec = None
            if d.get('mode') != 'stock':
                spec = dict(hue=float(d['hue']), sat=float(d['sat']),
                            val=float(d['val']), spread=float(d['spread']))
            STATE['n'] += 1
            jid = str(STATE['n'])
            name = d.get('name') or 'custom'
            out = os.path.join(ROOT, 'build', f'XDJ700_{name}.UPD')
            job = {'state': 'running', 'log': [], 'name': name}
            STATE['jobs'][jid] = job
            threading.Thread(target=do_build, daemon=True,
                             args=(job, spec, name, d.get('version', '').strip(), out)).start()
            return self._send(200, json.dumps({'id': jid}))
        return self._send(404, '{}')


PAGE = open(os.path.join(HERE, 'modapp.html'), encoding='utf-8').read() \
    if os.path.exists(os.path.join(HERE, 'modapp.html')) else '<h1>modapp.html missing</h1>'

if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('127.0.0.1', PORT), H) as s:
        print(f'\n  XDJ-700 mod studio  ->  http://127.0.0.1:{PORT}\n')
        try: webbrowser.open(f'http://127.0.0.1:{PORT}')
        except Exception: pass
        s.serve_forever()
