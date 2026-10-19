#!/usr/bin/env python3
"""dsp_replay.py RECORD [MAX_CYCLES] -- run OUR C674x core (emu/c6x/cfast.py)
on a recording made by cdj-nxs2-qemu's core (CDJ_C6X_RECORD=<file>, record
format in its hw/cdj/c6x/c66x_priv.h; replay rules as its c6xreplay.c):
every DSP bus read returns the recorded value, every bus write is checked,
host RAM stores and interrupt edges are applied at their recorded cycle,
idle skips repeated. The first difference = where our core (or our
reading of the hardware) differs from a DSP program run that played audio.
Our own code; the recording is the user's own run.
"""
import collections
import mmap
import struct
import sys
sys.path[:0] = ["."]
from emu.c6x.bus import DspBus          # noqa: E402
from emu.c6x import cfast              # noqa: E402
from emu.c6x.decode import decode_bytes  # noqa: E402

LOG_FROM = int(float(__import__("os").environ.get("REPLAY_LOG_FROM", "0")))
IDLE_LO, IDLE_HI = 0xC004C8CC, 0xC004C8CC + 0x60   # XDJ-700 DSP idle loop
ICR_OF = {0x01C01070: (0x01C01068, 0x01C02268), 0x01C02270: (0x01C01068, 0x01C02268)}
SLACK = int(__import__("os").environ.get("REPLAY_SLACK", "300"))
LEN = {ord("R"): 18, ord("W"): 18, ord("I"): 11, ord("S"): 17, ord("N"): 9,
       ord("M"): 21, ord("P"): 9 + 8 + 4096, ord("Z"): 13, ord("H"): 21}


def records(m):
    p, n = 0, len(m)
    while p + 9 <= n:
        t = m[p]
        if t == ord("D"):
            if p + 17 > n:
                return
            ln = 17 + struct.unpack_from("<I", m, p + 13)[0]
        else:
            ln = LEN.get(t)
            if ln is None:
                print("bad record type", t, "at", p)
                return
        if p + ln > n:
            return
        yield p, t, struct.unpack_from("<Q", m, p + 1)[0]
        p += ln


class _Stop(Exception):
    pass


