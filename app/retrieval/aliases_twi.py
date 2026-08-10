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
says otherwise (STRUCTURAL_WORDS below was given directly by the user, not
guessed). See each section's own note for where I'd push back hardest on
trusting it as-is.
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
# NUMBER WORDS — cardinals 1-10 ONLY.
#
# VERIFY, moderate confidence: these are basic, everyday Twi vocabulary
# (much more foundational than the obscure OT book titles above), so I'd
# push back less hard on trusting these than the VERIFY book block. Still
# unverified against a real reference — check spelling/diacritics.
#
# Deliberately NOT attempting 11+. English's "twenty" + "seven" = 27
# combinable pattern (see SMALL_NUMBERS/TENS below) doesn't just
# transliterate to Twi — Twi's counting system for larger numbers has its
# own, more involved compounding rules I don't have reliable enough
# knowledge of to construct correctly. Getting a number WRONG is a
# different, worse kind of failure than an awkward nav phrase (it's
# silently incorrect data, not just unnatural-sounding), so this is
# capped at what I'm actually confident about rather than guessed further.
# A native speaker filling in 11+ here is the natural way to extend this.
# ============================================================
NUMBER_WORDS: dict[str, int] = {
    "baako": 1,
    "mmienu": 2,
    "mmiɛnsa": 3, "mmiensa": 3,
    "nnan": 4,
    "enum": 5, "anum": 5,
    "nsia": 6,
    "nson": 7,
    "nwɔtwe": 8, "nwotwe": 8,
    "nkron": 9,
    "edu": 10,
}

# No compound-number support yet for Twi (see note above) — nothing to
# combine, so these stay empty rather than guessed.
SMALL_NUMBERS: set = set()
TENS: set = set()

# Same 1-10 cardinals, in the compound-literal shape version_detector.py's
# range detection expects (identical to NUMBER_WORDS above until compounds
# are ever added).
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
}

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
