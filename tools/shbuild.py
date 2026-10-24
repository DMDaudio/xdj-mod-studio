"""Build one of our SH-4 deck modules, or use the shipped prebuilt copy.

The deck code (hot cue pages, live stems, FLAC layer, theme switching...)
is C compiled with the local SH toolchain (work/sh-tools, tools/
build_sh_gcc.sh). Shipped apps (Mac and Windows) have no compiler and no
build scripts: they carry the compiled build/<mod>/*.bin + *.syms, built
with the same default addresses. Use those when the compiler is absent.

A checkout of the public repo has neither build/ (gitignored) nor a
toolchain, but prebuilt/deck/<mod>/ (committed). stage_prebuilt() copies
those into build/<mod>/ once, so every tool that reads build/<mod>/*.bin
works from any layout: dev tree, public checkout, packaged app.
"""
import os
import shutil
import subprocess


def twin(rel):
    """The prebuilt/deck counterpart of a build/<mod>/... path."""
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "build":
        parts[0:1] = ["prebuilt", "deck"]
        return "/".join(parts)
    return None


def stage_prebuilt(cwd=None, force=False):
    """Copy prebuilt/deck/<mod>/* into build/<mod>/ where missing.
    Existing files are never overwritten (a fresh compiler build wins).
    Returns the list of staged relative paths."""
    cwd = cwd or os.getcwd()
    pre = os.path.join(cwd, "prebuilt", "deck")
    if not os.path.isdir(pre):
        return []
    staged = []
    for mod in sorted(os.listdir(pre)):
        src = os.path.join(pre, mod)
        if not os.path.isdir(src):
            continue
        for f in sorted(os.listdir(src)):
            rel = f"build/{mod}/{f}"
            dst = os.path.join(cwd, rel)
            if os.path.exists(dst) and not force:
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(src, f), dst)
            staged.append(rel)
    return staged


def build(script, outputs, env=None, cwd=None):
    """Run `sh script` when the SH compiler is here; else use the shipped
    prebuilt outputs (paths relative to cwd; staged from prebuilt/deck
    when build/<mod>/ does not have them yet)."""
    cwd = cwd or os.getcwd()
    gcc = os.path.join(cwd, "work", "sh-tools", "bin", "sh-elf-gcc")
    if os.path.exists(gcc) and os.path.exists(os.path.join(cwd, script)):
        subprocess.run(["sh", script], check=True, env=env, cwd=cwd,
                       capture_output=True)
        return
    missing = [o for o in outputs if not os.path.exists(os.path.join(cwd, o))]
    if missing:
        stage_prebuilt(cwd)          # public checkouts: build/ is gitignored
        missing = [o for o in outputs if not os.path.exists(os.path.join(cwd, o))]
    if missing:
        raise FileNotFoundError(
            f"deck module not built and no compiler here: missing {missing}")
