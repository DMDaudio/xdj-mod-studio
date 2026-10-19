#!/usr/bin/env python3
"""hc_glyphs.h: anti-aliased pad letters A-H (alpha 0..32), Helvetica Neue
Medium at the stock pad-letter cap height (same renderer as aafont.py)."""
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import numpy as np
import aafont as AF

H = 14
gl = [AF._render(c, AF.NEUE, 10, H) for c in "ABCDEFGH"]
W = max(g.shape[1] for g in gl)
out = [f"#define GL_H {H}\n#define GL_W {W}\n",
       "static const unsigned char gl_w[8] = {" + ",".join(str(g.shape[1]) for g in gl) + "};\n",
       "static const unsigned char gl_a[8][GL_H * GL_W] = {\n"]
for g in gl:
    a = np.zeros((H, W))
    a[:, :g.shape[1]] = g
    out.append("  {" + ",".join(str(int(round(v * 32))) for v in a.ravel()) + "},\n")
out.append("};\n")
open(os.path.join(HERE, "hc_glyphs.h"), "w").write("".join(out))
print("hc_glyphs.h", H, W)
