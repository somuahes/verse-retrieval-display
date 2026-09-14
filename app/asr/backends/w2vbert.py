"""
app/asr/backends/w2vbert.py
==============================
Free, fully offline Twi ASR fallback (no API key, no quota) -- for when
Khaya is unavailable. Not a recommended primary choice: meaningfully
worse accuracy than Khaya. Ported from the reference implementation in
commit 9b274ac (which lived inline in transcriber.py before the ASR
backends were modularized in commit 40380ad) into this file's
ASRBackend/RawSegment shape, plus three targeted fixes below.

Base model: ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1, converted to
CTranslate2 (ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1_farmerline-ct2)
for ~2x faster CPU inference than the plain ONNX build. Measured at
73.6% WER on the original 30-sentence test set -- worse than Khaya, but
far better than two Whisper-based Akan candidates that measured 93-96%
WER. No per-segment confidence data, so output is returned with
bypass_gates=True (the numeric gates in transcriber.py's
_passes_segment_gates have nothing to check anyway; the text-level
gates -- word count, repetition-loop -- still apply downstream in
_is_bad_output regardless of this flag).

Four things changed from the reference implementation to address "slow
and inaccurate":

1. **intra_threads is now CPU-aware, not a hardcoded 16.** The original
   16 was tuned on one specific 8-physical-core machine (going higher --
   24/32 -- measured worse from thread contention). Hardcoding that
   number onto a different machine could just as easily be too low
   (wasting cores) or too high (the same contention penalty the
   original tuning was trying to avoid). Computed once at load time from
   os.cpu_count(), capped at 16 since the original testing never found
   benefit above that on comparable hardware.

2. **Decodes are now serialized with an internal lock.** transcriber.py
   dispatches CPU-bound backends (local, w2vbert) with up to 2 concurrent
   workers (_dispatch_worker_count) -- fine for local Whisper, whose own
   cpu_threads is sized with that concurrency in mind. w2vbert's
   intra_threads was never designed with a second concurrent decode in
   mind: two overlapping decodes would each spin up their own thread
   pool, directly reproducing the over-subscription/contention slowdown
   the original tuning measured (8.9s/7.7s vs 5.9s). Serializing here
   means intra_threads always gets the concurrency budget it was tuned
   for, regardless of what the dispatch pool sends.

3. **Leading/trailing near-silence is trimmed before decode.** CTC decode
   time scales with input frame count. transcriber.py's endpointing
   keeps a margin of quieter audio around real speech (by design, so
   words aren't cut mid-syllable) -- worth trimming here since every
   extra second of near-silence on either edge is pure wasted decode
   time on this already-slow backend, and this is a backend-local
   decision that doesn't touch transcriber.py's own endpoint tuning.

4. **Greedy argmax decode replaced with CTC beam search (pyctcdecode),
   with a greedy fallback.** The reference implementation picked the
   single highest-probability token at every timestep independently
   (np.argmax) and never reconsidered -- one locally-confident wrong
   token can derail the whole word with no way to recover. Beam search
   keeps multiple candidate sequences alive across timesteps and scores
   them jointly, which is the standard accuracy lever for CTC models
   before ever touching training data (this is the same technique behind
   HF's own "Boosting Wav2Vec2 with n-gram LMs" approach -- see
   https://huggingface.co/blog/wav2vec2-with-ngram for the vocab-to-
   labels conversion this follows). Deliberately NOT paired with a KenLM
   language model, unlike that blog's full pipeline: kenlm has no
   prebuilt Windows wheel on PyPI (source-only, needs a C++ toolchain to
   build), and a wrong build attempt was judged worse than a smaller,
   dependency-safe win. pyctcdecode itself is pure Python + numpy +
   pygtrie, no compiler needed, and still gives a real improvement over
   pure greedy from the beam search alone. If a Twi n-gram LM ever gets
   built from data/tw_asante.json (the same corpus already used for
   lexicon correction below) and kenlm's build risk becomes acceptable,
   passing kenlm_model_path into build_ctcdecoder() at the marked spot
   in load() is the natural next step -- likely the single biggest
   remaining accuracy lever short of real fine-tuning.

   Decoder construction (at load time) and every beam-search decode call
   are wrapped defensively, falling back to the original greedy path on
   any failure. This is not paranoia -- it hasn't been possible to test
   this integration against the real downloaded checkpoint yet (see
   PROGRESS.md/session notes), and the labels-list-from-tokenizer
   approach assumes a standard Wav2Vec2 CTC tokenizer shape that this
   specific community checkpoint hasn't been confirmed to match. A
   silent fallback to the already-working greedy decode is strictly
   safer than either crashing or blocking on something unverifiable
   without a live download.

Accuracy, second lever: a post-decode correction pass against a Twi
Bible word vocabulary, built from data/tw_asante.json (already shipped
in this repo -- the full Asante Twi Bible text, the actual target-domain
vocabulary for this app). Same technique already validated in this
codebase for book-name fuzzy matching (rapidfuzz, an 82% similarity
floor, a minimum candidate length) -- extended here to general decoded
tokens, on the reasoning that decode errors are usually near-miss
spellings of a real word, and this backend's real usage is Bible speech,
so a Bible-text vocabulary is the right correction target rather than a
general Twi dictionary. Runs after beam search (or greedy, if beam
search isn't available) either way -- complementary, not a substitute.

Neither accuracy lever (beam search or lexicon correction) has been
validated against real Twi audio or a native speaker -- there is no WER
benchmark in this repo to test either against yet, unlike the book-name
fuzzy matching (verified with a full 66-book typo simulation). Treat
both as plausible, not proven, until run against real recordings.

Still true from the reference implementation, unchanged: not a
recommended primary choice, no per-segment confidence data, downloads
~1.2GB on first use if not already cached.
"""

