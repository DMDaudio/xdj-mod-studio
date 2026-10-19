"""A Retina-sharp window for pygame on macOS.

pygame 2.6's `_sdl2.video.Window(allow_highdpi=True)` silently drops the
SDL_WINDOW_ALLOW_HIGHDPI flag (measured: window flags 0x26, pixel size ==
point size), so every pygame window on this Mac renders at half resolution
and macOS doubles it up -- the blurry/pixelated look. This opens the window
through SDL directly, with the flag, and draws a pygame Surface into it with
a linear-filtered texture at the display's real pixel count.

Events still arrive through pygame.event (SDL has one event queue); mouse
coordinates are in points, so use `to_canvas()` for hit-testing.
"""
import ctypes
import ctypes.util

import pygame

SDL_WINDOW_SHOWN = 0x04
SDL_WINDOW_RESIZABLE = 0x20
SDL_WINDOW_ALLOW_HIGHDPI = 0x2000
SDL_WINDOWPOS_CENTERED = 0x2FFF0000
SDL_RENDERER_ACCELERATED = 0x02
SDL_PIXELFORMAT_XRGB8888 = 0x16161804     # SDL_PIXELFORMAT_RGB888
SDL_TEXTUREACCESS_STREAMING = 1
SDL_SCALEMODE_LINEAR = 1

_vp = ctypes.c_void_p
_i = ctypes.c_int


def _find_sdl():
    """The SDL2 library pygame itself loaded (Homebrew or app bundle)."""
    libc = ctypes.CDLL(None)
    libc._dyld_image_count.restype = ctypes.c_uint32
    libc._dyld_get_image_name.restype = ctypes.c_char_p
    for k in range(libc._dyld_image_count()):
        name = libc._dyld_get_image_name(k) or b""
        if b"libSDL2" in name:
            return ctypes.CDLL(name.decode())
    return ctypes.CDLL(ctypes.util.find_library("SDL2"))


class _Rect(ctypes.Structure):
    _fields_ = [("x", _i), ("y", _i), ("w", _i), ("h", _i)]


class HiDPIWindow:
    def __init__(self, title, size, icon=None):
        L = self.L = _find_sdl()
        L.SDL_CreateWindow.restype = _vp
        L.SDL_CreateWindow.argtypes = [ctypes.c_char_p, _i, _i, _i, _i, ctypes.c_uint32]
        L.SDL_CreateRenderer.restype = _vp
        L.SDL_CreateRenderer.argtypes = [_vp, _i, ctypes.c_uint32]
        L.SDL_CreateTexture.restype = _vp
        L.SDL_CreateTexture.argtypes = [_vp, ctypes.c_uint32, _i, _i, _i]
        L.SDL_DestroyTexture.argtypes = [_vp]
        L.SDL_SetTextureScaleMode.argtypes = [_vp, _i]
        L.SDL_UpdateTexture.argtypes = [_vp, _vp, ctypes.c_char_p, _i]
        L.SDL_RenderCopy.argtypes = [_vp, _vp, _vp, _vp]
        L.SDL_RenderClear.argtypes = [_vp]
        L.SDL_RenderPresent.argtypes = [_vp]
        L.SDL_SetRenderDrawColor.argtypes = [_vp, ctypes.c_uint8, ctypes.c_uint8,
                                             ctypes.c_uint8, ctypes.c_uint8]
        for f in ("SDL_GetWindowSize", "SDL_GetRendererOutputSize"):
            getattr(L, f).argtypes = [_vp, ctypes.POINTER(_i), ctypes.POINTER(_i)]
        L.SDL_SetWindowSize.argtypes = [_vp, _i, _i]
        L.SDL_SetWindowTitle.argtypes = [_vp, ctypes.c_char_p]
        L.SDL_DestroyRenderer.argtypes = [_vp]
        L.SDL_DestroyWindow.argtypes = [_vp]

        w, h = size
        self.win = L.SDL_CreateWindow(
            title.encode(), SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, w, h,
            SDL_WINDOW_SHOWN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_ALLOW_HIGHDPI)
        if not self.win:
            raise RuntimeError("SDL_CreateWindow failed")
        self.ren = L.SDL_CreateRenderer(self.win, -1, SDL_RENDERER_ACCELERATED)
        if not self.ren:
            raise RuntimeError("SDL_CreateRenderer failed")
        self.tex = None
        self.tex_size = None
        self.map = (0, 0, 1.0, 1.0)
        if icon is not None:
            self.set_icon(icon)

    # -- geometry -------------------------------------------------------------
    @property
    def size(self):
        """Window size in points (what the mouse reports)."""
        a, b = _i(), _i()
        self.L.SDL_GetWindowSize(self.win, a, b)
        return a.value, b.value

    @size.setter
    def size(self, wh):
        self.L.SDL_SetWindowSize(self.win, int(wh[0]), int(wh[1]))

    @property
    def pixel_size(self):
        a, b = _i(), _i()
        self.L.SDL_GetRendererOutputSize(self.ren, a, b)
        return a.value, b.value

    def set_icon(self, surf):
        L = self.L
        L.SDL_CreateRGBSurfaceWithFormatFrom.restype = _vp
        L.SDL_CreateRGBSurfaceWithFormatFrom.argtypes = [
            ctypes.c_char_p, _i, _i, _i, _i, ctypes.c_uint32]
        L.SDL_SetWindowIcon.argtypes = [_vp, _vp]
        L.SDL_FreeSurface.argtypes = [_vp]
        w, h = surf.get_size()
        buf = pygame.image.tobytes(surf, "RGBA")
        # SDL_PIXELFORMAT_ABGR8888 == bytes R,G,B,A in memory on little-endian
        s = L.SDL_CreateRGBSurfaceWithFormatFrom(buf, w, h, 32, w * 4, 0x16762004)
        if s:
            L.SDL_SetWindowIcon(self.win, s)
            L.SDL_FreeSurface(s)

    # -- drawing --------------------------------------------------------------
    def present(self, surf, bg=(12, 12, 13)):
        """Draw `surf` as large as fits, centred, at full Retina resolution."""
        L = self.L
        cw, ch = surf.get_size()
        if self.tex_size != (cw, ch):
            if self.tex:
                L.SDL_DestroyTexture(self.tex)
            self.tex = L.SDL_CreateTexture(self.ren, SDL_PIXELFORMAT_XRGB8888,
                                           SDL_TEXTUREACCESS_STREAMING, cw, ch)
            L.SDL_SetTextureScaleMode(self.tex, SDL_SCALEMODE_LINEAR)
            self.tex_size = (cw, ch)
        L.SDL_UpdateTexture(self.tex, None, pygame.image.tobytes(surf, "BGRA"), cw * 4)
        pw, ph = self.pixel_size
        s = min(pw / cw, ph / ch)
        dw, dh = int(round(cw * s)), int(round(ch * s))
        dx, dy = (pw - dw) // 2, (ph - dh) // 2
        L.SDL_SetRenderDrawColor(self.ren, *bg, 255)
        L.SDL_RenderClear(self.ren)
        L.SDL_RenderCopy(self.ren, self.tex, None, ctypes.byref(_Rect(dx, dy, dw, dh)))
        L.SDL_RenderPresent(self.ren)
        self.map = (dx, dy, s, pw / max(1, self.size[0]))

    def to_canvas(self, pos):
        import os as _os
        dx, dy, s, k = self.map
        # Measured 2026-09-29: SDL delivers mouse coords already in
        # pixels for this window (clicks span the full pixel width),
        # so the Retina k factor must NOT be applied again (it doubled
        # every click). Override with XDJ_MOUSEK if a setup differs.
        k = float(_os.environ.get("XDJ_MOUSEK", "1.0"))
        return int((pos[0] * k - dx) / s), int((pos[1] * k - dy) / s)

    def poll(self):
        """Input events for this window (see module note)."""
        return poll_events(self.L)

    def close(self):
        if self.tex:
            self.L.SDL_DestroyTexture(self.tex)
        self.L.SDL_DestroyRenderer(self.ren)
        self.L.SDL_DestroyWindow(self.win)


