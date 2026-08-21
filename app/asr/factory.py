"""
app/asr/factory.py
====================
The English/Twi split: two call sites, zero shared backend code. Each
language maps to its own Config -- extending either one only ever means
touching that language's own backend file(s) in app/asr/backends/, never
this function or transcriber.py.

create_transcriber("twi", ...) raises a clear NotImplementedError today,
the moment it's called -- BibleAITranscriber.__init__ loads its backend
eagerly (same as it always has for every backend), and the Khaya backend
(see app/asr/backends/khaya.py) is a placeholder that raises on load().
A deliberate, informative stub rather than a silent hole or a confusing
failure later at .start().
"""

from app.asr.transcriber import BibleAITranscriber, Config

_LANGUAGE_CONFIGS = {
    "en": lambda overrides: Config(backend="auto", language="en", **overrides),
    "twi": lambda overrides: Config(backend="khaya", language="tw", **overrides),
}


def create_transcriber(language: str, **config_overrides) -> BibleAITranscriber:
    try:
        build_config = _LANGUAGE_CONFIGS[language]
    except KeyError:
        raise ValueError(
            f"Unsupported language {language!r} -- expected one of "
            f"{sorted(_LANGUAGE_CONFIGS)}"
        )

    return BibleAITranscriber(build_config(config_overrides))
