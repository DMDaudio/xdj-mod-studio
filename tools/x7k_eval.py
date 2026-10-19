#!/usr/bin/env python3
"""x7k_eval.py -- codec size/quality matrix for the .x7k key-shift container.

(1) MATRIX on real music (/tmp/demo30.wav when present, else a track found
    under ~/Downloads/Rekordbox\\ stuff/Contents -- audio is read-only, all
    sidecars go to /tmp/x7k_eval/):
      codecs mp3-128/192, opus-96/128, aac-128, flac; semis -2/0/+2.
      Reports per codec: MB/min, synth tuning error (x7k.peak_freq on a
      440 Hz tone through rubberband+codec), vocal-ish tuning error via a
      voiced-period (autocorrelation f0) method on a stable music window.
(2) PHASE-3 per codec: null-test of the +0 version vs the source mix
      (RMS residual dB after xcorr alignment) + transient smear via
      onset (spectral-flux) envelope correlation, with a qualitative read.
(3) FULL-TRACK: --fulltrack builds semis -4..+4 of the 339 s Radio 84
      track at the chosen codec and reports wall time.

Usage:
  .venv/bin/python tools/x7k_eval.py [--rebuild] [--fulltrack] [--codec X] [--bitrate N]
"""
import glob
import math
import os
import sys
import tempfile
import time

import numpy as np

sys.path[:0] = [".", "tools"]
import x7k

OUTDIR = "/tmp/x7k_eval"
MATRIX = [("mp3", 128), ("mp3", 192), ("opus", 96), ("opus", 128),
          ("aac", 128), ("flac", 0)]
SEMIS = [-2, 0, +2]
FULL_SEMIS = list(range(-4, 5))


def find_clip():
    c = "/tmp/demo30.wav"
    if os.path.exists(c):
        return c
    roots = sorted(glob.glob(os.path.expanduser(
        "~/Downloads/Rekordbox stuff/Contents/*/*/*")))
    for p in roots:
        if os.path.isfile(p) and p.lower().endswith(
                (".wav", ".aif", ".aiff", ".mp3", ".flac", ".m4a")):
            return p
    raise SystemExit("no source clip found")


def find_radio84():
    hits = glob.glob(os.path.expanduser(
        "~/Downloads/Rekordbox stuff/Contents/*/*/*Radio 84*"))
    hits = [h for h in hits if os.path.isfile(h)]
    if not hits:
        raise SystemExit("Radio 84 track not found")
    return sorted(hits)[0]


def to_mono(x):
    x = np.asarray(x, dtype=np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def xcorr_lag(ref, dec, maxlag=8192):
    """Samples by which dec LAGS ref (dec delayed => positive)."""
    n = 1 << (len(ref) + len(dec) - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(dec, n)), n)
    c = np.concatenate([c[-(maxlag + 1):], c[:maxlag + 1]])
    i = int(np.argmax(c))
    return (maxlag + 1) - i


def null_db(ref, dec, maxlag=8192, edge=2.0, rate=44100):
    """Align dec to ref, then 20*log10(rms(res)/rms(ref)) over the middle."""
    m = to_mono(ref)
    d = to_mono(dec)
    seg = slice(len(m) // 2 - 5 * rate, len(m) // 2 + 5 * rate)
    lag = xcorr_lag(m[seg], d[seg], maxlag)
    e = int(edge * rate)
    if lag >= 0:
        a, b = m[e:len(m) - e], d[e + lag:len(m) - e + lag]
    else:
        a, b = m[e - lag:len(m) - e - lag], d[e:len(m) - e]
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    res = b - a
    rr, ra = float(np.sqrt((res ** 2).mean())), float(
        np.sqrt((a ** 2).mean()))
    db = 20 * math.log10(rr / ra) if rr > 0 else float("-inf")
    return db, lag


def onset_env(mono, sr, n=2048, hop=1024, fmin=0.0):
    w = np.hanning(n)
    lo = int(fmin * n / sr)
    nfr = 1 + (len(mono) - n) // hop
    prev = None
    out = np.empty(nfr)
    for i in range(nfr):
        mag = np.log1p(np.abs(np.fft.rfft(mono[i * hop:i * hop + n] * w)))
        mag = mag[lo:]
        out[i] = 0.0 if prev is None else float(
            np.maximum(0.0, mag - prev).sum())
        prev = mag
    return out


def pearson(a, b):
    a = a - a.mean()
    b = b - b.mean()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-18))