import logging
import os
import re
import threading
from typing import List, Optional

import numpy as np

from .base import ASRBackend, RawSegment

log = logging.getLogger(__name__)


class W2VBertBackend(ASRBackend):
    name = "w2vbert"
    io_bound = False
    auto_eligible = False

    _MODEL_REPO = "ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1_farmerline-ct2"
    _PROCESSOR_REPO = "ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1"

    # See fix #1 above -- capped at 16 since the original per-machine
    # tuning never found benefit going higher, floored at 4 so a
    # low-core-count machine doesn't starve the decode entirely.
    _MAX_INTRA_THREADS = 16
    _MIN_INTRA_THREADS = 4

    # Silence-trim margin kept around detected speech (see fix #3) --
    # generous on purpose: trimming too tight risks clipping a real
    # word's onset/decay, which would cost more accuracy than the
    # decode-time savings are worth.
    _TRIM_WINDOW_S = 0.02
    _TRIM_PAD_S = 0.15

    # Lexicon-correction tuning (see accuracy section above) -- same
    # similarity floor validated for book-name fuzzy matching elsewhere
    # in this codebase (rapidfuzz, 82%). Length floor is lower than that
    # feature's 6 chars since ordinary Twi words run shorter than book
    # names and still need correcting, at the cost of a wider net for
    # false-positive corrections -- unvalidated, see module docstring.
    _CORRECTION_MIN_LEN = 5
    _CORRECTION_MIN_SCORE = 82.0

    # Beam width for pyctcdecode (see fix #4 above). Kept modest --
    # decode cost scales with beam width, and this backend is already
    # the slow one; 10 is a conservative middle ground between "wider
    # than greedy's effective width of 1" and "not eating back the
    # speed work from fixes #1-#3." Worth raising if real testing shows
    # headroom and latency allows it.
    _BEAM_WIDTH = 10

    _vocab_cache = None  # class-level: build once, shared by every instance
    _vocab_lock = threading.Lock()

    def load(self) -> None:
        try:
            import ctranslate2
            from transformers import AutoProcessor
        except ImportError as e:
            raise ImportError(
                "backend='w2vbert' requires ctranslate2 and transformers: "
                "pip install ctranslate2 transformers"
            ) from e

        self._intra_threads = max(
            self._MIN_INTRA_THREADS,
            min(self._MAX_INTRA_THREADS, os.cpu_count() or 8),
        )

        print(
            "\n[Offline Twi backend] backend='w2vbert' was set explicitly "
            "- using a local, offline Twi ASR model (no API key, no "
            "quota). Measured at 73.6% WER on the original test set -- "
            "meaningfully worse than Khaya, intended for testing while "
            "Khaya is unavailable, not as a primary choice.\n"
            f"intra_threads={self._intra_threads} (auto-selected from "
            f"{os.cpu_count()} logical CPUs, capped at "
            f"{self._MAX_INTRA_THREADS}).\n"
            "Loading model (downloads on first use if not cached, "
            "~1.2GB)...\n"
        )

        # The CT2 repo ships weights only (model.bin), no
        # preprocessor_config.json -- the processor/tokenizer config
        # comes from the fp32 parent repo instead (confirmed identical
        # vocab/feature-extraction settings; CT2 conversion only changes
        # the weights format, not preprocessing).
        self._processor = AutoProcessor.from_pretrained(self._PROCESSOR_REPO)

        from huggingface_hub import snapshot_download
        model_dir = snapshot_download(repo_id=self._MODEL_REPO)
        self._model = ctranslate2.models.Wav2Vec2Bert(
            model_dir,
            device="cpu",
            compute_type="int8",
            intra_threads=self._intra_threads,
        )
        self._ctranslate2 = ctranslate2  # kept for StorageView in decode
        self._decode_lock = threading.Lock()  # see fix #2 above

        self._load_vocab()
        self._decoder = self._build_decoder()

        print("Offline Twi model loaded.\n")

    def _build_decoder(self):
        """Builds the pyctcdecode CTC beam-search decoder from the
        processor's own tokenizer vocabulary (see fix #4's module-
        docstring section for the HF-documented pattern this follows).
        Returns None on any failure -- pyctcdecode not installed, or
        this checkpoint's tokenizer not exposing get_vocab() the way a
        standard Wav2Vec2 CTC tokenizer does -- so transcribe() can fall
        back to the always-available greedy path rather than fail
        load() entirely over an enhancement.

        To add a Twi n-gram LM later (see module docstring -- the
        biggest remaining lever short of real fine-tuning, blocked right
        now on kenlm's Windows build risk): pass
        kenlm_model_path="path/to/your.arpa" into build_ctcdecoder()
        below once one exists, built from data/tw_asante.json."""
        try:
            from pyctcdecode import build_ctcdecoder
        except ImportError:
            log.info(
                "w2vbert: pyctcdecode not installed -- using greedy "
                "argmax decode only (pip install pyctcdecode for beam "
                "search)."
            )
            return None

        try:
            vocab_dict = self._processor.tokenizer.get_vocab()
            sorted_vocab = {
                k.lower(): v
                for k, v in sorted(vocab_dict.items(), key=lambda item: item[1])
            }
            return build_ctcdecoder(labels=list(sorted_vocab.keys()))
        except Exception as e:
            log.warning(
                "w2vbert: couldn't build a CTC beam-search decoder from "
                "this checkpoint's tokenizer (%s) -- falling back to "
                "greedy argmax decode.",
                e,
            )
            return None

    # -- fix #3: silence trim ------------------------------------------

    def _trim_silence(self, audio: np.ndarray) -> np.ndarray:
        """Energy-based edge trim on already-normalized (peak in
        [-1, 1]) audio. Self-normalizing threshold (relative to the
        clip's own RMS) rather than an absolute cutoff, since this runs
        after transcriber.py's own peak-normalization has already erased
        any absolute volume information. Falls back to the original
        audio untouched if the clip is too short to window, or if
        nothing clears the threshold (rare -- transcriber.py's own VAD
        already confirmed real speech is present before this backend is
        ever called)."""
        window = int(self.config.sample_rate * self._TRIM_WINDOW_S)
        if window < 1 or audio.size < window * 2:
            return audio

        n_windows = audio.size // window
        trimmed_len = n_windows * window
        frames = audio[:trimmed_len].reshape(n_windows, window)
        frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))

        overall_rms = float(np.sqrt(np.mean(audio ** 2)))
        if overall_rms <= 0:
            return audio

        threshold = overall_rms * 0.3
        active = np.where(frame_rms >= threshold)[0]
        if active.size == 0:
            return audio

        pad = int(self.config.sample_rate * self._TRIM_PAD_S)
        start = max(0, active[0] * window - pad)
        end = min(audio.size, (active[-1] + 1) * window + pad)

        return audio[start:end]

    # -- accuracy: Twi Bible vocabulary correction -----------------------

    _WORD_RE = re.compile(r"\w+", re.UNICODE)

    def _load_vocab(self):
        """Builds (once, cached at class level) the set of unique
        lowercased word forms appearing anywhere in data/tw_asante.json
        -- the same Twi Bible text this app's retrieval/display already
        ships and uses, and the actual target domain for this backend's
        speech. Correction is scoped to this vocabulary rather than a
        general Twi dictionary because a general dictionary would accept
        plausible-looking corrections that are still wrong for what this
        app is ever going to hear."""
        with self._vocab_lock:
            if W2VBertBackend._vocab_cache is not None:
                self._vocab = W2VBertBackend._vocab_cache
                return

            project_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..")
            )
            data_path = os.path.join(project_root, "data", "tw_asante.json")

            vocab = set()
            try:
                import json
                with open(data_path, encoding="utf-8") as f:
                    books = json.load(f)
                for book in books:
                    for chapter in book.get("chapters", []):
                        for verse in chapter:
                            vocab.update(
                                w.lower() for w in self._WORD_RE.findall(verse)
                            )
            except (OSError, ValueError) as e:
                log.warning(
                    "w2vbert: couldn't build Twi vocabulary from %s (%s) -- "
                    "decoded output will not be lexicon-corrected.",
                    data_path, e,
                )

            W2VBertBackend._vocab_cache = vocab
            self._vocab = vocab

    def _correct_text(self, text: str) -> str:
        if not self._vocab:
            return text

        try:
            from rapidfuzz import fuzz, process
        except ImportError:
            return text

        vocab_list = list(self._vocab)
        out_tokens = []

        for token in text.split(" "):
            bare = token.strip()
            if len(bare) < self._CORRECTION_MIN_LEN:
                out_tokens.append(token)
                continue

            if bare.lower() in self._vocab:
                out_tokens.append(token)
                continue

            match = process.extractOne(
                bare.lower(), vocab_list, scorer=fuzz.ratio,
                score_cutoff=self._CORRECTION_MIN_SCORE,
            )
            if match is None:
                out_tokens.append(token)
                continue

            corrected = match[0]
            if bare[:1].isupper():
                corrected = corrected[:1].upper() + corrected[1:]
            out_tokens.append(corrected)

        return " ".join(out_tokens)

    # -- decode ----------------------------------------------------------

    @staticmethod
    def _log_softmax(logits: np.ndarray) -> np.ndarray:
        """pyctcdecode's beam search accumulates log-probabilities
        across timesteps -- it needs actual log-softmax output, not raw
        logits. For greedy argmax this normalization is a no-op (a
        per-timestep constant shift doesn't change which token has the
        highest score), which is why the original greedy-only code never
        needed it, but skipping it here would make beam search's scoring
        silently wrong rather than just less accurate."""
        shifted = logits - np.max(logits, axis=-1, keepdims=True)
        return shifted - np.log(np.sum(np.exp(shifted), axis=-1, keepdims=True))

    def _greedy_decode(self, logits: np.ndarray) -> str:
        predicted_ids = np.argmax(logits, axis=-1)
        return self._processor.batch_decode(predicted_ids)[0].strip()

    def _beam_decode(self, logits_2d: np.ndarray) -> Optional[str]:
        """logits_2d is one utterance's (time, vocab) slice -- no batch
        dimension, unlike batch_decode's expectation, since pyctcdecode
        decodes one sequence at a time. Returns None on any failure so
        transcribe() can fall back to greedy (see _build_decoder's
        docstring for why this stays defensive until verified against
        the real checkpoint)."""
        try:
            log_probs = self._log_softmax(logits_2d.astype(np.float32))
            return self._decoder.decode(
                log_probs, beam_width=self._BEAM_WIDTH
            ).strip()
        except Exception as e:
            log.warning(
                "w2vbert: beam-search decode failed (%s) -- falling back "
                "to greedy argmax for this utterance.", e,
            )
            return None

    def transcribe(self, audio: np.ndarray) -> Optional[List[RawSegment]]:
        audio = self._trim_silence(audio)

        inputs = self._processor(
            audio, sampling_rate=self.config.sample_rate, return_tensors="np",
        )
        input_features = inputs["input_features"].astype(np.float32)
        storage = self._ctranslate2.StorageView.from_array(input_features)

        with self._decode_lock:
            logits = np.array(self._model.encode(storage, to_cpu=True))

        raw = None
        if self._decoder is not None:
            raw = self._beam_decode(logits[0])
        if raw is None:
            raw = self._greedy_decode(logits)

        if not raw:
            return []

        raw = self._correct_text(raw)

        return [RawSegment(text=raw, bypass_gates=True)]
