# -*- mode: python ; coding: utf-8 -*-
"""
build_exe.spec
================
PyInstaller build for the Operator Panel as a portable, double-click
Windows .exe — no terminal, no venv activation, no `python -m ...`.

Run with:  .\venv311\Scripts\pyinstaller.exe build_exe.spec
Output:    dist\Bible AI\Bible AI.exe  (copy the whole "Bible AI" folder
           when moving it to another machine — it's onedir, not onefile,
           for faster startup)

Deliberately NOT bundled (must exist next to the .exe after building —
see TRANSCRIPTION_SETUP.md for how to fetch them):
    models/     — sentence-transformer + faster-whisper/w2vbert weights,
                  multiple GB; bundling would make every rebuild slow
                  and the .exe itself huge for no benefit (see
                  app/paths.py for how every module finds this at
                  runtime whether frozen or not).

Bundled (small, needed for the app to run out of the box):
    app/database/bible.db  — prebuilt verse DB (~33MB)
    themes/                — built-in + any saved custom themes
    data/                  — source Bible JSON (w2vbert.py's Twi
                              vocabulary-correction reads this directly)
    assets/icon.ico         — window/taskbar icon (style_kit.app_icon())
"""
import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# The shared Visual C++ runtime DLLs that PyQt5, torch, ctranslate2 and
# sklearn each separately bundle their own copy of. PyInstaller's onedir
# collection keeps PyQt5's copy at PyQt5/Qt5/bin/ -- a folder Qt itself
# adds to the process's DLL search path ahead of everything else (see
# the build's own "Extra DLL search directories (AddDllDirectory)" log
# line) -- so whichever version PyQt5 shipped (14.26.28720.3, dated
# 2020) is the one that gets locked in for the *entire* process the
# moment Qt starts, not just for Qt's own use. Windows never swaps in a
# different version once a DLL name is loaded, so when ctranslate2's
# real model-inference code first runs (only once Start Listening
# actually loads a transcriber -- not at plain import time) it can hit
# an access violation calling into a std-lib function the 2020 runtime
# doesn't behave the same way on. Confirmed via a live WER crash report:
# Fault Module = MSVCP140.dll, version 14.26.28720.3 -- an exact match
# for PyQt5's private copy, not the newer one sitting one level up.
_MSVC_RUNTIME_DLLS = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll",
)
_SYSTEM32 = r"C:\Windows\System32"

datas = [
    ("app/database/bible.db", "app/database"),
    ("themes", "themes"),
    ("data", "data"),
    ("assets", "assets"),
]
binaries = []
hiddenimports = []

# These pull in non-Python binaries / dynamic plugin-style imports that
# PyInstaller's static analysis can't always see on its own.
for pkg in (
    "torch",
    "sentence_transformers",
    "transformers",
    "faster_whisper",
    "ctranslate2",
    "faiss",
    "pyctcdecode",
    "rapidfuzz",
    "sounddevice",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["app/ui/main_ui.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "tkinter"],
    noarchive=False,
    cipher=block_cipher,
)
# Drop PyQt5's own stale copies of the shared MSVC runtime DLLs (see the
# note above) and replace them -- at that same PyQt5/Qt5/bin path Qt
# searches first, and at the top level everything else already expects
# them -- with a single known-good, version-matched set pulled straight
# from this build machine's System32 rather than trusting whichever
# bundled package's copy happens to win the collection race.
a.binaries = [
    (dest, src, kind) for (dest, src, kind) in a.binaries
    if not (dest.replace("\\", "/").lower().startswith("pyqt5/qt5/bin/")
            and os.path.basename(dest).lower() in _MSVC_RUNTIME_DLLS)
]
for _name in _MSVC_RUNTIME_DLLS:
    _src = os.path.join(_SYSTEM32, _name)
    if os.path.exists(_src):
        a.binaries += [
            (_name, _src, "BINARY"),
            (f"PyQt5/Qt5/bin/{_name}", _src, "BINARY"),
        ]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bible AI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Bible AI",
)
