"""SH7724 IIC0 (I2C master) + the chips on the deck's I2C bus.

Before this model every register read 0, so ICSR said "SCL and SDA stuck
low": the driver's bus-idle wait (0x8CB1D54: (ICSR & 0xD0) == 0xC0) spun
~1.8 M times per boot and every transfer failed.

Registers (8-bit, R01UH0174 section 30): ICDR +0, ICCR +4 (ICE 7, RACK 6,
TRS 4, BBSY 2, SCP 0), ICSR +8 (SCLM 7, SDAM 6, BUSY 4, AL 3, TACK 2,
WAIT 1, DTE 0; flags cleared by writing 0), ICIC +0xC, ICCL +0x10,
ICCH +0x14.

Transfers are instantaneous. Unknown slaves ACK and read 0x00 -- every
address the firmware uses is recorded in `self.seen` so real device
models can be added as they are identified.
"""

BASE = 0x04470000


class Slave:
    """Register-file slave: first written byte selects the register."""
    def __init__(self, regs=None):
        self.regs = dict(regs or {})
        self.ptr = 0
        self.first = True

    def start(self):
        self.first = True

    def write(self, b):
        if self.first:
            self.ptr, self.first = b, False
        else:
            self.regs[self.ptr] = b
            self.ptr = (self.ptr + 1) & 0xFF

    def read(self):
        v = self.regs.get(self.ptr, 0)
        self.ptr = (self.ptr + 1) & 0xFF
        return v


class AuthChip:
    """The player's authentication chip (7-bit address 0x10 on the 700, seen in boot logs):
    a read answers the command byte written before it -- 0x00 -> 0x05,
    0x01 -> 0x01, anything else the data register's reset value 0xFF.
    Without it every answer read 0x00 and the firmware's auth check fails
    (cdj-nxs2-qemu documents the same answers for the NXS/NXS2 decks)."""
    ANSWERS = {0x00: 0x05, 0x01: 0x01}

    def __init__(self):
        self.cmd = 0

    def start(self):
        pass

    def write(self, b):
        self.cmd = b

    def read(self):
        return self.ANSWERS.get(self.cmd, 0xFF)


AUTH_ADDR = 0x10


class IIC:
    def __init__(self, dev):
        self.dev = dev
        self.r = {0x4: 0, 0xC: 0, 0x10: 0, 0x14: 0}
        self.busy = False
        self.flags = 0              # AL TACK WAIT DTE
        self.phase = "idle"
        self.slave = None
        self.addr = None
        self.rx = 0
        self.slaves = {}
        self.due = None             # cycle at which a received byte lands
        self.seen = {}              # 7-bit address -> [writes, reads]
        self.log = []

    @staticmethod
    def owns(addr):
        return BASE <= (addr & 0x1FFFFFFF) < BASE + 0x20

    def _get(self, a):
        s = self.slaves.get(a)
        if s is None:
            s = self.slaves[a] = AuthChip() if a == AUTH_ADDR else Slave()
        return s

    def read(self, addr, size):
        v = 0
        for i in range(size):
            v |= self._rb(((addr & 0x1FFFFFFF) - BASE) + i) << (8 * i)
        self._irq()
        return v

    def write(self, addr, size, val):
        for i in range(size):
            self._wb(((addr & 0x1FFFFFFF) - BASE) + i, (val >> (8 * i)) & 0xFF)
        self._irq()

    def tick(self):
        if self.due is not None and self.dev.cyc >= self.due:
            self.due = None
            if self.phase == "rx" and self.slave is not None:
                self.rx = self.slave.read()
                self.seen[self.addr][1] += 1
                if len(self.log) < 400:
                    self.log.append(("rd", hex(self.addr), hex(self.rx)))
                self.flags |= 0x03              # WAIT | DTE: byte received
            self._irq()

    def cycles_to_next(self):
        return None if self.due is None else max(1, self.due - self.dev.cyc)

    IRQS = ((0x01, "IIC0_DTEI"), (0x02, "IIC0_WAITI"),
            (0x04, "IIC0_TACKI"), (0x08, "IIC0_ALI"))

    def _irq(self):
        """Level interrupts: ICSR flag AND its ICIC enable (same bit)."""
        on = self.flags & self.r.get(0xC, 0)
        for bit, name in self.IRQS:
            if on & bit:
                self.dev.raise_irq(name)
            else:
                self.dev.pending.discard(name)

    def _rb(self, o):
        if o == 0x8:
            if not self.busy:
                return 0xC0 | (self.flags & 0x0F)
            return 0xD0 | (self.flags & 0x0F)
        if o == 0x0:
            v = self.rx
            if self.phase == "rx":
                self.flags &= ~0x01             # DTE: data taken
            return v
        return self.r.get(o, 0)

    def _wb(self, o, v):
        if o == 0x4:                            # ICCR
            old = self.r[0x4]
            self.r[0x4] = v & ~0x01
            if not v & 0x80:                    # ICE off: reset
                self.busy, self.phase, self.flags = False, "idle", 0
                return
            if v & 0x04 and not v & 0x01:       # BBSY=1: (repeated) start
                self.busy = True
                self.phase = "addr"
                self.flags = (self.flags & ~0x04) | 0x01   # DTE: write address
            elif self.busy and not v & 0x04 and not v & 0x01:
                self.busy = False               # stop
                self.phase = "idle"
                self.flags |= 0x02
            return
        if o == 0x8:                            # ICSR: write 0 to clear
            was = self.flags
            self.flags &= v | 0xF0
            if self.phase == "rx" and was & 0x02 and not self.flags & 0x02:
                # releasing WAIT clocks the next byte in from the slave
                self.due = self.dev.cyc + 2000
            return
        if o == 0x0:                            # ICDR
            if self.phase == "addr":
                self.addr = v >> 1
                self.seen.setdefault(self.addr, [0, 0])
                self.slave = self._get(self.addr)
                self.slave.start()
                self.phase = "rx" if v & 1 else "tx"
                if len(self.log) < 400:
                    self.log.append(("start", hex(self.addr), "R" if v & 1 else "W"))
            elif self.phase == "tx" and self.slave is not None:
                self.slave.write(v)
                self.seen[self.addr][0] += 1
                if len(self.log) < 400:
                    self.log.append(("wr", hex(self.addr), hex(v)))
            self.flags = (self.flags & ~0x04) | 0x03    # ACK, WAIT, DTE
            return
        self.r[o] = v
