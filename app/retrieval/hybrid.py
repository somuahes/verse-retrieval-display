"""
app/retrieval/hybrid.py
========================
Final hybrid retrieval engine.

Priority:
1. Version change
2. Navigation
3. Direct reference
4. Verse jump
5. Strict semantic retrieval (context-aware, grace-period-blended)
6. Manual override / no match

Important:
- Full references like "Matthew 5 verse 10" must be detected BEFORE
  verse-jump commands like "verse 10".
- Otherwise, if the system is currently on Matthew 4, "Matthew 5 verse 10"
  may wrongly jump to Matthew 4:10.

Single-speed pipeline:
- Every utterance is resolved synchronously, end to end, inside
  process() — steps 1-4 (version/nav/direct-reference/verse-jump), then
  step 5 (semantic) if none of those fired. Whatever the transcriber just
  emitted is judged the moment it arrives; nothing is held back or
  batched waiting to see if the speaker keeps going. A live system can't
  see the future, and a scripture match that resolves after the preacher
  has already moved on is functionally wrong even when it's correct.
- Semantic search is scoped strictly to the single utterance that
  triggered it — never blended with anything from a previous utterance.
  An earlier design kept a persistent rolling window of the last N words
  spanning every utterance ever, "all the time." It was removed: confirmed
  live that a leftover word from an already-decided (or already-failed)
  utterance could silently ride along into a completely unrelated later
  one and change its match — invisible to the operator, since the "heard"
  caption only ever showed the new utterance, not the stale words actually
  driving the score. Direct-reference extraction is also NOT
  context-blended — it stays keyed to the current utterance alone.
  (self._sentence_history is a separate, still-present mechanism —
  whole-utterance granularity, used only by _extract_split_reference, for
  a book+chapter reference split across two utterances by ASR endpointing.)
"""

from app.retrieval.semantic import SemanticEngine
from app.retrieval.version_detector import (
    detect_version,
    detect_navigation,
    detect_verse_jump,
    SessionState,
)
from app.retrieval.reference_extractor import (
    extract_reference,
    extract_reference_verbose,
    strip_reference_words,
)

import os
import sys
import time
import logging
import sqlite3
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, Dict, List, Tuple


sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
)


log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


TRACKING_TIMEOUT = 30

# A single spoken nav phrase (e.g. "next verse") should trigger exactly one
# navigation step. Cooldown guards against any duplicate detection from the
# same utterance being processed twice; does NOT apply to direct
# _navigate() calls from UI button clicks, so a deliberate fast
# double-click still works.
VOICE_NAV_COOLDOWN = 2.5

# Matches semantic.py's own DISPLAY_THRESHOLD (Rule 1's final-score bar).
# Previously 0.53 — independently tuned from semantic.py's 0.50 with no
# documented reason for the gap, and it caused real misses: semantic.py's
# search() already runs its own careful Rule 1/Rule 2 decision (see its
# docstring) and only returns a candidate that clears it, so re-checking
# against a DIFFERENT, stricter number here means a candidate semantic.py
# itself considers a genuine match can still get silently discarded.
# Confirmed on a real, previously-failing eval case: "nothing can
# separate us from the love of God" -> Romans 8:35 scores final=0.529 in
# semantic.py (clears its own Rule 1 at >=0.50) but was being rejected
# here for missing the old 0.53 bar by 0.001. If hybrid-level strictness
# beyond semantic.py's own threshold is wanted again later, it needs its
# own documented rationale, not an accidental gap between two numbers
# tuned separately in two files.
SEMANTIC_CONFIDENCE = 0.55
# Applies only to matches that would take the display OUT of the book
# currently locked on screen while ACTIVELY SEQUENTIAL (session.state ==
# "sequential" — the operator/voice is explicitly walking verse-by-verse
# through a passage via next/prev/last) — raised well above the base
# threshold because interrupting a deliberate walk-through needs strong
# evidence, not a borderline score. A same-book match uses
# SEMANTIC_CONFIDENCE instead regardless of state (see the "in_context"
# handling in _run_semantic) — staying in the neighborhood you're already
# in is a normal, likely-correct continuation either way.
#
# Deliberately NOT keyed off "verse_tracking" (a much broader state that
# also covers "a single detection was just displayed, nothing sequential
# is happening"). It used to be, and that was a real bug: a topical
# sermon that cites one verse per book in quick succession sets
# verse_tracking after the very first citation and then never leaves it,
# so every subsequent citation in a different book — the ordinary case
# for that kind of sermon, not a risky tangent — had to clear this much
# higher bar for no real reason. Confirmed against a real transcribed
# sermon: every semantic match in it scored under even the base 0.50, so
# this specific bug wasn't what blocked those cases, but the general
# flaw (conflating "one thing was shown" with "actively navigating") is
# real and would silently bite the moment a paraphrase's score landed in
# the 0.50-0.72 band while tracking a different book — exactly the shape
# of the two live-confirmed cases in SYSTEM_DOCUMENTATION.md's known-
# issues list (Deuteronomy 31:6 @ 0.542, Philippians 4:19 @ 0.549, both
# wrongly blocked while tracking an unrelated book).
SEMANTIC_CONFIDENCE_HI = 0.72

# Removed: a fixed-size rolling word window (ROLLING_CONTEXT_WORDS = 13)
# that persisted across utterance boundaries "all the time." Confirmed
# live it let a stale word from an already-resolved or already-failed
# utterance silently dilute a later, unrelated utterance's score — see
# the module docstring above. Semantic search now scopes strictly to the
# single utterance that triggered it, nothing older.

