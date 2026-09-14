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
a real dummy forward pass at load time (see
app/asr/backends/local_whisper.py's load()) — a device being *detected*
(ctranslate2.get_cuda_device_count() > 0) doesn't mean it's actually
usable; the CUDA runtime libraries can be missing even with a working
driver, which only surfaces as a RuntimeError once real inference is
attempted, not at model construction. "local" / "cloud" force one path
explicitly. Segment-level anti-hallucination gates (avg_logprob,
no_speech_prob, compression_ratio, prompt-echo) are applied identically
regardless of which backend produced a segment (see _apply_gates below),
since Groq's verbose_json response includes the same per-segment fields
faster-whisper does, and a backend with no per-segment confidence data at
all can just pass None for those fields.

Each backend is its own class in app/asr/backends/ (local_whisper.py,
groq_cloud.py, ...), wired in through BACKEND_REGISTRY
(app/asr/backends/registry.py) — adding a new backend (a new language's
ASR service, say) means adding one file there and one registry entry,
never editing this file. See app/asr/backends/khaya.py and w2vbert.py
for placeholder Twi backends awaiting a real implementation.

Runtime recovery: if a cloud request fails mid-session (network drop,
outage, rate limit — effectively "no internet"), _fallback_to_local
drops that and subsequent utterances to local decode immediately, no
restart needed. Unlike a one-way circuit breaker, this isn't permanent —
_should_probe_cloud/_recover_to_cloud periodically retry cloud in the
background (Config.cloud_retry_interval_s) and switch back automatically
the moment a probe succeeds, so a session started with cloud as primary
returns to cloud on its own once internet/Groq comes back. This
fallback/recovery pairing is specific to the cloud+local pair (an
explicit-only backend like khaya has no fallback — see
app/asr/backends/base.py's auto_eligible).
"""

import argparse
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np
import sounddevice as sd
from faster_whisper.vad import VadOptions, get_speech_timestamps

from app.asr.backends import ASRBackend, BACKEND_REGISTRY, RawSegment
from app.retrieval import aliases_en, aliases_twi

log = logging.getLogger(__name__)

# A bare spoken number ("ten", "sixteen", "twenty seven", Twi's "dunsia",
# "aduonu baako", ...) or a bare chapter/verse marker word ("verse",
# Twi's "nkyekyɛmu"/"ti") — the classic "the speaker paused mid-
# reference" case in ANY supported language (English: "Proverbs 31" ...
# "10"; Twi: "Yohane ti mmiɛnsa" ... "nkyekyɛmu" ... "dunsia"), all
# meaningful regardless of word count, so all exempted from the
# min_words floor below (see _is_bare_number).
#
# Bug found by testing: this used to be a small, hardcoded, ENGLISH-ONLY
# set, with a deliberate comment about NOT importing from
# reference_extractor.py to keep this layer decoupled from Bible-
# reference-parsing logic. That reasoning doesn't apply to importing
# aliases_en.py/aliases_twi.py directly, though — those are pure
# vocabulary data (no logic, no imports of their own, confirmed), not
# reference-parsing code; importing them here doesn't create the
# dependency the original comment was avoiding. Keeping the old
# English-only set instead meant a bare Twi number or chapter/verse
# marker word, spoken alone after a natural pause, was silently dropped
# by min_words before it ever reached the Bible-matching pipeline at
# all — the exact "continuous speech gets segmented mid-reference" case
# this whole mechanism exists to handle, just unhandled for Twi. Built
# from the same per-language alias modules reference_extractor.py and
# version_detector.py already merge, so a third language (a new
# aliases_<code>.py) extends this automatically — nothing here needs
# touching again, same principle as app/asr/backends/registry.py's own
# "add a backend, never touch transcriber.py" contract.
_NUMBER_WORDS: set = set()
for _lang in (aliases_en, aliases_twi):
    # NUM_WORDS keys can be multi-word compounds now (Twi's "ɔha ne
    # aduoson nsia" = 176, see aliases_twi.py) — this check tests one
    # already-tokenized word at a time (_is_bare_number below), so each
    # compound's individual tokens need registering too, not just the
    # full phrase as one string (which could never equal a single word
    # and would silently be dead weight in the set).
    for _phrase in _lang.NUM_WORDS.keys():
        _NUMBER_WORDS.update(_phrase.split())
    # STRUCTURAL_WORDS keys are the "wrong"/spoken forms ("chapters",
    # "vs", Twi's "nkyekyɛmu") and values are the canonical form each
    # maps to ("chapter"/"verse") — the canonical forms themselves are
    # NEVER keys, only values, so both sides need registering (the old
    # hardcoded set's bare "verse" entry was exactly a values-side word;
    # keys-only would have silently dropped it on this rewrite).
    for _phrase in _lang.STRUCTURAL_WORDS.keys():
        _NUMBER_WORDS.update(_phrase.split())
    _NUMBER_WORDS.update(_lang.STRUCTURAL_WORDS.values())


@dataclass
class Config:
    model_size: str = "base.en"
    # "auto" resolves to "cuda" if a usable GPU is found (see module
    # docstring), else "cpu" — same behavior CPU-only machines had before.
    device: str = "auto"
    # "auto" resolves to "float16" on cuda, "int8" on cpu.
    compute_type: str = "auto"

    # "auto" or any key in app.asr.backends.registry.BACKEND_REGISTRY
    # ("local", "cloud", plus explicit-only placeholders "khaya"/
    # "w2vbert" -- see that package). "auto" only ever picks between
    # backends with auto_eligible=True (currently local/cloud) -- see
    # module docstring.
    backend: str = "auto"
    # Reverted back to full large-v3 (from "-turbo") on operator request
    # after a real-session accuracy concern — see PROGRESS.md/session
    # notes for the transcript. Turbo was tried for its lower round-trip
    # latency (1.4-2.5s measured for full large-v3 vs. Groq's LPU
    # hardware making even that fast enough to blow the 2s end-to-end
    # budget), but the actual issue investigated turned out to be a
    # reference-matching bug (app/retrieval/hybrid.py's version-switch
    # step swallowing a reference spoken in the same utterance, now
    # fixed), not ASR quality — this reversion is a deliberate choice to
    # prioritize accuracy over that latency margin, not a finding that
    # turbo was actually at fault. Groq's API still doesn't expose
    # beam_size/best_of the way local faster-whisper does, so model
    # choice remains the only real lever on this path.
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
    # gets chunked and dispatched rather than growing unbounded. Lowered
    # from 10.0 -> 6.0: a continuously-speaking preacher who never pauses
    # past endpoint_silence_ms previously got NOTHING dispatched to
    # decode for up to 10s straight — worst-case latency for that speech
    # pattern, not just the normal endpoint-triggered path. 6.0 still
    # gives _finalize_soft_cut a real syllable gap to find (its lookback
    # window is only ~500ms) and enough audio for decode context, while
    # roughly halving that worst case.
    max_utterance_seconds: float = 6.0
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

        self.backend = self._resolve_backend()
        # The backend chosen at startup — self.backend can drop to "local"
        # at runtime after a cloud failure (see _fallback_to_local), but
        # this never changes, so _should_probe_cloud knows whether it's
        # worth trying to recover back to cloud at all.
        self._preferred_backend = self.backend
        self._next_cloud_retry_at = 0.0
        # Guards backend construction against running twice concurrently
        # — the background prewarm (_warm_up_local_fallback) and a real
        # runtime fallback (_ensure_backend_ready) can otherwise both
        # start loading the same backend at once. Harmless either way
        # (both produce an equally valid instance), just wasted work; the
        # lock avoids it.
        self._local_init_lock = threading.Lock()
        # Loaded backend instances, keyed by registry name — see
        # _ensure_backend_ready/decode(). Populated eagerly for the
        # startup backend below, lazily for a cloud session's local
        # fallback.
        self._backend_instances: Dict[str, ASRBackend] = {}
        self._backend_instances[self.backend] = self._build_and_load(self.backend)

        if self.backend == "cloud":
            self._warm_up_local_fallback()

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

        # Bumped by every start()/stop() — a decode captures the value
        # current at submit time (see _finalize) and _transcribe_utterance
        # only delivers its result if it's unchanged when the decode
        # finishes. stop_event alone isn't enough to prevent a stale
        # in-flight decode from a just-stopped session leaking into a
        # NEW session started before that decode finishes (a quick
        # stop-then-start toggle, well within a Whisper/Groq round-trip
        # time) — stop_event.clear() in start() would make that stale
        # decode's guard pass again. Bumping on both ends means a decode
        # only ever delivers into the exact session it was submitted
        # under, stopped or not, restarted or not.
        self._session_id = 0

        # Bounded pool for per-utterance decode dispatch — see _finalize().
        # self._dispatch_workers tracks the size actually in use so
        # _resize_executor_for_backend (called after a runtime cloud<->
        # local switch) knows whether a resize is even needed. Guards
        # self._executor itself (not decode work, which runs ON the
        # executor's own threads and never holds this) — _finalize's
        # submit() (audio-callback thread) and _resize_executor_for_
        # backend's/stop()'s swap-and-shutdown (a decode thread / the UI
        # thread) could otherwise interleave: a submit() reading
        # self._executor right as a resize/stop swaps it out and shuts
        # the old one down races calling .submit() on an already-
        # shutdown pool, which raises inside the sounddevice callback —
        # fatal to the audio stream.
        self._executor_lock = threading.Lock()
        self._dispatch_workers = self._dispatch_worker_count()
        self._executor = ThreadPoolExecutor(
            max_workers=self._dispatch_workers,
            thread_name_prefix="bible-ai-transcribe",
        )

        self.last_text = ""
        self.last_emit_time = 0.0

    def set_callback(self, fn: Callable[[str], None]):
        self.callback_fn = fn

    def _build_and_load(self, name: str) -> ASRBackend:
        backend = BACKEND_REGISTRY[name](self.config)
        backend.load()
        return backend

    def _ensure_backend_ready(self, name: str) -> bool:
        """Lazily builds+loads a backend the first time it's actually
        needed (e.g. a cloud-primary session never pays local's load cost
        unless/until cloud actually fails). Returns whether it's ready.
        Safe to call repeatedly/concurrently — no-ops once already
        loaded."""
        if name in self._backend_instances:
            return True

        with self._local_init_lock:
            if name in self._backend_instances:
                return True
            try:
                self._backend_instances[name] = self._build_and_load(name)
                return True
            except Exception as e:
                log.error("Backend %r failed to initialize: %s", name, e)
                return False

    def _resolve_backend(self) -> str:
        backend = self.config.backend

        if backend != "auto" and backend not in BACKEND_REGISTRY:
            raise ValueError(
                f"Config.backend must be 'auto' or one of "
                f"{sorted(BACKEND_REGISTRY)}, got {backend!r}"
            )

        if backend != "auto":
            return backend

        # Groq is the primary path whenever a key is configured — local
        # (GPU if available, else CPU) is the fallback for sessions/
        # machines with no key set, not the other way around. Only
        # weighs auto_eligible backends (currently cloud/local, both
        # English-focused) — a language-specific backend must be
        # requested explicitly by name, never guessed at by "auto".
        if self.config.groq_api_key or os.environ.get("GROQ_API_KEY"):
            return "cloud"

        return "local"

    def _dispatch_worker_count(self) -> int:
        """How many utterances can decode concurrently — sized per
        backend, not one blanket number, because io-bound and CPU-bound
        backends have different bottlenecks. Local decode is CPU-bound
        (each CTranslate2 decode is already internally multithreaded via
        cpu_threads), so more worker threads than that just contend for
        the same cores; 2 matches the realistic concurrency ceiling there
        (one utterance still decoding while at most one more has just
        been endpointed or force-cut by max_utterance_seconds).

        An I/O-bound backend spends almost all its time blocked waiting
        on an HTTP response, not touching local CPU at all, so a higher
        count costs nothing locally. Measured directly against Groq's
        real API (whisper-large-v3, 2.5s clips): 450-1000ms round-trip,
        well above what local decode typically takes — at only 2
        workers, a fast preacher's utterances can easily endpoint faster
        than one round-trip completes and queue up behind the cap,
        adding real, avoidable latency. 4 is a conservative raise (not
        unbounded — still bounded per the original design's own goal,
        see the comment on self._executor) rather than a number tuned
        against a specific, unknown-to-us rate limit."""
        return 4 if self._backend_instances[self.backend].io_bound else 2

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
        with self._executor_lock:
            target = self._dispatch_worker_count()
            if target == self._dispatch_workers:
                return
            old_executor = self._executor
            self._dispatch_workers = target
            self._executor = ThreadPoolExecutor(
                max_workers=target, thread_name_prefix="bible-ai-transcribe"
            )
        old_executor.shutdown(wait=False)

    def _warm_up_local_fallback(self):
        """Loads the local Whisper backend in a background thread right
        after a cloud-primary session starts, instead of waiting for
        _fallback_to_local to load it on demand for the first time.
        Backend construction (reading weights off disk, plus the
        validation forward pass in LocalWhisperBackend.load()) is a
        multi-second cost on this kind of hardware — with no prewarm,
        that whole cost previously landed inline on whatever utterance
        happened to trigger the first cloud failure, on top of the
        failed request's own latency. That's the multi-second latency
        spike reported live during an otherwise-fast cloud session.
        Best-effort and silent on failure (e.g. no local model files
        present) — _ensure_backend_ready still loads synchronously if
        this hasn't finished (or didn't succeed) by the time a real
        fallback is needed, so nothing here is required for correctness,
        only for speed."""
        threading.Thread(
            target=lambda: self._ensure_backend_ready("local"),
            name="bible-ai-local-warmup", daemon=True,
        ).start()

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
        """Drops genuinely empty output, known non-speech filler phrases,
        and decode-loop repetition. A bare number is exempted from the
        min_words floor (see _is_bare_number) since it's meaningful in
        this domain regardless of word count — e.g. a spoken verse number
        on its own after a pause. (A prior iteration made every check
        here log-only and never actually drop anything; that let junk
        output — including the repetition-loop and known-filler cases,
        which essentially never match real sermon speech — ride straight
        into the Bible-matching pipeline. Reverted.)"""
        if not text:
            return True

        if (
            self._word_count(text) < self.config.min_words
            and not self._is_bare_number(text)
        ):
            log.info(
                "Dropped — %d word(s), below min_words=%d floor (and not "
                "a bare number): %r",
                self._word_count(text), self.config.min_words, text[:80],
            )
            return True

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
            log.info("Dropped — matches known filler output: %r", text[:80])
            return True

        if self._has_repetition_loop(text):
            log.info("Dropped — repetition loop: %r", text[:80])
            return True

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

        with self._executor_lock:
            self._executor.submit(
                self._transcribe_utterance, audio, started_at, self._session_id
            )

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
        """Anti-hallucination gates, applied identically regardless of
        which backend produced the segment (see module docstring).

        Only compression_ratio and prompt-echo actually drop a segment —
        avg_logprob/no_speech_prob are logged but kept. Whisper splits
        one utterance into multiple internal segments; dropping a
        mid-utterance segment on a blunt confidence signal doesn't lose
        the whole utterance, it carves a silent gap out of the middle of
        it (confirmed live: real words missing mid-sentence from the
        transcript, not whole utterances vanishing). These two
        thresholds were already eased once before for exactly this
        reason (-1.0/0.70 -> -1.2/0.80) and still caught genuine quiet/
        fast/emphasis-toned speech at the eased values — and _has_speech
        (a real Silero VAD pass) already screens out true silence/
        ambient noise before decode even starts, which was the main
        hallucination risk these two were guarding against in the first
        place. compression_ratio and prompt-echo stay hard drops: unlike
        the two above, they're specific, low-false-positive signals tied
        to the actual hallucination patterns observed on real sermon
        audio ("hey, hey, hey...", initial_prompt regurgitated
        verbatim) — real speech essentially never trips them."""
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
                "Dropped segment — compression ratio %.2f exceeds %.2f "
                "(likely a repetition loop): %r",
                compression_ratio,
                self.config.compression_ratio_threshold,
                raw[:80],
            )
            return False

        if self._is_prompt_echo(raw):
            log.warning(
                "Dropped segment — echoes the configured initial_prompt "
                "instead of real speech: %r",
                raw[:80],
            )
            return False

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

    def _apply_gates(self, segments: Optional[List[RawSegment]]) -> List[str]:
        """Applies the anti-hallucination gates uniformly to whatever a
        backend returned, regardless of which backend produced it — a
        backend with no per-segment confidence data (e.g. an explicit-
        only Twi backend) just passes None for those fields in its
        RawSegments, which _passes_segment_gates already treats as
        "skip that particular check" (see its own docstring)."""
        parts = []
        for seg in segments or []:
            if not seg.bypass_gates and not self._passes_segment_gates(
                seg.text, seg.avg_logprob, seg.no_speech_prob,
                seg.compression_ratio,
            ):
                continue
            parts.append(seg.text)
        return parts

    def decode(self, audio: np.ndarray) -> List[str]:
        """Backend-select + decode + gate-filter for one utterance clip.
        Public — also used directly by app/evaluation/pipeline.py so eval
        numbers reflect exactly what live transcription does, without
        that module needing its own copy of this backend/fallback
        branching (see _resolve_backend's registry-driven validation for
        why adding a backend never requires touching this method)."""
        if self.backend == "cloud":
            segments = self._backend_instances["cloud"].transcribe(audio)
            if segments is None:
                self._fallback_to_local("cloud request failed")
                segments = (
                    self._backend_instances["local"].transcribe(audio)
                    if self.backend == "local" else []
                )
        elif self._should_probe_cloud():
            # Background recovery probe: try cloud for this one
            # utterance without disrupting the local fallback if it
            # still fails.
            segments = self._backend_instances["cloud"].transcribe(audio)
            if segments is not None:
                self._recover_to_cloud()
            else:
                self._schedule_next_cloud_retry()
                segments = self._backend_instances["local"].transcribe(audio)
        else:
            segments = self._backend_instances[self.backend].transcribe(audio)

        return self._apply_gates(segments)

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

        if not self._ensure_backend_ready("local"):
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
            and "cloud" in self._backend_instances
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

    def _transcribe_utterance(
        self, audio: np.ndarray, started_at: float, session_id: int
    ):
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

            parts = self.decode(audio)
            text = self._clean_text(" ".join(parts))

            if self._is_bad_output(text):
                return

            # A decode running on the executor can still be in flight
            # when stop() — or a quick stop()-then-start() toggle, well
            # within a Whisper/Groq round-trip time — happens. session_id
            # was captured at submit time (see _finalize) and only still
            # matches self._session_id if neither start() nor stop() has
            # run since; both bump it (see __init__'s comment). Checked
            # before touching ANY shared state below, not just before
            # the callback, so a stale decode can't contaminate
            # last_text/last_emit_time or the console/log output with a
            # different (stopped or already-superseded) session's result.
            if session_id != self._session_id:
                return

            self.last_text = text
            self.last_emit_time = time.time()

            elapsed_ms = (self.last_emit_time - started_at) * 1000.0
            log.info("Utterance ready in %.0fms: %s", elapsed_ms, text)
            print(f">> ({elapsed_ms:.0f}ms) {text}")

            if self.callback_fn:
                self.callback_fn(text)

        except Exception as e:
            print("[Transcription error]", e)

    def start(self):
        self.stop_event.clear()
        self._session_id += 1
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
        self._session_id += 1

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        # Drop any not-yet-started decodes and don't block shutdown waiting
        # on in-flight ones — the session_id check in _transcribe_utterance
        # keeps a straggler from firing a stale callback (or, on a quick
        # stop()-then-start(), into the NEW session — see __init__'s
        # comment on _session_id, which is why stop_event alone isn't
        # enough). Replaced (not just shut down) so a subsequent start()
        # still has a usable pool. Locked against _finalize's submit()
        # and _resize_executor_for_backend's own swap (both read/replace
        # self._executor too) — unsynchronized, a submit() landing
        # between this shutdown() and the reassignment below would raise
        # inside the sounddevice callback thread.
        with self._executor_lock:
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
    parser.add_argument("--max-utterance-seconds", type=float, default=6.0)
    parser.add_argument(
        "--backend", type=str, default="auto",
        choices=["auto"] + sorted(BACKEND_REGISTRY),
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
