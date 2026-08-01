"""
app/retrieval/aliases_en.py
============================
English vocabulary tables: Bible book-name aliases, spoken number words,
navigation-command phrases, and Bible-version trigger phrases.

Part of the per-language alias-file pattern — see aliases_twi.py for the
sibling Twi tables and its confidence notes. reference_extractor.py and
version_detector.py each import every language module and merge the
matching tables together at import time (see the `for _lang in
_LANGUAGES:` loops near the top of both files) into one combined runtime
dict per vocabulary type. Adding a new language means adding a new
aliases_<code>.py module with these same seven names, NOT touching the
matching logic in either of those two files.

LANGUAGE is this module's code, used to gate matching by active Bible
version — reference_extractor.py only matches a language's BOOK_ALIASES/
NUMBER_WORDS/STRUCTURAL_WORDS when that language is in the caller's
allowed_languages set (hybrid.py and main_ui.py compute that from
session.active_version: Twi vocabulary is only allowed when the active
version is TWI, English is always allowed). Without this, saying a Twi
book name while KJV was the active version would resolve the reference
but still display KJV's English text — confusing, and not what "the Twi
version" means to an operator.
"""

LANGUAGE = "en"

# ============================================================
# BOOK NAME ALIASES
# ============================================================
BOOK_ALIASES: dict[str, str] = {
    # Old Testament
    "genesis": "Genesis",
    "gen": "Genesis",

    "exodus": "Exodus",
    "ex": "Exodus",
    "exo": "Exodus",

    "leviticus": "Leviticus",
    "lev": "Leviticus",

    "numbers": "Numbers",
    "number": "Numbers",
    "num": "Numbers",

    "deuteronomy": "Deuteronomy",
    "deutronomy": "Deuteronomy",
    "deut": "Deuteronomy",
    "dt": "Deuteronomy",

    "joshua": "Joshua",
    "josh": "Joshua",

    "judges": "Judges",
    "judg": "Judges",
    "jdg": "Judges",

    "ruth": "Ruth",

    "first samuel": "1 Samuel",
    "1 samuel": "1 Samuel",
    "one samuel": "1 Samuel",

    "second samuel": "2 Samuel",
    "2 samuel": "2 Samuel",
    "two samuel": "2 Samuel",

    "first kings": "1 Kings",
    "1 kings": "1 Kings",
    "one kings": "1 Kings",

    "second kings": "2 Kings",
    "2 kings": "2 Kings",
    "two kings": "2 Kings",

    "first chronicles": "1 Chronicles",
    "1 chronicles": "1 Chronicles",
    "one chronicles": "1 Chronicles",

    "second chronicles": "2 Chronicles",
    "2 chronicles": "2 Chronicles",
    "two chronicles": "2 Chronicles",

    "ezra": "Ezra",
    "nehemiah": "Nehemiah",
    "esther": "Esther",
    "job": "Job",

    "psalm": "Psalms",
    "psalms": "Psalms",
    "ps": "Psalms",
    "psa": "Psalms",
    "pss": "Psalms",

    "proverbs": "Proverbs",
    "proverb": "Proverbs",
    "prov": "Proverbs",
    "pro": "Proverbs",

    "ecclesiastes": "Ecclesiastes",
    "ecclesiastics": "Ecclesiastes",
    "ecc": "Ecclesiastes",
    "eccl": "Ecclesiastes",

    "song of solomon": "Song of Solomon",
    "songs of solomon": "Song of Solomon",
    "song of songs": "Song of Solomon",

    "isaiah": "Isaiah",
    "isiah": "Isaiah",
    "isa": "Isaiah",

    "jeremiah": "Jeremiah",
    "jer": "Jeremiah",

    "lamentations": "Lamentations",
    "lam": "Lamentations",

    "ezekiel": "Ezekiel",
    "ezek": "Ezekiel",

    "daniel": "Daniel",
    "dan": "Daniel",

    "hosea": "Hosea",
    "hos": "Hosea",

    "joel": "Joel",
    "amos": "Amos",

    "obadiah": "Obadiah",
    "obediah": "Obadiah",
    "obad": "Obadiah",

    "jonah": "Jonah",
    "micah": "Micah",
    "nahum": "Nahum",

    "habakkuk": "Habakkuk",
    "habbakuk": "Habakkuk",
    "hab": "Habakkuk",

    "zephaniah": "Zephaniah",
    "zeph": "Zephaniah",

    "haggai": "Haggai",

    "zechariah": "Zechariah",
    "zachariah": "Zechariah",
    "zech": "Zechariah",

    "malachi": "Malachi",
    "mal": "Malachi",

    # New Testament
    "matthew": "Matthew",
    "mathew": "Matthew",
    "mathews": "Matthew",
    "matt": "Matthew",
    "mt": "Matthew",

    "mark": "Mark",
    "mk": "Mark",

    "luke": "Luke",
    "lk": "Luke",

    "john": "John",
    "jn": "John",

    "acts": "Acts",

    "romans": "Romans",
    "roman": "Romans",
    "rom": "Romans",

    "first corinthians": "1 Corinthians",
    "1 corinthians": "1 Corinthians",
    "one corinthians": "1 Corinthians",
    "first corinthian": "1 Corinthians",
    "1 corinthian": "1 Corinthians",
    "one corinthian": "1 Corinthians",

    "second corinthians": "2 Corinthians",
    "2 corinthians": "2 Corinthians",
    "two corinthians": "2 Corinthians",
    "second corinthian": "2 Corinthians",
    "2 corinthian": "2 Corinthians",
    "two corinthian": "2 Corinthians",

    "galatians": "Galatians",
    "galatian": "Galatians",
    "gal": "Galatians",

    "ephesians": "Ephesians",
    "ephesian": "Ephesians",
    "eph": "Ephesians",
    "africans": "Ephesians",
    "african": "Ephesians",
    "effusions": "Ephesians",

    "philippians": "Philippians",
    "philippian": "Philippians",
    "filippians": "Philippians",
    "philipians": "Philippians",
    "phil": "Philippians",

    "colossians": "Colossians",
    "colossian": "Colossians",
    "collossians": "Colossians",
    "colosians": "Colossians",
    "col": "Colossians",

    "first thessalonians": "1 Thessalonians",
    "1 thessalonians": "1 Thessalonians",
    "one thessalonians": "1 Thessalonians",

    "second thessalonians": "2 Thessalonians",
    "2 thessalonians": "2 Thessalonians",
    "two thessalonians": "2 Thessalonians",

    "thessalonians": "Thessalonians",
    "thessalonian": "Thessalonians",
    "thessolonians": "Thessalonians",
    "thessalonions": "Thessalonians",
    "thess": "Thessalonians",

    "first timothy": "1 Timothy",
    "1 timothy": "1 Timothy",
    "one timothy": "1 Timothy",

    "second timothy": "2 Timothy",
    "2 timothy": "2 Timothy",
    "two timothy": "2 Timothy",

    "titus": "Titus",

    "philemon": "Philemon",
    "filemmon": "Philemon",

    "hebrews": "Hebrews",
    "heb": "Hebrews",

    "james": "James",
    "jas": "James",

    "first peter": "1 Peter",
    "1 peter": "1 Peter",
    "one peter": "1 Peter",

    "second peter": "2 Peter",
    "2 peter": "2 Peter",
    "two peter": "2 Peter",

    "first john": "1 John",
    "1 john": "1 John",
    "one john": "1 John",

    "second john": "2 John",
    "2 john": "2 John",
    "two john": "2 John",

    "third john": "3 John",
    "3 john": "3 John",
    "three john": "3 John",

    "jude": "Jude",

    "revelation": "Revelation",
    "revelations": "Revelation",
    "revel": "Revelation",
    "rev": "Revelation",
}