# whole-utterance granularity, used only by _extract_split_reference
# (needs the complete previous utterance's text to recombine with the
# current one, not a word-count window).
ROLLING_CONTEXT_SENTENCES = 3

# Metadata-only — a fixed confidence shown for direct-reference detections
# in the AI Detections panel. Not used in any matching/threshold decision.
DIRECT_MATCH_CONFIDENCE = 0.97

# Human-readable labels for navigation commands, used as source_text when
# tagging a nav-triggered verse (see _navigate) — shown to the operator
# in the AI Detections tooltip (main_ui.py's _method_reason) as
# "Navigation command — Previous Verse", not the internal short code
# "PREV".
NAV_LABELS = {
    "NEXT":   "Next Verse",
    "PREV":   "Previous Verse",
    "LAST":   "Last Verse",
    "REPEAT": "Repeat Verse",
}

DEFAULT_VERSION = "KJV"

DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "app",
    "database",
    "bible.db",
)


# ============================================================
# DATABASE HELPERS
# ============================================================

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _normalise_book_for_db(book: str) -> List[str]:
    book = str(book).strip()

    variants = [book]

    if book.lower() == "psalm":
        variants.append("Psalms")

    if book.lower() == "psalms":
        variants.append("Psalm")

    return list(dict.fromkeys(variants))


def db_get_verse(
    version: str,
    book: str,
    chapter: int,
    verse: int
) -> Optional[Dict]:
    try:
        with _conn() as c:
            for b in _normalise_book_for_db(book):
                # Exact equality, not UPPER()/LOWER()-wrapped — version and
                # book here always arrive already in the DB's own casing
                # (version via detect_version()'s VERSION_MAP / the
                # _switch_version().upper() guard; book via
                # reference_extractor._normalise_book(), which produces the
                # same Title-Case names the import script wrote). Wrapping
                # an indexed column in UPPER()/LOWER() stops SQLite from
                # using idx_lookup(version_id, book, chapter, verse) at all,
                # forcing a full scan of every verse in the version (~31k
                # rows) computing LOWER(book) per row for what should be an
                # index seek — on the hot path for every direct-reference
                # match and every next/prev/repeat navigation command.
                row = c.execute(
                    """
                    SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                    FROM verses v
                    JOIN versions ver ON v.version_id = ver.id
                    WHERE ver.code = ?
                      AND v.book = ?
                      AND v.chapter = ?
                      AND v.verse = ?
                    """,
                    (str(version), str(b), int(chapter), int(verse)),
                ).fetchone()

                if row:
                    return dict(row)

        return None

    except Exception as e:
        log.error(
            "db_get_verse(%s %s %s:%s): %s",
            version,
            book,
            chapter,
            verse,
            e,
        )
        return None


def db_get_next_verse(
    version: str,
    book: str,
    chapter: int,
    verse: int
) -> Optional[Dict]:
    try:
        with _conn() as c:
            for b in _normalise_book_for_db(book):
                # Next verse in same chapter — see db_get_verse's comment on
                # why these are plain equality, not UPPER()/LOWER()-wrapped.
                row = c.execute(
                    """
                    SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                    FROM verses v
                    JOIN versions ver ON v.version_id = ver.id
                    WHERE ver.code = ?
                      AND v.book = ?
                      AND v.chapter = ?
                      AND v.verse = ?
                    """,
                    (str(version), str(b), int(chapter), int(verse) + 1),
                ).fetchone()

                if row:
                    return dict(row)

                # First verse of next chapter
                row = c.execute(
                    """
                    SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                    FROM verses v
                    JOIN versions ver ON v.version_id = ver.id
                    WHERE ver.code = ?
                      AND v.book = ?
                      AND v.chapter = ?
                      AND v.verse = 1
                    """,
                    (str(version), str(b), int(chapter) + 1),
                ).fetchone()

                if row:
                    return dict(row)

        return None

    except Exception as e:
        log.error("db_get_next_verse: %s", e)
        return None


def db_get_prev_verse(
    version: str,
    book: str,
    chapter: int,
    verse: int
) -> Optional[Dict]:
    try:
        with _conn() as c:
            for b in _normalise_book_for_db(book):
                # Previous verse in same chapter
                if int(verse) > 1:
                    row = c.execute(
                        """
                        SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                        FROM verses v
                        JOIN versions ver ON v.version_id = ver.id
                        WHERE ver.code = ?
                          AND v.book = ?
                          AND v.chapter = ?
                          AND v.verse = ?
                        """,
                        (str(version), str(b), int(chapter), int(verse) - 1),
                    ).fetchone()

                    if row:
                        return dict(row)

                # Last verse of previous chapter
                if int(chapter) > 1:
                    row = c.execute(
                        """
                        SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                        FROM verses v
                        JOIN versions ver ON v.version_id = ver.id
                        WHERE ver.code = ?
                          AND v.book = ?
                          AND v.chapter = ?
                        ORDER BY v.verse DESC
                        LIMIT 1
                        """,
                        (str(version), str(b), int(chapter) - 1),
                    ).fetchone()

                    if row:
                        return dict(row)

        return None

    except Exception as e:
        log.error("db_get_prev_verse: %s", e)
        return None


