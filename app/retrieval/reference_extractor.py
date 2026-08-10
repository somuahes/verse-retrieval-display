
"""
app/retrieval/reference_extractor.py
=====================================
Extracts Bible book/chapter/verse references from transcribed sermon text.

Supports:
- John 3:16
- John 3.16
- John 3 16
- Mark 1 2           -> Mark 1:2
- Mark 12            -> Mark 12:1
- John chapter 3 verse 16
- Matthew chapter 5  -> Matthew 5:1
- Matthew 5          -> Matthew 5:1
- First Corinthians 13:4
- 2 Timothy 1:7
- Ps 23:1
- Gen 1:1
- Rom 8:28
- Whisper mishearings: Revelations -> Revelation, Mathew -> Matthew
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple, List, Iterable, FrozenSet

from rapidfuzz import fuzz

from app.retrieval import aliases_en, aliases_twi

# ============================================================
# FUZZY MATCHING — deliberately conservative, see _fuzzy_lookup's own
# docstring for the false-positive investigation behind these numbers.
# ============================================================
# Below this length, a candidate word is NOT considered for fuzzy
# matching at all, full stop — this is the primary safety mechanism,
# not the ratio threshold alone. Confirmed live: real, common sermon
# words ("father", "nation") score 67 against real book aliases
# ("esther", "lamentations") — the SAME ratio range needed to catch
# genuine ASR garbling ("kyeɛmu" vs "nkyekyemu" = 67). Ratio alone
# can't safely separate those two cases; word length can, since the
# dangerous collisions above are 6-7 letter common words being confused
# with LONGER book names, whereas raising the ratio bar high enough to
# exclude them (~80+) still catches the clearest garbling cases
# ("gyenisis" vs "gyenesis" = 88, "nkyikyemu" vs "nkyekyemu" = 89).
_FUZZY_MIN_CANDIDATE_LEN = 6
_FUZZY_MIN_RATIO = 82


def _fuzzy_lookup(word: str, choices: dict) -> Optional[str]:
    """word -> choices[best match], or None if no candidate clears
    _FUZZY_MIN_RATIO, or the top-scoring candidates disagree on the
    ANSWER (avoids picking arbitrarily between two genuinely different
    targets). `choices` maps alias-string -> canonical value (same
    shape as bundle.book_aliases / bundle.structural_words) — only
    single-word keys are considered, since a garbled single ASR token
    can't meaningfully match a multi-word alias by position anyway.

    Ambiguity is judged by canonical VALUE, not raw alias string — e.g.
    "collossians" (90.9) and "colosians" (90.0) are both aliases for
    Colossians, so there's no real ambiguity even though their scores
    are a hair apart; comparing raw scores alone (an earlier version of
    this function did) rejected that case as a false ambiguity. Only
    reject when the near-top candidates point to genuinely DIFFERENT
    books/values."""
    if len(word) < _FUZZY_MIN_CANDIDATE_LEN:
        return None

    scored = [
        (fuzz.ratio(word, alias), choices[alias])
        for alias in choices
        if " " not in alias
    ]
    if not scored:
        return None

    scored.sort(reverse=True)
    best_score, best_value = scored[0]

    if best_score < _FUZZY_MIN_RATIO:
        return None

    for score, value in scored[1:]:
        if score < best_score - 3:
            break
        if value != best_value:
            return None  # genuinely ambiguous — two DIFFERENT answers this close

    return best_value

# ============================================================
# LANGUAGE MERGE
# ============================================================
# Every language module contributes BOOK_ALIASES/NUMBER_WORDS/
# SMALL_NUMBERS/TENS/STRUCTURAL_WORDS in the same shape (see
# aliases_en.py's docstring for the full pattern). A preacher can
# code-switch mid-sentence (very common in Ghanaian churches), so by
# default everything below matches against ALL languages merged
# together. But some callers need to GATE matching to a subset — a Twi
# book name should only resolve while the Twi Bible version is actually
# active (see _LangBundle/_get_bundle below), otherwise "Yohane 3:16"
# would resolve the reference but still display whatever non-Twi
# version's English text, which is confusing. Adding a new language is
# "add aliases_<code>.py to this tuple", not touching any function here.
_LANGUAGES = (aliases_en, aliases_twi)

# _BOOK_ALIASES is imported directly by name from app/ui/main_ui.py (for
# its book-name autocomplete list, which intentionally lists every
# language's book names regardless of active version — it's just an
# autocomplete hint, not a matching decision) — keep this exact name.
# _NUMBER_WORDS is used unrestricted by strip_reference_words() below,
# which strips ANY known number word from leftover query text after a
# reference was already resolved elsewhere; not itself a matching/gating
# decision. Everything that actually needs to RESPECT allowed_languages
# goes through _LangBundle/_get_bundle instead of these two.
_BOOK_ALIASES: dict[str, str] = {}
_NUMBER_WORDS: dict[str, int] = {}
for _lang in _LANGUAGES:
    _BOOK_ALIASES.update(_lang.BOOK_ALIASES)
    _NUMBER_WORDS.update(_lang.NUMBER_WORDS)

_ALL_LANGUAGES: FrozenSet[str] = frozenset(_lang.LANGUAGE for _lang in _LANGUAGES)


@dataclass
class _LangBundle:
    """Every piece of per-language vocabulary the extraction functions
    below need, scoped to one set of allowed languages and built once per
    distinct set actually requested (see _get_bundle's cache) — not
    rebuilt per call. Mirrors the shape of the old always-all-languages
    module-level constants this replaced (_BOOK_PATTERN,
    _ALIASES_BY_FIRST_WORD, _NUMBER_PHRASE, ...), just parameterized."""
    book_aliases: dict = field(default_factory=dict)
    book_pattern: str = ""
    aliases_by_first_word: dict = field(default_factory=dict)
    number_words: dict = field(default_factory=dict)
    small_numbers: set = field(default_factory=set)
    tens: set = field(default_factory=set)
    number_phrase: str = r"\d{1,3}"
    structural_words: dict = field(default_factory=dict)


def _build_bundle(languages: FrozenSet[str]) -> _LangBundle:
    b = _LangBundle()

    for _lang in _LANGUAGES:
        if _lang.LANGUAGE not in languages:
            continue
        b.book_aliases.update(_lang.BOOK_ALIASES)
        b.number_words.update(_lang.NUMBER_WORDS)
        b.small_numbers.update(_lang.SMALL_NUMBERS)
        b.tens.update(_lang.TENS)
        b.structural_words.update(_lang.STRUCTURAL_WORDS)

    b.book_pattern = "|".join(
        sorted(map(re.escape, b.book_aliases.keys()), key=len, reverse=True)
    )

    # See the module-level docstring on the old _ALIASES_BY_FIRST_WORD
    # this replaced: grouped by first word so the token scanner only
    # checks the handful of aliases that could match at a given position
    # instead of scanning all of them. Longest-alias-first order within
    # each group preserved so "first corinthians" still wins over
    # "corinthians" alone.
    for _alias in sorted(
        b.book_aliases.keys(), key=lambda x: len(x.split()), reverse=True
    ):
        _alias_words = _alias.split()
        b.aliases_by_first_word.setdefault(
            _alias_words[0], []
        ).append((_alias, _alias_words))

    # A chapter/verse number as it can appear after "chapter"/"verse" in
    # strong_patterns: digits, or 1-2 number words (covers compounds like
    # "thirty three" — _parse_number_at/_number_from_words already know
    # how to combine a TENS+SMALL_NUMBERS pair, this just bounds the
    # regex capture to match one, instead of a previous unbounded
    # `[a-z0-9 -]+?` that had a real bug: being non-greedy with nothing
    # anchoring its far end, it always matched the SHORTEST possible
    # span — one word — even when a compound number followed. "Matthew
    # chapter six verse thirty three" was parsed as verse 30, not 33,
    # because the capture stopped at "thirty" the moment a word boundary
    # was available, never reaching "three".
    if b.number_words:
        _number_word_pattern = "|".join(
            sorted(map(re.escape, b.number_words.keys()), key=len, reverse=True)
        )
        b.number_phrase = (
            rf"(?:\d{{1,3}}|(?:{_number_word_pattern})"
            rf"(?:\s+(?:{_number_word_pattern}))?)"
        )

    return b


# Realistically only ever two distinct sets get requested (all languages,
# or just "en" when a non-Twi version is active) — cached the first time
# each is actually built rather than precomputed for every possible
# subset up front.
_BUNDLE_CACHE: dict[FrozenSet[str], _LangBundle] = {}
_DEFAULT_BUNDLE = _build_bundle(_ALL_LANGUAGES)
_BUNDLE_CACHE[_ALL_LANGUAGES] = _DEFAULT_BUNDLE


def _get_bundle(allowed_languages: Optional[Iterable[str]]) -> _LangBundle:
    if allowed_languages is None:
        return _DEFAULT_BUNDLE
    key = frozenset(allowed_languages)
    bundle = _BUNDLE_CACHE.get(key)
    if bundle is None:
        bundle = _build_bundle(key)
        _BUNDLE_CACHE[key] = bundle
    return bundle


# ============================================================
# HELPERS
# ============================================================

def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalise_book(raw: str, bundle: _LangBundle) -> str:
    key = raw.strip().lower()
    key = re.sub(r"\.$", "", key)
    key = _normalize_spaces(key)
    return bundle.book_aliases.get(key, raw.strip().title())


def _text_normalise(text: str, bundle: _LangBundle) -> str:
    text = text.lower()

    # Matthew 6.33 -> Matthew 6:33
    text = re.sub(r"(?<=\d)\.(?=\d)", ":", text)

    text = re.sub(r"[,;]", " ", text)

    # Replaces any language's chapter/verse marker word ("chapters", or
    # Twi's "ti"/"nkyekyɛmu", ...) with the literal canonical word, so
    # the regex patterns below only ever need to look for "chapter"/
    # "verse" regardless of which language marked them. See
    # aliases_en.py/aliases_twi.py's STRUCTURAL_WORDS.
    for wrong, right in bundle.structural_words.items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text)

    # Fuzzy fallback for structural words an ASR engine garbled beyond
    # the exact substitution above (see _fuzzy_lookup's docstring for
    # why this is conservative on purpose) — e.g. "nkyikyemu" (a real
    # offline-model mis-transcription of "nkyekyemu") still becomes
    # "verse" even though it never matched the exact regex above.
    words = text.split()
    for idx, w in enumerate(words):
        if w in ("chapter", "verse"):
            continue
        hit = _fuzzy_lookup(w, bundle.structural_words)
        if hit:
            words[idx] = hit
    text = " ".join(words)

    text = text.replace(":", " : ")
    text = text.replace("-", " - ")

    return _normalize_spaces(text)


def _number_from_words(raw: str, bundle: _LangBundle) -> Optional[int]:
    raw = _normalize_spaces(raw.lower().replace("-", " "))

    if raw.isdigit():
        return int(raw)

    if raw in bundle.number_words:
        return bundle.number_words[raw]

    parts = raw.split()

    if len(parts) == 2 and parts[0] in bundle.tens and parts[1] in bundle.small_numbers:
        return bundle.number_words[parts[0]] + bundle.number_words[parts[1]]

    if len(parts) == 2 and parts[0] in bundle.small_numbers and parts[1] == "hundred":
        return bundle.number_words[parts[0]] * 100

    return None


def _parse_number_at(
    words: List[str], i: int, bundle: _LangBundle
) -> tuple[Optional[int], int]:
    if i >= len(words):
        return None, 0

    if words[i].isdigit():
        return int(words[i]), 1

    if i + 1 < len(words):
        two = f"{words[i]} {words[i + 1]}"
        n = _number_from_words(two, bundle)
        if n is not None:
            return n, 2

    n = _number_from_words(words[i], bundle)
    if n is not None:
        return n, 1

    return None, 0


def _valid_reference(chapter: int, verse: int) -> bool:
    return 1 <= chapter <= 150 and 1 <= verse <= 176


def _add_result(
    results: List[Tuple[str, int, int]],
    seen: set,
    book: str,
    chapter: int,
    verse: int
) -> bool:
    """Returns True if a new entry was actually appended (not a dupe and
    not out of range) — callers that need to track a parallel per-entry
    flag (see explicit_flags in _extract_all_references_verbose) use this
    to know whether to append their own flag too."""
    if not _valid_reference(chapter, verse):
        return False

    key = (book.lower(), chapter, verse)

    if key not in seen:
        seen.add(key)
        results.append((book, chapter, verse))
        return True

    return False


def _chapter_already_found(
    results: List[Tuple[str, int, int]],
    book: str,
    chapter: int
) -> bool:
    """
    Prevents:
        John 3:16
    from also creating:
        John 3:1
    """
    for b, c, _v in results:
        if b.lower() == book.lower() and int(c) == int(chapter):
            return True
    return False


# ============================================================
# MAIN EXTRACTION
# ============================================================

def extract_reference(
    text: str, allowed_languages: Optional[Iterable[str]] = None
) -> Optional[Tuple[str, int, int]]:
    refs = extract_all_references(text, allowed_languages)
    return refs[0] if refs else None


def extract_all_references(
    text: str, allowed_languages: Optional[Iterable[str]] = None
) -> List[Tuple[str, int, int]]:
    """
    Extract all Bible references as:
        (book, chapter, verse)

    Important rule:
        Full references are detected before chapter-only references.

    So:
        Mark 1 2  -> Mark 1:2
        Mark 12   -> Mark 12:1

    allowed_languages restricts which language(s)' vocabulary can match —
    e.g. {"en"} to only recognise English book names/markers, or
    {"en", "twi"} to also allow Twi. None (the default) matches every
    known language, same as before this parameter existed. Callers that
    care which Bible VERSION is active (hybrid.py, main_ui.py) compute
    this from session.active_version so a Twi reference only resolves
    while the Twi version is actually selected — see _LangBundle above.
    """
    return [
        (b, c, v)
        for (b, c, v, _explicit) in _extract_all_references_verbose(
            text, allowed_languages
        )
    ]


def extract_reference_verbose(
    text: str, allowed_languages: Optional[Iterable[str]] = None
) -> Optional[Tuple[str, int, int, bool]]:
    """Like extract_reference, but the 4th element reports whether the
    verse number was explicitly spoken or defaulted to 1 because none was
    given — e.g. "Romans 12:3" -> (..., True) vs "Romans 12" alone ->
    (..., False). Used by hybrid.py to tell "jump to this chapter" apart
    from "a specific verse was described, but not numbered" (a chapter
    mention sitting inside a longer sentence that also paraphrases a
    verse — the naive verse-1 default would be wrong there). See
    extract_all_references for allowed_languages."""
    refs = _extract_all_references_verbose(text, allowed_languages)
    return refs[0] if refs else None


def extract_all_references_verbose(
    text: str, allowed_languages: Optional[Iterable[str]] = None
) -> List[Tuple[str, int, int, bool]]:
    return _extract_all_references_verbose(text, allowed_languages)


# Structural words that appear as literal boilerplate in every verse's own
# embedding text (see semantic.py's build_embedding_text: "{book} chapter
# {chapter} verse {verse}. {text}") — left in a query, they trivially
# self-match any verse in the mentioned book/chapter regardless of real
# paraphrase content, so strip_reference_words() removes them too.
_STRUCTURAL_WORDS = {"chapter", "chap", "verse", "verses", "vs", "vrs"}


def strip_reference_words(text: str, book: str) -> str:
    """Remove the spoken book name (any alias for it), bare numbers, and
    structural words ("chapter", "verse") from text. For a caller that
    already knows a book/chapter was mentioned and wants to run semantic
    search on "whatever's left" of the utterance — without this, the
    reference mention itself would pollute the query (see
    _STRUCTURAL_WORDS above)."""
    book_words = {
        w
        for alias, canonical in _BOOK_ALIASES.items()
        if canonical.lower() == book.lower()
        for w in alias.split()
    }
    book_words.update(book.lower().split())

    kept = []
    for w in text.split():
        bare = re.sub(r"[^\w]", "", w.lower())
        if not bare:
            continue
        if bare.isdigit():
            continue
        if bare in book_words or bare in _STRUCTURAL_WORDS or bare in _NUMBER_WORDS:
            continue
        kept.append(w)

    return " ".join(kept)


def _extract_all_references_verbose(
    text: str, allowed_languages: Optional[Iterable[str]] = None
) -> List[Tuple[str, int, int, bool]]:
    if not text:
        return []

    bundle = _get_bundle(allowed_languages)
    text = _text_normalise(text, bundle)

    results: List[Tuple[str, int, int]] = []
    explicit_flags: List[bool] = []
    seen = set()

    # --------------------------------------------------------
    # 1. Strong direct patterns
    # --------------------------------------------------------

    strong_patterns = [
        # John 3:16 / Matthew 6.33 after normalisation
        rf"\b({bundle.book_pattern})\s+(\d{{1,3}})\s*:\s*(\d{{1,3}})\b",

        # John chapter 3 verse 16
        rf"\b({bundle.book_pattern})\s+chapter\s+"
        rf"({bundle.number_phrase})\s+verse\s+({bundle.number_phrase})\b",

        # John 3 verse 16
        rf"\b({bundle.book_pattern})\s+"
        rf"({bundle.number_phrase})\s+verse\s+({bundle.number_phrase})\b",
    ]

    for pattern in strong_patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            raw_book = m.group(1)
            raw_chapter = m.group(2)
            raw_verse = m.group(3)

            book = _normalise_book(raw_book, bundle)

            chapter_words = raw_chapter.split()
            verse_words = raw_verse.split()

            chapter = None
            verse = None

            for start in range(len(chapter_words)):
                n, _used = _parse_number_at(chapter_words, start, bundle)
                if n is not None:
                    chapter = n
                    break

            for start in range(len(verse_words)):
                n, _used = _parse_number_at(verse_words, start, bundle)
                if n is not None:
                    verse = n
                    break

            if chapter is not None and verse is not None:
                if _add_result(results, seen, book, chapter, verse):
                    explicit_flags.append(True)

    # --------------------------------------------------------
    # 2. Token scanner
    # Handles:
    #   Mark 1 2
    #   Romans eight twenty eight
    #   first corinthians thirteen four
    #   Matthew 5
    # --------------------------------------------------------

    words = text.split()

    i = 0

    while i < len(words):
        matched_book = None
        matched_len = 0

        for alias, alias_words in bundle.aliases_by_first_word.get(words[i], ()):
            n = len(alias_words)

            if i + n <= len(words) and words[i:i + n] == alias_words:
                matched_book = bundle.book_aliases[alias]
                matched_len = n
                break

        if not matched_book:
            # Fuzzy fallback (see _fuzzy_lookup's docstring) — catches a
            # single garbled token an offline ASR model mis-transcribed
            # ("gyenisis" for "gyenesis") that the exact lookup above
            # necessarily misses. Deliberately conservative (min length
            # + high ratio threshold): only a single-word candidate is
            # tried, never a multi-word alias by position.
            fuzzy_book = _fuzzy_lookup(words[i], bundle.book_aliases)
            if fuzzy_book:
                matched_book = fuzzy_book
                matched_len = 1
            else:
                i += 1
                continue

        j = i + matched_len

        # Skip filler words before chapter
        while j < len(words) and words[j] in {
            "chapter", "chap", "the", "number", "no", "from",
            "to", "open", "turn", "read", "reading", "book", "of"
        }:
            j += 1

        chapter, used1 = _parse_number_at(words, j, bundle)

        if chapter is None:
            i += matched_len
            continue

        j += used1

        # Colon support after token normalization:
        # John 3 : 16
        if j < len(words) and words[j] == ":":
            j += 1

        while j < len(words) and words[j] in {
            "verse", "the", "number", "no", "and"
        }:
            j += 1

        verse, used2 = _parse_number_at(words, j, bundle)

        if verse is not None:
            if _add_result(results, seen, matched_book, chapter, verse):
                explicit_flags.append(True)
            i = j + used2
            continue

        # Book + chapter only -> verse 1
        # Mark 12 -> Mark 12:1
        if not _chapter_already_found(results, matched_book, chapter):
            if _add_result(results, seen, matched_book, chapter, 1):
                explicit_flags.append(False)

        i = j

    return [
        (book, chapter, verse, explicit)
        for (book, chapter, verse), explicit in zip(results, explicit_flags)
    ]

    return results


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":
    tests = [
        ("John 3:16", ("John", 3, 16)),
        ("John 3.16", ("John", 3, 16)),
        ("John 3 16", ("John", 3, 16)),
        ("John chapter 3 verse 16", ("John", 3, 16)),
        ("John chapter 3, verse 16", ("John", 3, 16)),
        ("John chapter 3 and verse 16", ("John", 3, 16)),
        ("John chapter 3, the verse number 16", ("John", 3, 16)),

        ("Mark 1 2", ("Mark", 1, 2)),
        ("Mark 12", ("Mark", 12, 1)),

        ("Matthew 6.33", ("Matthew", 6, 33)),
        ("Matthew chapter six verse thirty three", ("Matthew", 6, 33)),
        ("Matthew 5 verse 10", ("Matthew", 5, 10)),

        ("Matthew 5", ("Matthew", 5, 1)),
        ("Matthew chapter 5", ("Matthew", 5, 1)),
        ("John chapter 3", ("John", 3, 1)),
        ("Romans 8", ("Romans", 8, 1)),
        ("Psalms 23", ("Psalms", 23, 1)),
        ("Psalm 23", ("Psalms", 23, 1)),

        ("Romans eight twenty eight", ("Romans", 8, 28)),
        ("First Corinthians 13:4", ("1 Corinthians", 13, 4)),
        ("first corinthians thirteen four", ("1 Corinthians", 13, 4)),
        ("Second Timothy one seven", ("2 Timothy", 1, 7)),
        ("Ps 23:1", ("Psalms", 23, 1)),
        ("Eph 6:11", ("Ephesians", 6, 11)),
        ("Africans 2:8", ("Ephesians", 2, 8)),
        ("Filippians 4:13", ("Philippians", 4, 13)),
        ("Revelations 3:20", ("Revelation", 3, 20)),

        ("good morning everyone", None),
        ("the prodigal son", None),
    ]

    passed = 0

    for text, expected in tests:
        result = extract_reference(text)
        ok = result == expected

        print(
            ("✅" if ok else "❌"),
            text,
            "=>",
            result,
            "| expected:",
            expected
        )

        if ok:
            passed += 1

    print(f"\nPassed {passed}/{len(tests)}")
