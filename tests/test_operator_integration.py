"""
End-to-end integration tests for the Operator Panel (app/ui/main_ui.py)
against the real HybridEngine and the real bible.db — covers Session
History, Save-Slide-as-Image, and Browser-panel wiring added on top of
the existing app. Builds one real OperatorWindow (offscreen, via
conftest.py's QT_QPA_PLATFORM setting) and drives it the way a click
would, without needing an actual display.
"""

import pytest

from app.ui.browser_window import BrowserVerseRow
from app.ui.history_window import HistoryPanel


SAMPLE_VERSE = {
    "book": "John", "chapter": 3, "verse": 16, "version": "KJV",
    "text": "For God so loved the world...",
}
SAMPLE_VERSE_2 = {
    "book": "Genesis", "chapter": 1, "verse": 1, "version": "KJV",
    "text": "In the beginning God created the heaven and the earth.",
}


@pytest.fixture(scope="module")
def operator_window(qapp):
    import app.ui.main_ui as m
    win = m.OperatorWindow(version="KJV")
    yield win
    win.close()


@pytest.fixture(autouse=True)
def _clean_state(operator_window):
    """Every test starts from an empty History, regardless of execution
    order — the window itself is built once per module for speed
    (loading the real engine + semantic index isn't free)."""
    operator_window._history.clear()
    yield


# ── History ─────────────────────────────────────────────────

def test_set_live_appends_to_history_with_timestamp(operator_window):
    operator_window._set_live(SAMPLE_VERSE)
    assert len(operator_window._history) == 1
    entry = operator_window._history[0]
    assert entry["book"] == "John" and entry["verse"] == 16
    assert entry["displayed_at"] > 0


def test_set_live_none_does_not_append_to_history(operator_window):
    operator_window._set_live(SAMPLE_VERSE)
    operator_window._set_live(None)
    assert len(operator_window._history) == 1


def test_history_panel_reflects_appended_entries(operator_window):
    operator_window._set_live(SAMPLE_VERSE)
    operator_window._toggle_history()
    hw = operator_window._history_panel
    assert isinstance(hw, HistoryPanel)
    assert hw._table.rowCount() == 1
    assert "John" in hw._table.item(0, 1).text()


def test_history_export_json_csv_txt(operator_window, tmp_path):
    operator_window._set_live(SAMPLE_VERSE)
    operator_window._set_live(SAMPLE_VERSE_2)
    operator_window._toggle_history()
    hw = operator_window._history_panel
    hw.refresh()

    json_path = tmp_path / "history.json"
    hw._export_json(str(json_path))
    import json
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == 2

    csv_path = tmp_path / "history.csv"
    hw._export_csv(str(csv_path))
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "John" in csv_text and "Genesis" in csv_text

    txt_path = tmp_path / "history.txt"
    hw._export_txt(str(txt_path))
    txt_text = txt_path.read_text(encoding="utf-8")
    assert "John 3:16" in txt_text


def test_history_clear_empties_underlying_list(operator_window):
    operator_window._set_live(SAMPLE_VERSE)
    operator_window._toggle_history()
    operator_window._history_panel._clear()
    assert operator_window._history == []


# ── Save slide as image ────────────────────────────────────

def test_save_slide_image_grabs_a_non_null_pixmap(operator_window, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QFileDialog

    operator_window._set_live(SAMPLE_VERSE)
    operator_window._display.resize(400, 300)

    save_path = str(tmp_path / "slide.png")
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (save_path, "")))
    operator_window._save_slide_image()

    assert (tmp_path / "slide.png").exists()