# ============================================================
# NUMBER WORDS — combinable style (used by reference_extractor.py's
# _number_from_words for chapter/verse parsing: "twenty" + "seven" -> 27)
# ============================================================
NUMBER_WORDS: dict[str, int] = {
    "zero": 0,
    "one": 1, "first": 1,
    "two": 2, "second": 2,
    "three": 3, "third": 3,
    "four": 4, "fourth": 4,
    "five": 5, "fifth": 5,
    "six": 6, "sixth": 6,
    "seven": 7, "seventh": 7,
    "eight": 8, "eighth": 8,
    "nine": 9, "ninth": 9,
    "ten": 10, "tenth": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fourty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}

SMALL_NUMBERS: set = {
    "one", "first", "two", "second", "three", "third",
    "four", "fourth", "five", "fifth", "six", "sixth",
    "seven", "seventh", "eight", "eighth", "nine", "ninth",
}

TENS: set = {
    "twenty", "thirty", "forty", "fourty",
    "fifty", "sixty", "seventy", "eighty", "ninety",
}

# ============================================================
# STRUCTURAL WORDS — the words that mark "this number is the chapter" /
# "this number is the verse" in a spoken reference ("John CHAPTER 3
# VERSE 16"). reference_extractor.py's _text_normalise() replaces any of
# these (from whichever languages are allowed for the current call) with
# the literal canonical word ("chapter"/"verse") before its regex
# patterns run — so those patterns only ever need to know the English
# literal, regardless of which language the marker word was said in. See
# aliases_twi.py's STRUCTURAL_WORDS for the Twi equivalents ("ti",
# "nkyekyɛmu") that make "Yohane ti 3 nkyekyɛmu 16" work the same way.
# ============================================================
STRUCTURAL_WORDS: dict[str, str] = {
    "chapters": "chapter",
    "verses": "verse",
    "verse number": "verse",
    "the verse number": "verse",
    "verse no": "verse",
    "verse no.": "verse",
    "vs": "verse",
    "vrs": "verse",
    "v.": "verse",
    "chapter number": "chapter",
    "chapter no": "chapter",
    "chapter no.": "chapter",
    "and verse": "verse",
}