def flux_corr(ref, dec, rate):
    out = {}
    for key, fmin in (("full", 0.0), ("hi", 5000.0)):
        fr = onset_env(to_mono(ref), rate, fmin=fmin)
        fd = onset_env(to_mono(dec), rate, fmin=fmin)
        e = int(2.0 * rate / 1024)          # drop padded edges (~2 s)
        fr, fd = fr[e:-e], fd[e:-e]
        best = -2.0
        for s in range(-8, 9):              # +/-8 envelope frames of slack
            if s >= 0:
                r = pearson(fr[:len(fr) - s or None], fd[s:len(fr)])
            else:
                r = pearson(fr[-s:len(fr)], fd[:len(fr) + s or None])
            best = max(best, r)
        out[key] = best
    return out["full"], out["hi"]


def f0_autocorr(seg, sr, fmin=60.0, fmax=900.0):
    """Autocorrelation f0 with parabolic interpolation. Returns (f0, conf)."""
    x = np.asarray(seg, dtype=np.float64)
    x = x - x.mean()
    if float((x ** 2).mean()) < 1e-12:
        return 0.0, 0.0
    n = 1 << (2 * len(x) - 1).bit_length()
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, n)) ** 2, n)[:len(x)]
    ac /= ac[0] + 1e-18
    lo, hi = int(sr / fmax), min(int(sr / fmin), len(x) - 2)
    if hi <= lo + 1:
        return 0.0, 0.0
    k = lo + int(np.argmax(ac[lo:hi]))
    if not 0 < k < len(ac) - 1:
        return sr / k, float(ac[k])
    a, b, c = ac[k - 1], ac[k], ac[k + 1]
    d = 0.5 * (a - c) / (a - 2 * b + c + 1e-18)
    return sr / (k + d), float(b)


def find_voiced(mono, sr, win=0.35, skip=2.0):
    """Most periodic 350 ms window of the clip (the 'voiced' probe point)."""
    n = int(win * sr)
    hop = n // 2
    lo = int(skip * sr)
    hi = len(mono) - n - lo
    best = (0.0, lo)
    for s in range(lo, hi, hop):
        f0, conf = f0_autocorr(mono[s:s + n], sr)
        e = float(np.sqrt((mono[s:s + n] ** 2).mean()))
        score = conf * math.log1p(e * 20)
        if score > best[0]:
            best = (score, s)
    return best[1], best[1] + n


def cents(f, want):
    return 1200 * math.log2(f / want) if f > 0 and want > 0 else float("nan")


def build_cached(src, semis, codec, bitrate, tag, rebuild=False):
    os.makedirs(OUTDIR, exist_ok=True)
    xp = os.path.join(OUTDIR, f"{tag}_{codec}{bitrate or ''}.x7k")
    if os.path.exists(xp) and not rebuild:
        print(f"  reusing {xp}", flush=True)
        return xp
    t0 = time.perf_counter()
    data = x7k.build(src, semis, codec=codec, bitrate=bitrate or 192)
    open(xp, "wb").write(data)
    print(f"  built {xp} ({len(data) / 1e6:.2f} MB, "
          f"{time.perf_counter() - t0:.0f}s)", flush=True)
    return xp


def synth_tuning(codec, bitrate, td):
    """440 Hz tone -> rubberband +2 -> codec -> peak_freq cents error."""
    rate = 44100
    t = np.arange(int(rate * 2.0)) / rate
    sig = (0.5 * np.sin(2 * math.pi * 440 * t)).astype(np.float32)
    st = np.stack([sig, sig], 1)
    tone = os.path.join(td, "tone.wav")
    x7k.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate),
             "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", tone],
            input=st.tobytes())
    shifted = x7k.shift_render(tone, +2, td, rate, 2)
    rb = x7k.peak_freq(x7k.decode_pcm(shifted).reshape(-1, 2)[:, 0], rate)
    blob = x7k.encode_blob(shifted, codec, bitrate or 192, td, "syn")
    dp = os.path.join(td, f"syn.{codec}")
    open(dp, "wb").write(blob)
    f = x7k.peak_freq(x7k.decode_pcm(dp, rate, 2).reshape(-1, 2)[:, 0], rate)
    want = 440.0 * 2 ** (2 / 12)
    return cents(rb, want), cents(f, want)


