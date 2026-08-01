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

for _lang in _LANGUAGES:
    VERSION_MAP.update(_lang.VERSION_PHRASES)
    NAV_MAP.update(_lang.NAV_PHRASES)
    _NUM_WORDS.update(_lang.NUM_WORDS)


# ════════════════════════════════════════════════════════════
#  AMBIGUOUS PREV PHRASES
#
#  These phrases map to PREV in NAV_MAP but are short or
#  common enough that they can appear as casual speech in a
#  live sermon ("go back to what I was saying", congregation
#  chatting, etc.).
#
#  The hybrid engine uses nav_requires_confirm() to decide
#  whether to require the phrase to appear TWICE in consecutive
#  transcripts before executing the PREV navigation.
#
#  Explicit phrases like "previous verse" or "go to previous
#  verse" are long and unambiguous enough to fire immediately.
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
    # Longest-phrase-wins, mirroring detect_navigation()'s own algorithm —
    # a plain "phrase in t" substring check was wrong: "go to previous" is
    # a genuine, word-bounded PREFIX of "go to previous verse", so it
    # always matched there too and incorrectly demanded confirmation for
    # an explicit, unambiguous phrase. Finding whichever NAV_MAP phrase
    # would ACTUALLY fire first (same rule detect_navigation uses) and
    # checking THAT one against PREV_CONFIRM_PHRASES is the only way to
    # tell "go to previous" alone apart from it being a prefix of a longer
    # explicit phrase.
    for phrase in sorted(NAV_MAP, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return phrase in PREV_CONFIRM_PHRASES
    return False


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
#  RANGE DETECTION
#
#  Human preachers say ranges in MANY ways:
#
#  "verse 1 to 5"
#  "verse 1 through 5"
#  "verse 1 through to 5"
#  "verse 1 down to verse 5"
#  "verses 1 to 5"
#  "verses 1 through to 5"
#  "from verse 1 to verse 5"
#  "from verse 1 all the way to verse 5"
#  "verse 1 up to verse 5"
#  "verse 1 unto verse 5"
#  "John 1 verse 1 to 5"
#  "John chapter 1 verses 1 to 5"
#  "read from verse 1 to 5"
#  "let us read verses 1 to 5"
#  "starting at verse 1 ending at verse 5"
#  "starting from verse 1 to verse 5"
#  "we will read verse 1 and continue to verse 5"
#  "verse one to five"   ← word-form numbers
#
#  All normalised to (start_int, end_int).
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
    rf"verses?\s+(\d{{1,3}})\s+{_CONNECTOR}{_FILLER}(\d{{1,3}})\b"
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

    # Normalise word-form numbers first
    t = _normalise_numbers(text)

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
    r"(?:go\s+to\s+|turn\s+to\s+|read\s+)?verse\s+(?:number\s+)?(\d{1,3})\b",
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
    # Normalise word numbers first
    t = _normalise_numbers(text)
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