"""
app/ui/theme_designer.py
==========================
Theme Designer window — library (left), live preview (center),
properties panel with Text/Background/Layout tabs (right).

Scoped to the same core property set as theme_model.py's first pass
(see that file's docstring for what's deferred). The preview is a
fixed 960x540 box, not a true 1:1-at-target-resolution canvas with
zoom/pan — that's part of the deferred scope too.
"""

from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QLineEdit, QComboBox, QSpinBox,
    QTabWidget, QColorDialog, QFileDialog, QInputDialog, QMessageBox,
    QFrame,
)

from app.ui.theme_model import Theme
from app.ui import theme_store
from app.ui.display_window import DisplayWindow


SAMPLE_VERSE = {
    "book": "John", "chapter": 3, "verse": 16, "version": "KJV",
    "text": "For God so loved the world, that he gave his only begotten Son, "
            "that whosoever believeth in him should not perish, "
            "but have everlasting life.",
}


class ThemeDesigner(QMainWindow):
    theme_activated = pyqtSignal(Theme)

    def __init__(self, active_theme_name: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Theme Designer")
        self.resize(1400, 860)

        self._draft: Theme = Theme()
        self._saved: Theme = Theme()
        self._dirty = False
        self._loading = False
        self._current_name: Optional[str] = None
        self._color_refreshers = {}

        self._build_ui()
        self._refresh_library()

        start = active_theme_name or (theme_store.list_themes() or [None])[0]
        if start:
            self._load_theme(start)

    # ── UI build ────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._build_library())
        lay.addWidget(self._build_preview(), stretch=1)
        lay.addWidget(self._build_properties())

    def _build_library(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(220)
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        v.addWidget(QLabel("Themes"))

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_theme_selected)
        v.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._new_theme)
        dup_btn = QPushButton("Duplicate")
        dup_btn.clicked.connect(self._duplicate_theme)
        row.addWidget(new_btn)
        row.addWidget(dup_btn)
        v.addLayout(row)

        row2 = QHBoxLayout()
        ren_btn = QPushButton("Rename")
        ren_btn.clicked.connect(self._rename_theme)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_theme)
        row2.addWidget(ren_btn)
        row2.addWidget(del_btn)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        imp_btn = QPushButton("Import…")
        imp_btn.clicked.connect(self._import_theme)
        exp_btn = QPushButton("Export…")
        exp_btn.clicked.connect(self._export_theme)
        row3.addWidget(imp_btn)
        row3.addWidget(exp_btn)
        v.addLayout(row3)

        activate_btn = QPushButton("Use This Theme")
        activate_btn.clicked.connect(self._activate_theme)
        v.addWidget(activate_btn)

        return w

    def _build_preview(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #222;")
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)

        frame = QFrame()
        frame.setFixedSize(960, 540)
        frame.setStyleSheet("border: 2px solid #555;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)

        self._preview = DisplayWindow()
        self._preview.setWindowFlags(Qt.Widget)
        self._preview.setParent(frame)
        self._preview.resize(960, 540)
        fl.addWidget(self._preview)

        v.addWidget(frame)
        return w

    def _build_properties(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(360)
        v = QVBoxLayout(w)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        self._name_lbl = QLabel("—")
        self._name_lbl.setStyleSheet("font-weight: 700; font-size: 14px;")
        v.addWidget(self._name_lbl)

        self._dirty_lbl = QLabel("")
        self._dirty_lbl.setStyleSheet("color: #E8A830; font-size: 11px;")
        v.addWidget(self._dirty_lbl)

        tabs = QTabWidget()
        tabs.addTab(self._build_text_tab(), "Text")
        tabs.addTab(self._build_background_tab(), "Background")
        tabs.addTab(self._build_layout_tab(), "Layout")
        v.addWidget(tabs, stretch=1)

        row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_draft)
        discard_btn = QPushButton("Discard")
        discard_btn.clicked.connect(self._discard_draft)
        row.addWidget(save_btn)
        row.addWidget(discard_btn)
        v.addLayout(row)

        return w

    # ── Field helpers ─────────────────────────────────────

    def _set_draft_field(self, field: str, value):
        if self._loading:
            return
        setattr(self._draft, field, value)
        self._mark_dirty()
        self._apply_draft_to_preview()

    def _row(self, parent_layout, label_text):
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        parent_layout.addLayout(row)
        return row

    def _make_color_button(self, field: str, initial: str) -> QPushButton:
        btn = QPushButton()
        btn.setFixedSize(28, 24)

        def _refresh(color):
            btn.setStyleSheet(
                f"background: {color}; border: 1px solid #555; border-radius: 4px;")

        def _pick():
            current = QColor(getattr(self._draft, field, initial))
            color = QColorDialog.getColor(current, self, "Choose Color")
            if color.isValid():
                hex_color = color.name()
                _refresh(hex_color)
                self._set_draft_field(field, hex_color)

        btn.clicked.connect(_pick)
        _refresh(initial)
        self._color_refreshers[field] = _refresh
        return btn

    # ── Tabs ──────────────────────────────────────────────

    def _build_text_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        row = self._row(v, "Font family")
        self._font_family_in = QLineEdit()
        self._font_family_in.textChanged.connect(
            lambda t: self._set_draft_field("font_family", t))
        row.addWidget(self._font_family_in)

        row = self._row(v, "Weight")
        self._font_weight_combo = QComboBox()
        self._font_weight_combo.addItems(["normal", "bold"])
        self._font_weight_combo.currentTextChanged.connect(
            lambda t: self._set_draft_field("font_weight", t))
        row.addWidget(self._font_weight_combo)

        row = self._row(v, "Size")
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(12, 120)
        self._font_size_spin.valueChanged.connect(
            lambda n: self._set_draft_field("font_size", n))
        row.addWidget(self._font_size_spin)

        row = self._row(v, "Color")
        self._text_color_btn = self._make_color_button("text_color", "#FFFFFF")
        row.addWidget(self._text_color_btn)

        row = self._row(v, "Alignment")
        self._text_align_combo = QComboBox()
        self._text_align_combo.addItems(["left", "center", "right"])
        self._text_align_combo.currentTextChanged.connect(
            lambda t: self._set_draft_field("text_align", t))
        row.addWidget(self._text_align_combo)

        v.addWidget(QLabel("— Reference line —"))

        row = self._row(v, "Ref font family")
        self._ref_font_family_in = QLineEdit()
        self._ref_font_family_in.textChanged.connect(
            lambda t: self._set_draft_field("ref_font_family", t))
        row.addWidget(self._ref_font_family_in)

        row = self._row(v, "Ref size")
        self._ref_font_size_spin = QSpinBox()
        self._ref_font_size_spin.setRange(10, 80)
        self._ref_font_size_spin.valueChanged.connect(
            lambda n: self._set_draft_field("ref_font_size", n))
        row.addWidget(self._ref_font_size_spin)

        row = self._row(v, "Ref color")
        self._ref_color_btn = self._make_color_button("ref_color", "#D4AF37")
        row.addWidget(self._ref_color_btn)

        row = self._row(v, "Ref position")
        self._ref_pos_combo = QComboBox()
        self._ref_pos_combo.addItems(["above", "below"])
        self._ref_pos_combo.currentTextChanged.connect(
            lambda t: self._set_draft_field("reference_position", t))
        row.addWidget(self._ref_pos_combo)

        v.addStretch()
        return w

    def _build_background_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        row = self._row(v, "Type")
        self._bg_type_combo = QComboBox()
        self._bg_type_combo.addItems(["solid", "image"])
        self._bg_type_combo.currentTextChanged.connect(
            lambda t: self._set_draft_field("background_type", t))
        row.addWidget(self._bg_type_combo)

        row = self._row(v, "Color")
        self._bg_color_btn = self._make_color_button("background_color", "#000000")
        row.addWidget(self._bg_color_btn)

        row = self._row(v, "Image")
        self._bg_image_in = QLineEdit()
        self._bg_image_in.setReadOnly(True)
        row.addWidget(self._bg_image_in)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_bg_image)
        row.addWidget(browse_btn)

        row = self._row(v, "Fit")
        self._bg_fit_combo = QComboBox()
        self._bg_fit_combo.addItems(["cover", "contain", "stretch"])
        self._bg_fit_combo.currentTextChanged.connect(
            lambda t: self._set_draft_field("background_fit", t))
        row.addWidget(self._bg_fit_combo)

        v.addStretch()
        return w

    def _build_layout_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)

        row = self._row(v, "Content area %")
        self._content_pct_spin = QSpinBox()
        self._content_pct_spin.setRange(10, 100)
        self._content_pct_spin.valueChanged.connect(
            lambda n: self._set_draft_field("content_area_pct", n))
        row.addWidget(self._content_pct_spin)

        row = self._row(v, "Padding (px)")
        self._padding_spin = QSpinBox()
        self._padding_spin.setRange(0, 300)
        self._padding_spin.valueChanged.connect(
            lambda n: self._set_draft_field("padding", n))
        row.addWidget(self._padding_spin)

        row = self._row(v, "Element spacing (px)")
        self._spacing_spin = QSpinBox()
        self._spacing_spin.setRange(0, 150)
        self._spacing_spin.valueChanged.connect(
            lambda n: self._set_draft_field("element_spacing", n))
        row.addWidget(self._spacing_spin)

        v.addStretch()
        return w

    def _browse_bg_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Background Image", "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self._bg_image_in.setText(path)
            self._set_draft_field("background_image_path", path)

    # ── Load / populate ───────────────────────────────────

    def _populate_fields(self):
        self._loading = True
        t = self._draft
        self._font_family_in.setText(t.font_family)
        self._font_weight_combo.setCurrentText(t.font_weight)
        self._font_size_spin.setValue(t.font_size)
        self._color_refreshers["text_color"](t.text_color)
        self._text_align_combo.setCurrentText(t.text_align)
        self._ref_font_family_in.setText(t.ref_font_family)
        self._ref_font_size_spin.setValue(t.ref_font_size)
        self._color_refreshers["ref_color"](t.ref_color)
        self._ref_pos_combo.setCurrentText(t.reference_position)
        self._bg_type_combo.setCurrentText(t.background_type)
        self._color_refreshers["background_color"](t.background_color)
        self._bg_image_in.setText(t.background_image_path)
        self._bg_fit_combo.setCurrentText(t.background_fit)
        self._content_pct_spin.setValue(t.content_area_pct)
        self._padding_spin.setValue(t.padding)
        self._spacing_spin.setValue(t.element_spacing)
        self._loading = False

    def _apply_draft_to_preview(self):
        self._preview.set_theme(self._draft)
        self._preview.show_verse(SAMPLE_VERSE)

    def _mark_dirty(self):
        self._dirty = True
        self._dirty_lbl.setText("● Unsaved changes")

    def _load_theme(self, name: str):
        theme = theme_store.load_theme(name)
        if not theme:
            return
        self._current_name = name
        self._saved = theme
        self._draft = theme.clone()
        self._dirty = False
        self._dirty_lbl.setText("")
        self._name_lbl.setText(name)
        self._populate_fields()
        self._apply_draft_to_preview()
        self._highlight_selected(name)

    # ── Library CRUD ──────────────────────────────────────

    def _refresh_library(self):
        self._list.clear()
        for name in theme_store.list_themes():
            self._list.addItem(QListWidgetItem(name))
        self._highlight_selected(self._current_name)

    def _highlight_selected(self, name):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.text() == name:
                self._list.setCurrentItem(item)
                break

    def _confirm_discard(self) -> bool:
        resp = QMessageBox.question(
            self, "Unsaved changes",
            f'"{self._current_name}" has unsaved changes. Discard them?',
            QMessageBox.Yes | QMessageBox.No,
        )
        return resp == QMessageBox.Yes

    def _on_theme_selected(self, item: QListWidgetItem):
        if self._dirty and not self._confirm_discard():
            self._highlight_selected(self._current_name)
            return
        self._load_theme(item.text())

    def _new_theme(self):
        name, ok = QInputDialog.getText(self, "New Theme", "Theme name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in theme_store.list_themes():
            QMessageBox.warning(self, "Name in use", "A theme with that name already exists.")
            return
        theme_store.save_theme(Theme(name=name))
        self._refresh_library()
        self._load_theme(name)

    def _duplicate_theme(self):
        if not self._current_name:
            return
        dup = theme_store.duplicate_theme(self._current_name)
        if dup:
            self._refresh_library()
            self._load_theme(dup.name)

    def _rename_theme(self):
        if not self._current_name:
            return
        new_name, ok = QInputDialog.getText(
            self, "Rename Theme", "New name:", text=self._current_name)
        if not ok or not new_name.strip() or new_name == self._current_name:
            return
        renamed = theme_store.rename_theme(self._current_name, new_name.strip())
        if renamed:
            self._refresh_library()
            self._load_theme(renamed.name)

    def _delete_theme(self):
        if not self._current_name:
            return
        if len(theme_store.list_themes()) <= 1:
            QMessageBox.warning(self, "Can't delete", "At least one theme must remain.")
            return
        resp = QMessageBox.question(
            self, "Delete Theme", f'Delete "{self._current_name}"?',
            QMessageBox.Yes | QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        theme_store.delete_theme(self._current_name)
        self._refresh_library()
        remaining = theme_store.list_themes()
        if remaining:
            self._load_theme(remaining[0])

    def _import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Theme", "", "Theme JSON (*.json)")
        if not path:
            return
        theme = theme_store.import_theme(path)
        if theme:
            self._refresh_library()
            self._load_theme(theme.name)
        else:
            QMessageBox.warning(self, "Import failed", "Couldn't read that file as a theme.")

    def _export_theme(self):
        if not self._current_name:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Theme", f"{self._current_name}.json", "Theme JSON (*.json)")
        if not path:
            return
        theme_store.export_theme(self._current_name, path)

    # ── Draft save / discard / activate ───────────────────

    def _save_draft(self):
        if not self._current_name:
            return
        self._draft.name = self._current_name
        theme_store.save_theme(self._draft)
        self._saved = self._draft.clone()
        self._dirty = False
        self._dirty_lbl.setText("")

    def _discard_draft(self):
        if not self._current_name:
            return
        self._draft = self._saved.clone()
        self._dirty = False
        self._dirty_lbl.setText("")
        self._populate_fields()
        self._apply_draft_to_preview()

    def _activate_theme(self):
        self.theme_activated.emit(self._draft.clone())

    def closeEvent(self, event):
        if self._dirty and not self._confirm_discard():
            event.ignore()
            return
        super().closeEvent(event)
