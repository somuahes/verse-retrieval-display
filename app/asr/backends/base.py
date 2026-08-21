"""
app/asr/backends/base.py
=========================
Shared interface every ASR backend implements, plus the two things every
backend needs regardless of vendor: a common raw-segment shape (so the
engine's anti-hallucination gates in transcriber.py can apply identically
to a backend with no per-segment confidence data, like Khaya, as to one
with full Whisper-style stats) and WAV encoding for HTTP-based backends.

Adding a new backend means adding one new file here that subclasses
ASRBackend and one line in registry.py -- transcriber.py itself is never
touched. See registry.py for the registration point.
"""

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import soundfile as sf


@dataclass
class RawSegment:
    text: str
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = None
    compression_ratio: Optional[float] = None
    # True for a last-resort fallback segment (e.g. a response with no
    # per-segment data at all) that should reach the transcript
    # unconditionally -- the engine's gates exist to catch decode
    # artifacts *within* real segment data, which a bypass segment by
    # definition doesn't have.
    bypass_gates: bool = False


def encode_wav(audio: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
    buf.seek(0)
    return buf.read()


class ASRBackend(ABC):
    name: str = ""

    # Whether this backend spends its time waiting on network I/O rather
    # than local CPU -- determines how many utterances can safely decode
    # concurrently (see transcriber.py's _dispatch_worker_count).
    io_bound: bool = False

    # Whether Config.backend="auto" is allowed to pick this backend.
    # Only true for backends that are safe drop-in substitutes for each
    # other (local Whisper / Groq cloud, both English-focused). A
    # language-specific backend must be requested explicitly by name --
    # "auto" never guesses its way onto one, since silently substituting
    # a backend with no real support for the spoken language would
    # produce confident garbage instead of just failing loudly.
    auto_eligible: bool = False

    def __init__(self, config):
        self.config = config

    @abstractmethod
    def load(self) -> None:
        """Heavy one-time init: load a model, validate an API key, warm
        up a connection, etc. Called once before the first transcribe()."""

    @abstractmethod
    def transcribe(self, audio: np.ndarray) -> Optional[List[RawSegment]]:
        """Decode one self-contained utterance clip. Returns raw,
        gate-unfiltered segments -- transcriber.py applies its
        anti-hallucination gates uniformly afterward. Return None (not an
        empty list) to signal a request failure the caller should treat
        as "backend unavailable" (e.g. network drop) as opposed to a
        successful decode that just found no speech."""
