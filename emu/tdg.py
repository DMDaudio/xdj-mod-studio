"""SH7724 2D Graphics Accelerator (2DG, "M2DG") at 0xA4680000.

The hardware manual's chapter on this block is NDA-only. The command set
here is taken from Renesas's own open-source DirectFB driver for the
SH7723 (same engine: gfxdrivers/sh772x/sh7723_blt.[ch]) and checked
against the display lists the XDJ-700 firmware actually builds.

Registers (offsets from 0xA4680000):
  0x00 SCLR   write 1 = start executing the display list at DLSAR
  0x04 SSR    status (bit0 = TRAP reached)             [inferred]
  0x08 SSCR   status clear (write-1-to-clear)            [inferred]
  0x0C IER    interrupt enable (bit0 = TRAP)             [inferred]
  0x48 DLSAR  display list start address
  0x4C        source base address        0x58 source stride (pixels)
  0x50        destination base address   0x5C destination stride (pixels)
  0x80        source colour key          0x88 alpha
  0xC0        render control (pixel format; 0x0004xxxx = RGB565)
  0xD0        system clip (x_max << 16 | y_max)
  0xD4 / 0xD8 user clip min / max (x << 16 | y)

Display-list opcodes (top byte):
  0x00 TRAP (end, raises TRI 0x780)   0x08 NOP / 0x08008000 INTERRUPT
  0x12 SYNC                            0x18 WPR reg, value
  0x40 LCOFS xy (local offset)         0x48 MOVE xy
  0xA0 BITBLTC fill: ROP, colour, XY(L,R), XY(U,D), anchor xy
  0xA2 BITBLTA copy: ROP, src xy, XY(L,R), XY(U,D), dst xy
       (rect = anchor -L..+R, -U..+D; DirectFB sends L=U=0, i.e. w-1/h-1)
  0x80 POLYGON_4C: colour, 4 x xy
  0x82 POLYGON_4A (textured quad): src xy, src wh, 0, 4 x xy  [from the
       firmware's own lists: 0x82002900 = CLIP|STRANS|0x100]
  0xB0 LINE_C: (colour<<16)|n, 0, n x xy
Flags in the low bits: 0x2000 CLIP, 0x0800 source transparency (colour
key), 0x0002 alpha, 0x40/0x20 source direction, 0x10/0x08 dest direction.

Coordinates are XY(x, y) = (x << 16) | y, signed 16-bit each.
"""


import numpy as np


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _xy(w):
    return _s16(w >> 16), _s16(w)


