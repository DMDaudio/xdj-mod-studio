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
