#!/usr/bin/env python3
"""pv_lab.py -- experiment driver for the fixed-point key-shift chain.

Every experiment is a short run of pv_run.run_chain plus a measurement, so
the chain can be bisected (bypass vs phase engine, peak lock on/off, hop
ratios, amplitudes) without editing pv_run.py each time.

  pv_lab.py                 run the standard experiment set
  pv_lab.py lock            only the peak-lock comparison
  pv_lab.py <name> [...]    named experiments
"""
import math
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "tools")
import numpy as np
from pv_run import run_chain, shift_chain, snr, N, HS

SR = 44100
LAT = (N // HS - 1) * HS


def sig_tone(secs=0.12, amp=9000, f1=440.0, f2=1234.5):
    m = int(secs * SR)
    t = np.arange(m) / SR
    return (amp * np.sin(2 * math.pi * f1 * t)
            + 0.45 * amp * np.sin(2 * math.pi * f2 * t + 0.3)).astype(np.int32)


def sig_noise(secs=0.12, amp=9000, lvl=400, seed=4):
    x = sig_tone(secs, amp)
    rng = np.random.default_rng(seed)
    return (x + rng.integers(-lvl, lvl + 1, len(x))).astype(np.int32)


def sig_music(secs=0.12):
    """noise + a swept tone + a low tone: closer to programme material."""
    m = int(secs * SR)
    t = np.arange(m) / SR
    rng = np.random.default_rng(9)
    x = (7000 * np.sin(2 * math.pi * (200 + 3000 * t / t[-1]) * t)
         + 4000 * np.sin(2 * math.pi * 55 * t)
         + 3000 * np.sin(2 * math.pi * 3300 * t + 1.1)
         + rng.integers(-1500, 1500, m))
    return x.astype(np.int32)


def report(tag, y, x, skip=N):
    k = min(len(y), len(x) - LAT)
    if k <= skip + 64:
        print(f"{tag:34s} too short ({k} samples)")
        return -99
    s = snr(y, x[LAT:LAT + k], skip=skip)
    print(f"{tag:34s} SNR {s:6.2f} dB   (n={k - skip})", flush=True)
    return s


EXPS = {}


def exp(fn):
    EXPS[fn.__name__] = fn
    return fn


@exp
def kk():
    """Synthesis-FFT word length vs K: each unshifted stage doubles the
    peak (int16 stores), so a too-large K wraps and a too-small K throws
    away bits.  Bypass isolates this from the phase engine."""
    import pv_run
    x = sig_tone()
    for k in (2, 3, 4, 5, 6, 7):
        pv_run.set_k(k)
        y, _, _ = run_chain(x, r_target=1.0, bypass=True)
        report(f"K={k} bypass", y, x)
        y, _, _ = run_chain(x, r_target=1.0, lock=False)
        report(f"K={k} phase (no lock)", y, x)
    pv_run.set_k(5)


@exp
def lock():
    """Is the peak lock responsible for the identity loss?"""
    for nm, x in (("tone", sig_tone()), ("tone+noise", sig_noise()),
                  ("music-ish", sig_music())):
        y, _, _ = run_chain(x, r_target=1.0, lock=True)
        report(f"{nm} peak-lock ON", y, x)
        y, _, _ = run_chain(x, r_target=1.0, lock=False)
        report(f"{nm} peak-lock OFF", y, x)
        y, _, _ = run_chain(x, r_target=1.0, bypass=True)
        report(f"{nm} bypass (reference)", y, x)


@exp
def amp():
    """How much headroom before the fixed-point chain degrades?"""
    for a in (3000, 9000, 18000, 26000, 32000):
        x = sig_tone(amp=a)
        y, _, _ = run_chain(x, r_target=1.0, lock=False)
        report(f"tone amp={a} lock OFF", y, x)
        y, _, _ = run_chain(x, r_target=1.0, lock=True)
        report(f"tone amp={a} lock ON", y, x)


@exp
def hops():
    """R=1 identity at other hop pairs (Hs != Ha is not identity, but a
    hop sweep shows whether the phase bookkeeping depends on Hs == Ha)."""
    for hs, ha in ((256, 256), (512, 512), (128, 128), (512, 256)):
        y, _, _ = run_chain(sig_tone(), r_target=1.0, hs=hs, ha=ha)
        k = min(len(y), len(sig_tone()) - (N // hs - 1) * hs)
        s = snr(y, sig_tone()[(N // hs - 1) * hs:][:k], skip=N)
        print(f"Hs={hs} Ha={ha} (R={hs / ha:.3f})            SNR {s:6.2f} dB",
              flush=True)


@exp
def shift():
    """Actual pitch shifting: measure the output frequency."""
    for st in (-12, -7, -2, 2, 5, 7, 12):
        x = sig_tone(secs=0.3, amp=9000, f1=440.0, f2=0)
        y, rq, box = shift_chain(x, st)
        seg = y[4000:].astype(float)
        sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 18))
        f = np.fft.rfftfreq(1 << 18, 1 / SR)
        pk = f[sp.argmax()]
        want = 440 * 2 ** (st / 12.0)
        on = sp[(f > want * 0.97) & (f < want * 1.03)].sum() / sp.sum()
        print(f"shift {st:+3d} st  R={rq:.4f}  peak {pk:8.2f} Hz  "
              f"want {want:8.2f}  err {100 * (pk - want) / want:+.2f}%  "
              f"on-note {on * 100:5.1f}%", flush=True)


@exp
def detail():
    """Where the identity chain loses energy: compare the rebuilt spectrum
    against the analysis spectrum bin by bin, frame by frame."""
    from pv_run import (Box, tables, set_hops, spectrum, A_XIN, A_MAG1,
                        A_PH1, A_PH0, A_PREV, A_PK, A_NEAR, A_NEW, A_ZR,
                        A_STAB, A_SIN, A_COS, A_RE, A_IM, A_OMA, A_OMS,
                        A_ACC, A_OUT, A_WSYN, A_TW, A_ZI)
    import struct
    x = sig_tone()
    box = Box(N, HS)
    tables(box)
    nb = box.nb
    box.put(A_XIN, x.astype("<i2").tobytes())
    oma, oms = set_hops(box, HS)
    box.put(A_PH0, b"\0" * (2 * nb))
    box.put(A_PREV, b"\0" * (2 * nb))
    for m in (1, 2, 3, 8, 20):
        spectrum(box, A_XIN, m * HS, A_RE, A_IM, A_MAG1, A_PH1)
        box.put(A_PH0, box.get(A_PH1, 2 * nb))
        box.call("peaks", r4=A_MAG1, r5=nb, r6=A_PK)
        box.call("nearest", r4=A_PK, r5=nb, r6=A_NEAR)
        box.call("prop", r4=A_PREV, r5=A_PH0, r6=A_PH1, r7=A_OMA,
                 r8=A_OMS, r9=A_NEW, r10=nb)
        new = box.u16(A_NEW, nb)
        box.put(A_PREV, box.get(A_NEW, 2 * nb))
        mag = box.s16(A_MAG1, nb)
        re = box.s16(A_RE, nb).astype(float)
        im = box.s16(A_IM, nb).astype(float)
        anamp = np.hypot(re, im)
        pk = mag.argmax()
        print(f"frame {m}: mag peak bin {pk} mag {mag[pk]} "
              f"|re+im| {anamp[pk]:.0f}  magmax {mag.max()} "
              f"sum(mag^2) {int((mag.astype(float) ** 2).sum())}")
        print(f"   bins 0-8 re {re[:9].astype(int)}")
        print(f"   bins 0-8 im {im[:9].astype(int)}")
        print(f"   bins 0-8 mg {mag[:9]}")
        print(f"   new-new[0] {(int(new[0]) - int(new[1])) % 65536:6d} "
              f"oma[1]-oma[0] {(int(oma[1]) - int(oma[0])) % 65536:6d}")


if __name__ == "__main__":
    names = sys.argv[1:] or ["lock"]
    rc = 0
    for nm in names:
        print(f"=== {nm} ===", flush=True)
        EXPS[nm]()
