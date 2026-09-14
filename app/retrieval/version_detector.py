import re
from typing import Optional, Tuple

from app.retrieval import aliases_en, aliases_twi

# ════════════════════════════════════════════════════════════
#  LANGUAGE MERGE
# ════════════════════════════════════════════════════════════
# Same pattern as reference_extractor.py's _LANGUAGES merge — see
# aliases_en.py's module docstring. Adding a language is "add
# aliases_<code>.py to this tuple", not touching detect_version/
# detect_navigation/_normalise_numbers below.
_LANGUAGES = (aliases_en, aliases_twi)

VERSION_MAP: dict[str, str] = {}
NAV_MAP: dict[str, str] = {}
_NUM_WORDS: dict[str, int] = {}
_STRUCTURAL_WORDS: dict[str, str] = {}

for _lang in _LANGUAGES:
    VERSION_MAP.update(_lang.VERSION_PHRASES)
    NAV_MAP.update(_lang.NAV_PHRASES)
    _NUM_WORDS.update(_lang.NUM_WORDS)
    _STRUCTURAL_WORDS.update(_lang.STRUCTURAL_WORDS)


# ════════════════════════════════════════════════════════════
#  AMBIGUOUS PREV PHRASES — short/common enough to appear as casual
#  speech ("go back to what I was saying"). hybrid.py's
#  nav_requires_confirm() requires these to repeat across two
#  consecutive transcripts before firing; explicit phrases like
#  "previous verse" fire immediately.
# ════════════════════════════════════════════════════════════
PREV_CONFIRM_PHRASES: set = {
    "go back",
    "go to previous",
}


def nav_requires_confirm(text: str) -> bool:
    """
    Returns True if the matched PREV navigation came from an
    ambiguous phrase that warrants a second confirmation.

    Call this AFTER detect_navigation() has returned "PREV".

    Examples:
        "go back"                    → True  (needs confirm)
        "go to previous"             → True  (needs confirm)
        "previous verse"             → False (explicit, fire directly)
        "go to previous verse"       → False (explicit, fire directly)
    """
    t = text.lower().strip()
    # Longest-phrase-wins, mirroring detect_navigation() — a plain
    # substring check wrongly matched "go to previous" as a prefix of
    # "go to previous verse", demanding confirmation for an explicit
    # phrase. Checking whichever NAV_MAP phrase would actually fire first
    # is the only way to tell them apart.
    for phrase in sorted(NAV_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return phrase in PREV_CONFIRM_PHRASES
    return False


def _normalise_structural(text: str) -> str:
    """Replaces any language's chapter/verse marker word/phrase (Twi's
    "nkyekyɛmu", English's "vs"/"v."/"verse no.", ...) with the literal
    "chapter"/"verse" — same table and reasoning as
    reference_extractor.py's _text_normalise (see aliases_en.py/
    aliases_twi.py's STRUCTURAL_WORDS). Needed here too: _VERSE_JUMP_RE
    and _RANGE_PATTERNS below both hardcode the literal English word
    "verse" — without this, a bare Twi verse-jump ("nkyekyɛmu dunsia",
    intending "verse 16") silently never matched anything, the same gap
    that direct-reference matching used to have before _text_normalise
    existed. Bug found by testing: numbers now compound correctly for
    Twi (see aliases_twi.py) but a bare verse-jump utterance still
    failed, because this file's own copy of the structural-word step was
    simply missing, not because of anything number-related."""
    t = text.lower()
    for wrong, right in sorted(
        _STRUCTURAL_WORDS.items(), key=lambda x: len(x[0]), reverse=True
    ):
        t = re.sub(rf"\b{re.escape(wrong)}(?!\w)", right, t)
    return t


def _normalise_numbers(text: str) -> str:
    """
    Replace written number words with digits in text.
    Works longest-first so "twenty one" replaces before "one".
    """
    t = text.lower()
    for word, digit in sorted(_NUM_WORDS.items(), key=lambda x: len(x[0]), reverse=True):
        t = re.sub(rf"\b{re.escape(word)}\b", str(digit), t)
    return t


# ════════════════════════════════════════════════════════════
#  RANGE DETECTION — handles the many ways preachers say a range
#  ("verse 1 to 5", "verses 1 through to 5", "from verse 1 all the way
#  to verse 5", "starting at verse 1 ending at verse 5", word-form
#  numbers like "verse one to five", etc). All normalised to
#  (start_int, end_int).
# ════════════════════════════════════════════════════════════

# The connectors between start and end verse numbers
_CONNECTOR = (
    r"(?:"
    r"through\s+to|through|all\s+the\s+way\s+to|"
    r"down\s+to|up\s+to|unto|until|and\s+continue\s+to|"
    r"ending\s+at(?:\s+verse)?|"
    r"to"
    r")"
)

# Optional filler between connector and the end number
_FILLER = r"(?:\s+verse\s+|\s+)"

# Core pattern: captures start and end digit
_CORE = (
    rf"\bverses?\s+(\d{{1,3}})\s+{_CONNECTOR}{_FILLER}(\d{{1,3}})\b"
)

# "from verse X [connector] [verse] Y"
_FROM = (
    rf"from\s+verse\s+(\d{{1,3}})\s+{_CONNECTOR}{_FILLER}(\d{{1,3}})\b"
)

# "starting at verse X [connector] [verse] Y"
_START = (
    rf"starting\s+(?:at|from)\s+verse\s+(\d{{1,3}})\s+"
    rf"{_CONNECTOR}{_FILLER}(\d{{1,3}})\b"
)

_RANGE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [_CORE, _FROM, _START]
]


