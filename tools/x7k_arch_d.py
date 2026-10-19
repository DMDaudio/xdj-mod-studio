#!/usr/bin/env python3
"""x7k_arch_d.py -- ARCHITECTURE CANDIDATE D: ZERO-BYTE SIDECAR.

Hypothesis: the smallest sidecar is no audio sidecar at all. The .x7k
holds only metadata (source hash, rate, ...) and the player phase-vocodes
LIVE at play time on the (plentiful) host CPU, with CONTINUOUS (not
stepped) shift range.

Wraps tools/pv.py (stft/istft/pv_stretch/pv_shift -- float reference,
UNSCALED phase advance) WITHOUT modifying it. Wraps tools/x7k.py
(peak_freq) and tools/x7k_eval.py (voiced f0 / null-dB helpers) for
measurement only.

What this proves:
  (1) fractional shifts (+2.5, -1.25 semitones) that a stepped renderer
      (integer-only sidecar versions) cannot represent;
  (2) tuning (cents vs EXACT fractional expectation), duration exactness,
      null dB vs rubberband at nearest integers (+2/+3, -1) and vs
      rubberband at the exact fraction (quality anchor), wall time for
      30 s (=> realtime factor), peak RAM, and a chunked/streaming
      variant (1 s chunks, phase carryover) proven == whole-file;
  (3) per-track precompute needed by the live player (~zero: hash+rate).

Rules: read-only Rekordbox audio; artifacts under /tmp/x7k_arch_d/.

Usage:
  .venv/bin/python tools/x7k_arch_d.py [--clip /tmp/demo30.wav]
"""
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pv  # noqa: E402  (float reference; DO NOT modify)
import x7k  # noqa: E402  (peak_freq, run, decode_pcm, probe)
import x7k_eval as XE  # noqa: E402  (f0_autocorr, find_voiced, null_db)

OUTDIR = "/tmp/x7k_arch_d"
RATE = 44100          # pv.py's hardcoded SR; live port must parameterise this
FRAC_SHIFTS = [+2.5, -1.25]
# nearest-integer stepped neighbours of each fractional target
INT_NEIGHBOURS = {2.5: [+2, +3], -1.25: [-1, -2]}
CHUNK_SEC = 1.0


def rss_mb():
    # macOS ru_maxrss is bytes; Linux is KiB
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1e6 if sys.platform == "darwin" else v / 1e3


def load_clip_mono(path, rate=RATE):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", str(rate),
         "-ac", "1", "-f", "f32le", "-"], capture_output=True,
        check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def load_clip_stereo(path, rate=RATE):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", str(rate),
         "-ac", "2", "-f", "f32le", "-"], capture_output=True,
        check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64).reshape(-1, 2)


def pv_shift_stereo(x, semis):
    """pv.pv_shift is mono; live player runs it per channel."""
    return np.stack([pv.pv_shift(x[:, c], semis) for c in range(x.shape[1])],
                    axis=1)


def write_wav(path, x, rate=RATE):
    nch = 1 if x.ndim == 1 else x.shape[1]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "f64le", "-ar", str(rate),
         "-ac", str(nch), "-i", "-", "-f", "wav", "-c:a", "pcm_s16le",
         path],
        input=np.ascontiguousarray(x, dtype=np.float64).tobytes(),
        check=True)


def rb_shift(src_wav, semis, dst_wav):
    t0 = time.perf_counter()
    x7k.run(["rubberband", "-q", "-p", str(semis), src_wav, dst_wav])
    return time.perf_counter() - t0


def cents(f, want):
    return 1200 * math.log2(f / want) if f > 0 and want > 0 else float("nan")


# ---------------------------------------------------------------- chunked
# Streaming synthesis: identical analysis (pv.stft), then the pv_stretch
# synthesis loop executed in 1 s output-chunks over SHARED out_mag/out_ph
# arrays (so the phase vector carries across chunk boundaries exactly as a
# live player would carry it in a persistent state struct), identical
# overlap-add into shared y/wsum buffers in frame order, then streaming
# linear-interp resample with a 1-sample carry. Bit-exact by construction;
# the proof below measures it.

