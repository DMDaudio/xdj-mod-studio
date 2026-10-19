"""FastSH4: the C interpreter core (emu/fastcore.c) behind the SH4 API.

Drop-in for emu.sh4.SH4 as Machine.cpu. The C core runs the instruction
stream against the same bytearrays MachineMem owns; everything that is not
plain memory (peripherals) is a per-byte callback into MachineMem.dev,
exactly like MachineMem.r8phys/w8phys. Between slices this module does what
sh4.py did per instruction: interrupt delivery, device ticks (1 cycle per
loop iteration, as sh4.py), the idle-task fast-forward, and SLEEP.

Falls back to the Python core (same state, converted both ways):
  - whenever cpu.trace is set (per-instruction hooks are a debugging tool;
    Machine.run(stop_on_pc=...) and friends use them),
  - for LDTLB and with the MMU on (the firmware runs with it off).

Checkpoints are interchangeable with the Python core (same __getstate__).
Select with XDJ_CORE=py to force the Python core everywhere.
"""
import ctypes
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "fastcore.c")
LIB = os.path.join(HERE, "_fastcore.dll" if sys.platform == "win32"
                   else "_fastcore.so")

R_LIMIT, R_IDLE, R_SLEEP, R_IO, R_FALLBACK = range(5)
MD, RB, BL = 1 << 30, 1 << 29, 1 << 28
SLICE = 8192

u32, u64, vp = ctypes.c_uint32, ctypes.c_uint64, ctypes.c_void_p
IO_RD = ctypes.CFUNCTYPE(u32, u32)
IO_WR = ctypes.CFUNCTYPE(None, u32, u32)
IO_RDS = ctypes.CFUNCTYPE(u32, u32, u32)
IO_WRS = ctypes.CFUNCTYPE(None, u32, u32, u32)

_REGS = ("pc", "pr", "gbr", "vbr", "mach", "macl", "sr", "ssr", "spc", "sgr",
         "dbr", "tra", "expevt", "intevt", "tea", "pteh", "ptel", "ptea",
         "ttb", "mmucr", "ccr", "fpscr", "fpul")


class Core(ctypes.Structure):
    _fields_ = ([("r", u32 * 16), ("alt", u32 * 8)] +
                [(n, u32) for n in _REGS] +
                [("fr", u32 * 32), ("steps", u64), ("cycles", u64),
                 ("sleeping", u32), ("low_is_ram", u32),
                 ("flash", vp), ("flash_len", u32), ("pad0", u32),
                 ("ram0", vp), ("ram1", vp), ("fb0", vp), ("fb1", vp),
                 ("misc", vp), ("sq", vp), ("fb_writes", u64),
                  ("io_rd", IO_RD), ("io_wr", IO_WR),
                  ("io_rds", IO_RDS), ("io_wrs", IO_WRS),
                  ("cs6_addr", u32), ("cs6_ctrl", u32), ("cs6_run", u32),
                  ("cs6_bulk", u64),
                 ("idle_pc", u32), ("idle_skip", u32),
                 ("io_break", u32), ("exc_n", u32),
                 ("exc", (u32 * 7) * 32), ("exc_lost", u32), ("pad1", u32),
                 ("hpid_buf", vp), ("hpid_n", u64), ("hpid_cap", u64),
                 ("hpid_acc", u32), ("hpid_addr", u32), ("hpid_cnt", ctypes.c_uint8),
                 ("hpid_pad", ctypes.c_uint8 * 3), ("hpid_bytes", u64),
                 ("cs6_py", u32), ("cs6_pad", u32)])


_lib = None


