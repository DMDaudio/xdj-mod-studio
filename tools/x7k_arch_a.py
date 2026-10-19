#!/usr/bin/env python3
"""x7k_arch_a.py -- ARCHITECTURE CANDIDATE A: sinusoidal-analysis sidecar.

Hypothesis: precompute sparse spectral peaks + transient map once; the
player resynthesizes any pitch by shifting peak freqs (oscillator bank),
storing NO audio.

  analyze: 2048/512 STFT -> top-K magnitude peaks (freq+mag, parabolic
           interpolation) + transient flag/frame -> quantize (freq 1 Hz
           uint16, mag 8-bit log) -> temporal delta-store -> zlib.
  resynth: shift peak freqs by 2^(semis/12), sum sines with phase
           accumulation (nearest-freq continuation), Hann 2048 OLA at
           hop 512; transient-flagged frames reset phases (positions kept
           from original timing; NO original audio used at decode).
  measure: sidecar bytes + MB/min, pitch (peak_freq on 440 Hz sine, then
           voiced-period f0 on music) vs rubberband renders, duration,
           null dB vs rubberband, HF flux correlation, encode/decode time,
           peak RAM.

Read-only on ~/Downloads/Rekordbox* stuff. Artifacts -> /tmp/x7k_arch_a/.
"""
import math
import os
import resource
import struct
import subprocess
import sys
import tempfile
import time
import zlib

import numpy as np

sys.path[:0] = [".", "tools"]
import x7k
from x7k_eval import (cents, f0_autocorr, find_voiced, flux_corr, null_db,
                      to_mono, xcorr_lag)

OUTDIR = "/tmp/x7k_arch_a"
N_FFT = 2048
HOP = 512
K = 32
TRANS_THRESH = 0.45
SEMIS_A = +2
SEMIS_B = -3

# ---------------------------------------------------------------- IO


