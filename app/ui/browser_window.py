"""
app/ui/browser_window.py
==========================
Browse — click-to-navigate Book -> Chapter -> Verse, an alternative to
typing into Search (see BibleShow's Reference/Browser panels for the
interaction this mirrors). An embedded panel in the Operator Panel's own
window (toggled in/out of the main splitter by the topbar's Browse
button), not a separate popup window — closer to how BibleShow keeps
every panel in one place. Talks to the backend only through hybrid.py's
db_list_versions/db_list_books/db_chapter_count/db_get_chapter — no SQL
or matching logic lives in this file, keeping the frontend/backend line
in the same place the rest of the UI already draws it.
"""

from typing import Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem, QComboBox, QScrollArea,
    QSplitter, QFrame,
)

from app.retrieval.hybrid import (
    db_list_versions, db_list_books, db_chapter_count, db_get_chapter,
)
from app.ui import style_kit as sk


CHAPTER_COLUMNS = 6


class BrowserVerseRow(QFrame):
    """One verse in the chapter preview — mirrors the app's other
    result-row widgets (AI Detections / Search)."""

    send_preview = pyqtSignal(dict)
    add_queue = pyqtSignal(dict)

    def __init__(self, verse: dict, parent=None):
        super().__init__(parent)
        self._verse = verse
        self.setStyleSheet(sk.card_qss())
        self._build(verse)

    def _build(self, verse: dict):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        info = QVBoxLayout()
        info.setSpacing(2)
        num = QLabel(str(verse.get("verse", "")))
        num.setStyleSheet(
            f"color: {sk.c('gold')}; font-size: 12px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        info.addWidget(num)
        snip = QLabel(str(verse.get("text", "")))
        snip.setWordWrap(True)
        snip.setStyleSheet(
            f"color: {sk.c('text_p')}; font-size: 12px; font-family: 'Georgia';"
            f"background: transparent; border: none;")
        info.addWidget(snip)
        lay.addLayout(info, stretch=1)

        send_btn = QPushButton("▶")
        send_btn.setToolTip("Send to Preview")
        send_btn.setFixedSize(26, 22)
        send_btn.setStyleSheet(sk.btn_qss("nav"))
        send_btn.clicked.connect(lambda: self.send_preview.emit(self._verse))
        lay.addWidget(send_btn)

        add_btn = QPushButton("+")
        add_btn.setToolTip("Add to Queue")
        add_btn.setFixedSize(26, 22)
        add_btn.setStyleSheet(sk.btn_qss("ghost"))
        add_btn.clicked.connect(lambda: self.add_queue.emit(self._verse))
        lay.addWidget(add_btn)


class BrowsePanel(QWidget):
    """Embeddable Books | Chapters | Verses panel — the Operator Panel
    inserts/removes this from its main splitter; it never opens as its
    own top-level window."""

    send_preview = pyqtSignal(dict)
    add_queue = pyqtSignal(dict)
    closed = pyqtSignal()

    PANEL_WIDTH = 650

    def __init__(self, initial_version: str = "KJV", parent=None):
        super().__init__(parent)
        self.setStyleSheet(sk.window_qss())
        self.setMinimumWidth(self.PANEL_WIDTH)

        self._version = initial_version
        self._book: Optional[str] = None
        self._chapter: Optional[int] = None

        self._build_ui()
        self._refresh_versions()
        self._refresh_books()

    # ── UI build ────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        top = QHBoxLayout()
        eyebrow = QLabel("BROWSE SCRIPTURE")
        eyebrow.setStyleSheet(sk.eyebrow_qss())
        top.addWidget(eyebrow)
        top.addStretch()
        ver_lbl = QLabel("Version:")
        ver_lbl.setStyleSheet(f"color: {sk.c('text_m')}; background: transparent; border: none;")
        top.addWidget(ver_lbl)
        self._ver_combo = QComboBox()
        self._ver_combo.setStyleSheet(sk.combo_qss())
        self._ver_combo.currentTextChanged.connect(self._on_version_changed)
        top.addWidget(self._ver_combo)
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close Browse")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(sk.btn_qss("ghost"))
        close_btn.clicked.connect(self.closed.emit)
        top.addWidget(close_btn)
        outer.addLayout(top)

        self._breadcrumb = QLabel("Choose a book to begin")
        self._breadcrumb.setStyleSheet(
            f"color: {sk.c('gold')}; font-size: 16px; font-weight: 700;"
            f"font-family: 'Georgia'; background: transparent; border: none;")
        outer.addWidget(self._breadcrumb)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        split.setStyleSheet(f"QSplitter::handle {{ background: {sk.c('border')}; }}")

        split.addWidget(self._build_books_panel())
        split.addWidget(self._build_chapters_panel())
        split.addWidget(self._build_verses_panel())
        split.setSizes([180, 200, 270])

        outer.addWidget(split, stretch=1)

    def _panel_frame(self, title: str) -> Tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setStyleSheet(sk.card_qss())
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        head = QLabel(title)
        head.setStyleSheet(sk.eyebrow_qss())
        lay.addWidget(head)
        return card, lay

    def _build_books_panel(self) -> QFrame:
        card, lay = self._panel_frame("BOOKS")
        self._books_list = QListWidget()
        self._books_list.itemClicked.connect(self._on_book_selected)
        lay.addWidget(self._books_list, stretch=1)
        return card

    def _build_chapters_panel(self) -> QFrame:
        card, lay = self._panel_frame("CHAPTERS")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._chapters_grid = QGridLayout(container)
        self._chapters_grid.setSpacing(6)
        self._chapters_hint = QLabel("Pick a book first")
        self._chapters_hint.setStyleSheet(
            f"color: {sk.c('text_d')}; font-size: 12px; background: transparent; border: none;")
        self._chapters_grid.addWidget(self._chapters_hint, 0, 0)
        scroll.setWidget(container)
        lay.addWidget(scroll, stretch=1)
        return card

    def _build_verses_panel(self) -> QFrame:
        card, lay = self._panel_frame("VERSES")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        self._verses_layout = QVBoxLayout(container)
        self._verses_layout.setSpacing(6)
        self._verses_hint = QLabel("Pick a chapter to preview its verses")
        self._verses_hint.setStyleSheet(
            f"color: {sk.c('text_d')}; font-size: 12px; background: transparent; border: none;")
        self._verses_layout.addWidget(self._verses_hint)
        self._verses_layout.addStretch()
        scroll.setWidget(container)
        lay.addWidget(scroll, stretch=1)
        return card

    # ── Data ────────────────────────────────────────────────

    def _refresh_versions(self):
        self._ver_combo.blockSignals(True)
        self._ver_combo.clear()
        versions = db_list_versions()
        self._ver_combo.addItems(versions)
        idx = self._ver_combo.findText(self._version, Qt.MatchFixedString)
        if idx >= 0:
            self._ver_combo.setCurrentIndex(idx)
        elif versions:
            self._version = versions[0]
        self._ver_combo.blockSignals(False)

    def _on_version_changed(self, version: str):
        if not version:
            return
        self._version = version
        self._book = None
        self._chapter = None
        self._breadcrumb.setText("Choose a book to begin")
        self._clear_chapters()
        self._clear_verses()
        self._refresh_books()

    def _refresh_books(self):
        self._books_list.clear()
        for name in db_list_books(self._version):
            self._books_list.addItem(QListWidgetItem(name))

    def _on_book_selected(self, item: QListWidgetItem):
        self._book = item.text()
        self._chapter = None
        self._breadcrumb.setText(self._book)
        self._clear_verses()
        self._populate_chapters()

    def _clear_chapters(self):
        while self._chapters_grid.count():
            w = self._chapters_grid.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _populate_chapters(self):
        self._clear_chapters()
        count = db_chapter_count(self._version, self._book)
        for n in range(1, count + 1):
            btn = QPushButton(str(n))
            btn.setFixedSize(40, 32)
            btn.setCheckable(True)
            btn.setStyleSheet(sk.btn_qss("nav"))
            btn.clicked.connect(lambda _, ch=n: self._on_chapter_selected(ch))
            row, col = divmod(n - 1, CHAPTER_COLUMNS)
            self._chapters_grid.addWidget(btn, row, col)

    def _on_chapter_selected(self, chapter: int):
        self._chapter = chapter
        self._breadcrumb.setText(f"{self._book}  {chapter}")
        for i in range(self._chapters_grid.count()):
            w = self._chapters_grid.itemAt(i).widget()
            if isinstance(w, QPushButton):
                w.setChecked(w.text() == str(chapter))
        self._populate_verses()

    def _clear_verses(self):
        while self._verses_layout.count():
            item = self._verses_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate_verses(self):
        self._clear_verses()
        verses = db_get_chapter(self._version, self._book, self._chapter)
        for v in verses:
            row = BrowserVerseRow(v)
            row.send_preview.connect(self.send_preview.emit)
            row.add_queue.connect(self.add_queue.emit)
            self._verses_layout.addWidget(row)
        self._verses_layout.addStretch()
