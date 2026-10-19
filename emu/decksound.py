"""decksound.py -- the emulator makes sound, like the real deck.

What this adds on top of emu/hledsp.py + emu/macaudio.py (both stay
proof-grade): a LIVE pull-based CoreAudio output that

  * plays the loaded track while the firmware transport runs (mode 5),
    pauses with it, and resyncs on cue/seek -- the playhead master is
    the HLE DSP play clock (hle.pos_samples), the same clock behind
    the deck's on-screen TIME;
  * applies the SH's Master Tempo Q20 ratio word (0x0B6C2C50) as a
    live pitch shift (phase vocoder, tempo kept) -- so key-shift
    experiments (the tools/keyshift_probe.py addresses, forced ratio
    writes, the future hook mod) are AUDIBLE in the emulator, no deck.

Design: a producer thread renders 1 s chunks of pitch-shifted PCM
(phase vocoder per channel, 2048-sample crossfades between chunks)
into a ~4 s ring, keeping ~1.5 s ahead of the emulated playhead; the
AudioQueue callback pulls from the ring at real time. Emulator-vs-Mac
clock drift is absorbed by a hard resync whenever the ring and the
emulated playhead diverge by > 0.35 s (also covers cue/seek jumps).

Gating: XDJ_HLE_DSP=1 (needs the HLE play clock; hle is None => this
module's attach() returns None) and XDJ_DECK_SOUND != "0" (kill
switch). Nothing happens at import; the queue opens on first play.
"""
import ctypes
import os
import sys
import threading
import time

import numpy as np

from emu import macaudio

SR = 44100
CHUNK = SR               # 1 s of input per render pass
RING = int(SR * 4)       # ring capacity (stereo frames)
AHEAD = int(SR * 0.5)    # producer target: buffered ahead of playhead
RESYNC = int(SR * 0.35)  # playhead/ring divergence forcing a re-render
XFADE = 2048             # crossfade between consecutive shifted chunks
Q20_RATIO = 0x0B6C2C50   # SH Master Tempo stretch-ratio word (Q20)
PITCH_SITE = 0x08D36B3A  # mov.l r1,@(20,r12): 3E9C -> 0x09D24DD8 (-> BD8)
PITCH_WORD = 0x09D24DD8  # DSP-context word posted to mailbox BD8
MODE_ADDR = 0x0B6C4264
MODE_PLAYING = 5

_LPCM_S16LE = (1 << 2) | (1 << 3)          # signed integer | packed


def enabled():
    return os.environ.get("XDJ_DECK_SOUND") != "0"


def _q20_semis(word, inverse=False):
    f = (word & 0xFFFFFFFF) / float(1 << 20)
    if f <= 0:
        return 0.0
    if inverse:
        f = 1.0 / f
    return 12.0 * float(np.log2(f))


def _pv():
    try:
        import pv
        return pv
    except ImportError:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.append(os.path.join(root, "tools"))
        import pv
        return pv


