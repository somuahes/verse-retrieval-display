"""
app/ui/display_window.py
=========================
Projection display — what the congregation sees.

- Renders from an active Theme (app/ui/theme_model.py) — font, color,
  alignment, solid/image background, layout
- Instant on the very first verse after opening/clearing (nothing to
  cross-fade from); smooth fade-in / fade-out on every verse change
  after that
- Clears to blank when STOP is received
- Resizes font automatically to fit any screen resolution
- Opens windowed (resizable, movable, normal frame) by default — the
  operator positions it themselves and calls enter_fullscreen() when
  ready to actually project. Only in fullscreen mode does it move to a
  second monitor and drop its window frame.
"""

from PyQt5.QtWidgets import QWidget, QLabel, QVBoxLayout, QApplication
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt5.QtGui import QFont, QFontMetrics, QPainter, QPixmap

from app.ui.theme_model import Theme

import os
import sys


class FadeLabel(QLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._opacity = 1.0

    def get_opacity(self):
        return self._opacity

    def set_opacity(self, value):
        self._opacity = value
        colour = self.property("baseColour") or "#FFFFFF"
        r, g, b = (int(colour[i:i+2], 16) for i in (1, 3, 5))
        alpha = int(value * 255)
        self.setStyleSheet(
            f"color: rgba({r},{g},{b},{alpha}); background: transparent;"
        )

    opacity = pyqtProperty(float, get_opacity, set_opacity)


class DisplayWindow(QWidget):
    FADE_MS = 400
    DEFAULT_SIZE = (960, 540)

    def __init__(self):
        super().__init__()
        self._current_verse = None
        self._theme = Theme()
        self._bg_pixmap = None
        self._is_fullscreen = False
        self._ever_launched = False
        self._setup_window()
        self._setup_layout()
        # NOTE: do NOT call show()/showFullScreen() here.
        # Call launch() from the operator UI when ready.

    def _setup_window(self):
        self.setWindowTitle("Bible Display")
        self._apply_window_flags()
        self._apply_background()

    def _apply_window_flags(self):
        flags = Qt.Window
        if self._is_fullscreen:
            flags |= Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    def launch(self):
        """
        Call this once when ready to show the projector window. Opens as a
        normal, resizable, movable window — NOT fullscreen — so it never
        unexpectedly covers the operator's only monitor. Call
        enter_fullscreen() separately once it's positioned on the actual
        projector screen.
        """
        if not self._ever_launched:
            # Force the intended default only on the very first open of a
            # session — a never-shown top-level widget's width()/height()
            # aren't reliably near-zero before show() (confirmed live: a
            # real "windows"-platform run measured 203x318 here, not the
            # ~0 this used to assume), so a size check can't tell "never
            # positioned" apart from "genuinely small." A one-time flag
            # can. Once launched, later calls respect whatever size the
            # operator left it at (including a manual resize).
            self.resize(*self.DEFAULT_SIZE)
            self._ever_launched = True

        # Windowed mode has no "always on top" hint, so without an explicit
        # raise it can silently open *behind* the operator window on a
        # single monitor — it's technically visible (isVisible() is True,
        # so verses keep being pushed to it) but the operator never sees it
        # and it looks like nothing is showing. If a second screen exists,
        # default to centering there so it's out of the operator's way but
        # still plainly visible.
        screens = QApplication.screens()
        if len(screens) > 1:
            geom = screens[1].geometry()
            x = geom.x() + (geom.width() - self.width()) // 2
            y = geom.y() + (geom.height() - self.height()) // 2
            self.move(x, y)

        self.show()
        self.raise_()
        self.activateWindow()

    def enter_fullscreen(self):
        """Move to the second screen if available, then go fullscreen."""
        screens = QApplication.screens()
        geom = screens[1].geometry() if len(screens) > 1 else screens[0].geometry()
        self._is_fullscreen = True
        self._apply_window_flags()
        self.setGeometry(geom)
        self.showFullScreen()

    def exit_fullscreen(self):
        """Return to a normal, windowed, resizable display."""
        self._is_fullscreen = False
        self._apply_window_flags()
        self.resize(*self.DEFAULT_SIZE)
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_fullscreen(self):
        if self._is_fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    # ── Theme ─────────────────────────────────────────────

    def set_theme(self, theme: Theme):
        self._theme = theme
        self._apply_background()
        self._rebuild_layout()
        if self._current_verse:
            self.show_verse(self._current_verse)

    def _apply_background(self):
        t = self._theme
        if t.background_type == "image" and t.background_image_path and os.path.exists(t.background_image_path):
            self._bg_pixmap = QPixmap(t.background_image_path)
            self.setStyleSheet("background-color: #000000;")
        else:
            self._bg_pixmap = None
            self.setStyleSheet(f"background-color: {t.background_color};")
        self.update()

    def paintEvent(self, event):
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            painter = QPainter(self)
            rect = self.rect()
            fit = self._theme.background_fit
            pm = self._bg_pixmap
            if fit == "stretch":
                painter.drawPixmap(rect, pm)
            elif fit == "contain":
                scaled = pm.scaled(rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (rect.width() - scaled.width()) // 2
                y = (rect.height() - scaled.height()) // 2
                painter.fillRect(rect, Qt.black)
                painter.drawPixmap(x, y, scaled)
            else:  # cover
                scaled = pm.scaled(rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                x = (rect.width() - scaled.width()) // 2
                y = (rect.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
            painter.end()
        super().paintEvent(event)

    def _setup_layout(self):
        self._layout = QVBoxLayout(self)
        self._verse_label = FadeLabel("", self)
        self._verse_label.setWordWrap(True)
        self._ref_label = FadeLabel("", self)
        self._rebuild_layout()

    def _rebuild_layout(self):
        while self._layout.count():
            self._layout.takeAt(0)

        t = self._theme
        self._layout.setContentsMargins(80, t.padding, 80, t.padding)
        self._layout.setSpacing(0)

        align_map = {"left": Qt.AlignLeft, "center": Qt.AlignCenter, "right": Qt.AlignRight}
        h_align = align_map.get(t.text_align, Qt.AlignCenter)

        self._verse_label.setAlignment(h_align | Qt.AlignVCenter)
        self._verse_label.setProperty("baseColour", t.text_color)
        self._ref_label.setAlignment(h_align | Qt.AlignVCenter)
        self._ref_label.setProperty("baseColour", t.ref_color)

        self._layout.addStretch(2)
        if t.reference_position == "above":
            self._layout.addWidget(self._ref_label)
            self._layout.addSpacing(t.element_spacing)
            self._layout.addWidget(self._verse_label)
        else:
            self._layout.addWidget(self._verse_label)
            self._layout.addSpacing(t.element_spacing)
            self._layout.addWidget(self._ref_label)
        self._layout.addStretch(3)

        self._verse_label.setFont(self._body_font(t.font_size))
        self._ref_label.setFont(self._ref_font(t.ref_font_size))

    def _body_font(self, size: int) -> QFont:
        f = QFont(self._theme.font_family, size)
        f.setWeight(QFont.Bold if self._theme.font_weight == "bold" else QFont.Normal)
        return f

    def _ref_font(self, size: int) -> QFont:
        f = QFont(self._theme.ref_font_family, size)
        f.setItalic(True)
        return f

    def _fit_font_size(self, text: str) -> int:
        max_w = int(self.width() * (self._theme.content_area_pct / 100.0))
        max_h = int(self.height() * 0.60)
        size = self._theme.font_size
        while size > 20:
            fm = QFontMetrics(self._body_font(size))
            rect = fm.boundingRect(
                0, 0, max_w, 0,
                Qt.AlignCenter | Qt.TextWordWrap,
                text,
            )
            if rect.height() <= max_h:
                break
            size -= 2
        return size

    def show_verse(self, verse: dict):
        if not verse:
            self.clear()
            return

        # Nothing is on screen yet right after the display opens (or
        # after a Clear) -- fading OUT already-invisible text before
        # fading the new text IN just adds a wasted FADE_MS delay with
        # nothing visible to show for it. Project the first verse
        # instantly; only verse-to-verse changes get the fade.
        first_appearance = self._current_verse is None

        self._current_verse = verse
        text = str(verse.get("text",    ""))
        book = str(verse.get("book",    ""))
        chapter = verse.get("chapter", "")
        vnum = verse.get("verse",   "")
        version = str(verse.get("version", "")).upper()
        reference = f"{book}  {chapter}:{vnum}  —  {version}"

        font_size = self._fit_font_size(text)
        self._verse_label.setFont(self._body_font(font_size))
        if first_appearance:
            self._set_content(text, reference, animate_in=False)
        else:
            self._fade_out(lambda: self._set_content(text, reference))

    def clear(self):
        self._current_verse = None
        self._fade_out(lambda: self._set_content("", ""))

    def _set_content(self, text: str, reference: str, animate_in: bool = True):
        self._verse_label.setText(text)
        self._ref_label.setText(reference)
        if animate_in:
            self._fade_in()
        else:
            self._verse_label.set_opacity(1.0)
            self._ref_label.set_opacity(1.0)

    def _fade_out(self, then=None):
        self._animate(self._verse_label, 1.0, 0.0)
        anim = self._animate(self._ref_label, 1.0, 0.0)
        if then:
            anim.finished.connect(then)

    def _fade_in(self):
        self._animate(self._verse_label, 0.0, 1.0)
        self._animate(self._ref_label,   0.0, 1.0)

    def _animate(self, widget, start: float, end: float):
        # A rapid re-trigger (two show_verse()/clear() calls inside one
        # FADE_MS window — e.g. two AI detections landing close
        # together) used to just overwrite widget._anim with the new
        # animation, dropping the only Python reference to the still-
        # running previous one. With no Qt parent, that previous
        # QPropertyAnimation had nothing else keeping it alive and could
        # be garbage-collected mid-flight — its `finished` signal (which
        # _fade_out's caller relies on to run _set_content) then never
        # fires. Explicitly stopping it first makes the interruption
        # deterministic instead of GC-timing-dependent, and giving the
        # new animation `self` as its Qt parent ties its C++ lifetime to
        # this long-lived window instead of the transient widget
        # attribute, so it can't be collected out from under Qt while
        # still running.
        old = getattr(widget, "_anim", None)
        if old is not None:
            old.stop()

        anim = QPropertyAnimation(widget, b"opacity", self)
        anim.setDuration(self.FADE_MS)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.start()
        widget._anim = anim
        return anim

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clear()
        super().keyPressEvent(event)
