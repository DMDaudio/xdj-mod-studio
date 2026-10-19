#!/usr/bin/env python3
"""x7k_arch_c.py -- ARCHITECTURE CANDIDATE C: PSOLA pitch-mark sidecar.

Hypothesis: precompute pitch marks (glottal-epoch estimates) + voicing
flags once (tiny data); the player does TD-PSOLA resynthesis for any
shift, cheap (no per-shift audio stored, no DSP).

Prototype (honest test, music clip):
  (1) pitch-mark extraction: autocorrelation/YIN-style per frame on the
      mono mix -> period + voicing + transient flag, compact 2 B/frame
      sidecar ("X7KC1").
  (2) TD-PSOLA resynthesis at +2 and -3 semitones: re-space
      pitch-synchronous Hann windows (length 2T centred on analysis
      marks) at synthesis spacing T/p, overlap-add with weight
      normalisation; unvoiced/transient frames passed through (direct
      copy). Stereo channels share the mono-derived marks.
  (3) measure vs rubberband CLI (v4) renders: sidecar bytes + projected
      KB/min, pitch cents (x7k.peak_freq AND autocorrelation f0 on a
      voiced window), duration exactness, null dB vs rubberband,
      encode/decode wall time, peak RAM, periodicity-breakup metric.

Prediction under test: TD-PSOLA assumes a single periodic source and
will fail on polyphonic music. If so we PROVE it (null dB, breakup
fraction, tuning error) instead of hand-waving.

Rules: reads /tmp/demo30.wav only (never Rekordbox audio); artifacts in
/tmp/x7k_arch_c/; no servers, no deck.

Usage:
  .venv/bin/python tools/x7k_arch_c.py [--shifts +2,-3]
"""
import json
import os
import resource
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, "tools")
import x7k  # noqa: E402  (peak_freq tuning check)

SRC = "/tmp/demo30.wav"
OUTDIR = "/tmp/x7k_arch_c"
MAGIC = b"X7KC"
VERSION = 1

# Analysis parameters (fixed for this prototype; stored in sidecar header)
FRM = 4096        # analysis frame length (samples)
HOP = 512         # analysis hop (samples) -> ~187.5 frames/s @96k
FMIN = 50.0       # f0 search floor (Hz)
FMAX = 1200.0     # f0 search ceiling (Hz)
VOICE_CONF = 0.55  # normalised-AC peak threshold for voiced
RMS_GATE = 1e-4    # below this RMS a frame is forced unvoiced (-80 dBFS)
FLUX_MAD_K = 3.5   # transient if flux > median + K*MAD (robust onset pick)


def run(cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def decode_mono_stereo(path):
    """Decode to float32 stereo (nsamp, 2) + rate via ffmpeg."""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True)
    rate, ch = (int(x) for x in r.stdout.strip().split(","))
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", str(rate),
         "-ac", "2", "-f", "f32le", "-acodec", "pcm_f32le", "-"],
        capture_output=True, check=True).stdout
    pcm = np.frombuffer(raw, np.float32).reshape(-1, 2)
    return pcm, rate


def write_wav(path, pcm_stereo, rate):
    run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(rate),
         "-ac", "2", "-i", "-", "-c:a", "pcm_s16le", path],
        input=np.ascontiguousarray(pcm_stereo, dtype=np.float32).tobytes())


def rss_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return r / 1e6  # bytes on macOS
    return r / 1e3  # kilobytes on Linux


