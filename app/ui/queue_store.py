"""
app/ui/queue_store.py
=======================
Read/write a Queue (program list) as JSON. Split out from main_ui.py so
the persistence logic is testable without driving a real file-picker
dialog — same role theme_store.py plays for the Theme Designer.
"""

import json
from typing import Dict, List


def entries_from_queue(queue: List[Dict]) -> List[Dict]:
    """Strip a live Queue's verse dicts down to the fields worth
    persisting — drops ephemeral analysis fields like confidence/
    match_type/source_text that only make sense during the session
    that produced them."""
    return [
        {
            "book": v.get("book", ""),
            "chapter": v.get("chapter", ""),
            "verse": v.get("verse", ""),
            "version": v.get("version", ""),
            "text": v.get("text", ""),
        }
        for v in queue
    ]


def save_queue(queue: List[Dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries_from_queue(queue), f, indent=2)


def load_queue(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    # A syntactically valid JSON list with the wrong element shape (a
    # hand-edited program file, e.g. a flat list of strings) is not
    # caught by the caller's json.JSONDecodeError guard — silently
    # skip non-dict entries here instead of letting them crash later,
    # deep inside the queue panel's rendering code.
    return [entry for entry in data if isinstance(entry, dict)]
