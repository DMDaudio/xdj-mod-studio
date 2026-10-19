#!/usr/bin/env python3
"""x7k_bakeoff.py -- bake off EVERY key-shift method on the same material.

Methods:
  (1) rubberband CLI v4 (reference)
  (2) tools/pv.py pv_shift float
  (3) tools/x7k_arch_d.py pv_shift_chunked (streaming)
  (4) stored-version codecs from tools/x7k.py (mp3-128/192, opus-96/128, aac-128)
  (5) tools/x7k_arch_a.py sinusoidal resynth
  (6) tools/x7k_arch_c.py TD-PSOLA

Material: synth 440 Hz sine (exact cents) + /tmp/demo30.wav 30 s music if
  present else rebuild 30 s from Radio 84 via make_3band.track_path
  (read-only Rekordbox audio).
Shifts: +2 and -3 (plus +2.5 fractional ONLY for methods supporting it).

Metrics per method (via imported helpers only -- no duplicated DSP):
  tuning cents (x7k.peak_freq + voiced-autocorr from x7k_eval),
  duration exactness ms, null dB vs rubberband render (x7k_eval.null_db),
  onset-flux correlation full + HF (x7k_eval.flux_corr),
  render time MB/min + realtime factor, peak RAM (resource module),
  output latency class (offline/chunked/instant), sidecar bytes.

Rules: imports (never duplicates) existing modules; artifacts /tmp/x7k_bakeoff/.
"""
import glob
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
# All DSP/measurement comes from these imports -- nothing reimplemented.
import pv  # pv.pv_shift float reference
import x7k  # peak_freq, build, version_pcm, run, probe
import x7k_arch_a as ARCHA  # analyze/serialize/deserialize/resynth
import x7k_arch_c as ARCHC  # extract/psola_resynth/pack_sidecar
import x7k_arch_d as ARCHD  # pv_shift_chunked
import x7k_eval as XE  # cents, f0_autocorr, find_voiced, null_db, flux_corr
import make_3band  # track_path fallback only

OUTDIR = "/tmp/x7k_bakeoff"
SR = 44100
SHIFTS_INT = [+2, -3]
FRAC = +2.5
CODEC_MATRIX = [("mp3", 128), ("mp3", 192), ("opus", 96), ("opus", 128),
                ("aac", 128)]
SYNTH_DUR = 12.0
SYNTH_F = 440.0


def rss_mb():
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return v / 1e6 if sys.platform == "darwin" else v / 1e3


