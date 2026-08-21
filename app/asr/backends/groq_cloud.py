"""
app/asr/backends/groq_cloud.py
================================
Hosted Whisper decode via Groq's API -- the primary backend whenever
GROQ_API_KEY is set (see app/asr/transcriber.py's module docstring for
why cloud is treated as primary, not just a GPU-less fallback).
"""

import logging
import os

import numpy as np

from .base import ASRBackend, RawSegment, encode_wav

log = logging.getLogger(__name__)


class GroqCloudBackend(ASRBackend):
    name = "cloud"
    io_bound = True
    auto_eligible = True

    def load(self) -> None:
        try:
            from groq import Groq
        except ImportError as e:
            raise ImportError(
                "backend='cloud' (or 'auto' with GROQ_API_KEY set) requires "
                "the groq package: pip install groq"
            ) from e

        api_key = self.config.groq_api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "backend='cloud' was requested but no GROQ_API_KEY is set. "
                "Set the GROQ_API_KEY environment variable, or pass "
                "Config(backend='local') to run locally instead."
            )

        self._client = Groq(api_key=api_key)

        print(
            f"\n[Cloud backend] using Groq's hosted "
            f"'{self.config.groq_model}' for transcription.\n"
            "Each utterance's audio will be sent to Groq's API.\n"
        )

        self._warm_up()

    def _warm_up(self):
        """Fires one throwaway request at load time so the TLS/HTTP
        connection to Groq is already established before the first real
        utterance needs it. Measured directly: the first request on a
        fresh client pays a real, one-time connection-setup cost
        (585-1337ms observed) that every later request doesn't (settles
        to ~440-500ms). Best-effort -- a failure here (no internet yet,
        key not valid) just means the first real utterance pays the same
        cost it always would have."""
        try:
            dummy = np.zeros(1600, dtype=np.float32)
            self.transcribe(dummy)
        except Exception as e:
            log.debug("Cloud connection warm-up skipped: %s", e)

    def transcribe(self, audio: np.ndarray):
        """Returns None on request failure (network drop, outage, rate
        limit) so the caller can fall back -- see transcriber.py's
        decode()."""
        wav_bytes = encode_wav(audio, self.config.sample_rate)

        kwargs = dict(
            file=("utterance.wav", wav_bytes),
            model=self.config.groq_model,
            language=self.config.language,
            temperature=0.0,
            response_format="verbose_json",
        )
        # Omitted entirely rather than passed as prompt=None -- the SDK
        # forwards kwargs into a multipart form, which isn't guaranteed
        # to treat a literal None the same as the field being absent.
        if self.config.initial_prompt:
            kwargs["prompt"] = self.config.initial_prompt

        try:
            response = self._client.audio.transcriptions.create(**kwargs)
        except Exception as e:
            log.warning("Groq transcription request failed: %s", e)
            return None

        segments = getattr(response, "segments", None) or []
        out = []

        for seg in segments:
            get = seg.get if isinstance(seg, dict) else (
                lambda k, d=None: getattr(seg, k, d)
            )
            raw = (get("text", "") or "").strip()
            if not raw:
                continue
            out.append(RawSegment(
                text=raw,
                avg_logprob=get("avg_logprob", 0.0),
                no_speech_prob=get("no_speech_prob", 0.0),
                compression_ratio=get("compression_ratio", 0.0),
            ))

        # verbose_json should always carry segments, but fall back to the
        # top-level text rather than silently dropping the utterance if a
        # future API response ever omits them. bypass_gates=True since
        # this has no per-segment data for the gates to evaluate anyway,
        # matching the old _decode_cloud's behavior of appending this
        # fallback unconditionally, after (not through) the gating loop.
        if not out:
            top_level = getattr(response, "text", "") or ""
            if top_level.strip():
                out = [RawSegment(text=top_level.strip(), bypass_gates=True)]

        return out