def detect_range(text: str) -> Optional[Tuple[int, int]]:
    """
    Detect a verse range in spoken text.
    Returns (start_verse, end_verse) as ints, or None.

    Handles digit and word-form numbers, and all natural
    spoken connectors a preacher might use.

    Examples:
        "verse 1 to 5"                       → (1, 5)
        "verses 1 through to 5"              → (1, 5)
        "verse 1 all the way to verse 5"     → (1, 5)
        "from verse 1 to verse 5"            → (1, 5)
        "starting at verse 1 to verse 5"     → (1, 5)
        "verse one to five"                  → (1, 5)
        "John 1 verse 1 through to 5"        → (1, 5)
        "John 3:16"                          → None
        "verse 5 to 3"  (reversed)           → None
    """
    if not text:
        return None

    # Normalise chapter/verse marker words (e.g. Twi's "nkyekyɛmu" ->
    # "verse") before word-form numbers -- _RANGE_PATTERNS below
    # hardcodes the literal English word "verse".
    t = _normalise_structural(text)
    t = _normalise_numbers(t)

    for pattern in _RANGE_PATTERNS:
        m = pattern.search(t)
        if m:
            start = int(m.group(1))
            end = int(m.group(2))
            if 1 <= start <= 176 and 1 <= end <= 176 and end > start:
                return (start, end)

    return None


