"""SimPlay: Mac-side playback-DISPLAY simulation for the XDJ-700 emulator.

The firmware's audio path ends at an external DSP chip that is not
modelled: the player enters mode 5 (playing) but position never advances,
so TIME stays --:-- and the detail waveform never draws. Faking the DSP
protocol was tried (status reads, triggers, FIFO levels) and goes nowhere:
the feeder needs the DSP's active participation (consumption/position
reports), which is not derivable from zeros.

This module does NOT fake firmware state. It draws a clearly-badged,
Mac-side overlay (scrolling detail wave + running time) while the
firmware reports mode 5, using the LOADED track's own analysis (PWV5 +
MP3-derived duration). Purpose: make the emulator usable for display-side
work (e.g. validating the RGB detail-wave hook against a moving playhead)
until the DSP position word is found. Everything it draws is labelled SIM.

Track identification (no RAM parsing): the track the firmware streamed
over USB is the .mp3 with the most sector reads in the stick's access
log; its analysis is found by matching PPTH basenames under
PIONEER/USBANLZ. Falls back to a fixed session default.
"""
import os
import struct
from collections import Counter

MODE_ADDR = 0x0B6C4264
MODE_PLAYING = 5

DEFAULT_EXT = os.path.expanduser(
    "~/Downloads/Rekordbox stuff/PIONEER/USBANLZ/P000/0002D83A/ANLZ0000.EXT")


def loaded_mp3(machine):
    """Path of the track the firmware streamed most (or None)."""
    try:
        disk = machine.mem.dev._usb().stick.disk
        acc = getattr(disk, "access", None)
        if not acc:
            return None
        n = Counter()
        for kind, pos in acc:
            if isinstance(kind, str) and kind.lower().endswith(".mp3"):
                n[kind] += 1
        return n.most_common(1)[0][0] if n else None
    except Exception:
        return None


def _sections(ext_path):
    b = open(ext_path, "rb").read()
    hl = struct.unpack(">I", b[4:8])[0]
    pos, out = hl, {}
    while pos + 12 <= len(b):
        tag = b[pos:pos + 4]
        h, t = struct.unpack(">II", b[pos + 4:pos + 12])
        if t == 0:
            break
        out[tag] = b[pos + h:pos + t]
        pos += t
    return out


def _ppth_name(ppth):
    try:
        s = ppth.decode("utf-16-be")
    except Exception:
        return ""
    return s.replace("\x00", "").strip().strip("/")


def find_ext(usb_root, mp3_path):
    """Match a track to its ANLZ .EXT via PPTH basename (cached).

    No mp3 (e.g. snapshot-restored sticks whose access log starts empty)
    -> session default, NEVER an arbitrary track.
    """
    base = os.path.basename(mp3_path or "").lower()
    cache = find_ext.__dict__.setdefault("cache", {})
    key = (usb_root, base)
    if key in cache:
        return cache[key]
    default = (DEFAULT_EXT if os.path.isfile(DEFAULT_EXT) else None)
    if not base:
        cache[key] = default
        return default
    top = os.path.join(usb_root, "PIONEER", "USBANLZ")
    try:
        roots = [os.path.join(top, d) for d in sorted(os.listdir(top))]
    except OSError:
        roots = []
    for proot in roots:
        if not os.path.isdir(proot):
            continue
        try:
            ids = sorted(os.listdir(proot))
        except OSError:
            continue
        for i in ids:
            p = os.path.join(proot, i, "ANLZ0000.EXT")
            if not os.path.isfile(p):
                continue
            try:
                secs = _sections(p)
                name = _ppth_name(secs.get(b"PPTH", b""))
            except Exception:
                continue
            if base and os.path.basename(name).lower() == base:
                cache[key] = p
                return p
    cache[key] = default
    return default


_BR = {1: 32, 2: 40, 3: 48, 4: 56, 5: 64, 6: 80, 7: 96, 8: 112, 9: 128,
       10: 160, 11: 192, 12: 224, 13: 256, 14: 320}
