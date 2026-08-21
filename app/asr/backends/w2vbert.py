"""
app/asr/backends/w2vbert.py
==============================
Placeholder for a free, fully offline Twi ASR fallback (no API key, no
quota) -- for when Khaya is unavailable. Not a recommended primary
choice: meaningfully worse accuracy than Khaya. Not implemented yet;
this file exists so a real implementation is "fill in load()/
transcribe() here," never a change to transcriber.py or any English
backend.

A prior working implementation existed and was measured against real
audio before being reverted along with an unrelated bad merge -- see
commit 9b274ac ("Add Twi ASR backends (Khaya + offline fallback) and
fuzzy reference matching") for the reference implementation:
ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1, converted to CTranslate2
(ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1_farmerline-ct2) for ~2x
faster CPU inference than the plain ONNX build -- measured ~5.9s vs
~13s per 7s clip on the original project hardware, at
intra_threads=16 (found to be the sweet spot; higher measured worse
from thread contention). Measured at 73.6% WER on that project's own
30-sentence test set -- worse than Khaya, but far better than two
Whisper-based Akan candidates that measured 93-96% WER. No
per-segment confidence data (pass None for avg_logprob/no_speech_prob/
compression_ratio in RawSegment, same as khaya.py). ctranslate2 and
transformers are already listed in requirements.txt for this.

Deliberately explicit-only (auto_eligible=False) -- same reasoning as
khaya.py.
"""

from typing import List, Optional

import numpy as np

from .base import ASRBackend, RawSegment


class W2VBertBackend(ASRBackend):
    name = "w2vbert"
    io_bound = False
    auto_eligible = False

    def load(self) -> None:
        raise NotImplementedError(
            "backend='w2vbert' is a placeholder -- see app/asr/backends/"
            "w2vbert.py's module docstring for the reference implementation "
            "(commit 9b274ac) to port in."
        )

    def transcribe(self, audio: np.ndarray) -> Optional[List[RawSegment]]:
        raise NotImplementedError(
            "backend='w2vbert' is a placeholder -- see app/asr/backends/"
            "w2vbert.py's module docstring for the reference implementation "
            "(commit 9b274ac) to port in."
        )
