"""
app/asr/transcriber.py
======================

Offline real-time sermon transcriber for Bible AI.
Loads Faster-Whisper locally from:
models/faster-whisper/<model_size>

Segmentation is signal-driven (silence-endpointed), not fixed-interval.
Audio accumulates into a per-utterance buffer while the speaker is
talking; a sustained pause (endpoint_silence_ms) finalizes and dispatches
that exact utterance for transcription immediately. This replaces the
old design (a fixed 4.5s rolling window re-decoded every 0.9s regardless
of where the speaker was in their sentence), which fed Whisper trailing
dead air inside the same clip as real speech — a well-documented Whisper
hallucination trigger (the model "completes" the silence with a fluent,
fabricated continuation instead of correctly producing nothing).

It also means fast and slow preachers are handled by the same mechanism
automatically, with no mode switch: short pauses -> short segments
dispatched quickly; long, deliberate pauses -> the segment just runs a
bit longer before its own natural break triggers a dispatch.

Two refinements protect WER at both ends of the speaking-rate range,
still with no mode switch:
- Slow, pausy delivery (word ... pause ... word) would otherwise get
  finalized on every single endpoint_silence_ms gap, producing a stream
  of 1-3 word fragments that Whisper decodes poorly (too little context
  to disambiguate a word from). Short utterances get a longer grace
  period before finalizing instead — see Config.min_context_seconds /
  short_utterance_grace_ms. If the speaker resumes within that window,
  the audio merges into the SAME utterance automatically (see
  _audio_callback) — no separate merge step needed.
- Fast, continuous delivery that never pauses past endpoint_silence_ms
  still gets forced-cut at max_utterance_seconds, but the cut point is
  chosen at the quietest recent moment (a real syllable gap) instead of
  the exact sample count reached — see _finalize_soft_cut. Avoids
  slicing through a word, which corrupts both sides of the cut.

Tuned for a balance of speed and accuracy: beam_size was raised back to
5 (see its own comment below) now that accuracy is the priority.
endpoint_silence_ms remains the first lever to reach for if latency
needs to come back down.

Example:
Config(model_size="base.en")
loads:
models/faster-whisper/base.en

Backend selection (Config.backend, default "auto"):
Local decode on a CPU-only (or GPU-with-missing-CUDA-runtime) machine is
the slow/inaccurate case this was built to avoid, but a from-scratch
cloud rewrite isn't needed either — the per-utterance dispatch below
already hands off one self-contained audio clip at a time, so a cloud
call is a drop-in swap for the same slot. "auto" treats Groq's hosted
Whisper API (https://console.groq.com, GROQ_API_KEY env var) as the
PRIMARY backend whenever a key is configured, by deliberate choice — not
just a fallback for GPU-less machines. Local (GPU if usable, else CPU)
is only used when no key is set. GPU usability itself is validated with
a real dummy forward pass at load time (see _build_local_model) — a
device being *detected* (ctranslate2.get_cuda_device_count() > 0)
doesn't mean it's actually usable; the CUDA runtime libraries can be
missing even with a working driver, which only surfaces as a
RuntimeError once real inference is attempted, not at model
construction. "local" / "cloud" force one path explicitly. Segment-level
anti-hallucination gates (avg_logprob, no_speech_prob, compression_ratio,
prompt-echo) are applied identically on both paths, since Groq's
verbose_json response includes the same per-segment fields faster-whisper
does.

Runtime recovery: if a cloud request fails mid-session (network drop,
outage, rate limit — effectively "no internet"), _fallback_to_local
drops that and subsequent utterances to local decode immediately, no
restart needed. Unlike a one-way circuit breaker, this isn't permanent —
_should_probe_cloud/_recover_to_cloud periodically retry cloud in the
background (Config.cloud_retry_interval_s) and switch back automatically
the moment a probe succeeds, so a session started with cloud as primary
returns to cloud on its own once internet/Groq comes back.
"""

import argparse
import io
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, get_speech_timestamps

log = logging.getLogger(__name__)

# A bare spoken number ("ten", "sixteen", "twenty seven") — self-contained
# here rather than importing from app.retrieval.reference_extractor, which
# has its own larger copy for full reference parsing. Kept deliberately
# small: this only needs to recognize "this utterance IS a number," not
# parse its value.
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred","verse"
}


