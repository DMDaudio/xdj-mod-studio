"""macaudio.py -- Mac-side audible playback for the XDJ-700 emulator.

Status: proof-of-path module, NOT wired into any live path. Nothing in
emu/ui.py, emu/main.py, studio/ or the frozen app imports this file, so
stock behavior is byte-identical whether it exists or not.

What it does
------------
1. Identifies the LOADED track's audio file (read-only):
   primary = feeder-buffer byte-match (emu/hledsp.py HleDsp.identify --
   works on snapshot-restored sticks whose USB access log is empty);
   fallback = emu/simplay.py loaded_mp3 (live-session USB sector log).
2. Decodes it on the Mac with ffmpeg to s16 stereo 44.1k (read-only;
   Rekordbox audio files are never written) and renders a WAV proof.
3. Plays it through the speakers via one of three Mac backends:
   (a) CoreAudio AudioQueue via ctypes -- FIXED (hledsp's attempt got
       status -50 from AudioQueueNewOutput because it passed NULL for
       the output callback; NULL callback = kAudio_ParamError. This
       module passes a real (no-op) callback + full argtypes/restype
       and every call returns 0).
   (b) afplay subprocess on a rendered WAV (most robust, no deps).
   (c) pygame.mixer -- attempted, reported, but NOT available in this
       repo's .venv (pygame 2.6.1 / Python 3.14 ships without the mixer
       extension; system python has it). Documented, not required.
4. Gates playback on firmware transport state: play while the firmware
   mode word *0x0B6C4264 == 5, pause otherwise; position follows the HLE
   DSP play clock (hle.pos_samples) when present, else a wall-clock.

Env gating
----------
Importing this module has no side effects and makes no sound. Audible
output happens ONLY when the caller passes audible=True AND the env var
XDJ_MACAUDIO_PLAY=1 is set (proof __main__ honors both). The default
proof path renders the WAV + runs the transport-gating sequence against
a FakePlayer (headless-safe) + probes CoreAudio without starting it.

Chunk note: the firmware-side audio ISR/DSP consumes PCM in small
chunks (the "16-stereo-frame" path in the task brief lives behind the
unmodeled TI C6x DSP -- see emu/hledsp.py LIMIT). Mac-side playback is
whole-track decode; chunking happens only at the CoreAudio buffer level
(single buffer for the proof slice; streaming is future work).

Run: .venv/bin/python -m emu.macaudio --proof [--audible]
"""

import ctypes
import os
import shutil
import struct
import subprocess
import time
import wave

MODE_ADDR = 0x0B6C4264
MODE_PLAYING = 5
SAMPLE_RATE = 44100

TRACK_CKPT = "cache/emu_track_loaded.pkl"
PROOF_WAV = "/tmp/macaudio_proof.wav"
PROOF_PNG = "screenshots/macaudio_proof.png"
PROOF_LOG = "/tmp/macaudio_proof.log"

_AUDIBLE_ENV = "XDJ_MACAUDIO_PLAY"


# -- transport state (firmware ground truth) --------------------------------

def mode(machine):
    """Firmware transport mode word (5 = playing, 3 = paused/loaded)."""
    try:
        return machine.mem.r32(MODE_ADDR)
    except Exception:
        return 0


def is_playing(machine):
    return mode(machine) == MODE_PLAYING


def hle_pos_seconds(machine):
    """HLE DSP play-clock position, or None when unavailable.

    Available only when the machine was built with XDJ_HLE_DSP=1 (the HLE
    model's pos_samples advances with emulated time while mode == 5).
    Snapshot lineage cache/emu_track_loaded.pkl was saved WITHOUT the HLE
    model, so this is normally None there -- the gate then uses its own
    wall-clock (same as emu/simplay.py).
    """
    try:
        hle = getattr(machine.mem.dev, "hle", None)
        if hle is None:
            return None
        return float(getattr(hle, "pos_samples", 0)) / SAMPLE_RATE
    except Exception:
        return None


# -- track identification (read-only) ----------------------------------------