# -- events -------------------------------------------------------------------
# pygame.event never sees this window (it was created behind pygame's back),
# so poll SDL's own queue here. Types/fields mirror pygame's numbering
# (pygame copies SDL's: QUIT=0x100, KEYDOWN/UP=0x300/0x301,
# MOTION/WHEEL/BTNDN/BTNUP=0x400..0x403), so ui.handle() works unchanged.
_EV_QUIT = 0x100
_EV_WINDOW = 0x200
_EV_WIN_CLOSE = 14
_EV_WIN_RESIZED = 5
_EV_WIN_SIZE_CHANGED = 6
_EV_KEYDOWN = 0x300
_EV_KEYUP = 0x301
_EV_MOTION = 0x400
_EV_BTNDN = 0x401
_EV_BTNUP = 0x402
_EV_WHEEL = 0x403
_EV_VIDEORESIZE = pygame.VIDEORESIZE  # 32769 on pygame 2.x (not 16)


class SdlEv:
    __slots__ = ("type", "button", "pos", "key", "mod", "y")

    def __init__(self, type, button=0, pos=(0, 0), key=0, mod=0, y=0):
        self.type = type
        self.button = button
        self.pos = pos
        self.key = key
        self.mod = mod
        self.y = y


def _ev_setup(L):
    L.SDL_PumpEvents.restype = None
    L.SDL_PumpEvents.argtypes = []
    L.SDL_PollEvent.restype = _i
    L.SDL_PollEvent.argtypes = [ctypes.c_char_p]


def poll_events(L):
    """Yield SdlEv for everything pending in SDL's queue."""
    if getattr(poll_events, "_setup", None) is not L:
        _ev_setup(L)
        poll_events._setup = L
    L.SDL_PumpEvents()
    buf = ctypes.create_string_buffer(56)
    out = []
    while L.SDL_PollEvent(buf):
        raw = buf.raw
        typ = int.from_bytes(raw[0:4], "little")
        if typ == _EV_QUIT:
            out.append(SdlEv(typ))
        elif typ == _EV_WINDOW:
            eid = raw[12]
            if eid == _EV_WIN_CLOSE:
                out.append(SdlEv(_EV_QUIT))
            elif eid in (_EV_WIN_RESIZED, _EV_WIN_SIZE_CHANGED):
                out.append(SdlEv(_EV_VIDEORESIZE))
        elif typ in (_EV_KEYDOWN, _EV_KEYUP):
            sym = int.from_bytes(raw[20:24], "little", signed=True)
            mod = int.from_bytes(raw[24:26], "little")
            out.append(SdlEv(typ, key=sym, mod=mod))
        elif typ == _EV_MOTION:
            x = int.from_bytes(raw[20:24], "little", signed=True)
            y = int.from_bytes(raw[24:28], "little", signed=True)
            out.append(SdlEv(typ, pos=(x, y)))
        elif typ in (_EV_BTNDN, _EV_BTNUP):
            b = raw[16]
            x = int.from_bytes(raw[20:24], "little", signed=True)
            y = int.from_bytes(raw[24:28], "little", signed=True)
            out.append(SdlEv(typ, button=b, pos=(x, y)))
        elif typ == _EV_WHEEL:
            y = int.from_bytes(raw[20:24], "little", signed=True)
            out.append(SdlEv(typ, y=y))
    return out