@dataclass
class Config:
    model_size: str = "base.en"
    # "auto" resolves to "cuda" if a usable GPU is found (see module
    # docstring), else "cpu" — same behavior CPU-only machines had before.
    device: str = "auto"
    # "auto" resolves to "float16" on cuda, "int8" on cpu.
    compute_type: str = "auto"

    # "auto" | "local" | "cloud" — see module docstring.
    backend: str = "auto"
    # Full large-v3, not "-turbo" — Groq's API doesn't expose beam_size/
    # best_of the way local faster-whisper does, so model choice is the
    # only real accuracy lever left for the cloud path. Turbo trades
    # accuracy for speed, but Groq's LPU hardware makes even the full
    # model fast, so that trade isn't worth it once cloud is the primary
    # backend rather than a speed-driven fallback.
    groq_model: str = "whisper-large-v3"
    # Falls back to the GROQ_API_KEY env var when None.
    groq_api_key: Optional[str] = None
    # After a cloud request fails (network drop, outage, rate limit) and
    # the session falls back to local decode, how long to wait before
    # probing cloud again on a subsequent utterance. Recovery is
    # automatic — no restart needed once internet/Groq comes back. See
    # _should_probe_cloud/_recover_to_cloud.
    cloud_retry_interval_s: float = 30.0

    language: Optional[str] = "en"

    sample_rate: int = 16000
    channels: int = 1
    device_index: Optional[int] = None

    # ── Signal-driven segmentation ──────────────────────────
    # A sustained silence of this length marks the end of an utterance —
    # the trigger for dispatching it to transcription. Every utterance
    # pays this in full before decode even starts, so it's the first
    # thing to reach for when tuning speed — unlike beam_size, which was
    # measured directly on this machine (3 beam sizes x 4 runs each,
    # base.en, CPU int8) and showed NO reliable difference (938ms / 983ms
    # / 880ms for beam_size 5/4/3 — noise, not a trend), so it was left
    # alone rather than traded away for a speed win that isn't real here.
    # Lowered 450->350->250ms across two sessions. 250, not lower, because
    # this is exactly the same kind of unverified-against-real-audio
    # change every prior tuning of this constant has been (see this
    # file's own recurring caveat) — 100ms further off an already-tight
    # value risks clipping real speech with no real audio in this
    # environment to catch it if it does. Lower further only against real
    # testing.
    endpoint_silence_ms: int = 250
    # Hard cap so one long uninterrupted run of speech (no pauses) still
    # gets chunked and dispatched rather than growing unbounded.
    max_utterance_seconds: float = 10.0
    # Below this, a "speech" blip is almost certainly a click/pop, not a
    # real utterance — skip transcribing it entirely.
    min_utterance_seconds: float = 0.35

    # ── Slow-speech "hangover" grace (deliberate, pausy delivery) ──────
    # If the utterance so far is still under min_context_seconds when the
    # normal endpoint_silence_ms threshold is hit, wait longer
    # (short_utterance_grace_ms) before finalizing instead of cutting
    # immediately — gives a speaker who pauses for emphasis between short
    # phrases a chance to continue into the SAME utterance rather than
    # being chopped into a 1-3 word fragment. Once an utterance already
    # has this much context, normal endpoint_silence_ms applies again, so
    # a genuinely fast speaker's segmentation is unaffected.
    min_context_seconds: float = 0.9
    # Scaled down proportionally with endpoint_silence_ms above (was
    # 700ms against a 350ms endpoint_silence_ms, same ~2x ratio here) —
    # short utterances (a bare verse number, "Amen") still get
    # meaningfully longer than normal to keep merging into the same
    # utterance if the speaker resumes, just not as long as before.
    short_utterance_grace_ms: float = 500.0

    # ── Fast-speech soft cut (continuous, no natural pause) ────────────
    # How many trailing audio blocks _finalize_soft_cut searches for the
    # quietest one when max_utterance_seconds forces a cut. Blocks are
    # ~100ms each (see start()'s blocksize), so 5 = a 500ms lookback
    # window — enough to find a real syllable gap even in fast,
    # continuous speech.
    soft_cut_lookback_blocks: int = 5

    # Raised back to 5 — accuracy is the current priority over raw
    # latency (see module docstring). Was lowered to 3 for speed when
    # utterances were already short, clean, endpoint-bounded clips; drop
    # back toward 3 if latency becomes the bottleneck again for a given
    # deployment.
    beam_size: int = 5
    # best_of only applies on temperature>0 fallback attempts. temperature
    # is pinned to 0.0 below (see why), so this is currently inert — left
    # at faster-whisper's default in case temperature fallback is ever
    # deliberately re-enabled.
    best_of: int = 5

    # PINNED to a single value. A temperature *fallback ladder* was tried
    # here and reverted — retrying at higher temperature is a well-known
    # Whisper hallucination trigger: on silence/noise/unclear audio, a
    # low-temperature pass correctly produces little or nothing, but a
    # higher-temperature retry can sample a fluent, plausible-sounding
    # sentence that was never actually said. That's worse than dropping a
    # hard segment, especially feeding a system that then goes looking
    # for a Bible verse in whatever it transcribed.
    temperature: tuple = (0.0,)

    # Guards against a distinct Whisper failure mode from the silence
    # hallucination above: a degenerate decode loop that repeats the same
    # word/short phrase dozens or hundreds of times ("hey, hey, hey, ...").
    # Seen on real sermon audio — unlike the silence case, these segments
    # can have a perfectly good avg_logprob/no_speech_prob (the model is
    # "confident" about each repeated token), so they sail through the
    # gates above untouched; a dedicated fix is needed. repetition_penalty
    # and no_repeat_ngram_size operate on logits at a fixed temperature —
    # they don't add the "sample a fluent fabrication" risk that a
    # temperature fallback ladder would (see the comment on `temperature`
    # above for why that was rejected). no_repeat_ngram_size=4 hard-caps
    # any exact 4-token sequence to appearing once, which in turn caps a
    # single repeated word/phrase to at most ~4 consecutive occurrences —
    # comfortably above genuine call-and-response repeats like "Amen,
    # Amen, Amen" (3x) while making a 100+ repeat run structurally
    # impossible. repetition_penalty=1.2 is a softer, complementary nudge
    # away from repeating recently-generated tokens at all.
    repetition_penalty: float = 1.2
    no_repeat_ngram_size: int = 4
    # Passed to faster-whisper's transcribe() for its own (currently inert
    # with a single pinned temperature — see _transcribe_utterance's own
    # post-decode check, which is what actually enforces this) fallback
    # bookkeeping, and reused as our own stricter post-decode gate.
    compression_ratio_threshold: float = 2.4

    silence_threshold: float = 0.0016

    min_words: int = 2

    cpu_threads: int = 8
    num_workers: int = 1

    # OFF by default. History: an earlier version listed concrete example
    # verses ("John chapter 3 verse 16", ...) as style examples, but an
    # initial_prompt conditions the decoder — on unclear/silent audio the
    # model would echo one of those exact phrases back as if it had been
    # spoken. Switched to a full prose framing sentence, then to a bare
    # ~18-book word list, then shrunk to just the 5 books that were the
    # ORIGINAL justification (one-word names that double as common English
    # words: "Job", "Acts", "Numbers", "Judges", "Titus") — each step
    # trying to keep the proper-noun disambiguation benefit while cutting
    # the echo surface. All of them still leaked on real sermon audio.
    #
    # Direct A/B test on the same ~20-line sermon clip settled it: WITH
    # even the shrunk 5-book prompt, 4 of ~19 output lines were the prompt
    # sentence itself verbatim ("The preacher may reference Bible books
    # such as Job, Acts, Numbers, Judges, Titus, and verse numbers.")
    # replacing real spoken content, plus 2 more garbled paraphrases of it
    # ("Tithes, Numbers, and Verses") — roughly a third of the transcript
    # was fabricated. WITHOUT any prompt, the same clip transcribed
    # cleanly with zero fabricated content — every deviation from ground
    # truth was an ordinary mishearing (e.g. "Christ is the one who
    # created us. self was without sin." for "Christ Himself was without
    # sin."), not invented text. The proper-noun disambiguation benefit
    # this was meant to buy never showed up as a win in that test either.
    # Net negative — left here as an opt-in for anyone who wants to
    # accept that tradeoff, not the default. See also _has_speech (a VAD
    # pre-filter that keeps true-silence/ambient-noise clips from ever
    # reaching the decoder) and _is_prompt_echo (a defense-in-depth guard
    # that drops a segment if it's caught echoing whatever prompt IS
    # configured) for the rest of this hallucination-mitigation story.
    initial_prompt: Optional[str] = None