def test_save_slide_image_does_nothing_before_any_history(operator_window, tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QFileDialog

    called = {"dialog_opened": False}

    def _fake_dialog(*a, **k):
        called["dialog_opened"] = True
        return (str(tmp_path / "slide.png"), "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(_fake_dialog))
    operator_window._save_slide_image()
    assert called["dialog_opened"] is False


# ── Browser wiring through the Operator Panel ──────────────

def test_browser_button_opens_a_browse_panel_bound_to_active_version(operator_window):
    operator_window._toggle_browse()
    bw = operator_window._browse_panel
    assert bw is not None
    assert bw._version == str(operator_window._engine.session.active_version)


def test_browser_send_preview_reaches_operator_preview_state(operator_window):
    operator_window._toggle_browse()
    bw = operator_window._browse_panel

    from PyQt5.QtCore import Qt
    genesis = bw._books_list.findItems("Genesis", Qt.MatchExactly)[0]
    bw._on_book_selected(genesis)
    bw._on_chapter_selected(1)

    row = next(
        bw._verses_layout.itemAt(i).widget()
        for i in range(bw._verses_layout.count())
        if isinstance(bw._verses_layout.itemAt(i).widget(), BrowserVerseRow)
    )
    row.send_preview.emit(row._verse)

    assert operator_window._last_preview_verse is not None
    assert operator_window._last_preview_verse["book"] == "Genesis"
    assert operator_window._last_preview_verse["verse"] == 1


# ── Detections empty-state + pruning ────────────────────────

def test_detections_empty_hint_and_prune_window(operator_window):
    operator_window._clear_detections()
    assert not operator_window._det_empty_hint.isHidden()

    import app.ui.main_ui as m
    sample = dict(SAMPLE_VERSE, match_type="direct", confidence=0.97)
    for i in range(m.DETECTION_MAX_CARDS + 5):
        operator_window._add_detection_card(dict(sample, verse=i))

    assert operator_window._det_empty_hint.isHidden()
    assert len(operator_window._detections) == m.DETECTION_MAX_CARDS
    # The hint widget must have survived the pruning loop, not been
    # deleted as if it were an oldest-card slot.
    assert operator_window._det_empty_hint.parent() is not None

    operator_window._clear_detections()
    assert not operator_window._det_empty_hint.isHidden()


# ── Browse/History embedded AUX panel ──────────────────────
# Neither opens a separate window — both attach as a 4th pane in the
# main splitter, one at a time, toggled by the topbar buttons.

def _ensure_aux_closed(win):
    if win._aux_kind is not None:
        win._toggle_browse() if win._aux_kind == "browse" else win._toggle_history()


def test_toggle_browse_opens_then_closes(operator_window):
    _ensure_aux_closed(operator_window)
    operator_window._toggle_browse()
    assert operator_window._aux_kind == "browse"
    assert operator_window._browse_panel.parent() is operator_window._main_splitter

    operator_window._toggle_browse()
    assert operator_window._aux_kind is None
    assert operator_window._browse_panel.parent() is None


def test_toggle_history_swaps_in_over_browse(operator_window):
    _ensure_aux_closed(operator_window)
    operator_window._toggle_browse()
    assert operator_window._aux_kind == "browse"

    operator_window._toggle_history()
    assert operator_window._aux_kind == "history"
    # Only one AUX panel attached at a time — Browse must be detached,
    # not just hidden underneath History.
    assert operator_window._browse_panel.parent() is None
    assert operator_window._history_panel.parent() is operator_window._main_splitter

    _ensure_aux_closed(operator_window)


def test_browse_panel_state_survives_a_theme_switch(operator_window):
    from PyQt5.QtCore import Qt

    _ensure_aux_closed(operator_window)
    operator_window._toggle_browse()
    bw = operator_window._browse_panel
    genesis = bw._books_list.findItems("Genesis", Qt.MatchExactly)[0]
    bw._on_book_selected(genesis)
    bw._on_chapter_selected(1)
    assert bw._book == "Genesis" and bw._chapter == 1

    # _refresh_styles() rebuilds the entire splitter tree — the panel
    # instance (and its selection) must be reattached, not recreated.
    operator_window._refresh_styles()

    assert operator_window._aux_kind == "browse"
    assert operator_window._browse_panel is bw
    assert bw._book == "Genesis" and bw._chapter == 1
    assert bw.parent() is operator_window._main_splitter

    _ensure_aux_closed(operator_window)
