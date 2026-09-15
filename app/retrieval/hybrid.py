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
- context_decision.py's ContextManager (self.context_manager below) is NOT
  a reintroduction of that reverted rolling-word-window design. It never
  touches what gets SEARCHED — semantic.search_top_k() still runs against
  exactly the current utterance's own text, same as always, so a match is
  still only ever justified by words the operator actually just saw
  logged. What it accumulates across utterances is evidence behind a
  candidate VERSE already independently found each time, to decide
  whether to act on it yet — the failure mode that got the old design
  reverted (a stale word silently riding into an unrelated utterance's
  score) can't happen here, because no utterance's search input is ever
  blended with another's.
"""

from app.retrieval.semantic import SemanticEngine
from app.retrieval.context_decision import (
    ContextManager,
    Decision,
    DecisionResult,
    ScriptureCandidate,
)
from app.retrieval.version_detector import (
    detect_version,
    detect_navigation,
    detect_verse_jump,
    nav_requires_confirm,
    SessionState,
)
from app.retrieval.reference_extractor import (
    extract_reference,
    extract_reference_verbose,
    extract_book_only,
    strip_reference_words,
)

import os
import sys
import time
import logging
import sqlite3
import threading
import numpy as np
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

# How long a bare book mention with no resolvable verse ("in the book of
# Ezekiel", said as its own utterance with no accompanying paraphrase)
# keeps scoping semantic search to that book — see _announced_book in
# __init__ and its use in _current_context_book. Longer than
# TRACKING_TIMEOUT: an operator naming a book before preaching from it for
# a while is a slower-paced signal than an actively-tracked verse, and
# should survive a few sentences of scene-setting before the first real
# paraphrase arrives.
ANNOUNCED_BOOK_TIMEOUT = 90.0

# A single spoken nav phrase (e.g. "next verse") should trigger exactly one
# navigation step. Cooldown guards against any duplicate detection from the
# same utterance being processed twice; does NOT apply to direct
# _navigate() calls from UI button clicks, so a deliberate fast
# double-click still works.
VOICE_NAV_COOLDOWN = 2.5

# Ambiguous PREV phrases ("go back", "go to previous" — see
# version_detector.py's PREV_CONFIRM_PHRASES/nav_requires_confirm) are
# common enough in ordinary rhetorical preaching ("go back to what I was
# saying") that firing on a single mention is a real false-positive risk,
# confirmed live: it silently jumped the display back a verse on
# completely non-navigational speech. Genuine navigational intent
# naturally repeats within a few seconds ("go back... let's go back a
# verse"); a one-off rhetorical use doesn't. This window is how long a
# second, matching mention has to arrive before the pending confirmation
# is dropped — long enough to allow a short pause or an intervening
# sentence, short enough that it's clearly still "the same moment."
AMBIGUOUS_NAV_CONFIRM_WINDOW = 8.0

# Matches semantic.py's own DISPLAY_THRESHOLD — must stay equal. A
# stricter number here would silently re-reject a candidate semantic.py's
# own Rule 1/Rule 2 already accepted (confirmed live: Romans 8:35 at
# final=0.529 was wrongly rejected by an old, independently-tuned 0.53).
# Drifted out of sync again, caught 2026-09-15: DISPLAY_THRESHOLD was
# lowered 0.58 -> 0.50 (Judges 13:18, see its own comment there) but this
# constant was left at 0.58 — exactly the same class of bug as the
# Romans 8:35 case above, silently vetoing candidates semantic.py itself
# had already accepted. Also silently neutered that day's own
# threshold-sweep validation: app/evaluation/pipeline.py imports this
# name directly (`from app.retrieval.hybrid import ... SEMANTIC_CONFIDENCE`,
# bound once at import time), so the eval harness kept re-applying the
# stale 0.58 regardless of what semantic.py's own DISPLAY_THRESHOLD was
# set to underneath it.
SEMANTIC_CONFIDENCE = 0.50
# Raised bar for a match that would take the display OUT of the book
# locked on screen while ACTIVELY SEQUENTIAL (walking verse-by-verse via
# next/prev/last) — interrupting a deliberate walk-through needs strong
# evidence. A same-book match still uses SEMANTIC_CONFIDENCE regardless
# of state; see _run_semantic's "in_context" handling.
#
# Deliberately keyed off "sequential", not the broader "verse_tracking"
# (also true after a single detection with nothing sequential happening)
# — that used to be a real bug: a topical sermon citing one verse per
# book in quick succession sets verse_tracking after the first citation
# and never leaves it, so every ordinary subsequent citation had to clear
# this bar for no reason. Known, deliberately unfixed gap from this same
# state: a genuine cross-book topic change scoring in [0.50, 0.72) is
# still wrongly blocked (confirmed: Deuteronomy 31:6 @ 0.542, Philippians
# 4:19 @ 0.549) — see PROGRESS.md §20c for why this wasn't retuned.
SEMANTIC_CONFIDENCE_HI = 0.72

# ROLLING_CONTEXT_SENTENCES: whole-utterance granularity, used only by
# _extract_split_reference (needs a complete previous utterance to
# recombine with the current one, not a word-count window). A separate
# word-count rolling window for semantic search itself was tried and
# removed — it let a stale word from an already-resolved utterance dilute
# a later, unrelated one's score; semantic search now scopes strictly to
# the single utterance that triggered it.
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
                # Exact equality, not UPPER()/LOWER()-wrapped — version/book
                # always arrive in the DB's own casing already, and
                # wrapping an indexed column stops SQLite using idx_lookup,
                # forcing a full ~31k-row scan on this hot path.
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


def db_list_books(version: str) -> List[str]:
    """Books in canonical Bible order (import order — the source JSON
    files list books Genesis→Revelation, so the lowest row id per book
    reproduces that order without a hardcoded book list)."""
    try:
        with _conn() as c:
            rows = c.execute(
                """
                SELECT v.book
                FROM verses v
                JOIN versions ver ON v.version_id = ver.id
                WHERE ver.code = ?
                GROUP BY v.book
                ORDER BY MIN(v.id)
                """,
                (str(version),),
            ).fetchall()
            return [r[0] for r in rows]

    except Exception as e:
        log.error("db_list_books: %s", e)
        return []


def db_chapter_count(version: str, book: str) -> int:
    try:
        with _conn() as c:
            for b in _normalise_book_for_db(book):
                row = c.execute(
                    """
                    SELECT MAX(v.chapter)
                    FROM verses v
                    JOIN versions ver ON v.version_id = ver.id
                    WHERE ver.code = ? AND v.book = ?
                    """,
                    (str(version), str(b)),
                ).fetchone()
                if row and row[0] is not None:
                    return int(row[0])
        return 0

    except Exception as e:
        log.error("db_chapter_count: %s", e)
        return 0


def db_get_chapter(version: str, book: str, chapter: int) -> List[Dict]:
    """Every verse in a chapter, in verse order — powers the Browser
    panel's chapter preview."""
    try:
        with _conn() as c:
            for b in _normalise_book_for_db(book):
                rows = c.execute(
                    """
                    SELECT v.book, v.chapter, v.verse, v.text, ver.code AS version
                    FROM verses v
                    JOIN versions ver ON v.version_id = ver.id
                    WHERE ver.code = ? AND v.book = ? AND v.chapter = ?
                    ORDER BY v.verse
                    """,
                    (str(version), str(b), int(chapter)),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
        return []

    except Exception as e:
        log.error("db_get_chapter: %s", e)
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

        # The verse actually live on the projector right now — distinct
        # from session.current_book/chapter/verse, which _display() updates
        # for every engine decision regardless of whether it ever reaches
        # the screen. Navigation, verse-jump, and version-switch's
        # "redisplay current verse" all prefer this but fall back to
        # session position when nothing is live yet — Go Live defaults OFF
        # and every verse lands in Preview first, so this is routinely
        # still None mid-sermon; without the fallback those commands would
        # silently no-op (a real, previously-shipped bug). Set via
        # confirm_live(), called by main_ui.py's _set_live.
        self._live_position: Optional[Tuple[str, int, int]] = None

        self.semantic = SemanticEngine()
        self.semantic.load_version(version)

        # Context-aware display decision layer — sits after semantic.py's
        # candidates, before a verse is judged safe to show. See
        # app/retrieval/context_decision.py and this file's own module
        # docstring for why this is not the previously-reverted rolling-
        # context design. embed_fn/similarity_fn reuse the SAME already-
        # loaded SentenceTransformer semantic.py holds (self.semantic.model)
        # for context_decision.py's contextual-alignment signal — no
        # second model loaded, nothing reimplemented; normalize_embeddings
        # =True (same convention semantic.py's own event_map similarity
        # check uses) makes a plain dot product the cosine similarity.
        self.context_manager = ContextManager(
            embed_fn=lambda t: self.semantic.model.encode(
                [t], convert_to_numpy=True, normalize_embeddings=True,
            )[0],
            similarity_fn=lambda a, b: float(np.dot(a, b)),
        )

        self._verse_cb: Optional[Callable[[Optional[Dict]], None]] = None
        self._status_cb: Optional[Callable[[str, str], None]] = None
        self._no_match_cb: Optional[Callable[[], None]] = None

        self._last_match_time = 0.0
        self._last_key: Optional[str] = None
        # Reentrant: process() (background ASR thread) holds this while
        # running _run_fast, which itself calls _navigate/_switch_version
        # internally — a plain Lock would deadlock on that same-thread
        # re-entry. Needs to be reentrant rather than just "some lock" so
        # the operator UI (main thread) can call _navigate/_switch_version/
        # manual_display/confirm_live directly — as it does, for the Nav
        # buttons, Switch Version, detection ▶, and Live confirm — without
        # racing a concurrent process() call on the ASR thread over the
        # same session/semantic-index state.
        self._lock = threading.RLock()

        # De-dupes nav commands detected from voice/typed text — see
        # VOICE_NAV_COOLDOWN above.
        self._last_voice_nav_cmd: Optional[str] = None
        self._last_voice_nav_time: float = 0.0

        # Ambiguous PREV phrase awaiting a confirming repeat — see
        # AMBIGUOUS_NAV_CONFIRM_WINDOW above.
        self._pending_ambiguous_nav: Optional[str] = None
        self._pending_ambiguous_nav_time: float = 0.0

        # A book named with no resolvable verse in the same utterance
        # ("in the book of Ezekiel") — see ANNOUNCED_BOOK_TIMEOUT and
        # _current_context_book. Distinct from session.current_book,
        # which only reflects a book a verse was actually DISPLAYED
        # from; this is a weaker, purely advisory signal that never
        # implies a position or survives past its own timeout.
        self._announced_book: Optional[str] = None
        self._announced_at: float = 0.0

        # Whole-utterance-granularity buffer — only used by
        # _extract_split_reference, which needs a complete previous
        # utterance's text, not a word-count window.
        self._sentence_history: deque = deque(maxlen=ROLLING_CONTEXT_SENTENCES)

        # ── Semantic shadow check ────────────────────────────────────
        # A direct-reference match resolves and displays immediately
        # (_run_fast), so it never gets a semantic opinion the way
        # _run_semantic candidates do. This runs one in the background,
        # purely to log agreement/disagreement for review (see
        # _log_semantic_shadow) — never touches the display or session.
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

    def _semantic_enabled(self) -> bool:
        """Gap found by testing: semantic.py has no language awareness at
        all — self.semantic.search()/search_within_chapter()/
        search_within_book() run the same English-only sentence-
        transformer model (all-MiniLM-L6-v2) regardless of which Bible
        version is active. A TWI.faiss index exists and switch_version()
        happily loads it, so a semantic call against Twi text doesn't
        crash — it just silently returns confident-looking nonsense, an
        English embedding model scoring Twi verse text it was never
        trained on. This was found and fixed once before (a Twi query
        for "God loves the world" didn't surface John 3:16 in its top 5
        results) but the fix didn't survive an unrelated revert (commit
        1c8bce2) that took it out along with a bad merge. Direct-
        reference matching (book/chapter/verse the operator actually
        said) is completely unaffected — it never touches the embedding
        model — so TWI still works for anything with a real reference;
        only the "no reference, guess from paraphrase" path is gated
        here. Re-embedding the corpus with a genuinely multilingual model
        is the real fix if Twi semantic search is ever wanted; until
        then, disabled is more honest than wrong."""
        return str(self.session.active_version).upper() != "TWI"

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
        needs the higher bar while a deliberate walk-through is active.

        Falls back to a recently-announced bare book mention (see
        _announced_book) when nothing is actively tracked — a preacher
        saying "in the book of Ezekiel" as its own utterance, with no
        paraphrase to resolve yet, previously scoped nothing at all: the
        mention cleared no threshold anywhere, so the very next
        utterance's real paraphrase got an unscoped, whole-Bible search
        instead of one narrowed to the book that was just named. An
        active verse_tracking position still wins when both are present
        — it's live, confirmed evidence, stronger than an announcement
        alone."""
        tracking = getattr(self.session, "state", "") == "verse_tracking"
        if tracking and self._has_position():
            return str(self.session.current_book)

        if (
            self._announced_book
            and time.time() - self._announced_at <= ANNOUNCED_BOOK_TIMEOUT
        ):
            return self._announced_book

        return None

    def _short_trailing_fragment(self, text: str) -> Optional[str]:
        """Combines this utterance with the immediately preceding one when
        this one looks like the tail half of a command/reference that
        ASR's signal-driven endpointing split across a mid-phrase pause
        (e.g. "go to previous" ... "verse", or "Leviticus" ... "27") —
        shared by the navigation, verse-jump, and direct-reference
        split-fallbacks below, all of which need a complete multi-word
        phrase/pattern that neither half alone contains. Deliberately
        narrow: only tried when THIS utterance is a short trailing
        fragment (<=4 words, cheap to rule out on the common case) and
        real sentence history exists — combined with each caller's own
        detector needing a specific match in the joined text, this is
        narrow enough not to revive an unrelated command/reference from
        an earlier utterance on a random short one. Returns the combined
        text, or None if this utterance doesn't look like a split
        fragment at all."""
        if len(text.split()) > 4:
            return None

        if len(self._sentence_history) < 2:
            return None

        return f"{self._sentence_history[-2]} {text}"

    def _extract_split_reference(
        self, text: str
    ) -> Tuple[Optional[Tuple[str, int, int]], Optional[str]]:
        """Fallback for a spoken reference split across two utterances by
        ASR endpointing (e.g. a pause between "Leviticus" and "27") —
        neither utterance alone contains a complete book+chapter pattern,
        so the single-utterance extract_reference() call above misses
        both, and the utterance falls through to the slow semantic path
        with no real chance of matching (it isn't a paraphrase of any
        verse). Returns (ref, combined_source_text) or (None, None).
        """
        combined = self._short_trailing_fragment(text)
        if not combined:
            return None, None

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
    # Navigation/verse-jump/version-switch are deterministic commands, not
    # probabilistic guesses, but still need to show in the AI Detections
    # audit trail — tagged confidence=1.0 with a distinct match_type so
    # the UI styles them as commands and _on_verse's Auto/Manual gate
    # (which only holds back "semantic") never touches them.

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
            self.context_manager.clear_current_verse()

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
            # Still a real, current re-detection of the verse actively on
            # screen — e.g. a direct match followed by a later utterance
            # semantically re-confirming the same verse. Refreshing the
            # timeout clock here (even though the display/callback side
            # effects below are correctly skipped) keeps verse_tracking
            # alive while the preacher keeps referencing it; previously
            # this returned before ever touching _last_match_time, so the
            # tracking window kept counting from the ORIGINAL match and
            # could silently expire mid-conversation about the same verse.
            self._last_match_time = time.time()
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

        # Keeps context_manager's hysteresis (point 9) correct regardless
        # of WHICH path changed the display — direct reference,
        # navigation, verse-jump, manual override, version-switch
        # redisplay, or the semantic/context path itself. Without this, a
        # verse put on screen by, say, a spoken direct reference would
        # leave the context manager still comparing new semantic
        # candidates against a stale or zeroed "current verse evidence".
        self.context_manager.sync_current_verse(
            (
                str(verse.get("book", "")),
                int(verse.get("chapter", 0)),
                int(verse.get("verse", 0)),
            ),
            confidence=float(verse.get("confidence", 0.97) or 0.97),
        )

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
        /chapter/verse. Called from the UI (main) thread — takes the same
        lock process() (background ASR thread) holds, since both read/
        write this and related session state (see __init__'s _lock
        comment)."""
        with self._lock:
            if verse:
                self._live_position = (
                    str(verse.get("book", "")),
                    int(verse.get("chapter", 0)),
                    int(verse.get("verse", 0)),
                )
            else:
                self._live_position = None

    def sync_position(self, book: str, chapter: int, verse: int):
        """Public, locked wrapper for updating session position from the
        UI (main) thread — e.g. main_ui.py's _promote_search_result()
        keeping the engine's position in sync with a search result the
        operator sent to Preview. session.update_position() itself writes
        book/chapter/verse as three separate, non-atomic assignments;
        going through this (instead of touching self.session directly,
        as this call site used to) keeps it from interleaving with
        process() on the ASR thread and leaving current_book/chapter/
        verse momentarily mismatched."""
        with self._lock:
            self.session.update_position(str(book), int(chapter), int(verse))

    def position_snapshot(self):
        """Atomic (active_version, book, chapter, verse) snapshot for the
        UI thread to read — book/chapter/verse are None if no position is
        tracked yet. SessionState.update_position()/clear_position()
        write current_book/chapter/verse as separate, non-atomic
        assignments (see version_detector.py), so reading them as three
        separate attribute accesses (as main_ui.py used to) risks a torn
        read if process() lands a position update on the ASR thread
        between them — e.g. the new book paired with the still-old
        chapter, silently looking up the wrong verse. Callers that need
        the position should use this instead of touching self.session
        directly."""
        with self._lock:
            version = str(self.session.active_version)
            if self._has_position():
                book = str(self.session.current_book)
                chapter = int(self.session.current_chapter)
                verse = int(self.session.current_verse)
            else:
                book = chapter = verse = None
            return version, book, chapter, verse

    def search_top_k(self, query: str, k: int = 8) -> List[dict]:
        """Public, locked wrapper around self.semantic.search_top_k() —
        for direct UI use (e.g. main_ui.py's search box), which used to
        call self._engine.semantic.search_top_k() straight through,
        racing a concurrent process() call on the ASR thread that can
        reassign self.semantic.index/verse_store via _switch_version
        (e.g. an operator typing a search just as a spoken version
        switch lands) — this pairs the index and verse_store it reads
        with whichever version was actually active when the call
        started, instead of possibly a torn mix of both."""
        with self._lock:
            return self.semantic.search_top_k(query, k=k)

    # ========================================================
    # NAVIGATION
    # ========================================================

    def _navigate(self, cmd: str):
        # Called from both process() (background ASR thread, already
        # holding self._lock) and directly from the UI's Nav buttons
        # (main thread) — reentrant lock, see __init__'s comment.
        with self._lock:
            cmd = str(cmd).upper().strip()

            # Prefer live position; fall back to session position (see
            # __init__'s _live_position comment) so navigation still
            # works before the first Go Live push, same as
            # _switch_version.
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

            # _set_state("sequential") must run AFTER _display(), not
            # before — _display() unconditionally sets "verse_tracking"
            # on every call, which would otherwise immediately overwrite
            # "sequential" in the same command, making it unreachable by
            # SEMANTIC_CONFIDENCE_HI's state check and the status label.
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

    def _switch_version(self, new_ver: str) -> bool:
        """Applies a version switch (session + semantic index) if new_ver
        differs from the current one and is installed. Returns whether it
        actually switched. Deliberately does NOT redisplay the current
        position — see _redisplay_current_position, called separately by
        _run_fast only when the same utterance carried no reference/
        verse-jump of its own, so a version cue spoken alongside a
        reference ("Hebrews 7 verse 19 in the Message") doesn't flash the
        old verse under the new version before the correct one replaces
        it. Called from both process() (background ASR thread, already
        holding self._lock) and directly from the UI's Switch Version
        control (main thread) — reentrant lock, see __init__'s comment."""
        with self._lock:
            new_ver = str(new_ver).upper().strip()
            current = str(self.session.active_version).upper().strip()

            if new_ver == current:
                return False

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

                return False

            log.info("Version switch: %s → %s", current, new_ver)

            self.session.update_version(new_ver)
            self.semantic.switch_version(new_ver)

            if self._status_cb:
                self._status_cb("version_switch", f"Switched to {new_ver}")

            return True

    def _redisplay_current_position(self, new_ver: str):
        """Re-shows whatever position was already current, under the
        version just switched to — otherwise Preview would keep showing
        the old version after a switch, before anything's gone live yet.
        Prefers live position; falls back to session position (see
        __init__'s _live_position comment). Called from both process()
        (background ASR thread, already holding self._lock) and directly
        from the UI's Switch Version control (main thread) — reentrant
        lock, see __init__'s comment."""
        with self._lock:
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
        version_cue = bool(new_ver)
        version_switched = self._switch_version(new_ver) if new_ver else False
        # Falls through instead of returning — a version cue is often
        # spoken in the same breath as a reference ("Hebrews 7 the verse
        # number 19, let's do it in MSG"), and returning here would
        # apply the switch but silently discard that reference. Steps
        # 2-4 below get a chance to also handle the rest of this same
        # text (against the version just switched to). _switch_version
        # itself does NOT redisplay the current position — that only
        # happens at the bottom of this method, and only if nothing else
        # fired, so a reference found by steps 2-4 is what gets shown,
        # not a flash of the old verse first.

        # ====================================================
        # 2. Navigation
        # ====================================================
        nav = detect_navigation(text)
        nav_source_text = text

        if not nav:
            # Every NAV_PHRASES entry is 2-4 words ("next verse", "go to
            # previous verse", "clear the screen", ...) — a mid-phrase
            # pause splits it across two utterances the same way a spoken
            # book reference can (see _short_trailing_fragment), and
            # neither half alone contains the full phrase detect_navigation
            # needs. Without this, the command is silently lost: "next"
            # alone isn't in NAV_MAP, so it falls through to the slow
            # semantic path with no real chance of matching anything.
            combined = self._short_trailing_fragment(text)
            if combined:
                nav = detect_navigation(combined)
                if nav:
                    nav_source_text = combined
                    log.info(
                        "Navigation '%s' found only after combining with "
                        "the previous utterance (split by a mid-phrase "
                        "pause): %r",
                        nav, combined[:100],
                    )

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

            if nav_requires_confirm(nav_source_text):
                if (
                    self._pending_ambiguous_nav == nav
                    and now - self._pending_ambiguous_nav_time
                    < AMBIGUOUS_NAV_CONFIRM_WINDOW
                ):
                    log.info(
                        "Ambiguous nav '%s' confirmed by a repeat — firing",
                        nav,
                    )
                    self._pending_ambiguous_nav = None
                else:
                    log.info(
                        "Ambiguous nav '%s' detected (%r) — waiting up to "
                        "%.0fs for a confirming repeat before acting",
                        nav, nav_source_text[:60], AMBIGUOUS_NAV_CONFIRM_WINDOW,
                    )
                    self._pending_ambiguous_nav = nav
                    self._pending_ambiguous_nav_time = now
                    return True

            self._last_voice_nav_cmd = nav
            self._last_voice_nav_time = now
            self._navigate(nav)
            return True

        # ====================================================
        # 3. Direct reference ALWAYS wins — must come before verse jump,
        # or e.g. "Matthew 5 verse 10" while sitting on Matthew 4:21 would
        # detect only "verse 10" and wrongly jump to Matthew 4:10.
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
        elif detect_verse_jump(text) is not None:
            # This utterance is already a complete, self-sufficient
            # verse-jump command ("verse 10") — never hand it to the
            # split-reference fallback below. Bug found by testing:
            # _extract_split_reference combines a short trailing
            # fragment with whichever utterance came right before it in
            # _sentence_history, regardless of whether that fragment
            # actually needed combining. "verse 10" said right after
            # "Genesis 1:1" (a completely ordinary sequence — "turn to
            # Genesis chapter 1 verse 1... now verse 10") was combining
            # into "Genesis 1:1 verse 10", which extract_reference()
            # parses as the FIRST complete pattern it finds (Genesis
            # 1:1) — silently discarding "verse 10" and re-displaying
            # the old verse instead of jumping to 10. Step 4 below
            # already handles a bare "verse N" correctly against
            # whatever position is currently tracked; this fragment was
            # never incomplete in the first place; it never needed
            # cross-utterance recombination. A bare "27" (the genuine
            # split-reference case from Leviticus 27) has no "verse"
            # keyword, so detect_verse_jump leaves it None here and this
            # branch doesn't affect it.
            book = chapter = verse = None
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
                scoped = None
                if self._semantic_enabled():
                    stripped = strip_reference_words(text, book)
                    scoped = self.semantic.search_within_chapter(
                        stripped, book, chapter)

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

        # No chapter/verse number anywhere in the utterance, but a book
        # may still have been NAMED ("in the book of Romans, he talks
        # about how we should live") — extract_reference_verbose and the
        # split-reference fallback above both need a number to anchor a
        # reference on, so this case falls through both of them with
        # book left None. Without this, a named book carries no more
        # weight than if it had never been said at all — the utterance
        # would get an unscoped, whole-Bible semantic search exactly like
        # any other sentence, discarding a real, spoken signal about
        # where to look. search_within_book only returns a candidate that
        # already clears the normal display threshold scoped to that one
        # book, so a bare book mention with no real paraphrase content
        # ("let's go to the book of Romans") correctly finds nothing here
        # and falls through to step 5's unscoped search, same as before.
        if book is None:
            named_book = extract_book_only(
                text, allowed_languages=self._allowed_languages()
            )
            if named_book and self._semantic_enabled():
                stripped = strip_reference_words(text, named_book)
                scoped = self.semantic.search_within_book(stripped, named_book)

                if scoped:
                    final_score = float(scoped.get("final_score", 0))
                    log.info(
                        "Book-scoped semantic match within %s: %s %s:%s "
                        "score=%.3f",
                        named_book,
                        scoped.get("book"),
                        scoped.get("chapter"),
                        scoped.get("verse"),
                        final_score,
                    )

                    r = db_get_verse(
                        str(self.session.active_version),
                        str(scoped.get("book", "")),
                        int(scoped.get("chapter", 0)),
                        int(scoped.get("verse", 0)),
                    )

                    if r:
                        r["match_type"] = "semantic"
                        r["confidence"] = final_score
                        r["matched_at"] = time.time()
                        r["source_text"] = text
                        self._display(r)
                        self._log_semantic_shadow(text, r)
                        return True

                # No resolvable verse in THIS utterance, but the book
                # itself was a real, deliberate mention ("in the book of
                # Ezekiel", said before any paraphrase follows) — remember
                # it so the next utterance's semantic search is scoped to
                # this book instead of an unscoped, whole-Bible search.
                # See _current_context_book/ANNOUNCED_BOOK_TIMEOUT.
                self._announced_book = named_book
                self._announced_at = time.time()
                log.info(
                    "Book named with no resolvable verse yet — scoping "
                    "semantic search to %s for the next %.0fs: %r",
                    named_book, ANNOUNCED_BOOK_TIMEOUT, text[:100],
                )

        # ====================================================
        # 4. Verse jump — only if no direct reference was found. E.g. from
        # John 3:16, "verse 20" resolves to John 3:20.
        # ====================================================
        jump_book = jump_chapter = None
        if self._live_position:
            jump_book, jump_chapter, _ = self._live_position
        elif self._has_position():
            jump_book = str(self.session.current_book)
            jump_chapter = int(self.session.current_chapter)

        if jump_book:
            target = detect_verse_jump(text)
            jump_source_text = text

            if target is None:
                # detect_verse_jump's regex requires "verse" and the
                # number in the SAME string ("verse twenty") — a
                # mid-phrase pause splits these across two utterances the
                # same way a book reference can (see
                # _short_trailing_fragment), and "twenty" alone matches
                # nothing on its own.
                combined = self._short_trailing_fragment(text)
                if combined:
                    target = detect_verse_jump(combined)
                    if target is not None:
                        jump_source_text = combined
                        log.info(
                            "Verse jump found only after combining with "
                            "the previous utterance (split by a "
                            "mid-phrase pause): %r",
                            combined[:100],
                        )

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

                    self._tag_command(r, "verse_jump", jump_source_text)
                    self._display(r)
                    self._set_state("sequential")
                    return True

        # Nothing else in this utterance produced its own display —
        # if a version switch happened, this was a pure version-switch
        # utterance, so show the current position under the new version
        # now (deferred from _switch_version — see its docstring).
        if version_switched:
            self._redisplay_current_position(str(self.session.active_version))

        return version_cue

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
        if not self._semantic_enabled():
            # See _semantic_enabled's docstring — the English-only model
            # has nothing meaningful to say about Twi text, so a
            # "disagreement" here would just be noise, not a real signal.
            return
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

            # Log-only from here — the log.warning above already captures
            # book/chapter/verse/score/source text for review. This used
            # to also push the disagreement to the AI Detections panel via
            # _notify_secondary_detection, on the theory that it might be
            # a genuine second citation riding in the same utterance (a
            # preacher citing a verse by number, then paraphrasing another
            # in the same breath). In practice this surfaced a confusing
            # second card for sentences the direct match already resolved
            # correctly, often on nothing more than a stray shared word
            # (e.g. a split-utterance fragment's trailing "spirit" from
            # the PREVIOUS sentence coincidentally scoring against an
            # unrelated verse) — operator-misleading noise, not signal.
            # The log trail below is kept for future tuning; only the
            # UI-facing side effect was removed.

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

    def _collect_semantic_candidates(
        self, text: str, context_book: Optional[str], k: int = 3,
    ) -> List[dict]:
        """Top-k raw candidates (not gated by DISPLAY_THRESHOLD or any
        confidence bar — context_manager.process_utterance() is what
        judges these now) merged from the same-book-scoped pass (if a
        book is currently locked) and the unscoped whole-corpus pass,
        the same two passes semantic.py's own search() runs internally.
        Reusing search_top_k() (a public, already-existing method) —
        this file does not reimplement or duplicate any scoring logic
        from semantic.py, only asks it for more than the single top
        result search() itself would return."""
        merged: Dict[Tuple[str, int, int], dict] = {}

        if context_book:
            for c in self.semantic.search_top_k(text, k=k, threshold=0.0, book=context_book):
                merged[(str(c["book"]), int(c["chapter"]), int(c["verse"]))] = c

        for c in self.semantic.search_top_k(text, k=k, threshold=0.0):
            key = (str(c["book"]), int(c["chapter"]), int(c["verse"]))
            if key not in merged or c["final_score"] > merged[key]["final_score"]:
                merged[key] = c

        ranked = sorted(merged.values(), key=lambda c: c["final_score"], reverse=True)
        return ranked[:k]

    def _act_on_context_decision(self, decision: DecisionResult, source_text: str, t0: float) -> None:
        """Turns context_decision.py's Decision into the same UI-facing
        side effects _run_semantic used to produce inline — _display()
        for DISPLAY, _notify_secondary_detection() (AI Detections panel
        only, no position/state change) for anything still worth showing
        the operator, and the no-match callback when there is truly
        nothing."""
        if decision.decision == Decision.DISPLAY:
            book, chapter, verse_no = decision.verse
            r = db_get_verse(str(self.session.active_version), book, chapter, verse_no)
            if not r:
                log.warning(
                    "Context decision DISPLAY for a verse not in the DB: %s %s:%s",
                    book, chapter, verse_no,
                )
                return
            r["match_type"] = "semantic"
            r["confidence"] = decision.evidence
            r["matched_at"] = time.time()
            r["source_text"] = source_text
            log.info(
                "Context decision DISPLAY: %s %s:%s evidence=%.3f (%.0fms) — %s",
                book, chapter, verse_no, decision.evidence,
                (time.time() - t0) * 1000.0, decision.reason,
            )
            self._display(r)
            return

        if decision.decision == Decision.KEEP_CURRENT:
            log.debug("Context decision KEEP_CURRENT — %s", decision.reason)
            return

        # WAIT_FOR_CONTEXT / HUMAN_CONFIRMATION_REQUIRED / NO_CONFIDENT_MATCH
        # with a leading candidate still worth showing the operator (log-
        # only, same contract _notify_secondary_detection has always had:
        # never touches _last_key/session position/state).
        if decision.verse:
            book, chapter, verse_no = decision.verse
            row = db_get_verse(str(self.session.active_version), book, chapter, verse_no)
            if row:
                row["match_type"] = "semantic"
                row["confidence"] = decision.evidence
                row["matched_at"] = time.time()
                row["source_text"] = source_text
                row["_context_state"] = decision.state.name if decision.state else ""
                row["_below_confidence"] = decision.decision != Decision.HUMAN_CONFIRMATION_REQUIRED
                row["_human_confirmation"] = decision.decision == Decision.HUMAN_CONFIRMATION_REQUIRED
                log.info(
                    "Context decision %s: %s %s:%s evidence=%.3f — %s",
                    decision.decision.name, book, chapter, verse_no,
                    decision.evidence, decision.reason,
                )
                self._notify_secondary_detection(row)
                return

        log.debug("Context decision %s — %s", decision.decision.name, decision.reason)
        if getattr(self.session, "state", "") == "context_matching":
            if self._no_match_cb:
                self._no_match_cb()

    def _run_semantic(self, text: str):
        """Step 5 — context-aware semantic search.

        text: the single utterance that triggered this resolution. This is
        both what gets SEARCHED (semantic.search_top_k() runs against
        exactly this string, nothing blended in from any other utterance)
        and what gets shown to the operator as "heard" when a candidate is
        surfaced from it. What DOES span multiple utterances is the
        evidence-accumulation/decision layer in context_decision.py — see
        the module docstring's note on why that is not the previously-
        reverted rolling-word-window design.
        """
        t0 = time.time()
        self._check_timeout()

        if not self._semantic_enabled():
            # See _semantic_enabled's docstring. Same outcome as "found
            # nothing" since that's exactly what this is — not a real
            # search, so nothing to report as a candidate, only as a
            # plain miss.
            log.debug(
                "Semantic search skipped — %s has no working embedding "
                "model (English-only): %r",
                self.session.active_version, text[:80],
            )
            if getattr(self.session, "state", "") == "context_matching":
                if self._no_match_cb:
                    self._no_match_cb()
            return

        context_book = self._current_context_book()
        log.info("Semantic (%d words): %r", len(text.split()), text)

        # semantic.py's own search() still runs first, unchanged — it
        # already does phrase-map/event-map lookup then the context-
        # scoped/unscoped statistical passes internally, and remains the
        # one place that logic lives (not duplicated here). Its result is
        # used two ways below: a curated phrase_map/event_map hit is
        # hand-verified evidence, treated the same tier as a spoken
        # direct reference (bypasses accumulation via
        # explicit_reference=); anything else becomes one of the ranked
        # candidates handed to the context manager instead of being
        # accepted or rejected on the spot.
        top1 = self.semantic.search(text, context_book=context_book)
        source_text = text

        if not top1:
            # This utterance alone may be an incomplete fragment of one
            # continuous thought ASR's signal-driven endpointing split
            # across a mid-phrase pause — see this method's previous
            # version/PROGRESS.md for the Hebrews 4:15 case this exists
            # for. Only the ONE immediately preceding utterance is ever
            # considered, only when THIS utterance alone matched nothing.
            combined = self._short_trailing_fragment(text)
            if combined:
                combined_top1 = self.semantic.search(combined, context_book=context_book)
                if combined_top1:
                    top1 = combined_top1
                    source_text = combined
                    log.info(
                        "Semantic match found only after combining with "
                        "the previous utterance (one thought split by a "
                        "mid-phrase pause): %r",
                        combined[:100],
                    )

        explicit_reference = None
        if top1 and top1.get("method") in ("phrase_map", "event_map"):
            explicit_reference = ScriptureCandidate.from_result(top1, source_text=source_text)

        candidates_raw = self._collect_semantic_candidates(source_text, context_book)
        candidates = [ScriptureCandidate.from_result(c, source_text=source_text) for c in candidates_raw]

        decision = self.context_manager.process_utterance(
            source_text, candidates, explicit_reference=explicit_reference,
        )

        self._act_on_context_decision(decision, source_text, t0)

    # ========================================================
    # MANUAL OVERRIDE
    # ========================================================

    def manual_display(self, book: str, chapter: int, verse: int):
        # Called directly from the UI (main thread) — e.g. detection ▶ —
        # while process() may be running concurrently on the ASR thread;
        # takes the same lock (reentrant, see __init__'s comment) so the
        # two never race over session/active_version state.
        with self._lock:
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