def db_get_last_verse(
    version: str,
    book: str,
    chapter: int
) -> Optional[Dict]:
    try:
        with _conn() as c:
            for b in _normalise_book_for_db(book):
                row = c.execute(
                    """
                    SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                    FROM verses v
                    JOIN versions ver ON v.version_id = ver.id
                    WHERE ver.code = ?
                      AND v.book = ?
                      AND v.chapter = ?
                    ORDER BY v.verse DESC
                    LIMIT 1
                    """,
                    (str(version), str(b), int(chapter)),
                ).fetchone()

                if row:
                    return dict(row)

        return None

    except Exception as e:
        log.error("db_get_last_verse: %s", e)
        return None


def db_search_text(
    version: str,
    query: str,
    limit: int = 10
) -> List[Dict]:
    try:
        with _conn() as c:
            rows = c.execute(
                """
                SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                FROM verses v
                JOIN versions ver ON v.version_id = ver.id
                WHERE UPPER(ver.code) = UPPER(?)
                  AND v.text LIKE ?
                LIMIT ?
                """,
                (str(version), f"%{query}%", int(limit)),
            ).fetchall()

            return [dict(r) for r in rows]

    except Exception as e:
        log.error("db_search_text: %s", e)
        return []


def db_list_versions() -> List[str]:
    try:
        with _conn() as c:
            return [
                r[0]
                for r in c.execute(
                    "SELECT code FROM versions ORDER BY id"
                ).fetchall()
            ]

    except Exception as e:
        log.error("db_list_versions: %s", e)
        return ["KJV"]


# ============================================================
# HYBRID ENGINE
# ============================================================

