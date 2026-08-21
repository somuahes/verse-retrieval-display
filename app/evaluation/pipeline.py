"""
app/evaluation/pipeline.py
============================
Read-only replication of hybrid.py's core per-utterance detection
priority (direct reference, then semantic) with per-stage timing.
Standalone — never imported by the live app, and does not modify
hybrid.py, semantic.py, or reference_extractor.py in any way.

Deliberately does NOT replicate navigation/version-switch/state-
tracking: accuracy test cases are independent utterances, not
multi-turn conversations. Imports SEMANTIC_CONFIDENCE directly from
hybrid.py so the threshold always matches production instead of
duplicating the number here — if hybrid.py's threshold changes, this
harness picks it up automatically.
"""

import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

from app.retrieval.reference_extractor import extract_reference
from app.retrieval.semantic import SemanticEngine
from app.retrieval.hybrid import db_get_verse, SEMANTIC_CONFIDENCE


@dataclass
class StageTimings:
    asr_ms: Optional[float] = None
    direct_ref_ms: float = 0.0
    semantic_ms: float = 0.0
    db_lookup_ms: float = 0.0
    total_ms: float = 0.0


@dataclass
class DetectionResult:
    matched: bool
    match_type: Optional[str] = None   # "direct" | "semantic" | None
    book: Optional[str] = None
    chapter: Optional[int] = None
    verse: Optional[int] = None
    confidence: Optional[float] = None
    timings: StageTimings = field(default_factory=StageTimings)


def detect(text: str, version: str, semantic_engine: SemanticEngine) -> DetectionResult:
    t_start = time.perf_counter()
    timings = StageTimings()

    t0 = time.perf_counter()
    ref = extract_reference(text)
    timings.direct_ref_ms = (time.perf_counter() - t0) * 1000

    if ref:
        book, chapter, verse = ref
        t0 = time.perf_counter()
        row = db_get_verse(version, book, chapter, verse)
        timings.db_lookup_ms += (time.perf_counter() - t0) * 1000
        if row:
            timings.total_ms = (time.perf_counter() - t_start) * 1000
            return DetectionResult(
                matched=True, match_type="direct",
                book=row["book"], chapter=row["chapter"], verse=row["verse"],
                confidence=0.97, timings=timings,
            )

    t0 = time.perf_counter()
    sem = semantic_engine.search(text)
    timings.semantic_ms = (time.perf_counter() - t0) * 1000

    if sem:
        final_score = float(sem.get("final_score", 0))
        if final_score >= SEMANTIC_CONFIDENCE:
            t0 = time.perf_counter()
            row = db_get_verse(
                version,
                str(sem.get("book", "")),
                int(sem.get("chapter", 0)),
                int(sem.get("verse", 0)),
            )
            timings.db_lookup_ms += (time.perf_counter() - t0) * 1000
            book = row["book"] if row else sem.get("book")
            chapter = row["chapter"] if row else sem.get("chapter")
            verse = row["verse"] if row else sem.get("verse")
            timings.total_ms = (time.perf_counter() - t_start) * 1000
            return DetectionResult(
                matched=True, match_type="semantic",
                book=book, chapter=chapter, verse=verse,
                confidence=final_score, timings=timings,
            )

    timings.total_ms = (time.perf_counter() - t_start) * 1000
    return DetectionResult(matched=False, timings=timings)


def transcribe_audio(transcriber, audio_path: str) -> Tuple[str, float]:
    """Returns (text, elapsed_ms). `transcriber` is a real
    BibleAITranscriber instance — routes through transcriber.decode(),
    the same public entry point live transcription uses, so it picks up
    whatever backend actually resolved (Groq cloud, local, or any future
    registered backend) plus its real anti-hallucination gates, with no
    backend-specific branching duplicated here. A bare
    WhisperModel.transcribe() call here would silently test a different,
    simpler pipeline than production (no prompt, no segment gates, no
    Groq path at all)."""
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(audio_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != transcriber.config.sample_rate:
        import scipy.signal as sps
        audio = sps.resample_poly(
            audio, transcriber.config.sample_rate, sr
        ).astype(np.float32)

    audio = transcriber._normalize_audio(audio)

    t0 = time.perf_counter()
    parts = transcriber.decode(audio)
    text = transcriber._clean_text(" ".join(parts))
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if transcriber._is_bad_output(text):
        text = ""

    return text, elapsed_ms