# ════════════════════════════════════════════════════════════
#  DETECT VERSION CHANGE
# ════════════════════════════════════════════════════════════
def detect_version(text: str) -> Optional[str]:
    """
    Detect a Bible version change request in transcribed text.

    Returns version code e.g. "KJV", or None.
    """
    if not text:
        return None
    t = text.lower().strip()
    # Word-boundary match, not plain substring — short abbreviations like
    # "amp" are real English word fragments ("example", "camp", "sample",
    # "trample", ...), and a naive `phrase in t` fires on any of them.
    # Confirmed live: "Such an example, all human of little faith..."
    # falsely triggered a switch to AMP purely from "ex-AMP-le".
    for phrase in sorted(VERSION_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return VERSION_MAP[phrase]
    return None


# ════════════════════════════════════════════════════════════
#  DETECT NAVIGATION COMMAND
# ════════════════════════════════════════════════════════════
def detect_navigation(text: str) -> Optional[str]:
    """
    Detect a navigation command in transcribed text.

    Returns "NEXT" | "PREV" | "LAST" | "REPEAT" | "STOP" | None.
    """
    if not text:
        return None
    t = text.lower().strip()
    # Word-boundary match — see the comment on the identical fix in
    # detect_version() above; same substring-containment risk applies
    # here (e.g. a short phrase landing inside an unrelated longer word).
    for phrase in sorted(NAV_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return NAV_MAP[phrase]
    return None


# ════════════════════════════════════════════════════════════
#  DETECT ALL — convenience wrapper
# ════════════════════════════════════════════════════════════
def detect_all(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (version_code, nav_action). Either may be None.
    """
    return detect_version(text), detect_navigation(text)


# ════════════════════════════════════════════════════════════
#  DETECT VERSE JUMP
# ════════════════════════════════════════════════════════════
_VERSE_JUMP_RE = re.compile(
    r"(?:go\s+to\s+|turn\s+to\s+|read\s+)?\bverse\s+(?:number\s+)?(\d{1,3})\b",
    re.IGNORECASE,
)


def detect_verse_jump(text: str) -> Optional[int]:
    """
    Detect a within-chapter verse jump command.
    Returns the target verse number (int) or None.

    Only call this when the system already has a chapter loaded —
    the hybrid engine checks has_position() before calling.
    """
    if not text:
        return None
    # Normalise chapter/verse marker words, then word-form numbers --
    # _VERSE_JUMP_RE below hardcodes the literal English word "verse".
    t = _normalise_structural(text)
    t = _normalise_numbers(t)
    # Skip if it looks like a range (avoid "verse 1 to 5" → jump 1)
    if detect_range(text):
        return None
    m = _VERSE_JUMP_RE.search(t)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 176 else None


# ════════════════════════════════════════════════════════════
#  SESSION STATE
# ════════════════════════════════════════════════════════════
class SessionState:
    """
    Tracks the active Bible version and current verse position
    across the entire sermon session. Updated by the hybrid engine.
    """

    def __init__(self, default_version: str = "KJV"):
        self.default_version = default_version
        self.active_version = default_version
        self.current_book = None
        self.current_chapter = None
        self.current_verse = None
        self.state = "context_matching"

        # Range state
        self.range_active = False
        self.range_end_verse = None

    def update_version(self, version_code: str):
        self.active_version = version_code

    def set_default_version(self, version_code: str):
        self.default_version = version_code
        self.active_version = version_code

    def reset_to_default(self):
        self.active_version = self.default_version

    def update_position(self, book: str, chapter: int, verse: int):
        self.current_book = book
        self.current_chapter = int(chapter)
        self.current_verse = int(verse)

    def has_position(self) -> bool:
        return (
            self.current_book is not None
            and self.current_chapter is not None
            and self.current_verse is not None
        )

    def clear_position(self):
        self.current_book = None
        self.current_chapter = None
        self.current_verse = None
        self.range_active = False
        self.range_end_verse = None

    def set_state(self, new_state: str):
        self.state = new_state

    def start_range(self, end_verse: int):
        self.range_active = True
        self.range_end_verse = end_verse

    def end_range(self):
        self.range_active = False
        self.range_end_verse = None

    def __repr__(self):
        return (
            f"SessionState("
            f"version={self.active_version}, "
            f"position={self.current_book} "
            f"{self.current_chapter}:{self.current_verse}, "
            f"state={self.state}, "
            f"range={self.range_active}→{self.range_end_verse})"
        )


# ════════════════════════════════════════════════════════════
#  SELF-TEST
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    passed = failed = 0

    def check(label, got, expected):
        global passed, failed
        ok = (got == expected)
        print(f"  {'✅' if ok else '❌'}  {label}")
        if not ok:
            print(f"       got: {got!r}   expected: {expected!r}")
        passed += ok
        failed += (not ok)

    print("\n── detect_range ─────────────────────────────────────")
    check("verse 1 to 5",                   detect_range(
        "verse 1 to 5"),                              (1, 5))
    check("verses 1 to 5",                  detect_range(
        "verses 1 to 5"),                             (1, 5))
    check("verse 1 through 5",              detect_range(
        "verse 1 through 5"),                         (1, 5))
    check("verse 1 through to 5",           detect_range(
        "verse 1 through to 5"),                      (1, 5))
    check("verse 1 all the way to 5",       detect_range(
        "verse 1 all the way to verse 5"),            (1, 5))
    check("verse 1 down to 5",              detect_range(
        "verse 1 down to 5"),                         (1, 5))
    check("verse 1 up to verse 5",          detect_range(
        "verse 1 up to verse 5"),                     (1, 5))
    check("verse 1 unto verse 5",           detect_range(
        "verse 1 unto verse 5"),                      (1, 5))
    check("from verse 1 to verse 5",        detect_range(
        "from verse 1 to verse 5"),                   (1, 5))
    check("from verse 1 to 5",             detect_range(
        "from verse 1 to 5"),                          (1, 5))
    check("starting at verse 1 to 5",       detect_range(
        "starting at verse 1 to 5"),                  (1, 5))
    check("starting from verse 1 to 5",     detect_range(
        "starting from verse 1 to 5"),                (1, 5))
    check("verse one to five (words)",      detect_range(
        "verse one to five"),                         (1, 5))
    check("verses one through to five",     detect_range(
        "verses one through to five"),                (1, 5))
    check("John 1 verse 1 through to 5",    detect_range(
        "John 1 verse 1 through to 5"),               (1, 5))
    check("John chapter 1 verses 1 to 5",   detect_range(
        "John chapter 1 verses 1 to 5"),              (1, 5))
    check("reversed → None",               detect_range(
        "verse 5 to 3"),                               None)
    check("equal → None",                  detect_range(
        "verse 5 to 5"),                               None)
    check("John 3:16 → None",              detect_range(
        "John 3:16"),                                  None)

    print("\n── detect_verse_jump ────────────────────────────────")
    check("verse 4 → 4",                   detect_verse_jump(
        "verse 4"),                               4)
    check("verse 1 to 5 → None (range)",   detect_verse_jump(
        "verse 1 to 5"),                         None)
    check("John 3:16 → None",              detect_verse_jump(
        "John 3:16"),                             None)

    print("\n── detect_navigation ────────────────────────────────")
    check("next verse → NEXT",             detect_navigation(
        "next verse"),                            "NEXT")
    check("stop sinning → None",           detect_navigation(
        "stop sinning"),                          None)
    check("back in those days → None",     detect_navigation(
        "back in those days"),                    None)

    print("\n── nav_requires_confirm ─────────────────────────────")
    check("go back → needs confirm",       nav_requires_confirm(
        "go back"),                               True)
    check("go to previous → needs confirm", nav_requires_confirm(
        "go to previous"),                        True)
    check("previous verse → no confirm",   nav_requires_confirm(
        "previous verse"),                        False)
    check("go to previous verse → no confirm", nav_requires_confirm(
        "go to previous verse"),                  False)

    print("\n── detect_version ───────────────────────────────────")
    check("King James → KJV",              detect_version(
        "read in King James"),                       "KJV")
    check("the message → MSG",             detect_version(
        "the message bible"),                        "MSG")

    print(f"\nPassed: {passed}   Failed: {failed}")