"""Isolate the ~38 dB bypass ceiling (tools/pv_lab.py `lock`).

bypass=True pushes the analysis spectrum straight back through the synthesis
half, so nothing in the phase engine can be at fault: whatever is left is
window / FFT / scaling / OLA.  This walks the same path stage by stage against
a float64 numpy model of the identical algorithm and reports the residual at
each hand-off, which says which stage owns the loss instead of guessing.

Every stage is compared after removing its best-fit scale, because the SH
kernels normalise by 1/N inside FFTSTAGES and the exact factor is a convention
rather than the thing under test.
"""

import math
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "tools")

import pv_lab as L                                    # noqa: E402
import pv_run as R                                    # noqa: E402

N, HS = R.N, R.HS
NB = N // 2 + 1


def hann_f(n):
    return 0.5 * (1.0 - np.cos(2.0 * np.pi * np.arange(n) / n))


def resid(sh, fl, tag):
    """SNR of `sh` against `fl` after removing the best-fit scale."""
    sh = np.asarray(sh, np.float64).ravel()
    fl = np.asarray(fl, np.float64).ravel()
    k = min(len(sh), len(fl))
    sh, fl = sh[:k], fl[:k]
    a = float((sh * fl).sum() / max((fl * fl).sum(), 1e-30))
    e = sh - a * fl
    s = 20 * math.log10(abs(a) * math.sqrt((fl ** 2).mean())
                        / max(math.sqrt((e ** 2).mean()), 1e-12))
    print(f"  {tag:34s} scale {a:12.6g}   SNR {s:6.2f} dB")
    return s


def main():
    x = L.sig_tone().astype(np.float64)
    win = hann_f(N)

    box = R.Box(N, HS)
    R.tables(box)
    box.put(R.A_XIN, x.astype(np.int32).astype("<i2").tobytes())
    print("stage-by-stage, tone, frame 3")
    print(" analysis side")
    fr = x[3 * HS:3 * HS + N]
    R.spectrum(box, R.A_XIN, 3 * HS, R.A_RE, R.A_IM, R.A_MAG1, R.A_PH1)
    sh_re = box.s16(R.A_RE, NB).astype(np.float64)
    sh_im = box.s16(R.A_IM, NB).astype(np.float64)
    # the SH window is a Q15 int16 table, so compare against the same window
    wq = np.array([int(round(32767 * w)) for w in win], np.float64) / 32767.0
    wf = fr * wq
    F = np.fft.rfft(wf) / N
    resid(sh_re, F.real, "analysis FFT  Re")
    resid(sh_im, F.imag, "analysis FFT  Im")
    mag = np.abs(F)
    resid(box.s16(R.A_MAG1, NB), mag, "magnitude")
    ph = np.mod(np.angle(F), 2 * np.pi) / (2 * np.pi) * 65536.0
    resid(box.u16(R.A_PH1, NB).astype(np.float64), ph, "phase (turns Q16)")

    print(" synthesis side (bypass: spectrum pushed back unchanged)")
    box.put(R.A_ZR, box.get(R.A_RE, 2 * NB))
    box.put(R.A_ZI, box.get(R.A_IM, 2 * NB))
    box.call("mirr", r4=R.A_RE, r5=R.A_IM, r6=R.A_ZR, r7=R.A_ZI, r8=N)
    box.call("conj", r4=R.A_ZI, r5=N)
    box.call("bitrev", r4=R.A_ZR, r5=R.A_ZI, r6=N)
    box.call("fftsyn", r4=R.A_ZR, r5=R.A_ZI, r6=R.A_TW, r7=N,
             r8=N.bit_length() - 1, r9=R.FFTSYN_K)
    box.call("conj", r4=R.A_ZI, r5=N)
    y = box.s16(R.A_ZR, N).astype(np.float64)
    # Bypass pushes the mirrored analysis spectrum back, so the correct
    # synthesis answer IS the windowed frame again (up to FFTSYN's scale
    # constant, which the synthesis Hann and EMIT32 normalise away).
    resid(y, fr * wq, "synthesis frame vs x*win")
    box.call("hann", r4=R.A_ZR, r5=R.A_WSYN, r6=N)
    resid(box.s16(R.A_ZR, N), fr * wq * wq, "after synthesis Hann vs x*win^2")

    print(" whole-stream bypass vs float stream")
    ys, _, _ = R.run_chain(x.astype(np.int32), r_target=1.0, bypass=True)
    lat = (N // HS - 1) * HS
    resid(ys, x, "SH bypass vs input (r1)")
    # float stream: same framing and OLA in float64, no fixed point anywhere
    acc = np.zeros(N + HS)
    out = []
    for m in range((len(x) - N) // HS + 1):
        fr_m = x[m * HS:m * HS + N] * wq
        Fm = np.fft.rfft(fr_m) / N
        Zm = np.concatenate([Fm, np.conj(Fm[1:-1])[::-1]]) * N
        fr_out = np.fft.ifft(Zm).real * N
        acc[:N] += fr_out * wq
        if m >= N // HS - 1:
            out.append(acc[:HS].copy())
        acc[HS:] = 0.0
    yfl = np.concatenate(out)
    k = min(len(ys), len(yfl))
    print(f"  float stream length {len(yfl)}, SH {len(ys)}")
    resid(ys[:k], yfl[:k], "SH bypass vs float bypass")


if __name__ == "__main__":
    main()
