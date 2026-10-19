"""Where the project's data lives: firmware builds, the snapshot, themes.

From source, the repo this package sits in. Inside the frozen .app the
code is unpacked to a temp dir, so the repo path is baked in at build time
(emu/_buildinfo.py, written by tools/build_app.sh); XDJ_ROOT overrides both.
"""
import os
import sys


def repo_root():
    env = os.environ.get("XDJ_ROOT")
    if env and os.path.isdir(env):
        return env
    if getattr(sys, "frozen", False):
        try:
            from ._buildinfo import REPO
            if os.path.isdir(REPO):
                return REPO
        except ImportError:
            pass
        return getattr(sys, "_MEIPASS", os.getcwd())
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_support_dir():
    """Per-user folder for the app's own files (settings, firmware home,
    caches). Mac: ~/Library/Application Support/XDJ Mod Studio;
    Windows: %LOCALAPPDATA%\\XDJ Mod Studio."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local"))
        return os.path.join(base, "XDJ Mod Studio")
    return os.path.expanduser("~/Library/Application Support/XDJ Mod Studio")


def native_lib(stem):
    """File name of a compiled helper library: _x.so (Mac) / _x.dll."""
    return stem + (".dll" if sys.platform == "win32" else ".so")