def load_lib():
    """Compile fastcore.c if needed (clang/cc) and load it. None if no
    compiler is available -- callers fall back to the Python core."""
    global _lib
    if _lib is not None:
        return _lib
    try:
        # a bundled app ships the compiled library without the source
        # (Windows: the app ships a prebuilt DLL and there is no compiler)
        if sys.platform != "win32" and os.path.exists(SRC) and (not os.path.exists(LIB) or
                os.path.getmtime(LIB) < os.path.getmtime(SRC)):
            tmp = LIB + ".tmp"
            subprocess.check_call(["cc", "-O2", "-shared", "-fPIC", "-o", tmp,
                                   SRC, "-lm"])
            os.replace(tmp, LIB)
        lib = ctypes.CDLL(LIB)
    except (OSError, subprocess.CalledProcessError):
        return None
    lib.core_run.argtypes = [ctypes.POINTER(Core), u64, ctypes.POINTER(u64)]
    lib.core_run.restype = ctypes.c_int
    lib.core_enter_interrupt.argtypes = [ctypes.POINTER(Core), u32, u32]
    lib.core_set_sr.argtypes = [ctypes.POINTER(Core), u32]
    lib.core_read_virt.argtypes = [ctypes.POINTER(Core), u32, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_int)]
    lib.core_read_virt.restype = u32
    lib.core_hpid.argtypes = [ctypes.POINTER(Core), ctypes.POINTER(u64)]
    lib.core_hpid.restype = ctypes.POINTER(u32)
    if lib.core_sizeof() != ctypes.sizeof(Core):
        raise RuntimeError("fastcore.c / fastcpu.py struct layout mismatch")
    _lib = lib
    return lib


def available():
    return os.environ.get("XDJ_CORE", "fast") != "py" and load_lib() is not None


def _f2u(x):
    try:
        return struct.unpack("<I", struct.pack("<f", float(x)))[0]
    except OverflowError:
        return 0x7F800000 if x > 0 else 0xFF800000


def _u2f(u):
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


