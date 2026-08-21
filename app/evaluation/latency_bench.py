"""
app/evaluation/latency_bench.py
==================================
Standalone latency benchmark — stage-by-stage timing (direct-reference
extraction, semantic search, DB lookup, and ASR when audio cases are
present) across the test set, and across whatever Whisper model sizes
are actually installed locally. Run manually; never wired into the
live app.

Usage:
    python -m app.evaluation.latency_bench
"""

import os
import sys
import argparse
import statistics

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.retrieval.semantic import SemanticEngine
from app.evaluation import dataset_schema, report
from app.evaluation.pipeline import detect, transcribe_audio

DEFAULT_TESTSET = os.path.join(os.path.dirname(__file__), "testsets", "starter_testset.csv")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
WHISPER_MODELS_DIR = os.path.join(ROOT, "models", "faster-whisper")


def available_whisper_models():
    if not os.path.isdir(WHISPER_MODELS_DIR):
        return []
    return sorted([
        d for d in os.listdir(WHISPER_MODELS_DIR)
        if os.path.isdir(os.path.join(WHISPER_MODELS_DIR, d))
        and os.path.exists(os.path.join(WHISPER_MODELS_DIR, d, "model.bin"))
    ])


def _bench_asr(rows, testset_path, model_size):
    # transcribe_audio() (app/evaluation/pipeline.py) routes through a
    # real BibleAITranscriber — its own decode()/gate/cleanup pipeline,
    # not a bare WhisperModel.transcribe() call — so eval numbers reflect
    # what live transcription actually does (see that function's
    # docstring). A raw WhisperModel has none of the attributes it needs
    # (.config, .decode(), ._normalize_audio(), ._clean_text(),
    # ._is_bad_output()) and would raise AttributeError on the first
    # clip.
    from app.asr.transcriber import BibleAITranscriber, Config
    transcriber = BibleAITranscriber(
        Config(backend="local", model_size=model_size)
    )

    per_clip = []
    for row in rows:
        audio_path = row["input"]
        if not os.path.isabs(audio_path):
            audio_path = os.path.join(os.path.dirname(testset_path), audio_path)
        if not os.path.exists(audio_path):
            continue
        clip_seconds = os.path.getsize(audio_path) / (16000 * 2)  # rough estimate, 16kHz 16-bit mono
        _text, elapsed_ms = transcribe_audio(transcriber, audio_path)
        per_clip.append({"id": row["id"], "approx_clip_seconds": round(clip_seconds, 2), "asr_ms": round(elapsed_ms, 2)})
    return per_clip


def run(testset_path: str, version: str = "KJV"):
    all_rows = dataset_schema.load_testset(testset_path)
    text_rows = [r for r in all_rows if r["input_type"] == "text"]
    audio_rows = [r for r in all_rows if r["input_type"] == "audio"]

    print(f"Timing {len(text_rows)} text-input cases "
          f"({len(audio_rows)} audio cases in the test set)")

    engine = SemanticEngine()
    engine.load_version(version)

    stage_times = {"direct_ref_ms": [], "semantic_ms": [], "db_lookup_ms": [], "total_ms": []}
    per_case = []

    for row in text_rows:
        result = detect(row["input"], version, engine)
        per_case.append({
            "id": row["id"],
            "input_len_chars": len(row["input"]),
            "direct_ref_ms": round(result.timings.direct_ref_ms, 3),
            "semantic_ms": round(result.timings.semantic_ms, 3),
            "db_lookup_ms": round(result.timings.db_lookup_ms, 3),
            "total_ms": round(result.timings.total_ms, 3),
        })
        for k in stage_times:
            stage_times[k].append(getattr(result.timings, k))

    rows_summary = []
    for stage, values in stage_times.items():
        if not values:
            continue
        rows_summary.append((
            stage, f"{statistics.mean(values):.2f}", f"{min(values):.2f}",
            f"{max(values):.2f}", f"{statistics.median(values):.2f}",
        ))
    report.print_table(
        ["Stage", "Mean (ms)", "Min (ms)", "Max (ms)", "Median (ms)"],
        rows_summary, title="LATENCY BY STAGE (text-input cases)",
    )

    models = available_whisper_models()
    print(f"\nWhisper models available locally: {models or '(none found)'}")
    if len(models) <= 1:
        print("Only one (or zero) Whisper model size is installed — install "
              "additional sizes (e.g. small.en, medium.en) under "
              "models/faster-whisper/ to compare ASR latency across sizes.")

    asr_results = {}
    if audio_rows and models:
        for model_size in models:
            print(f"\nBenchmarking ASR with model '{model_size}'...")
            asr_results[model_size] = _bench_asr(audio_rows, testset_path, model_size)
            durations = [c["asr_ms"] for c in asr_results[model_size]]
            if durations:
                report.print_table(
                    ["Clip", "~Length (s)", "ASR time (ms)"],
                    [(c["id"], c["approx_clip_seconds"], c["asr_ms"]) for c in asr_results[model_size]],
                    title=f"ASR LATENCY — model '{model_size}'",
                )
    elif audio_rows and not models:
        print("Audio cases present in the test set, but no Whisper model "
              "found under models/faster-whisper/ — skipping ASR timing.")
    else:
        print("No audio cases in the test set — ASR stage not measured. "
              "Add input_type=audio rows to the test set to include it.")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    csv_path = report.timestamped_path(RESULTS_DIR, "latency", "csv")
    json_path = report.timestamped_path(RESULTS_DIR, "latency", "json")
    if per_case:
        report.write_csv(per_case, list(per_case[0].keys()), csv_path)
    report.write_json({
        "per_case_text": per_case,
        "whisper_models_available": models,
        "asr_by_model": asr_results,
    }, json_path)
    print(f"\nSaved results to:\n  {json_path}" + (f"\n  {csv_path}" if per_case else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bible AI — latency benchmark")
    parser.add_argument("--testset", default=DEFAULT_TESTSET)
    parser.add_argument("--version", default="KJV")
    args = parser.parse_args()
    run(args.testset, args.version)