class Replay:
    def __init__(self, path):
        f = open(path, "rb")
        self.m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        self.it = records(self.m)
        self.q = collections.deque()      # (p, type, cycle, inline)
        self.prev_inline_cyc = None
        self.cpu = None
        self.stop = None
        self.n = collections.Counter()
        self.decoded = set()
        self.bus = None
        self.last = {}

    def _fill(self):
        r = next(self.it, None)
        if r is None:
            return False
        p, t, cyc = r
        inl = t in (ord("R"), ord("W"))
        if t in (ord("I"), ord("D")) and self.q and self.q[-1][3] and self.q[-1][2] == cyc:
            inl = True
        elif t in (ord("I"), ord("D")) and not self.q and self.prev_inline_cyc == cyc:
            inl = True
        self.q.append((p, t, cyc, inl))
        return True

    def boundary(self):
        """index of the next record that is NOT consumed inside a step"""
        k = 0
        while True:
            while k >= len(self.q):
                if not self._fill():
                    return None
            if not self.q[k][3]:
                return k
            k += 1

    # ---- the core's bus (MMIO only; RAM is mapped directly)
    def _rw(self, k):
        p, t, cyc, inl = self.q[k]
        return (t,) + struct.unpack_from("<IBI", self.m, p + 9) + (cyc,)

    def _pop_inline(self, kind, a, n, v=None):
        """elastic match: the two cores drift a few cycles apart (stall
        modelling, load timing), so host stores/interrupts recorded just
        before an access are applied first, extra or missing polls of one
        register are tolerated, and only a DIFFERENT access or a different
        written value counts as a divergence."""
        if self.stop:
            return 0
        if LOG_FROM and self.cpu.cycle >= LOG_FROM and self.n["logged"] < 80:
            self.n["logged"] += 1
            nxt = self._rw(0) if self.q and self.q[0][1] in (ord("R"), ord("W")) else (self.q[0][1:3] if self.q else None)
            print(f"  ours {kind} {a:#010x}" + (f"={v:#x}" if v is not None else "") +
                  f" cyc {self.cpu.cycle} pc {self.cpu.pc:#010x} | rec next {nxt}", flush=True)
        K = ord(kind)
        for _ in range(4096):
            while len(self.q) < 64 and self._fill():
                pass
            if not self.q:
                break
            p, t, cyc, inl = self.q[0]
            if t in (ord("I"), ord("D"), ord("S"), ord("H")) and cyc <= self.cpu.cycle + SLACK:
                # a boundary event the other core saw just before this access
                self.q.popleft()
                if t in (ord("I"), ord("D")):
                    self.apply(p, t)
                elif t == ord("S"):
                    self.n["S_inline"] += 1
                self.n["early_" + chr(t)] += 1
                continue
            if t not in (ord("R"), ord("W")):
                break
            tt, ra, rn, rv, rc = self._rw(0)
            if tt == K and ra == a and rn == n:
                self.q.popleft()
                self.n[kind] += 1
                self.last[a] = rv
                if kind == "W" and a in ICR_OF:          # EDMA ICR clears IPR bits
                    for ipr in ICR_OF[a]:
                        if ipr in self.last:
                            self.last[ipr] &= ~rv
                if kind == "W" and rv != (v & 0xFFFFFFFF):
                    self.n["Wbad"] += 1
                    if self.n["Wbad"] <= 20:
                        print(f"  WRITE DIFFERS {a:#010x}/{n} ours {v:#x} recorded {rv:#x} "
                              f"cycle {rc} (ours {self.cpu.cycle}) pc {self.cpu.pc:#010x}", flush=True)
                return rv
            # same access a little further on (an interrupt landed on the
            # other side of it in the two runs): take it out of order
            for k in range(1, 48):
                if k >= len(self.q):
                    break
                pk, tk, ck, ik = self.q[k]
                if tk == K:
                    _, rak, rnk, rvk, rck = self._rw(k)
                    if rak == a and rnk == n:
                        del self.q[k]
                        self.n["reordered"] += 1
                        self.last[a] = rvk
                        if kind == "W" and rvk != (v & 0xFFFFFFFF):
                            self.n["Wbad"] += 1
                            if self.n["Wbad"] <= 20:
                                print(f"  WRITE DIFFERS {a:#010x}/{n} ours {v:#x} recorded {rvk:#x} "
                                      f"cycle {rck} (ours {self.cpu.cycle}) pc {self.cpu.pc:#010x}", flush=True)
                        return rvk
            # their core polled something more times than ours: drop its
            # extra reads of a register we already know
            if tt == ord("R") and ra in self.last:
                self.q.popleft()
                self.last[ra] = rv
                self.n["drop_their_poll"] += 1
                continue
            break
        if kind == "R" and a in self.last:          # our extra poll
            self.n["extra_our_poll"] += 1
            return self.last[a]
        if kind == "W":
            self.n["extra_our_write"] += 1
            if a == 0 and __import__("os").environ.get("REPLAY_STOP_NULL"):
                self.stop = f"write to address 0 at cycle {self.cpu.cycle} pc {self.cpu.pc:#010x}"
                self.cpu.lib.c6x_stop(self.cpu.h)
                return 0
            if self.n["extra_our_write"] <= 10:
                nxt = self._rw(0) if self.q and self.q[0][1] in (ord("R"), ord("W")) else None
                print(f"  extra write by ours {a:#010x}={v:#x} at {self.cpu.cycle} pc {self.cpu.pc:#010x}; "
                      f"recorded next {nxt}", flush=True)
            return 0
        nxt = self._rw(0) if self.q and self.q[0][1] in (ord("R"), ord("W")) else self.q[0][1:3] if self.q else None
        self.stop = (f"DIVERGED: ours {kind} {a:#010x}/{n}" + (f"={v:#x}" if v is not None else "") +
                     f" at cycle {self.cpu.cycle} pc {self.cpu.pc:#010x}; recorded next {nxt}")
        self.cpu.lib.c6x_stop(self.cpu.h)
        return 0

    def read(self, a, n):
        return self._pop_inline("R", a, n)

    def write(self, a, n, v):
        self._pop_inline("W", a, n, v)

    def apply(self, p, t):
        m = self.m
        if t == ord("I"):
            line, lvl = m[p + 9], m[p + 10]
            bit = 1 << line
            if lvl and not self.irq & bit:
                self.cpu.c["ifr"] = self.cpu.c["ifr"] | bit
                self.irq |= bit
            elif not lvl:
                self.irq &= ~bit
            self.n["I"] += 1
        elif t == ord("D"):
            a, ln = struct.unpack_from("<II", m, p + 9)
            data = m[p + 17:p + 17 + ln]
            self.bus.load(a, data)
            pg = a >> 16
            if pg in self.decoded:
                self.decoded.discard(pg)
                self.cpu.drop_code(pg << 16, (pg + 1) << 16)
            self.n["D"] += 1

    def ensure(self, pc):
        pg = pc >> 16
        if pg not in self.decoded:
            base = pg << 16
            self.cpu.add_code(decode_bytes(self.bus.dump(base, 0x10000), base))
            self.decoded.add(pg)

    def run(self, max_cycles):
        self.irq = 0
        maps, pages = [], []
        while True:                       # header: N, M..., P..., Z
            b = self.boundary()
            p, t, cyc, inl = self.q[0]
            if t == ord("N"):
                pass
            elif t == ord("M"):
                maps.append(struct.unpack_from("<III", self.m, p + 9))
            elif t == ord("P"):
                pages.append((p,) + struct.unpack_from("<II", self.m, p + 9))
            elif t == ord("Z"):
                pc = struct.unpack_from("<I", self.m, p + 9)[0]
                self.q.popleft()
                break
            self.q.popleft()
        self.bus = DspBus()
        hostbuf = {}
        for base, size, hid in maps:            # put L2/DDR where the recording had them
            for lo, hi, buf in self.bus.ram:
                if lo == base:
                    hostbuf[hid] = buf
        for p, hid, off in pages:
            hostbuf[hid][off:off + 4096] = self.m[p + 17:p + 17 + 4096]
        self.bus.devices.clear()

        class Mmio:                              # every non-RAM access -> the recording
            def __init__(s, r): s.r = r
            def read(s, a, n): return s.r.read(a, n)
            def write(s, a, n, v): s.r.write(a, n, v)
        mm = Mmio(self)
        self.bus.read_mmio, self.bus.write_mmio = mm.read, mm.write
        orig_read, orig_write = self.bus.read, self.bus.write
        bus = self.bus

        def bread(a, n):
            buf, o = bus._ram(a)
            return orig_read(a, n) if buf is not None else mm.read(a, n)

        def bwrite(a, n, v):
            buf, o = bus._ram(a)
            return orig_write(a, n, v) if buf is not None else mm.write(a, n, v)
        bus.read, bus.write = bread, bwrite
        import os
        if os.environ.get("XDJ_DSP_CORE") == "py":
            from emu.c6x.cpu import Cpu
            cpu = Cpu(bus)
            rp = self

            class PyLib:                               # the bits of the C API we use
                def c6x_stop(self, h):
                    raise _Stop()

                def c6x_skip(self, h, n):
                    for _ in range(16):
                        if n <= 0:
                            break
                        cpu._commit(cpu.cycle)
                        cpu.cycle += 1
                        n -= 1
                    cpu.cycle += n

            def run_cycles(n):
                try:
                    for _ in range(n):
                        rp.ensure(cpu.pc)
                        cpu.step()
                except _Stop:
                    pass
            cpu.lib, cpu.h, cpu.run_cycles = PyLib(), None, run_cycles
            self.cpu = cpu
            ring = collections.deque(maxlen=int(os.environ.get("REPLAY_RING", "300")))

            def tr(c, pc, ep):
                ring.append((c.cycle, pc, " | ".join(i.text for i in ep), c.r[4], c.r[35]))
            cpu.trace = tr
            self.ring = ring
        else:
            self.cpu = cfast.CCpu(bus)
        self.cpu.pc = pc
        self.cpu.code_hook = self.ensure
        lib = self.cpu.lib
        if hasattr(lib, "c6x_skip") and hasattr(lib.c6x_skip, "argtypes"):
            lib.c6x_skip.argtypes = [cfast.ctypes.c_void_p, cfast.u64]
        while not self.stop:
            b = self.boundary()
            if b is None:
                break
            if b > 0 and self.q[0][3] is False:
                pass
            target = self.q[b][2]
            cur = self.cpu.cycle
            if cur < target:
                if cur >= max_cycles:
                    break
                try:
                    self.cpu.run_cycles(target - cur)
                except Exception as e:      # noqa: BLE001
                    if not self.stop:
                        self.stop = f"core stopped: {e}"
                continue
            if b > 0 and self.q[0][1] in (ord("I"), ord("D")):
                p0, t0, c0, i0 = self.q.popleft()      # same-cycle follower
                self.apply(p0, t0)
                continue
            if b > 0:
                tt, ra, rn, rv, rc = self._rw(0)
                if tt == ord("R") and ra in self.last:
                    self.q.popleft()                 # their extra poll
                    self.last[ra] = rv
                    self.n["drop_their_poll"] += 1
                    continue
                if target - cur > 0:
                    pass
                # their access not done yet by ours: let ours run a little more
                if self.n["late"] < 1 or cur - target < 2000:
                    self.n["late"] += 1
                    try:
                        self.cpu.run_cycles(16)
                    except Exception as e:      # noqa: BLE001
                        self.stop = f"core stopped: {e}"
                    continue
                self.stop = (f"core skipped {b} recorded bus accesses before cycle {target}; next "
                             f"{chr(tt)} {ra:#010x} at {rc}, ours pc {self.cpu.pc:#010x}")
                break
            if cur > target:
                self.n["ran_past"] += 1               # drift: apply now
            p, t, cyc, inl = self.q.popleft()
            if t in (ord("I"), ord("D")):
                self.apply(p, t)
            elif t == ord("S"):
                n = struct.unpack_from("<Q", self.m, p + 9)[0]
                # skip only from the idle loop with nothing in flight (as the
                # recording core did); otherwise just run those cycles
                if IDLE_LO <= self.cpu.pc < IDLE_HI and not self.cpu.pend and not self.cpu.sp:
                    lib.c6x_skip(self.cpu.h, n)
                    self.n["S"] += 1
                else:
                    self.n["S_run"] += 1
            elif t == ord("H"):
                rpc = struct.unpack_from("<I", self.m, p + 9)[0]
                self.n["H"] += 1
                if rpc != self.cpu.pc:
                    self.n["Hpc"] += 1
                    if self.n["Hpc"] <= 3:
                        print(f"  hash point pc differs at {cyc}: ours {self.cpu.pc:#010x} recorded {rpc:#010x}")
            elif t == ord("N"):
                self.stop = "a second core in the recording (DSP reset) -- stop"
            if sum(self.n.values()) % 200000 == 0:
                print(f"  cycle {self.cpu.cycle:,} {dict(self.n)}", flush=True)
        print("STOP:", self.stop)
        for lo, hi in ((0x11837BC0, 0x11837C00), (0x11837CC0, 0x11837CE0), (0x11838140, 0x11838150), (0x11838180, 0x118381C0)):
            print(f"  {lo:#010x}:", " ".join(f"{self.bus.read(a, 4):08x}" for a in range(lo, hi, 4)))
        for e in list(getattr(self, "ring", []))[-int(__import__("os").environ.get("REPLAY_SHOW", "0")):]:
            print(f"   {e[0]} {e[1]:#010x} a4={e[3]:#x} b3={e[4]:#x}  {e[2]}")
        print(f"cycle {self.cpu.cycle:,} pc {self.cpu.pc:#010x} counts {dict(self.n)}")


if __name__ == "__main__":
    Replay(sys.argv[1]).run(int(float(sys.argv[2])) if len(sys.argv) > 2 else 1 << 62)
