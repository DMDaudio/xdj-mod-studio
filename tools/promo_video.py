"""Vertical (1080x1920) promo clip for XDJ Mod Studio.

    .venv/bin/python tools/promo_video.py [OUT.mp4]

Scenes are deck screens (emulator captures + Studio preview renders of the
user's own stick) with big captions. Silent: put music under it in the
editor. Contains firmware-drawn pixels -> the user's own video only, never
in the public repo.
"""
import os, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

R = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(R, p) for p in ("studio", "tools", "mods", "")]
W, H, FPS = 1080, 1920, 30
BG = (11, 11, 16)
PURPLE, TEAL, WHITE, DIM = (155, 77, 255), (25, 211, 197), (255, 255, 255), (150, 150, 165)


def font(sz, bold=True):
    for p, i in (("/System/Library/Fonts/HelveticaNeue.ttc", 1 if bold else 0),
                 ("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0)):
        try:
            return ImageFont.truetype(p, sz, index=i)
        except OSError:
            pass
    return ImageFont.load_default()


def shot(name):
    return Image.open(os.path.join(R, "screenshots", name)).convert("RGB")


def render(theme=None, screen="main", style="blue"):
    import io
    import app
    import themes as T
    spec = T.THEMES[theme] if theme else None
    return Image.open(io.BytesIO(app.preview_png(spec, screen, wopts={"style": style}))).convert("RGB")


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def text_c(d, y, s, f, fill):
    w = d.textlength(s, font=f)
    d.text(((W - w) / 2, y), s, font=f, fill=fill)


def wrap(d, s, f, maxw):
    out, line = [], ""
    for wd in s.split():
        t = (line + " " + wd).strip()
        if d.textlength(t, font=f) > maxw and line:
            out.append(line)
            line = wd
        else:
            line = t
    return out + [line]


def deck_frame(img, scale=1.30):
    """Screen on a rounded dark bezel, centred."""
    w, h = int(800 * scale), int(480 * scale)
    im = img.resize((w, h), Image.LANCZOS)
    pad = 18
    card = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (28, 28, 36))
    m = Image.new("L", card.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, card.size[0] - 1, card.size[1] - 1], 26, fill=255)
    card.paste(im, (pad, pad))
    return card, m


