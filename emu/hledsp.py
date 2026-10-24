"""heldsp.py -- behavioral (HLE) model of the TI C674x audio DSP.

What is modeled (all values measured live in the emulator, Oct 2026):
  * Mailbox RAM: the DSP shared window 0x11837Bxx/0x118381xx behind the
    CS6/HPI bridge (HPIC 0x18000000, HPIA 0x18040000, auto-inc HPID
    0x18080000, HPID 0x180C0000). Reads return live protocol answers;
    everything MAIN wrote is echoed back (real shared-RAM semantics --
    the stock stub answers 0, which is wrong for readbacks).
  * Protocol (captured across the PLAY transition, py core):
      W 0x11837BA0 = 0x00000003 once   = PLAY command (fire-and-forget)
      per-cycle cmd block: BC0=0x00100000 BD0=0x3C BD8=0x00100000 BDC=0x18
      BEC doorbell strobes 1,0,1,0...; C00/C04/C50-C60 written as 0.
      per-cycle reads: BF8 CCC CD0 8180/84 81A0/A4 BF0/BF4/BFC C00/C04
      + cmd echo BC0-BDC + DSP->MAIN streams C10/C30/C50 (8/8/5 words
      copied to MAIN 0x0B6C50D4/F4/114 every cycle).
  * Play clock: while MAIN mode (0x0B6C4264) == 5, pos_samples advances
    with emulated time (44100 Hz). Served as the model's position word
    on the C10 stream head; logged.
  * TIME handshake (Oct 2026, cache/emu_track_loaded.pkl lineage):
      SH position word [0x09D246E0] (75 Hz, = pos_samples//588) and
      POSL [0x0C0E6D1C] (= 2x, 150/s -- the time digits' source; its
      SH_POS twin freezes on a real deck, sec. 103) are advanced by
      tick() while mode==5; POSL -> snapshot copy
      0x08DC8B9E (384 B 0x0C0E6C90->0x0C0E6AF4, POSL @+140) ->
      formatter 0x08DBC104. TRACKLEN [0x0BA3C050] is set to a valid
      nonzero (8000000, gate: POS<=TL) when a track is loaded
      (mode 3/5) so REMAIN/TIME render numbers instead of dashes.
      POSL 150/s verified: 200->00:01.334, 2000->00:13.334,
      20000->02:13.334, 88200->09:48.000. Feeder DMA to CS6 bulk
      completes (P2-mask fix) but the feeder issues no further DMA
      during PLAY in the emu (bulk stays 0, py core): it waits on a
      DSP consumption message beyond single-word HPI answers (30+
      prior probes: CD0/BF8/stream patterns all ignored) -- audio
      streaming stays stubbed; TIME does not need it.
    Guest-RAM writes by the model: [0x09D246E0] + [0x0C0E6D1C] +
      [0x0BA3C050] ONLY,
    ONLY when XDJ_HLE_DSP=1 (else tick() never runs: hle is None).
    Rekordbox audio files are still read-only (ffmpeg decode only).
  * Shadow audio: the loaded track is identified by byte-matching the
    feeder buffers against stick files, decoded on the Mac with ffmpeg
    (s16 stereo 44.1k), and the shadow playhead follows the play clock.
    Proof artifacts: /tmp/hle_dsp.wav + buffer-correlation numbers.

Gating: active only when XDJ_HLE_DSP=1 (checked in SH7724.__init__).
Default off: hooks never installed, stock behavior byte-identical
(hle is None; tick() never runs; sh4test unaffected).
py core: full mailbox model. C core: serves CS6 in C (bypasses this
file); tick()/TIME-handshake/shadow-audio still run -- mailbox stays
stock-stub there (CCC=0 stock already = playable, no E-8302; CD0 not
required for TIME).
"""
import os
import struct
import subprocess

MODE_ADDR = 0x0B6C4264
MODE_PLAYING = 5
MODE_PAUSED = 3   # track loaded, at cue (TIME handshake also arms here)
BUF0, BUF1, BLOCK = 0x0E910B00, 0x0E91FB40, 0x24C0
CPUFREQ = 500_000_000
SAMPLE_RATE = 44100
# TIME handshake (SH side; all verified by forcing in emu_track_loaded.pkl):
SH_POS = 0x09D246E0    # SH position word (75 Hz units)
POSL = 0x0C0E6D1C      # live time-struct position (150 Hz = 2x SH_POS);
                       # what the time digits + the phase meter read. Frozen
                       # on a real deck's SH_POS twin, driven there by the DSP
