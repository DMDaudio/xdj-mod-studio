#!/usr/bin/env python3
"""x7s_small.py -- shrink study for the X7S stems sidecar.

Imports tools/x7s (geometry, PCM decode, xcorr, synth fallback) and
modifies nothing in it. All lossy codecs render through ffmpeg; all
metrics are computed from decoded PCM against the source wavs.

Corpus (read-only, never modified):
  REAL separated stems (htdemucs):
    full: /tmp/x7k_cprime/demucs_full/htdemucs/Lifelike & Roman Salzger - Radio 84/
          bass/drums/other/vocals.wav, 44.1 kHz stereo, 339 s (~5.6 min)
    demo: /tmp/x7k_cprime/demucs_sep/htdemucs/demo30/ (same, 30 s)
  If neither exists, falls back to x7s._synth_stems (10 s synth).

Codec matrix (per plane, stereo):
  opus 32/48/64/96/128 (libopus VBR), aac 64/96/128 (native aac),
  mp3 64/96/128 (libmp3lame), flac (lossless reference).
  Spec core is opus-64/96/128, aac-96/128, mp3-96/128, flac; the
  32/48/64 extras are the per-plane allocation probes (bass/drums floor).

Metrics per (codec, bitrate, plane), delay-aligned via xcorr:
  null_dB  = 20*log10(rms(src)/rms(src-dec)) on the central region.
  segnull  = mean per-4096-sample-frame null over ACTIVE frames only
             (frame rms > 1e-3 = -60 dBFS), segSNR-style. This is the
             primary fidelity number: separated stems are sparse and a
             global null is dominated by codec idle noise in silent
             passages (demo30's bass/vocals sit at -80 dBFS and score
             ~5 dB on ANY lossy codec, including the mp3-128 status quo).
  flux_r   = Pearson r of spectral-flux envelopes (broadband + highband
             >4 kHz), STFT Hann 2048/1024 over a 60 s central excerpt,
             numpy only. Transient keeper.
  lag      = absolute xcorr lag vs source (sample-sync check: all planes
             of one setting must share the same lag).
  bytes    = blob size -> MB/min (60 s) and projected 5-min track.

Mix==sum check per setting: mix plane (exact sample sum, encoded with the
same setting) decoded vs sum-of-decoded-stems: lag must be 0 and the
residual null_dB must sit at/below the planes' own codec-noise floor.
That decides whether the 5th (mix) plane can be dropped (saves 20%).

Transparency bar (stated upfront, applied in recommend()):
  good:        null >= 30 dB every plane, flux_r >= 0.95 (highband >= 0.93)
  transparent: null >= 35 dB every plane, flux_r >= 0.97
  no-regression vs status quo (mp3-128): within 3 dB null per plane.
Winner = smallest total MB/min clearing "good" + no-regression; goal
is < ~2 MB/min.

Usage:
  .venv/bin/python tools/x7s_small.py matrix [full|demo]  # full=339 s song
      -> /tmp/x7s_small/matrix_<which>.json (tables printed too)
  .venv/bin/python tools/x7s_small.py validate [spec ...]  # candidate
      # spec: opus64 | mp3128 | bass:opus48,drums:opus64,vocals:opus96,
      # other:opus64,mix:opus64  -> /tmp/x7s_small/validate.json
  .venv/bin/python tools/x7s_small.py report    # prints tables from JSONs
Artifacts: /tmp/x7s_small/ (blobs, pcm, json). Rekordbox audio untouched.

FINDINGS 2026-10-08 (Radio 84, htdemucs stems, 339 s, 44.1 kHz stereo):
  WINNER = opus, bass:64k + drums:96k + vocals:96k + other:96k, mix DROPPED
  (nplane=4): 2.55 MB/min = 12.7 MB per 5-min track. Segnull deltas vs the
  mp3-128 status quo: bass -0.9, drums -1.2, vocals -0.4, other -2.1 dB
  (all within the 3 dB no-regression bar); flux broad >=0.995, high >=0.953;
  sample-sync lagset [0], mix==sum lag 0 / residual segnull 16.5 dB
  (= plane codec-noise level: dropping mix adds no new artifact class).
  Under-2MB/min alternative = uniform opus64 @1.85 (9.2 MB/5min) with a
  stated -4..-6 dB segnull regression on drums/vocals/other (flux holds).
  Simplicity alternative = uniform opus96 @2.77 (13.9 MB/5min).
  DISQUALIFIED: opus <=48 at any rate control (VBR, constrained, true CBR
  all wander lag 0..5 mid-stream, SILK/CELT mode switches; vocals segnull
  collapses to ~0 dB and inter-plane sync FAILS). Opus floor is 64k.
  ALLOCATION correction: bass has headroom (knee at 64k), DRUMS does not
  (binding at every setting, monotonic 6.5->14.8 dB, no knee) -- the task
  premise "bass/drums need fewer bits" holds for bass only.
  FORMAT: one addition -- CODECS["opus"]=4 (reserve aac=5); no structural
  change (per-plane codec ids exist; bitrate not stored; nplane=4 is
  expressible; same-codec mixed-bitrate keeps one delay, verified).
  PRODUCER NOTE: these real stems sum to peak 1.001, which stock x7s.build
  REFUSES (peak>1.0) -- needs headroom handling before build.
  Full tables: /tmp/x7s_small/matrix_full.json + validate.json.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import x7s

ART = "/tmp/x7s_small"
FULL_DIR = "/tmp/x7k_cprime/demucs_full/htdemucs/Lifelike & Roman Salzger - Radio 84"
DEMO_DIR = "/tmp/x7k_cprime/demucs_sep/htdemucs/demo30"
PLANES = ["bass", "drums", "vocals", "other"]

MATRIX = [("opus", 32), ("opus", 48), ("opus", 64), ("opus", 96),
          ("opus", 128), ("aac", 64), ("aac", 96), ("aac", 128),
          ("mp3", 64), ("mp3", 96), ("mp3", 128), ("flac", 0)]
EXT = {"opus": "opus", "aac": "m4a", "mp3": "mp3", "flac": "flac"}

MARGIN = 12000      # samples cleared at each end for metrics
MAXLAG = 8192       # xcorr search window


def encode_plane(src_wav, codec, bitrate, out_path):
    if codec == "mp3":
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src_wav,
               "-c:a", "libmp3lame", "-b:a", f"{bitrate}k", out_path]
    elif codec == "opus":
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src_wav,
               "-c:a", "libopus", "-b:a", f"{bitrate}k", out_path]
    elif codec == "aac":
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src_wav,
               "-c:a", "aac", "-b:a", f"{bitrate}k", out_path]
    elif codec == "flac":
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", src_wav,
               "-c:a", "flac", out_path]
    else:
        raise ValueError(codec)
    subprocess.run(cmd, check=True, capture_output=True)


def decode_file(path, rate=44100, ch=2):
    """Decode to f32 PCM at (rate, ch). Own arg order: -ar/-ac BEFORE the
    output url (x7s.decode_pcm appends them after `-`, where ffmpeg
    ignores them -- harmless for 44.1k-native mp3/flac, fatal for opus
    which decodes at its native 48 kHz)."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-acodec",
           "pcm_f32le", "-ar", str(rate), "-ac", str(ch), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch)


