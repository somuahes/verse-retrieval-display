"""
app/ui/main_ui.py
=================
Operator panel — Bible AI. Self-contained entry point: run this file
directly (`python -m app.ui.main_ui` or `python app/ui/main_ui.py`).

Owns:
- HybridEngine construction
- Projector DisplayWindow (app/ui/display_window.py)
- Microphone lifecycle (device selection, start/stop)

Features:
- Dark / Light / System theme
- Detection reason log
- Clean responsive card layout
"""

import os
import sys

# Must run before any `app.*` import — when this file is launched
# directly (`python app/ui/main_ui.py`), Python only puts this file's
# own directory (app/ui/) on sys.path, not the project root, so
# `app.retrieval.hybrid` etc. can't resolve without this.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Fix DLL loading on paths with spaces (Windows) ────────
_torch_lib = os.path.join(
    os.path.dirname(sys.executable),
    "..", "Lib", "site-packages", "torch", "lib"
)
_torch_lib = os.path.abspath(_torch_lib)
if os.path.isdir(_torch_lib):
    os.add_dll_directory(_torch_lib)

from app.retrieval.hybrid import HybridEngine, db_list_versions, db_get_verse
from app.retrieval.reference_extractor import extract_reference, _BOOK_ALIASES
from app.asr.transcriber import BibleAITranscriber, Config
from app.ui.display_window import DisplayWindow
from app.ui.theme_model import Theme
from app.ui import theme_store
from app.ui.theme_designer import ThemeDesigner

from PyQt5.QtGui import QPalette
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QLineEdit, QComboBox, QFrame,
    QSizePolicy, QSplitter, QScrollArea, QCompleter,
)

import sounddevice as sd
import time
import argparse
from datetime import datetime
from typing import Optional, List


# ════════════════════════════════════════════════════════════
# THEMES
# ════════════════════════════════════════════════════════════

THEMES = {
    "dark": {
        "bg":        "#0D0F14",
        "panel":     "#13161E",
        "card":      "#1A1E29",
        "border":    "#252A38",
        "border_hi": "#353D54",
        "text_p":    "#F0EBE0",
        "text_m":    "#8A8FA8",
        "text_d":    "#4A5068",
        "gold":      "#C9A84C",
        "gold_l":    "#E8C97A",
        "gold_d":    "#7A6330",
        "green":     "#3DD68C",
        "green_d":   "#1A4D36",
        "red":       "#E85555",
        "red_d":     "#4D1E1E",
        "amber":     "#E8A830",
        "amber_d":   "#4D3810",
        "log_ref":   "#C9A84C",
        "log_reason": "#8A8FA8",
        "log_ts":    "#4A5068",
    },
    "light": {
        "bg":        "#F4F6FB",
        "panel":     "#FFFFFF",
        "card":      "#ECEEF5",
        "border":    "#D0D4E4",
        "border_hi": "#A0A8C8",
        "text_p":    "#1A1D2E",
        "text_m":    "#4A5068",
        "text_d":    "#8A8FA8",
        "gold":      "#7A6330",
        "gold_l":    "#C9A84C",
        "gold_d":    "#E8C97A",
        "green":     "#1A7A4A",
        "green_d":   "#C8EED8",
        "red":       "#C0392B",
        "red_d":     "#FADBD8",
        "amber":     "#B7770D",
        "amber_d":   "#FDEBD0",
        "log_ref":   "#7A6330",
        "log_reason": "#4A5068",
        "log_ts":    "#8A8FA8",
    },
}

T = THEMES["dark"]
RS = "6px"
R = "10px"


def _t(key): return T[key]


# ════════════════════════════════════════════════════════════
# STYLE HELPERS
# ════════════════════════════════════════════════════════════

def global_qss():
    return f"""
    QWidget {{
        background: transparent;
        color: {_t('text_p')};
        font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
        font-size: 13px;
    }}
    QScrollBar:vertical {{
        background: {_t('bg')}; width: 6px; border-radius: 3px;
    }}
    QScrollBar::handle:vertical {{
        background: {_t('border_hi')}; border-radius: 3px; min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ height: 0; }}
    QToolTip {{
        background: {_t('card')}; color: {_t('text_p')};
        border: 1px solid {_t('border_hi')}; padding: 4px;
    }}
    """


def btn_qss(kind="ghost"):
    bg, fg, border, hover, pressed = {
        "primary": (_t('gold'),    "#1A1200",    _t('gold'),    _t('gold_l'), _t('gold_d')),
        "danger":  (_t('red_d'),   _t('red'),    _t('red_d'),   _t('red'),    _t('red_d')),
        "nav":     (_t('card'),    _t('text_p'), _t('border'),  _t('border_hi'), _t('border')),
        "ghost":   ("transparent", _t('text_m'), _t('border'),  _t('card'),   _t('border')),
        "active":  (_t('gold_d'),  _t('gold_l'), _t('gold_d'),  _t('gold_d'), _t('gold_d')),
    }.get(kind, ("transparent", _t('text_m'), _t('border'), _t('card'), _t('border')))

    disabled_bg = _t('border') if kind == "primary" else "transparent"

    return (
        f"QPushButton {{"
        f"  background: {bg}; color: {fg}; border: 1px solid {border};"
        f"  border-radius: {RS}; padding: 8px 16px; font-weight: 600;"
        f"}}"
        f"QPushButton:hover {{ background: {hover}; color: {_t('text_p')}; border-color: {_t('border_hi')}; }}"
        f"QPushButton:pressed {{ background: {pressed}; }}"
        f"QPushButton:disabled {{ background: {disabled_bg}; color: {_t('text_d')}; border-color: {_t('border')}; }}"
    )


def combo_qss():
    return (
        f"QComboBox {{"
        f"  background: {_t('bg')}; border: 1px solid {_t('border')};"
        f"  border-radius: {RS}; color: {_t('text_p')}; padding: 7px 10px;"
        f"}}"
        f"QComboBox:hover {{ border-color: {_t('border_hi')}; }}"
        f"QComboBox::drop-down {{ border: none; width: 22px; }}"
        f"QComboBox QAbstractItemView {{"
        f"  background: {_t('panel')}; border: 1px solid {_t('border_hi')};"
        f"  color: {_t('text_p')}; selection-background-color: {_t('gold_d')};"
        f"  selection-color: {_t('gold_l')};"
        f"}}"
    )


def input_qss():
    return (
        f"QLineEdit {{"
        f"  background: {_t('bg')}; border: 1px solid {_t('border')};"
        f"  border-radius: {RS}; color: {_t('text_p')}; padding: 7px 10px;"
        f"}}"
        f"QLineEdit:focus {{ border-color: {_t('gold_d')}; }}"
    )


def textedit_qss():
    return (
        f"QTextEdit {{"
        f"  background: {_t('bg')}; border: 1px solid {_t('border')};"
        f"  border-radius: {RS}; color: {_t('text_p')}; padding: 10px;"
        f"  font-family: 'Consolas', 'Menlo', monospace; font-size: 12px;"
        f"}}"
    )


def card_qss(gold_border=False):
    border = _t('gold_d') if gold_border else _t('border')
    bg = "#1E1A0E" if gold_border and T is THEMES["dark"] else _t('card')
    return (
        f"QFrame {{"
        f"  background: {bg}; border: 1px solid {border};"
        f"  border-radius: {R};"
        f"}}"
    )


def lbl(text="", size=13, color_key="text_p", bold=False):
    l = QLabel(text)
    l.setStyleSheet(
        f"color: {_t(color_key)}; font-size: {size}px;"
        f"font-weight: {'700' if bold else '400'};"
        f"background: transparent; border: none;"
    )
    return l


def _card(gold=False):
    f = QFrame()
    f.setStyleSheet(card_qss(gold))
    return f


# ════════════════════════════════════════════════════════════
# AI DETECTIONS
# ════════════════════════════════════════════════════════════

# Detections below this confidence never reach the panel.
DETECTION_MIN_CONFIDENCE = 0.35

# Keep the panel from growing unbounded over a long service.
DETECTION_MAX_CARDS = 30