def extract(mono, rate):
    """Per-frame period/voicing/transient. Returns dict of arrays."""
    n = len(mono)
    starts = np.arange(0, max(n - FRM + 1, 1), HOP, dtype=np.int64)
    nf = len(starts)
    lo = max(int(rate / FMAX), 2)
    hi = min(int(rate / FMIN), FRM - 2)
    nfft = 8192
    periods = np.zeros(nf, dtype=np.int32)
    confs = np.zeros(nf, dtype=np.float64)
    rms = np.zeros(nf, dtype=np.float64)
    flux = np.zeros(nf, dtype=np.float64)
    prev_logmag = None
    for i, s in enumerate(starts):
        seg = mono[s:s + FRM].astype(np.float64)
        seg -= seg.mean()
        e = float(np.sqrt((seg ** 2).mean()))
        rms[i] = e
        if e < 1e-12:
            continue
        spec = np.fft.rfft(seg, nfft)
        mag = np.abs(spec)
        # spectral-flux (transient detector) from the same spectrum
        logmag = np.log1p(mag)
        if prev_logmag is not None:
            flux[i] = float(np.maximum(0.0, logmag - prev_logmag).sum())
        prev_logmag = logmag
        # normalised autocorrelation via power spectrum
        ac = np.fft.irfft(np.abs(spec) ** 2, nfft)[:FRM]
        if ac[0] <= 0:
            continue
        ac /= ac[0]
        k = lo + int(np.argmax(ac[lo:hi]))
        if 0 < k < FRM - 1:
            a, b, c = ac[k - 1], ac[k], ac[k + 1]
            denom = a - 2 * b + c
            d = 0.5 * (a - c) / denom if abs(denom) > 1e-12 else 0.0
            d = float(np.clip(d, -1.0, 1.0))
            periods[i] = int(round(k + d))
            confs[i] = float(b)
        else:
            periods[i] = k
            confs[i] = float(ac[k])
    med = float(np.median(flux[1:])) if nf > 2 else 0.0
    mad = float(np.median(np.abs(flux[1:] - med))) if nf > 2 else 0.0
    thr = med + FLUX_MAD_K * mad
    transient = flux > max(thr, 1e-9)
    voiced = (confs >= VOICE_CONF) & (rms >= RMS_GATE) & (~transient)
    voiced[0] = False  # flux[0] has no predecessor; keep boundary safe
    return {"starts": starts, "period": periods, "conf": confs,
            "rms": rms, "flux": flux, "flux_med": med, "flux_thr": thr,
            "voiced": voiced, "transient": transient}


def pack_sidecar(path, rate, ana):
    nf = len(ana["starts"])
    codes = np.zeros(nf, dtype=np.uint16)
    v = ana["voiced"].astype(np.uint16)
    t = ana["transient"].astype(np.uint16)
    p = np.clip(ana["period"], 0, 0x3FFF).astype(np.uint16)
    codes = (v << 15) | (t << 14) | p
    hdr = struct.pack("<4sHIIii", MAGIC, VERSION, rate, HOP, FRM, nf)
    open(path, "wb").write(hdr + codes.tobytes())
    return os.path.getsize(path)


def breakup_metrics(ana):
    v = ana["voiced"]
    p = ana["period"].astype(np.float64)
    both = v[:-1] & v[1:] & (p[:-1] > 0) & (p[1:] > 0)
    n_both = int(both.sum())
    if n_both:
        jumps = 1200 * np.log2(p[1:][both] / p[:-1][both])
        unstable = np.abs(jumps) > 100.0
        octave = (np.abs(jumps) > 1100.0) & (np.abs(jumps) < 1300.0)
        f_unstable = float(unstable.mean())
        f_octave = float(octave.mean())
        med_jump = float(np.median(np.abs(jumps)))
    else:
        f_unstable = f_octave = med_jump = float("nan")
    flips = float((v[1:] != v[:-1]).mean()) if len(v) > 1 else float("nan")
    return {"voiced_frac": float(v.mean()),
            "transient_frac": float(ana["transient"].mean()),
            "median_conf": float(np.median(ana["conf"])),
            "median_conf_voiced": float(np.median(ana["conf"][v]))
            if v.any() else float("nan"),
            "both_voiced_pairs": n_both,
            "unstable_frac": f_unstable,   # |dperiod| > 100 cents
            "octave_slip_frac": f_octave,  # 1100-1300 cents jump
            "median_abs_jump_cents": med_jump,
            "voicing_flip_frac": flips}