class HybridEngine:
    def __init__(self, version: str = DEFAULT_VERSION):
        log.info("Initialising Hybrid Engine...")

        self.session = SessionState(default_version=version)

        # The verse actually live on the projector right now (the
        # "green" on-screen text) — distinct from session.current_book/
        # chapter/verse, which _display() updates for every engine
        # decision regardless of whether it ever reaches the screen (it
        # may just be sitting in Preview or the AI Detections queue).
        # Navigation (_navigate), within-chapter verse-jump (_run_fast
        # step 4), and version-switch's "redisplay current verse"
        # (_switch_version) all PREFER this — anchoring to what's actually
        # on the projector when something is live — but all three fall
        # back to session position when nothing is live yet. That fallback
        # matters: Go Live defaults OFF (main_ui.py) and every verse lands
        # in Preview first, so _live_position is routinely still None
        # mid-sermon. Without the fallback, voice "next verse" / "verse
        # 20" / version-switch commands would silently no-op for any
        # operator who hasn't explicitly gone live yet — this was a real
        # bug, not a hypothetical. See confirm_live(), called by the UI at
        # the single point a verse actually goes live (main_ui.py's
        # _set_live).
        self._live_position: Optional[Tuple[str, int, int]] = None

        self.semantic = SemanticEngine()
        self.semantic.load_version(version)

        self._verse_cb: Optional[Callable[[Optional[Dict]], None]] = None
        self._status_cb: Optional[Callable[[str, str], None]] = None
        self._no_match_cb: Optional[Callable[[], None]] = None

        self._last_match_time = 0.0
        self._last_key: Optional[str] = None
        self._lock = threading.Lock()

        # De-dupes nav commands detected from voice/typed text — see
        # VOICE_NAV_COOLDOWN above.
        self._last_voice_nav_cmd: Optional[str] = None
        self._last_voice_nav_time: float = 0.0

        # Whole-utterance-granularity buffer — only used by
        # _extract_split_reference, which needs a complete previous
        # utterance's text, not a word-count window.
        self._sentence_history: deque = deque(maxlen=ROLLING_CONTEXT_SENTENCES)

        # ── Semantic shadow check ────────────────────────────────────
        # A successful direct-reference match resolves and displays
        # immediately (see _run_fast) — it's already the most reliable
        # signal available, so it must never wait on semantic. But that
        # means direct-reference utterances otherwise never get a
        # semantic opinion at all, unlike everything that falls through to
        # _run_semantic. This gives every direct-reference match a
        # background-only semantic look, purely to log agreement/
        # disagreement for review — see _log_semantic_shadow. Never
        # touches the display or session state.
        self._shadow_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="bible-ai-shadow-semantic"
        )

        log.info(
            "✅ Hybrid Engine ready — version=%s available=%s",
            version,
            db_list_versions(),
        )

    # ========================================================
    # CALLBACKS
    # ========================================================

    def set_verse_callback(self, cb: Callable[[Optional[Dict]], None]):
        self._verse_cb = cb

    def set_status_callback(self, cb: Callable[[str, str], None]):
        self._status_cb = cb

    def set_no_match_callback(self, cb: Callable[[], None]):
        self._no_match_cb = cb

    # ========================================================
    # SAFE SESSION HELPERS
    # ========================================================

    def _has_position(self) -> bool:
        return bool(
            getattr(self.session, "current_book", None)
            and getattr(self.session, "current_chapter", None)
            and getattr(self.session, "current_verse", None)
        )

    def _allowed_languages(self) -> set:
        """Which languages' book-name/number/chapter-verse-marker
        vocabulary reference_extractor.py should match against, given
        the CURRENTLY active Bible version. Twi is gated behind the Twi
        version specifically — without this, saying a Twi book name
        ("Yohane 3:16") while on KJV would resolve the reference but
        still display KJV's English text, which is confusing and not
        what an operator means by "the Twi version." English stays
        always-allowed regardless (it's the base/default language every
        other English version — KJV, BBE, ... — shares, and there's no
        ambiguity about "which English version" the way there could be
        about which non-English language is meant)."""
        if str(self.session.active_version).upper() == "TWI":
            return {"en", "twi"}
        return {"en"}

    def _clear_position(self):
        if hasattr(self.session, "clear_position"):
            self.session.clear_position()
            return

        self.session.current_book = None
        self.session.current_chapter = None
        self.session.current_verse = None

    def _current_context_book(self) -> Optional[str]:
        """The book to scope semantic search to right now, or None. Any
        verse_tracking state (even a single one-off detection, not just
        active sequential navigation) is enough to try the same-book
        scoped pass first — continuing in the book just shown is the
        ordinary, low-risk case regardless of how it got locked in. This
        is a different, broader condition than SEMANTIC_CONFIDENCE_HI's
        "sequential-only" gate on leaving that book — trying same-book
        first is cheap and safe; the HI bar is specifically about the
        much riskier case of jumping to a DIFFERENT book, which only
        needs the higher bar while a deliberate walk-through is active."""
        tracking = getattr(self.session, "state", "") == "verse_tracking"
        return (
            str(self.session.current_book)
            if tracking and self._has_position()
            else None
        )

    def _extract_split_reference(
        self, text: str
    ) -> Tuple[Optional[Tuple[str, int, int]], Optional[str]]:
        """Fallback for a spoken reference split across two utterances by
        ASR endpointing (e.g. a pause between "Leviticus" and "27") —
        neither utterance alone contains a complete book+chapter pattern,
        so the single-utterance extract_reference() call above misses
        both, and the utterance falls through to the slow semantic path
        with no real chance of matching (it isn't a paraphrase of any
        verse).

        Only tried when the current utterance is a short trailing
        fragment (<=4 words, cheap to rule out on the common case) —
        combined with requiring a real book alias to already be present
        in the *previous* utterance for extract_reference to find
        anything at all in the joined text, this is narrow enough not to
        revive an unrelated reference from an earlier utterance on a
        random short one. Returns (ref, combined_source_text) or
        (None, None).
        """
        if len(text.split()) > 4:
            return None, None

        if len(self._sentence_history) < 2:
            return None, None

        previous = self._sentence_history[-2]
        combined = f"{previous} {text}"
        ref = extract_reference(combined, allowed_languages=self._allowed_languages())

        if ref:
            return ref, combined

        return None, None

    # ========================================================
    # STATE
    # ========================================================

    def _set_state(self, state: str):
        if getattr(self.session, "state", None) == state:
            return

        self.session.set_state(state)

        labels = {
            "context_matching": "Listening — full detection active",
            "verse_tracking": "Verse locked on screen",
            "sequential": "Sequential — moving through chapter",
        }

        log.info("State → %s", state)

        if self._status_cb:
            self._status_cb(state, labels.get(state, state))

    def _check_timeout(self):
        if (
            getattr(self.session, "state", "") == "verse_tracking"
            and time.time() - self._last_match_time > TRACKING_TIMEOUT
        ):
            log.info("Tracking timeout — returning to context matching")
            self._set_state("context_matching")

    # ========================================================
    # COMMAND TAGGING
    # ========================================================
    # Direct/semantic detections are tagged with match_type inside their
    # own steps because they carry a real, graded confidence. Navigation,
    # verse-jump, and version-switch are deterministic operator/voice
    # commands, not probabilistic guesses — but the operator still wants
    # every verse-triggering event visible in the AI Detections panel and
    # audit trail, not just AI guesses. Tagged with confidence=1.0 (no
    # ambiguity to grade) and a distinct match_type so the UI can style
    # them as commands rather than detections, and so _on_verse's
    # Auto/Manual gate — which only holds back "semantic" — never touches
    # them.

    def _tag_command(self, verse: Dict, match_type: str, source_text: str):
        verse["match_type"] = match_type
        verse["confidence"] = 1.0
        verse["matched_at"] = time.time()
        verse["source_text"] = source_text
        return verse

    # ========================================================
    # DISPLAY
    # ========================================================

    def _display(self, verse: Optional[Dict], force: bool = False):
        if not verse:
            self._last_key = None

            if self._verse_cb:
                self._verse_cb(None)

            return

        key = (
            f"{verse.get('book')}_"
            f"{verse.get('chapter')}_"
            f"{verse.get('verse')}_"
            f"{verse.get('version', '')}"
        )

        if not force and key == self._last_key:
            log.debug("Skipping duplicate verse: %s", key)
            return

        self._last_key = key
        self._last_match_time = time.time()

        self.session.update_position(
            str(verse.get("book", "")),
            int(verse.get("chapter", 0)),
            int(verse.get("verse", 0)),
        )

        self._set_state("verse_tracking")

        log.info(
            "📖 %s %s:%s (%s)",
            verse.get("book"),
            verse.get("chapter"),
            verse.get("verse"),
            verse.get("version", ""),
        )

        if self._verse_cb:
            self._verse_cb(verse)

    def confirm_live(self, verse: Optional[Dict]):
        """Called by the UI at the single point a verse actually becomes
        live on the projector — see main_ui.py's _set_live(). This is
        the ONLY place self._live_position is updated; see its comment
        in __init__ for why it's kept separate from session.current_book
        /chapter/verse."""
        if verse:
            self._live_position = (
                str(verse.get("book", "")),
                int(verse.get("chapter", 0)),
                int(verse.get("verse", 0)),
            )
        else:
            self._live_position = None

    # ========================================================
    # NAVIGATION
    # ========================================================

    def _navigate(self, cmd: str):
        cmd = str(cmd).upper().strip()

        # Prefer the live projector position when there is one. But every
        # verse lands in Preview before it's ever pushed Live (Go Live
        # defaults OFF — see main_ui.py), so _live_position is routinely
        # still None even mid-sermon if the operator hasn't explicitly
        # gone live yet. Falling back to session.current_book/chapter/
        # verse — updated by every _display() call regardless of Live/
        # Preview, see __init__'s comment on _live_position — means voice
        # navigation still moves whatever's currently being tracked
        # instead of silently no-op'ing until the first Live push. Same
        # fallback already applied to _switch_version for the identical
        # reason.
        if self._live_position:
            book, chapter, verse = self._live_position
        elif self._has_position():
            book = str(self.session.current_book)
            chapter = int(self.session.current_chapter)
            verse = int(self.session.current_verse)
        else:
            book = None

        if cmd != "STOP" and book is None:
            log.info(
                "Navigation '%s' ignored — no verse currently tracked "
                "(live or preview)",
                cmd,
            )
            return

        version = str(self.session.active_version)
        if book is None:
            book, chapter, verse = "", 0, 0

        # _set_state("sequential") runs AFTER _display(), not before — see
        # the comment on _display() itself for why the order matters:
        # _display() unconditionally sets state to "verse_tracking" as
        # part of every call, which would otherwise immediately overwrite
        # "sequential" back to "verse_tracking" in the very same command,
        # making the sequential state unreachable by anything that runs
        # afterward (including SEMANTIC_CONFIDENCE_HI's own state check
        # and the operator-facing status label).
        if cmd == "NEXT":
            r = db_get_next_verse(version, book, chapter, verse)
            if r:
                self._tag_command(r, "navigation", NAV_LABELS.get(cmd, cmd))
                self._display(r)
                self._set_state("sequential")

        elif cmd == "PREV":
            r = db_get_prev_verse(version, book, chapter, verse)
            if r:
                self._tag_command(r, "navigation", NAV_LABELS.get(cmd, cmd))
                self._display(r)
                self._set_state("sequential")

        elif cmd == "LAST":
            r = db_get_last_verse(version, book, chapter)
            if r:
                self._tag_command(r, "navigation", NAV_LABELS.get(cmd, cmd))
                self._display(r)
                self._set_state("sequential")

        elif cmd == "REPEAT":
            r = db_get_verse(version, book, chapter, verse)
            if r:
                self._tag_command(r, "navigation", NAV_LABELS.get(cmd, cmd))
                self._display(r, force=True)

        elif cmd == "STOP":
            self._last_key = None
            self._clear_position()
            self._set_state("context_matching")

            if self._verse_cb:
                self._verse_cb(None)

    # ========================================================
    # VERSION SWITCH
    # ========================================================

    def _switch_version(self, new_ver: str):
        new_ver = str(new_ver).upper().strip()
        current = str(self.session.active_version).upper().strip()

        if new_ver == current:
            return

        available = [v.upper() for v in db_list_versions()]

        if new_ver not in available:
            log.warning(
                "Version '%s' is not installed. Available: %s",
                new_ver,
                available,
            )

            if self._status_cb:
                self._status_cb(
                    "version_error",
                    f"{new_ver} not installed — staying on {current}",
                )

            return

        log.info("Version switch: %s → %s", current, new_ver)

        self.session.update_version(new_ver)
        self.semantic.switch_version(new_ver)

        if self._status_cb:
            self._status_cb("version_switch", f"Switched to {new_ver}")

        # Prefer the live projector position when there is one (keeps the
        # on-screen verse anchored to what's actually live). But every
        # verse lands in Preview before it's ever pushed Live (see
        # main_ui.py's _promote_to_preview) — if nothing has gone live
        # yet, _live_position is still None even though the operator has
        # a verse showing in Preview, and that Preview text would
        # otherwise keep displaying the OLD version after the switch.
        # Falling back to session.current_book/chapter/verse (updated by
        # every _display() call regardless of Live/Preview — see
        # __init__'s comment on _live_position) covers that case too.
        if self._live_position:
            book, chapter, verse_no = self._live_position
        elif self._has_position():
            book = str(self.session.current_book)
            chapter = int(self.session.current_chapter)
            verse_no = int(self.session.current_verse)
        else:
            book = None

        if book:
            r = db_get_verse(new_ver, book, chapter, verse_no)

            if r:
                self._tag_command(r, "version_switch", f"→ {new_ver}")
                self._last_key = None
                self._display(r, force=True)

    # ========================================================
    # MAIN PROCESS — single public entry point, called identically by
    # every caller (live ASR, the operator UI, the evaluation harness's
    # separate replica, the legacy panel, this file's own REPL below).
    # ========================================================

    def process(self, text: str):
        """
        Run one utterance/typed line through the pipeline synchronously,
        end to end: steps 1-4 (version/nav/direct-reference/verse-jump),
        then step 5 (semantic) if none of those fired. No queueing, no
        grace period — whatever the transcriber (or the operator) just
        said is judged the moment it arrives, so nothing is held back
        waiting to see if more speech follows.
        """
        # No minimum length beyond non-empty — a bare 1-2 character verse
        # number ("5", "10") is meaningful in this domain and must reach
        # the pipeline; the fixed 3-char floor this used to have silently
        # dropped exactly that case. Real noise is filtered upstream, in
        # the transcriber's own confidence/VAD/repetition gates.
        text = (text or "").strip()

        if not text:
            return

        with self._lock:
            # Whole-utterance history for _extract_split_reference only —
            # see ROLLING_CONTEXT_SENTENCES.
            self._sentence_history.append(text)

            handled = self._run_fast(text)

            if handled:
                return

            self._run_semantic(text)

    def _run_fast(self, text: str) -> bool:
        """Steps 1-4. Returns True if the utterance was fully handled
        (and any pending semantic grace-period group should be
        discarded — a decisive action just happened, so stale queued
        context is no longer relevant)."""
        t0 = time.time()
        log.info("▶️ %s", text[:100])

        self._check_timeout()

        # ====================================================
        # 1. Version change
        # ====================================================
        new_ver = detect_version(text)

        if new_ver:
            self._switch_version(new_ver)
            return True

        # ====================================================
        # 2. Navigation
        # ====================================================
        nav = detect_navigation(text)

        if nav:
            now = time.time()
            if (
                nav == self._last_voice_nav_cmd
                and now - self._last_voice_nav_time < VOICE_NAV_COOLDOWN
            ):
                log.info(
                    "Navigation '%s' ignored — repeat within %.1fs, likely "
                    "the same utterance re-appearing in overlapping "
                    "transcription windows",
                    nav,
                    now - self._last_voice_nav_time,
                )
                return True

            self._last_voice_nav_cmd = nav
            self._last_voice_nav_time = now
            self._navigate(nav)
            return True

        # ====================================================
        # 3. Direct reference ALWAYS wins
        #
        # This must come before verse jump.
        #
        # Example:
        #   Current position: Matthew 4:21
        #   Input: "Matthew 5 verse 10"
        #
        # Correct:
        #   Matthew 5:10
        #
        # Wrong old behavior:
        #   Detects only "verse 10"
        #   Jumps to Matthew 4:10
        # ====================================================
        ref_info = extract_reference_verbose(
            text, allowed_languages=self._allowed_languages()
        )
        ref_source_text = text
        # True whenever the caller should trust `verse` directly (chapter
        # 1 was actually spoken, or came from the split-reference
        # fallback, which only ever combines short trailing fragments —
        # not the kind of utterance that also carries paraphrase content
        # worth a scoped semantic check).
        verse_explicit = True

        if ref_info:
            book, chapter, verse, verse_explicit = ref_info
        else:
            # A spoken reference can land as two separate utterances if
            # the speaker pauses between the book name and the number
            # (e.g. "Let's go to Leviticus" ... "27") — signal-driven
            # endpointing has no way to know that's one reference, not
            # two. Neither utterance alone contains a complete
            # book+chapter pattern, so the check above misses both and
            # this silently fell through to the slow semantic path with
            # no real chance of matching. See _extract_split_reference.
            split_ref, ref_source_text = self._extract_split_reference(text)
            if split_ref:
                book, chapter, verse = split_ref
            else:
                book = chapter = verse = None

        if book is not None:
            match_type = "direct"
            confidence = DIRECT_MATCH_CONFIDENCE

            if not verse_explicit:
                # A chapter was named but no verse number was given — the
                # rest of the utterance may paraphrase a specific verse
                # within that chapter (e.g. "in Romans 12 we should not
                # think of ourselves more highly than we ought" -> Romans
                # 12:3, not the naive 12:1 default). Try a chapter-scoped
                # semantic search first. A genuine bare chapter jump
                # ("let's go to Romans 12") has no real paraphrase content
                # to match, so this correctly finds nothing and falls
                # through to the verse-1 default below, same as before.
                stripped = strip_reference_words(text, book)
                scoped = self.semantic.search_within_chapter(stripped, book, chapter)

                if scoped:
                    verse = int(scoped.get("verse", verse))
                    match_type = "semantic"
                    confidence = float(scoped.get("final_score", 0))
                    log.info(
                        "Chapter-scoped semantic match within %s %s: "
                        "verse %s (score=%.3f)",
                        book, chapter, verse, confidence,
                    )

            log.info("Direct ref: %s %s:%s", book, chapter, verse)

            r = db_get_verse(
                str(self.session.active_version),
                str(book),
                int(chapter),
                int(verse),
            )

            if r:
                r["match_type"] = match_type
                r["confidence"] = confidence
                r["matched_at"] = time.time()
                r["source_text"] = ref_source_text
                self._set_state("context_matching")
                self._display(r)
                log.info(
                    "Direct-reference path resolved in %.0fms",
                    (time.time() - t0) * 1000.0,
                )
                self._log_semantic_shadow(ref_source_text, r)
                return True

            log.warning(
                "Reference detected but not found in DB: %s %s:%s",
                book,
                chapter,
                verse,
            )

        # ====================================================
        # 4. Verse jump
        #
        # Only runs if NO direct reference was found.
        #
        # Example:
        #   Current position: John 3:16
        #   Input: "verse 20"
        #   Result: John 3:20
        # ====================================================
        jump_book = jump_chapter = None
        if self._live_position:
            jump_book, jump_chapter, _ = self._live_position
        elif self._has_position():
            jump_book = str(self.session.current_book)
            jump_chapter = int(self.session.current_chapter)

        if jump_book:
            target = detect_verse_jump(text)

            if target:
                r = db_get_verse(
                    str(self.session.active_version),
                    jump_book,
                    jump_chapter,
                    int(target),
                )

                if r:
                    log.info(
                        "Verse jump → %s %s:%s",
                        r["book"],
                        r["chapter"],
                        r["verse"],
                    )

                    self._tag_command(r, "verse_jump", text)
                    self._display(r)
                    self._set_state("sequential")
                    return True

        return False

    # ========================================================
    # SEMANTIC — shadow verification + resolution
    # ========================================================

    def _log_semantic_shadow(self, text: str, direct_match: Dict):
        """Fire-and-forget: run semantic search on an utterance that a
        direct-reference match already resolved and displayed, purely to
        log whether semantic agrees — never overrides the display or any
        session state, and never blocks the caller (runs on
        self._shadow_executor). See the comment on that executor in
        __init__ for why this exists."""
        context_book = self._current_context_book()
        # See SEMANTIC_CONFIDENCE_HI's comment — the higher cross-book bar
        # applies only while actively sequential-navigating, not merely
        # "tracking" (which covers any single prior detection too).
        actively_sequential = getattr(self.session, "state", "") == "sequential"

        def _run():
            try:
                sem = self.semantic.search(text, context_book=context_book)
            except Exception as e:
                log.debug("Semantic shadow check failed: %s", e)
                return

            if not sem:
                return

            final_score = float(sem.get("final_score", 0))
            in_context = bool(sem.get("in_context", False))
            threshold = (
                SEMANTIC_CONFIDENCE
                if in_context
                else (SEMANTIC_CONFIDENCE_HI if actively_sequential else SEMANTIC_CONFIDENCE)
            )

            if final_score < threshold:
                return

            agrees = (
                str(sem.get("book", "")).strip().lower()
                == str(direct_match.get("book", "")).strip().lower()
                and int(sem.get("chapter", 0)) == int(direct_match.get("chapter", 0))
                and int(sem.get("verse", 0)) == int(direct_match.get("verse", 0))
            )

            if agrees:
                log.info(
                    "Semantic shadow check: agrees with direct match "
                    "(%s %s:%s, score=%.3f)",
                    sem.get("book"), sem.get("chapter"), sem.get("verse"),
                    final_score,
                )
                return

            log.warning(
                "Semantic shadow check DISAGREES with direct match — "
                "displayed %s %s:%s, semantic suggests %s %s:%s "
                "(score=%.3f) for: %r",
                direct_match.get("book"), direct_match.get("chapter"),
                direct_match.get("verse"), sem.get("book"),
                sem.get("chapter"), sem.get("verse"), final_score,
                text[:100],
            )

            # A genuine, independent match for DIFFERENT content than the
            # reference itself — not a disagreement to merely log and
            # discard. The common real case: a preacher cites a verse by
            # number and, in the same breath, paraphrases a second,
            # different verse ("...Romans 1:16, for I am not ashamed...
            # We receive forgiveness, redemption and acceptance before
            # God"). Direct-reference wins the utterance and returns
            # immediately (see _run_fast), so that second citation would
            # otherwise never reach semantic search at all — this is the
            # general fix for that whole utterance shape, not a curated
            # phrase for one sermon's wording. Surfaced as a detection
            # only (see _notify_secondary_detection) — never overrides the
            # direct match already on screen or touches session position;
            # the same Auto/Manual gating any other semantic detection
            # gets applies here too, via the normal verse callback.
            row = db_get_verse(
                str(self.session.active_version),
                str(sem.get("book", "")),
                int(sem.get("chapter", 0)),
                int(sem.get("verse", 0)),
            )
            notify = row or sem
            notify["match_type"] = "semantic"
            notify["confidence"] = final_score
            notify["matched_at"] = time.time()
            notify["source_text"] = text
            notify.setdefault("version", str(self.session.active_version))
            self._notify_secondary_detection(notify)

        self._shadow_executor.submit(_run)

    def _notify_secondary_detection(self, verse: Dict):
        """Surface a detection to the operator UI (AI Detections panel,
        Auto-mode promotion if eligible) WITHOUT any of the side effects
        _display() has: does not touch self._last_key, does not call
        session.update_position(), does not change session.state. This is
        for a genuine second finding riding alongside an utterance
        another mechanism already resolved and displayed (see
        _log_semantic_shadow) — it must never silently become the
        engine's "current position" (which would corrupt what a
        subsequent next/prev/verse-jump command applies to) or steal the
        duplicate-key dedupe slot from the verse actually on screen."""
        if self._verse_cb:
            self._verse_cb(verse)

    def _run_semantic(self, text: str):
        """Step 5 — context-aware semantic search.

        text: the single utterance that triggered this resolution. This is
        both what gets searched and what gets shown to the operator as
        "heard" — deliberately the same string, so there's no way for a
        match to be justified on screen by words the operator never saw.
        An earlier design searched a persistent rolling word window
        instead, wider than what it displayed, and delayed this whole step
        behind a grace-period timer to blend it with whatever utterance
        came next; confirmed live that a stale word from an already-decided
        group could silently ride into an unrelated later utterance's
        score, and that the delay itself made a correct match arrive too
        late to be useful. See the module docstring.
        """
        t0 = time.time()
        self._check_timeout()

        # See SEMANTIC_CONFIDENCE_HI's comment — the higher cross-book bar
        # applies only while actively sequential-navigating, not merely
        # "tracking" (which covers any single prior detection too).
        actively_sequential = getattr(self.session, "state", "") == "sequential"
        context_book = self._current_context_book()

        log.info("Semantic (%d words): %r", len(text.split()), text)

        sem = self.semantic.search(text, context_book=context_book)

        if sem:
            final_score = float(sem.get("final_score", 0))
            in_context = bool(sem.get("in_context", False))
            threshold = (
                SEMANTIC_CONFIDENCE
                if in_context
                else (SEMANTIC_CONFIDENCE_HI if actively_sequential else SEMANTIC_CONFIDENCE)
            )

            # Judged purely against the absolute threshold for this
            # candidate (in-context vs tracking vs default — see above),
            # not against whatever confidence happens to already be on
            # screen. A same-book verse read immediately after a
            # high-confidence one can legitimately score lower and still
            # clear its own bar — e.g. Romans 7:24 read right after 7:23 —
            # and previously got silently blocked by a relative "must beat
            # the on-screen score" gate that's been removed.
            if final_score >= threshold:
                log.info(
                    "Semantic: %s %s:%s score=%.3f method=%s "
                    "(resolved in %.0fms)",
                    sem.get("book"),
                    sem.get("chapter"),
                    sem.get("verse"),
                    final_score,
                    sem.get("method", ""),
                    (time.time() - t0) * 1000.0,
                )

                r = db_get_verse(
                    str(self.session.active_version),
                    str(sem.get("book", "")),
                    int(sem.get("chapter", 0)),
                    int(sem.get("verse", 0)),
                )

                if r:
                    r["match_type"] = "semantic"
                    r["confidence"] = final_score
                    r["matched_at"] = time.time()
                    r["source_text"] = text
                    self._display(r)
                    return

                sem["version"] = str(self.session.active_version)
                sem["match_type"] = "semantic"
                sem["confidence"] = final_score
                sem["matched_at"] = time.time()
                sem["source_text"] = text
                self._display(sem)
                return

        # ====================================================
        # 6. No match
        # ====================================================
        log.debug("No match: %s", text[:80])

        if getattr(self.session, "state", "") == "context_matching":
            if self._no_match_cb:
                self._no_match_cb()

    # ========================================================
    # MANUAL OVERRIDE
    # ========================================================

    def manual_display(self, book: str, chapter: int, verse: int):
        r = db_get_verse(
            str(self.session.active_version),
            str(book),
            int(chapter),
            int(verse),
        )

        if r:
            self._tag_command(r, "manual", f"{book} {chapter}:{verse}")
            self._last_key = None
            self._display(r, force=True)

    def manual_search(self, query: str) -> List[Dict]:
        return db_search_text(str(self.session.active_version), query)

    # ========================================================
    # INFO
    # ========================================================

    def info(self):
        print(f"\n{'─' * 45}")
        print(f"  Version   : {self.session.active_version}")
        print(f"  State     : {self.session.state}")
        print(
            f"  Position  : {self.session.current_book} "
            f"{self.session.current_chapter}:{self.session.current_verse}"
        )
        print(f"  Available : {db_list_versions()}")
        print(
            f"  Threshold : {SEMANTIC_CONFIDENCE} / {SEMANTIC_CONFIDENCE_HI}")
        print(f"{'─' * 45}\n")