class TDG:
    BASE = 0x04680000

    def __init__(self, mem, raise_irq, log=None):
        self.mem = mem
        self.raise_irq = raise_irq
        self.log = log
        self.reg = {}
        self.ssr = 0
        self.lcofs = (0, 0)
        # the SH C core may own reg 0x00-0xFF + ssr (fastcpu 2DG mirror):
        # pull() flushes it here, gen tells it to re-adopt after restore()
        self.pull = None
        self.gen = 0
        self.unknown = []      # (addr, word) of opcodes not understood
        self.stats = {}

    def state(self):
        if self.pull is not None:
            self.pull()
        return {"reg": dict(self.reg), "ssr": self.ssr, "lcofs": self.lcofs,
                "runs": getattr(self, "runs", 0)}

    def restore(self, st):
        self.reg = dict(st["reg"])
        self.ssr = st["ssr"]
        self.lcofs = tuple(st["lcofs"])
        self.runs = st.get("runs", 0)
        self.gen += 1

    # -- register file --------------------------------------------------------
    def r32(self, off):
        if off == 0x04:
            return self.ssr
        return self.reg.get(off, 0)

    def read(self, off, size):
        base = off & ~3
        v = self.r32(base)
        sh = (off & 3) * 8
        return (v >> sh) & ((1 << (size * 8)) - 1)

    def write(self, off, size, val):
        base = off & ~3
        sh = (off & 3) * 8
        m = ((1 << (size * 8)) - 1) << sh
        cur = self.reg.get(base, 0)
        new = (cur & ~m) | ((val << sh) & m)
        if base == 0x08:            # SSCR: write 1 to clear status bits
            self.ssr &= ~((val << sh) & m)
            self.reg[base] = new
            return
        self.reg[base] = new
        if base == 0x00 and (new & 1):
            self.reg[0x00] = 0
            self.run(self.reg.get(0x48, 0))

    # -- pixel access -----------------------------------------------------------
    def _clip(self, flags):
        smax = self.reg.get(0xD0, 0)
        x1, y1 = 0, 0
        x2, y2 = _xy(smax) if smax else (0x7FFF, 0x7FFF)
        if flags & 0x2000:
            cx1, cy1 = _xy(self.reg.get(0xD4, 0))
            cx2, cy2 = _xy(self.reg.get(0xD8, 0))
            x1, y1 = max(x1, cx1), max(y1, cy1)
            x2, y2 = min(x2, cx2), min(y2, cy2)
        return x1, y1, x2, y2

    def _rec(self, kind, dst, x, y, w, h, **kw):
        log = getattr(self, "draw_log", None)
        if log is not None:
            ox, oy = self.lcofs
            log.append(dict(kind=kind, dst=dst, x=x + ox, y=y + oy, w=w, h=h,
                            src=self.reg.get(0x4C, 0),
                            sstride=self.reg.get(0x58, 0), **kw))

    def fill(self, flags, colour, x, y, w, h):
        dst = self.reg.get(0x50, 0)
        self._rec("fill", dst, x, y, w, h, colour=colour & 0xFFFF, flags=flags)
        stride = self.reg.get(0x5C, 0) or 800
        ox, oy = self.lcofs
        x += ox
        y += oy
        cx1, cy1, cx2, cy2 = self._clip(flags)
        xa, xb = max(x, cx1), min(x + w - 1, cx2)
        ya, yb = max(y, cy1), min(y + h - 1, cy2)
        if xa > xb or ya > yb:
            return
        c = colour & 0xFFFF
        # 2026-10-26: one strided numpy store when the whole rect sits in
        # one plain RAM buffer and nobody wants per-row hooks (the Python
        # row loop was ~480 iterations for every full-screen clear)
        if getattr(self.mem, "fb_hook", None) is None:
            a0 = dst + (ya * stride + xa) * 2
            res = self._u16view(a0, ((yb - ya) * stride + xb - xa + 1) * 2)
            if res is not None:
                view, i0, fb = res
                idx = ((np.arange(ya, yb + 1)[:, None] - ya) * stride
                       + (np.arange(xa, xb + 1)[None, :] - xa))
                view[i0 + idx.ravel()] = c
                if fb:
                    self.mem.fb_writes += (yb - ya + 1) * (xb - xa + 1) * 2
                return
        row = bytes([c & 0xFF, c >> 8]) * (xb - xa + 1)
        for yy in range(ya, yb + 1):
            self._write_row(dst + (yy * stride + xa) * 2, row)

    def blit(self, flags, sx, sy, x, y, w, h):
        self._rec("blit", self.reg.get(0x50, 0), x, y, w, h, sx=sx, sy=sy, flags=flags,
                  alpha=self.reg.get(0x88, 0))
        src = self.reg.get(0x4C, 0)
        sstride = self.reg.get(0x58, 0) or 800
        dst = self.reg.get(0x50, 0)
        dstride = self.reg.get(0x5C, 0) or 800
        ox, oy = self.lcofs
        x += ox
        y += oy
        cx1, cy1, cx2, cy2 = self._clip(flags)
        key = self.reg.get(0x80, 0) & 0xFFFF if flags & 0x0800 else None
        if self._blit_fast(src, sstride, sx, sy, dst, dstride, x, y, w, h,
                           (cx1, cy1, cx2, cy2), key):
            return
        for j in range(h):
            yy = y + j
            if yy < cy1 or yy > cy2:
                continue
            xa, xb = max(x, cx1), min(x + w - 1, cx2)
            if xa > xb:
                continue
            srow = self._read_row(src + ((sy + j) * sstride + sx + (xa - x)) * 2,
                                  (xb - xa + 1) * 2)
            if key is None:
                self._write_row(dst + (yy * dstride + xa) * 2, srow)
            else:
                # colour key: copy only source pixels != key (vectorised)
                sv = np.frombuffer(srow, dtype="<u2")
                dv = np.frombuffer(self._read_row(dst + (yy * dstride + xa) * 2,
                                                  len(srow)), dtype="<u2").copy()
                m = sv != key
                dv[m] = sv[m]
                self._write_row(dst + (yy * dstride + xa) * 2, dv.tobytes())

    def _u16view(self, a, nbytes):
        """(uint16 view of the whole RAM buffer, element index) when
        [a, a+nbytes) lies in one plain RAM buffer, else None"""
        a = self._phys(a)
        b, o, fb = self.mem._loc(a)
        if b is None or b is self.mem.flash or o & 1 or o + nbytes > len(b):
            return None
        cache = self.__dict__.setdefault("_views", {})
        v = cache.get(id(b))
        if v is None or v[0] is not b:
            v = cache[id(b)] = (b, np.frombuffer(b, dtype="<u2"), fb)
        return v[1], o >> 1, v[2]

    def _blit_fast(self, src, sstride, sx, sy, dst, dstride, x, y, w, h, clip, key):
        """whole-rectangle copy with numpy (one gather/scatter instead of a
        Python loop per row); False -> caller uses the per-row path"""
        cx1, cy1, cx2, cy2 = clip
        ya, yb = max(y, cy1), min(y + h - 1, cy2)
        xa, xb = max(x, cx1), min(x + w - 1, cx2)
        if ya > yb or xa > xb:
            return True
        rows, cols = yb - ya + 1, xb - xa + 1
        s0 = src + ((sy + (ya - y)) * sstride + sx + (xa - x)) * 2
        d0 = dst + (ya * dstride + xa) * 2
        sv = self._u16view(s0, ((rows - 1) * sstride + cols) * 2)
        dv = self._u16view(d0, ((rows - 1) * dstride + cols) * 2)
        if sv is None or dv is None:
            return False
        sarr, si, _ = sv
        darr, di, fb = dv
        if sarr is darr and abs(si - di) < rows * max(sstride, dstride):
            return False                       # overlapping copy: keep row order
        sidx = si + np.add.outer(np.arange(rows) * sstride, np.arange(cols))
        didx = di + np.add.outer(np.arange(rows) * dstride, np.arange(cols))
        pix = sarr[sidx]
        if key is None:
            darr[didx] = pix
        else:
            m = pix != key
            darr[didx[m]] = pix[m]
        if fb:
            self.mem.fb_writes += rows * cols * 2
            if getattr(self.mem, "fb_hook", None) is not None:
                a = self._phys(d0)
                self.mem.fb_hook(a, a + ((rows - 1) * dstride + cols) * 2)
        return True

    @staticmethod
    def _phys(a):
        a &= 0xFFFFFFFF
        if 0x80000000 <= a < 0xC0000000:
            a &= 0x1FFFFFFF
        return a

    def _read_row(self, a, n):
        a = self._phys(a)
        b, o, _ = self.mem._loc(a)
        if b is not None and o + n <= len(b):
            return bytes(b[o:o + n])
        return bytes(self.mem.r8(a + i) for i in range(n))

    def _write_row(self, a, data):
        a = self._phys(a)
        b, o, fb = self.mem._loc(a)
        if b is not None and b is not self.mem.flash and o + len(data) <= len(b):
            b[o:o + len(data)] = data
            if fb:
                self.mem.fb_writes += len(data)
                if getattr(self.mem, "fb_hook", None) is not None:
                    self.mem.fb_hook(a, a + len(data))
            return
        for i, x in enumerate(data):
            self.mem.w8(a + i, x)

    def line(self, flags, colour, pts):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dx, dy = abs(x1 - x0), -abs(y1 - y0)
            sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
            err = dx + dy
            while True:
                self.fill(flags, colour, x0, y0, 1, 1)
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err += dy
                    x0 += sx
                if e2 <= dx:
                    err += dx
                    y0 += sy

    def polygon(self, flags, colour, pts):
        # axis-aligned bounding fill (the GUI uses POLYGON_4C for rects)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.fill(flags, colour, min(xs), min(ys),
                  max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)

    def quad_tex(self, flags, sx, sy, sw, sh, pts):
        """Map source rect (sx,sy,sw,sh) onto the quad's bounding box.
        The GUI only emits axis-aligned quads (corners TL,TR,BR,BL);
        same size -> plain blit, otherwise nearest-neighbour scale."""
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0 = min(xs), min(ys)
        w, h = max(xs) - x0 + 1, max(ys) - y0 + 1
        if sw <= 0 or sh <= 0:
            return
        if (w, h) == (sw, sh):
            self.blit(flags, sx, sy, x0, y0, w, h)
            return
        self.stats["scaled"] = self.stats.get("scaled", 0) + 1
        self._rec("quad", self.reg.get(0x50, 0), x0, y0, w, h, sx=sx, sy=sy,
                  sw=sw, sh=sh)
        src = self.reg.get(0x4C, 0)
        sstride = self.reg.get(0x58, 0) or 800
        dst = self.reg.get(0x50, 0)
        dstride = self.reg.get(0x5C, 0) or 800
        ox, oy = self.lcofs
        cx1, cy1, cx2, cy2 = self._clip(flags)
        key = self.reg.get(0x80, 0) & 0xFFFF if flags & 0x0800 else None
        # nearest-neighbour column map and clip mask, once per quad
        cols = (np.arange(w) * sw // w).astype(np.intp)
        xx = np.arange(w) + x0 + ox
        inside = (xx >= cx1) & (xx <= cx2)
        for j in range(h):
            yy = y0 + j + oy
            if yy < cy1 or yy > cy2:
                continue
            sv = np.frombuffer(self._read_row(
                src + ((sy + j * sh // h) * sstride + sx) * 2, sw * 2),
                dtype="<u2")[cols]
            dv = np.frombuffer(self._read_row(dst + (yy * dstride + x0 + ox) * 2,
                                              w * 2), dtype="<u2").copy()
            m = inside if key is None else inside & (sv != key)
            dv[m] = sv[m]
            self._write_row(dst + (yy * dstride + x0 + ox) * 2, dv.tobytes())

    # -- display list interpreter ---------------------------------------------------
    def run(self, addr, limit=200000):
        rd = lambda a: self.mem.r32(self._phys(a))
        pc = addr
        self.runs = getattr(self, "runs", 0) + 1
        if not hasattr(self, "dsts"):
            self.dsts = set()
        n = 0
        # 2026-10-26: the C core runs the list when it can (exact port of the
        # ops below); it stops BEFORE anything it cannot do and we carry on
        cx = getattr(self.mem, "tdg_cexec", None)
        if (cx is not None and getattr(self, "draw_log", None) is None
                and getattr(self.mem, "fb_hook", None) is None):
            done, pc, n = cx(self, addr, limit)
            if done:
                return
        self.run_from(pc, n, limit)

    def run_from(self, pc, n, limit=200000):
        """The Python list interpreter from pc with n ops already used
        (also where the C core hands over a list it cannot finish)."""
        rd = lambda a: self.mem.r32(self._phys(a))
        if not hasattr(self, "dsts"):
            self.dsts = set()
        while n < limit:
            n += 1
            w = rd(pc)
            op = w >> 24
            flags = w & 0x00FFFFFF
            self.stats[op] = self.stats.get(op, 0) + 1
            if op == 0x00:                      # TRAP
                self.ssr |= 1
                if self.reg.get(0x0C, 0) & 1:
                    self.raise_irq("TDG_TRI")
                return
            if op == 0x18:                      # WPR
                r, v = rd(pc + 4) & 0xFFFF, rd(pc + 8)
                self.reg[r] = v
                if r == 0x50:
                    self.dsts.add(v)
                pc += 12
            elif op == 0x40:                    # LCOFS
                self.lcofs = _xy(rd(pc + 4))
                pc += 8
            elif op == 0x48:                    # MOVE
                pc += 8
            elif op == 0x08:                    # NOP / INTERRUPT
                if w & 0x8000:
                    self.raise_irq("TDG_INI")
                pc += 4
            elif op == 0x12:                    # SYNC
                pc += 4
            elif op == 0xA0:                    # BITBLTC (fill)
                # size words are XY(extent before, extent after) around the
                # anchor point: rect = [x-L, x+R] x [y-U, y+D]. The SH7723
                # DirectFB driver only ever sends L = U = 0 (plain w-1/h-1);
                # the XDJ firmware centres its fills, e.g. the waveform-area
                # clear (439,197)+-(341|342, 121|122) -- reading only the low
                # halves left the splash logo on the player screen.
                colour = rd(pc + 8)
                lr, ud = _xy(rd(pc + 12)), _xy(rd(pc + 16))
                x, y = _xy(rd(pc + 20))
                self.fill(flags, colour, x - lr[0], y - ud[0],
                          lr[0] + lr[1] + 1, ud[0] + ud[1] + 1)
                pc += 24
            elif op == 0xA2:                    # BITBLTA (copy), same extents
                sx, sy = _xy(rd(pc + 8))
                lr, ud = _xy(rd(pc + 12)), _xy(rd(pc + 16))
                x, y = _xy(rd(pc + 20))
                self.blit(flags, sx - lr[0], sy - ud[0], x - lr[0], y - ud[0],
                          lr[0] + lr[1] + 1, ud[0] + ud[1] + 1)
                pc += 24
            elif op == 0x82:                    # POLYGON_4A (textured quad)
                sx, sy = _xy(rd(pc + 4))
                sw, sh = _xy(rd(pc + 8))
                pts = [_xy(rd(pc + 16 + 4 * i)) for i in range(4)]
                self.quad_tex(flags, sx, sy, sw, sh, pts)
                pc += 32
            elif op == 0x80:                    # POLYGON_4C
                colour = rd(pc + 4)
                pts = [_xy(rd(pc + 8 + 4 * i)) for i in range(4)]
                self.polygon(flags, colour, pts)
                pc += 24
            elif op == 0xB0:                    # LINE_C
                cw = rd(pc + 4)
                cnt = cw & 0xFFFF
                pts = [_xy(rd(pc + 12 + 4 * i)) for i in range(cnt)]
                self.line(flags, cw >> 16, pts)
                pc += 12 + 4 * cnt
            else:
                self.unknown.append((pc, w))
                if self.log:
                    self.log(f"2DG: unknown opcode {w:#010x} at {pc:#x}")
                self.ssr |= 1                  # treat as end so the GUI proceeds
                if self.reg.get(0x0C, 0) & 1:
                    self.raise_irq("TDG_TRI")
                return