def build_marks(n, ana):
    """Analysis pitch-mark positions: voiced -> local period, else HOP."""
    v = ana["voiced"]
    p = ana["period"]
    marks = []
    pos = 0
    while pos < n:
        fi = min(pos // HOP, len(v) - 1)
        step = int(p[fi]) if (v[fi] and p[fi] > 0) else HOP
        step = max(step, 8)
        marks.append(pos)
        pos += step
    return np.array(marks, dtype=np.int64)


def psola_resynth(stereo, rate, ana, semis):
    """TD-PSOLA pitch shift, duration preserved (output len == input len)."""
    p = 2.0 ** (semis / 12.0)
    n, ch = stereo.shape
    v = ana["voiced"]
    per = ana["period"]
    marks = build_marks(n, ana)
    tmax = int(max(per.max(), HOP)) + HOP
    pad = np.zeros((tmax, ch), dtype=np.float64)
    xin = np.vstack([pad, stereo.astype(np.float64), pad])
    base = tmax  # xin index of input sample 0
    out = np.zeros((n + 2 * tmax, ch), dtype=np.float64)
    wsum = np.zeros(n + 2 * tmax, dtype=np.float64)
    o0 = tmax  # out index of output sample 0
    synth = 0
    ptr = 0
    nm = len(marks)
    while synth < n:
        while ptr + 1 < nm and marks[ptr + 1] <= synth:
            ptr += 1
        a = int(marks[ptr])
        fi = min(a // HOP, len(v) - 1)
        if (not v[fi]) or per[fi] <= 0:
            h = min(HOP, n - synth)
            out[o0 + synth:o0 + synth + h] += xin[base + synth:base + synth + h]
            wsum[o0 + synth:o0 + synth + h] += 1.0
            synth += h
        else:
            T = int(per[fi])
            Ts = max(8, int(round(T / p)))
            L = 2 * T
            seg = xin[base + a - T:base + a + T]
            if len(seg) < L:  # input tail: zero-pad (should not happen)
                seg = np.pad(seg, ((0, L - len(seg)), (0, 0)))
            w = np.hanning(L)
            lo, hi = o0 + synth - T, o0 + synth + T
            clo, chi = max(lo, 0), min(hi, len(out))
            if chi > clo:
                s0 = clo - lo
                out[clo:chi] += seg[s0:s0 + (chi - clo)] * w[s0:s0 + (chi - clo), None]
                wsum[clo:chi] += w[s0:s0 + (chi - clo)]
            synth += Ts
    m = wsum > 1e-3
    out[m] /= wsum[m, None]
    return np.clip(out[o0:o0 + n], -1.0, 1.0).astype(np.float32)


def xcorr_lag(ref, dec, maxlag=8192):
    n = 1 << (len(ref) + len(dec) - 1).bit_length()
    c = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(dec, n)), n)
    c = np.concatenate([c[-(maxlag + 1):], c[:maxlag + 1]])
    return (maxlag + 1) - int(np.argmax(c))


