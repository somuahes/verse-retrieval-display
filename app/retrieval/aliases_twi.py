"""
app/retrieval/aliases_twi.py
=============================
Twi (Akan) vocabulary tables — same shape as aliases_en.py (BOOK_ALIASES,
NUMBER_WORDS, SMALL_NUMBERS, TENS, NUM_WORDS, NAV_PHRASES, VERSION_PHRASES),
merged into the shared runtime dicts at import time by reference_extractor.py
and version_detector.py. See aliases_en.py's module docstring for how the
merge works and what adding a further language (aliases_<code>.py) requires.

CONFIDENCE varies a lot by section below — flagged per block, not per line,
because the honest risk profile is genuinely different for a transliterated
proper noun ("John" -> "Yohane") versus a constructed compositional phrase
("next verse" -> ?) versus a numeral. Nothing in this file has been checked
by a native Twi speaker or against a real Ghana Bible Society edition — it
is a starting draft, not a verified translation, EXCEPT where a section
says otherwise (STRUCTURAL_WORDS, and NUMBER_WORDS' 11-176 compounding
rules, were both given directly by the user, not guessed). See each
section's own note for where I'd push back hardest on trusting it as-is.
"""

LANGUAGE = "twi"

# ============================================================
# BOOK NAME ALIASES
# ============================================================
# CONFIRMED block below (was "VERIFY" — now checked): cross-referenced
# every entry in this file against the actual USFM headers (\h/\toc2/
# \toc3) of the Biblica Open Asante Twi Contemporary Bible 2020 — the
# same translation imported as this project's TWI Bible version
# (data/tw_asante.json). Every book now has at least one alias sourced
# directly from that translation's own stated name for itself, not a
# guessed transliteration. A few entries below are ADDITIONS beyond
# what that source uses verbatim (e.g. "roma" as a shorter form of its
# "Romafoɔ", or "asomafoɔ" alone alongside the fuller "asomafoɔ
# nnwuma") — those are still plausible natural short forms, just not
# literally the header text, so slightly lower confidence than the
# header-verbatim entries.
# - The whole Pentateuch (Genesis-Deuteronomy) and Song of Solomon were
#   previously MISSING entirely — this translation numbers Genesis-
#   Deuteronomy "1 Mose"-"5 Mose" (confirming the old VERIFY note's
#   guess about Basel Mission-descended numbering) and titles Song of
#   Solomon "Nnwom Mu Dwom" ("song within songs").
# - 1 Samuel, 2 Samuel, Daniel, Hosea, Amos, Nahum use the identical
#   spelling in this Twi translation as in English, so they need no
#   separate entry here — aliases_en.py's existing English aliases
#   already match them.
# - ASCII fallbacks (no ɛ/ɔ) are included alongside diacritic forms
#   throughout, since ASR/typed input is unlikely to ever produce the
#   Akan-specific characters.
BOOK_ALIASES: dict[str, str] = {
    "mateo": "Matthew",
    "marko": "Mark",
    "luka": "Luke",
    "yohane": "John",
    "asomafo nnwuma": "Acts",
    "asomafoɔ nnwuma": "Acts",
    "asomafoɔ": "Acts",
    "asomafo": "Acts",
    "roma": "Romans",
    "romafoɔ": "Romans",
    "romafo": "Romans",
    "1 korintofo": "1 Corinthians",
    "1 korintofoɔ": "1 Corinthians",
    "korintofo a edi kan": "1 Corinthians",
    "2 korintofo": "2 Corinthians",
    "2 korintofoɔ": "2 Corinthians",
    "korintofo a ɛto so mmienu": "2 Corinthians",
    "galatafo": "Galatians",
    "galatifoɔ": "Galatians",
    "galatifo": "Galatians",
    "efesofo": "Ephesians",
    "efesofoɔ": "Ephesians",
    "filipifo": "Philippians",
    "filipifoɔ": "Philippians",
    "kolosefo": "Colossians",
    "kolosefoɔ": "Colossians",
    "1 tesalonikafo": "1 Thessalonians",
    "1 tesalonikafoɔ": "1 Thessalonians",
    "2 tesalonikafo": "2 Thessalonians",
    "2 tesalonikafoɔ": "2 Thessalonians",
    "tesalonikafo": "Thessalonians",
    "1 timoteo": "1 Timothy",
    "2 timoteo": "2 Timothy",
    "timoteo": "Timothy",
    "tito": "Titus",
    "filemon": "Philemon",
    "hebrifo": "Hebrews",
    "hebrifoɔ": "Hebrews",
    "yakobo": "James",
    "1 petro": "1 Peter",
    "2 petro": "2 Peter",
    "petro": "Peter",
    "1 yohane": "1 John",
    "2 yohane": "2 John",
    "3 yohane": "3 John",
    "yuda": "Jude",
    "adiyisɛm": "Revelation",
    "adiyisem": "Revelation",
    "yohane adiyisɛm": "Revelation",
    "yohane adiyisem": "Revelation",

    "yosua": "Joshua",
    "yos": "Joshua",
    "rut": "Ruth",
    "esra": "Ezra",
    "ɛsra": "Ezra",
    "nehemia": "Nehemiah",
    "ɛster": "Esther",
    "ester": "Esther",
    "yesaia": "Isaiah",
    "yeremia": "Jeremiah",
    "esekiel": "Ezekiel",
    "hesekiel": "Ezekiel",
    "yoɛl": "Joel",
    "yoel": "Joel",
    "obadia": "Obadiah",
    "yona": "Jonah",
    "mika": "Micah",
    "habakuk": "Habakkuk",
    "sefania": "Zephaniah",
    "hagai": "Haggai",
    "sakaria": "Zechariah",
    "malaki": "Malachi",

    # Pentateuch — this translation numbers these "1 Mose"-"5 Mose"
    # (Mose = Moses), with a parenthetical transliterated alt-name in
    # its own header for each. Both forms included.
    "1 mose": "Genesis",
    "gyenesis": "Genesis",
    "2 mose": "Exodus",
    "ɛksodɔs": "Exodus",
    "eksodos": "Exodus",
    "3 mose": "Leviticus",
    "lewitikɔs": "Leviticus",
    "lewitikos": "Leviticus",
    "4 mose": "Numbers",
    "numeri": "Numbers",
    "5 mose": "Deuteronomy",
    "deuteronomium": "Deuteronomy",

    "nnwom mu dwom": "Song of Solomon",

    # Confirmed against the actual translation's own USFM headers (see
    # note above this dict) — previously flagged "VERIFY before
    # production use"; that flag is now resolved for these entries.
    "hiob": "Job",
    "1 ahemfo": "1 Kings",
    "2 ahemfo": "2 Kings",
    "ahemfo": "Kings",
    "1 beresosɛm": "1 Chronicles",
    "2 beresosɛm": "2 Chronicles",
    "1 beresosem": "1 Chronicles",
    "2 beresosem": "2 Chronicles",
    "atemmufoɔ": "Judges",
    "atemmufo": "Judges",
    "nnwom": "Psalms",
    "mmɛbusɛm": "Proverbs",
    "mmebusem": "Proverbs",
    "ɔsɛnkafoɔ": "Ecclesiastes",
    "osenkafo": "Ecclesiastes",
    "kwadwom": "Lamentations",
}

