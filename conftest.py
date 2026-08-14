"""
conftest.py (project root)
============================
Test-session setup shared by every file under tests/:

- Puts the project root on sys.path (mirrors the ROOT-insertion block at
  the top of app/ui/main_ui.py, needed here too since pytest imports
  test modules directly rather than running main_ui.py as a script).
- Forces Qt's offscreen platform plugin so the full PyQt5 UI (Operator
  Panel, Browser, History, projector Display) can be built and driven
  in CI / a terminal with no real display, without changing a single
  line of app code.
- Applies the same torch DLL-directory fix main_ui.py applies for itself,
  and forces torch to load before pytest imports any test file — see the
  comment below on why import order matters here.
- Provides a session-scoped QApplication — PyQt only allows one per
  process, so every test that builds a widget shares this fixture.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_torch_lib = os.path.join(
    os.path.dirname(sys.executable), "..", "Lib", "site-packages", "torch", "lib"
)
_torch_lib = os.path.abspath(_torch_lib)
if os.path.isdir(_torch_lib):
    os.add_dll_directory(_torch_lib)

# Import order matters on Windows: app/ui/main_ui.py deliberately imports
# the torch-dependent retrieval stack before PyQt5 for the same reason —
# whichever of PyQt5's or torch's bundled runtime DLLs (MSVC/OpenMP/MKL)
# loads into the process first "wins" the DLL search order, and the loser
# fails with WinError 1114. Test files don't all agree on which they
# import first, so force torch to load here, before pytest imports any
# test module — this is the only ordering guarantee that reliably holds
# across however pytest discovers/orders files.
import app.retrieval.hybrid  # noqa: F401

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