def null_db(ref, dec, rate, halfwin_s=3.0):
    m = np.asarray(ref, dtype=np.float64)
    d = np.asarray(dec, dtype=np.float64)
    hw = int(halfwin_s * rate)
    seg = slice(len(m) // 2 - hw, len(m) // 2 + hw)
    lag = xcorr_lag(m[seg], d[seg])
    e = int(2.0 * rate)
    if lag >= 0:
        a, b = m[e:len(m) - e], d[e + lag:len(m) - e + lag]
    else:
        a, b = m[e - lag:len(m) - e - lag], d[e:len(m) - e]
    k = min(len(a), len(b))
    a, b = a[:k], b[:k]
    res = b - a
    db = 20 * float(np.log10(np.sqrt((res ** 2).mean())
                             / (np.sqrt((a ** 2).mean()) + 1e-18) + 1e-18))
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
    """Onset-envelope correlation (transient preservation), full + hi-band."""
    out = {}
    for key, fmin in (("full", 0.0), ("hi", 5000.0)):
        fr = onset_env(ref, rate, fmin=fmin)
        fd = onset_env(dec, rate, fmin=fmin)
        e = int(2.0 * rate / 1024)
        fr, fd = fr[e:-e], fd[e:-e]
        best = -2.0
        for s in range(-8, 9):
            if s >= 0:
                r = pearson(fr[:len(fr) - s or None], fd[s:len(fr)])
            else:
                r = pearson(fr[-s:len(fr)], fd[:len(fr) + s or None])
            best = max(best, r)
        out[key] = best
    return out["full"], out["hi"]


def global_tuning(src_mono, tst_mono, rate, ana, semis, step=7):
    """f0 cents error of tst vs pitch-expected source, over strong-voiced
    source frames. Returns (median, med_abs, frac_within_50c, n)."""
    v = ana["voiced"] & (ana["conf"] > 0.6)
    idx = np.where(v)[0][::step]
    errs = []
    for i in idx:
        s = int(i * HOP)
        if s + FRM > len(tst_mono):
            continue
        f, _ = f0_autocorr(tst_mono[s:s + FRM], rate)
        w, _ = f0_autocorr(src_mono[s:s + FRM], rate)
        e = cents(f, w * 2.0 ** (semis / 12.0))
        if np.isfinite(e) and abs(e) < 1200.0:
            errs.append(e)
    errs = np.array(errs)
    if not len(errs):
        return float("nan"), float("nan"), float("nan"), 0
    return (float(np.median(errs)), float(np.median(np.abs(errs))),
            float((np.abs(errs) < 50.0).mean()), len(errs))


def f0_autocorr(seg, sr, fmin=50.0, fmax=1200.0):
    x = np.asarray(seg, dtype=np.float64)
    x -= x.mean()
    if float((x ** 2).mean()) < 1e-12:
        return 0.0, 0.0
    n = 1 << (2 * len(x) - 1).bit_length()
    ac = np.fft.irfft(np.abs(np.fft.rfft(x, n)) ** 2, n)[:len(x)]
    ac /= ac[0] + 1e-18
    lo, hi = max(int(sr / fmax), 2), min(int(sr / fmin), len(x) - 2)
    if hi <= lo + 1:
        return 0.0, 0.0
    k = lo + int(np.argmax(ac[lo:hi]))
    if 0 < k < len(ac) - 1:
        a, b, c = ac[k - 1], ac[k], ac[k + 1]
        d = 0.5 * (a - c) / (a - 2 * b + c + 1e-18)
        return sr / (k + d), float(b)
    return sr / k, float(ac[k])


def find_voiced(mono, sr, win=0.35, skip=2.0):
    import math
    n = int(win * sr)
    hop = n // 2
    lo = int(skip * sr)
    best = (0.0, lo)
    for s in range(lo, len(mono) - n - lo, hop):
        f0, conf = f0_autocorr(mono[s:s + n], sr)
        e = float(np.sqrt((mono[s:s + n] ** 2).mean()))
        score = conf * math.log1p(e * 20)
        if score > best[0]:
            best = (score, s)
    return best[1], best[1] + n


def cents(f, want):
    return 1200 * float(np.log2(f / want)) if f > 0 and want > 0 else float("nan")


def main():
    import re
    shifts = [+2, -3]
    for a in sys.argv[1:]:
        m = re.match(r"--shifts=(.*)", a)
        if m:
            shifts = [int(x) for x in m.group(1).split(",")]
    os.makedirs(OUTDIR, exist_ok=True)
    t_all = time.perf_counter()

    print(f"source: {SRC}", flush=True)
    stereo, rate = decode_mono_stereo(SRC)
    dur = len(stereo) / rate
    mono = stereo.mean(axis=1).astype(np.float64)
    print(f"  {rate} Hz stereo {dur:.2f}s ({len(stereo)} samples/ch)", flush=True)
    ram_after_decode = rss_mb()

    # (1) extraction = ENCODE
    t0 = time.perf_counter()
    ana = extract(mono, rate)
    sc_path = os.path.join(OUTDIR, "marks.x7kc")
    sc_bytes = pack_sidecar(sc_path, rate, ana)
    t_enc = time.perf_counter() - t0
    ram_after_enc = rss_mb()
    nf = len(ana["starts"])
    kbmin = sc_bytes / (dur / 60.0) / 1024.0
    bk = breakup_metrics(ana)
    print(f"encode: {nf} frames (N={FRM} hop={HOP} @ {rate} Hz)", flush=True)
    print(f"  sidecar {sc_path}: {sc_bytes} B "
          f"({sc_bytes / nf:.2f} B/frame incl header, "
          f"projected {kbmin:.1f} KB/min)", flush=True)
    print(f"  voiced {bk['voiced_frac'] * 100:.1f}% | transient "
          f"{bk['transient_frac'] * 100:.1f}% | median conf "
          f"{bk['median_conf']:.2f} (voiced {bk['median_conf_voiced']:.2f})",
          flush=True)
    print(f"  BREAKUP: {bk['unstable_frac'] * 100:.1f}% of {bk['both_voiced_pairs']} "
          f"voiced-voiced adjacencies jump >100 cents "
          f"(octave slips {bk['octave_slip_frac'] * 100:.1f}%, "
          f"median |jump| {bk['median_abs_jump_cents']:.0f}c, "
          f"voicing flips {bk['voicing_flip_frac'] * 100:.1f}%)", flush=True)
    print(f"  encode wall {t_enc:.1f}s, peak RAM {ram_after_enc:.0f} MB", flush=True)

    # voiced probe window (fixed on the SOURCE; reused for every version)
    s0, s1 = find_voiced(mono, rate)
    f0_src, c_src = f0_autocorr(mono[s0:s1], rate)
    print(f"voiced probe [{s0 / rate:.1f},{s1 / rate:.1f}]s: "
          f"f0_src={f0_src:.1f} Hz conf={c_src:.2f}", flush=True)

    results = {"rate": rate, "dur_s": dur, "nsamp": len(stereo),
               "sidecar_bytes": sc_bytes, "sidecar_path": sc_path,
               "frames": nf, "kb_per_min": kbmin,
               "encode_s": t_enc, "breakup": bk,
               "probe": {"s0": s0, "s1": s1, "f0_src": f0_src,
                         "conf_src": c_src},
               "shifts": {}}
    # (2)+(3) per shift: rubberband reference, PSOLA, metrics
    for s in shifts:
        tag = f"p{s:+d}"
        print(f"== shift {s:+d} ==", flush=True)
        # reference render (not counted in decode time)
        rb_path = os.path.join(OUTDIR, f"rb_{tag}.wav")
        t0 = time.perf_counter()
        run(["rubberband", "-q", "-3", "-p", str(s), SRC, rb_path])
        t_rb = time.perf_counter() - t0
        rb, _ = decode_mono_stereo(rb_path)
        rb_m = rb.mean(axis=1).astype(np.float64)
        # PSOLA resynthesis = DECODE
        t0 = time.perf_counter()
        ps = psola_resynth(stereo, rate, ana, s)
        t_dec = time.perf_counter() - t0
        ps_path = os.path.join(OUTDIR, f"psola_{tag}.wav")
        write_wav(ps_path, ps, rate)
        ps_m = ps.mean(axis=1).astype(np.float64)
        # duration exactness
        d_ps = (len(ps) - len(stereo)) / rate
        d_rb = (len(rb) - len(stereo)) / rate
        # null of PSOLA vs rubberband (aligned) + calibrations
        ndb, lag = null_db(rb_m, ps_m, rate)
        ndb_base, _ = null_db(rb_m, mono[:len(rb_m)], rate)  # shifted-vs-src
        ndb_self, _ = null_db(rb_m, rb_m, rate)               # sanity (~-inf)
        fl_full, fl_hi = flux_corr(rb_m, ps_m, rate)
        c_al = float(np.corrcoef(
            rb_m[1000:len(ps_m) - 1000], ps_m[1000:len(ps_m) - 1000])[0, 1])
        rms_ratio = float(np.sqrt((ps_m ** 2).mean())
                          / (np.sqrt((rb_m ** 2).mean()) + 1e-18))
        peak_ps = float(np.abs(ps_m).max())
        nclip = int((np.abs(ps) >= 1.0).sum())
        g_rb = global_tuning(mono, rb_m, rate, ana, s)
        g_ps = global_tuning(mono, ps_m, rate, ana, s)
        # tuning: same probe window, lag-compensated per version
        want = f0_src * 2.0 ** (s / 12.0)
        win_src = mono[s0:s1]
        lag_rb = xcorr_lag(win_src, rb_m[s0:s1])
        lag_ps = xcorr_lag(win_src, ps_m[s0:s1])
        f_rb, c_rb = f0_autocorr(rb_m[s0 + lag_rb:s1 + lag_rb], rate)
        f_ps, c_ps = f0_autocorr(ps_m[s0 + lag_ps:s1 + lag_ps], rate)
        c_rb_c = cents(f_rb, want)
        c_ps_c = cents(f_ps, want)
        # spec-mandated peak_freq check on the same window
        pk_src = x7k.peak_freq(win_src.astype(np.float32), rate)
        pk_rb = x7k.peak_freq(rb_m[s0 + lag_rb:s1 + lag_rb].astype(np.float32), rate)
        pk_ps = x7k.peak_freq(ps_m[s0 + lag_ps:s1 + lag_ps].astype(np.float32), rate)
        pk_want = pk_src * 2.0 ** (s / 12.0)
        print(f"  rubberband render {t_rb:.0f}s wall; psola decode {t_dec:.1f}s",
              flush=True)
        print(f"  duration: psola {d_ps * 1000:+.1f} ms "
              f"({len(ps)} vs {len(stereo)} samp) | "
              f"rubberband {d_rb * 1000:+.1f} ms", flush=True)
        print(f"  NULL psola-vs-rb: {ndb:.1f} dB (lag {lag}) "
              f"[baselines: rb-vs-src {ndb_base:.1f} dB, rb-vs-rb "
              f"{ndb_self:.0f} dB]", flush=True)
        print(f"  waveform: corr(rb,ps)={c_al:.3f} rms(ps)/rms(rb)={rms_ratio:.3f} "
              f"peak {peak_ps:.3f} clips {nclip}", flush=True)
        print(f"  onset-env corr vs rb: full {fl_full:.3f} hi-band {fl_hi:.3f} "
              f"(transient preservation)", flush=True)
        print(f"  GLOBAL tuning over strong-voiced frames: rb med {g_rb[0]:+.0f}c "
              f"med|.| {g_rb[1]:.0f}c in50 {g_rb[2] * 100:.0f}% (n={g_rb[3]}) | "
              f"psola med {g_ps[0]:+.0f}c med|.| {g_ps[1]:.0f}c "
              f"in50 {g_ps[2] * 100:.0f}% (n={g_ps[3]})", flush=True)
        print(f"  f0-autocorr: rb {f_rb:.1f} Hz ({c_rb_c:+.0f}c, conf {c_rb:.2f}) | "
              f"psola {f_ps:.1f} Hz ({c_ps_c:+.0f}c, conf {c_ps:.2f}) "
              f"[want {want:.1f} Hz]", flush=True)
        print(f"  peak_freq (x7k): src {pk_src:.1f} | rb {pk_rb:.1f} "
              f"({cents(pk_rb, pk_want):+.0f}c) | psola {pk_ps:.1f} "
              f"({cents(pk_ps, pk_want):+.0f}c) [want {pk_want:.1f}]",
              flush=True)
        results["shifts"][str(s)] = {
            "rubberband_wav": rb_path, "psola_wav": ps_path,
            "rubberband_render_s": t_rb, "decode_s": t_dec,
            "dur_psola_ms": d_ps * 1000.0, "dur_rb_ms": d_rb * 1000.0,
            "null_db_vs_rb": ndb, "null_lag": lag,
            "null_baseline_rb_vs_src": ndb_base, "null_self": ndb_self,
            "corr_rb_ps": c_al, "rms_ratio": rms_ratio,
            "peak_psola": peak_ps, "clips": nclip,
            "flux_full": fl_full, "flux_hi": fl_hi,
            "glob_rb": g_rb, "glob_ps": g_ps,
            "f0_rb": f_rb, "f0_rb_cents": c_rb_c, "conf_rb": c_rb,
            "f0_psola": f_ps, "f0_psola_cents": c_ps_c, "conf_psola": c_ps,
            "peak_src": pk_src, "peak_rb": pk_rb,
            "peak_rb_cents": cents(pk_rb, pk_want),
            "peak_psola": pk_ps,
            "peak_psola_cents": cents(pk_ps, pk_want)}
    results["peak_ram_mb"] = rss_mb()
    results["total_wall_s"] = time.perf_counter() - t_all
    results["ram_after_decode_mb"] = ram_after_decode
    results["ram_after_encode_mb"] = ram_after_enc
    json.dump(results, open(os.path.join(OUTDIR, "metrics.json"), "w"),
              indent=1)
    print(f"peak RAM {results['peak_ram_mb']:.0f} MB | "
          f"total wall {results['total_wall_s']:.0f}s", flush=True)
    print(f"artifacts: {OUTDIR}/ "
          f"(marks.x7kc, rb_/psola_ wavs, metrics.json)", flush=True)


if __name__ == "__main__":
    main()