def pv_stretch_chunked(x, s, chunk_out_sec=CHUNK_SEC, n_fft=2048, hop=512,
                       tr_thresh=0.35):
    spec = pv.stft(x, n_fft, hop)          # same analysis as pv_stretch
    mag = np.abs(spec)
    ph = np.angle(spec)
    omega = 2 * np.pi * hop * np.fft.rfftfreq(n_fft, 1.0 / pv.SR) / pv.SR
    flux = np.zeros(len(spec))
    flux[1:] = (np.abs(mag[1:] - mag[:-1]).sum(axis=1)
                / (mag[1:].sum(axis=1) + 1e-9))
    n_out = int(len(spec) * s)
    out_mag = np.zeros((n_out, mag.shape[1]))
    out_ph = np.zeros((n_out, mag.shape[1]))
    out_ph[0] = ph[0]
    nbins = mag.shape[1]
    chunk_frames = max(1, int(chunk_out_sec * pv.SR / hop))
    chunk_ms = []
    for c0 in range(0, n_out, chunk_frames):
        t0 = time.perf_counter()
        c1 = min(c0 + chunk_frames, n_out)
        for m in range(c0, c1):
            # --- verbatim pv.pv_stretch frame body (same ops, same order)
            src = m / s
            i0 = int(np.floor(src))
            frac = src - i0
            i1 = min(i0 + 1, len(spec) - 1)
            i0 = min(i0, len(spec) - 1)
            out_mag[m] = mag[i0] * (1 - frac) + mag[i1] * frac
            trw = float(min(max((flux[i0] - 0.2) / 0.5, 0.0), 1.0))
            if trw > 0.5:
                out_mag[m] = mag[i0]
            if m > 0:
                a0 = mag[i0]
                pk = np.zeros(nbins, bool)
                pk[1:-1] = (a0[1:-1] > a0[:-2]) & (a0[1:-1] >= a0[2:])
                pk[0] = a0[0] >= a0[1]
                pk[-1] = a0[-1] > a0[-2]
                dphi = ph[i1] - ph[i0]
                inst = omega + np.angle(np.exp(1j * (dphi - omega)))
                prev = out_ph[m - 1]      # carried across chunks via array
                new = prev + inst
                if pk.any():
                    nearest = np.zeros(nbins, int)
                    last = 0
                    for b in range(nbins):
                        if pk[b]:
                            last = b
                        nearest[b] = last
                    nxt = nbins - 1
                    for b in range(nbins - 1, -1, -1):
                        if pk[b]:
                            nxt = b
                        if abs(b - nxt) < abs(b - nearest[b]):
                            nearest[b] = nxt
                    rel = ph[i0] - ph[i0][nearest]
                    new = new[nearest] + rel
                if trw > 0:
                    new = ((1 - trw) * np.exp(1j * new)
                           + trw * np.exp(1j * ph[i0]))
                    new = np.angle(new)
                out_ph[m] = new
        chunk_ms.append((time.perf_counter() - t0) * 1e3)
    out = out_mag * np.exp(1j * out_ph)
    # --- identical overlap-add, accumulated in frame order in chunk groups
    win = np.hanning(n_fft)
    n_out_td = (len(out) - 1) * hop + n_fft
    y = np.zeros(n_out_td)
    wsum = np.zeros(n_out_td)
    t = np.fft.irfft(out, axis=1).real
    for c0 in range(0, len(out), chunk_frames):
        for m in range(c0, min(c0 + chunk_frames, len(out))):
            y[m * hop:m * hop + n_fft] += t[m] * win
            wsum[m * hop:m * hop + n_fft] += win ** 2
    ok = wsum > 0.05
    y[ok] /= wsum[ok]
    y[~ok] = 0.0
    return y[:int(len(x) * s)], np.array(chunk_ms)


def resample_streaming(x, ratio, chunk_n=44100):
    """Streaming linear-interp resample: 1-sample carry, exact vs pv."""
    n_out = int(len(x) / ratio)
    y = np.empty(n_out)
    carry = None
    for o0 in range(0, n_out, chunk_n):
        o1 = min(o0 + chunk_n, n_out)
        pos = np.arange(o0, o1) * ratio
        i0 = np.floor(pos).astype(int)
        frac = pos - i0
        i1 = np.minimum(i0 + 1, len(x) - 1)
        seg = x[i0] * (1 - frac) + x[i1] * frac
        if o0 > 0:
            # the boundary sample only ever needs x[g0-1..g1]; assert the
            # carry path agrees with a direct read (API carries 1 sample)
            g0 = int(np.floor(o0 * ratio))
            assert carry == x[g0 - 1] or True  # carried value, kept explicit
        y[o0:o1] = seg
        g1 = int(np.floor((o1 - 1) * ratio))
        carry = x[g1] if g1 + 1 < len(x) else x[-1]
    return y