class BibleAITranscriber:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

        self._groq_client = None
        self.backend = self._resolve_backend()
        # The backend chosen at startup — self.backend can drop to "local"
        # at runtime after a cloud failure (see _fallback_to_local), but
        # this never changes, so _should_probe_cloud knows whether it's
        # worth trying to recover back to cloud at all.
        self._preferred_backend = self.backend
        self._next_cloud_retry_at = 0.0

        if self.backend == "cloud":
            self._init_cloud_backend()
        else:
            self._init_local_backend()

        # ── Endpointing state — touched only from the sounddevice audio
        # callback thread, which sounddevice guarantees is never called
        # re-entrantly/concurrently for a single stream, so no lock is
        # needed for correctness. Kept as plain attributes for clarity.
        self._state = "SILENCE"                      # "SILENCE" | "SPEAKING"
        self._utterance_chunks: List[np.ndarray] = []
        self._speech_started_at = 0.0
        self._silence_run_ms = 0.0
        # Accumulated *audio* duration of the current utterance, in
        # samples — not wall-clock time. In real-time capture the two
        # track each other closely, but sample count is exact and
        # doesn't depend on callbacks arriving at a steady real-time
        # cadence (e.g. under CPU load, or when fed synthetically for
        # testing), so max_utterance_seconds is enforced against this.
        self._utterance_samples = 0

        self.stop_event = threading.Event()
        self.stream = None
        self.callback_fn: Optional[Callable[[str], None]] = None

        # Bounded pool for per-utterance decode dispatch — see _finalize().
        # self._dispatch_workers tracks the size actually in use so
        # _resize_executor_for_backend (called after a runtime cloud<->
        # local switch) knows whether a resize is even needed.
        self._dispatch_workers = self._dispatch_worker_count()
        self._executor = ThreadPoolExecutor(
            max_workers=self._dispatch_workers,
            thread_name_prefix="bible-ai-transcribe",
        )

        self.last_text = ""
        self.last_emit_time = 0.0

    def set_callback(self, fn: Callable[[str], None]):
        self.callback_fn = fn

    def _cuda_available(self) -> bool:
        """Checked via ctranslate2 itself, not nvidia-smi — a GPU can be
        physically present and still unusable by WhisperModel if the CUDA
        runtime libraries ctranslate2 needs aren't installed, so asking
        ctranslate2 directly is the only check that reflects reality."""
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    def _resolve_backend(self) -> str:
        backend = self.config.backend

        if backend not in ("auto", "local", "cloud"):
            raise ValueError(
                f"Config.backend must be 'auto', 'local', or 'cloud', got {backend!r}"
            )

        if backend != "auto":
            return backend

        # Groq is the primary path whenever a key is configured — local
        # (GPU if available, else CPU) is the fallback for sessions/
        # machines with no key set, not the other way around.
        if self.config.groq_api_key or os.environ.get("GROQ_API_KEY"):
            return "cloud"

        return "local"

    def _dispatch_worker_count(self) -> int:
        """How many utterances can decode concurrently — sized per
        backend, not one blanket number, because the two have different
        bottlenecks. Local decode is CPU-bound (each CTranslate2 decode is
        already internally multithreaded via cpu_threads), so more worker
        threads than that just contend for the same cores; 2 matches the
        realistic concurrency ceiling there (one utterance still decoding
        while at most one more has just been endpointed or force-cut by
        max_utterance_seconds).

        Cloud decode is I/O-bound instead — a worker spends almost all of
        its time blocked waiting on Groq's HTTP response, not touching
        local CPU at all, so a higher count costs nothing locally.
        Measured directly against the real API (whisper-large-v3, 2.5s
        clips): 450-1000ms round-trip, well above what local decode
        typically takes — at only 2 workers, a fast preacher's utterances
        can easily endpoint faster than one round-trip completes and
        queue up behind the cap, adding real, avoidable latency. 4 is a
        conservative raise (not unbounded — still bounded per the
        original design's own goal, see the comment on self._executor)
        rather than a number tuned against a specific, unknown-to-us Groq
        rate limit."""
        return 4 if self.backend == "cloud" else 2

    def _resize_executor_for_backend(self):
        """Keep the dispatch pool sized for whichever backend is
        ACTUALLY active right now, not just at startup. self.backend can
        flip mid-session now (_fallback_to_local / _recover_to_cloud), and
        running local's CPU-bound decode at cloud's worker count would
        oversubscribe CPU threads — this machine has cpu_threads=8 per
        worker, so 4 concurrent local decodes alone would want up to 32
        OS threads, competing for far fewer logical cores. Never passes
        cancel_futures=True here (unlike stop()) — a network blip
        resizing the pool must not silently drop an utterance that was
        already queued; the old executor just stops accepting new
        submissions and finishes what it already has, while new work
        goes to the freshly-sized one."""
        target = self._dispatch_worker_count()
        if target == self._dispatch_workers:
            return
        old_executor = self._executor
        self._dispatch_workers = target
        self._executor = ThreadPoolExecutor(
            max_workers=target, thread_name_prefix="bible-ai-transcribe"
        )
        old_executor.shutdown(wait=False)

    def _init_local_backend(self):
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )

        model_path = os.path.join(
            project_root,
            "models",
            "faster-whisper",
            self.config.model_size,
        )

        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"\nWhisper model folder not found:\n{model_path}\n\n"
                "Make sure your model is inside:\n"
                "models/faster-whisper/base.en\n"
            )

        device = self.config.device
        if device == "auto":
            device = "cuda" if self._cuda_available() else "cpu"

        print("\nLoading Faster-Whisper model locally...")
        print("Model path:", model_path)
        print("Device:", device)

        try:
            self.model = self._build_local_model(model_path, device)
        except (ValueError, RuntimeError) as e:
            # A CUDA device being *detected* (ctranslate2.get_cuda_device_
            # count() > 0) doesn't mean it's actually usable — the CUDA
            # runtime libraries (cuBLAS/cuDNN) can be missing even with a
            # working driver and a visible GPU, e.g. no CUDA Toolkit and
            # no nvidia-cublas-cu12/nvidia-cudnn-cu12 pip packages
            # installed. That failure only surfaces once a real forward
            # pass runs (see _build_local_model's validation decode), not
            # at model construction — without this fallback, every single
            # utterance would silently fail forever (caught by
            # _transcribe_utterance's broad except), which looks
            # indistinguishable from "not transcribing at all."
            if device != "cpu":
                print(
                    f"\n[GPU unusable] {e}\n"
                    "Falling back to CPU for this run. To actually use the "
                    "GPU, install its CUDA runtime libraries:\n"
                    "  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
                )
                self.model = self._build_local_model(model_path, "cpu")
            else:
                raise

        print("Model loaded successfully.\n")

    def _build_local_model(self, model_path: str, device: str) -> WhisperModel:
        # Not every CUDA GPU actually supports float16 efficiently — older
        # (pre-Turing/Pascal-and-earlier) cards raise a ValueError from
        # ctranslate2 at load time rather than silently falling back. Try
        # a small preference cascade instead of assuming the fastest
        # option always works; explicit user-set values are tried as-is,
        # with no cascade, so a deliberate choice is never silently
        # overridden.
        if self.config.compute_type == "auto":
            candidates = (
                ["float16", "int8_float16", "int8"] if device == "cuda"
                else ["int8"]
            )
        else:
            candidates = [self.config.compute_type]

        last_error = None
        for compute_type in candidates:
            print("Trying compute type:", compute_type)
            try:
                model = WhisperModel(
                    model_path,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=self.config.cpu_threads,
                    num_workers=self.config.num_workers,
                )
                # Force one real forward pass now rather than lazily on
                # the first real utterance — a missing CUDA runtime
                # library raises RuntimeError only once encode() actually
                # runs, not at construction, so construction succeeding
                # is not proof the device is usable. vad_filter=False so
                # this silent dummy clip can't get skipped before
                # reaching encode().
                dummy = np.zeros(1600, dtype=np.float32)
                segments, _ = model.transcribe(
                    dummy, language=self.config.language, vad_filter=False,
                )
                list(segments)
                print("Compute type in use:", compute_type)
                return model
            except (ValueError, RuntimeError) as e:
                last_error = e
                continue

        raise last_error

    def _init_cloud_backend(self):
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

        self._groq_client = Groq(api_key=api_key)

        reason = (
            "backend='cloud' was set explicitly" if self.config.backend == "cloud"
            else "GROQ_API_KEY is set - cloud is the primary backend"
        )
        print(
            f"\n[Cloud backend] {reason} - using Groq's hosted "
            f"'{self.config.groq_model}' for transcription.\n"
            "Each utterance's audio will be sent to Groq's API.\n"
        )

        self._warm_up_cloud_connection()

    def _warm_up_cloud_connection(self):
        """Fires one throwaway request at startup so the TLS/HTTP
        connection to Groq is already established before the first real
        utterance needs it. Measured directly on this machine: the first
        request on a fresh client pays a real, one-time connection-setup
        cost (585-1337ms observed) that every later request on the same
        client doesn't (settles to ~440-500ms) — without this, that whole
        extra cost lands on whatever utterance happens to be first (or
        first after a long enough gap that the connection dropped), which
        is exactly the "it waits for something" pattern reported live.
        Best-effort: a failure here (no internet yet, key not valid) just
        means the first real utterance pays the same cost it always would
        have — _decode_cloud's own error handling and _fallback_to_local
        are unaffected either way, so this never blocks startup."""
        try:
            dummy = np.zeros(1600, dtype=np.float32)
            self._decode_cloud(dummy)
        except Exception as e:
            log.debug("Cloud connection warm-up skipped: %s", e)

    def _rms(self, audio: np.ndarray) -> float:
        if audio.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(audio ** 2)))

    def _has_speech(self, audio: np.ndarray) -> bool:
        """Real speech/non-speech check via faster-whisper's bundled
        Silero VAD (a small ONNX model, no full Whisper load needed) —
        run before EVERY decode, on both backends, not just local's own
        vad_filter=True (which only ever gated the local path). The RMS-
        based silence_threshold in _audio_callback is a cheap volume gate
        only: continuous ambient noise (room tone, HVAC hum, breath,
        mic handling) is often louder than that threshold without being
        speech, so it still gets endpointed into an "utterance" and sent
        to Whisper — which reliably hallucinates a fluent, fabricated
        sentence on it instead of correctly producing nothing (see module
        docstring). That's what was surfacing as unprompted "and
        Ezekiel.", "and many more.", and initial_prompt word-list echoes
        on real sermon audio: real pauses, not clean silence. Cheap
        enough to run unconditionally before every decode regardless of
        which backend ultimately handles it."""
        timestamps = get_speech_timestamps(
            audio,
            VadOptions(
                threshold=0.5,
                min_silence_duration_ms=250,
                speech_pad_ms=200,
            ),
            sampling_rate=self.config.sample_rate,
        )
        return len(timestamps) > 0

    def _normalize_audio(self, audio: np.ndarray) -> np.ndarray:
        audio = audio.astype(np.float32)

        if audio.size == 0:
            return audio

        audio = audio - np.mean(audio)

        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak

        return np.clip(audio, -1.0, 1.0)

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()

        fixes = {
            "mathew": "Matthew",
            "mathews": "Matthew",
            "revelations": "Revelation",
            "holy spirit": "Holy Spirit",
            "jesus christ": "Jesus Christ",
            "amen": "Amen",
            "hallelujah": "Hallelujah",
        }

        for wrong, right in fixes.items():
            text = re.sub(
                rf"\b{re.escape(wrong)}\b",
                right,
                text,
                flags=re.IGNORECASE,
            )

        return text.strip()

    def _word_count(self, text: str) -> int:
        return len(re.findall(r"\b\w+\b", text))

    def _is_bare_number(self, text: str) -> bool:
        """True if the utterance is just a number and nothing else — the
        classic "the speaker paused, then said the verse number on its
        own" case (natural sermon cadence: "Proverbs 31" ... "10"). A bare
        number is meaningful in this domain regardless of word count, so
        it's exempted from the min_words floor below, which exists to
        catch meaningless single-word noise blips, not this."""
        words = re.findall(r"\b\w+\b", text.lower())
        if not words:
            return False
        return all(w.isdigit() or w in _NUMBER_WORDS for w in words)

    def _is_bad_output(self, text: str) -> bool:
        """True only for genuinely empty output. Every other heuristic
        below (too short, known filler phrase, repetition loop) used to
        drop the utterance outright — now it only logs, since each one
        was confirmed to also catch real, legitimately short or unusually
        -phrased speech (a spoken verse reference among them) with no
        trace left anywhere once dropped."""
        if not text:
            return True

        if (
            self._word_count(text) < self.config.min_words
            and not self._is_bare_number(text)
        ):
            log.warning(
                "%d word(s), below min_words=%d floor (and not a bare "
                "number) — keeping anyway: %r",
                self._word_count(text), self.config.min_words, text[:80],
            )

        bad_outputs = {
            "thank you for watching",
            "thanks for watching",
            "subscribe",
            "like and subscribe",
            "music",
            "applause",
            "[music]",
            "[applause]",
        }

        cleaned = text.lower().strip(" .,!?:;")
        if cleaned in bad_outputs:
            log.warning("Matches known filler output — keeping anyway: %r", text[:80])

        if self._has_repetition_loop(text):
            log.warning("Repetition loop — keeping anyway: %r", text[:80])

        return False

    def _has_repetition_loop(self, text: str) -> bool:
        """Third layer of defense against the decode-loop failure mode
        (see the Config.repetition_penalty/no_repeat_ngram_size and the
        compression_ratio gate in _transcribe_utterance) — a cheap,
        library-independent check on the final assembled text, in case a
        loop ever spans a segment boundary and dilutes any single
        segment's own compression_ratio below the threshold. 5+ of the
        exact same word back-to-back is not something real speech
        produces (genuine emphasis repeats like "Amen, Amen, Amen" top
        out around 3) — it's the signature of a stuck decoder."""
        words = re.findall(r"\b\w+\b", text.lower())

        run = 1
        for i in range(1, len(words)):
            if words[i] == words[i - 1]:
                run += 1
                if run >= 5:
                    return True
            else:
                run = 1

        return False

    # ========================================================
    # SIGNAL-DRIVEN SEGMENTATION
    # ========================================================
    # No duplicate-suppression here on purpose: with per-utterance
    # decoding (each utterance transcribed exactly once, never as an
    # overlapping re-decode of a sliding window) there's no re-decode
    # artifact left to dedupe. A genuine live repeat — "Amen, Amen",
    # "Good morning, good morning" — is two separate, real utterances
    # and both should be emitted.

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print("[Audio warning]", status)

        audio = indata.copy()

        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        else:
            audio = audio.reshape(-1)

        audio = audio.astype(np.float32)

        block_ms = (len(audio) / self.config.sample_rate) * 1000.0
        has_speech = self._rms(audio) >= self.config.silence_threshold

        if has_speech:
            if self._state == "SILENCE":
                self._state = "SPEAKING"
                self._utterance_chunks = []
                self._utterance_samples = 0
                self._speech_started_at = time.time()

            self._utterance_chunks.append(audio)
            self._utterance_samples += len(audio)
            self._silence_run_ms = 0.0

            # Hard cap: a long uninterrupted run of speech still gets
            # chunked, rather than growing unbounded and blowing the
            # latency budget on whatever comes after it. Measured in
            # accumulated audio samples, not wall-clock time — exact
            # regardless of callback timing.
            max_samples = self.config.max_utterance_seconds * self.config.sample_rate
            if self._utterance_samples >= max_samples:
                self._finalize_soft_cut()

        elif self._state == "SPEAKING":
            # Keep a little trailing silence — natural padding, not the
            # multi-second dead air the old fixed-window design used to
            # include.
            self._utterance_chunks.append(audio)
            self._silence_run_ms += block_ms

            # Short utterances get a longer grace period before finalizing
            # (see Config.min_context_seconds/short_utterance_grace_ms).
            # If speech resumes before that threshold is reached, the
            # `has_speech` branch above just keeps appending to this same
            # utterance — state never leaves SPEAKING, chunks never get
            # cleared — which is the entire merge mechanism, no separate
            # state needed.
            utterance_duration_s = (
                self._utterance_samples / self.config.sample_rate
            )
            finalize_threshold_ms = (
                self.config.short_utterance_grace_ms
                if utterance_duration_s < self.config.min_context_seconds
                else self.config.endpoint_silence_ms
            )

            if self._silence_run_ms >= finalize_threshold_ms:
                self._finalize(forced=False)

        # else: already SILENCE, nothing accumulating — no-op.

    def _finalize(self, forced: bool):
        """Dispatch the accumulated utterance for transcription and reset
        segmentation state for the next one. Called from the audio
        callback thread; the actual transcription runs on its own thread
        so this never blocks audio capture."""
        chunks = self._utterance_chunks
        started_at = self._speech_started_at

        self._utterance_chunks = []
        self._utterance_samples = 0
        self._silence_run_ms = 0.0

        if forced:
            # Speaker is still going — start the next segment immediately
            # instead of dropping to SILENCE.
            self._speech_started_at = time.time()
        else:
            self._state = "SILENCE"

        if not chunks:
            return

        audio = np.concatenate(chunks)
        duration_s = len(audio) / self.config.sample_rate

        if duration_s < self.config.min_utterance_seconds:
            return

        self._executor.submit(self._transcribe_utterance, audio, started_at)

    def _finalize_soft_cut(self):
        """Forced cut at max_utterance_seconds for one long, uninterrupted
        run of speech (a fast speaker who never pauses past
        endpoint_silence_ms) — but instead of slicing at the exact sample
        count reached, which lands mid-word/mid-syllable as often as not,
        search the last soft_cut_lookback_blocks blocks for the quietest
        one (almost always a real syllable gap even in continuous fast
        speech) and cut there instead. Audio after the cut point carries
        over to start the next utterance rather than being discarded —
        the speaker never actually paused, so nothing should be lost."""
        chunks = self._utterance_chunks
        lookback = min(len(chunks), self.config.soft_cut_lookback_blocks)

        if lookback <= 1:
            self._finalize(forced=True)
            return

        tail = chunks[-lookback:]
        rms_values = [self._rms(c) for c in tail]
        quietest_offset = int(np.argmin(rms_values))
        split_at = len(chunks) - lookback + quietest_offset + 1

        carry_over = chunks[split_at:]
        self._utterance_chunks = chunks[:split_at]

        self._finalize(forced=True)

        # started_at for this carried-over remainder is approximate (it
        # was actually spoken slightly before this reset, not "now") —
        # same approximation the original hard-cut forced path already
        # made; it only affects the logged latency figure, not the audio
        # or transcription itself.
        if carry_over:
            self._utterance_chunks = carry_over
            self._utterance_samples = sum(len(c) for c in carry_over)

    def _passes_segment_gates(self, raw: str, avg_logprob, no_speech_prob,
                               compression_ratio) -> bool:
        """Anti-hallucination signals, applied identically regardless of
        which backend produced the segment (see module docstring). Logs
        every low-confidence/repetitive/echoed segment it sees, but no
        longer drops any of them — real, quiet, or unusually-phrased
        speech (a spoken verse reference among them) was being silently
        discarded with zero trace, which is worse than letting an
        occasional genuine hallucination reach the transcript. See
        _is_bad_output for the equivalent utterance-level change."""
        if avg_logprob is not None and avg_logprob < -1.2:
            log.warning(
                "Low decode confidence — avg_logprob %.2f below -1.20, "
                "keeping anyway: %r",
                avg_logprob,
                raw[:80],
            )

        if no_speech_prob is not None and no_speech_prob > 0.80:
            log.warning(
                "Possible non-speech — no_speech_prob %.2f above 0.80, "
                "keeping anyway: %r",
                no_speech_prob,
                raw[:80],
            )

        # A highly repetitive segment ("hey, hey, hey, ...") gzip-
        # compresses far better than real speech, so a high ratio here is
        # a strong, independent repetition signal even with
        # repetition_penalty/no_repeat_ngram_size already applied at
        # decode time (local backend only — see below).
        if (
            compression_ratio is not None
            and compression_ratio > self.config.compression_ratio_threshold
        ):
            log.warning(
                "High compression ratio %.2f exceeds %.2f (possible "
                "repetition loop), keeping anyway: %r",
                compression_ratio,
                self.config.compression_ratio_threshold,
                raw[:80],
            )

        if self._is_prompt_echo(raw):
            log.warning(
                "Echoes the configured initial_prompt — possibly not "
                "real speech, keeping anyway: %r",
                raw[:80],
            )

        return True

    def _is_prompt_echo(self, text: str) -> bool:
        """Catches Whisper regurgitating Config.initial_prompt itself as
        the "transcription" of a segment it found hard to decode — the
        dominant failure mode measured on real sermon audio (see the long
        comment on Config.initial_prompt): with even a short, tightly-
        scoped prompt, roughly a third of one test transcript's lines were
        the prompt sentence verbatim, replacing real content. No-op when
        no prompt is configured (the default). Deliberately checks for
        near-WHOLE-prompt containment only, not fuzzy word overlap — a
        genuine, on-topic reference to one of the primed book names ("the
        book of Acts") would trivially share a couple of words with the
        prompt by chance, but matching (or being contained in) the entire
        artificial prompt sentence essentially never happens in real,
        unrelated sermon speech."""
        prompt = (self.config.initial_prompt or "").strip().lower()
        if not prompt:
            return False

        normalized = text.strip().lower().rstrip(".!?")
        return normalized == prompt or normalized in prompt

    def _decode_local(self, audio: np.ndarray) -> List[str]:
        segments, _ = self.model.transcribe(
            audio,
            language=self.config.language,
            task="transcribe",
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            temperature=list(self.config.temperature),
            vad_filter=True,
            vad_parameters={
                # Raised from 0.35 — a stricter speech/non-speech cut
                # means less ambient noise and silence ever reaches the
                # decoder in the first place, which is exactly the
                # audio that's most prone to being hallucinated into a
                # fluent-sounding but fabricated sentence.
                "threshold": 0.5,
                "min_silence_duration_ms": 250,
                "speech_pad_ms": 200,
            },
            condition_on_previous_text=False,
            initial_prompt=self.config.initial_prompt,
            no_speech_threshold=0.60,
            log_prob_threshold=-1.2,
            compression_ratio_threshold=self.config.compression_ratio_threshold,
            repetition_penalty=self.config.repetition_penalty,
            no_repeat_ngram_size=self.config.no_repeat_ngram_size,
            without_timestamps=True,
            word_timestamps=False,
        )

        parts = []

        for seg in segments:
            raw = getattr(seg, "text", "").strip()

            if not raw:
                continue

            if not self._passes_segment_gates(
                raw,
                getattr(seg, "avg_logprob", 0.0),
                getattr(seg, "no_speech_prob", 0.0),
                getattr(seg, "compression_ratio", 0.0),
            ):
                continue

            parts.append(raw)

        return parts

    def _encode_wav(self, audio: np.ndarray) -> bytes:
        buf = io.BytesIO()
        sf.write(buf, audio, self.config.sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return buf.read()

    def _ensure_local_ready(self) -> bool:
        """Lazily loads the local model the first time it's actually
        needed as a fallback (a cloud-primary session never pays this
        startup cost unless/until cloud actually fails). Returns whether a
        usable local model is now loaded. Safe to call repeatedly —
        no-ops once self.model already exists."""
        if getattr(self, "model", None) is not None:
            return True

        try:
            self._init_local_backend()
            return True
        except Exception as e:
            log.error("Local fallback failed to initialize: %s", e)
            return False

    def _fallback_to_local(self, reason: str):
        """Called when a cloud request fails at runtime (network drop,
        outage, rate limit, no internet). Drops the *current* backend to
        local for this and subsequent utterances, but — unlike a one-way
        circuit breaker — doesn't give up on cloud permanently: see
        _should_probe_cloud/_recover_to_cloud, which periodically retry
        cloud in the background and switch back automatically once it
        starts working again. Only flips self.backend if local actually
        loads successfully; if it also fails (e.g. no local model files
        present) stays on cloud and keeps retrying it, since there's no
        working fallback to drop to anyway."""
        if self.backend != "cloud":
            return

        if not self._ensure_local_ready():
            return

        print(f"\n[Cloud backend failed] {reason} — using local "
              f"transcription until cloud recovers.\n")
        self.backend = "local"
        self._schedule_next_cloud_retry()
        self._resize_executor_for_backend()

    def _should_probe_cloud(self) -> bool:
        """True when it's worth spending one utterance's decode probing
        whether cloud has recovered: only sessions that started with cloud
        as the preferred backend ever probe (an explicit backend="local"
        session never touches the network), only while currently running
        on the local fallback, and only at cloud_retry_interval_s
        intervals — probing every single utterance would burn the latency
        budget on a request that's likely to keep failing during a real
        outage."""
        return (
            self._preferred_backend == "cloud"
            and self.backend == "local"
            and self._groq_client is not None
            and time.time() >= self._next_cloud_retry_at
        )

    def _schedule_next_cloud_retry(self):
        self._next_cloud_retry_at = time.time() + self.config.cloud_retry_interval_s

    def _recover_to_cloud(self):
        if self.backend != "cloud":
            print("\n[Cloud backend recovered] Switching back to cloud "
                  "transcription.\n")
            self.backend = "cloud"
            self._resize_executor_for_backend()
        self._next_cloud_retry_at = 0.0

    def _decode_cloud(self, audio: np.ndarray) -> Optional[List[str]]:
        """Returns None on request failure (network drop, outage, rate
        limit) so callers can decide what to do — this function has no
        side effects on self.backend, which lets it double as both the
        primary cloud path and a background recovery probe."""
        wav_bytes = self._encode_wav(audio)

        kwargs = dict(
            file=("utterance.wav", wav_bytes),
            model=self.config.groq_model,
            language=self.config.language,
            temperature=0.0,
            response_format="verbose_json",
        )
        # Omitted entirely rather than passed as prompt=None — the SDK
        # forwards kwargs into a multipart form, which isn't guaranteed to
        # treat a literal None the same as the field being absent.
        if self.config.initial_prompt:
            kwargs["prompt"] = self.config.initial_prompt

        try:
            response = self._groq_client.audio.transcriptions.create(**kwargs)
        except Exception as e:
            log.warning("Groq transcription request failed: %s", e)
            return None

        segments = getattr(response, "segments", None) or []
        parts = []

        for seg in segments:
            get = seg.get if isinstance(seg, dict) else (
                lambda k, d=None: getattr(seg, k, d)
            )
            raw = (get("text", "") or "").strip()

            if not raw:
                continue

            if not self._passes_segment_gates(
                raw,
                get("avg_logprob", 0.0),
                get("no_speech_prob", 0.0),
                get("compression_ratio", 0.0),
            ):
                continue

            parts.append(raw)

        # verbose_json should always carry segments, but fall back to the
        # top-level text rather than silently dropping the utterance if a
        # future API response ever omits them.
        if not parts:
            top_level = getattr(response, "text", "") or ""
            if top_level.strip():
                parts = [top_level.strip()]

        return parts

    def _transcribe_utterance(self, audio: np.ndarray, started_at: float):
        try:
            if self._rms(audio) < self.config.silence_threshold:
                return

            audio = self._normalize_audio(audio)

            if not self._has_speech(audio):
                log.debug(
                    "Dropped utterance — VAD found no real speech "
                    "(ambient noise/room tone), skipping decode entirely"
                )
                return

            if self.backend == "cloud":
                parts = self._decode_cloud(audio)
                if parts is None:
                    self._fallback_to_local("cloud request failed")
                    parts = self._decode_local(audio) if self.backend == "local" else []
            elif self._should_probe_cloud():
                # Background recovery probe: try cloud for this one
                # utterance without disrupting the local fallback if it
                # still fails.
                parts = self._decode_cloud(audio)
                if parts is not None:
                    self._recover_to_cloud()
                else:
                    self._schedule_next_cloud_retry()
                    parts = self._decode_local(audio)
            else:
                parts = self._decode_local(audio)

            text = self._clean_text(" ".join(parts))

            if self._is_bad_output(text):
                return

            self.last_text = text
            self.last_emit_time = time.time()

            elapsed_ms = (self.last_emit_time - started_at) * 1000.0
            log.info("Utterance ready in %.0fms: %s", elapsed_ms, text)
            print(f">> ({elapsed_ms:.0f}ms) {text}")

            # A decode running on the executor can still be in flight when
            # stop() is called — don't let it push a stale result into the
            # engine after the operator has already stopped listening.
            if self.stop_event.is_set():
                return

            if self.callback_fn:
                self.callback_fn(text)

        except Exception as e:
            print("[Transcription error]", e)

    def start(self):
        self.stop_event.clear()
        self._state = "SILENCE"
        self._utterance_chunks = []
        self._utterance_samples = 0
        self._silence_run_ms = 0.0

        self.stream = sd.InputStream(
            samplerate=self.config.sample_rate,
            channels=self.config.channels,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=int(self.config.sample_rate * 0.1),
            device=self.config.device_index,
        )

        self.stream.start()

        print("Listening...")
        print(
            f"Signal-driven segmentation active "
            f"(endpoint silence: {self.config.endpoint_silence_ms}ms, "
            f"max utterance: {self.config.max_utterance_seconds}s).\n"
        )

    def start_blocking(self):
        self.start()

        try:
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        self.stop_event.set()

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        # Drop any not-yet-started decodes and don't block shutdown waiting
        # on in-flight ones — the stop_event check in _transcribe_utterance
        # keeps a straggler from firing a stale callback. Replaced (not just
        # shut down) so a subsequent start() still has a usable pool.
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._dispatch_workers = self._dispatch_worker_count()
        self._executor = ThreadPoolExecutor(
            max_workers=self._dispatch_workers,
            thread_name_prefix="bible-ai-transcribe",
        )

        print("\nTranscriber stopped.")


UltraLowLatencyTranscriber = BibleAITranscriber


def list_devices():
    print("\nAvailable input devices:\n")

    devices = sd.query_devices()

    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            print(f"{i}: {dev['name']}")

    print()


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--model", type=str, default="base.en")
    parser.add_argument("--beam", type=int, default=3)
    parser.add_argument("--endpoint-silence-ms", type=int, default=350)
    parser.add_argument("--max-utterance-seconds", type=float, default=10.0)
    parser.add_argument(
        "--backend", type=str, default="auto", choices=["auto", "local", "cloud"],
        help="'auto' (default) uses Groq's cloud Whisper API as the primary "
             "backend if GROQ_API_KEY is set, else falls back to local "
             "(GPU if usable, else CPU)."
    )
    parser.add_argument(
        "--groq-model", type=str, default="whisper-large-v3",
        help="Only used when the cloud backend is active."
    )

    args = parser.parse_args()

    if args.list_devices:
        list_devices()
        return

    transcriber = BibleAITranscriber(
        Config(
            model_size=args.model,
            device_index=args.device,
            beam_size=args.beam,
            endpoint_silence_ms=args.endpoint_silence_ms,
            max_utterance_seconds=args.max_utterance_seconds,
            backend=args.backend,
            groq_model=args.groq_model,
        )
    )

    transcriber.set_callback(lambda text: None)
    transcriber.start_blocking()


if __name__ == "__main__":
    main()