def run(cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def decode_mono44(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ar", str(SR), "-ac", "1",
         "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def write_mono16(path, x, sr=SR):
    x = np.asarray(x, dtype=np.float64)
    x = np.clip(x, -1.0, 1.0)
    run(["ffmpeg", "-v", "error", "-y", "-f", "f64le", "-ar", str(sr),
         "-ac", "1", "-i", "-", "-c:a", "pcm_s16le", path],
        input=np.ascontiguousarray(x, dtype=np.float64).tobytes())


def find_music_src():
    c = "/tmp/demo30.wav"
    if os.path.exists(c):
        return c, "demo30"
    # fallback: 30 s from Radio 84 via make_3band.track_path (read-only)
    roots = sorted(glob.glob(os.path.expanduser(
        "~/Downloads/Rekordbox stuff/Contents/*/*")))
    # roots are artist/album dirs; ANLZ dirs live deeper -- scan for them
    anlz_dirs = sorted(glob.glob(os.path.expanduser(
        "~/Downloads/Rekordbox stuff/Contents/*/*/*/P*")))
    stick = os.path.expanduser("~/Downloads/Rekordbox stuff")
    for ad in anlz_dirs:
        try:
            tp = make_3band.track_path(stick, ad)
        except Exception:
            continue
        if tp and os.path.isfile(tp) and "Radio 84" in tp:
            return tp, "radio84-fallback"
    # last resort: direct audio glob (still read-only)
    hits = glob.glob(os.path.expanduser(
        "~/Downloads/Rekordbox stuff/Contents/*/*/*Radio 84*"))
    hits = [h for h in hits if os.path.isfile(h)]
    if hits:
        return sorted(hits)[0], "radio84-direct"
    raise SystemExit("no music source found")


def rb_render_to_mem(src_wav, semis, td, tag):
    """Rubberband CLI render of src_wav; returns (mono_float64, wall_s, path)."""
    safe = f"{semis:+.2f}".replace(".", "_")
    dst = os.path.join(td, f"rb_{tag}_{safe}.wav")
    t0 = time.perf_counter()
    if float(semis).is_integer():
        # integer path reuses the reference wrapper (same -q -3 -p flags)
        out = x7k.shift_render(src_wav, int(semis), td, SR, 1)
        os.replace(out, dst)
    else:
        # fractional: same CLI/flags via the shared run() helper (no dup)
        x7k.run(["rubberband", "-q", "-3", "-p", str(semis), src_wav, dst])
    wall = time.perf_counter() - t0
    y = decode_mono44(dst)
    return y, wall, dst


def synth_tuning_both(y, sr, want):
    f_pk = x7k.peak_freq(y.astype(np.float32), sr)
    f0, conf = XE.f0_autocorr(y, sr)
    return (XE.cents(f_pk, want), f_pk,
            XE.cents(f0, want), f0, conf)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    td = tempfile.mkdtemp(prefix="x7k_bakeoff_")
    t_all = time.perf_counter()
    print("== x7k key-shift bakeoff ==", flush=True)
    print(f"cwd={os.getcwd()} out={OUTDIR} tmp={td}", flush=True)
    # CODECS check (extend only if trivially missing -- none missing)
    need = {"mp3", "opus", "aac"}
    missing = need - set(x7k.CODECS)
    print(f"x7k.CODECS={x7k.CODECS} missing={missing or 'none'}", flush=True)
    assert not missing, f"missing codecs: {missing}"

    # ---- material ----
    src_path, src_kind = find_music_src()
    print(f"music src: {src_path} ({src_kind})", flush=True)
    music_full = decode_mono44(src_path)
    # canonical 30 s @44.1k mono
    want_n = int(30.0 * SR)
    if len(music_full) >= want_n:
        music = music_full[:want_n]
    else:  # short fallback track: loop-free pad by repeating? no -- take all
        music = music_full
    music_dur = len(music) / SR
    music_wav = os.path.join(OUTDIR, "src_music_mono44.wav")
    write_mono16(music_wav, music)
    print(f"  music canonical: {len(music)} samp mono@{SR} = {music_dur:.2f}s",
          flush=True)

    t = np.arange(int(SR * SYNTH_DUR)) / SR
    synth = 0.5 * np.sin(2 * math.pi * SYNTH_F * t)
    synth_wav = os.path.join(OUTDIR, "src_synth440.wav")
    write_mono16(synth_wav, synth)
    print(f"  synth: {SYNTH_F} Hz {SYNTH_DUR}s n={len(synth)}", flush=True)

    s0, s1 = XE.find_voiced(music, SR)
    f0_src, c_src = XE.f0_autocorr(music[s0:s1], SR)
    pk_src = x7k.peak_freq(music[s0:s1].astype(np.float32), SR)
    print(f"  voiced window [{s0/SR:.1f},{s1/SR:.1f}]s f0_src={f0_src:.1f}Hz "
          f"conf={c_src:.2f} peak={pk_src:.1f}Hz", flush=True)

    # ---- rubberband references (music + synth, +2/-3/+2.5) ----
    rb = {}  # rb[mat][shift] = np array
    rb_wall = {}
    for mat, wav, x in (("music", music_wav, music),
                        ("synth", synth_wav, synth)):
        rb[mat] = {}
        for s in SHIFTS_INT + [FRAC]:
            y, w, p = rb_render_to_mem(wav, s, td, mat)
            # trim/pad to source len for metric comparability (keep raw len too)
            rb[mat][s] = y
            rb_wall[(mat, s)] = w
            # stash artifact copy
            ap = os.path.join(OUTDIR, f"ref_rb_{mat}_{s:+.2f}.wav")
            run(["cp", p, ap])
            print(f"  rb {mat} {s:+.2f}: wall {w:.1f}s len {len(y)}", flush=True)

    results = {}

    def music_metrics(name, y, s, wall, sidecar_bytes, latency):
        ref = rb["music"][s]
        n = min(len(ref), len(y))
        # duration exactness vs SOURCE (tempo constancy)
        dur_ms = (len(y) - len(music)) / SR * 1e3
        # null + flux vs rubberband render
        ndb, lag = XE.null_db(ref, y, rate=SR)
        fl, fh = XE.flux_corr(ref, y, SR)
        # voiced tuning vs expected
        want = f0_src * 2.0 ** (s / 12.0)
        seg = y[s0:s1] if len(y) >= s1 else y
        f0, conf = XE.f0_autocorr(seg, SR)
        ce_f0 = XE.cents(f0, want)
        fpk = x7k.peak_freq(seg.astype(np.float32), SR)
        ce_pk = XE.cents(fpk, pk_src * 2.0 ** (s / 12.0))
        out_mb = len(y) * 2 / 1e6  # 16-bit mono equiv
        mbmin = out_mb / (wall / 60) if wall > 0 else float("inf")
        rtf = music_dur / wall if wall > 0 else float("inf")
        return {"shift": s, "wall_s": wall, "mb_per_min": mbmin, "rtf": rtf,
                "dur_ms": dur_ms, "null_db": ndb, "null_lag": lag,
                "flux_full": fl, "flux_hf": fh,
                "music_f0": f0, "music_f0_conf": conf,
                "music_cents_f0": ce_f0, "music_cents_peak": ce_pk,
                "sidecar_bytes": sidecar_bytes, "latency": latency,
                "ram_mb": rss_mb()}

    def synth_metrics_row(y, s):
        want = SYNTH_F * 2.0 ** (s / 12.0)
        c_pk, f_pk, c_f0, f_f0, conf = synth_tuning_both(y, SR, want)
        dur_ms = (len(y) - len(synth)) / SR * 1e3
        ref = rb["synth"][s]
        ndb, lag = XE.null_db(ref, y, rate=SR)
        return {"cents_peak": c_pk, "f_peak": f_pk, "cents_f0": c_f0,
                "f_f0": f_f0, "conf": conf, "dur_ms": dur_ms,
                "null_db": ndb, "null_lag": lag}

    # ---- (1) rubberband reference as a method (self-null = -inf control) ----
    for s in SHIFTS_INT + [FRAC]:
        y = rb["music"][s]
        # self null
        ndb, _ = XE.null_db(y, y, rate=SR)
        results.setdefault("rubberband", {"label": "rubberband CLI v4 (ref)",
                                          "latency": "offline",
                                          "frac": True, "shifts": {}})
        m = music_metrics("rubberband", y, s, rb_wall[("music", s)], 0,
                          "offline")
        m["null_db"] = float("-inf")  # self-comparison control
        m["synth"] = synth_metrics_row(rb["synth"][s], s)
        m["synth"]["null_db"] = float("-inf")
        results["rubberband"]["shifts"][str(s)] = m
    results["rubberband"]["sidecar_bytes"] = 0

    # ---- (2) pv float ----
    results["pv_float"] = {"label": "pv.py pv_shift (float)",
                           "latency": "offline", "frac": True, "shifts": {}}
    for s in SHIFTS_INT + [FRAC]:
        t0 = time.perf_counter()
        ym = pv.pv_shift(music, s)
        wm = time.perf_counter() - t0
        t0 = time.perf_counter()
        ys = pv.pv_shift(synth, s)
        ws = time.perf_counter() - t0
        wall = wm + ws * 0  # music wall only for speed metrics
        m = music_metrics("pv", ym, s, wm, 0, "offline")
        m["synth"] = synth_metrics_row(ys, s)
        m["synth_wall_s"] = ws
        results["pv_float"]["shifts"][str(s)] = m
        write_mono16(os.path.join(OUTDIR, f"pv_music_{s:+.2f}.wav"), ym)
        print(f"pv {s:+.2f}: music wall {wm:.1f}s null {m['null_db']:.1f}dB "
              f"flux {m['flux_full']:.3f}/{m['flux_hf']:.3f} "
              f"music {m['music_cents_f0']:+.0f}c synth "
              f"{m['synth']['cents_peak']:+.1f}c", flush=True)
    results["pv_float"]["sidecar_bytes"] = 0

    # ---- (3) chunked streaming ----
    results["pv_chunked"] = {"label": "x7k_arch_d pv_shift_chunked",
                             "latency": "chunked", "frac": True,
                             "shifts": {}}
    for s in SHIFTS_INT + [FRAC]:
        t0 = time.perf_counter()
        ym, chunk_ms = ARCHD.pv_shift_chunked(music, s)
        wm = time.perf_counter() - t0
        ys, _ = ARCHD.pv_shift_chunked(synth, s)
        # bit-exactness vs whole-file pv
        yf = pv.pv_shift(music, s)
        mad = float(np.max(np.abs(ym - yf)))
        ndbc, _ = XE.null_db(yf, ym, rate=SR)
        m = music_metrics("chunked", ym, s, wm, 0, "chunked")
        m["synth"] = synth_metrics_row(ys, s)
        m["chunk_bit_exact_mad"] = mad
        m["chunk_vs_whole_null_db"] = ndbc
        m["chunk_ms_median"] = float(np.median(chunk_ms))
        results["pv_chunked"]["shifts"][str(s)] = m
        print(f"chunked {s:+.2f}: wall {wm:.1f}s mad-vs-whole {mad:.1e} "
              f"null {m['null_db']:.1f}dB flux {m['flux_full']:.3f}",
              flush=True)
    results["pv_chunked"]["sidecar_bytes"] = 0

    # ---- (5) sinusoidal resynth (arch_a) ----
    # calibrate gain on synth +2 (same as arch_a main)
    an_s = ARCHA.analyze(synth, SR)
    blob_s, _, _ = ARCHA.serialize(an_s)
    dec_s = ARCHA.deserialize(blob_s)
    y2raw = ARCHA.resynth(dec_s, +2, len(synth), gain=1.0)
    gain = float(np.sqrt((synth ** 2).mean() / ((y2raw ** 2).mean() + 1e-18)))
    an_m = ARCHA.analyze(music, SR)
    blob_m, raw_m, _ = ARCHA.serialize(an_m)
    open(os.path.join(OUTDIR, "sidecar.x7ka"), "wb").write(blob_m)
    dec_m = ARCHA.deserialize(blob_m)
    results["sinusoidal"] = {"label": "x7k_arch_a sinusoidal",
                             "latency": "offline", "frac": True,
                             "shifts": {},
                             "sidecar_bytes": len(blob_m),
                             "sidecar_raw_bytes": raw_m, "calib_gain": gain}
    print(f"sinusoidal sidecar {len(blob_m)}B raw {raw_m}B gain {gain:.3f}",
          flush=True)
    for s in SHIFTS_INT + [FRAC]:
        t0 = time.perf_counter()
        ym = ARCHA.resynth(dec_m, s, len(music), gain=gain)
        wm = time.perf_counter() - t0
        ys = ARCHA.resynth(dec_s, s, len(synth), gain=gain)
        m = music_metrics("sinusoidal", ym, s, wm, len(blob_m), "offline")
        m["synth"] = synth_metrics_row(ys, s)
        results["sinusoidal"]["shifts"][str(s)] = m
        write_mono16(os.path.join(OUTDIR, f"sine_music_{s:+.2f}.wav"), ym)
        print(f"sinusoidal {s:+.2f}: wall {wm:.1f}s null {m['null_db']:.1f}dB "
              f"flux {m['flux_full']:.3f}/{m['flux_hf']:.3f} "
              f"music {m['music_cents_f0']:+.0f}c synth "
              f"{m['synth']['cents_peak']:+.1f}c", flush=True)

    # ---- (6) TD-PSOLA (arch_c) ----
    ana_m = ARCHC.extract(music, SR)
    sc_path = os.path.join(OUTDIR, "marks.x7kc")
    sc_bytes = ARCHC.pack_sidecar(sc_path, SR, ana_m)
    ana_s = ARCHC.extract(synth, SR)
    st_m = np.stack([music, music], axis=1)
    st_s = np.stack([synth, synth], axis=1)
    results["psola"] = {"label": "x7k_arch_c TD-PSOLA", "latency": "offline",
                        "frac": True, "shifts": {},
                        "sidecar_bytes": sc_bytes}
    print(f"psola sidecar {sc_bytes}B voiced "
          f"{100*ana_m['voiced'].mean():.1f}%", flush=True)
    for s in SHIFTS_INT + [FRAC]:
        t0 = time.perf_counter()
        ym2 = ARCHC.psola_resynth(st_m, SR, ana_m, s)
        wm = time.perf_counter() - t0
        ym = np.asarray(ym2.mean(axis=1), dtype=np.float64)
        ys2 = ARCHC.psola_resynth(st_s, SR, ana_s, s)
        ys = np.asarray(ys2.mean(axis=1), dtype=np.float64)
        m = music_metrics("psola", ym, s, wm, sc_bytes, "offline")
        m["synth"] = synth_metrics_row(ys, s)
        results["psola"]["shifts"][str(s)] = m
        write_mono16(os.path.join(OUTDIR, f"psola_music_{s:+.2f}.wav"), ym)
        print(f"psola {s:+.2f}: wall {wm:.1f}s null {m['null_db']:.1f}dB "
              f"flux {m['flux_full']:.3f}/{m['flux_hf']:.3f} "
              f"music {m['music_cents_f0']:+.0f}c synth "
              f"{m['synth']['cents_peak']:+.1f}c", flush=True)

    # ---- (4) stored-version codecs ----
    for codec, br in CODEC_MATRIX:
        key = f"stored_{codec}{br}"
        # build music + synth containers (semis +2,-3 only: stepped)
        t0 = time.perf_counter()
        data_m = x7k.build(music_wav, SHIFTS_INT, codec=codec, bitrate=br)
        data_s = x7k.build(synth_wav, SHIFTS_INT, codec=codec, bitrate=br)
        wbuild = time.perf_counter() - t0
        pm = os.path.join(OUTDIR, f"stored_music_{codec}{br}.x7k")
        ps = os.path.join(OUTDIR, f"stored_synth_{codec}{br}.x7k")
        open(pm, "wb").write(data_m)
        open(ps, "wb").write(data_s)
        idx_m = x7k.read_index(pm)
        idx_s = x7k.read_index(ps)
        entry = {"label": f"x7k stored {codec}-{br}", "latency": "instant",
                 "frac": False, "shifts": {},
                 "sidecar_bytes": len(data_m),
                 "sidecar_synth_bytes": len(data_s),
                 "build_wall_s": wbuild}
        # per-version MB/min of sidecar (like x7k_eval matrix)
        per = [v["len"] / (music_dur / 60) / 1e6 for v in idx_m["versions"]]
        entry["sidecar_mb_per_min"] = sum(per) / len(per)
        for s in SHIFTS_INT:
            t0d = time.perf_counter()
            pcm, rate = x7k.version_pcm(idx_m, s, td)
            wdec = time.perf_counter() - t0d
            ym = np.asarray(pcm.mean(axis=1), dtype=np.float64)
            pcs, _ = x7k.version_pcm(idx_s, s, td)
            ys = np.asarray(pcs.mean(axis=1), dtype=np.float64)
            assert rate == SR, (rate, SR)
            m = music_metrics(key, ym, s, wbuild / len(SHIFTS_INT),
                              len(data_m), "instant")
            m["decode_wall_s"] = wdec
            m["synth"] = synth_metrics_row(ys, s)
            entry["shifts"][str(s)] = m
        # render throughput for the offline precompute
        total_dur = music_dur * len(SHIFTS_INT) + SYNTH_DUR * len(SHIFTS_INT)
        entry["render_mb_per_min"] = (
            (sum(len(v) for v in [data_m, data_s]) / 1e6) / (wbuild / 60))
        entry["render_rtf"] = total_dur / wbuild
        results[key] = entry
        m2 = entry["shifts"]["2"]
        mm = entry["shifts"]["-3"]
        print(f"{key}: build {wbuild:.0f}s sidecar {len(data_m)/1e3:.0f}KB "
              f"({entry['sidecar_mb_per_min']:.2f}MB/min/ver) "
              f"+2 null {m2['null_db']:.1f}dB flux {m2['flux_full']:.3f} "
              f"music {m2['music_cents_f0']:+.0f}c synth "
              f"{m2['synth']['cents_peak']:+.1f}c | "
              f"-3 null {mm['null_db']:.1f}dB music "
              f"{mm['music_cents_f0']:+.0f}c", flush=True)

    # ---- PASS/FAIL per method ----
    # PASS iff: synth peak |c|<25 AND music f0 |c|<50 (both int shifts)
    #           AND duration |ms|<100 AND flux_full>0.50 AND flux_hf>0.50
    #           (both int shifts). HF gate catches sinusoidal-residual loss.
    def verdict(entry):
        bad = []
        for s in [str(x) for x in SHIFTS_INT]:
            m = entry["shifts"][s]
            if abs(m["synth"]["cents_peak"]) >= 25:
                bad.append(f"{s} synth {m['synth']['cents_peak']:+.0f}c")
            if abs(m["music_cents_f0"]) >= 50 or not math.isfinite(
                    m["music_cents_f0"]):
                bad.append(f"{s} music {m['music_cents_f0']:+.0f}c")
            if abs(m["dur_ms"]) >= 100:
                bad.append(f"{s} dur {m['dur_ms']:+.0f}ms")
            if not (m["flux_full"] > 0.50):
                bad.append(f"{s} flux {m['flux_full']:.2f}")
            if not (m["flux_hf"] > 0.50):
                bad.append(f"{s} fluxHF {m['flux_hf']:.2f}")
        return ("PASS" if not bad else "FAIL", "; ".join(bad))

    # ---- ranking: PASS first, then avg null (more -inf/negative first),
    # then flux_full desc, then |music cents| asc ----
    def agg(entry):
        nulls = [entry["shifts"][s]["null_db"] for s in entry["shifts"]]
        fls = [entry["shifts"][s]["flux_full"] for s in entry["shifts"]]
        ces = [abs(entry["shifts"][s]["music_cents_f0"])
               if math.isfinite(entry["shifts"][s]["music_cents_f0"])
               else 999 for s in entry["shifts"]]
        # avg ignoring -inf (rubberband self)
        fn = [n for n in nulls if n > float("-inf")]
        avg_null = sum(fn) / len(fn) if fn else float("-inf")
        return (sum(fls) / len(fls), sum(ces) / len(ces), avg_null)

    rows = []
    for key, e in results.items():
        v, why = verdict(e) if key != "rubberband" else ("PASS", "reference")
        a_fl, a_ce, a_null = agg(e)
        # sort key: PASS(0)/FAIL(1), null asc, -flux asc, cents asc
        sk = (0 if v == "PASS" else 1,
              a_null if a_null > float("-inf") else -1e9,
              -a_fl, a_ce)
        # display avgs over int shifts only for comparability
        ints = [str(x) for x in SHIFTS_INT]
        d = {"key": key, "label": e["label"], "verdict": v, "why": why,
             "sort": sk,
             "synth_c": sum(e["shifts"][s]["synth"]["cents_peak"]
                            for s in ints) / len(ints),
             "music_c": sum(abs(e["shifts"][s]["music_cents_f0"])
                            if math.isfinite(e["shifts"][s]["music_cents_f0"])
                            else 999 for s in ints) / len(ints),
             "dur": max(abs(e["shifts"][s]["dur_ms"]) for s in ints),
             "null": sum(e["shifts"][s]["null_db"] for s in ints
                         if e["shifts"][s]["null_db"] > float("-inf")) / max(
                 1, sum(1 for s in ints
                        if e["shifts"][s]["null_db"] > float("-inf"))),
             "fl": sum(e["shifts"][s]["flux_full"] for s in ints) / len(ints),
             "fh": sum(e["shifts"][s]["flux_hf"] for s in ints) / len(ints),
             "wall": sum(e["shifts"][s]["wall_s"] for s in ints),
             "mbmin": sum(e["shifts"][s]["mb_per_min"] for s in ints) / len(
                 ints),
             "rtf": sum(music_dur / e["shifts"][s]["wall_s"]
                        if e["shifts"][s]["wall_s"] > 0 else 0
                        for s in ints) / len(ints),
             "lat": e["latency"] if "latency" in e else
             e["shifts"][ints[0]]["latency"],
             "sc": e.get("sidecar_bytes", 0)}
        if key == "rubberband":
            d["null"] = float("-inf")
        rows.append(d)
    rows.sort(key=lambda r: r["sort"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    # ---- print ----
    print("\n== per-method verdicts ==", flush=True)
    for r in rows:
        print(f"{r['verdict']:4s} {r['label']:28s} "
              f"synth {r['synth_c']:+.1f}c music {r['music_c']:.0f}c "
              f"dur {r['dur']:.0f}ms null "
              f"{r['null'] if r['null'] > float('-inf') else float('-inf'):.1f}"
              f"dB flux {r['fl']:.3f}/{r['fh']:.3f}"
              + (f" -- {r['why']}" if r["why"] else ""), flush=True)
    print("\n== ranked table (PASS first, then null asc) ==", flush=True)
    hdr = (f"{'#':>2} {'method':28s} {'V':4s} {'synthc':>7} {'musicc':>7} "
           f"{'durms':>6} {'nulldB':>7} {'flux':>5} {'fluxHF':>6} "
           f"{'wall_s':>6} {'MB/min':>7} {'RTF':>6} {'lat':>7} {'sidecar':>9}")
    print(hdr, flush=True)
    for r in rows:
        nulls = "-inf" if r["null"] == float("-inf") else f"{r['null']:.1f}"
        print(f"{r['rank']:>2} {r['label']:28s} {r['verdict']:4s} "
              f"{r['synth_c']:+7.1f} {r['music_c']:7.0f} "
              f"{r['dur']:6.0f} {nulls:>7} {r['fl']:5.3f} {r['fh']:6.3f} "
              f"{r['wall']:6.1f} {r['mbmin']:7.1f} {r['rtf']:6.1f} "
              f"{r['lat']:>7} {r['sc']:9.0f}", flush=True)
    # fractional +2.5 annex
    print("\n== fractional +2.5 annex (methods supporting it) ==", flush=True)
    for key, e in results.items():
        # int-shift keys are "2"/"-3", frac key is "2.5" (str(2.5))
        if not e.get("frac") or "2.5" not in e["shifts"]:
            print(f"  {e['label']:28s} N/A (stepped)", flush=True)
            continue
        m = e["shifts"]["2.5"]
        print(f"  {e['label']:28s} synth {m['synth']['cents_peak']:+.1f}c "
              f"music {m['music_cents_f0']:+.0f}c null {m['null_db']:.1f}dB "
              f"flux {m['flux_full']:.3f}/{m['flux_hf']:.3f}", flush=True)
    print(f"\npeak RAM {rss_mb():.0f} MB total wall "
          f"{time.perf_counter()-t_all:.0f}s", flush=True)

    out = {"src": src_path, "src_kind": src_kind, "sr": SR,
           "music_dur_s": music_dur, "voiced_win_s": [s0 / SR, s1 / SR],
           "f0_src": f0_src, "peak_src": pk_src, "methods": results,
           "table": [{k: v for k, v in r.items() if k != "sort"} for r in rows]}
    json.dump(out, open(os.path.join(OUTDIR, "metrics.json"), "w"), indent=1,
              default=float)
    print(f"artifacts: {OUTDIR}/ (metrics.json + wavs + sidecars)",
          flush=True)


if __name__ == "__main__":
    main()