# ============================================================
# INTERACTIVE TEST
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  HYBRID ENGINE — Interactive Test")
    print("=" * 60)

    engine = HybridEngine()
    engine.info()

    def on_verse(verse):
        if verse:
            t = str(verse.get("text", ""))
            print(
                f"\n📖 {verse.get('book')} "
                f"{verse.get('chapter')}:{verse.get('verse')} "
                f"({verse.get('version', '')}) — "
                f"{t[:100]}{'...' if len(t) > 100 else ''}"
            )
        else:
            print("\n🔇 Display cleared")

    def on_status(state, desc):
        print(f"⚙️ [{state}] {desc}")

    def on_no_match():
        print("⚠️ No match — manual override available")

    engine.set_verse_callback(on_verse)
    engine.set_status_callback(on_status)
    engine.set_no_match_callback(on_no_match)

    print("  <sermon text>        — auto detect (resolves synchronously)")
    print("  Matthew 6.33         — direct reference")
    print("  Matthew 5            — book + chapter, should go to verse 1")
    print("  Matthew 5 verse 10   — full reference, should NOT act as verse jump")
    print("  next verse           — advance")
    print("  previous verse       — go back")
    print("  verse 4              — jump within current chapter")
    print("  read that again      — repeat")
    print("  stop display         — clear screen")
    print("  read in BBE          — switch version")
    print("  search <query>       — keyword search")
    print("  info                 — status")
    print("  q                    — quit")
    print()

    while True:
        try:
            text = input("Sermon: ").strip()

        except (KeyboardInterrupt, EOFError):
            print("\n⏹️ Stopped.")
            break

        if not text:
            continue

        if text.lower() == "q":
            break

        if text.lower() == "info":
            engine.info()
            continue

        if text.lower().startswith("search "):
            results = engine.manual_search(text[7:].strip())

            if results:
                print(f"\nFound {len(results)} results:")

                for i, r in enumerate(results, 1):
                    print(
                        f"  {i}. {r['book']} {r['chapter']}:{r['verse']} "
                        f"— {r['text'][:100]}..."
                    )

            else:
                print("No results found.")

            continue

        engine.process(text)
