"""Build one of our SH-4 deck modules, or use the shipped prebuilt copy.

The deck code (hot cue pages, live stems, FLAC layer, theme switching...)
is C compiled with the local SH toolchain (work/sh-tools, tools/
build_sh_gcc.sh). Shipped apps (Mac and Windows) have no compiler and no
build scripts: they carry the compiled build/<mod>/*.bin + *.syms, built
with the same default addresses. Use those when the compiler is absent.
"""
import os
import subprocess


def build(script, outputs, env=None, cwd=None):
    """Run `sh script` when the SH compiler is here; else require the
    prebuilt outputs (paths relative to cwd) and leave them as they are."""
    cwd = cwd or os.getcwd()
    gcc = os.path.join(cwd, "work", "sh-tools", "bin", "sh-elf-gcc")
    if os.path.exists(gcc) and os.path.exists(os.path.join(cwd, script)):
        subprocess.run(["sh", script], check=True, env=env, cwd=cwd,
                       capture_output=True)
        return
    missing = [o for o in outputs if not os.path.exists(os.path.join(cwd, o))]
    if missing:
        raise FileNotFoundError(
            f"deck module not built and no compiler here: missing {missing}")
