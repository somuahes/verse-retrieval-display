"""
app/paths.py
=============
One shared "where's the project root" resolver. Every module that
locates bible.db, themes/, or models/ used to compute this itself from
its own __file__ -- fine from source, but wrong once packaged: inside a
PyInstaller-frozen .exe (see scripts/build_exe.spec), source files live
in an internal bundle directory, not next to the executable. Since
models/ is deliberately kept OUTSIDE the packaged app (too large to
bundle -- see CLAUDE.md/TRANSCRIPTION_SETUP.md), every module locating
it needs to agree on where "next to the app" actually means once frozen.
"""
import os
import sys


def app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


ROOT = app_root()
