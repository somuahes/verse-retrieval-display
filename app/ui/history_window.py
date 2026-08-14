"""
app/ui/history_window.py
==========================
Session History — every verse actually pushed to the live projector
during this run, independent of the AI Detections panel (which logs
every engine decision, including ones never sent live, capped at 30).
An embedded panel in the Operator Panel's own window (toggled in/out of
the main splitter by the topbar's History button), not a separate popup
window.
"""

import csv
import json
from datetime import datetime
from typing import Callable, List

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QHeaderView, QAbstractItemView,
)

from app.ui import style_kit as sk


class HistoryPanel(QWidget):
    """Embeddable session-history table — reads directly from the shared
    `history` list the operator window appends to; call refresh() to
    re-pull it after an external change."""

    closed = pyqtSignal()

    PANEL_WIDTH = 560

    def __init__(self, history: List[dict], on_clear: Callable[[], None], parent=None):
        super().__init__(parent)
        self.setStyleSheet(sk.window_qss())
        self.setMinimumWidth(self.PANEL_WIDTH)
        self._history = history
        self._on_clear = on_clear
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("VERSES DISPLAYED LIVE THIS SESSION")
        title.setStyleSheet(sk.eyebrow_qss())
        header.addWidget(title)
        header.addStretch()
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(f"color: {sk.c('text_d')}; background: transparent; border: none;")
        header.addWidget(self._count_lbl)
        refresh_btn = QPushButton("↺ Refresh")
        refresh_btn.setStyleSheet(sk.btn_qss("ghost"))
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        export_btn = QPushButton("Export…")
        export_btn.setStyleSheet(sk.btn_qss("nav"))
        export_btn.clicked.connect(self._export)
        header.addWidget(export_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet(sk.btn_qss("danger"))
        clear_btn.clicked.connect(self._clear)
        header.addWidget(clear_btn)
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close History")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet(sk.btn_qss("ghost"))
        close_btn.clicked.connect(self.closed.emit)
        header.addWidget(close_btn)
        lay.addLayout(header)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Time", "Reference", "Version", "Text", "Source"])
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setVisible(False)
        header_view = self._table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(3, QHeaderView.Stretch)
        header_view.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        lay.addWidget(self._table)

    # ── Data ────────────────────────────────────────────────

    def refresh(self):
        self._table.setRowCount(0)
        for entry in self._history:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(self._time_text(entry)))
            self._table.setItem(row, 1, QTableWidgetItem(self._ref_text(entry)))
            self._table.setItem(row, 2, QTableWidgetItem(str(entry.get("version", "")).upper()))
            self._table.setItem(row, 3, QTableWidgetItem(str(entry.get("text", ""))))
            self._table.setItem(row, 4, QTableWidgetItem(str(entry.get("match_type", ""))))
        self._count_lbl.setText(f"{len(self._history)} verse(s)")

    @staticmethod
    def _time_text(entry: dict) -> str:
        ts = entry.get("displayed_at")
        if not ts:
            return ""
        try:
            return datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return ""

    @staticmethod
    def _ref_text(entry: dict) -> str:
        return f"{entry.get('book', '')} {entry.get('chapter', '')}:{entry.get('verse', '')}"

    # ── Export ──────────────────────────────────────────────

    def _export(self):
        if not self._history:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export History", "session_history.json",
            "JSON (*.json);;CSV (*.csv);;Text (*.txt)")
        if not path:
            return
        if path.lower().endswith(".csv"):
            self._export_csv(path)
        elif path.lower().endswith(".txt"):
            self._export_txt(path)
        else:
            self._export_json(path)

    def _export_json(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._history, f, indent=2, default=str)

    def _export_csv(self, path: str):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "book", "chapter", "verse", "version", "text"])
            for e in self._history:
                w.writerow([
                    self._time_text(e), e.get("book", ""), e.get("chapter", ""),
                    e.get("verse", ""), str(e.get("version", "")).upper(),
                    e.get("text", ""),
                ])

    def _export_txt(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            for e in self._history:
                f.write(
                    f"[{self._time_text(e)}] {self._ref_text(e)} "
                    f"({str(e.get('version', '')).upper()}) — {e.get('text', '')}\n"
                )

    # ── Clear ───────────────────────────────────────────────

    def _clear(self):
        self._on_clear()
        self.refresh()
