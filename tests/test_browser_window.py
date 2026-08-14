"""
Integration tests for the Browser panel (app/ui/browser_window.py) in
isolation from the Operator Panel — it only needs the DB, not the ASR/
matching engine, so these run fast and don't require a full
OperatorWindow.
"""

from PyQt5.QtCore import Qt

from app.ui.browser_window import BrowsePanel, BrowserVerseRow


def _select_book(bw: BrowsePanel, name: str):
    items = bw._books_list.findItems(name, Qt.MatchExactly)
    assert items, f"{name!r} not found in Books list"
    bw._on_book_selected(items[0])


def test_opens_with_books_populated_and_no_selection(qapp):
    bw = BrowsePanel(initial_version="KJV")
    assert bw._books_list.count() > 0
    assert bw._books_list.item(0).text() == "Genesis"
    assert bw._breadcrumb.text() == "Choose a book to begin"
    bw.close()


def test_selecting_a_book_populates_chapter_grid(qapp):
    bw = BrowsePanel(initial_version="KJV")
    _select_book(bw, "Genesis")
    assert bw._book == "Genesis"
    assert bw._breadcrumb.text() == "Genesis"
    assert bw._chapters_grid.count() == 50
    bw.close()


def test_selecting_a_chapter_populates_verse_rows(qapp):
    bw = BrowsePanel(initial_version="KJV")
    _select_book(bw, "John")
    bw._on_chapter_selected(3)
    assert bw._chapter == 3
    assert bw._breadcrumb.text() == "John  3"

    rows = [
        bw._verses_layout.itemAt(i).widget()
        for i in range(bw._verses_layout.count())
        if isinstance(bw._verses_layout.itemAt(i).widget(), BrowserVerseRow)
    ]
    assert len(rows) == 36  # John 3 has 36 verses
    assert rows[15]._verse["verse"] == 16
    assert "God so loved" in rows[15]._verse["text"]
    bw.close()


def test_send_preview_signal_carries_the_clicked_verse(qapp):
    bw = BrowsePanel(initial_version="KJV")
    _select_book(bw, "John")
    bw._on_chapter_selected(3)

    received = {}
    bw.send_preview.connect(lambda v: received.setdefault("verse", v))

    row = next(
        bw._verses_layout.itemAt(i).widget()
        for i in range(bw._verses_layout.count())
        if isinstance(bw._verses_layout.itemAt(i).widget(), BrowserVerseRow)
        and bw._verses_layout.itemAt(i).widget()._verse["verse"] == 16
    )
    row.send_preview.emit(row._verse)

    assert received["verse"]["book"] == "John"
    assert received["verse"]["chapter"] == 3
    assert received["verse"]["verse"] == 16
    bw.close()


def test_add_queue_signal_carries_the_clicked_verse(qapp):
    bw = BrowsePanel(initial_version="KJV")
    _select_book(bw, "Genesis")
    bw._on_chapter_selected(1)

    received = {}
    bw.add_queue.connect(lambda v: received.setdefault("verse", v))

    row = next(
        bw._verses_layout.itemAt(i).widget()
        for i in range(bw._verses_layout.count())
        if isinstance(bw._verses_layout.itemAt(i).widget(), BrowserVerseRow)
    )
    row.add_queue.emit(row._verse)

    assert received["verse"]["book"] == "Genesis"
    bw.close()


def test_switching_book_clears_previous_chapter_and_verses(qapp):
    bw = BrowsePanel(initial_version="KJV")
    _select_book(bw, "John")
    bw._on_chapter_selected(3)
    assert bw._chapter == 3

    _select_book(bw, "Genesis")
    assert bw._book == "Genesis"
    assert bw._chapter is None
    verse_rows = [
        bw._verses_layout.itemAt(i).widget()
        for i in range(bw._verses_layout.count())
        if isinstance(bw._verses_layout.itemAt(i).widget(), BrowserVerseRow)
    ]
    assert verse_rows == []
    bw.close()


def test_changing_version_reloads_books_and_resets_selection(qapp):
    bw = BrowsePanel(initial_version="KJV")
    _select_book(bw, "Genesis")
    bw._on_chapter_selected(1)

    versions = [bw._ver_combo.itemText(i) for i in range(bw._ver_combo.count())]
    other = next((v for v in versions if v != "KJV"), None)
    if other is None:
        bw.close()
        return  # only one version loaded in this DB — nothing to switch to

    bw._on_version_changed(other)
    assert bw._version == other
    assert bw._book is None
    assert bw._chapter is None
    assert bw._breadcrumb.text() == "Choose a book to begin"
    bw.close()