def scene(title, sub, imgs, dur, accent=PURPLE, crop=None, detail=None):
    """imgs: list of PIL screens shown one after another (crossfaded)."""
    F = int(dur * FPS)
    ft, fs = font(84), font(44, False)
    dets = [im.crop(detail) for im in imgs] if detail else None
    if crop:
        imgs = [im.crop(crop).resize((800, int(800 * (crop[3] - crop[1]) / (crop[2] - crop[0]))))
                for im in imgs]
    frames = []
    for i in range(F):
        t = i / F
        fr = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(fr)
        # glow
        a = ease(t * 4)
        d.rectangle([0, 0, W, 10], fill=accent)
        # title + sub slide in
        dy = int(60 * (1 - ease(t * 5)))
        y = 200 + dy
        for ln in wrap(d, title, ft, W - 120):
            text_c(d, y, ln, ft, WHITE)
            y += 100
        y += 10
        for ln in wrap(d, sub, fs, W - 160):
            text_c(d, y, ln, fs, tuple(int(c * a + BG[k] * (1 - a)) for k, c in enumerate(DIM)))
            y += 58
        # screen(s)
        n = len(imgs)
        seg = 1.0 / n
        k = min(n - 1, int(t / seg))
        lt = (t - k * seg) / seg
        cur = imgs[k]
        if k + 1 < n and lt > 0.85:
            pass                    # clean cut between screens
        zoom = 1.30 + 0.05 * t
        card, m = deck_frame(cur, zoom)
        cx = (W - card.size[0]) // 2
        cy = (760 if dets else 900) + int(40 * (1 - ease(t * 4)))
        fr.paste(card, (cx, cy), m)
        if dets:                        # zoomed close-up under the screen
            dc = dets[k]
            if k + 1 < n and lt > 0.85:
                pass
            dw = 1000
            dh = int(dw * dc.size[1] / dc.size[0])
            z = dc.resize((dw, dh), Image.LANCZOS)
            zm = Image.new("L", z.size, 0)
            ImageDraw.Draw(zm).rounded_rectangle([0, 0, dw - 1, dh - 1], 22, fill=255)
            zy = cy + card.size[1] + 50
            ImageDraw.Draw(fr).rounded_rectangle([(W - dw) // 2 - 6, zy - 6, (W + dw) // 2 + 5, zy + dh + 5], 26, outline=accent, width=6)
            fr.paste(z, ((W - dw) // 2, zy), zm)
        frames.append(fr)
    return frames


def title_card(lines, dur, accent=PURPLE, small=None):
    F = int(dur * FPS)
    out = []
    fb, fsm = font(120), font(48, False)
    for i in range(F):
        t = i / F
        fr = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(fr)
        # platter mark
        r = 150
        cx, cy = W // 2, 640
        sp = 360 * t
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=14)
        d.ellipse([cx - 28, cy - 28, cx + 28, cy + 28], fill=TEAL)
        import math
        a = math.radians(sp - 90)
        d.line([cx, cy, cx + (r - 24) * math.cos(a), cy + (r - 24) * math.sin(a)], fill=WHITE, width=10)
        y = 900
        al = ease(t * 3)
        for j, ln in enumerate(lines):
            c = tuple(int(v * al + BG[k] * (1 - al)) for k, v in enumerate(WHITE if j == 0 else TEAL))
            text_c(d, y, ln, fb if j == 0 else font(70), c)
            y += 150 if j == 0 else 100
        if small:
            y += 30
            for sl in small:
                for ln in wrap(d, sl, fsm, W - 140):
                    text_c(d, y, ln, fsm, DIM)
                    y += 64
        out.append(fr)
    return out


def main(out):
    print("rendering screens…")
    P = lambda n: Image.open(os.path.join(R, "screenshots", "promo_gl", n + ".png")).convert("RGB")
    B = lambda n: Image.open(os.path.join(R, "screenshots", "promo_glb", n + ".png")).convert("RGB")
    T3 = lambda n: Image.open(os.path.join(R, "screenshots", "promo_gl3", n + ".png")).convert("RGB")
    REC = os.path.join(R, "screenshots", "promo_rec")
    rec = sorted(os.listdir(REC)) if os.path.isdir(REC) else []
    if rec:                              # continuous recording (moving screens)
        import json
        meta = json.load(open(os.path.join(R, "build", "promo_rec.json")))
        fps, r0 = meta["fps"], meta["rec0"]
        Dd = [2.6, 4.2, 3.4, 4.5, 3.6, 3.6, 3.6, 3.6]
        Tt = np.cumsum([0] + Dd)

        def RS(i):                       # frames of scene i
            a, b = int(round((Tt[i] - r0) * fps)), int(round((Tt[i + 1] - r0) * fps))
            return [Image.open(os.path.join(REC, rec[min(k, len(rec) - 1)])).convert("RGB")
                    for k in range(a, b)]
    wave = [B("main_a"), P("main_a"), T3("main_a")]
    pm = [T3("main_a"), T3("main_b")]
    themes = [P("theme_hot-pink_main"), P("theme_ice-cyan_main"), P("theme_amber-gold_main")]
    bj = [P("perform_beatjump"), P("bj_back16"), P("bj_fwd16")]
    ks = [P("ks_up2"), P("ks_dn2")]
    st = [P("perform_stems"), P("perform_stems_vocals_off")]
    hc = [P("perform"), P("theme_hot-pink_perform")]
    if rec:
        wave, pm, themes, bj, ks, st = RS(1), RS(2), RS(3), RS(4), RS(5), RS(6)
        hc = RS(7)
    row = (0, 140, 800, 340)   # PERFORM pad area, zoomed
    seq = []
    seq += title_card(["XDJ-700", "MODS"], 2.6, small=["Free · for Mac · no flashing"])
    seq += scene("RGB & 3-band waveforms", "Blue, RGB or 3-band, like the newest decks. Uses the colours rekordbox already exports.", wave, 4.2, TEAL, detail=(100, 340, 640, 460))
    seq += scene("Phase meter", "Beats and downbeats scroll past a fixed line above the waveform, like the XDJ-1000MK2.", pm, 3.4, PURPLE, detail=(170, 112, 560, 162))
    seq += scene("Themes", "Recolour the whole deck from its own menu. OFF puts the stock look back.", themes, 4.5, detail=(96, 6, 788, 104))
    seq += scene("Beat jump pads", "Back 16, forward 16. Swipe the row for 1 up to 64 beats.", bj, 3.6, TEAL, detail=(8, 228, 628, 300))
    seq += scene("Live key shift", "Up 2, down 2 while it plays. Tempo stays put. Shows the real key.", ks, 3.6, PURPLE, detail=(8, 228, 628, 300))
    seq += scene("Stems", "Mute vocals, drums, bass or other right on the deck.", st, 3.6, TEAL, detail=(8, 228, 628, 300))
    seq += scene("And more", "8 hot cues · vinyl brake length · FLAC & Opus · MY SETTINGS menu", hc + ([] if rec else [T3("perform")]), 3.6, PURPLE, detail=(8, 228, 628, 300))
    seq += title_card(["Power-cycle", "= stock"], 2.6, TEAL,
                      small=["Everything loads into the deck's memory over USB.", "Switch off and on: 100% original."])
    seq += title_card(["XDJ Mod Studio", "free beta"], 3.0, PURPLE, small=["for XDJ-700 · firmware 1.15"])
    print(f"{len(seq)} frames -> {out}")
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                          "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "medium",
                          "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    for fr in seq:
        p.stdin.write(np.asarray(fr, np.uint8).tobytes())
    p.stdin.close()
    sys.exit(p.wait())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, "dist", "XDJ-Mod-Studio-promo-vertical.mp4"))
