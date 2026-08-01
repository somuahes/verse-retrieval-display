"""
app/evaluation/dataset_schema.py
===================================
Labeled test-set schema for the accuracy evaluation harness.

CSV columns:
    id               - unique string id for the test case
    input_type       - "text" or "audio"
    input            - transcript text, OR path to a WAV file (relative
                        to the testset's own directory, or absolute)
    case_type        - "direct" | "semantic" | "no_match_expected"
    expected_book    - expected book name (blank for no_match_expected)
    expected_chapter - expected chapter number (blank for no_match_expected)
    expected_verse   - expected verse number (blank for no_match_expected)
    notes            - optional free text

"no_match_expected" cases are for false-positive testing: text that
should NOT trigger any verse detection (casual sermon speech with no
scripture reference or quote). If the pipeline detects anything for
one of these, it counts as a false positive.
"""

import csv
import os

CSV_FIELDS = [
    "id", "input_type", "input", "case_type",
    "expected_book", "expected_chapter", "expected_verse", "notes",
]


def write_template(path: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow({
            "id": "example-1",
            "input_type": "text",
            "input": "John chapter 3 verse 16",
            "case_type": "direct",
            "expected_book": "John",
            "expected_chapter": 3,
            "expected_verse": 16,
            "notes": "Direct spoken reference",
        })
        writer.writerow({
            "id": "example-2",
            "input_type": "text",
            "input": "be strong and do not be afraid for the lord your god is with you",
            "case_type": "semantic",
            "expected_book": "Deuteronomy",
            "expected_chapter": 31,
            "expected_verse": 6,
            "notes": "Paraphrase, no exact quote",
        })
        writer.writerow({
            "id": "example-3",
            "input_type": "text",
            "input": "thank you all so much for coming out today",
            "case_type": "no_match_expected",
            "expected_book": "",
            "expected_chapter": "",
            "expected_verse": "",
            "notes": "Casual speech, no scripture reference",
        })
        writer.writerow({
            "id": "example-4",
            "input_type": "audio",
            "input": "clips/example4.wav",
            "case_type": "direct",
            "expected_book": "Romans",
            "expected_chapter": 8,
            "expected_verse": 28,
            "notes": "Path is relative to this CSV's own directory",
        })


def load_testset(path: str):
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)