SOURCE_BADGE = {
    "direct":         ("DIRECT",   "green",  "green_d"),
    "semantic":       ("SEMANTIC", "gold",   "gold_d"),
    "navigation":     ("NAV",      "text_m", "border"),
    "verse_jump":     ("NAV",      "text_m", "border"),
    "version_switch": ("VERSION",  "text_m", "border"),
    "manual":         ("MANUAL",   "text_m", "border"),
}

# match_types that are deterministic operator/voice commands, not
# probabilistic AI guesses — hybrid.py tags these with a fixed
# confidence=1.0 (nothing to grade), and they're never held back by
# Auto/Manual mode (see _on_verse) the way a "semantic" guess is.
COMMAND_MATCH_TYPES = {"navigation", "verse_jump", "version_switch", "manual"}

# Confidence tiers — the underlying matching model has a real precision
# ceiling on short generic phrases (see project notes), so instead of
# pretending a single threshold separates "right" from "wrong", the panel
# marks borderline detections for a quick operator glance rather than
# hiding or auto-trusting them.
CONFIDENCE_HIGH = 0.65


def _confidence_tier(confidence: float, match_type: str = ""):
    if match_type in COMMAND_MATCH_TYPES:
        return "OPERATOR COMMAND", "text_m"
    if confidence >= CONFIDENCE_HIGH:
        return "HIGH CONFIDENCE", "green"
    return "POSSIBLE MATCH", "amber"


def _make_badge_pair(verse: dict):
    """Source badge + confidence % labels for a verse dict, or (None,
    None) if it didn't come from an AI detection (e.g. a plain Book-mode
    search result has no match_type/confidence to show)."""
    if "match_type" not in verse:
        return None, None

    match_type = str(verse.get("match_type", ""))
    confidence = float(verse.get("confidence", 0))

    label, fg_key, bg_key = SOURCE_BADGE.get(
        match_type, (match_type.upper() or "?", "text_m", "border"))
    badge = QLabel(label)
    badge.setStyleSheet(
        f"color: {_t(fg_key)}; background: {_t(bg_key)};"
        f"border-radius: 4px; padding: 2px 6px;"
        f"font-size: 9px; font-weight: 700; letter-spacing: 1px; border: none;")

    tier_label, tier_color = _confidence_tier(confidence, match_type)
    # A "100%" readout next to a button click reads like a probability
    # judgment that was never made — commands show a short dash instead;
    # the full "OPERATOR COMMAND" label is still available as a tooltip.
    conf_text = "—" if match_type in COMMAND_MATCH_TYPES else f"{confidence * 100:.0f}%"
    conf_lbl = QLabel(conf_text)
    conf_lbl.setToolTip(tier_label)
    conf_lbl.setStyleSheet(
        f"color: {_t(tier_color)}; font-size: 9px; font-weight: 700;"
        f"background: transparent; border: none;")

    return badge, conf_lbl


# ════════════════════════════════════════════════════════════
# AUTO / MANUAL DISPLAY MODE
# ════════════════════════════════════════════════════════════
# UI-level policy only — this decides whether/when the operator panel
# forwards a verse hybrid.py already selected to Preview/Live. It never
# changes which verse hybrid.py picks.

AUTO_COOLDOWN_DIRECT = 1.5     # seconds — direct refs can go live fast
AUTO_COOLDOWN_SEMANTIC = 4.0     # seconds — semantic needs more settle time
AUTO_SEMANTIC_MIN_CONFIDENCE = 0.65     # stricter bar for auto-push specifically