class DeckSound:
    """One per emulator; sync() from the UI loop, sound comes out."""

    def __init__(self, machine, inverse=False):
        self.m = machine
        self.inverse = inverse
        self.status = ""
        self.lock = threading.Lock()
        self.ring = np.zeros((RING, 2), np.float32)
        self.ring_pos = 0           # absolute frame index of ring[0]
        self.ring_n = 0             # valid frames in ring
        self.tail = None            # last XFADE frames of previous render
        self.read_abs = 0           # producer input position (frames)
        self.semis = 0.0
        self.pitch_override = None   # manual semitones (experiments/UI);
        #                             None = follow the firmware ratio word
        self.hold_semis = None       # KEY-SHIFT KEEPER PROTOTYPE: rewrite
        #                             the SH Q20 word every sync (10 Hz) so
        #                             the REAL chain word->mailbox->DSP (and
        #                             deck sound via the mailbox echo) car-
        #                             ries the shift. The deck's own
        #                             normalizer fights back (<0.5 s cycle);
        #                             hold_wins/hold_n measure the duty.
        self.hold_wins = 0
        self._hold_last = None
        self.hold_n = 0
        self.rendered_semis = 0.0
        self.playing = False
        self.muted = False             # UI mute (producer keeps running)
        self.pcm = None             # float32 stereo frames
        self.track = None
        self.want = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.aq = None
        self.at = None
        self.bufs = []
        self.served = 0

    # -- machine state --------------------------------------------------
    def _hle(self):
        return getattr(self.m.mem.dev, "hle", None)

    def _pos(self):
        hle = self._hle()
        return int(getattr(hle, "pos_samples", 0) or 0) if hle else 0

    def _mode(self):
        try:
            return self.m.mem.r32(MODE_ADDR)
        except Exception:
            return 0

    def _ratio_word(self):
        """Key ratio implied by the DSP command block: pitch slot BD8 over
        tempo slot BC0 (Q20). Deck session 4 proved BC0 alone (tempo word
        0x0B6C2C50, mirrored into BD8 by 0x08D36B34) changes TEMPO with
        the key kept under MT; the key only moves when BD8 differs from
        BC0 (2026-10-11 key-shift hook). Returned as a Q20 ratio so the
        callers' semitone maths stay unchanged. DIRECTION is unproven on
        the deck: self.inverse flips it."""
        hle = self._hle()
        if hle is not None:
            try:
                tempo = hle.echo.get(0x11837BC0, 1 << 20) or (1 << 20)
                pitch = hle.echo.get(0x11837BD8, tempo) or tempo
                return int(round(pitch * (1 << 20) / tempo))
            except Exception:
                pass
        return 1 << 20

    # -- track pcm --------------------------------------------------------
    def _ensure_track(self):
        hle = self._hle()
        if hle is not None and getattr(hle, "pcm", None) and \
                getattr(hle, "track_path", None) == self.track:
            return True
        if hle is not None and getattr(hle, "pcm", None):
            self._load_pcm(hle.pcm, hle.track_path)
            return True
        path, why = macaudio.identify_track(self.m)
        if not path:
            if not self.status.startswith("sound: no track"):
                self.status = f"sound: no track ({why})"
            return False
        self._load_pcm(macaudio.decode_track(path), path)
        return True

    def _load_pcm(self, raw_s16, path):
        x = np.frombuffer(raw_s16, "<i2").astype(np.float32) / 32768.0
        self.pcm = x.reshape(-1, 2)
        self.track = path
        self.status = f"sound: {os.path.basename(path)}"

    # -- producer ----------------------------------------------------------
    def _render_more(self):
        """Append one crossfaded 1 s chunk of shifted audio to the ring."""
        pos = self._pos()
        with self.lock:
            have_end = self.ring_pos + self.ring_n
            if self.ring_n == 0 or pos < self.ring_pos - RESYNC or \
                    pos > have_end + RESYNC:
                # nothing usable buffered for this playhead: re-render here
                self.ring_pos, self.ring_n = pos, 0
                self.tail = None
                self.read_abs = pos
            if have_end - pos >= AHEAD:
                return                      # buffered enough
            start = self.ring_pos + self.ring_n
        if self.read_abs < start - XFADE or self.read_abs > start + XFADE:
            self.read_abs = max(self.read_abs, start)
            self.tail = None                # discontinuity: no crossfade
        if self.pcm is None or self.read_abs >= len(self.pcm) - XFADE:
            time.sleep(0.2)                 # end of track
            return
        i0 = self.read_abs
        block = self.pcm[i0:i0 + CHUNK + XFADE]
        if abs(self.semis) < 0.01:
            new = np.ascontiguousarray(block, np.float32)
        else:
            pv = _pv()
            new = np.stack(
                [pv.pv_shift(block[:, c], self.semis).astype(np.float32)
                 for c in (0, 1)], axis=1)
        piece = new
        if self.tail is not None and len(self.tail) == XFADE \
                and len(new) > XFADE and abs(self.semis - self.rendered_semis) < 0.01:
            up = np.linspace(0, 1, XFADE, dtype=np.float32)[:, None]
            piece = np.concatenate(
                [self.tail * (1 - up) + new[:XFADE] * up, new[XFADE:]], axis=0)
        self.tail = new[-XFADE:] if len(new) >= XFADE else None
        self.rendered_semis = self.semis
        self.read_abs = i0 + len(block)
        with self.lock:
            self._push(piece)

    def _push(self, stereo):
        n = len(stereo)
        if self.ring_n + n > RING:          # drop oldest to make room
            drop = self.ring_n + n - RING
            self.ring_pos += drop
            self.ring_n -= drop
        start = (self.ring_pos + self.ring_n) % RING
        first = min(n, RING - start)
        self.ring[start:start + first] = stereo[:first]
        if n > first:
            self.ring[:n - first] = stereo[first:]
        self.ring_n += n

    def _produce(self):
        while not self.stop.is_set():
            if not self.want.is_set():
                self.want.wait(0.25)
                continue
            try:
                if not self._ensure_track():
                    time.sleep(0.5)
                    continue
                self._render_more()
                time.sleep(0.05)           # never busy-spin: the emulator
            except Exception as e:         # thread needs the GIL too
                self.status = f"sound: {type(e).__name__}: {e}"
                time.sleep(0.5)

    # -- consumer (CoreAudio callback thread) -------------------------------
    def _callback(self, user, aq, bufref):
        nfr = 4096
        nbytes = nfr * 4
        out = np.zeros((nfr, 2), np.float32)
        if self.playing and not self.muted and self.aq is not None:
            pos = self._pos()
            with self.lock:
                skip = pos - self.ring_pos
                if skip > 0:                # playhead ran ahead: drop stale
                    take = min(skip, self.ring_n)
                    self.ring_pos += take
                    self.ring_n -= take
                take = min(nfr, self.ring_n)
                if take > 0:
                    off = self.ring_pos % RING
                    idx = (np.arange(take) + off) % RING
                    out[:take] = self.ring[idx]
                    self.ring_pos += take
                    self.ring_n -= take
        data = (np.clip(out, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        b = ctypes.cast(bufref, ctypes.POINTER(macaudio._AQB)).contents
        ctypes.memmove(b.mAudioData, data, nbytes)
        b.mAudioDataByteSize = nbytes
        if self.at is not None:            # None in output-less tests
            self.at.AudioQueueEnqueueBuffer(aq, bufref, 0, None)
        self.served += nfr

    def _open_queue(self):
        at = macaudio._audio_toolbox()
        macaudio._bind(at)
        for fn in ("AudioQueueStart", "AudioQueueStop"):
            getattr(at, fn).argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            getattr(at, fn).restype = ctypes.c_int32
        self.at = at
        asbd = macaudio._ASBD()
        asbd.mSampleRate = float(SR)
        asbd.mFormatID = 0x6C70636D                     # 'lpcm' (0x21='!' was wrong: "fmt?" 1718449215)
        asbd.mFormatFlags = _LPCM_S16LE
        asbd.mBytesPerPacket = 4
        asbd.mFramesPerPacket = 1
        asbd.mBytesPerFrame = 4
        asbd.mChannelsPerFrame = 2
        asbd.mBitsPerChannel = 16
        self._cb_keepalive = macaudio._CB(self._callback)
        q = ctypes.c_void_p()
        r = at.AudioQueueNewOutput(ctypes.byref(asbd), self._cb_keepalive,
                                   None, None, None, 0, ctypes.byref(q))
        if r != 0:
            self.status = f"sound: AudioQueueNewOutput {r}"
            return False
        self.aq = q
        for _ in range(3):
            bref = ctypes.c_void_p()
            if at.AudioQueueAllocateBuffer(q, nfr_bytes := 4096 * 4,
                                           ctypes.byref(bref)) != 0:
                self.status = "sound: buffer alloc failed"
                return False
            self.bufs.append(bref)
            self._callback(None, q, bref)
        if at.AudioQueueStart(q, None) != 0:
            self.status = "sound: AudioQueueStart failed"
            return False
        self.thread = threading.Thread(target=self._produce, daemon=True)
        self.thread.start()
        self.status = "sound: ready"
        return True

    # -- UI loop entry -------------------------------------------------------
    def sync(self):
        """Call ~10-30 Hz from the emulator UI loop."""
        playing = self._mode() == MODE_PLAYING
        if playing and self.aq is None and not self._open_failed:
            if not self._open_queue():
                self._open_failed = True
        if playing != self.playing:
            self.playing = playing
            if self.aq is not None:
                self.status = "sound: playing" if playing else "sound: paused"
        if self.hold_semis is not None and playing:
            # key-shift v2 (tools/keyshift_v2_apply.py, pitch slot): NOP the
            # 0x09D24DD8 copier once, then own that word -> mailbox BD8
            # carries the key ratio while BC0 keeps the tempo path.
            v = int(round(2.0 ** (self.hold_semis / 12.0) * (1 << 20)))
            try:
                mem = self.m.mem
                if mem.r8(PITCH_SITE) | mem.r8(PITCH_SITE + 1) << 8 == 0x1C15:
                    mem.w8(PITCH_SITE, 0x09); mem.w8(PITCH_SITE + 1, 0x00)
                for i, b in enumerate(v.to_bytes(4, "little")):
                    mem.w8(PITCH_WORD + i, b)
            except Exception:
                pass
            self.hold_n += 1
            if self._hle() is not None and \
                    self._hle().echo.get(0x11837BD8) == v & 0xFFFFFFFF:
                self.hold_wins += 1
        semis = self.pitch_override if self.pitch_override is not None \
            else _q20_semis(self._ratio_word(), self.inverse)
        if abs(semis - self.semis) > 0.01:
            self.semis = semis
            self.tail = None              # crossfade-free restart of render
            with self.lock:               # drop stale-pitch lead so the new
                keep = int(0.2 * SR)      # pitch is audible ~0.2 s later
                if self.ring_n > keep:
                    self.ring_pos += self.ring_n - keep
                    self.ring_n = keep
            self.status = f"sound: playing, {semis:+.2f} semitones"
        if playing:
            self.want.set()
        else:
            self.want.clear()

    _open_failed = False

    def close(self):
        self.stop.set()
        self.want.set()
        if self.aq is not None:
            try:
                self.at.AudioQueueStop(self.aq, True)
            except Exception:
                pass
            self.aq = None

    def describe(self):
        """State for UIs: what's playing, at what pitch."""
        return {"on": self.aq is not None, "playing": self.playing,
                "muted": self.muted,
                "pitch": self.pitch_override if self.pitch_override is not None
                else round(_q20_semis(self._ratio_word(), self.inverse), 2),
                "follows_firmware": self.pitch_override is None,
                "hold": self.hold_semis,
                "hold_duty": round(self.hold_wins / self.hold_n, 2)
                if self.hold_n else None,
                "status": self.status,
                "track": os.path.basename(self.track) if self.track else None}


SOUND = [None]


def attach(machine, inverse=False):
    """The one deck-sound for this machine (None when not available)."""
    if not enabled() or getattr(machine.mem.dev, "hle", None) is None:
        return None
    if SOUND[0] is None or SOUND[0].m is not machine:
        if SOUND[0] is not None:
            SOUND[0].close()
        SOUND[0] = DeckSound(machine, inverse=inverse)
    return SOUND[0]


def sync(machine):
    s = attach(machine)
    if s is not None:
        s.sync()