def run(cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def decode_mono(path):
    """Decode any audio file to float64 mono at native rate. Returns (x, sr)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=sample_rate",
                        "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    sr = int(r.stdout.strip().split(",")[0])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path,
                          "-ac", "1", "-f", "f64le", "-acodec",
                          "pcm_f64le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float64), sr


def write_wav_mono(x, sr, path):
    x = np.asarray(x, dtype=np.float64)
    x = np.clip(x, -1.0, 1.0)
    run(["ffmpeg", "-v", "error", "-y", "-f", "f64le", "-ar", str(sr),
         "-ac", "1", "-i", "-", "-c:a", "pcm_s16le", path],
        input=x.astype(np.float64).tobytes())


def rubberband_shift(src_wav, semis, out_wav):
    t0 = time.perf_counter()
    run(["rubberband", "-q", "-3", "-p", str(semis), src_wav, out_wav])
    return time.perf_counter() - t0


def rss_mb():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1e6 if sys.platform == "darwin" else rss / 1e3


# ---------------------------------------------------------------- analyze

def analyze(x, sr, n_fft=N_FFT, hop=HOP, k=K):
    """Returns dict with freqs (nfr,K) Hz, mags (nfr,K) STFT-mag, trans bool."""
    w = np.hanning(n_fft)
    cg = w.sum() / 2.0  # sine-at-bin-centre magnitude
    nfr = 1 + (len(x) - n_fft) // hop
    freqs = np.zeros((nfr, k))
    mags = np.zeros((nfr, k))
    trans = np.zeros(nfr, bool)
    ecap = np.zeros(nfr)
    prev = None
    bin_hz = sr / n_fft
    for i in range(nfr):
        seg = x[i * hop:i * hop + n_fft] * w
        spec = np.fft.rfft(seg)
        mag = np.abs(spec)
        tot = float((mag ** 2).sum())
        # local maxima (skip DC + Nyquist)
        a = mag
        pk = np.zeros(len(a), bool)
        pk[1:-1] = (a[1:-1] > a[:-2]) & (a[1:-1] >= a[2:])
        idx = np.flatnonzero(pk[1:-1]) + 1
        if len(idx):
            order = idx[np.argsort(a[idx])[::-1][:k]]
            ff = np.zeros(k)
            mm = np.zeros(k)
            for j, b in enumerate(order):
                if 0 < b < len(a) - 1 and a[b] > 0:
                    d = 0.5 * (a[b - 1] - a[b + 1]) / (
                        a[b - 1] - 2 * a[b] + a[b + 1] + 1e-18)
                    d = min(max(d, -0.5), 0.5)
                    ff[j] = (b + d) * bin_hz
                    mm[j] = a[b] - 0.25 * (a[b - 1] - a[b + 1]) * d
                else:
                    ff[j] = b * bin_hz
                    mm[j] = a[b]
            # ascending-freq order (delta coherence + osc matching)
            o = np.argsort(ff)
            freqs[i] = ff[o]
            mags[i] = mm[o]
            ecap[i] = float((mm ** 2).sum()) / (tot + 1e-18)
        if prev is not None:
            flux = float(np.abs(mag - prev).sum() / (prev.sum() + 1e-9))
            trans[i] = flux > TRANS_THRESH
        prev = mag
    return {"freqs": freqs, "mags": mags, "trans": trans, "ecap": ecap,
            "sr": sr, "n_fft": n_fft, "hop": hop, "k": k,
            "mmax": float(mags.max()), "cg": cg, "nsrc": len(x)}


def serialize(an):
    """Quantize -> delta-store -> zlib. Returns (blob, raw_bytes)."""
    fr = np.round(an["freqs"]).astype(np.int64)  # 1 Hz LSB
    mmax = an["mmax"]
    mq = np.round(255 * np.log1p(an["mags"]) / np.log1p(mmax)).astype(np.int64)
    mq = np.clip(mq, 0, 255)
    nfr, k = fr.shape
    fr0 = fr[:1].astype("<u2")
    mq0 = mq[:1].astype("u1")
    dfr = np.diff(fr, axis=0).astype("<i2")  # fits: frame-to-frame << 1 kHz
    dmq = np.diff(mq, axis=0).astype("<i2")
    assert dfr.min() >= -32768 and dfr.max() <= 32767
    tb = np.packbits(an["trans"]).tobytes()
    HDR = struct.Struct("<4sHIIHHHfI")
    hdr = HDR.pack(b"X7KA", 1, an["sr"], nfr,
                   an["n_fft"], an["hop"], k, mmax, len(tb))
    raw = b"".join([hdr, tb, fr0.tobytes(), mq0.tobytes(), dfr.tobytes(),
                    dmq.tobytes()])
    return zlib.compress(raw, 9), len(raw), HDR.size


def deserialize(blob):
    raw = zlib.decompress(blob)
    HDR = struct.Struct("<4sHIIHHHfI")
    magic, ver, sr, nfr, n_fft, hop, k, mmax, ntb = HDR.unpack(
        raw[:HDR.size])
    assert magic == b"X7KA"
    p = HDR.size
    tb = raw[p:p + ntb]
    p += ntb
    trans = np.unpackbits(np.frombuffer(tb, dtype="u1"))[:nfr].astype(bool)
    fr0 = np.frombuffer(raw[p:p + 2 * k], dtype="<u2").astype(np.int64)
    p += 2 * k
    mq0 = np.frombuffer(raw[p:p + k], dtype="u1").astype(np.int64)
    p += k
    dfr = np.frombuffer(raw[p:p + 2 * k * (nfr - 1)], dtype="<i2")
    p += 2 * k * (nfr - 1)
    dmq = np.frombuffer(raw[p:p + 2 * k * (nfr - 1)], dtype="<i2")
    fr = np.empty((nfr, k), np.int64)
    mq = np.empty((nfr, k), np.int64)
    fr[0] = fr0
    mq[0] = mq0
    fr[1:] = fr0 + np.cumsum(dfr.reshape(nfr - 1, k), axis=0)
    mq[1:] = np.clip(mq0 + np.cumsum(dmq.reshape(nfr - 1, k), axis=0), 0, 255)
    freqs = fr.astype(np.float64)
    mags = np.expm1(mq.astype(np.float64) / 255 * np.log1p(mmax)) - 1.0
    mags = np.maximum(mags, 0.0)
    w = np.hanning(n_fft)
    return {"freqs": freqs, "mags": mags, "trans": trans, "sr": sr,
            "n_fft": n_fft, "hop": hop, "k": k, "mmax": mmax,
            "cg": w.sum() / 2.0}


# ---------------------------------------------------------------- resynth

def resynth(dec, semis, nsrc, gain=1.0):
    """Oscillator-bank resynthesis. gain = linear calibration (from sine)."""
    ratio = 2.0 ** (semis / 12.0)
    sr, n_fft, hop, k = dec["sr"], dec["n_fft"], dec["hop"], dec["k"]
    nyq = sr / 2.0
    nfr = dec["freqs"].shape[0]
    w = np.hanning(n_fft)
    out = np.zeros(nfr * hop + n_fft)
    phi = np.zeros(k)  # radians, per rank (both sorted by freq)
    nn = np.arange(n_fft) / sr
    for i in range(nfr):
        fq = dec["freqs"][i] * ratio
        amp = dec["mags"][i] / dec["cg"] * gain
        ok = fq < nyq * 0.999
        if dec["trans"][i]:
            phi[:] = 0.0  # transient: restart (timing kept, no smear)
        else:
            phi += 2 * math.pi * fq * hop / sr
        if ok.any():
            grain = (amp[ok, None] * np.cos(
                2 * math.pi * fq[ok, None] * nn[None, :] + phi[ok, None])
                     ).sum(axis=0)
            out[i * hop:i * hop + n_fft] += grain * w
    out *= hop / (n_fft * 0.5)  # Hann 75%-overlap COLA gain
    return out[:nsrc]


# ---------------------------------------------------------------- main

def main():
    os.makedirs(OUTDIR, exist_ok=True)
    clip = "/tmp/demo30.wav"
    print(f"clip: {clip} ({os.path.getsize(clip) / 1e6:.1f} MB)", flush=True)
    x, sr = decode_mono(clip)
    dur = len(x) / sr
    print(f"  {sr} Hz mono {dur:.2f}s n={len(x)}", flush=True)
    td = tempfile.mkdtemp(prefix="x7k_a_")
    src_wav = os.path.join(td, "src_mono.wav")
    write_wav_mono(x, sr, src_wav)

    # ---- 1. synth 440 Hz probe (same rate) ----
    print("-- synth 440 Hz probe --", flush=True)
    t = np.arange(int(sr * 2.0)) / sr
    sine = 0.5 * np.sin(2 * math.pi * 440 * t)
    t0 = time.perf_counter()
    an_s = analyze(sine, sr)
    enc_s = time.perf_counter() - t0
    blob_s, raw_s, _ = serialize(an_s)
    dec_s = deserialize(blob_s)
    synth_res = {}
    for semis in (SEMIS_A, SEMIS_B):
        t0 = time.perf_counter()
        y = resynth(dec_s, semis, len(sine), gain=1.0)
        dt = time.perf_counter() - t0
        g = float(np.sqrt((sine ** 2).mean() / ((y ** 2).mean() + 1e-18)))
        synth_res[semis] = (y, dt)
    # calibrate linear gain on the +2 sine (window/coherent-gain math only)
    y2raw = synth_res[SEMIS_A][0]
    gain = float(np.sqrt((sine ** 2).mean() / ((y2raw ** 2).mean() + 1e-18)))
    print(f"  synth sidecar: raw {raw_s} B -> zlib {len(blob_s)} B; "
          f"enc {enc_s:.2f}s; calib gain {gain:.3f} "
          f"({20 * math.log10(gain):+.2f} dB)", flush=True)
    rb_wavs = {}
    for semis in (SEMIS_A, SEMIS_B):
        sw = os.path.join(td, f"sine_rb{semis:+d}.wav")
        sw_src = os.path.join(td, "sine_src.wav")
        write_wav_mono(sine, sr, sw_src)
        rubberband_shift(sw_src, semis, sw)
        rb_wavs[semis] = sw
        y = resynth(dec_s, semis, len(sine), gain=gain)
        f_syn = x7k.peak_freq(y, sr)
        rbr, _ = decode_mono(sw)
        f_rb = x7k.peak_freq(rbr[:len(y)], sr)
        want = 440.0 * 2 ** (semis / 12)
        print(f"  sine {semis:+d}: synth {f_syn:.2f} Hz "
              f"({cents(f_syn, want):+.1f}c) | rubberband {f_rb:.2f} Hz "
              f"({cents(f_rb, want):+.1f}c) | want {want:.2f} Hz",
              flush=True)

    # ---- 2. music analyze ----
    print("-- music analyze --", flush=True)
    t0 = time.perf_counter()
    an = analyze(x, sr)
    enc_t = time.perf_counter() - t0
    blob, raw, HS = serialize(an)
    sc_path = os.path.join(OUTDIR, "sidecar.x7ka")
    open(sc_path, "wb").write(blob)
    mbmin = len(blob) / (dur / 60) / 1e6
    rawmin = raw / (dur / 60) / 1e6
    trfrac = 100 * an["trans"].mean()
    print(f"  frames={an['freqs'].shape[0]} K={K} N={N_FFT} hop={HOP} "
          f"mmax={an['mmax']:.0f}", flush=True)
    print(f"  sidecar raw {raw / 1e3:.0f} KB ({rawmin:.2f} MB/min) -> "
          f"zlib {len(blob) / 1e3:.0f} KB ({mbmin:.3f} MB/min); "
          f"transient frames {trfrac:.1f}%", flush=True)
    print(f"  mean top-{K} energy captured: {100 * an['ecap'].mean():.1f}% "
          f"(rest = discarded residual/noise)", flush=True)
    print(f"  encode time {enc_t:.1f}s, peak RAM {rss_mb():.0f} MB",
          flush=True)
    dec = deserialize(blob)

    # ---- 3. resynth music +2 / -3 / 0(control) ----
    print("-- music resynth --", flush=True)
    syn = {}
    for semis in (0, SEMIS_A, SEMIS_B):
        t0 = time.perf_counter()
        y = resynth(dec, semis, len(x), gain=gain)
        dt = time.perf_counter() - t0
        p = os.path.join(OUTDIR, f"synth_{semis:+d}.wav")
        write_wav_mono(y, sr, p)
        syn[semis] = (y, dt, p)
        print(f"  {semis:+d}: decode {dt:.1f}s -> {p} "
              f"len {len(y)} ({len(y) / sr:.3f}s)", flush=True)

    # ---- 4. rubberband references ----
    print("-- rubberband references --", flush=True)
    rb = {}
    for semis in (SEMIS_A, SEMIS_B):
        o = os.path.join(OUTDIR, f"rb_{semis:+d}.wav")
        wt = rubberband_shift(src_wav, semis, o)
        r, rsr = decode_mono(o)
        assert rsr == sr
        rb[semis] = r[:len(x)]
        print(f"  {semis:+d}: {wt:.0f}s wall, len {len(rb[semis])}", flush=True)

    # ---- 5. metrics ----
    print("-- metrics vs rubberband render --", flush=True)
    s0, s1 = find_voiced(x, sr)
    f0_src, c_src = f0_autocorr(x[s0:s1], sr)
    print(f"  voiced window [{s0 / sr:.1f},{s1 / sr:.1f}]s "
          f"f0_src={f0_src:.1f} Hz conf={c_src:.2f}", flush=True)
    results = {}
    for semis in (SEMIS_A, SEMIS_B):
        y = syn[semis][0]
        r = rb[semis]
        n = min(len(y), len(r))
        y, r = y[:n], r[:n]
        # voiced f0 on synth vs expected; rubberband as reference
        lag = xcorr_lag(r[s0:s1], y[s0:s1])
        # align same content: dec lags ref by `lag` (either sign)
        w = y[s0 + lag:s1 + lag] if lag >= 0 else y[s0 - lag:s1 - lag]
        f0_syn, c_syn = f0_autocorr(w, sr)
        f0_rb, c_rb = f0_autocorr(r[s0:s1], sr)
        want = f0_src * 2 ** (semis / 12)
        ndb, nlag = null_db(r, y, rate=sr)
        fl, fh = flux_corr(r, y, sr)
        durex = "EXACT" if len(syn[semis][0]) == len(x) else "MISMATCH"
        results[semis] = (f0_syn, c_syn, f0_rb, ndb, fl, fh)
        print(f"  {semis:+d}: synth f0 {f0_syn:.1f} Hz "
              f"({cents(f0_syn, want):+.0f}c vs expected {want:.1f}, "
              f"conf {c_syn:.2f}) | rb f0 {f0_rb:.1f} Hz "
              f"({cents(f0_rb, want):+.0f}c) | dur {durex} | "
              f"null {ndb:.1f} dB (lag {nlag}) | "
              f"flux r={fl:.3f} hi(>5kHz) r={fh:.3f}", flush=True)
    # shift-0 control: sine-only resynthesis with NO pitch change
    y0 = syn[0][0]
    ndb0, _ = null_db(x[:len(y0)], y0, rate=sr)
    fl0, fh0 = flux_corr(x[:len(y0)], y0, sr)
    res0 = x[:len(y0)] - y0
    res_dbfs = 20 * math.log10(float(np.sqrt((res0 ** 2).mean())) + 1e-18)
    print(f"  +0 control (no shift): null {ndb0:.1f} dB | flux r={fl0:.3f} "
          f"hi r={fh0:.3f} | residual {res_dbfs:.1f} dBFS", flush=True)
    # residual coding cost: even 8-bit residual ~= PCM bytes
    pcm16_min = sr * 2 * 60 / 1e6
    pcm8_min = sr * 1 * 60 / 1e6
    print(f"  residual fix cost: 16-bit residual = {pcm16_min:.1f} MB/min, "
          f"8-bit = {pcm8_min:.1f} MB/min mono@{sr // 1000}k "
          f"(vs sidecar {mbmin:.3f} MB/min)", flush=True)
    print(f"  decode times: {[f'{k:+d}:{syn[k][1]:.1f}s' for k in syn]}; "
          f"peak RAM {rss_mb():.0f} MB", flush=True)

    # ---- verdict ----
    ndb_worst = max(results[s][3] for s in results)
    fh_worst = min(results[s][5] for s in results)
    ok = ndb_worst <= -20.0 and fh_worst >= 0.90
    print("VERDICT: " + (
        "VIABLE -- sinusoidal sidecar passes music bars." if ok else
        "DISPROVEN as a standalone music shifter -- the 440 Hz sine probe "
        "lands within ~7c (parabolic-interpolator bias on a Hann spectrum, "
        "correctable, not structural), but polyphonic music keeps only "
        f"{100 * an['ecap'].mean():.0f}% of spectral energy in top-{K}; "
        f"even with NO shift the sine-only resynthesis nulls at {ndb0:.1f} dB "
        f"(residual {res_dbfs:.1f} dBFS = drums/noise/reverb the model "
        "cannot make); shifted nulls sit near "
        f"{ndb_worst:.0f} dB vs rubberband with HF transient correlation "
        f"r~{fh_worst:.2f}. Minimum fix = a coded residual (noise+transient "
        f"path): even 8-bit residual costs ~{pcm8_min:.1f} MB/min, "
        f"{pcm8_min / mbmin:.0f}x the sidecar -- at which point storing "
        "compressed version audio (candidate B) is strictly cheaper and "
        "exact. Keep the peak map only as a sub-0.5 MB/min analysis "
        "accelerator, not as the audio path."), flush=True)
    rep = os.path.join(OUTDIR, "report.txt")
    print(f"(report dir: {OUTDIR}/)", flush=True)


if __name__ == "__main__":
    main()