POS_DIV = 588          # 44100 Hz samples -> 75 Hz SH units (150/s POSL)
TRACKLEN = 0x0BA3C050  # gate: formatter needs nonzero and POS<=TL
FIXED_TL = 8000000     # ~148 h at 150/s: always passes, proven no crash

CCC = 0x11837CCC   # decode status: 0 = playable (1 -> E-8302 on deck)
CD0 = 0x11837CD0   # play state: 1 = playing look
BF8 = 0x11837BF8   # DSP status: 0 = ok
BA0 = 0x11837BA0   # PLAY command register (3 = play, fire-and-forget)
FFF0 = 0x1183FFF0  # DSP run flag
FFF4 = 0x1183FFF4  # DSP ready flag -> 1
STREAMS = (0x11837C10, 0x11837C30, 0x11837C50)


def enabled():
    return os.environ.get("XDJ_HLE_DSP") == "1"


class HleDsp:
    def __init__(self, dev):
        self.dev = dev          # SH7724 (back-pointer; mem via dev.mem)
        self.echo = {}          # DSP addr -> u32 last written by MAIN
        self._pend = {}         # addr -> [value, nbytes] (byte assembly)
        self.cmd_log = []       # (dspaddr, value) BA0 commands observed
        self.pos_samples = 0    # MODEL position word: advances on play clock
        self._stream_n = {}     # stream base -> words served
        self.polls = 0
        self.track_path = None
        self.track_off = None   # file offset matching BUF0 content
        self.pcm = None         # decoded s16 stereo bytes
        self.pcm_frames = 0
        self.track_duration = 0.0
        self.primed = False
        self.n_ticks = 0

    # -- CS6 ownership (called from SH7724 when self is installed) --------
    def read(self, a, size):
        off = a - 0x18000000
        if off < 4:  # HPIC: echo control (stock behavior)
            return (self.dev.cs6["ctrl"] >> (off * 8)) & ((1 << (size * 8)) - 1)
        if 0xC0000 <= off < 0xC0004:  # HPID single-word port
            self.polls += 1
            return self._hpid() >> ((off - 0xC0000) * 8) & ((1 << (size * 8)) - 1)
        if 0x80000 <= off < 0x80004:  # auto-inc port (DSP streams)
            return self._stream() >> ((off - 0x80000) * 8) & ((1 << (size * 8)) - 1)
        return 0

    def write(self, a, size, val):
        off = a - 0x18000000
        dev = self.dev
        if off < 4:  # HPIC control (byte-split RMW, stock behavior)
            sh = off * 8
            m = ((1 << (size * 8)) - 1) << sh
            dev.cs6["ctrl"] = ((dev.cs6["ctrl"] & ~m) | ((val << sh) & m)) & 0xFFFFFFFF
            return
        if 0x40000 <= off < 0x40004:  # HPIA latch (byte-split RMW)
            sh = (off - 0x40000) * 8
            m = ((1 << (size * 8)) - 1) << sh
            dev.cs6["addr"] = ((dev.cs6["addr"] & ~m) | ((val << sh) & m)) & 0xFFFFFFFF
            return
        if 0x80000 <= off < 0x80100:  # bulk FIFO: accept + count (stock)
            dev.cs6["bulk"] += size
            return
        if 0xC0000 <= off < 0xC0004:  # HPID data port
            addr = dev.cs6["addr"]
            if addr == FFF0:
                dev.cs6["run"] = val & ((1 << (size * 8)) - 1)
            b = off - 0xC0000
            v, n = self._pend.get(addr, (0, 0))
            v |= (val & 0xFF) << (b * 8)
            n += 1
            if n >= 4:
                self.echo[addr] = v & 0xFFFFFFFF
                if addr == BA0:
                    self.cmd_log.append(v & 0xFFFFFFFF)
                self._pend.pop(addr, None)
            else:
                self._pend[addr] = (v, n)
            return

    def _hpid(self):
        addr = self.dev.cs6["addr"]
        if addr == FFF4:
            return 1
        if addr == CCC:
            return 0            # playable: never E-8302
        if addr == CD0:
            return 1 if self._mode() == MODE_PLAYING else 0
        if addr == BF8:
            return 0            # status ok
        return self.echo.get(addr, 0)

    def _stream(self):
        base = self.dev.cs6["addr"] & ~0x3
        n = self._stream_n.get(base, 0) + 1
        self._stream_n[base] = n
        if base == STREAMS[0] and n == 1:
            return self.pos_samples & 0xFFFFFFFF  # position word
        return self.echo.get(base + (n - 1) * 4, 0)

    def _mode(self):
        try:
            return self.dev.mem.r32(MODE_ADDR)
        except Exception:
            return 0

    # -- play clock + TIME handshake (called from SH7724.tick) --------
    # Guest writes: [SH_POS] + [POSL] + [TRACKLEN] ONLY, ONLY when
    # XDJ_HLE_DSP=1 (this object exists only then; default off: hle is
    # None, no writes).
    def tick(self, cyc):
        self.n_ticks += 1
        mode = self._mode()
        if mode == MODE_PLAYING:
            self.pos_samples += (cyc * SAMPLE_RATE) // CPUFREQ
        if not self.primed:
            self.primed = True
            try:
                self.prime()
            except Exception as e:
                self.prime_error = repr(e)
        # TIME handshake: supply TRACKLEN once a track is loaded, advance
        # SH_POS with the play clock while playing. On a real deck the DSP
        # data drives POSL (the time digits' source, CLAUDE.md sec. 103:
        # SH_POS freezes on silicon while the clock moves); the firmware's
        # own SH_POS->POSL derivation does not run in every emulated state,
        # so the model advances POSL=2x directly -- the emulated clock and
        # the phase meter (tools/phasemeter/pm.c reads POSL) both move.
        try:
            mem = self.dev.mem
            if mode in (MODE_PAUSED, MODE_PLAYING):
                try:
                    if mem.r32(TRACKLEN) == 0:
                        mem.w32(TRACKLEN, FIXED_TL)
                except Exception:
                    pass
            if mode == MODE_PLAYING:
                try:
                    cur = mem.r32(SH_POS)
                except Exception:
                    return
                if cur == 0xFFFFFFFF:
                    # fresh load (firmware reset value): restart the clock
                    self.pos_samples = 0
                    try:
                        mem.w32(SH_POS, 0)
                        mem.w32(POSL, 0)
                    except Exception:
                        pass
                    return
                want = (self.pos_samples // POS_DIV) & 0xFFFFFFFF
                if cur != want:
                    # a jump bigger than 1 s means someone else wrote SH_POS
                    # (a test seeking the playhead, or the firmware at a
                    # track load): adopt it as the clock instead of
                    # overwriting it -- POSL then follows in one tick
                    if abs(cur - want) > 75:
                        self.pos_samples = cur * POS_DIV
                        want = cur
                    try:
                        mem.w32(SH_POS, want)
                        mem.w32(POSL, (want * 2) & 0xFFFFFFFF)
                    except Exception:
                        pass
        except Exception:
            pass

    # -- track pipeline (Mac side; guest audio files read-only) -----------
    def _stick_root(self):
        try:
            disk = self.dev._usb().stick.disk
            root = getattr(disk, "root", None)
            if root:
                return root
        except Exception:
            pass
        return os.environ.get("XDJ_USB")

    def _needles(self):
        mem = self.dev.mem
        out = []
        for base in (BUF0, BUF1):
            try:
                blk = mem.r_block(base, BLOCK)
            except Exception:
                continue
            if not blk or not any(blk):
                continue
            for off in (0x30, 0x200, 0x800, 0x1000):
                w = bytes(blk[off:off + 64])
                if len(w) == 64 and any(w) and w != b"\xaa" * 64:
                    out.append((base, off, w))
        return out

    def identify(self):
        root = self._stick_root()
        needles = self._needles()
        if not root or not needles:
            return None, None
        cands = []
        for dp, _, fs in os.walk(root):
            for fn in fs:
                p = os.path.join(dp, fn)
                try:
                    if os.path.getsize(p) > 100_000:
                        cands.append(p)
                except OSError:
                    pass
        for base, off, w in needles:
            for p in cands:
                try:
                    with open(p, "rb") as f:
                        d = f.read()
                except OSError:
                    continue
                j = d.find(w)
                if j >= 0:
                    return p, j - off
        return None, None

    def prime(self):
        """Identify + ffmpeg-decode the loaded track (idempotent)."""
        if self.pcm is not None:
            return True
        path, off = self.identify()
        if not path:
            return False
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", "44100",
             "-f", "s16le", "-acodec", "pcm_s16le", "-"],
            capture_output=True, check=True).stdout
        if len(raw) < 16:
            return False
        self.track_path, self.track_off = path, off
        self.pcm = raw
        self.pcm_frames = len(raw) // 4
        self.track_duration = self.pcm_frames / 44100.0
        return True

    def correlation(self):
        """Buffer bytes vs track file bytes at the matched offsets."""
        if not self.track_path:
            return {"track": None}
        mem = self.dev.mem
        with open(self.track_path, "rb") as f:
            d = f.read()
        res = {"track": self.track_path, "size": len(d)}
        for nm, base in (("buf0", BUF0), ("buf1", BUF1)):
            blk = bytes(mem.r8(base + i) for i in range(BLOCK))
            data = [(i, b) for i, b in enumerate(blk) if b not in (0, 0xAA)]
            if not data:
                res[nm] = {"data_bytes": 0}
                continue
            start = data[0][0]
            seg = bytes(blk[start:start + 1024])
            j = d.find(bytes(seg[:64]))
            if j < 0:
                res[nm] = {"data_bytes": len(data), "match": False}
                continue
            k = 0
            while start + k < BLOCK and j + k < len(d) and \
                    blk[start + k] == d[j + k]:
                k += 1
            res[nm] = {"data_bytes": len(data), "match": True,
                       "file_off": j, "run": k}
        return res

    def write_wav(self, path, seconds=5.0):
        """PCM head as a WAV (what the shadow playhead would output)."""
        import wave
        n = min(self.pcm_frames, int(seconds * SAMPLE_RATE))
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(self.pcm[:n * 4])
        return {"path": path, "frames": n, "seconds": n / SAMPLE_RATE}

    def try_coreaudio(self, seconds=3.0, timeout=12.0):
        """Best-effort audible playback via CoreAudio ctypes (macOS).

        Returns (ok, note). Any failure -> (False, reason); the WAV dump
        remains the proof artifact.
        """
        import ctypes
        import threading
        res = {}

        def play():
            try:
                at = ctypes.CDLL(
                    "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
                n = min(self.pcm_frames, int(seconds * SAMPLE_RATE))
                buf = (ctypes.c_byte * (n * 4)).from_buffer_copy(self.pcm[:n * 4])

                class ASBD(ctypes.Structure):
                    _fields_ = [("mSampleRate", ctypes.c_double),
                                ("mFormatID", ctypes.c_uint32),
                                ("mFormatFlags", ctypes.c_uint32),
                                ("mBytesPerPacket", ctypes.c_uint32),
                                ("mFramesPerPacket", ctypes.c_uint32),
                                ("mBytesPerFrame", ctypes.c_uint32),
                                ("mChannelsPerFrame", ctypes.c_uint32),
                                ("mBitsPerChannel", ctypes.c_uint32),
                                ("mReserved", ctypes.c_uint32)]

                fmt = ASBD(44100.0, 0x6C70636D, 12, 4, 1, 4, 2, 16, 0)
                q = ctypes.c_void_p()
                st = at.AudioQueueNewOutput(
                    ctypes.byref(fmt), None, None, None, None, 0,
                    ctypes.byref(q))
                if st:
                    res["r"] = (False, f"AudioQueueNewOutput status {st}")
                    return
                bufid = ctypes.c_void_p()
                st = at.AudioQueueAllocateBuffer(q, n * 4, ctypes.byref(bufid))
                if st:
                    res["r"] = (False, f"AllocateBuffer status {st}")
                    return
                ABL = ctypes.c_uint32 * 2

                class AQB(ctypes.Structure):
                    _fields_ = [("mAudioDataBytesCapacity", ctypes.c_uint32),
                                ("mAudioData", ctypes.c_void_p),
                                ("mAudioDataByteSize", ctypes.c_uint32),
                                ("mUserData", ctypes.c_void_p),
                                ("mPacketDescriptionCapacity", ctypes.c_uint32),
                                ("mPacketDescriptions", ctypes.c_void_p),
                                ("mPacketDescriptionCount", ctypes.c_uint32)]

                aq = ctypes.cast(bufid, ctypes.POINTER(AQB)).contents
                ctypes.memmove(aq.mAudioData, buf, n * 4)
                aq.mAudioDataByteSize = n * 4
                at.AudioQueueEnqueueBuffer(q, bufid, 0, None)
                st = at.AudioQueueStart(q, None)
                if st:
                    res["r"] = (False, f"AudioQueueStart status {st}")
                    return
                import time as _t
                _t.sleep(min(seconds, n / 44100.0) + 0.5)
                at.AudioQueueStop(q, True)
                at.AudioQueueDispose(q, True)
                res["r"] = (True, f"played {n / 44100.0:.1f}s via AudioQueue")
            except Exception as e:
                res["r"] = (False, f"{type(e).__name__}: {e}")

        t = threading.Thread(target=play, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            return False, "timeout (no audio device?)"
        return res.get("r", (False, "unknown"))

    def status(self):
        return {"pos_samples": self.pos_samples,
                "pos_seconds": self.pos_samples / SAMPLE_RATE,
                "polls": self.polls,
                "track": self.track_path,
                "track_off": self.track_off,
                "pcm_frames": self.pcm_frames,
                "duration": self.track_duration,
                "ba0_cmds": list(self.cmd_log)}
