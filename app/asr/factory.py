"""
app/asr/factory.py
====================
The English/Twi split: two call sites, zero shared backend code. Each
language maps to its own Config default -- extending either one only
ever means touching that language's own backend file(s) in
app/asr/backends/, never this function or transcriber.py.

create_transcriber("twi", ...) currently defaults to backend="w2vbert" --
the free offline model, actively being tuned for speed/accuracy (see
app/asr/backends/w2vbert.py). Khaya (see app/asr/backends/khaya.py) is
still an unimplemented placeholder that raises on load(); pass
backend="khaya" explicitly once it's filled in to go back to using it as
the default again.

config_overrides can include "backend" to pick a different one than the
language's default (e.g. create_transcriber("twi", backend="khaya")) --
_LANGUAGE_DEFAULTS is merged with, not layered as fixed kwargs on top of,
whatever the caller passes, so this doesn't collide.
"""

from app.asr.transcriber import BibleAITranscriber, Config

_LANGUAGE_DEFAULTS = {
    "en": {"backend": "auto", "language": "en"},
    "twi": {"backend": "w2vbert", "language": "tw"},
}


def create_transcriber(language: str, **config_overrides) -> BibleAITranscriber:
    try:
        defaults = _LANGUAGE_DEFAULTS[language]
    except KeyError:
        raise ValueError(
            f"Unsupported language {language!r} -- expected one of "
            f"{sorted(_LANGUAGE_DEFAULTS)}"
        )

    merged = {**defaults, **config_overrides}
    return BibleAITranscriber(Config(**merged))
