#!/usr/bin/env python3
"""x7k_arch_cprime.py -- C-PRIME: TD-PSOLA key shift on ISOLATED sources.

Follow-up to tools/x7k_arch_c.py, where TD-PSOLA on the FULL MIX failed
(null ~+3.8 dB vs rubberband, periodicity confidence 0.75 -> 0.46
collapse): PSOLA assumes a single periodic source, which a full mix
violates. This test isolates single sources first (Demucs vocal stem;
bass stem as second data point) and re-runs the SAME arch_c machinery
(imported, not reimplemented) on each isolated source at +2 / -3
semitones, measuring:

  * tuning: probe-window f0 cents (psola vs wanted) + global median over
    strong-voiced frames, both vs the rubberband reference;
  * null dB of PSOLA vs rubberband (aligned);
  * periodicity confidence retained: probe autocorr conf src -> psola,
    plus re-extracted median voiced conf of source vs PSOLA output
    (the full-mix killer metric: 0.76 -> 0.45 collapse);
  * voiced-frame fraction of the isolated source.

Source: /tmp/demo30.wav (read-only; never Rekordbox audio).
Separation: ~/.xdj700-studio/venv/bin/python -m demucs (htdemucs,
cached weights; Demucs is NEVER imported into this interpreter --
only driven as a subprocess of its own venv python).
Fallback if Demucs is unusable: synthesised 10 s vocal-like tone
(440 Hz + 5 Hz vibrato + harmonics).
Artifacts: /tmp/x7k_cprime/. No servers, no deck.

Usage:
  .venv/bin/python tools/x7k_arch_cprime.py [--shifts +2,-3] [--bass-shift +2]
  .venv/bin/python tools/x7k_arch_cprime.py --vocal-wav /tmp/demo30vox.wav
      --bass-wav /tmp/demo30bass.wav --outdir /tmp/x7k_cprime/iso
      (use pre-isolated sources directly, skipping Demucs)
"""
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, "tools")
import x7k  # noqa: E402  (peak_freq check, same as arch_c)
import x7k_arch_c as C  # noqa: E402  (reuse: extract/psola/metrics)

SRC = "/tmp/demo30.wav"
OUTDIR = "/tmp/x7k_cprime"
DEMUCS_PY = os.path.expanduser("~/.xdj700-studio/venv/bin/python")
SEPDIR = os.path.join(OUTDIR, "demucs_sep")
MODEL = "htdemucs"