# ============================================================
# NUMBER WORDS — cardinals 1-176 (the full range this app ever needs:
# verse numbers cap at 176, Psalm 119's length — see
# reference_extractor.py's _valid_reference).
#
# 1-10 are VERIFY, moderate confidence, as before. 11-176 were given
# directly by the user (cross-checked against Harvard's Twi counting
# materials and a Twi numbering lesson, cited in conversation), following
# the same "given directly by the user, not guessed" trust level as
# STRUCTURAL_WORDS below — this project's policy throughout this file has
# been that a wrong number is a worse failure than a missing one, so 11+
# stayed unimplemented until someone who actually knows the compounding
# rules supplied them, rather than being guessed.
#
# Twi's compounding is regular, not a flat list to hand-maintain:
#   11-19: "du" ("ten") + a reduced ones-word            -> dubaako (11)
#   20,30,...90: their own tens-words                     -> aduonu (20)
#   21-99 (not a multiple of 10): tens-word + ones-word    -> aduonu baako (21)
#   100: "ɔha"
#   101-176: "ɔha ne" ("hundred and") + the 1-99 remainder -> ɔha ne aduoson nsia (176)
# so this is generated below from three small tables, the same way the
# user's own reference implementation was structured, rather than
# hand-listing every value. Spelling variants (e.g. "mmienu"/"mienu",
# "aduoson"/"aduɔson") are included generously and all normalize to the
# same integer — same reasoning as this file's existing ASCII-fallback
# pattern (e.g. "nwɔtwe"/"nwotwe" below), and explicitly recommended by
# the user's own sourcing ("I would normalize those rather than treat
# them as different numbers").
# ============================================================