def identify_track(machine):
    """Return (path, source) for the loaded track's audio file, or (None, why).

    Never writes to the stick. Primary: feeder-buffer byte-match (works on
    snapshots). Fallback: USB sector access log (live sessions only).
    """
    try:
        from emu.hledsp import HleDsp
        hle = HleDsp(machine.mem.dev)
        path, off = hle.identify()
        if path and os.path.isfile(path):
            return path, f"feeder byte-match @file+{off}"
    except Exception as e:
        last_err = repr(e)
    else:
        last_err = "no feeder match"
    try:
        from emu import simplay
        mp3 = simplay.loaded_mp3(machine)
        if mp3 and os.path.isfile(mp3):
            return mp3, "usb access log (simplay.loaded_mp3)"
    except Exception as e:
        last_err = f"{last_err}; simplay err {e!r}"
    return None, last_err


# -- decode / render (read-only source -> /tmp WAV) ---------------------------

def decode_track(path, sr=SAMPLE_RATE):
    """ffmpeg decode to s16 stereo sr bytes. Source file opened read-only."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(sr),
         "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        capture_output=True, check=True).stdout
    if len(raw) < 16:
        raise ValueError(f"decode of {path!r} produced {len(raw)} bytes")
    return raw


def render_wav(pcm, out_path, sr=SAMPLE_RATE):
    nframes = len(pcm) // 4
    with wave.open(out_path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return {"path": out_path, "frames": nframes, "seconds": nframes / float(sr)}


def render_track_wav(machine_or_path, out_path=PROOF_WAV, seconds=None,
                     sr=SAMPLE_RATE):
    """Identify (if given a machine) + decode + write WAV. Returns info dict."""
    if isinstance(machine_or_path, (str, bytes, os.PathLike)):
        path = os.fspath(machine_or_path)
        source = "explicit path"
    else:
        path, source = identify_track(machine_or_path)
        if not path:
            raise SystemExit(f"no loaded track identified ({source})")
    pcm = decode_track(path, sr=sr)
    if seconds is not None:
        pcm = pcm[:int(seconds * sr) * 4]
    info = render_wav(pcm, out_path, sr=sr)
    info.update({"track": path, "source": source})
    return info


# -- backend (a): CoreAudio AudioQueue, fixed ---------------------------------
# hledsp.try_coreaudio failed with status -50 because it passed None for
# inCallbackProc. AudioQueueNewOutput requires a callback (NULL = paramErr).
# Fixed here: real no-op CFUNCTYPE callback + explicit argtypes/restype.

def _audio_toolbox():
    return ctypes.CDLL(
        "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")


class _ASBD(ctypes.Structure):
    _fields_ = [("mSampleRate", ctypes.c_double),
                ("mFormatID", ctypes.c_uint32),
                ("mFormatFlags", ctypes.c_uint32),
                ("mBytesPerPacket", ctypes.c_uint32),
                ("mFramesPerPacket", ctypes.c_uint32),
                ("mBytesPerFrame", ctypes.c_uint32),
                ("mChannelsPerFrame", ctypes.c_uint32),
                ("mBitsPerChannel", ctypes.c_uint32),
                ("mReserved", ctypes.c_uint32)]


class _AQB(ctypes.Structure):
    _fields_ = [("mAudioDataBytesCapacity", ctypes.c_uint32),
                ("mAudioData", ctypes.c_void_p),
                ("mAudioDataByteSize", ctypes.c_uint32),
                ("mUserData", ctypes.c_void_p),
                ("mPacketDescriptionCapacity", ctypes.c_uint32),
                ("mPacketDescriptions", ctypes.c_void_p),
                ("mPacketDescriptionCount", ctypes.c_uint32)]


_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p)


def _cb_nop(user, aq, buf):
    pass


_CBNOP = _CB(_cb_nop)  # keep alive: queue holds the pointer, not the object


def _bind(at):
    at.AudioQueueNewOutput.argtypes = [
        ctypes.POINTER(_ASBD), _CB, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    at.AudioQueueNewOutput.restype = ctypes.c_int32
    at.AudioQueueAllocateBuffer.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    at.AudioQueueAllocateBuffer.restype = ctypes.c_int32
    at.AudioQueueEnqueueBuffer.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    at.AudioQueueEnqueueBuffer.restype = ctypes.c_int32
    at.AudioQueueStart.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    at.AudioQueueStart.restype = ctypes.c_int32
    at.AudioQueuePause.argtypes = [ctypes.c_void_p]
    at.AudioQueuePause.restype = ctypes.c_int32
    at.AudioQueueStop.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    at.AudioQueueStop.restype = ctypes.c_int32
    at.AudioQueueDispose.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    at.AudioQueueDispose.restype = ctypes.c_int32
    return at


def coreaudio_probe():
    """Create + allocate + dispose a queue without starting it (no sound).

    Returns (ok, note) with the raw statuses. Proves the format struct and
    bindings are correct in this environment.
    """
    try:
        at = _bind(_audio_toolbox())
    except Exception as e:
        return False, f"load AudioToolbox: {e!r}"
    try:
        fmt = _ASBD(44100.0, 0x6C70636D, 12, 4, 1, 4, 2, 16, 0)
        q = ctypes.c_void_p()
        st = at.AudioQueueNewOutput(ctypes.byref(fmt), _CBNOP, None, None,
                                    None, 0, ctypes.byref(q))
        if st:
            return False, f"AudioQueueNewOutput status {st} (was -50 pre-fix)"
        bufid = ctypes.c_void_p()
        st = at.AudioQueueAllocateBuffer(q, 44100 * 4, ctypes.byref(bufid))
        if st:
            at.AudioQueueDispose(q, True)
            return False, f"AllocateBuffer status {st}"
        st = at.AudioQueueDispose(q, True)
        if st:
            return False, f"Dispose status {st}"
        return True, "NewOutput 0, AllocateBuffer 0, Dispose 0 (no sound made)"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def coreaudio_play(pcm, seconds=3.0, sr=SAMPLE_RATE, audible=False):
    """Blocking play of pcm[:seconds] via AudioQueue. Returns (ok, note).

    Env-gated: needs audible=True AND XDJ_MACAUDIO_PLAY=1, else returns
    (False, 'env-gated') without touching the speakers (headless-safe).
    """
    if not (audible and os.environ.get(_AUDIBLE_ENV) == "1"):
        return False, f"env-gated (need audible=True + {_AUDIBLE_ENV}=1)"
    try:
        at = _bind(_audio_toolbox())
        n = min(len(pcm) // 4, int(seconds * sr))
        if n <= 0:
            return False, "empty slice"
        blob = bytes(pcm[:n * 4])
        fmt = _ASBD(float(sr), 0x6C70636D, 12, 4, 1, 4, 2, 16, 0)
        q = ctypes.c_void_p()
        st = at.AudioQueueNewOutput(ctypes.byref(fmt), _CBNOP, None, None,
                                    None, 0, ctypes.byref(q))
        if st:
            return False, f"AudioQueueNewOutput status {st}"
        bufid = ctypes.c_void_p()
        st = at.AudioQueueAllocateBuffer(q, n * 4, ctypes.byref(bufid))
        if st:
            at.AudioQueueDispose(q, True)
            return False, f"AllocateBuffer status {st}"
        aq = ctypes.cast(bufid, ctypes.POINTER(_AQB)).contents
        if aq.mAudioDataBytesCapacity < n * 4:
            at.AudioQueueDispose(q, True)
            return False, "buffer too small"
        ctypes.memmove(aq.mAudioData, blob, n * 4)
        aq.mAudioDataByteSize = n * 4
        st = at.AudioQueueEnqueueBuffer(q, bufid, 0, None)
        if st:
            at.AudioQueueDispose(q, True)
            return False, f"EnqueueBuffer status {st}"
        st = at.AudioQueueStart(q, None)
        if st:
            at.AudioQueueDispose(q, True)
            return False, f"AudioQueueStart status {st}"
        time.sleep(n / float(sr) + 0.5)
        at.AudioQueueStop(q, True)
        at.AudioQueueDispose(q, True)
        return True, f"played {n / float(sr):.1f}s via AudioQueue"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# -- backend (b): afplay (most robust: ships with macOS, no deps) -------------

def afplay_available():
    return shutil.which("afplay") is not None


def afplay_play(wav_path, play_seconds=None, audible=False, timeout=30.0):
    """Blocking afplay of a WAV file. Returns (ok, note). Env-gated."""
    if not afplay_available():
        return False, "afplay not found"
    if not (audible and os.environ.get(_AUDIBLE_ENV) == "1"):
        return False, f"env-gated (need audible=True + {_AUDIBLE_ENV}=1)"
    cmd = ["afplay"]
    if play_seconds is not None:
        cmd += ["-t", str(play_seconds)]
    cmd += [wav_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode == 0:
            return True, f"afplay {os.path.basename(wav_path)} exit 0"
        return False, f"afplay exit {r.returncode}: {r.stderr[:160]}"
    except subprocess.TimeoutExpired:
        return False, "afplay timeout"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


class AfplayGate:
    """Non-blocking afplay handle for TransportGate (pause = stop).

    play(pos): (re)starts afplay on a temp WAV sliced at pos seconds.
    Limit: afplay cannot seek, so every resume re-renders the slice (fast:
    a memslice + wave write, no ffmpeg re-decode).
    """

    def __init__(self, pcm, sr=SAMPLE_RATE, tmp="/tmp/macaudio_gate.wav"):
        self.pcm = pcm
        self.sr = sr
        self.tmp = tmp
        self.proc = None

    def _spawn(self, pos=0.0):
        self.stop()
        start = max(0, int(pos * self.sr)) * 4
        with wave.open(self.tmp, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(self.pcm[start:start + 0] if False else
                          self.pcm[start:])
        self.proc = subprocess.Popen(
            ["afplay", self.tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def play(self, pos=0.0):
        if not afplay_available():
            return
        if self.proc is not None and self.proc.poll() is None:
            return  # already sounding
        self._spawn(pos)

    def pause(self):
        self.stop()

    def stop(self):
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
            except Exception:
                pass
            self.proc = None

    @property
    def sounding(self):
        return self.proc is not None and self.proc.poll() is None


# -- backend (c): pygame.mixer (optional; missing in this repo's .venv) --------

def pygame_mixer_info():
    """Report pygame.mixer availability (never raises)."""
    try:
        import pygame.mixer  # noqa: F401
    except Exception as e:
        return (False, f"unavailable in this .venv ({e!r}); "
                       "use CoreAudio/afplay instead")
    try:
        import pygame
        pygame.mixer.init(frequency=44100, size=-16, channels=2)
        ok = pygame.mixer.get_init() is not None
        pygame.mixer.quit()
        return (True, f"init ok: {ok}") if ok else (False, "init returned None")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# -- transport gate ------------------------------------------------------------

class FakePlayer:
    """Headless stand-in: records play/pause calls for logic verification."""

    def __init__(self):
        self.events = []  # ("play", pos) | ("pause", pos)
        self.sounding = False
        self.pos = 0.0

    def play(self, pos=0.0):
        self.events.append(("play", round(float(pos), 3)))
        self.sounding = True
        self.pos = float(pos)

    def pause(self):
        self.events.append(("pause", round(float(self.pos), 3)))
        self.sounding = False


class TransportGate:
    """Gate Mac-side playback on firmware transport state.

    update(machine, now=None): reads mode; mode == 5 -> ensure the player
    is sounding, else ensure paused. Position follows hle.pos_samples when
    the HLE model is attached, else a wall-clock advanced while sounding.
    Never writes firmware state (reads mode + hle clock only).
    """

    def __init__(self, player=None):
        self.player = player
        self.sounding = False
        self.pos = 0.0
        self._t = None
        self.transitions = []  # (mode, action)

    def _hle_pos(self, machine):
        return hle_pos_seconds(machine)

    def update(self, machine, now=None):
        now = time.time() if now is None else float(now)
        m = mode(machine)
        want = (m == MODE_PLAYING)
        hp = self._hle_pos(machine)
        if self._t is None:
            self._t = now
        dt = max(0.0, now - self._t)
        self._t = now
        if self.sounding:
            self.pos += dt
        if hp is not None and want:
            self.pos = hp  # HLE clock wins when available
        if want and not self.sounding:
            if self.player is not None:
                try:
                    self.player.play(self.pos)
                except TypeError:
                    self.player.play()
            self.sounding = True
            action = "play"
        elif want:
            action = "keep-playing"
        elif not want and self.sounding:
            if self.player is not None:
                self.player.pause()
            self.sounding = False
            action = "pause"
        else:
            action = "keep-paused"
        self.transitions.append((m, action))
        if self.player is not None and hasattr(self.player, "pos"):
            try:
                self.player.pos = self.pos
            except Exception:
                pass
        return want, action

    def position(self, machine=None):
        if machine is not None:
            hp = self._hle_pos(machine)
            if hp is not None:
                return hp
        return self.pos


# -- framebuffer screenshot (proof artifact; emu pixels only) ------------------

def fb_png(machine, path):
    """Save the live framebuffer as PNG. Returns (path, nonzero_bytes)."""
    import numpy as np
    from PIL import Image
    reg = machine.mem.dev.reg
    base = sum(reg.get(0xFE940430 + i, 0) << (8 * i) for i in range(4))
    idx = 1 if (base & 0x1FFFFFFF) == 0x0E83B800 else 0
    buf = bytes(machine.mem.fb[idx])
    if not any(buf[::97]):
        buf = bytes(machine.mem.fb[1 - idx])
    a = np.frombuffer(buf, dtype="<u2").astype(np.uint32)
    r = (a >> 11) & 0x1F
    g = (a >> 5) & 0x3F
    b = a & 0x1F
    rgb = np.dstack([
        (((r * 255 + 15) // 31).astype(np.uint8)).reshape(480, 800),
        (((g * 255 + 31) // 63).astype(np.uint8)).reshape(480, 800),
        (((b * 255 + 15) // 31).astype(np.uint8)).reshape(480, 800)])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    Image.fromarray(rgb).save(path)
    return path, sum(1 for x in buf[::16] if x)


# -- proof ---------------------------------------------------------------------

def run_proof(audible=False, ckpt=TRACK_CKPT):
    """Headless proof: WAV render + gating logic + backend probes.

    Makes no sound unless audible=True AND XDJ_MACAUDIO_PLAY=1. Writes
    PROOF_WAV + PROOF_PNG + PROOF_LOG. Returns True iff all checks pass.
    """
    import sys
    sys.path.insert(0, ".")
    lines = []

    def log(s=""):
        lines.append(s)
        print(s, flush=True)

    ok = True
    log("macaudio proof (env-gated; no servers, no deck, read-only stick)")

    # 1. load lineage ----------------------------------------------------------
    from emu.machine import Machine
    if not os.path.isfile(ckpt):
        log(f"FAIL no checkpoint {ckpt}")
        return False
    m = Machine.load(ckpt, core="py")
    m0 = mode(m)
    log(f"loaded {ckpt}: mode={m0} (expect 3 = paused/loaded)")

    # 2. identify + decode + WAV ------------------------------------------------
    path, source = identify_track(m)
    if not path:
        log(f"FAIL identify ({source})")
        return False
    log(f"track: {path}")
    log(f"  via: {source}")
    try:
        st = os.stat(path)
        log(f"  size: {st.st_size:,} B")
    except OSError:
        pass
    try:
        pcm = decode_track(path)
    except Exception as e:
        log(f"FAIL decode: {e!r}")
        return False
    frames = len(pcm) // 4
    log(f"decoded: {frames:,} frames = {frames / 44100.0:.2f}s s16 stereo 44.1k")
    info = render_wav(pcm, PROOF_WAV)
    log(f"wav: {info['path']} {info['frames']:,} frames "
        f"({info['seconds']:.2f}s)")
    if not (os.path.isfile(PROOF_WAV) and info["frames"] > 44100):
        log("FAIL wav render too short/missing")
        ok = False
    else:
        log("PASS (a) WAV renders for the loaded track")

    # 3. transport gating across play/pause transitions -------------------------
    fake = FakePlayer()
    gate = TransportGate(player=fake)
    mem = m.mem
    seq = []
    # t=0 paused (as loaded) -> keep-paused, no player call
    seq.append(gate.update(m, now=1000.0))
    # PLAY pressed: firmware flips 3 -> 5
    mem.w32(MODE_ADDR, 5)
    seq.append(gate.update(m, now=1001.0))
    seq.append(gate.update(m, now=1002.5))  # still playing, wall-clock +1.5s
    # PAUSE: 5 -> 3
    mem.w32(MODE_ADDR, 3)
    seq.append(gate.update(m, now=1003.0))
    seq.append(gate.update(m, now=1004.0))  # still paused
    # PLAY again -> resumes
    mem.w32(MODE_ADDR, 5)
    seq.append(gate.update(m, now=1005.0))
    mem.w32(MODE_ADDR, 3)  # leave lineage as found
    log(f"gating sequence (want, action): {seq}")
    log(f"fake player events: {fake.events}")
    log(f"gate wall pos: {gate.pos:.2f}s (expect ~2.0s across sounding spans)")
    plays = [e for e in fake.events if e[0] == "play"]
    pauses = [e for e in fake.events if e[0] == "pause"]
    checks = [
        (seq[0] == (False, "keep-paused"), "paused start silent"),
        (seq[1] == (True, "play"), "3->5 starts playback"),
        (seq[2] == (True, "keep-playing"), "sustained 5 keeps playing"),
        (seq[3] == (False, "pause"), "5->3 pauses"),
        (seq[4] == (False, "keep-paused"), "sustained 3 stays silent"),
        (seq[5] == (True, "play"), "second 3->5 resumes"),
        (len(plays) == 2 and len(pauses) == 1, "2 plays / 1 pause"),
        (1.0 <= gate.pos <= 3.0, "wall-clock advanced while sounding"),
    ]
    for passed, name in checks:
        log(f"  {'PASS' if passed else 'FAIL'} gating: {name}")
        ok = ok and passed
    if all(p for p, _ in checks):
        log("PASS (b) transport gating correct across play/pause transitions")
    # HLE position honesty: this lineage has no HLE model
    hp = hle_pos_seconds(m)
    log(f"hle position: {hp} (None = no HLE model in this lineage, "
        "wall-clock used -- expected)")

    # 4. backends ----------------------------------------------------------------
    cok, cnote = coreaudio_probe()
    log(f"coreaudio probe: {'PASS' if cok else 'FAIL'}: {cnote}")
    ok = ok and cok
    pok, pnote = pygame_mixer_info()
    log(f"pygame.mixer: {'available' if pok else 'not available'}: {pnote}")
    log(f"afplay present: {afplay_available()}")
    # Audible demos only when explicitly asked AND env allows
    if audible:
        c2ok, c2note = coreaudio_play(pcm, seconds=3.0, audible=True)
        log(f"coreaudio play 3s: {'PASS' if c2ok else 'NOTE'}: {c2note}")
        aok, anote = afplay_play(PROOF_WAV, play_seconds=3, audible=True)
        log(f"afplay play 3s: {'PASS' if aok else 'NOTE'}: {anote}")
    else:
        log("audible play skipped (pass --audible + "
            f"{_AUDIBLE_ENV}=1 to sound 3s)")

    # 5. screenshot ---------------------------------------------------------------
    try:
        p, nz = fb_png(m, PROOF_PNG)
        log(f"screenshot: {p} (nonzero sample {nz})")
    except Exception as e:
        log(f"FAIL screenshot: {e!r}")
        ok = False

    # 6. sh4test reminder (run by caller; cheap enough to run here too) -----------
    try:
        r = subprocess.run([".venv/bin/python", "tools/sh4test.py"],
                           capture_output=True, text=True, timeout=120)
        tail = (r.stdout.strip().splitlines() or [""]) [-1]
        log(f"sh4test: {tail} (rc={r.returncode})")
        if "44 passed, 0 failed" not in r.stdout or r.returncode != 0:
            log("FAIL sh4test regression")
            ok = False
        else:
            log("PASS (c) no regressions (sh4test 44/44)")
    except Exception as e:
        log(f"FAIL sh4test run: {e!r}")
        ok = False

    log(f"RESULT {'PASS' if ok else 'FAIL'}")
    try:
        with open(PROOF_LOG, "w") as f:
            f.write("\n".join(lines) + "\n")
        log(f"log: {PROOF_LOG}")
    except OSError as e:
        print(f"(log write failed: {e!r})", flush=True)
    return ok


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--proof", action="store_true",
                    help="run the headless proof (WAV + gating + probes)")
    ap.add_argument("--audible", action="store_true",
                    help=f"also sound 3s (needs {_AUDIBLE_ENV}=1)")
    ap.add_argument("--ckpt", default=TRACK_CKPT)
    a = ap.parse_args()
    if not a.proof:
        ap.print_help()
        raise SystemExit(2)
    raise SystemExit(0 if run_proof(audible=a.audible, ckpt=a.ckpt) else 1)
