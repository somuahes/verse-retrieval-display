"""
app/asr/backends/local_whisper.py
==================================
Local, offline decode via faster-whisper. GPU if usable, else CPU int8.
GPU usability is validated with a real dummy forward pass at load time,
not just device detection -- a CUDA device being *detected*
(ctranslate2.get_cuda_device_count() > 0) doesn't mean it's actually
usable; the CUDA runtime libraries can be missing even with a working
driver, which only surfaces as a RuntimeError once real inference is
attempted.
"""

import os

import numpy as np
from faster_whisper import WhisperModel

from .base import ASRBackend, RawSegment


class LocalWhisperBackend(ASRBackend):
    name = "local"
    io_bound = False
    auto_eligible = True

    def load(self) -> None:
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        model_path = os.path.join(
            project_root, "models", "faster-whisper", self.config.model_size,
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
            self.model = self._build_model(model_path, device)
        except (ValueError, RuntimeError) as e:
            if device != "cpu":
                print(
                    f"\n[GPU unusable] {e}\n"
                    "Falling back to CPU for this run. To actually use the "
                    "GPU, install its CUDA runtime libraries:\n"
                    "  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
                )
                self.model = self._build_model(model_path, "cpu")
            else:
                raise

        print("Model loaded successfully.\n")

    def _cuda_available(self) -> bool:
        try:
            import ctranslate2
            return ctranslate2.get_cuda_device_count() > 0
        except Exception:
            return False

    def _build_model(self, model_path: str, device: str) -> WhisperModel:
        # Not every CUDA GPU actually supports float16 efficiently --
        # older cards raise a ValueError from ctranslate2 at load time
        # rather than silently falling back, so try a small preference
        # cascade instead of assuming the fastest option always works.
        # An explicit user-set compute_type is tried as-is, no cascade,
        # so a deliberate choice is never silently overridden.
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
                # the first real utterance -- a missing CUDA runtime
                # library raises RuntimeError only once encode() actually
                # runs, not at construction.
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

    def transcribe(self, audio: np.ndarray):
        segments, _ = self.model.transcribe(
            audio,
            language=self.config.language,
            task="transcribe",
            beam_size=self.config.beam_size,
            best_of=self.config.best_of,
            temperature=list(self.config.temperature),
            vad_filter=True,
            vad_parameters={
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

        out = []
        for seg in segments:
            raw = getattr(seg, "text", "").strip()
            if not raw:
                continue
            out.append(RawSegment(
                text=raw,
                avg_logprob=getattr(seg, "avg_logprob", 0.0),
                no_speech_prob=getattr(seg, "no_speech_prob", 0.0),
                compression_ratio=getattr(seg, "compression_ratio", 0.0),
            ))
        return out