# Ones-words as used standalone (1-9) AND as the trailing part of a
# tens/hundred compound ("aduonu baako" = 21, "ɔha ne baako" = 101) --
# Twi reportedly reduces some of these in the 11-19 "du+" position
# specifically (see _TEEN_ONES below), so this table is NOT reused there.
_ONES: dict[int, list] = {
    1: ["baako"],
    2: ["mmienu", "mienu"],
    3: ["mmiɛnsa", "mmiensa", "miɛnsa", "miensa"],
    4: ["nnan", "nan"],
    5: ["enum", "anum", "num"],
    6: ["nsia"],
    7: ["nson"],
    8: ["nwɔtwe", "nwotwe"],
    9: ["nkron"],
}

# The 11-19 "du+" forms don't all reduce the same way an ones-word would
# on its own (e.g. 12 is "dumienu", not "du" + the bare "mmienu"/"mienu"
# above) -- given as complete words per the user's sourced table, same as
# how English's "eleven".."nineteen" are complete NUMBER_WORDS entries
# below rather than "ten"+"one" compounds.
_TEENS: dict[int, list] = {
    11: ["dubaako"],
    12: ["dumienu", "dummienu"],
    13: ["dumiɛnsa", "dummiɛnsa", "dumiensa", "dummiensa"],
    14: ["dunan", "dunnan"],
    15: ["dunum", "dunnum"],
    16: ["dunsia"],
    17: ["dunson"],
    18: ["dunwɔtwe", "dunwotwe"],
    19: ["dunkron"],
}

_TENS_WORDS: dict[int, list] = {
    20: ["aduonu"],
    30: ["aduasa"],
    40: ["aduanan"],
    50: ["aduonum"],
    60: ["aduosia"],
    70: ["aduoson", "aduɔson"],
    80: ["aduowɔtwe", "aduɔwɔtwe"],
    90: ["aduokron", "aduɔkron"],
}

# "hundred and" -- joins "ɔha" (100) to a 1-99 remainder ("ɔha ne
# aduoson nsia" = 176). Single spelling given; not a place to guess a
# variant that wasn't sourced.
_HUNDRED_CONNECTOR = "ne"


def _build_twi_numbers() -> dict[str, int]:
    words: dict[str, int] = {
        "baako": 1, "mmienu": 2, "mmiɛnsa": 3, "mmiensa": 3,
        "nnan": 4, "enum": 5, "anum": 5, "nsia": 6, "nson": 7,
        "nwɔtwe": 8, "nwotwe": 8, "nkron": 9,
        "edu": 10, "du": 10,
        "ɔha": 100,
    }

    # 1-9 in every compound-context spelling too (SMALL_NUMBERS below
    # needs the full variant set, not just the one canonical spelling
    # each already got above).
    ones_phrases: dict[int, list] = {n: list(v) for n, v in _ONES.items()}

    for n, variants in _TEENS.items():
        for v in variants:
            words[v] = n

    tens_phrases: dict[int, list] = {}
    for tens_val, tens_variants in _TENS_WORDS.items():
        tens_phrases[tens_val] = list(tens_variants)
        for tv in tens_variants:
            words[tv] = tens_val
            for ones_val, ones_variants in ones_phrases.items():
                for ov in ones_variants:
                    words[f"{tv} {ov}"] = tens_val + ones_val

    # 1-99 phrases eligible to follow "ɔha ne" -- every spelling of every
    # value from 1 to 99 that's now recognized above (ones, ten itself,
    # teens, and tens/tens+ones compounds). "10" is neither in
    # ones_phrases (1-9 only) nor _TEENS (11-19 only) -- without adding
    # it here explicitly, "ɔha ne du"/"ɔha ne edu" (110) would silently
    # never get generated (caught by testing every table entry directly,
    # not just a handful of end-to-end phrases -- a 3-line gap that
    # produced no error, just a quietly missing value).
    remainder_phrases: dict[int, list] = {n: list(v) for n, v in ones_phrases.items()}
    remainder_phrases[10] = ["edu", "du"]
    for n, variants in _TEENS.items():
        remainder_phrases.setdefault(n, []).extend(variants)
    for tens_val, variants in tens_phrases.items():
        remainder_phrases.setdefault(tens_val, []).extend(variants)
        for ones_val, ones_variants in ones_phrases.items():
            combo_val = tens_val + ones_val
            for tv in variants:
                for ov in ones_variants:
                    remainder_phrases.setdefault(combo_val, []).append(f"{tv} {ov}")

    for remainder_val, variants in remainder_phrases.items():
        if not (1 <= remainder_val <= 99):
            continue
        total = 100 + remainder_val
        for v in variants:
            words[f"ɔha {_HUNDRED_CONNECTOR} {v}"] = total

    return words