# ============================================================
# NUMBER WORDS — compound-literal style (used by version_detector.py's
# _normalise_numbers for verse-range detection: plain string substitution,
# so compounds like "twenty one" need their own literal entry)
# ============================================================
NUM_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty one": 21, "twenty two": 22, "twenty three": 23, "twenty four": 24,
    "twenty five": 25, "twenty six": 26, "twenty seven": 27, "twenty eight": 28,
    "twenty nine": 29, "thirty": 30, "thirty one": 31, "thirty two": 32,
    "thirty three": 33, "thirty four": 34, "thirty five": 35, "thirty six": 36,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "hundred and fifty": 150, "hundred and seventy six": 176,
}

# ============================================================
# NAVIGATION COMMANDS
# ============================================================
NAV_PHRASES: dict[str, str] = {
    "next verse":           "NEXT",
    "following verse":      "NEXT",
    "go to next":           "NEXT",
    "move to next verse":   "NEXT",
    "previous verse":       "PREV",
    "go back":              "PREV",
    "go to previous":       "PREV",
    "go to previous verse": "PREV",
    "last verse":           "LAST",
    "final verse":          "LAST",
    "end of chapter":       "LAST",
    "read that again":      "REPEAT",
    "say that again":       "REPEAT",
    "read again":           "REPEAT",
    "repeat that verse":    "REPEAT",
    "repeat the verse":     "REPEAT",
    "stop display":         "STOP",
    "clear screen":         "STOP",
    "clear the screen":     "STOP",
    "hide verse":           "STOP",
    "hide the verse":       "STOP",
    "clear verse":          "STOP",
    "remove verse":         "STOP",
}

# ============================================================
# VERSION REGISTRY (trigger phrases -> version code)
# ============================================================
VERSION_PHRASES: dict[str, str] = {
    "king james":                  "KJV",
    "king james version":          "KJV",
    "kjv":                         "KJV",
    "bible in basic english":      "BBE",
    "basic english":               "BBE",
    "bbe":                         "BBE",
    "new international version":   "NIV",
    "international version":       "NIV",
    "niv":                         "NIV",
    "new living translation":      "NLT",
    "living translation":          "NLT",
    "nlt":                         "NLT",
    "english standard version":    "ESV",
    "standard version":            "ESV",
    "esv":                         "ESV",
    "new american standard":       "NASB",
    "american standard":           "NASB",
    "nasb":                        "NASB",
    "amplified":                   "AMP",
    "amplified bible":             "AMP",
    "amp":                         "AMP",
    "good news":                   "GNT",
    "good news translation":       "GNT",
    "good news bible":             "GNT",
    "gnt":                         "GNT",
    "gnb":                         "GNT",
    # No data imported — Thomas Nelson caps fair-use quotation at 500
    # verses total, far short of bulk-caching the whole Bible (~31,100
    # verses) this app's architecture needs; would require a separate
    # written license. Recognized anyway so "read in NKJV" reports
    # "not installed" via _switch_version's existing fallback instead of
    # going unrecognized — same treatment as GNT above.
    "new king james":              "NKJV",
    "new king james version":      "NKJV",
    "nkjv":                        "NKJV",
    "the message":                 "MSG",
    "message bible":               "MSG",
    "msg":                         "MSG",
    "message":                     "MSG",
}