class FastSH4:
    def __init__(self, mem, machine=None):
        object.__setattr__(self, "core", Core())
        object.__setattr__(self, "lib", load_lib())
        self.mem = mem
        self.machine = machine
        self.utlb = []
        self.trace = None
        self.native_hook = None
        self.fpu = None
        self._pins = None
        self._pin_ids = None
        self._py = None
        dev_rd, dev_wr = self._io_rd, self._io_wr
        self._cb_rd = IO_RD(dev_rd)
        self._cb_wr = IO_WR(dev_wr)
        self._cb_rds = IO_RDS(self._io_rds)
        self._cb_wrs = IO_WRS(self._io_wrs)
        self.core.io_rd = self._cb_rd
        self.core.io_wr = self._cb_wr
        self.core.io_rds = self._cb_rds
        self.core.io_wrs = self._cb_wrs
        self.core.idle_pc = 0x08D6A7C0
        self.reset()

    # -- register access (SH4-compatible) ----------------------------------
    def __getattr__(self, name):
        if name in _REGS or name in ("steps", "cycles"):
            return getattr(self.core, name)
        if name == "sleeping":
            return bool(self.core.sleeping)
        raise AttributeError(name)

    def __setattr__(self, name, v):
        if name == "sr":
            self.lib.core_set_sr(ctypes.byref(self.core), v & 0xFFFFFFFF)
        elif name in _REGS or name in ("steps", "cycles"):
            setattr(self.core, name, v & (0xFFFFFFFF if name in _REGS
                                          else 0xFFFFFFFFFFFFFFFF))
        elif name == "sleeping":
            self.core.sleeping = 1 if v else 0
        else:
            object.__setattr__(self, name, v)

    def gr(self, i):
        return self.core.r[i]

    def set_gr(self, i, v):
        self.core.r[i] = v & 0xFFFFFFFF

    def t(self):
        return self.core.sr & 1

    def set_t(self, v):
        self.core.sr = (self.core.sr | 1) if v else (self.core.sr & ~1)

    @property
    def hpid(self):
        """The DSP-image upload log: list of (dsp_addr, value) 32-bit words
        written through the CS6 data port 0x180C0000. Grows in C; read here."""
        c = self.core
        n = ctypes.c_uint64(0)
        p = self.lib.core_hpid(ctypes.byref(c), ctypes.byref(n))
        if not p or not n.value:
            return []
        buf = ctypes.cast(p, ctypes.POINTER(ctypes.c_uint32 * (n.value * 2)))
        return [(buf.contents[i], buf.contents[i + 1])
                for i in range(0, n.value * 2, 2)]

    def imask(self):
        return (self.core.sr >> 4) & 0xF

    def mmu_on(self):
        return bool(self.core.mmucr & 1)

    @property
    def b0(self):
        c = self.core
        return list(c.alt) if c.sr & RB else list(c.r[:8])

    @property
    def b1(self):
        c = self.core
        return list(c.r[:8]) if c.sr & RB else list(c.alt)

    @property
    def rhi(self):
        return list(self.core.r[8:16])

    @property
    def fr(self):
        f = self.core.fr
        return [[_u2f(f[i]) for i in range(16)],
                [_u2f(f[16 + i]) for i in range(16)]]

    def translate(self, addr, write=False, fetch=False):
        from .sh4 import SH4
        return SH4.translate(self, addr, write, fetch)

    def _rd(self, a, n, write=False, fetch=False):
        from .sh4 import SH4
        return SH4._rd(self, a, n, write, fetch)

    def r8(self, a):
        return self._rd(a, 1)

    def r32(self, a):
        return self._rd(a, 4)

    # -- state --------------------------------------------------------------
    def reset(self):
        c = self.core
        for i in range(16):
            c.r[i] = 0
        for i in range(8):
            c.alt[i] = 0
        c.pc = 0xA0000000
        c.pr = 0
        c.sr = MD | RB | BL | 0xF0 | (1 << 15)
        c.vbr = 0
        c.sleeping = 0
        if c.hpid_buf:
            ctypes.free(ctypes.cast(c.hpid_buf, ctypes.c_void_p))
        c.hpid_buf = 0
        c.hpid_n = 0
        c.hpid_cap = 0
        c.hpid_acc = 0
        c.hpid_addr = 0
        c.hpid_cnt = 0

    def _cs6_out(self):
        """Flush the C DSP-bridge registers to the Python device (which
        owns save/load and the Python-core path)."""
        if self.core.cs6_py:            # Python owns CS6 outright
            return
        d = self.mem.dev.cs6
        d["addr"], d["ctrl"], d["run"] = (self.core.cs6_addr & 0xFFFFFFFF,
                                          self.core.cs6_ctrl & 0xFFFFFFFF,
                                          self.core.cs6_run & 0xFFFFFFFF)
        d["bulk"] = self.core.cs6_bulk

    def _cs6_in(self):
        """Adopt the Python device's DSP-bridge registers into C."""
        self.core.cs6_py = 1 if getattr(self.mem.dev, "cs6_py", False) else 0
        d = self.mem.dev.cs6
        self.core.cs6_addr = d.get("addr", 0) & 0xFFFFFFFF
        self.core.cs6_ctrl = d.get("ctrl", 0) & 0xFFFFFFFF
        self.core.cs6_run = d.get("run", 0) & 0xFFFFFFFF
        self.core.cs6_bulk = d.get("bulk", 0) & 0xFFFFFFFFFFFFFFFF

    def __getstate__(self):
        self._cs6_out()
        c = self.core
        d = {n: getattr(c, n) for n in _REGS}
        d.update(b0=self.b0, b1=self.b1, rhi=self.rhi, fr=self.fr,
                 fr_raw=list(c.fr), utlb=list(self.utlb),
                 sleeping=bool(c.sleeping), steps=c.steps, cycles=c.cycles)
        return d

    def __setstate__(self, d):
        c = self.core
        for n in _REGS:
            setattr(c, n, d.get(n, 0) & 0xFFFFFFFF)
        cur, alt = (d["b1"], d["b0"]) if c.sr & RB else (d["b0"], d["b1"])
        for i in range(8):
            c.r[i] = cur[i] & 0xFFFFFFFF
            c.alt[i] = alt[i] & 0xFFFFFFFF
            c.r[8 + i] = d["rhi"][i] & 0xFFFFFFFF
        if d.get("fr_raw"):
            for i in range(32):
                c.fr[i] = d["fr_raw"][i]
        else:
            for b in range(2):
                for i in range(16):
                    c.fr[b * 16 + i] = _f2u(d["fr"][b][i])
        self.utlb = list(d.get("utlb", []))
        c.sleeping = 1 if d.get("sleeping") else 0
        c.steps = d.get("steps", 0)
        c.cycles = d.get("cycles", 0)
        if c.hpid_buf:
            ctypes.free(ctypes.cast(c.hpid_buf, ctypes.c_void_p))
        c.hpid_buf = 0
        c.hpid_n = 0
        c.hpid_cap = 0
        c.hpid_acc = 0
        c.hpid_addr = 0
        c.hpid_cnt = 0
        self._cs6_in()

    # -- memory binding -------------------------------------------------------
    def _bind(self):
        m = self.mem
        bufs = (m.ram0, m.ram1, m.fb[0], m.fb[1], m.misc, m.sq)
        ids = tuple(id(b) for b in bufs) + (id(m.flash),)
        if ids == self._pin_ids:
            return
        pins = [(ctypes.c_char * len(b)).from_buffer(b) for b in bufs]
        fl = ctypes.c_char_p(bytes(m.flash)) if not isinstance(m.flash, bytes) \
            else ctypes.c_char_p(m.flash)
        c = self.core
        c.ram0, c.ram1, c.fb0, c.fb1, c.misc, c.sq = \
            [ctypes.addressof(p) for p in pins]
        c.flash = ctypes.cast(fl, vp).value
        c.flash_len = len(m.flash)
        self._pins = (pins, fl, m.flash)
        self._pin_ids = ids

    def _io_rd(self, a):
        try:
            return self.mem.dev.read(a, 1) & 0xFF
        except Exception as e:           # a device bug must not kill the core
            self._io_err = repr(e)
            return 0

    def _io_wr(self, a, v):
        # End the slice only when the write can change what interrupt the
        # CPU should take next: a new pending source, or an INTC register
        # (IPR/IMR/IMCR). Most device writes (GPIO strobes, DMA/2DG setup)
        # do neither, and breaking on each cost ~half the run time.
        dev = self.mem.dev
        try:
            before = len(dev.pending)
            dev.write(a, 1, v)
            if len(dev.pending) != before or (a & 0x1FFF0000) == 0x04080000:
                self.core.io_break = 1
        except Exception as e:
            self._io_err = repr(e)
            self.core.io_break = 1

    def _io_rds(self, a, n):
        # One crossing instead of n: the device still sees n identical
        # byte accesses in order (same triggers, same FIFO pops), so this
        # is exactly _io_rd x n with less ctypes overhead.
        try:
            # Fast path (XDJ_USB_FAST=1): single word-sized dev.read where
            # trivially safe (USB + generic stub); else the loop below.
            try:
                from emu.usb_fast import fast_io_read
                fv = fast_io_read(self.mem.dev, a, n)
                if fv is not None:
                    return fv
            except ImportError:
                pass
            v = 0
            for i in range(n):
                v |= self.mem.dev.read(a + i, 1) << (8 * i)
            return v
        except Exception as e:
            self._io_err = repr(e)
            return 0

    def _io_wrs(self, a, v, n):
        dev = self.mem.dev
        try:
            # NOTE: word-sized write batching (usb_fast.fast_io_write) is
            # intentionally NOT used: even generic-stub batching changes
            # UI timing enough to divert browse navigation (337 vs 1143
            # ops). Writes stay byte-wise; reads are the safe win.
            before = len(dev.pending)
            for i in range(n):
                dev.write(a + i, 1, (v >> (8 * i)) & 0xFF)
            if len(dev.pending) != before or (a & 0x1FFF0000) == 0x04080000:
                self.core.io_break = 1
        except Exception as e:
            self._io_err = repr(e)
            self.core.io_break = 1

    def _drain_exc(self):
        c = self.core
        mc = self.machine
        for i in range(c.exc_n):
            e = tuple(c.exc[i])
            if mc is not None:
                mc.excs.append(e[:3])
                mc.exc_total += 1
                if len(mc.excs_first) < 16:
                    mc.excs_first.append(e)
        if mc is not None:
            mc.exc_total += c.exc_lost
        c.exc_n = 0
        c.exc_lost = 0

    # -- Python-core fallback ---------------------------------------------------
    def _py_run(self, limit):
        from .sh4 import SH4
        from .sh4fpu import FPU
        py = SH4(self.mem)
        py.__setstate__(self.__getstate__())
        py.fpu = FPU(py)
        py.mem = self.mem
        mc = self.machine
        user = self.trace
        if mc is not None:
            def tr(pc, op):
                mc._trace(pc, op)
                if user is not None:
                    return user(pc, op)
            py.trace = tr
            py.native_hook = mc._native
            saved = mc.cpu
            mc.cpu = py
            mc._hook_exc()
            self.mem.cpu = py
        else:
            py.trace = user
        try:
            res = py.run(limit=limit)
        finally:
            if mc is not None:
                mc.cpu = saved
                self.mem.cpu = self
            st = py.__getstate__()
            st.pop("fr_raw", None)
            self.__setstate__(st)
            self.core.low_is_ram = 1 if self.mem.low_is_ram else 0
        return res

    # -- run --------------------------------------------------------------------
    def run(self, limit=1000000):
        if self.trace is not None or self.mmu_on():
            return self._py_run(limit)
        lib, c, mem = self.lib, self.core, self.mem
        dev = mem.dev
        self._bind()
        mc = self.machine
        c.idle_skip = 1 if (mc is None or getattr(mc, "idle_skip", True)) else 0
        c.cs6_py = 1 if getattr(dev, "cs6_py", False) else 0
        de = getattr(dev, "dspemu", None)
        if de is not None and de.clock is None:     # time-lock the real DSP
            de.clock = lambda c=c: c.cycles
        c.low_is_ram = 1 if mem.low_is_ram else 0
        c.fb_writes = 0
        done = u64()
        n = 0
        cref = ctypes.byref(c)
        try:
            while n < limit:
                if dev.pending and not (c.sr & BL):
                    got = dev.take_interrupt((c.sr >> 4) & 0xF)
                    if got is not None:
                        lib.core_enter_interrupt(cref, got[0], got[1])
                if c.sleeping:
                    cyc = dev.cycles_to_next_event()
                    mem.tick(min(max(cyc, 100), 4_000_000))
                    n += 1
                    continue
                cte = dev.cycles_to_next_event()
                k = min(limit - n, SLICE, cte if cte > 0 else 64)
                k = max(k, 1)
                reason = lib.core_run(cref, k, ctypes.byref(done))
                d = done.value
                if d:
                    mem.tick(d)
                    n += d
                if c.exc_n or c.exc_lost:
                    self._drain_exc()
                if reason == R_IDLE:
                    cyc = dev.cycles_to_next_event()
                    if cyc > 0:
                        mem.tick(min(cyc + 16, 4_000_000))
                        if mc is not None:
                            mc.idle_cycles += cyc
                    c.pc = c.pr
                    n += 1
                elif reason == R_FALLBACK:
                    mem.fb_writes += c.fb_writes
                    c.fb_writes = 0
                    mem.low_is_ram = bool(c.low_is_ram)
                    self._py_run(256)
                    self._bind()
                    n += 256
        finally:
            mem.fb_writes += c.fb_writes
            c.fb_writes = 0
            mem.low_is_ram = bool(c.low_is_ram)
        return f"limit {limit} reached at {c.pc:#010x}"