def matrix(rebuild=False):
    src = find_clip()
    rate, ch, frames = x7k.probe(src)
    dur = frames / rate
    print(f"source: {src}\n  {rate} Hz ch={ch} {dur:.1f}s "
          f"({os.path.getsize(src) / 1e6:.1f} MB)")
    ref0 = x7k.decode_pcm(src).reshape(-1, ch)
    td = tempfile.mkdtemp(prefix="x7k_eval_")
    print(f"sidecars: {td} (+ cached builds in {OUTDIR}/)")
    # rubberband-only +2 reference (pre-codec) for the f0 probe
    rb2wav = x7k.shift_render(src, +2, td, rate, ch)
    ref2 = x7k.decode_pcm(rb2wav).reshape(-1, ch)
    m0, m2 = to_mono(ref0), to_mono(ref2)
    s0, s1 = find_voiced(m0, rate)
    f0_src, c_src = f0_autocorr(m0[s0:s1], rate)
    f0_rb, c_rb = f0_autocorr(m2[s0:s1], rate)
    rb_c = cents(f0_rb, f0_src * 2 ** (2 / 12))
    print(f"voiced window [{s0 / rate:.1f},{s1 / rate:.1f}]s "
          f"f0_src={f0_src:.1f}Hz conf={c_src:.2f} "
          f"f0_rb(+2)={f0_rb:.1f}Hz ({rb_c:+.0f}c rubberband-only)")
    rows = []
    for codec, br in MATRIX:
        print(f"== {codec}-{br or 'lossless'} ==", flush=True)
        xp = build_cached(src, SEMIS, codec, br, "mtx", rebuild)
        idx = x7k.read_index(xp)
        per_min = sorted(v["len"] / (idx["frames"] / idx["rate"] / 60) / 1e6
                         for v in idx["versions"])
        mbmin = sum(per_min) / len(per_min)
        dec0, _ = x7k.version_pcm(idx, 0, td)
        dec2, _ = x7k.version_pcm(idx, +2, td)
        ndb, lag = null_db(ref0, dec0, rate=rate)
        fl, fh = flux_corr(ref0, dec0, rate)
        # voiced f0 through the codec (align codec padding vs ref2 first)
        seg = slice(len(m2) // 2 - 5 * rate, len(m2) // 2 + 5 * rate)
        lag2 = xcorr_lag(m2[seg], to_mono(dec2)[seg])
        w = to_mono(dec2)[s0 + lag2:s1 + lag2]
        f0_dec, c_dec = f0_autocorr(w, rate)
        vc = cents(f0_dec, f0_src * 2 ** (2 / 12))
        sc_rb, sc = synth_tuning(codec, br, td)
        rows.append({"codec": codec, "br": br, "mbmin": mbmin,
                     "synth_rb_c": sc_rb, "synth_c": sc,
                     "voc_f0": f0_dec, "voc_conf": c_dec, "voc_c": vc,
                     "null_db": ndb, "lag": lag, "flux": fl, "flux_hi": fh})
        print(f"  {mbmin:.2f} MB/min | synth {sc:+.0f}c (rb {sc_rb:+.0f}c) | "
              f"voc f0 {f0_dec:.1f}Hz ({vc:+.0f}c, conf {c_dec:.2f}) | "
              f"null {ndb:.1f}dB (lag {lag}) | "
              f"flux r={fl:.4f} hi={fh:.4f}", flush=True)
    print(f"\nvoiced probe: f0_src={f0_src:.1f}Hz, rubberband-only {rb_c:+.0f}c")
    return rows


def fulltrack(codec, bitrate, rebuild=False):
    src = find_radio84()
    rate, ch, frames = x7k.probe(src)
    print(f"full-track source: {src}\n  {rate} Hz ch={ch} "
          f"{frames / rate:.0f}s ({os.path.getsize(src) / 1e6:.0f} MB)")
    t0 = time.perf_counter()
    xp = build_cached(src, FULL_SEMIS, codec, bitrate, "full", rebuild)
    wall = time.perf_counter() - t0
    idx = x7k.read_index(xp)
    per_min = sum(v["len"] for v in idx["versions"]) / len(idx["versions"]) / \
        (idx["frames"] / idx["rate"] / 60) / 1e6
    print(f"FULL-TRACK {codec}-{bitrate}: {len(open(xp, 'rb').read()) / 1e6:.0f} MB "
          f"({per_min:.2f} MB/min/version), wall {wall / 60:.1f} min")
    return wall


def main():
    a = sys.argv[1:]
    rebuild = "--rebuild" in a
    if "--fulltrack" in a:
        ci = a.index("--codec") if "--codec" in a else None
        codec = a[ci + 1] if ci is not None else "opus"
        bi = a.index("--bitrate") if "--bitrate" in a else None
        br = int(a[bi + 1]) if bi is not None else (96 if codec == "opus" else 128)
        fulltrack(codec, br, rebuild)
    else:
        matrix(rebuild)


if __name__ == "__main__":
    main()
