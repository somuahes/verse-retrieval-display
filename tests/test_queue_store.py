"""
Unit/integration tests for app/ui/queue_store.py — the save/load logic
split out of main_ui.py's Queue panel specifically so it's testable
without driving a real QFileDialog.
"""

import json

from app.ui import queue_store


SAMPLE_QUEUE = [
    {
        "book": "John", "chapter": 3, "verse": 16, "version": "KJV",
        "text": "For God so loved the world...",
        "match_type": "direct", "confidence": 0.97, "source_text": "John 3:16",
    },
    {
        "book": "Genesis", "chapter": 1, "verse": 1, "version": "KJV",
        "text": "In the beginning God created the heaven and the earth.",
    },
]


def test_entries_from_queue_keeps_only_persistable_fields():
    entries = queue_store.entries_from_queue(SAMPLE_QUEUE)
    assert entries[0] == {
        "book": "John", "chapter": 3, "verse": 16, "version": "KJV",
        "text": "For God so loved the world...",
    }
    # Ephemeral, session-only analysis fields must not survive to disk.
    assert "match_type" not in entries[0]
    assert "confidence" not in entries[0]
    assert "source_text" not in entries[0]


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "program.json"
    queue_store.save_queue(SAMPLE_QUEUE, str(path))

    loaded = queue_store.load_queue(str(path))
    assert loaded == queue_store.entries_from_queue(SAMPLE_QUEUE)


def test_save_writes_readable_json(tmp_path):
    path = tmp_path / "program.json"
    queue_store.save_queue(SAMPLE_QUEUE, str(path))
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert isinstance(raw, list)
    assert len(raw) == 2


def test_load_rejects_non_list_json(tmp_path):
    path = tmp_path / "not_a_list.json"
    path.write_text(json.dumps({"oops": "this is an object, not a list"}), encoding="utf-8")
    assert queue_store.load_queue(str(path)) == []


def test_load_empty_queue_round_trip(tmp_path):
    path = tmp_path / "empty.json"
    queue_store.save_queue([], str(path))
    assert queue_store.load_queue(str(path)) == []
