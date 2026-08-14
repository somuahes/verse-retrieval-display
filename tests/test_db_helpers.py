"""
Integration tests for the Browser panel's backend — the DB helpers in
app/retrieval/hybrid.py that db_list_books/db_chapter_count/
db_get_chapter added. Runs against the real bible.db (read-only
reference data), not a mock — the whole point is catching a real
schema/query mismatch, which a fake DB can't surface.
"""

from app.retrieval.hybrid import (
    db_list_versions,
    db_list_books,
    db_chapter_count,
    db_get_chapter,
    db_get_verse,
)


def test_list_versions_includes_kjv():
    versions = db_list_versions()
    assert "KJV" in versions


def test_list_books_is_canonical_order_for_kjv():
    books = db_list_books("KJV")
    assert books[0] == "Genesis"
    assert books[-1] == "Revelation"
    assert "Exodus" in books
    assert "John" in books
    # Genesis must come before Exodus, not just be present — the whole
    # point of db_list_books is preserving canonical order via MIN(id),
    # not just returning a set of book names.
    assert books.index("Genesis") < books.index("Exodus")


def test_list_books_unknown_version_returns_empty():
    assert db_list_books("NOT_A_REAL_VERSION") == []


def test_chapter_count_matches_known_bible_structure():
    assert db_chapter_count("KJV", "Genesis") == 50
    assert db_chapter_count("KJV", "Psalms") == 150
    assert db_chapter_count("KJV", "John") == 21
    assert db_chapter_count("KJV", "Revelation") == 22


def test_chapter_count_unknown_book_returns_zero():
    assert db_chapter_count("KJV", "Not A Book") == 0


def test_get_chapter_returns_verses_in_order():
    verses = db_get_chapter("KJV", "Genesis", 1)
    assert len(verses) == 31
    assert [v["verse"] for v in verses] == list(range(1, 32))
    assert all(v["book"] == "Genesis" and v["chapter"] == 1 for v in verses)


def test_get_chapter_unknown_chapter_returns_empty():
    assert db_get_chapter("KJV", "Genesis", 999) == []


def test_get_chapter_agrees_with_get_verse():
    chapter = db_get_chapter("KJV", "John", 3)
    single = db_get_verse("KJV", "John", 3, 16)
    match = next(v for v in chapter if v["verse"] == 16)
    assert match["text"] == single["text"]


def test_get_chapter_handles_psalm_psalms_alias():
    # db_get_verse normalises Psalm/Psalms; db_get_chapter should too,
    # since the Browser panel lists whichever name db_list_books returns.
    verses = db_get_chapter("KJV", "Psalms", 23)
    assert len(verses) == 6
    assert verses[0]["text"]