_SR = {(3, 0): 44100, (3, 1): 48000, (3, 2): 32000,
       (2, 0): 22050, (2, 1): 24000, (2, 2): 16000,
       (0, 0): 11025, (0, 1): 12000, (0, 2): 8000}


def mp3_duration(path):
    """Duration in seconds by walking MPEG frame headers (any track)."""
    try:
        mb = open(path, "rb").read()
    except OSError:
        return 0.0
    pos = mb.find(b"ID3")
    if pos >= 0:
        ln = 0
        for i in range(6, 10):
            ln = (ln << 7) | (mb[pos + i] & 0x7F)
        pos += 10 + ln
    else:
        pos = 0
    frames = 0
    sr = 44100
    n = len(mb)
    while pos + 4 <= n:
        if mb[pos] == 0xFF and (mb[pos + 1] & 0xE0) == 0xE0:
            v = (mb[pos + 1] >> 3) & 3
            layer = (mb[pos + 1] >> 1) & 3
            br = (mb[pos + 2] >> 4) & 15
            sri = (mb[pos + 2] >> 2) & 3
            pad = (mb[pos + 2] >> 1) & 1
            if v == 1 or layer != 1 or br in (0, 15) or sri == 3:
                pos += 1
                continue
            key = 3 if v == 3 else 2
            brate = _BR[br] * 1000
            sr = _SR[(v, sri)]
            pos += int(144 * brate / sr) + pad
            frames += 1
        else:
            pos += 1
    return frames * 1152 / sr if frames else 0.0


class SimTrack:
    """One loaded track's display data (PWV5 columns + duration)."""

    def __init__(self, ext_path, mp3_path):
        self.ext_path = ext_path
        try:
            secs = _sections(ext_path)
        except Exception:
            secs = {}
        pwv5 = secs.get(b"PWV5", b"")
        # 2 B/column (matches PWV3's 1 B/column count on the session track)
        self.ncols = len(pwv5) // 2
        self.cols = pwv5
        self.duration = mp3_duration(mp3_path) if mp3_path else 0.0
        if self.duration <= 0:
            self.duration = 85.0

    def height01(self, col):
        if not self.ncols:
            return 0.0
        b = self.cols[(col % self.ncols) * 2]
        return b / 255.0


class SimPlay:
    """Wall-clock playhead; runs while the firmware reports mode 5."""

    def __init__(self):
        self.enabled = True
        self.track = None       # SimTrack
        self.track_key = None   # (ext_path, mp3_path)
        self.pos = 0.0
        self.playing = False

    def mode(self, machine):
        try:
            return machine.mem.r32(MODE_ADDR)
        except Exception:
            return 0

    def update(self, machine, usb_root, now):
        """Advance the playhead; returns (track, pos) or (None, 0)."""
        if machine is None:
            self.playing = False
            return None, 0.0
        playing = self.enabled and self.mode(machine) == MODE_PLAYING
        mp3 = loaded_mp3(machine) if playing else None
        ext = find_ext(usb_root, mp3) if (playing and usb_root) else None
        key = (ext, mp3)
        if not playing:
            self.playing = False
            self.track = None
            self.track_key = None
            self.pos = 0.0
            return None, 0.0
        if key != self.track_key or self.track is None:
            self.track = SimTrack(ext, mp3) if ext else None
            self.track_key = key
            self.pos = 0.0
        dt = now - getattr(self, "_t", now)
        self._t = now
        if self.track is not None:
            self.pos += dt
            if self.pos >= self.track.duration:
                self.pos = self.track.duration  # stop at end (deck-like)
        self.playing = self.track is not None
        return self.track, self.pos

    @staticmethod
    def fmt(pos):
        m = int(pos) // 60
        s = pos - 60 * m
        return f"{m}:{s:06.3f}"