def run(cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def demucs_available():
    if not os.path.exists(DEMUCS_PY):
        return False
    r = subprocess.run([DEMUCS_PY, "-c", "import demucs; print('ok')"],
                       capture_output=True, text=True)
    return r.returncode == 0 and "ok" in r.stdout


def stem_paths(src=SRC):
    """Expected htdemucs outputs for a source path (vocals + bass)."""
    stem = os.path.splitext(os.path.basename(src))[0]
    base = os.path.join(SEPDIR, MODEL, stem)
    return {"vocals": os.path.join(base, "vocals.wav"),
            "bass": os.path.join(base, "bass.wav")}


def separate(src=SRC):
    """Run Demucs separation once; reuse stems if already present."""
    sp = stem_paths(src)
    if all(os.path.exists(p) for p in sp.values()):
        print(f"stems already present under {SEPDIR} (reusing)", flush=True)
        return sp
    print(f"separating {src} with {MODEL} "
          f"(Demucs venv python, CPU) ...", flush=True)
    t0 = time.perf_counter()
    r = subprocess.run([DEMUCS_PY, "-m", "demucs", "-n", MODEL,
                        "-o", SEPDIR, src],
                       capture_output=True, text=True)
    dt = time.perf_counter() - t0
    print(f"  demucs wall {dt:.0f}s", flush=True)
    if r.returncode != 0:
        print(f"  demucs FAILED:\n{r.stderr[-3000:]}", flush=True)
        raise RuntimeError("demucs separation failed")
    missing = [k for k, p in sp.items() if not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"demucs ok but stems missing: {missing}")
    for k, p in sp.items():
        print(f"  {k}: {p} ({os.path.getsize(p) / 1e6:.1f} MB)",
              flush=True)
    return sp


def synth_vocal(path, sr=44100, dur=10.0):
    """Fallback: 10 s vocal-like tone (440 Hz + 5 Hz vibrato + harmonics)."""
    print(f"synthesising vocal-like fallback -> {path}", flush=True)
    n = int(sr * dur)
    t = np.arange(n) / sr
    vib = 0.006 * np.sin(2 * np.pi * 5.0 * t)  # ~+-10 cents @5 Hz
    phase = 2 * np.pi * 440.0 * np.cumsum(1.0 + vib) / sr
    x = (np.sin(phase) + 0.5 * np.sin(2 * phase) + 0.25 * np.sin(3 * phase)
         + 0.12 * np.sin(4 * phase))
    x *= 0.25 * (0.8 + 0.2 * np.sin(2 * np.pi * 5.0 * t + 1.0))
    stereo = np.stack([x, x], axis=1).astype(np.float32)
    C.write_wav(path, stereo, sr)
    return path


def eval_source(name, path, shifts):
    """Run the arch_c pipeline on one isolated source. Returns dict."""
    print(f"-- source: {name} ({path})", flush=True)
    stereo, rate = C.decode_mono_stereo(path)
    dur = len(stereo) / rate
    mono = stereo.mean(axis=1).astype(np.float64)
    print(f"  {rate} Hz stereo {dur:.2f}s ({len(stereo)} samples/ch)",
          flush=True)
    ana = C.extract(mono, rate)
    bk = C.breakup_metrics(ana)
    print(f"  voiced {bk['voiced_frac'] * 100:.1f}% | transient "
          f"{bk['transient_frac'] * 100:.1f}% | median conf "
          f"{bk['median_conf']:.2f} (voiced {bk['median_conf_voiced']:.2f})",
          flush=True)
    print(f"  BREAKUP: {bk['unstable_frac'] * 100:.1f}% of "
          f"{bk['both_voiced_pairs']} voiced-voiced adjacencies jump "
          f">100 cents (octave slips {bk['octave_slip_frac'] * 100:.1f}%, "
          f"median |jump| {bk['median_abs_jump_cents']:.0f}c, "
          f"voicing flips {bk['voicing_flip_frac'] * 100:.1f}%)",
          flush=True)
    s0, s1 = C.find_voiced(mono, rate)
    f0_src, c_src = C.f0_autocorr(mono[s0:s1], rate)
    print(f"  voiced probe [{s0 / rate:.1f},{s1 / rate:.1f}]s: "
          f"f0_src={f0_src:.1f} Hz conf={c_src:.2f}", flush=True)
    res = {"path": path, "rate": rate, "dur_s": dur,
           "nsamp": len(stereo), "breakup": bk,
           "probe": {"s0": s0, "s1": s1, "f0_src": f0_src,
                     "conf_src": c_src}, "shifts": {}}
    for s in shifts:
        tag = f"p{s:+d}"
        print(f"  == shift {s:+d} ==", flush=True)
        rb_path = os.path.join(OUTDIR, f"{name}_rb_{tag}.wav")
        t0 = time.perf_counter()
        run(["rubberband", "-q", "-3", "-p", str(s), path, rb_path])
        t_rb = time.perf_counter() - t0
        rb, _ = C.decode_mono_stereo(rb_path)
        rb_m = rb.mean(axis=1).astype(np.float64)
        t0 = time.perf_counter()
        ps = C.psola_resynth(stereo, rate, ana, s)
        t_dec = time.perf_counter() - t0
        ps_path = os.path.join(OUTDIR, f"{name}_psola_{tag}.wav")
        C.write_wav(ps_path, ps, rate)
        ps_m = ps.mean(axis=1).astype(np.float64)
        ndb, lag = C.null_db(rb_m, ps_m, rate)
        ndb_base, _ = C.null_db(rb_m, mono[:len(rb_m)], rate)
        g_rb = C.global_tuning(mono, rb_m, rate, ana, s)
        g_ps = C.global_tuning(mono, ps_m, rate, ana, s)
        want = f0_src * 2.0 ** (s / 12.0)
        win_src = mono[s0:s1]
        lag_rb = C.xcorr_lag(win_src, rb_m[s0:s1])
        lag_ps = C.xcorr_lag(win_src, ps_m[s0:s1])
        f_rb, c_rb = C.f0_autocorr(rb_m[s0 + lag_rb:s1 + lag_rb], rate)
        f_ps, c_ps = C.f0_autocorr(ps_m[s0 + lag_ps:s1 + lag_ps], rate)
        c_rb_c = C.cents(f_rb, want)
        c_ps_c = C.cents(f_ps, want)
        # periodicity retained: re-extract PSOLA output, compare voiced conf
        ana_ps = C.extract(ps_m, rate)
        bk_ps = C.breakup_metrics(ana_ps)
        print(f"    rubberband {t_rb:.0f}s wall; psola decode {t_dec:.1f}s",
              flush=True)
        print(f"    NULL psola-vs-rb: {ndb:.1f} dB (lag {lag}) "
              f"[rb-vs-src baseline {ndb_base:.1f} dB]", flush=True)
        print(f"    GLOBAL tuning: rb med {g_rb[0]:+.0f}c med|.| {g_rb[1]:.0f}c "
              f"in50 {g_rb[2] * 100:.0f}% (n={g_rb[3]}) | psola med "
              f"{g_ps[0]:+.0f}c med|.| {g_ps[1]:.0f}c "
              f"in50 {g_ps[2] * 100:.0f}% (n={g_ps[3]})", flush=True)
        print(f"    f0-autocorr: rb {f_rb:.1f} Hz ({c_rb_c:+.0f}c, conf "
              f"{c_rb:.2f}) | psola {f_ps:.1f} Hz ({c_ps_c:+.0f}c, conf "
              f"{c_ps:.2f}) [want {want:.1f} Hz]", flush=True)
        print(f"    CONF retained: probe {c_src:.2f} -> {c_ps:.2f} "
              f"({c_ps / c_src * 100:.0f}%) | re-extracted voiced median "
              f"{bk['median_conf_voiced']:.2f} -> "
              f"{bk_ps['median_conf_voiced']:.2f}", flush=True)
        res["shifts"][str(s)] = {
            "rubberband_wav": rb_path, "psola_wav": ps_path,
            "rubberband_render_s": t_rb, "decode_s": t_dec,
            "null_db_vs_rb": ndb, "null_lag": lag,
            "null_baseline_rb_vs_src": ndb_base,
            "glob_rb": g_rb, "glob_ps": g_ps,
            "f0_rb": f_rb, "f0_rb_cents": c_rb_c, "conf_rb": c_rb,
            "f0_psola": f_ps, "f0_psola_cents": c_ps_c,
            "conf_psola": c_ps,
            "conf_retained_probe": (c_ps / c_src if c_src > 0
                                    else float("nan")),
            "median_conf_voiced_psola": bk_ps["median_conf_voiced"],
            "voiced_frac_psola": bk_ps["voiced_frac"]}
    return res


def main():
    global OUTDIR, SEPDIR
    import re
    shifts = [+2, -3]
    bass_shifts = [+2]
    src = SRC
    outdir = OUTDIR
    vocal_wav = None
    bass_wav = None
    i = 0
    args = sys.argv[1:]
    while i < len(args):
        a = args[i]
        m = re.match(r"--shifts=(.*)", a)
        if m:
            shifts = [int(x) for x in m.group(1).split(",")]
            i += 1
            continue
        m = re.match(r"--bass-shift=(.*)", a)
        if m:
            bass_shifts = [int(x) for x in m.group(1).split(",")]
            i += 1
            continue
        if a in ("--src", "--outdir", "--vocal-wav", "--bass-wav"):
            if i + 1 >= len(args):
                raise SystemExit(f"{a} needs a value")
            val = args[i + 1]
        else:
            m = re.match(r"--(src|outdir|vocal-wav|bass-wav)=(.*)", a)
            if not m:
                raise SystemExit(f"unknown arg: {a}")
            a, val = "--" + m.group(1), m.group(2)
        if a == "--src":
            src = val
        elif a == "--outdir":
            outdir = val
        elif a == "--vocal-wav":
            vocal_wav = val
        elif a == "--bass-wav":
            bass_wav = val
        i += 2
    OUTDIR = outdir
    SEPDIR = os.path.join(OUTDIR, "demucs_sep")
    os.makedirs(OUTDIR, exist_ok=True)
    t_all = time.perf_counter()

    results = {"shifts vocal": shifts, "shifts bass": bass_shifts,
               "sources": {}}
    if vocal_wav or bass_wav:
        # Pre-isolated sources: no Demucs run (stems cut from a full-track
        # separation of the same Radio 84 source; Rekordbox audio untouched
        # by this script -- the cuts were made with ffmpeg reads only).
        results["mode"] = "precut_stems"
        results["src"] = {"vocals": vocal_wav, "bass": bass_wav}
        print(f"pre-isolated sources: vocals={vocal_wav} bass={bass_wav}",
              flush=True)
        if vocal_wav:
            results["sources"]["vocals"] = eval_source(
                "vocals", vocal_wav, shifts)
        if bass_wav:
            results["sources"]["bass"] = eval_source(
                "bass", bass_wav, bass_shifts)
    else:
        print(f"source: {src}", flush=True)
        results["src"] = src
        if demucs_available():
            print("demucs: importable in Demucs venv -> separating",
                  flush=True)
            try:
                sp = separate(src)
                results["sources"]["vocals"] = eval_source(
                    "vocals", sp["vocals"], shifts)
                results["sources"]["bass"] = eval_source(
                    "bass", sp["bass"], bass_shifts)
                results["mode"] = "demucs"
            except Exception as e:  # noqa: BLE001 -- fall back to synth
                print(f"demucs path failed ({e}); using synth fallback",
                      flush=True)
                fb = os.path.join(OUTDIR, "synth_vocal.wav")
                synth_vocal(fb)
                results["sources"]["synth_vocal"] = eval_source(
                    "synth_vocal", fb, shifts)
                results["mode"] = "synth_fallback"
        else:
            print("demucs: NOT importable -> synth fallback", flush=True)
            fb = os.path.join(OUTDIR, "synth_vocal.wav")
            synth_vocal(fb)
            results["sources"]["synth_vocal"] = eval_source(
                "synth_vocal", fb, shifts)
            results["mode"] = "synth_fallback"

    results["total_wall_s"] = time.perf_counter() - t_all
    json.dump(results, open(os.path.join(OUTDIR, "metrics.json"), "w"),
              indent=1)
    print(f"total wall {results['total_wall_s']:.0f}s", flush=True)
    print(f"artifacts: {OUTDIR}/ (demucs_sep/, *_rb_*/ *_psola_* wavs, "
          f"metrics.json)", flush=True)


if __name__ == "__main__":
    main()