class DetectionCard(QFrame):
    """One card in the AI Detections panel. ▶ sends to Preview
    (and Live, if Go Live is on); + adds to the Queue."""

    send_preview = pyqtSignal(dict)
    add_queue = pyqtSignal(dict)

    def __init__(self, verse: dict, parent=None):
        super().__init__(parent)
        self._verse = verse
        self.setStyleSheet(card_qss())
        self._build(verse)

    def _build(self, verse: dict):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        confidence = float(verse.get("confidence", 0))
        match_type = str(verse.get("match_type", ""))
        tier_label, tier_color = _confidence_tier(confidence, match_type)

        bar = QFrame()
        bar.setFixedWidth(3)
        bar.setStyleSheet(
            f"background: {_t(tier_color)}; border-top-left-radius: {R}; border-bottom-left-radius: {R};")
        outer.addWidget(bar)

        lay = QVBoxLayout()
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        outer.addLayout(lay)

        book = verse.get("book", "")
        chapter = verse.get("chapter", "")
        vnum = verse.get("verse", "")
        text = str(verse.get("text", ""))
        matched_at = verse.get("matched_at")

        top = QHBoxLayout()
        top.setSpacing(6)

        ref = QLabel(f"{book} {chapter}:{vnum}")
        ref.setStyleSheet(
            f"color: {_t('gold')}; font-size: 13px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        top.addWidget(ref)
        top.addStretch()

        label, fg_key, bg_key = SOURCE_BADGE.get(
            match_type, (match_type.upper() or "?", "text_m", "border"))
        badge = QLabel(label)
        badge.setStyleSheet(
            f"color: {_t(fg_key)}; background: {_t(bg_key)};"
            f"border-radius: 4px; padding: 2px 6px;"
            f"font-size: 9px; font-weight: 700; letter-spacing: 1px; border: none;")
        top.addWidget(badge)

        conf_text = "—" if match_type in COMMAND_MATCH_TYPES else f"{confidence * 100:.0f}%"
        conf_lbl = QLabel(conf_text)
        conf_lbl.setToolTip(tier_label)
        conf_lbl.setStyleSheet(
            f"color: {_t(tier_color)}; font-size: 10px; font-weight: 700;"
            f"background: transparent; border: none;")
        top.addWidget(conf_lbl)

        lay.addLayout(top)

        snippet = text if len(text) <= 110 else text[:107] + "…"
        text_lbl = QLabel(f'"{snippet}"')
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet(
            f"color: {_t('text_p')}; font-size: 12px; font-family: 'Georgia';"
            f"background: transparent; border: none;")
        lay.addWidget(text_lbl)

        source_text = str(verse.get("source_text", "")).strip()
        if source_text:
            source_snippet = (
                source_text if len(source_text) <= 90
                else source_text[:87] + "…"
            )
            # "heard:" implies speech — misleading for a button click or
            # version switch, which aren't transcribed audio.
            prefix = "via:" if match_type in COMMAND_MATCH_TYPES else "heard:"
            source_lbl = QLabel(f'{prefix} "{source_snippet}"')
            source_lbl.setWordWrap(True)
            source_lbl.setToolTip(source_text)
            source_lbl.setStyleSheet(
                f"color: {_t('text_d')}; font-size: 10px; font-style: italic;"
                f"background: transparent; border: none;")
            lay.addWidget(source_lbl)

        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        ts_text = ""
        if matched_at:
            try:
                ts_text = datetime.fromtimestamp(matched_at).strftime("%H:%M:%S")
            except Exception:
                ts_text = ""
        ts_lbl = QLabel(ts_text)
        ts_lbl.setStyleSheet(
            f"color: {_t('text_d')}; font-size: 10px; background: transparent; border: none;")
        bottom.addWidget(ts_lbl)
        bottom.addStretch()

        preview_btn = QPushButton("▶")
        preview_btn.setToolTip("Send to Preview")
        preview_btn.setFixedSize(26, 22)
        preview_btn.setStyleSheet(btn_qss("nav"))
        preview_btn.clicked.connect(lambda: self.send_preview.emit(self._verse))
        bottom.addWidget(preview_btn)

        queue_btn = QPushButton("+")
        queue_btn.setToolTip("Add to Queue")
        queue_btn.setFixedSize(26, 22)
        queue_btn.setStyleSheet(btn_qss("ghost"))
        queue_btn.clicked.connect(lambda: self.add_queue.emit(self._verse))
        bottom.addWidget(queue_btn)

        lay.addLayout(bottom)


class QueueItem(QFrame):
    """One row in the Queue — manual advance only, no auto-pop."""

    send_preview = pyqtSignal(dict)
    remove_item = pyqtSignal(object)

    def __init__(self, verse: dict, parent=None):
        super().__init__(parent)
        self._verse = verse
        self.setStyleSheet(card_qss())
        self._build(verse)

    def _build(self, verse: dict):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        book = verse.get("book", "")
        chapter = verse.get("chapter", "")
        vnum = verse.get("verse", "")
        text = str(verse.get("text", ""))

        info = QVBoxLayout()
        info.setSpacing(2)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        ref = QLabel(f"{book} {chapter}:{vnum}")
        ref.setStyleSheet(
            f"color: {_t('gold')}; font-size: 12px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        top_row.addWidget(ref)
        badge, conf_lbl = _make_badge_pair(verse)
        if badge:
            top_row.addWidget(badge)
            top_row.addWidget(conf_lbl)
        top_row.addStretch()
        info.addLayout(top_row)
        snippet = text if len(text) <= 70 else text[:67] + "…"
        snip_lbl = QLabel(snippet)
        snip_lbl.setWordWrap(True)
        snip_lbl.setStyleSheet(
            f"color: {_t('text_m')}; font-size: 11px; background: transparent; border: none;")
        info.addWidget(snip_lbl)
        lay.addLayout(info, stretch=1)

        send_btn = QPushButton("▶")
        send_btn.setToolTip("Send to Preview")
        send_btn.setFixedSize(26, 22)
        send_btn.setStyleSheet(btn_qss("nav"))
        send_btn.clicked.connect(lambda: self.send_preview.emit(self._verse))
        lay.addWidget(send_btn)

        remove_btn = QPushButton("✕")
        remove_btn.setToolTip("Remove from Queue")
        remove_btn.setFixedSize(26, 22)
        remove_btn.setStyleSheet(btn_qss("danger"))
        remove_btn.clicked.connect(lambda: self.remove_item.emit(self))
        lay.addWidget(remove_btn)


class SearchResultItem(QFrame):
    """One row in Context-mode search results (the top hit also gets
    auto-promoted; this list lets the operator pick a different one)."""

    send_preview = pyqtSignal(dict)
    add_queue = pyqtSignal(dict)

    def __init__(self, verse: dict, parent=None):
        super().__init__(parent)
        self._verse = verse
        self.setStyleSheet(card_qss())
        self._build(verse)

    def _build(self, verse: dict):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        book = verse.get("book", "")
        chapter = verse.get("chapter", "")
        vnum = verse.get("verse", "")
        text = str(verse.get("text", ""))

        info = QVBoxLayout()
        info.setSpacing(2)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        ref = QLabel(f"{book} {chapter}:{vnum}")
        ref.setStyleSheet(
            f"color: {_t('gold')}; font-size: 12px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        top_row.addWidget(ref)
        badge, conf_lbl = _make_badge_pair(verse)
        if badge:
            top_row.addWidget(badge)
            top_row.addWidget(conf_lbl)
        top_row.addStretch()
        info.addLayout(top_row)
        snippet = text if len(text) <= 65 else text[:62] + "…"
        snip_lbl = QLabel(snippet)
        snip_lbl.setWordWrap(True)
        snip_lbl.setStyleSheet(
            f"color: {_t('text_m')}; font-size: 10px; background: transparent; border: none;")
        info.addWidget(snip_lbl)
        lay.addLayout(info, stretch=1)

        send_btn = QPushButton("▶")
        send_btn.setToolTip("Send to Preview")
        send_btn.setFixedSize(24, 20)
        send_btn.setStyleSheet(btn_qss("nav"))
        send_btn.clicked.connect(lambda: self.send_preview.emit(self._verse))
        lay.addWidget(send_btn)

        add_btn = QPushButton("+")
        add_btn.setToolTip("Add to Queue")
        add_btn.setFixedSize(24, 20)
        add_btn.setStyleSheet(btn_qss("ghost"))
        add_btn.clicked.connect(lambda: self.add_queue.emit(self._verse))
        lay.addWidget(add_btn)


# ════════════════════════════════════════════════════════════
# DEVICE HELPERS
# ════════════════════════════════════════════════════════════

def list_input_devices():
    out = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                name = d["name"][:55] + ("…" if len(d["name"]) > 55 else "")
                out.append((i, name))
    except Exception:
        pass
    return out


def default_input_index():
    try:
        return sd.default.device[0]
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# ASR WORKER
# ════════════════════════════════════════════════════════════

class ASRWorker(QThread):
    transcript_signal = pyqtSignal(str)
    status_signal     = pyqtSignal(str)

    def __init__(self, device_index=None, engine=None, parent=None):
        super().__init__(parent)
        self.device_index = device_index
        self.engine       = engine
        self.transcriber  = None

    def run(self):
        try:
            self.status_signal.emit("loading")
            self.transcriber = BibleAITranscriber(
                Config(device_index=self.device_index)
            )

            def _on_text(text: str):
                # Engine gets the text immediately in this callback
                # thread — no Qt overhead in the hot path.
                if self.engine:
                    try:
                        self.engine.process(text)
                    except Exception as e:
                        print("[Engine error]", e)

                # UI transcript box updated separately via signal.
                # Qt queues this to the main thread — it never blocks
                # or delays the engine call above.
                self.transcript_signal.emit(text)

            self.transcriber.set_callback(_on_text)
            self.transcriber.start()
            self.status_signal.emit("listening")
            while not self.isInterruptionRequested():
                time.sleep(0.3)
        except Exception as e:
            self.status_signal.emit(f"error:{e}")

    def stop_worker(self):
        self.requestInterruption()
        if self.transcriber:
            self.transcriber.stop()
            self.transcriber = None
        self.status_signal.emit("stopped")
        if not self.wait(3000):
            self.terminate()


# ════════════════════════════════════════════════════════════
# OPERATOR WINDOW
# ════════════════════════════════════════════════════════════

class OperatorWindow(QMainWindow):
    _verse_sig      = pyqtSignal(object)
    _status_sig     = pyqtSignal(str, str)
    _no_match_sig   = pyqtSignal()

    def __init__(self, version: str = "KJV"):
        super().__init__()
        self.setWindowTitle("Bible AI · Operator Panel")
        self.setMinimumSize(1300, 760)
        self.resize(1440, 860)

        self._settings   = QSettings("BibleAI", "OperatorPanel")
        self._theme_mode = self._settings.value("theme", "dark")
        self._apply_theme(self._theme_mode, boot=True)

        # Auto/Manual display mode. Defaults to manual — given the
        # matching model's real precision ceiling on short generic
        # phrases (see project notes), nothing should reach the
        # congregation without operator approval unless explicitly
        # switched to Auto.
        self._display_mode: str = self._settings.value("display_mode", "manual")
        self._last_auto_push_time: float = 0.0
        self._last_auto_confidence: float = 0.0

        # Engine
        self._engine = HybridEngine(version=version)
        self._engine.set_verse_callback(
            lambda v:    self._verse_sig.emit(v))
        self._engine.set_status_callback(
            lambda s, d: self._status_sig.emit(s, d))
        self._engine.set_no_match_callback(
            lambda:      self._no_match_sig.emit())

        self._verse_sig.connect(self._on_verse,         Qt.QueuedConnection)
        self._status_sig.connect(self._on_engine_status, Qt.QueuedConnection)
        self._no_match_sig.connect(self._on_no_match,   Qt.QueuedConnection)

        self._asr:       Optional[ASRWorker] = None
        self._listening: bool = False
        self._display   = DisplayWindow()

        # Projector display theme (font/color/background/layout) — not
        # to be confused with the dark/light/system UI theme above.
        self._active_theme_name: str = self._settings.value(
            "active_display_theme", "Classic Gold")
        self._theme_designer: Optional[ThemeDesigner] = None
        active_theme = theme_store.load_theme(self._active_theme_name) or Theme()
        self._display.set_theme(active_theme)

        # AI Detections state — persists across theme rebuilds.
        self._detections: List[dict] = []
        self._new_count:  int = 0

        # Queue state — persists across theme rebuilds.
        self._queue: List[dict] = []

        # Search state.
        self._last_search_enter_time: float = 0.0
        self._last_search_top_result: Optional[dict] = None
        # True only while the unified Search box's full-pipeline fallback
        # (see _do_search_enter) is calling self._engine.process() — lets
        # _on_verse() tell "operator typed this deliberately" apart from a
        # live-speech guess, so explicit input always promotes regardless
        # of Auto/Manual mode.
        self._explicit_pipeline_call: bool = False

        # Preview / Live state. Go Live defaults OFF — nothing reaches the
        # actual congregation screen (including navigation echoes like
        # verse-jump, which aren't gated by Auto/Manual mode) until the
        # operator explicitly turns Go Live on or clicks "Push to Live".
        self._go_live: bool = False
        self._last_preview_verse: Optional[dict] = None

        self._build_ui()
        self._refresh_versions()
        self._refresh_devices()

    # ── Theme ─────────────────────────────────────────────

    def _apply_theme(self, mode: str, boot=False):
        global T
        self._theme_mode = mode
        self._settings.setValue("theme", mode)
        if mode == "system":
            pal = QApplication.palette()
            bg  = pal.color(QPalette.Window)
            T   = THEMES["dark"] if bg.lightness() < 128 else THEMES["light"]
        else:
            T = THEMES.get(mode, THEMES["dark"])
        if not boot:
            self._refresh_styles()

    def _refresh_styles(self):
        old = self.centralWidget()
        self._build_ui()
        old.deleteLater()

    # ── UI Build ──────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        root.setStyleSheet(global_qss())
        self.setCentralWidget(root)
        self.setStyleSheet(f"QMainWindow {{ background: {_t('bg')}; }}")

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {_t('border')}; }}")

        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_middle())
        splitter.addWidget(self._build_right())
        splitter.setSizes([520, 380, 420])

        outer.addWidget(splitter, stretch=1)
        outer.addWidget(self._build_statusbar())

        self._refresh_versions()
        self._refresh_devices()

    # ── Top bar ───────────────────────────────────────────

    def _build_topbar(self):
        bar = QWidget()
        bar.setFixedHeight(58)
        bar.setStyleSheet(
            f"background: {_t('panel')}; border-bottom: 1px solid {_t('border')};")

        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(10)

        logo  = QLabel("✦")
        logo.setStyleSheet(
            f"color: {_t('gold')}; font-size: 22px;"
            f"background: transparent; border: none;")
        title = QLabel("VERSE RETRIEVAL AND DISPLAY")
        title.setStyleSheet(
            f"color: {_t('text_p')}; font-size: 18px; font-weight: 700;"
            f"background: transparent; border: none;")
        sub = QLabel("Sermon Intelligence")
        sub.setStyleSheet(
            f"color: {_t('text_d')}; font-size: 11px; letter-spacing: 1px;"
            f"background: transparent; border: none;")

        self._badge = QLabel("● READY")
        self._badge.setStyleSheet(
            f"color: {_t('green')}; background: {_t('green_d')};"
            f"border-radius: 4px; padding: 4px 10px;"
            f"font-size: 11px; font-weight: 700; letter-spacing: 1px; border: none;")

        theme_row = QHBoxLayout()
        theme_row.setSpacing(4)
        self._theme_btns = {}
        for mode, icon in [("dark", "🌙"), ("light", "☀️"), ("system", "⚙")]:
            b = QPushButton(icon)
            b.setFixedSize(32, 32)
            b.setToolTip(f"{mode.capitalize()} mode")
            b.setStyleSheet(btn_qss(
                "active" if mode == self._theme_mode else "ghost"))
            b.clicked.connect(lambda _, m=mode: self._switch_theme(m))
            self._theme_btns[mode] = b
            theme_row.addWidget(b)

        self._mode_btn = QPushButton()
        self._mode_btn.setFixedHeight(32)
        self._mode_btn.setToolTip(
            "Auto: highest-confidence detections go live automatically "
            "(cooldown-limited). Manual: nothing auto-displays — every "
            "verse needs an explicit ▶ from Detections, Queue, or Search.")
        self._mode_btn.clicked.connect(self._toggle_display_mode)
        self._update_mode_btn()

        self._go_live_btn = QPushButton()
        self._go_live_btn.setFixedHeight(32)
        self._go_live_btn.setToolTip(
            "Go Live (L): ON = Preview changes show immediately. "
            "OFF = stage in Preview, push to Live manually.")
        self._go_live_btn.clicked.connect(self._toggle_go_live)
        self._update_go_live_btn()

        self._themes_btn = QPushButton("🎨 Themes…")
        self._themes_btn.setFixedHeight(32)
        self._themes_btn.setToolTip("Open the Theme Designer for the projector display")
        self._themes_btn.setStyleSheet(btn_qss("ghost"))
        self._themes_btn.clicked.connect(self._open_theme_designer)

        self._btn_display = QPushButton("Open Display")
        self._btn_display.setStyleSheet(btn_qss("primary"))
        self._btn_display.setToolTip(
            "Opens the projector window in windowed mode so it never "
            "covers your whole screen. Use Fullscreen once it's "
            "positioned on the actual projector.")
        self._btn_display.clicked.connect(self._toggle_display)

        self._btn_fullscreen = QPushButton("⛶ Fullscreen")
        self._btn_fullscreen.setStyleSheet(btn_qss("ghost"))
        self._btn_fullscreen.setToolTip(
            "Toggle the projector window between windowed and fullscreen "
            "(moves to a second monitor if one is connected).")
        self._btn_fullscreen.setEnabled(False)
        self._btn_fullscreen.clicked.connect(self._toggle_fullscreen_display)

        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addSpacing(4)
        lay.addWidget(sub)
        lay.addStretch()
        lay.addWidget(self._badge)
        lay.addSpacing(12)
        lay.addLayout(theme_row)
        lay.addSpacing(12)
        lay.addWidget(self._mode_btn)
        lay.addSpacing(8)
        lay.addWidget(self._go_live_btn)
        lay.addSpacing(8)
        lay.addWidget(self._themes_btn)
        lay.addSpacing(8)
        lay.addWidget(self._btn_display)
        lay.addSpacing(8)
        lay.addWidget(self._btn_fullscreen)

        return bar

    def _switch_theme(self, mode: str):
        self._apply_theme(mode)
        for m, b in self._theme_btns.items():
            b.setStyleSheet(btn_qss("active" if m == mode else "ghost"))

    # ── Left column ───────────────────────────────────────

    def _build_left(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 6, 14)
        lay.setSpacing(10)

        lay.addWidget(self._build_mic_card())
        # Detection Log (transcript-only "why" log) was removed — it
        # duplicated the AI Detections panel, which now also shows the
        # transcribed sentence that triggered each detection.
        lay.addWidget(self._build_transcript_card(), stretch=1)
        return w

    # ── Middle column: AI Detections + Queue ───────────────

    def _build_middle(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 6, 14)
        lay.setSpacing(0)

        split = QSplitter(Qt.Vertical)
        split.setHandleWidth(1)
        split.setStyleSheet(
            f"QSplitter::handle {{ background: {_t('border')}; }}")
        split.addWidget(self._build_detections())
        split.addWidget(self._build_queue())
        split.setSizes([420, 260])

        lay.addWidget(split)
        return w

    # ── AI Detections ───────────────────────────────────────

    def _build_detections(self):
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(lbl("AI Detections", 13, "text_p", True))
        header.addStretch()

        self._new_badge = QPushButton("")
        self._new_badge.setFixedHeight(24)
        self._new_badge.setStyleSheet(btn_qss("active"))
        self._new_badge.clicked.connect(self._jump_to_detections_top)
        self._new_badge.hide()
        header.addWidget(self._new_badge)

        clear = QPushButton("Clear")
        clear.setStyleSheet(btn_qss())
        clear.setFixedHeight(24)
        clear.clicked.connect(self._clear_detections)
        header.addWidget(clear)
        lay.addLayout(header)

        self._det_scroll = QScrollArea()
        self._det_scroll.setWidgetResizable(True)
        self._det_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._det_layout = QVBoxLayout(container)
        self._det_layout.setContentsMargins(0, 0, 6, 0)
        self._det_layout.setSpacing(8)
        self._det_layout.addStretch()

        self._det_scroll.setWidget(container)
        self._det_scroll.verticalScrollBar().valueChanged.connect(
            self._on_detections_scrolled)

        lay.addWidget(self._det_scroll, stretch=1)

        self._rerender_detections()
        return card

    def _make_detection_card(self, verse: dict) -> DetectionCard:
        card = DetectionCard(verse)
        card.setToolTip(self._method_reason(verse))
        card.send_preview.connect(self._on_detection_preview)
        card.add_queue.connect(self._add_to_queue)
        return card

    def _rerender_detections(self):
        for i, v in enumerate(self._detections):
            self._det_layout.insertWidget(i, self._make_detection_card(v))

    def _add_detection_card(self, verse: dict):
        if "match_type" not in verse:
            return
        if float(verse.get("confidence", 0)) < DETECTION_MIN_CONFIDENCE:
            return

        was_at_top = self._det_scroll.verticalScrollBar().value() <= 0

        self._detections.insert(0, verse)
        self._det_layout.insertWidget(0, self._make_detection_card(verse))

        while len(self._detections) > DETECTION_MAX_CARDS:
            self._detections.pop()
            old_item = self._det_layout.itemAt(self._det_layout.count() - 2)
            if old_item and old_item.widget():
                old_item.widget().deleteLater()

        if was_at_top:
            self._det_scroll.verticalScrollBar().setValue(0)
            self._new_count = 0
            self._new_badge.hide()
        else:
            self._new_count += 1
            self._new_badge.setText(f"{self._new_count} new ▲")
            self._new_badge.show()

    def _on_detections_scrolled(self, value):
        if value <= 0 and self._new_count:
            self._new_count = 0
            self._new_badge.hide()

    def _jump_to_detections_top(self):
        self._det_scroll.verticalScrollBar().setValue(0)
        self._new_count = 0
        self._new_badge.hide()

    def _clear_detections(self):
        self._detections.clear()
        self._new_count = 0
        self._new_badge.hide()
        while self._det_layout.count() > 1:
            item = self._det_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_detection_preview(self, verse: dict):
        self._engine.manual_display(
            str(verse.get("book", "")),
            int(verse.get("chapter", 0)),
            int(verse.get("verse", 0)),
        )

    # ── Queue ────────────────────────────────────────────────
    # Ordered, manual-advance only — nothing here auto-pops or
    # auto-displays. The operator decides when to send each item.

    def _build_queue(self):
        card = _card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(lbl("Queue", 13, "text_p", True))
        header.addStretch()
        self._queue_count_lbl = lbl("0 staged", 10, "text_d")
        header.addWidget(self._queue_count_lbl)
        clear = QPushButton("Clear")
        clear.setStyleSheet(btn_qss())
        clear.setFixedHeight(24)
        clear.clicked.connect(self._clear_queue)
        header.addWidget(clear)
        lay.addLayout(header)

        self._queue_scroll = QScrollArea()
        self._queue_scroll.setWidgetResizable(True)
        self._queue_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._queue_layout = QVBoxLayout(container)
        self._queue_layout.setContentsMargins(0, 0, 6, 0)
        self._queue_layout.setSpacing(8)
        self._queue_layout.addStretch()

        self._queue_scroll.setWidget(container)
        lay.addWidget(self._queue_scroll, stretch=1)

        self._rerender_queue()
        return card

    def _make_queue_item(self, verse: dict) -> QueueItem:
        item = QueueItem(verse)
        item.send_preview.connect(self._on_detection_preview)
        item.remove_item.connect(self._remove_queue_item)
        return item

    def _rerender_queue(self):
        for v in self._queue:
            self._queue_layout.insertWidget(
                self._queue_layout.count() - 1, self._make_queue_item(v))
        self._update_queue_count()

    def _add_to_queue(self, verse: dict):
        self._queue.append(verse)
        self._queue_layout.insertWidget(
            self._queue_layout.count() - 1, self._make_queue_item(verse))
        self._update_queue_count()

    def _remove_queue_item(self, item_widget):
        idx = self._queue_layout.indexOf(item_widget)
        if 0 <= idx < len(self._queue):
            self._queue.pop(idx)
        self._queue_layout.removeWidget(item_widget)
        item_widget.deleteLater()
        self._update_queue_count()

    def _clear_queue(self):
        self._queue.clear()
        while self._queue_layout.count() > 1:
            item = self._queue_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._update_queue_count()

    def _update_queue_count(self):
        n = len(self._queue)
        self._queue_count_lbl.setText(f"{n} staged")

    def _build_mic_card(self):
        card = _card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        row0 = QHBoxLayout()
        row0.addWidget(lbl("Microphone Input", 13, "text_p", True))
        row0.addStretch()
        self._loading_lbl = lbl("", 11, "amber")
        row0.addWidget(self._loading_lbl)
        lay.addLayout(row0)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(lbl("Device:", 11, "text_d"))
        self._dev_combo = QComboBox()
        self._dev_combo.setStyleSheet(combo_qss())
        self._dev_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        refresh = QPushButton("↺")
        refresh.setFixedSize(30, 28)
        refresh.setToolTip("Refresh device list")
        refresh.setStyleSheet(btn_qss())
        refresh.clicked.connect(self._refresh_devices)
        row1.addWidget(self._dev_combo, stretch=1)
        row1.addWidget(refresh)
        lay.addLayout(row1)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self._btn_listen = QPushButton("▶  Start Listening")
        self._btn_listen.setStyleSheet(btn_qss("primary"))
        self._btn_listen.clicked.connect(self._toggle_listen)
        self._btn_stop = QPushButton("■  Stop")
        self._btn_stop.setStyleSheet(btn_qss("danger"))
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_asr)
        row3.addWidget(self._btn_listen, stretch=2)
        row3.addWidget(self._btn_stop)
        lay.addLayout(row3)
        return card

    def _build_transcript_card(self):
        card = _card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)

        header = QHBoxLayout()
        header.addWidget(lbl("Live Transcript", 13, "text_p", True))
        header.addStretch()
        header.addWidget(lbl("(Whisper output — raw speech)", 10, "text_d"))
        clear = QPushButton("Clear")
        clear.setStyleSheet(btn_qss())
        clear.setFixedHeight(26)
        clear.clicked.connect(lambda: self._tx.clear())
        header.addWidget(clear)
        lay.addLayout(header)

        self._tx = QTextEdit()
        self._tx.setReadOnly(True)
        self._tx.setStyleSheet(textedit_qss())
        lay.addWidget(self._tx, stretch=1)
        return card

    # ── Right column ──────────────────────────────────────

    def _build_right(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(6, 14, 14, 14)
        lay.setSpacing(10)

        lay.addWidget(self._build_verse_card())
        lay.addWidget(self._build_live_card())
        lay.addWidget(self._build_nav_card())
        lay.addWidget(self._build_version_card())
        lay.addWidget(self._build_search_card())
        lay.addStretch()
        return w

    def _build_verse_card(self):
        card = _card(gold=True)
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(lbl("PREVIEW", 10, "gold_d", True))
        top.addStretch()
        self._preview_badge_lbl = QLabel("")
        self._preview_badge_lbl.setStyleSheet("background: transparent; border: none;")
        self._preview_badge_lbl.hide()
        top.addWidget(self._preview_badge_lbl)
        self._preview_conf_lbl = QLabel("")
        self._preview_conf_lbl.setStyleSheet("background: transparent; border: none;")
        self._preview_conf_lbl.hide()
        top.addWidget(self._preview_conf_lbl)
        self._ver_lbl = lbl("", 10, "gold_d")
        top.addWidget(self._ver_lbl)
        push = QPushButton("Push to Live →")
        push.setToolTip("Copy Preview to Live now (always works, regardless of Go Live)")
        push.setFixedHeight(22)
        push.setStyleSheet(btn_qss("nav"))
        push.clicked.connect(self._push_to_live)
        top.addWidget(push)
        lay.addLayout(top)

        self._ref_lbl = QLabel("—")
        self._ref_lbl.setStyleSheet(
            f"color: {_t('gold')}; font-size: 17px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        self._verse_lbl = QLabel("Listening for scripture…")
        self._verse_lbl.setWordWrap(True)
        self._verse_lbl.setStyleSheet(
            f"color: {_t('text_m')}; font-size: 13px; font-family: 'Georgia';"
            f"background: transparent; border: none;")

        lay.addWidget(self._ref_lbl)
        lay.addWidget(self._verse_lbl)
        return card

    def _build_live_card(self):
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: #000000; border: 1px solid {_t('border_hi')}; border-radius: {R}; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(6)

        lay.addWidget(lbl("LIVE OUTPUT", 10, "text_d", True))

        self._live_ref_lbl = QLabel("")
        self._live_ref_lbl.setStyleSheet(
            f"color: {_t('gold')}; font-size: 13px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        self._live_text_lbl = QLabel("")
        self._live_text_lbl.setWordWrap(True)
        self._live_text_lbl.setStyleSheet(
            "color: #F0EBE0; font-size: 12px; font-family: 'Georgia';"
            "background: transparent; border: none;")

        lay.addWidget(self._live_ref_lbl)
        lay.addWidget(self._live_text_lbl)
        return card

    def _build_nav_card(self):
        card = _card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        lay.addWidget(lbl("Navigation", 13, "text_p", True))

        row = QHBoxLayout()
        row.setSpacing(6)
        for label, cmd, tip in [
            ("◀ Prev",    "PREV",   "Previous verse"),
            ("↺ Repeat",  "REPEAT", "Repeat current verse"),
            ("Next ▶",    "NEXT",   "Next verse"),
            ("Last ↓",    "LAST",   "Last verse in chapter"),
        ]:
            b = QPushButton(label)
            b.setToolTip(tip)
            b.setStyleSheet(btn_qss("nav"))
            b.clicked.connect(lambda _, c=cmd: self._engine._navigate(c))
            row.addWidget(b)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self._jump_in = QLineEdit()
        self._jump_in.setPlaceholderText("Jump to verse #")
        self._jump_in.setStyleSheet(input_qss())
        self._jump_in.setFixedWidth(110)
        self._jump_in.returnPressed.connect(self._do_jump)
        go = QPushButton("Go")
        go.setStyleSheet(btn_qss())
        go.clicked.connect(self._do_jump)
        clear_btn = QPushButton("✕  Clear Screen")
        clear_btn.setStyleSheet(btn_qss("danger"))
        clear_btn.clicked.connect(lambda: self._engine._navigate("STOP"))
        row2.addWidget(self._jump_in)
        row2.addWidget(go)
        row2.addStretch()
        row2.addWidget(clear_btn)
        lay.addLayout(row2)
        return card

    def _build_version_card(self):
        card = _card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)
        lay.addWidget(lbl("Bible Version", 13, "text_p", True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self._ver_combo = QComboBox()
        self._ver_combo.setStyleSheet(combo_qss())
        switch = QPushButton("Switch Version")
        switch.setStyleSheet(btn_qss())
        switch.clicked.connect(self._switch_version)
        row.addWidget(self._ver_combo, stretch=1)
        row.addWidget(switch)
        lay.addLayout(row)
        return card

    def _build_search_card(self):
        card = _card()
        lay  = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(lbl("Search", 13, "text_p", True))
        header.addStretch()
        header.addWidget(lbl("Reference, topic, or command", 10, "text_d"))
        lay.addLayout(header)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._search_in = QLineEdit()
        self._search_in.setStyleSheet(input_qss())
        self._search_in.setPlaceholderText(
            "\"John 3:16\" · \"the prodigal son\" · \"next verse\" · \"read in BBE\"…")
        self._search_in.setToolTip(
            "One box for everything: tries a reference/verse-jump first, "
            "then a topic/paraphrase search, then falls back to the full "
            "engine (navigation commands, version switches, etc). Always "
            "promotes immediately — this is an explicit action, so it's "
            "never held back by Auto/Manual mode.")
        self._search_in.returnPressed.connect(self._do_search_enter)
        self._book_completer = QCompleter(sorted(set(_BOOK_ALIASES.values())))
        self._book_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._search_in.setCompleter(self._book_completer)
        search_btn = QPushButton("Search")
        search_btn.setStyleSheet(btn_qss())
        search_btn.clicked.connect(self._do_search_enter)
        add_btn = QPushButton("+")
        add_btn.setToolTip("Add top result to Queue")
        add_btn.setFixedWidth(30)
        add_btn.setStyleSheet(btn_qss("ghost"))
        add_btn.clicked.connect(self._add_search_result_to_queue)
        row.addWidget(self._search_in, stretch=1)
        row.addWidget(search_btn)
        row.addWidget(add_btn)
        lay.addLayout(row)

        self._search_results_scroll = QScrollArea()
        self._search_results_scroll.setWidgetResizable(True)
        self._search_results_scroll.setFixedHeight(140)
        self._search_results_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._search_results_layout = QVBoxLayout(container)
        self._search_results_layout.setContentsMargins(0, 0, 6, 0)
        self._search_results_layout.setSpacing(6)
        self._search_results_layout.addStretch()
        self._search_results_scroll.setWidget(container)
        lay.addWidget(self._search_results_scroll)

        return card

    def _build_statusbar(self):
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setStyleSheet(
            f"background: {_t('panel')}; border-top: 1px solid {_t('border')};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(24)
        self._state_lbl   = lbl("State: —", 11, "text_d")
        self._pos_lbl     = lbl("Position: —", 11, "text_d")
        self._match_lbl   = lbl("", 11, "text_d")
        lay.addWidget(self._state_lbl)
        lay.addWidget(self._pos_lbl)
        lay.addStretch()
        lay.addWidget(self._match_lbl)
        return bar

    # ── Helpers ───────────────────────────────────────────

    def _refresh_devices(self):
        if not hasattr(self, '_dev_combo'):
            return
        self._dev_combo.blockSignals(True)
        self._dev_combo.clear()
        self._dev_combo.addItem("Default (system)", userData=None)
        default_idx  = default_input_index()
        selected_row = 0
        for row, (idx, name) in enumerate(list_input_devices(), start=1):
            self._dev_combo.addItem(f"[{idx}] {name}", userData=idx)
            if idx == default_idx:
                selected_row = row
        self._dev_combo.setCurrentIndex(selected_row)
        self._dev_combo.blockSignals(False)

    def _refresh_versions(self):
        if not hasattr(self, '_ver_combo'):
            return
        self._ver_combo.clear()
        for v in db_list_versions():
            self._ver_combo.addItem(v)
        current = str(self._engine.session.active_version).upper()
        idx = self._ver_combo.findText(current, Qt.MatchFixedString)
        if idx >= 0:
            self._ver_combo.setCurrentIndex(idx)

    def _selected_device(self):
        return self._dev_combo.currentData() if hasattr(self, '_dev_combo') else None

    def _set_badge(self, text, fg_key, bg_key):
        self._badge.setText(f"● {text}")
        self._badge.setStyleSheet(
            f"color: {_t(fg_key)}; background: {_t(bg_key)};"
            f"border-radius: 4px; padding: 4px 10px;"
            f"font-size: 11px; font-weight: 700;"
            f"letter-spacing: 1px; border: none;")

    # ── ASR control ───────────────────────────────────────

    def _toggle_listen(self):
        if self._listening:
            self._stop_asr()
        else:
            self._start_asr()

    def _start_asr(self):
        self._asr = ASRWorker(
            device_index=self._selected_device(),
            engine=self._engine,
            parent=self)
        self._asr.transcript_signal.connect(self._on_transcript)
        self._asr.status_signal.connect(self._on_asr_status)
        self._asr.start()
        self._listening = True
        self._dev_combo.setEnabled(False)
        self._btn_listen.setEnabled(False)
        self._btn_listen.setText("Loading model…")
        self._btn_listen.setStyleSheet(btn_qss())
        self._btn_stop.setEnabled(True)
        self._loading_lbl.setText("loading…")
        self._set_badge("LOADING", "amber", "amber_d")

    def _stop_asr(self):
        if self._asr:
            self._asr.stop_worker()
            self._asr = None
        self._listening = False
        self._dev_combo.setEnabled(True)
        self._btn_listen.setEnabled(True)
        self._btn_listen.setText("▶  Start Listening")
        self._btn_listen.setStyleSheet(btn_qss("primary"))
        self._btn_stop.setEnabled(False)
        self._loading_lbl.setText("")
        self._set_badge("READY", "green", "green_d")

    def _on_asr_status(self, status):
        if status == "listening":
            self._loading_lbl.setText("")
            self._btn_listen.setEnabled(True)
            self._btn_listen.setText("● Listening")
            self._btn_listen.setStyleSheet(btn_qss())
            self._set_badge("LISTENING", "green", "green_d")
        elif status == "loading":
            self._set_badge("LOADING", "amber", "amber_d")
        elif status == "stopped":
            self._set_badge("READY", "green", "green_d")
        elif status.startswith("error:"):
            self._loading_lbl.setText(f"Err: {status[6:][:50]}")
            self._set_badge("ERROR", "red", "red_d")
            self._listening = False
            self._dev_combo.setEnabled(True)
            self._btn_listen.setEnabled(True)
            self._btn_listen.setText("▶  Start Listening")
            self._btn_listen.setStyleSheet(btn_qss("primary"))
            self._btn_stop.setEnabled(False)

    # ── Transcript → UI display ───────────────────────────
    # Engine is called directly from ASRWorker._on_text callback.
    # This handler only updates the transcript log in the UI.

    def _on_transcript(self, text: str):
        text = str(text).strip()
        if not text:
            return
        self._tx.append(text)
        sb = self._tx.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ── Engine callbacks ──────────────────────────────────

    def _on_verse(self, verse):
        if verse:
            book      = verse.get("book",    "")
            chapter   = verse.get("chapter", "")
            verse_no  = verse.get("verse",   "")
            version   = verse.get("version", "")

            # The AI Detections panel and the engine-position readout
            # always reflect every engine decision, regardless of
            # Auto/Manual mode — only Preview/Live promotion is gated.
            self._pos_lbl.setText(f"Position: {book} {chapter}:{verse_no}")
            self._match_lbl.setText(f"✓ {version}")

            self._add_detection_card(verse)

            # Only a "semantic" match_type is a probabilistic guess that
            # needs operator review — direct references are unambiguous
            # (the preacher said the exact book/chapter/verse), and
            # navigation/verse_jump/version_switch/manual are deterministic
            # commands (hybrid.py's _tag_command, confidence=1.0 fixed).
            # All of those always override whatever is currently displayed,
            # regardless of Auto/Manual mode — only semantic guesses stay
            # gated behind operator review.
            is_semantic_guess = verse.get("match_type") == "semantic"
            if is_semantic_guess and not self._explicit_pipeline_call:
                if self._display_mode == "manual":
                    # Manual: stays in the Detections panel only, until
                    # the operator explicitly sends it (▶) or queues it (+).
                    return
                if self._display_mode == "auto" and not self._auto_should_push(verse):
                    return

            self._promote_to_preview(verse)
        else:
            self._pos_lbl.setText("Position: —")
            self._match_lbl.setText("")
            self._promote_to_preview(None)

    def _promote_to_preview(self, verse: Optional[dict]):
        """Every verse lands in Preview first. Live only follows if Go
        Live is on — otherwise it stays staged until the operator
        explicitly pushes it."""
        if verse:
            book = verse.get("book", "")
            chapter = verse.get("chapter", "")
            verse_no = verse.get("verse", "")
            version = verse.get("version", "")
            text = verse.get("text", "")

            self._ref_lbl.setText(f"{book}  {chapter}:{verse_no}")
            self._ref_lbl.setStyleSheet(
                f"color: {_t('gold')}; font-size: 17px; font-weight: 700;"
                f"font-family: 'Georgia'; background: transparent; border: none;")
            self._verse_lbl.setText(f'"{text}"')
            self._verse_lbl.setStyleSheet(
                f"color: {_t('text_p')}; font-size: 13px; font-family: 'Georgia';"
                f"background: transparent; border: none;")
            self._ver_lbl.setText(str(version).upper())

            badge, conf_lbl = _make_badge_pair(verse)
            if badge:
                self._preview_badge_lbl.setText(badge.text())
                self._preview_badge_lbl.setStyleSheet(badge.styleSheet())
                self._preview_badge_lbl.show()
                self._preview_conf_lbl.setText(conf_lbl.text())
                self._preview_conf_lbl.setToolTip(conf_lbl.toolTip())
                self._preview_conf_lbl.setStyleSheet(conf_lbl.styleSheet())
                self._preview_conf_lbl.show()
            else:
                self._preview_badge_lbl.hide()
                self._preview_conf_lbl.hide()

            self._last_preview_verse = verse
            if self._go_live:
                self._set_live(verse)
        else:
            self._ref_lbl.setText("—")
            self._ref_lbl.setStyleSheet(
                f"color: {_t('gold')}; font-size: 17px; font-weight: 700;"
                f"font-family: 'Georgia'; background: transparent; border: none;")
            self._verse_lbl.setText("Listening for scripture…")
            self._verse_lbl.setStyleSheet(
                f"color: {_t('text_m')}; font-size: 13px; font-family: 'Georgia';"
                f"background: transparent; border: none;")
            self._ver_lbl.setText("")
            self._preview_badge_lbl.hide()
            self._preview_conf_lbl.hide()
            # An explicit clear (Clear Screen / STOP) always clears Live
            # too, regardless of Go Live — it's a deliberate operator
            # reset, not a new detection that should stay staged.
            self._last_preview_verse = None
            self._set_live(None)

    def _auto_should_push(self, verse: dict) -> bool:
        """Auto mode: highest-confidence detection goes live after a
        cooldown to prevent flicker. Direct references qualify faster
        than semantic matches, which also need a stricter minimum
        confidence. A clearly higher-confidence match can override an
        active cooldown instead of waiting it out."""
        match_type = verse.get("match_type")
        confidence = float(verse.get("confidence", 0))

        if match_type == "semantic" and confidence < AUTO_SEMANTIC_MIN_CONFIDENCE:
            return False

        cooldown = AUTO_COOLDOWN_DIRECT if match_type == "direct" else AUTO_COOLDOWN_SEMANTIC
        now = time.time()
        elapsed = now - self._last_auto_push_time

        eligible = elapsed >= cooldown or confidence > self._last_auto_confidence
        if eligible:
            self._last_auto_push_time = now
            self._last_auto_confidence = confidence
        return eligible

    def _method_reason(self, verse: dict) -> str:
        match_type = str(verse.get("match_type", ""))
        source = str(verse.get("source_text", ""))

        if match_type == "navigation":
            return f"Navigation command — {source}" if source else "Navigation command"
        if match_type == "verse_jump":
            return f"Verse jump — {source}" if source else "Verse jump"
        if match_type == "version_switch":
            return f"Version switch — {source}" if source else "Version switch"
        if match_type == "manual":
            return f"Manual override — {source}" if source else "Manual override"

        method  = str(verse.get("method", ""))
        sim     = verse.get("similarity",    None)
        lex     = verse.get("lexical_score", None)
        final   = verse.get("final_score",   None)
        matched = verse.get("matched_text",  "")

        if method == "phrase_map":
            return f'Exact phrase → "{matched}"'
        if method == "event_map":
            return f'Event match → "{matched}"'
        if method == "lexical":
            score = f" · score {final:.2f}" if final is not None else ""
            return f"Direct quote{score}"
        if method in ("semantic_lexical", "semantic"):
            parts = []
            if sim   is not None: parts.append(f"sim={float(sim):.2f}")
            if lex   is not None: parts.append(f"lex={float(lex):.2f}")
            if final is not None: parts.append(f"score={float(final):.2f}")
            detail = " · ".join(parts)
            return f"Semantic{(' · ' + detail) if detail else ''}"
        if method:
            score = f" · score={float(final):.2f}" if final is not None else ""
            return f"{method.replace('_', ' ').title()}{score}"
        score = f" · score={float(final):.2f}" if final is not None else ""
        return f"Semantic{score}"

    def _on_engine_status(self, state, desc):
        self._state_lbl.setText(f"State: {desc}")

    def _on_no_match(self):
        self._match_lbl.setText("No match")

    # ── Manual controls ───────────────────────────────────

    def _do_jump(self):
        text = self._jump_in.text().strip()
        if text.isdigit():
            self._engine.process(f"verse {text}")
            self._jump_in.clear()

    def _switch_version(self):
        version = self._ver_combo.currentText().strip().upper()
        if version:
            self._engine._switch_version(version)

    # ── Unified search / manual override ───────────────────

    def _book_mode_lookup(self, query: str) -> Optional[dict]:
        version = str(self._engine.session.active_version)

        if query.isdigit() and self._engine._has_position():
            book = str(self._engine.session.current_book)
            chapter = int(self._engine.session.current_chapter)
            return db_get_verse(version, book, chapter, int(query))

        # Same language gating the voice pipeline uses (hybrid.py's
        # _allowed_languages) — typing a Twi book name into this box
        # while on an English version should behave the same as saying
        # it out loud would: it shouldn't resolve at all, not silently
        # display English text for a Twi-named reference.
        ref = extract_reference(
            query, allowed_languages=self._engine._allowed_languages()
        )
        if not ref:
            return None
        book, chapter, verse = ref
        return db_get_verse(version, book, chapter, verse)

    def _do_search_enter(self):
        """Unified search: try it as a reference/digit-jump first; if that
        finds nothing, fall back to topic/paraphrase semantic search."""
        query = self._search_in.text().strip()
        if not query:
            return

        now = time.time()
        is_double = (now - self._last_search_enter_time) < 0.6
        self._last_search_enter_time = now

        self._clear_search_results()

        result = self._book_mode_lookup(query)
        if result:
            self._last_search_top_result = result
            self._search_results_layout.insertWidget(
                self._search_results_layout.count() - 1,
                self._make_search_result_item(result))
            self._promote_search_result(result, force_go_live=is_double)
            return

        candidates = self._engine.semantic.search_top_k(query, k=8)
        # search_top_k() returns final_score, not the match_type/
        # confidence keys hybrid.py's pipeline adds — normalise here,
        # in the UI layer only, so badges render consistently.
        for c in candidates:
            c.setdefault("match_type", "semantic")
            c.setdefault("confidence", c.get("final_score", 0))
        self._last_search_top_result = candidates[0] if candidates else None
        for c in candidates:
            self._search_results_layout.insertWidget(
                self._search_results_layout.count() - 1,
                self._make_search_result_item(c))
        if candidates:
            self._promote_search_result(candidates[0], force_go_live=is_double)
            return

        # Neither a reference/digit-jump nor a semantic match — this one box
        # also doubles as the manual/test input, so fall back to running the
        # query through the full engine pipeline (navigation commands like
        # "next verse" or "stop display", version switches like "read in
        # BBE", direct references it phrased oddly, etc). Since typing into
        # this box is always an explicit operator action, whatever it
        # resolves to should promote immediately — never held back by
        # Auto/Manual mode the way a live-speech guess would be. process()
        # itself always resolves synchronously (see hybrid.py), so this
        # returns with the result already applied.
        self._tx.append(f'[typed] {query}')
        self._explicit_pipeline_call = True
        try:
            self._engine.process(query)
        finally:
            self._explicit_pipeline_call = False

    def _make_search_result_item(self, verse: dict) -> SearchResultItem:
        item = SearchResultItem(verse)
        item.send_preview.connect(
            lambda v: self._promote_search_result(v, force_go_live=False))
        item.add_queue.connect(self._add_to_queue)
        return item

    def _clear_search_results(self):
        while self._search_results_layout.count() > 1:
            item = self._search_results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_search_result_to_queue(self):
        if self._last_search_top_result:
            self._add_to_queue(self._last_search_top_result)

    def _promote_search_result(self, verse: dict, force_go_live: bool):
        # A search result is already an explicit operator action, so it
        # always promotes to Preview regardless of Auto/Manual mode.
        if force_go_live and not self._go_live:
            self._go_live = True
            self._update_go_live_btn()
        # Keep the engine's session position in sync so a subsequent
        # digit-only Book-mode search ("verse jump within a chapter")
        # and the Nav card's jump-to-verse both resolve against
        # whatever was last shown via search.
        book, chapter, vnum = verse.get("book"), verse.get("chapter"), verse.get("verse")
        if book and chapter and vnum:
            self._engine.session.update_position(str(book), int(chapter), int(vnum))
        self._promote_to_preview(verse)

    def _toggle_display(self):
        if self._display.isVisible():
            self._display.hide()
            self._btn_display.setText("Open Display")
            self._btn_fullscreen.setEnabled(False)
        else:
            self._display.launch()
            self._btn_display.setText("Hide Display")
            self._btn_fullscreen.setEnabled(True)
        self._update_fullscreen_btn()

    def _toggle_fullscreen_display(self):
        if not self._display.isVisible():
            return
        self._display.toggle_fullscreen()
        self._update_fullscreen_btn()

    def _update_fullscreen_btn(self):
        if self._display.is_fullscreen:
            self._btn_fullscreen.setText("⛶ Exit Fullscreen")
        else:
            self._btn_fullscreen.setText("⛶ Fullscreen")

    # ── Theme Designer ──────────────────────────────────────

    def _open_theme_designer(self):
        if self._theme_designer is None:
            self._theme_designer = ThemeDesigner(active_theme_name=self._active_theme_name)
            self._theme_designer.theme_activated.connect(self._on_theme_activated)
        self._theme_designer.show()
        self._theme_designer.raise_()
        self._theme_designer.activateWindow()

    def _on_theme_activated(self, theme: Theme):
        self._display.set_theme(theme)
        self._active_theme_name = theme.name
        self._settings.setValue("active_display_theme", theme.name)

    # ── Auto / Manual display mode ─────────────────────────

    def _update_mode_btn(self):
        if self._display_mode == "auto":
            self._mode_btn.setText("⚡ Mode: Auto")
            self._mode_btn.setStyleSheet(btn_qss("nav"))
        else:
            self._mode_btn.setText("🖐 Mode: Manual")
            self._mode_btn.setStyleSheet(btn_qss("ghost"))

    def _toggle_display_mode(self):
        self._display_mode = "manual" if self._display_mode == "auto" else "auto"
        self._settings.setValue("display_mode", self._display_mode)
        self._update_mode_btn()

    # ── Preview / Live ─────────────────────────────────────

    def _update_go_live_btn(self):
        if self._go_live:
            self._go_live_btn.setText("● Go Live: ON")
            self._go_live_btn.setStyleSheet(btn_qss("primary"))
        else:
            self._go_live_btn.setText("○ Go Live: OFF")
            self._go_live_btn.setStyleSheet(btn_qss("ghost"))

    def _toggle_go_live(self):
        self._go_live = not self._go_live
        self._update_go_live_btn()
        if self._go_live and self._last_preview_verse:
            self._set_live(self._last_preview_verse)

    def _set_live(self, verse: Optional[dict]):
        """Push a verse (or None to clear) to the actual projector."""
        # The single point a verse actually becomes live — tell the
        # engine, so navigation/verse-jump/version-switch anchor to what
        # the congregation is actually looking at, not whatever's still
        # sitting in Preview or the AI Detections queue. See hybrid.py's
        # confirm_live().
        self._engine.confirm_live(verse)
        if verse:
            book = verse.get("book", "")
            chapter = verse.get("chapter", "")
            vnum = verse.get("verse", "")
            self._live_ref_lbl.setText(f"{book}  {chapter}:{vnum}")
            self._live_text_lbl.setText(str(verse.get("text", "")))
            if self._display.isVisible():
                self._display.show_verse(verse)
        else:
            self._live_ref_lbl.setText("")
            self._live_text_lbl.setText("")
            if self._display.isVisible():
                self._display.clear()

    def _push_to_live(self):
        """Explicit Preview → Live push, for when Go Live is off."""
        if self._last_preview_verse:
            self._set_live(self._last_preview_verse)

    def keyPressEvent(self, event):
        focus = QApplication.focusWidget()
        is_text_input = isinstance(focus, (QLineEdit, QTextEdit))
        if not is_text_input and event.key() == Qt.Key_L:
            self._toggle_go_live()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._stop_asr()
        self._display.close()
        if self._theme_designer is not None:
            self._theme_designer.close()
        super().closeEvent(event)


# ════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Bible AI — Sermon Verse Display")
    parser.add_argument("--version",      type=str,  default="KJV",
                        help="Starting Bible version (default: KJV)")
    parser.add_argument("--list-devices", action="store_true",
                        help="Print all audio devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = OperatorWindow(version=args.version.upper())
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
