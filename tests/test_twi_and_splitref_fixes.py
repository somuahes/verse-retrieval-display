"""
Regression tests for the 2026-09-14 session's changes (see PROGRESS.md
§27) against the real HybridEngine, real bible.db, and the real TWI/KJV
FAISS indexes -- no mocks, same convention as the rest of this project's
test suite. Each engine is built fresh per test (not shared/fixtured)
since several tests depend on starting from a clean, unswitched session.
"""

from app.retrieval.hybrid import HybridEngine


def _pos(engine):
    return (engine.session.current_book, engine.session.current_chapter,
            engine.session.current_verse)


# ── Split-reference verse-jump guard (new) ─────────────────────────

def test_verse_jump_after_complete_reference_is_not_recombined():
    """A bare 'verse 10' right after an already-complete reference must
    jump to verse 10, not get wrongly recombined with the prior
    utterance into 'Genesis 1:1 verse 10' (which extract_reference()
    would parse as Genesis 1:1, silently discarding the jump)."""
    e = HybridEngine(version="KJV")
    e.process("Genesis chapter 1 verse 1")
    assert _pos(e) == ("Genesis", 1, 1)
    e.process("verse 10")
    assert _pos(e) == ("Genesis", 1, 10)


def test_genuine_split_reference_still_recombines():
    """The guard above must not break the original split-reference case
    it sits next to -- a genuinely incomplete fragment still needs to
    recombine with the previous utterance."""
    e = HybridEngine(version="KJV")
    e.process("You know what, let's go to Leviticus")
    e.process("27")
    assert _pos(e) == ("Leviticus", 27, 1)


def test_bare_verse_number_split_reference_still_works():
    e = HybridEngine(version="KJV")
    e.process("Proverbs 31")
    e.process("10")
    assert _pos(e) == ("Proverbs", 31, 10)


# ── Twi semantic-search safety gate (re-added) ─────────────────────

def test_twi_semantic_search_is_disabled():
    """semantic.py has no language awareness -- it would otherwise score
    Twi text against an English-only model and silently return
    confident-looking nonsense. A pure paraphrase with no direct
    reference must not move the session position while TWI is active."""
    e = HybridEngine(version="KJV")
    e.process("switch to Twi bible")
    assert str(e.session.active_version).upper() == "TWI"
    assert e._semantic_enabled() is False

    before = _pos(e)
    e.process("Onyankopɔn dɔ wiase yi")  # Twi paraphrase, no reference
    assert _pos(e) == before


def test_kjv_semantic_search_still_enabled():
    e = HybridEngine(version="KJV")
    assert e._semantic_enabled() is True


# ── Twi number compounding, 1-176 (new) ────────────────────────────

def test_twi_tens_ones_compound_number():
    e = HybridEngine(version="TWI")
    e.process("yohane ti mmiɛnsa nkyekyɛmu aduonu baako")  # John 3:21
    assert _pos(e) == ("John", 3, 21)


def test_twi_hundred_compound_number():
    e = HybridEngine(version="TWI")
    # Psalms 119 has exactly 176 verses -- the full range this app needs.
    e.process("nnwom ti ɔha ne dunkron nkyekyɛmu ɔha ne aduoson nsia")
    assert _pos(e) == ("Psalms", 119, 176)


def test_twi_bare_verse_jump_uses_structural_word_normalisation():
    """version_detector.py's own verse-jump regex hardcodes the literal
    word 'verse' -- without normalising Twi structural words first, a
    bare Twi verse-jump silently never matched anything."""
    e = HybridEngine(version="TWI")
    e.process("yohane ti mmiɛnsa")
    assert _pos(e) == ("John", 3, 1)
    e.process("nkyekyɛmu dunsia")  # "verse 16"
    assert _pos(e) == ("John", 3, 16)


def test_twi_near_miss_structural_word_te_means_chapter():
    """Confirmed from live w2v-bert output: 'te' is a one-vowel ASR
    misreading of 'ti' ('chapter')."""
    e = HybridEngine(version="TWI")
    e.process("romafoɔ te baako")
    assert _pos(e) == ("Romans", 1, 1)


# ── Baseline regression check (unrelated to today, cheap to keep) ──

def test_kjv_direct_reference_still_works():
    e = HybridEngine(version="KJV")
    e.process("John 3:16")
    assert _pos(e) == ("John", 3, 16)