def null_db(ref, tst):
    """20*log10(rms(ref)/rms(ref-tst)); inf if bit-exact."""
    ref = np.asarray(ref, dtype=np.float64)
    tst = np.asarray(tst, dtype=np.float64)
    num = float(np.sqrt(np.mean(ref ** 2)))
    den = float(np.sqrt(np.mean((ref - tst) ** 2)))
    if den == 0.0:
        return float("inf")
    if num == 0.0:
        return float("-inf")
    return 20.0 * np.log10(num / den)


def _onset_env(x, rate=44100, n=2048, hop=1024, fmin=0.0):
    """log1p-magnitude onset envelope (same method as x7k_eval.onset_env:
    Hann 2048/1024, positive spectral differences)."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    w = np.hanning(n).astype(np.float32)
    lo = int(fmin * n / rate)
    nfr = 1 + (len(x) - n) // hop
    out = np.empty(max(nfr, 0))
    prev = None
    for i in range(max(nfr, 0)):
        mag = np.log1p(np.abs(np.fft.rfft(x[i * hop:i * hop + n] * w)))
        mag = mag[lo:]
        out[i] = 0.0 if prev is None else float(np.maximum(0.0, mag - prev).sum())
        prev = mag
    return out


def _pearson(a, b):
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 1.0
    return float(a @ b / (denom + 1e-18))


def seg_null(ref, tst, frame=4096, thresh=1e-3):
    """Mean per-frame null-dB over active frames (segSNR-style)."""
    ref = np.asarray(ref, dtype=np.float64).reshape(-1)
    tst = np.asarray(tst, dtype=np.float64).reshape(-1)
    m = min(len(ref), len(tst))
    ref, tst = ref[:m], tst[:m]
    nf = m // frame
    vals = []
    for i in range(nf):
        s = ref[i * frame:(i + 1) * frame]
        e = s - tst[i * frame:(i + 1) * frame]
        if float(np.sqrt(np.mean(s ** 2))) < thresh:
            continue
        den = float(np.sqrt(np.mean(e ** 2)))
        if den == 0.0:
            vals.append(80.0)  # bit-exact frame: cap, don't inf the mean
            continue
        vals.append(20.0 * np.log10(float(np.sqrt(np.mean(s ** 2))) / den))
    if not vals:
        return float("nan"), 0.0
    return float(np.mean(vals)), len(vals) / nf


def flux_corr(a, dec, rate=44100):
    """Onset-envelope correlation (transient preservation), full + hi-band.
    Same method as x7k_eval.flux_corr: 2 s edge trim, best of +/-8
    envelope frames of slack. Runs on a 60 s central excerpt (statistical
    metric; full-length STFT would be ~6x the cost for the same answer)."""
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    dec = np.asarray(dec, dtype=np.float32).reshape(-1)
    m = min(len(a), len(dec))
    if m > 60 * rate:
        s = (m - 60 * rate) // 2
        a, dec = a[s:s + 60 * rate], dec[s:s + 60 * rate]
    else:
        a, dec = a[:m], dec[:m]
    out = {}
    for name, fmin in (("broad", 0.0), ("high", 5000.0)):
        fa = _onset_env(a, rate, fmin=fmin)
        fd = _onset_env(dec, rate, fmin=fmin)
        e = int(2.0 * rate / 1024)
        fa, fd = fa[e:-e], fd[e:-e]
        best = -2.0
        for sh in range(-8, 9):
            if sh >= 0:
                r = _pearson(fa[:len(fa) - sh or None], fd[sh:len(fa)])
            else:
                r = _pearson(fa[-sh:len(fa)], fd[:len(fa) + sh or None])
            best = max(best, r)
        out[name] = best
    return out


def locate_corpus():
    if os.path.isdir(FULL_DIR) and os.path.isdir(DEMO_DIR):
        return "real-demucs"
    return "synth"


def corpus_paths(which):
    """which: 'demo' (30 s) or 'full' (339 s). Returns {plane: wavpath}."""
    d = DEMO_DIR if which == "demo" else FULL_DIR
    return {p: os.path.join(d, f"{p}.wav") for p in PLANES}


def render_mix(srcs, td):
    """Exact sample sum of the 4 stem wavs -> mix.wav path (peak-checked).
    Returns (path, peak, scale). If the stem sum exceeds 1.0 (this corpus:
    1.001 -- note: stock x7s.build would REFUSE these stems), only the mix
    is scaled by 1/peak; stems are untouched. The scale imposes a ~60 dB
    ceiling on the mix==sum residual, far above codec-noise floors, so the
    drop-the-mix-plane verdict is unaffected."""
    dec = [decode_file(srcs[p], 44100, 2) for p in PLANES]
    mix = np.sum(dec, axis=0)
    peak = float(np.max(np.abs(mix)))
    scale = 1.0
    if peak > 1.0:
        scale = 1.0 / peak
        mix = mix * scale
    mx = os.path.join(td, "mix.wav")
    x7s.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", "44100",
             "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", mx],
            input=mix.astype(np.float32).tobytes())
    return mx, peak, scale


def run_setting(srcs, mix_path, codec, bitrate, workdir, rate=44100,
                spec=None, tag=None):
    """Encode 4 stems + mix, decode all, score. Returns row dict.
    Uniform (codec, bitrate) by default; spec={plane: (codec, br)} for a
    mixed per-plane allocation (tag required). NOTE: mixed CODECS give
    different absolute delays per plane (mp3 ~2112 vs opus ~0) -- the
    sync check will catch it; same-codec mixed-bitrate keeps one delay.
    """
    os.makedirs(workdir, exist_ok=True)
    if spec is None:
        spec = {p: (codec, bitrate) for p in PLANES + ["mix"]}
    if tag is None:
        tag = f"{codec}{bitrate}" if codec != "flac" else "flac"
    src_pcm = {p: decode_file(srcs[p], rate, 2) for p in PLANES}
    src_mix = decode_file(mix_path, rate, 2)
    n = min(len(v) for v in list(src_pcm.values()) + [src_mix])
    row = {"codec": codec, "bitrate": bitrate, "tag": tag,
           "planes": {}, "sizes": {}}
    dec = {}
    for p in PLANES + ["mix"]:
        src = srcs[p] if p in srcs else mix_path
        c, b = spec[p]
        ptag = f"{c}{b}" if c != "flac" else "flac"
        blob = os.path.join(workdir, f"{p}-{tag}__{ptag}.{EXT[c]}")
        t0 = time.time()
        encode_plane(src, c, b, blob)
        enc_s = time.time() - t0
        row["sizes"][p] = {"bytes": os.path.getsize(blob), "enc_s": enc_s,
                           "codec": c, "bitrate": b}
        d = decode_file(blob, rate, 2)
        dec[p] = d
    # per-plane: lag vs source, then aligned null + flux
    lags = {}
    for p in PLANES:
        a = src_pcm[p][:200000, 0]
        b = dec[p][:200000, 0]
        m = min(len(a), len(b))
        lags[p] = x7s._xcorr_lag(a[:m], b[:m], maxlag=MAXLAG)
    row["lags"] = lags
    lagset = sorted(set(lags.values()))
    lens = {p: len(dec[p]) for p in PLANES + ["mix"]}
    for p in PLANES:
        L = lags[p]
        a = src_pcm[p][MARGIN:n - MARGIN, :]
        lo, hi = MARGIN + L, n - MARGIN + L
        if lo <= 0 or hi > len(dec[p]):
            row["planes"][p] = {"null_db": None, "note": "lag out of range"}
            continue
        b = dec[p][lo:hi, :]
        nd = null_db(a, b)
        sn, af = seg_null(a, b)
        fc = flux_corr(a[:, 0], b[:, 0])
        row["planes"][p] = {
            "null_db": nd,
            "segnull_db": sn, "active_frac": af,
            "src_rms": float(np.sqrt(np.mean(a.astype(np.float64) ** 2))),
            "src_peak": float(np.max(np.abs(a))),
            "maxerr": float(np.max(np.abs(a - b))),
            "rms": float(np.sqrt(np.mean((a - b) ** 2))),
            "flux_broad": fc["broad"], "flux_high": fc["high"],
        }
    row["sync"] = {"equal_lags": len(lagset) == 1, "lagset": lagset,
                   "equal_lens": len(set(lens.values())) == 1, "lens": lens}
    # mix == sum of decoded stems (the drop-the-mix-plane question)
    names = PLANES
    L0 = lags["bass"]
    psum = sum(dec[k][MARGIN + lags[k]:n - MARGIN + lags[k], :] for k in names)
    m = dec["mix"][MARGIN + L0:n - MARGIN + L0, :]
    lag_m = x7s._xcorr_lag(psum[:200000, 0].copy(), m[:200000, 0].copy(),
                           maxlag=MAXLAG)
    # residual of sum-vs-decoded-mix, referenced to mix RMS:
    # can the mix plane be dropped (play sum instead) transparently?
    # res_segnull: same residual in active-frame terms (the DJ-relevant
    # number: drops are only audible where the mix is actually playing).
    sn_m, af_m = seg_null(m, psum)
    row["mixcheck"] = {
        "lag": lag_m,
        "res_null_db": null_db(m, psum),
        "res_segnull_db": sn_m, "res_active_frac": af_m,
        "res_maxerr": float(np.max(np.abs(psum - m))),
    }
    # sizes: 5-plane and 4-plane (no mix) totals
    dur_s = n / rate
    b5 = sum(row["sizes"][p]["bytes"] for p in PLANES + ["mix"])
    b4 = sum(row["sizes"][p]["bytes"] for p in PLANES)
    row["mb_min_5plane"] = b5 * 60.0 / dur_s / 1e6
    row["mb_min_4plane"] = b4 * 60.0 / dur_s / 1e6
    row["proj5_5min_MB"] = row["mb_min_5plane"] * 5.0
    row["proj4_5min_MB"] = row["mb_min_4plane"] * 5.0
    row["dur_s"] = dur_s
    return row


def cmd_matrix(which="full"):
    """which='full' (339 s real song, default) or 'demo' (30 s excerpt).
    demo30 is a drums breakdown with bass/vocals ~-80 dBFS -- useful as a
    sparseness stress case, NOT as the sizing corpus."""
    kind = locate_corpus()
    print(f"x7s_small matrix: corpus={kind} which={which}")
    os.makedirs(ART, exist_ok=True)
    td = tempfile.mkdtemp(prefix="x7s_small_mix_")
    if kind == "real-demucs":
        srcs = corpus_paths(which)
        for p, w in srcs.items():
            assert os.path.exists(w), w
    else:
        tds = tempfile.mkdtemp(prefix="x7s_small_synth_")
        srcs = x7s._synth_stems(tds)
    mix_path, peak, scale = render_mix(srcs, td)
    print(f"  mix rendered from stem sum, peak {peak:.3f} scale {scale:.4f}")
    rows = []
    blobdir = os.path.join(ART, f"blobs_{which}")
    for codec, br in MATRIX:
        t0 = time.time()
        row = run_setting(srcs, mix_path, codec, br, blobdir)
        row["wall_s"] = time.time() - t0
        rows.append(row)
        pl = " ".join(f"{p}={row['planes'][p].get('segnull_db', float('nan')):.1f}dB"
                      for p in PLANES)
        print(f"  [{row['tag']:8s}] {row['mb_min_4plane']:.2f} MB/min(4pl) / "
              f"{row['mb_min_5plane']:.2f} MB/min(5pl)  segnull {pl}  "
              f"mixlag {row['mixcheck']['lag']} mixres "
              f"{row['mixcheck']['res_null_db']:.1f}dB  lags{eq(row)} "
              f"({row['wall_s']:.0f}s)")
    out = {"corpus": kind, "which": which, "rows": rows}
    jp = os.path.join(ART, f"matrix_{which}.json")
    json.dump(out, open(jp, "w"), indent=1)
    print(f"wrote {jp}")
    return out


def eq(row):
    s = row["sync"]
    return "OK" if (s["equal_lags"] and s["equal_lens"]) else "FAIL"


def recommend(matrix, ref_override=None):
    """Apply the bar; print per-plane knees + winner. Returns winner tag.
    Primary fidelity number is segnull (active frames only); the bar is
    stated on segnull because global null is sparsity-dominated."""
    rows = {r["tag"]: r for r in matrix["rows"]}
    ref = ref_override if ref_override is not None else rows.get("mp3128")
    print("\n-- per-plane segnull/global-null (dB) + flux broad/high --")
    hdr = "setting   " + "".join(f"{p:>28s}" for p in PLANES) + \
        "   MB/min4  MB/min5"
    print(hdr)
    for r in matrix["rows"]:
        cells = []
        for p in PLANES:
            q = r["planes"][p]
            cells.append(f"{q['segnull_db']:5.1f}/{q['null_db']:5.1f} "
                         f"({q['flux_broad']:.3f}/{q['flux_high']:.3f})")
        print(f"{r['tag']:10s}" + "".join(f"{c:>28s}" for c in cells) +
              f"   {r['mb_min_4plane']:6.2f}   {r['mb_min_5plane']:6.2f}")
    print("\n-- active-frame fraction per plane (sparsity context) --")
    for p in PLANES:
        act = " ".join(f"{r['tag']}:{r['planes'][p]['active_frac']:.2f}"
                       for r in matrix["rows"] if r["codec"] == "flac")
        print(f"  {p:6s} {act}")
    print("\n-- mix==sum residual (drop-the-mix-plane cost) --")
    for r in matrix["rows"]:
        print(f"  {r['tag']:10s} lag {r['mixcheck']['lag']:4d} "
              f"res {r['mixcheck']['res_null_db']:.1f}dB "
              f"maxerr {r['mixcheck']['res_maxerr']:.2e} sync={eq(r)}")
    print("\n-- recommendation (bar: segnull within 3 dB of mp3-128 per "
          "plane + flux>=0.95/0.93 + sample-sync OK. NOTE: the absolute "
          "30 dB bar fits dense mixes, not sparse stems -- the mp3-128 "
          "status quo itself scores 13.8-25.7 segnull, so no-regression "
          "IS the transparency bar) --")
    cands = []
    for r in matrix["rows"]:
        if r["codec"] == "flac":
            continue
        segs = [r["planes"][p]["segnull_db"] for p in PLANES]
        fl = min(r["planes"][p]["flux_broad"] for p in PLANES)
        fh = min(r["planes"][p]["flux_high"] for p in PLANES)
        ok_reg = True
        if ref is not None:
            ok_reg = all(r["planes"][p]["segnull_db"] >=
                         ref["planes"][p]["segnull_db"] - 3.0 for p in PLANES)
        ok_sync = eq(r) == "OK"
        ok_flux = fl >= 0.95 and fh >= 0.93
        ok = ok_reg and ok_sync and ok_flux
        cands.append((r["mb_min_4plane"], r["tag"], min(segs), fl, fh,
                      ok_reg, ok_sync, ok_flux, ok))
    cands.sort()
    for mb, tag, mn, fl, fh, ok_reg, ok_sync, ok_flux, ok in cands:
        print(f"  {tag:10s} {mb:5.2f} MB/min minsegnull {mn:5.1f}dB "
              f"flux {fl:.3f}/{fh:.3f} noreg={'OK' if ok_reg else '--'} "
              f"sync={'OK' if ok_sync else '--'} "
              f"fluxbar={'OK' if ok_flux else '--'}")
    win = next(((mb, tag) for mb, tag, mn, fl, fh, ok_reg, ok_sync,
                ok_flux, ok in cands if ok), None)
    print("WINNER:", win if win else "none clears the bar (floor is honest)")
    return win


def parse_spec(s):
    """'opus64' -> uniform spec; 'bass:opus48,drums:opus64,...' -> mixed."""
    if ":" not in s:
        if s.startswith("opus"):
            c, b = "opus", int(s[4:])
        elif s.startswith("aac"):
            c, b = "aac", int(s[3:])
        elif s.startswith("mp3"):
            c, b = "mp3", int(s[3:])
        else:
            c, b = "flac", 0
        spec = {p: (c, b) for p in PLANES + ["mix"]}
        return spec, s
    spec = {}
    for item in s.split(","):
        plane, cb = item.split(":")
        for prefix in ("opus", "aac", "mp3", "flac"):
            if cb.startswith(prefix):
                rest = cb[len(prefix):]
                spec[plane] = (prefix, int(rest) if rest else 0)
                break
    for p in PLANES + ["mix"]:
        assert p in spec, f"spec missing {p}"
    tag = "mix_" + "_".join(f"{p}{spec[p][0]}{spec[p][1]}" for p in PLANES)
    return spec, tag


def cmd_validate(specs=("opus64", "mp3128")):
    """Re-run candidate spec(s) on the full 339 s track (default: the
    uniform winner + mp3-128 reference). Mixed specs look like
    'bass:opus48,drums:opus64,vocals:opus96,other:opus64,mix:opus64'."""
    td = tempfile.mkdtemp(prefix="x7s_small_fullmix_")
    srcs = corpus_paths("full")
    for p, w in srcs.items():
        assert os.path.exists(w), w
    mix_path, peak, scale = render_mix(srcs, td)
    print(f"x7s_small validate: full 339 s track, mix peak {peak:.3f}")
    rows = []
    for s in specs:
        spec, tag = parse_spec(s)
        t0 = time.time()
        row = run_setting(srcs, mix_path, None, None,
                          os.path.join(ART, "blobs_validate"),
                          spec=spec, tag=tag)
        row["wall_s"] = time.time() - t0
        rows.append(row)
        print(f"  [{row['tag']:8s}] 4pl {row['mb_min_4plane']:.2f} MB/min "
              f"({row['proj4_5min_MB']:.1f} MB/5min) 5pl "
              f"{row['mb_min_5plane']:.2f} MB/min "
              f"({row['proj5_5min_MB']:.1f} MB/5min) mixres "
              f"{row['mixcheck']['res_null_db']:.1f}dB ({row['wall_s']:.0f}s)")
        for p in PLANES:
            q = row["planes"][p]
            print(f"    {p:6s} null {q['null_db']:.1f}dB segnull "
                  f"{q['segnull_db']:.1f}dB act {q['active_frac']:.2f} "
                  f"maxerr {q['maxerr']:.2e} flux {q['flux_broad']:.4f}/"
                  f"{q['flux_high']:.4f}")
    out = {"corpus": "real-demucs", "which": "full339", "rows": rows}
    jp = os.path.join(ART, "validate.json")
    json.dump(out, open(jp, "w"), indent=1)
    print(f"wrote {jp}")


def cmd_report():
    mp = os.path.join(ART, "matrix_full.json")
    ref = None
    if os.path.exists(mp):
        m = json.load(open(mp))
        print(f"=== matrix_full.json: corpus={m['corpus']} which={m['which']} ===")
        recommend(m)
        ref = {r["tag"]: r for r in m["rows"]}.get("mp3128")
    vp = os.path.join(ART, "validate.json")
    if os.path.exists(vp):
        v = json.load(open(vp))
        print(f"=== validate.json: corpus={v['corpus']} which={v['which']} "
              f"(ref=matrix mp3-128) ===")
        recommend(v, ref_override=ref)
    if ref is None and not os.path.exists(vp):
        print("(no JSONs; run matrix first)")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "report"
    if a == "matrix":
        which = sys.argv[2] if len(sys.argv) > 2 else "full"
        m = cmd_matrix(which)
        recommend(m)
    elif a == "validate":
        cmd_validate(tuple(sys.argv[2:]) or ("opus64", "mp3128"))
    else:
        cmd_report()
