#!/usr/bin/env python3
"""pygame_output_test.py -- PygameOutput (the Windows sound arm of
emu/macaudio.py) against a FAKE pygame.mixer, because this repo's mac
wheel ships without the mixer extension. Verifies the contract the real
Windows wheel must honour: init 44.1k s16 stereo, feeder paced at the
device rate, Sound(buffer=...) chunks of nfr*4 bytes, play then queue,
clean close. Run on Windows against real pygame for the audible proof.
"""
import sys
import time
import types

sys.path.insert(0, ".")


class FakeSound:
    def __init__(self, buffer=b""):
        self.buffer = buffer


class FakeChannel:
    def __init__(self, i):
        self.i = i
        self.played = []
        self.queued = []
        self.busy = False

    def get_busy(self):
        return self.busy

    def play(self, snd):
        self.busy = True
        self.played.append(snd)

    def queue(self, snd):
        self.queued.append(snd)


def fake_pygame():
    pg = types.ModuleType("pygame")
    mixer = types.ModuleType("pygame.mixer")
    ch = [FakeChannel(0)]
    state = {"init": None, "pre": None}

    def pre_init(*a):
        state["pre"] = a

    def init(*a):
        state["init"] = a or True

    def get_init():
        return (44100, -16, 2) if state["init"] else None

    def Channel(i):
        return ch[i]

    def Sound(buffer=b""):
        return FakeSound(buffer)

    def stop():
        ch[0].busy = False
    mixer.pre_init, mixer.init, mixer.get_init = pre_init, init, get_init
    mixer.Channel, mixer.Sound, mixer.stop = Channel, Sound, stop
    mixer._ch, mixer._state = ch, state
    pg.mixer = mixer
    return pg


def main():
    fake = fake_pygame()
    sys.modules["pygame"] = fake
    sys.modules["pygame.mixer"] = fake.mixer
    from emu import macaudio
    macaudio.PygameOutput  # import check

    fills = []

    def fill(nfr):
        fills.append(time.time())
        return bytes(nfr * 4)

    out = macaudio.PygameOutput(4096, fill, sr=44100)
    time.sleep(2.5)
    out.close()
    out.thread.join(timeout=1.0)         # feeder sleeps <=50ms per wait
    dt = fills[-1] - fills[0] if len(fills) > 1 else 0
    rate = (len(fills) - 1) / dt if dt else 0
    ch = fake.mixer._ch[0]
    n = len(fills)
    print(f"fills={n} rate={rate:.1f} Hz (want ~10.7)")
    print(f"channel: play={len(ch.played)} queue={len(ch.queued)} "
          f"total={len(ch.played) + len(ch.queued)}")
    sizes = {len(s.buffer) for s in ch.played + ch.queued}
    print(f"sound buffer sizes: {sizes} (want {{16384}})")
    ok = (9.5 <= rate <= 12.0
          and len(ch.played) >= 1
          and len(ch.played) + len(ch.queued) >= n - 2
          and sizes == {4096 * 4}
          and not out.thread.is_alive())
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