NUMBER_WORDS: dict[str, int] = _build_twi_numbers()

# Same word sets English's aliases_en.py uses to let
# reference_extractor.py's _parse_number_at/_number_from_words combine a
# tens-word with a ones-word at runtime (e.g. "aduonu baako" -> 21) --
# populated from the same words already given their own NUMBER_WORDS
# entries above, not a separate guess.
SMALL_NUMBERS: set = {v for variants in _ONES.values() for v in variants}
TENS: set = {v for variants in _TENS_WORDS.values() for v in variants}

# Same cardinals, in the compound-literal shape version_detector.py's
# range detection expects (identical to NUMBER_WORDS above).
NUM_WORDS: dict[str, int] = dict(NUMBER_WORDS)

# ============================================================
# STRUCTURAL WORDS — given directly by the user, not guessed (unlike most
# of this file): "ti" marks the chapter number, "nkyekyɛmu" marks the
# verse number, e.g. "Yohane ti 3 nkyekyɛmu 16" = "John chapter 3 verse
# 16". See aliases_en.py's STRUCTURAL_WORDS for how these get merged in.
# ASCII fallback ("nkyekyemu") included per this file's usual pattern.
#
# Split-token variants ("nkyekyɛ mu", "nkyekye mu") added after real
# Khaya output showed it sometimes transcribes this as two words with a
# space instead of one — confirmed live: "Genesis t baako nkyekyɛ mu
# baako" (intending "Genesis chapter 1 verse 1") failed to resolve
# because the substitution below requires an exact single-token match,
# and a mid-word space breaks that entirely. Without this, ANY split
# ASR output for this word silently loses its "verse" marker instead of
# reference detection just being unnaturally strict — dict keys can be
# multi-word phrases here since the substitution is a word-boundary
# regex, not a literal single-token lookup.
# ============================================================
STRUCTURAL_WORDS: dict[str, str] = {
    "ti": "chapter",
    "nkyekyɛmu": "verse",
    "nkyekyemu": "verse",
    "nkyekyɛ mu": "verse",
    "nkyekye mu": "verse",
    # "t" alone: also confirmed live -- Khaya sometimes drops "ti" down to
    # a bare "t". A single letter would normally be too risky to add (see
    # the book-alias exclusions elsewhere in this file for words like
    # "yes"/"ate" that ARE real English words) but "t" as a standalone
    # spoken token essentially never occurs in real English speech, and
    # even a wrong substitution here only ever injects an extra filler
    # word that gets skipped past -- unlike a number word, it can't turn
    # into a wrong chapter/verse digit on its own.
    "t": "chapter",
    # "te" -- confirmed live from w2v-bert output ("romafoɔ te baako",
    # intending "romafoɔ ti baako" = "Romans chapter 1"): a one-vowel
    # ASR misreading of "ti". Same low-risk reasoning as "t" above -- a
    # wrong substitution only ever injects a harmless extra "chapter"
    # filler, never fabricates a wrong number on its own.
    "te": "chapter",
    # Near-miss spellings of "nkyekyɛmu" confirmed live from w2v-bert
    # output, close enough (75-88% character similarity, checked against
    # rapidfuzz.fuzz.ratio) that adding them as exact variants is safe --
    # same reasoning as "nkyekyɛ mu"/"nkyekye mu" above, just without the
    # space. NOT a general fuzzy-match fix: real w2v-bert output on this
    # word also included FAR heavier garbling ("ntyityee", "nkyitkyee",
    # "ntwityere" -- 33-56% similarity) that no safe similarity threshold
    # can catch without also matching unrelated short Twi words by
    # chance (the same false-positive risk already documented for
    # book-name fuzzy matching elsewhere in this codebase, where
    # "father"/"esther" collided at 67%). That heavier tier is a real
    # ASR-accuracy ceiling for this specific word on this specific
    # model, not something a reference-parsing fix can safely close --
    # see app/asr/backends/w2vbert.py and TRANSCRIPTION_SETUP.md for the
    # actual levers (Khaya's hosted API measured far more accurate;
    # fine-tuning is the other one, blocked on real training audio).
    "nkyekyɛ": "verse",
    "nkyekye": "verse",
    "nkyikyemu": "verse",
    "kyekyɛɛ": "verse",
}

