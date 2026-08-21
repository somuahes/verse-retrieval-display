"""
app/asr/backends/khaya.py
===========================
Placeholder for GhanaNLP's Khaya ASR API -- the one service that actually
understands Twi (neither local faster-whisper nor Groq's hosted Whisper
has real Twi support). Not implemented yet; this file exists so a real
implementation is "fill in load()/transcribe() here," never a change to
transcriber.py or any English backend.

A prior working implementation existed and was measured against real
audio before being reverted along with an unrelated bad merge -- see
commit 9b274ac ("Add Twi ASR backends (Khaya + offline fallback) and
fuzzy reference matching") for the reference implementation: POST audio/
wav to https://translation-api.ghananlp.org/asr/v1/transcribe with an
Ocp-Apim-Subscription-Key header (KHAYA_API_KEY env var), ?language=
query param, response body is a bare JSON-encoded string (no
per-segment confidence data -- pass None for avg_logprob/no_speech_prob/
compression_ratio in RawSegment, same as w2vbert.py). requests is
already listed in requirements.txt for this.

Deliberately explicit-only (auto_eligible=False): falling back to a
local Whisper model with no real Twi support on failure would silently
produce confident English-shaped garbage, worse than dropping the
utterance -- see transcriber.py's decode(), which never pairs an
explicit-only backend with a fallback.
"""

from typing import List, Optional

import numpy as np

from .base import ASRBackend, RawSegment


class KhayaBackend(ASRBackend):
    name = "khaya"
    io_bound = True
    auto_eligible = False

    def load(self) -> None:
        raise NotImplementedError(
            "backend='khaya' is a placeholder -- see app/asr/backends/"
            "khaya.py's module docstring for the reference implementation "
            "(commit 9b274ac) to port in."
        )

    def transcribe(self, audio: np.ndarray) -> Optional[List[RawSegment]]:
        raise NotImplementedError(
            "backend='khaya' is a placeholder -- see app/asr/backends/"
            "khaya.py's module docstring for the reference implementation "
            "(commit 9b274ac) to port in."
        )
