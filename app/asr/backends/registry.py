"""
app/asr/backends/registry.py
==============================
The single place a new ASR backend gets wired in. transcriber.py imports
BACKEND_REGISTRY and never needs another line changed when a backend is
added, removed, or renamed here -- Config.backend's valid values,
argparse's --backend choices, and "auto" eligibility are all derived
from this dict.
"""

from typing import Dict, Type

from .base import ASRBackend
from .groq_cloud import GroqCloudBackend
from .khaya import KhayaBackend
from .local_whisper import LocalWhisperBackend
from .w2vbert import W2VBertBackend

BACKEND_REGISTRY: Dict[str, Type[ASRBackend]] = {
    "local": LocalWhisperBackend,
    "cloud": GroqCloudBackend,
    "khaya": KhayaBackend,
    "w2vbert": W2VBertBackend,
}