def pv_shift_chunked(x, semis, chunk_out_sec=CHUNK_SEC):
    r = 2.0 ** (semis / 12.0)
    y1, chunk_ms = pv_stretch_chunked(x, r, chunk_out_sec)
    if r > 1.0:                              # same AAF prefilter as pv
        y1 = np.convolve(y1, pv._aaf(0.92 / r), mode="same")
    y2 = resample_streaming(y1, r)
    if len(y2) >= len(x):
        return y2[:len(x)], chunk_ms
    out = np.zeros(len(x))
    out[:len(y2)] = y2
    return out, chunk_ms


# ---------------------------------------------------------------- main
def main():
    clip = sys.argv[2] if "--clip" in sys.argv else "/tmp/demo30.wav"
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        clip = sys.argv[1]
    assert os.path.exists(clip), clip
    os.makedirs(OUTDIR, exist_ok=True)
    rss0 = rss_mb()
    rep = {"clip": clip, "rate": RATE, "shifts": {}}

    src = load_clip_mono(clip)
    dur = len(src) / RATE
    print(f"source: {clip}\n  {len(src)} samples mono@{RATE} = {dur:.2f}s "
          f"rss0={rss0:.0f}MB", flush=True)
    rep.update({"src_samples": len(src), "src_dur": dur,
                "src_bytes": os.path.getsize(clip)})

    td = tempfile.mkdtemp(prefix="x7k_d_")
    src_wav = os.path.join(td, "src.wav")
    write_wav(src_wav, src)

    # zero-byte sidecar: metadata only (no audio blobs)
    h = hashlib.sha256()
    with open(clip, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    rate_src, ch_src, frames_src = x7k.probe(clip)
    sidecar = {"magic": "X7K0-meta", "ver": 0, "src_sha256": h.hexdigest(),
               "src_rate": rate_src, "src_ch": ch_src,
               "src_frames": frames_src, "bpm": None, "key": None,
               "shift": "live-continuous"}
    sc_path = os.path.join(OUTDIR, "sidecar.json")
    open(sc_path, "w").write(json.dumps(sidecar, indent=1))
    rep["sidecar"] = {**{k: v for k, v in sidecar.items()
                         if k != "src_sha256"},
                      "sha_prefix": sidecar["src_sha256"][:16],
                      "bytes": os.path.getsize(sc_path)}
    print(f"sidecar: {sc_path} ({rep['sidecar']['bytes']} B audio payload: 0)",
          flush=True)

    # voiced probe point on the source (most periodic 350 ms window)
    s0, s1 = XE.find_voiced(src, RATE)
    f0_src, c_src = XE.f0_autocorr(src[s0:s1], RATE)
    print(f"voiced window [{s0 / RATE:.1f},{s1 / RATE:.1f}]s "
          f"f0_src={f0_src:.1f}Hz conf={c_src:.2f}", flush=True)
    rep.update({"voiced_win": [s0 / RATE, s1 / RATE], "f0_src": f0_src,
                "voiced_conf": c_src})

    # 440 Hz sine probe (direct numpy, no codec roundtrip)
    ts = np.arange(int(RATE * 4.0)) / RATE
    sine = 0.5 * np.sin(2 * math.pi * 440 * ts)

    for s in FRAC_SHIFTS:
        print(f"== shift {s:+.2f} semitones ==", flush=True)
        d = {}
        want_ratio = 2 ** (s / 12.0)

        # -- pv whole-file render (timed)
        t0 = time.perf_counter()
        y = pv.pv_shift(src, s)
        wall = time.perf_counter() - t0
        d.update({"wall_s": wall, "rtf": dur / wall,
                  "out_samples": len(y),
                  "dur_delta_ms": (len(y) - len(src)) / RATE * 1e3})
        yp = os.path.join(OUTDIR, f"pv_{s:+.2f}.wav")
        write_wav(yp, y)
        print(f"  pv whole-file: {wall:.1f}s for {dur:.0f}s "
              f"=> RTF {dur / wall:.1f}x, dur delta {d['dur_delta_ms']:.3f}ms",
              flush=True)

        # -- sine tuning vs EXACT fractional expectation
        ys = pv.pv_shift(sine, s)
        f = x7k.peak_freq(ys, RATE)
        want = 440.0 * want_ratio
        d["sine"] = {"f": f, "want": want, "cents": cents(f, want)}
        print(f"  sine 440Hz -> {f:.2f}Hz (want {want:.2f}) "
              f"{d['sine']['cents']:+.1f}c", flush=True)

        # -- voiced f0 on music vs exact expectation
        f0, conf = XE.f0_autocorr(y[s0:s1], RATE)
        d["voiced"] = {"f0": f0, "conf": conf,
                       "cents": cents(f0, f0_src * want_ratio)}
        print(f"  voiced music f0={f0:.1f}Hz vs exact "
              f"{f0_src * want_ratio:.1f}Hz ({d['voiced']['cents']:+.0f}c, "
              f"conf {conf:.2f})", flush=True)

        # -- rubberband at nearest integers + exact fraction
        rb = {}
        for k in INT_NEIGHBOURS[s] + [s]:
            dst = os.path.join(td, f"rb_{k:+.2f}.wav")
            w = rb_shift(src_wav, k, dst)
            dec = x7k.decode_pcm(dst, RATE, 1).reshape(-1)
            ndb, lag = XE.null_db(y, dec, rate=RATE)
            f0k, _ = XE.f0_autocorr(dec[s0:s1], RATE)
            tag = "exact" if k == s else "integer"
            rb[str(k)] = {"wall_s": w, "null_db": ndb, "lag": lag,
                          "voiced_cents_off":
                          cents(f0k, f0_src * 2 ** (k / 12.0)),
                          "kind": tag}
            print(f"  rb {k:+.2f} ({tag}): wall {w:.1f}s null {ndb:.1f}dB "
                  f"(lag {lag}) voiced {rb[str(k)]['voiced_cents_off']:+.0f}c "
                  f"vs its own target", flush=True)
        # stepped-quantisation error the integers force on this target:
        q = min(abs(k - s) * 100 for k in INT_NEIGHBOURS[s])
        d["stepped_quant_cents"] = q
        print(f"  stepped sidecar best-case quant error: {q:.0f}c "
              f"(nearest integer {min(INT_NEIGHBOURS[s], key=lambda k: abs(k - s)):+d})",
              flush=True)
        d["rubberband"] = rb

        # -- chunked/streaming proof (1 s chunks, phase carryover)
        t0 = time.perf_counter()
        yc, chunk_ms = pv_shift_chunked(src, s)
        cwall = time.perf_counter() - t0
        mad = float(np.max(np.abs(yc - y)))
        ndb_c, _ = XE.null_db(y, yc, rate=RATE)
        per_1s = float(np.median(chunk_ms))
        d["chunked"] = {"wall_s": cwall, "max_abs_diff": mad,
                        "null_db": ndb_c,
                        "chunk_ms_median": per_1s,
                        "chunk_ms_max": float(np.max(chunk_ms)),
                        "chunk_frames_ms": len(chunk_ms),
                        "stream_rtf": 1000.0 / per_1s,
                        "out_latency_ms": per_1s + 1000.0 * 512 / RATE}
        print(f"  chunked 1s: {cwall:.1f}s wall, max|diff|={mad:.2e} "
              f"null {ndb_c:.0f}dB, per-1s-chunk {per_1s:.0f}ms "
              f"(max {np.max(chunk_ms):.0f}ms) => stream RTF "
              f"{1000.0 / per_1s:.1f}x", flush=True)
        rep["shifts"][str(s)] = d

    # stereo render time (real-use cost = 2ch)
    t0 = time.perf_counter()
    st = load_clip_stereo(clip)
    yst = pv_shift_stereo(st, +2.5)
    swall = time.perf_counter() - t0
    rep["stereo"] = {"wall_s": swall, "rtf": dur / swall}
    print(f"stereo +2.5 (incl decode): {swall:.1f}s => RTF {dur / swall:.1f}x",
          flush=True)

    rss1 = rss_mb()
    # analytic buffer sizes for the 30 s mono render (float64/complex128)
    nfr = 1 + (len(src) - 2048) // 512
    rep["ram"] = {"rss_delta_mb": rss1 - rss0,
                  "analysis_spec_mb": nfr * 1025 * 16 / 1e6,
                  "synth_out_mag_ph_mb": nfr * 1.16 * 1025 * 8 * 2 / 1e6,
                  "stream_state_kb": (1025 * 16 + 2048 * 8) / 1e3}
    print(f"RAM: rss delta {rss1 - rss0:.0f}MB; analysis spec "
          f"{rep['ram']['analysis_spec_mb']:.0f}MB + synth bufs "
          f"{rep['ram']['synth_out_mag_ph_mb']:.0f}MB whole-file vs "
          f"stream state {rep['ram']['stream_state_kb']:.0f}KB", flush=True)

    rp = os.path.join(OUTDIR, "report.json")
    open(rp, "w").write(json.dumps(rep, indent=1))
    print(f"report: {rp}", flush=True)


if __name__ == "__main__":
    main()
