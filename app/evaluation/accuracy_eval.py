"""
app/evaluation/accuracy_eval.py
==================================
Standalone accuracy evaluation — run manually, never wired into the
live app or operator panel. Loads a labeled test set (see
dataset_schema.py), runs each case through the real pipeline
(app/evaluation/pipeline.py — direct-reference extraction, then
semantic search, then DB lookup), and reports exact-match accuracy
split by direct vs semantic, a confusion breakdown of what it got
wrong, false-positive rate, and no-match rate. Saves CSV + JSON,
prints a summary table.

Usage:
    python -m app.evaluation.accuracy_eval
    python -m app.evaluation.accuracy_eval --testset path/to/set.csv --version KJV
"""

import os
import sys
import argparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.retrieval.semantic import SemanticEngine
from app.evaluation import dataset_schema, report
from app.evaluation.pipeline import detect, transcribe_audio

DEFAULT_TESTSET = os.path.join(os.path.dirname(__file__), "testsets", "starter_testset.csv")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")


def _normalise_book(name: str) -> str:
    return (name or "").strip().lower().replace("psalm", "psalms")


def _matches_expected(result, row) -> bool:
    if not result.matched:
        return False
    exp_book = row.get("expected_book", "")
    if not exp_book:
        return False
    try:
        return (
            _normalise_book(result.book) == _normalise_book(exp_book)
            and int(result.chapter) == int(row.get("expected_chapter"))
            and int(result.verse) == int(row.get("expected_verse"))
        )
    except (TypeError, ValueError):
        return False


def run(testset_path: str, version: str = "KJV", whisper_model_size: str = "base.en"):
    rows = dataset_schema.load_testset(testset_path)
    print(f"Loaded {len(rows)} test cases from {testset_path}")

    print("Loading SemanticEngine...")
    engine = SemanticEngine()
    engine.load_version(version)

    transcriber = None
    needs_audio = any(r["input_type"] == "audio" for r in rows)
    if needs_audio:
        from app.asr.transcriber import BibleAITranscriber
        from app.asr.transcriber import Config as TranscriberConfig
        # Real transcriber, same Config a live run would use — whichever
        # backend it resolves to (Groq cloud if GROQ_API_KEY is set, else
        # local) is what actually gets evaluated, matching production.
        transcriber = BibleAITranscriber(TranscriberConfig(model_size=whisper_model_size))

    results = []
    for row in rows:
        text = row["input"]
        asr_ms = None

        if row["input_type"] == "audio":
            audio_path = row["input"]
            if not os.path.isabs(audio_path):
                audio_path = os.path.join(os.path.dirname(testset_path), audio_path)
            text, asr_ms = transcribe_audio(transcriber, audio_path)

        result = detect(text, version, engine)

        is_correct = _matches_expected(result, row)
        is_expected_no_match = not row.get("expected_book")
        is_false_positive = is_expected_no_match and result.matched
        is_missed = (not result.matched) and (not is_expected_no_match)

        results.append({
            "id": row["id"],
            "case_type": row.get("case_type", ""),
            "input": text,
            "expected": f"{row.get('expected_book', '')} {row.get('expected_chapter', '')}:{row.get('expected_verse', '')}".strip(),
            "detected": f"{result.book} {result.chapter}:{result.verse}" if result.matched else "(no match)",
            "match_type": result.match_type or "",
            "confidence": round(result.confidence, 3) if result.confidence is not None else "",
            "correct": is_correct,
            "false_positive": is_false_positive,
            "missed": is_missed,
            "direct_ref_ms": round(result.timings.direct_ref_ms, 2),
            "semantic_ms": round(result.timings.semantic_ms, 2),
            "db_lookup_ms": round(result.timings.db_lookup_ms, 2),
            "asr_ms": round(asr_ms, 2) if asr_ms is not None else "",
            "total_ms": round(result.timings.total_ms, 2),
        })

    _summarise(results)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    csv_path = report.timestamped_path(RESULTS_DIR, "accuracy", "csv")
    json_path = report.timestamped_path(RESULTS_DIR, "accuracy", "json")
    if results:
        report.write_csv(results, list(results[0].keys()), csv_path)
        report.write_json(results, json_path)
        print(f"\nSaved results to:\n  {csv_path}\n  {json_path}")
    return results


def _summarise(results):
    direct = [r for r in results if r["case_type"] == "direct"]
    semantic = [r for r in results if r["case_type"] == "semantic"]
    no_match_cases = [r for r in results if r["case_type"] == "no_match_expected"]
    matchable = direct + semantic

    def acc(rows):
        return (sum(1 for r in rows if r["correct"]) / len(rows) * 100) if rows else float("nan")

    fp_rate = (
        sum(1 for r in no_match_cases if r["false_positive"]) / len(no_match_cases) * 100
        if no_match_cases else float("nan")
    )
    no_match_rate = (
        sum(1 for r in matchable if r["missed"]) / len(matchable) * 100
        if matchable else float("nan")
    )

    report.print_table(
        ["Metric", "Value"],
        [
            ("Total test cases", len(results)),
            ("Exact-match accuracy (direct+semantic)", f"{acc(matchable):.1f}%"),
            ("Direct-reference accuracy", f"{acc(direct):.1f}%  (n={len(direct)})"),
            ("Semantic-match accuracy", f"{acc(semantic):.1f}%  (n={len(semantic)})"),
            ("False-positive rate (no-match-expected cases)", f"{fp_rate:.1f}%  (n={len(no_match_cases)})"),
            ("No-match rate (expected a match, got none)", f"{no_match_rate:.1f}%"),
        ],
        title="ACCURACY SUMMARY",
    )

    wrong = [r for r in matchable if not r["correct"]]
    if wrong:
        report.print_table(
            ["ID", "Case", "Input", "Expected", "Detected"],
            [(r["id"], r["case_type"], r["input"][:40], r["expected"], r["detected"]) for r in wrong],
            title="CONFUSION — WRONG OR MISSED (for manual review)",
        )

    fps = [r for r in no_match_cases if r["false_positive"]]
    if fps:
        report.print_table(
            ["ID", "Input", "Wrongly detected"],
            [(r["id"], r["input"][:50], r["detected"]) for r in fps],
            title="FALSE POSITIVES",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bible AI — accuracy evaluation harness")
    parser.add_argument("--testset", default=DEFAULT_TESTSET)
    parser.add_argument("--version", default="KJV")
    parser.add_argument("--whisper-model", default="base.en")
    args = parser.parse_args()
    run(args.testset, args.version, args.whisper_model)