# See aliases_en.py's BARE_MENTION_BLOCKLIST — no current Twi
# BOOK_ALIASES entry is also an ordinary word risky enough to need one.
BARE_MENTION_BLOCKLIST: set[str] = set()

# ============================================================
# NAVIGATION COMMANDS — DRAFT, UNVERIFIED.
#
# Unlike the book-name aliases and TWI version-name entries below, these
# are full compositional phrases, not proper nouns or single numerals — I
# have meaningfully lower confidence I've constructed natural, correct
# Twi imperatives here versus just transliterating "John" -> "Yohane".
# Kept deliberately short/simple (single common verbs rather than
# elaborate sentences) to minimize how wrong a guess can be, but this
# whole block genuinely needs a native Twi speaker's review before
# relying on it in a live service — treat it as a starting draft, not a
# verified mapping.
# ============================================================
NAV_PHRASES: dict[str, str] = {
    "kɔ so":                "NEXT",   # "go on / continue"
    "ko so":                "NEXT",   # ascii fallback (no ɔ)
    "kɔ akyi":              "PREV",   # "go back"
    "ko akyi":              "PREV",
    "san ka":               "REPEAT", # "say again" (san = "return")
    "ka bio":               "REPEAT", # "say again" -- "bio" ("again/
    "si so bio":            "REPEAT", # repeat") is dictionary-attested
                                       # (akandictionary.com), more solidly
                                       # confirmed than "san ka" above; kept
                                       # both multi-word phrases rather than
                                       # replacing since neither is confirmed
                                       # wrong. Deliberately NOT adding bare
                                       # "bio" alone -- unlike book-name
                                       # aliases (gated to TWI-active only,
                                       # see reference_extractor.py's
                                       # _allowed_languages), NAV_PHRASES
                                       # merges into a single GLOBAL dict
                                       # checked regardless of active
                                       # version, and "bio" is also an
                                       # ordinary English word ("read her
                                       # bio") that would misfire on
                                       # English sermon speech.
    "nkyekyɛm no awiei":    "LAST",   # "the chapter's end"
    "nkyekyem no awiei":    "LAST",
    "gyae":                 "STOP",   # "stop" -- dictionary-confirmed
    "gyae kyerɛ":           "STOP",   # "stop showing"
    "gyae kyere":           "STOP",
}

# ============================================================
# VERSION REGISTRY (trigger phrases -> version code)
#
# TWI is a real, installed version (see app/database/db.py). These are
# just the version NAME, not a compositional phrase, so confidence here
# is similar to the book-name aliases above, not the NAV_PHRASES block.
# Deliberately NOT adding bare "asante" alone — it's a real ethnic/
# regional name ("the Asante people...") that could appear in ordinary
# sermon speech unrelated to a version switch; same false-positive risk
# already documented in aliases_en.py's VERSION_PHRASES for "amp" vs.
# "example".
# ============================================================
VERSION_PHRASES: dict[str, str] = {
    "twi":                         "TWI",
    "asante twi":                  "TWI",
    "asante twi bible":            "TWI",
    "twi bible":                   "TWI",
}
