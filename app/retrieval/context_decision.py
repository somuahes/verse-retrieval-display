"""
app/retrieval/context_decision.py
===================================

CONTEXT-AWARE DISPLAY DECISION LAYER

Sits strictly AFTER the existing semantic engine (semantic.py) and BEFORE
a verse is displayed. Does not replace, wrap, or re-implement retrieval —
it consumes whatever candidates semantic.py/hybrid.py already produced
and decides whether that evidence is strong enough, sustained enough, and
unambiguous enough to project, versus waiting for more context or holding
the currently displayed verse.

    Existing engine  ──▶  candidates (book, chapter, verse, scores)
                                │
                                ▼
                        ContextManager.process_utterance()
                                │
                    (this file, nothing above)
                                │
                                ▼
                    DecisionResult(DISPLAY | KEEP_CURRENT |
                                    WAIT_FOR_CONTEXT | NO_CONFIDENT_MATCH |
                                    HUMAN_CONFIRMATION_REQUIRED)

Central distinction this file exists to enforce: a semantic match is a
CANDIDATE, not a display decision. "Which verse scored highest this
utterance" and "what is the preacher most likely referring to, given
this utterance, recent context, prior evidence for this candidate, and
whatever is already on screen" are different questions — this module
answers the second one using the first as its raw material.

No new ML model, no re-embedding requirement, no change to any existing
threshold in semantic.py/hybrid.py. Contextual alignment (see
_contextual_alignment) uses the same clean_text()/tokens() utilities
semantic.py already exports; an optional embed_fn hook lets a caller
plug in the existing SentenceTransformer for a richer version of that
one signal without this module importing torch itself, which keeps it
unit-testable with plain Python.

INTEGRATION POINT (not wired in by this file): hybrid.py's
_run_semantic() currently takes semantic.py's top candidate and checks
it against a single number (SEMANTIC_CONFIDENCE / _HI) before displaying
or logging it as a secondary detection. A ContextManager instance
belongs at exactly that point — feed it the utterance text plus the
candidate list semantic.py already computed (search_top_k(), not just
search()'s single top-1), and act on the returned DecisionResult instead
of the inline threshold check. See the module docstring's bottom section
for a concrete sketch.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Deque, Dict, List, Optional, Tuple

from app.retrieval.semantic import clean_text, tokens as _tokens

VerseKey = Tuple[str, int, int]


# ════════════════════════════════════════════════════════════
# CONFIG — starting defaults, not empirically tuned against real
# sermon data the way semantic.py's/hybrid.py's constants are (this is
# new code with no A/B history yet). Documented with intent so they can
# be retuned the same way the rest of this project's thresholds have
# been: against real transcripts, one confirmed case at a time.
# ════════════════════════════════════════════════════════════

@dataclass
class DecisionConfig:
    # ── Evidence accumulation weights (must sum to 1.0) ──
    # current utterance's own (specificity-aware) score
    w_current: float = 0.35
    # decayed average of this candidate's score across prior utterances
    w_historical: float = 0.30
    # lexical alignment between the candidate verse and the rolling
    # context window (recent utterances), not just this one utterance
    w_contextual: float = 0.20
    # reward for a rising/sustained trend vs a single erratic spike —
    # this is what makes 0.71→0.76→0.84→0.91 outrank an isolated 0.91
    w_consistency: float = 0.15

    ema_alpha: float = 0.5  # weight on the newest score in the EMA

    # ── Candidate state thresholds (on the combined evidence score) ──
    contextual_support_threshold: float = 0.45
    stable_threshold: float = 0.60
    high_confidence_threshold: float = 0.72

    # ── Specificity gate (point 7: generic religious vocabulary must
    # not create false confidence on its own) ──
    # lexical_score is already IDF-weighted in semantic.py (rare words
    # dominate, "God"/"love"/"good" barely move it) — reused directly as
    # a specificity signal rather than reinventing one.
    low_specificity_cutoff: float = 0.35
    low_specificity_extra_streak: int = 2  # extra corroborating utterances required

    base_min_streak: int = 1

    # ── Ambiguity (point 6: "God is good" style near-ties) ──
    # top-1 vs top-2 raw semantic_score this utterance closer than this
    # = treat the utterance's contribution as ambiguous, not decisive.
    ambiguity_margin: float = 0.05

    # ── Hysteresis (point 9: don't flap off an already-displayed verse) ──
    switch_margin: float = 0.08
    min_streak_to_switch: int = 2

    # ── Evidence lifecycle ──
    evidence_timeout_s: float = 60.0   # stale candidate expires outright
    absence_decay: float = 0.85        # EMA multiplier per utterance a
                                        # tracked candidate doesn't reappear
    absence_prune_after: int = 3       # consecutive absences -> drop it

    # ── Rolling context window ──
    context_window_utterances: int = 6
    context_window_seconds: float = 90.0

    # ── Operator-review surfacing (point 8's WAIT branch, made visible
    # instead of silently stalling forever) ──
    human_review_streak: int = 6
    human_review_min_factor: float = 0.85  # * high_confidence_threshold

    def __post_init__(self):
        total = self.w_current + self.w_historical + self.w_contextual + self.w_consistency
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Evidence weights must sum to 1.0, got {total}")


# ════════════════════════════════════════════════════════════
# STATES / DECISIONS
# ════════════════════════════════════════════════════════════

class CandidateState(Enum):
    CANDIDATE = auto()             # just appeared
    CONTEXTUAL_SUPPORT = auto()    # context aligns, not yet sustained
    STABLE_CANDIDATE = auto()      # sustained across enough utterances
    HIGH_CONFIDENCE = auto()       # evidence clears the display bar
    CONTRADICTED = auto()          # a rival candidate has since dominated
    REJECTED = auto()              # expired or contradicted and dropped
    INSUFFICIENT_CONTEXT = auto()  # ambiguous this utterance; holding


class Decision(Enum):
    DISPLAY = auto()
    KEEP_CURRENT = auto()
    WAIT_FOR_CONTEXT = auto()
    NO_CONFIDENT_MATCH = auto()
    HUMAN_CONFIRMATION_REQUIRED = auto()


# ════════════════════════════════════════════════════════════
# INPUT: what the existing semantic engine already produces
# ════════════════════════════════════════════════════════════

@dataclass
class ScriptureCandidate:
    """One candidate for ONE utterance, as already produced by
    semantic.py/hybrid.py. This module adds nothing to how a candidate
    is found — only to what happens after."""
    book: str
    chapter: int
    verse: int
    text: str = ""
    semantic_score: float = 0.0    # semantic.py's "similarity"
    lexical_score: float = 0.0     # semantic.py's "lexical_score" (IDF-weighted)
    final_score: float = 0.0       # semantic.py's "final_score" (what search() gates on)
    method: str = ""                # phrase_map / event_map / semantic_lexical / direct
    source_text: str = ""

    @property
    def key(self) -> VerseKey:
        return (self.book, int(self.chapter), int(self.verse))

    @classmethod
    def from_result(cls, result: dict, source_text: str = "") -> "ScriptureCandidate":
        """Adapter for semantic.py's search()/search_top_k() dicts and
        hybrid.py's enriched verse dicts — both already have this shape
        under slightly different key names."""
        return cls(
            book=str(result.get("book", "")),
            chapter=int(result.get("chapter", 0)),
            verse=int(result.get("verse", 0)),
            text=str(result.get("text", "")),
            semantic_score=float(result.get("similarity", result.get("semantic_score", 0.0)) or 0.0),
            lexical_score=float(result.get("lexical_score", 0.0) or 0.0),
            final_score=float(result.get("final_score", result.get("confidence", 0.0)) or 0.0),
            method=str(result.get("method", result.get("match_type", ""))),
            source_text=source_text,
        )


# ════════════════════════════════════════════════════════════
# TRACKED EVIDENCE — one per candidate verse, persists across utterances
# ════════════════════════════════════════════════════════════

@dataclass
class CandidateEvidence:
    key: VerseKey
    text: str = ""
    score_history: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=20))
    ema: float = 0.0
    streak: int = 0            # consecutive utterances seen, non-decreasing
    absences: int = 0          # consecutive utterances NOT seen since last hit
    state: CandidateState = CandidateState.CANDIDATE
    first_seen: float = 0.0
    last_seen: float = 0.0
    last_evidence: float = 0.0  # combined E(t) as of the last update


# ════════════════════════════════════════════════════════════
# OUTPUT
# ════════════════════════════════════════════════════════════

@dataclass
class DecisionResult:
    decision: Decision
    verse: Optional[VerseKey]
    evidence: float
    state: Optional[CandidateState]
    reason: str
    ambiguous_with: List[VerseKey] = field(default_factory=list)


# ════════════════════════════════════════════════════════════
# STRUCTURED CONTEXT (point 11 — not a text buffer)
# ════════════════════════════════════════════════════════════

@dataclass
class ContextState:
    recent_utterances: Deque[str] = field(default_factory=lambda: deque(maxlen=6))
    recent_timestamps: Deque[float] = field(default_factory=lambda: deque(maxlen=6))
    candidate_evidence: Dict[VerseKey, CandidateEvidence] = field(default_factory=dict)
    current_verse: Optional[VerseKey] = None
    current_verse_evidence: float = 0.0
    confidence_history: Deque[float] = field(default_factory=lambda: deque(maxlen=20))

    def as_dict(self) -> dict:
        """Serialisable snapshot — matches the shape requested in point
        11, for logging/debugging/the AI Detections panel."""
        return {
            "recent_utterances": list(self.recent_utterances),
            "scripture_candidates": [
                {"verse": f"{k[0]} {k[1]}:{k[2]}", "state": ev.state.name,
                 "evidence": round(ev.last_evidence, 3), "streak": ev.streak}
                for k, ev in self.candidate_evidence.items()
            ],
            "current_verse": (
                f"{self.current_verse[0]} {self.current_verse[1]}:{self.current_verse[2]}"
                if self.current_verse else None
            ),
            "confidence_history": list(self.confidence_history),
        }


# ════════════════════════════════════════════════════════════
# PURE HELPER FUNCTIONS (each independently testable)
# ════════════════════════════════════════════════════════════

def _specificity_factor(ev_or_cand, cfg: DecisionConfig) -> float:
    """0..1 — how distinctive the wording behind this candidate is.
    Reuses semantic.py's own IDF-weighted lexical_score rather than
    inventing a second notion of 'specificity'."""
    lex = getattr(ev_or_cand, "lexical_score", None)
    if lex is None:
        return 1.0
    return max(0.0, min(1.0, lex / cfg.low_specificity_cutoff)) if cfg.low_specificity_cutoff else 1.0


def _required_streak(candidate: ScriptureCandidate, cfg: DecisionConfig) -> int:
    """Point 7: generic wording needs MORE corroborating context, not a
    flat score penalty (a flat penalty would also suppress genuine
    low-lexical-overlap paraphrases, which this project's own eval set
    has confirmed real cases of — see semantic.py's DISPLAY_THRESHOLD
    history). Ambiguity is handled separately in _is_ambiguous."""
    if candidate.lexical_score < cfg.low_specificity_cutoff:
        return cfg.base_min_streak + cfg.low_specificity_extra_streak
    return cfg.base_min_streak


def _update_ema(prev: float, new_score: float, alpha: float) -> float:
    return alpha * new_score + (1 - alpha) * prev


def _consistency_bonus(history: Deque[float], streak_cap: int = 4) -> float:
    """Rewards a rising or sustained trend, not just a high latest value
    — the concrete distinction point 5 asks for (0.71→0.76→0.84→0.91
    should outrank an isolated 0.91). Counts the length of the most
    recent non-decreasing run (small negative noise tolerated)."""
    hist = list(history)
    if len(hist) < 2:
        return 0.0
    run = 0
    for i in range(len(hist) - 1, 0, -1):
        if hist[i] - hist[i - 1] >= -0.02:
            run += 1
        else:
            break
    return min(1.0, run / streak_cap)


def _contextual_alignment(
    candidate_text: str,
    recent_utterances: Deque[str],
    embed_fn: Optional[Callable[[str], object]] = None,
    similarity_fn: Optional[Callable[[object, object], float]] = None,
) -> float:
    """Lexical-overlap alignment between the candidate verse's own text
    and the ROLLING CONTEXT (all recent utterances, not just the current
    one) — cheap, dependency-free, deterministic, and reuses semantic.py's
    existing clean_text()/tokens() rather than a new text pipeline.

    Optional embed_fn/similarity_fn let a caller plug in the existing
    SentenceTransformer (semantic.engine.model.encode) for a richer
    embedding-based version of this same signal — purely additive, never
    required, so this module has no hard ML dependency of its own."""
    context_text = " ".join(recent_utterances)
    if not context_text.strip() or not candidate_text.strip():
        return 0.0

    if embed_fn is not None and similarity_fn is not None:
        try:
            return max(0.0, min(1.0, similarity_fn(embed_fn(candidate_text), embed_fn(context_text))))
        except Exception:
            pass  # fall through to lexical overlap — never let this hard-fail the decision

    cand_tokens = set(_tokens(candidate_text))
    ctx_tokens = set(_tokens(context_text))
    if not cand_tokens or not ctx_tokens:
        return 0.0
    overlap = cand_tokens & ctx_tokens
    return len(overlap) / len(cand_tokens)


def _is_ambiguous(candidates: List[ScriptureCandidate], cfg: DecisionConfig) -> List[VerseKey]:
    """Point 6: several close, similarly-generic candidates this
    utterance ('God is good' -> Psalm 34:8 / 100:5 / Nahum 1:7, all
    within hundredths of each other) is itself evidence the utterance
    ALONE doesn't distinguish between them yet — returns every key
    within ambiguity_margin of the top score, or [] if there's a clear
    leader."""
    if len(candidates) < 2:
        return []
    ranked = sorted(candidates, key=lambda c: c.semantic_score, reverse=True)
    top = ranked[0].semantic_score
    close = [c.key for c in ranked if top - c.semantic_score <= cfg.ambiguity_margin]
    return close if len(close) > 1 else []


# ════════════════════════════════════════════════════════════
# THE DECISION LAYER
# ════════════════════════════════════════════════════════════

class ContextManager:
    """Owns one sermon session's rolling ContextState and turns each
    utterance's raw candidates into a Decision. Stateful, single-
    threaded-use (mirrors HybridEngine's own session-per-instance
    model) — a caller integrating this into hybrid.py would own exactly
    one instance for the engine's lifetime, the same way SessionState
    already is."""

    def __init__(
        self,
        config: Optional[DecisionConfig] = None,
        embed_fn: Optional[Callable[[str], object]] = None,
        similarity_fn: Optional[Callable[[object, object], float]] = None,
    ):
        """embed_fn/similarity_fn (both optional, both-or-neither): lets a
        caller plug in the EXISTING semantic engine's own embedding model
        (e.g. hybrid.py passing self.semantic.model.encode) for
        contextual_alignment (point 4/11), instead of this module's
        dependency-free lexical-overlap fallback. Reuses the already-
        loaded model; does not load a second one. Purely additive — this
        module works correctly, just with a cruder contextual signal,
        if neither is given (e.g. unit tests, which pass neither)."""
        self.config = config or DecisionConfig()
        self.context = ContextState(
            recent_utterances=deque(maxlen=self.config.context_window_utterances),
            recent_timestamps=deque(maxlen=self.config.context_window_utterances),
        )
        self._embed_fn = embed_fn
        self._similarity_fn = similarity_fn

    # ── External sync ────────────────────────────────────────────
    # A real deployment can change what's on screen through paths this
    # module never sees directly — a spoken direct reference ("John
    # chapter 3 verse 16" resolved before semantic search even runs),
    # next/prev/last navigation, a verse-jump, an operator's manual
    # override, or a version switch's same-position redisplay. The
    # hysteresis math in _decide() (point 9) compares a new candidate
    # against "the evidence behind the currently displayed verse" — if
    # that verse changed through one of those paths without this object
    # being told, the comparison would be against stale, wrong state.
    # A caller wiring this module into a real engine should call
    # sync_current_verse()/clear_current_verse() from the SAME choke
    # point every display path already funnels through (e.g. hybrid.py's
    # _display()), not just from inside the semantic-candidate path.

    def sync_current_verse(self, verse: VerseKey, confidence: float = 0.97) -> None:
        """Record that `verse` is now on screen via a path outside this
        module's own accumulation (direct reference, navigation, manual
        override, ...). Seeds/updates that verse's own evidence entry so
        a later semantic candidate is compared against a real, current
        number rather than 0.0 (which would make hysteresis a no-op —
        any positive evidence would "clear" a phantom bar)."""
        self.context.current_verse = verse
        self.context.current_verse_evidence = confidence
        ev = self.context.candidate_evidence.get(verse)
        if ev is None:
            ev = CandidateEvidence(key=verse, first_seen=time.time())
            self.context.candidate_evidence[verse] = ev
        ev.last_evidence = max(ev.last_evidence, confidence)
        ev.last_seen = time.time()
        ev.state = CandidateState.HIGH_CONFIDENCE

    def clear_current_verse(self) -> None:
        """The screen was explicitly cleared (e.g. a STOP command) —
        there is no longer a displayed verse for hysteresis to protect."""
        self.context.current_verse = None
        self.context.current_verse_evidence = 0.0

    # ── Public entry point — call once per utterance ──────────────

    def process_utterance(
        self,
        utterance: str,
        candidates: List[ScriptureCandidate],
        explicit_reference: Optional[ScriptureCandidate] = None,
        now: Optional[float] = None,
    ) -> DecisionResult:
        """Point 2/3/4: every utterance is processed and folded into the
        rolling context, whether or not it ends up producing a display.

        explicit_reference (point 10): pass this when hybrid.py's own
        direct-reference extractor already fired for this utterance
        ("John chapter 3 verse 16"). Still runs through a plausibility
        check (does hybrid.py's caller already know this verse exists in
        the DB?) before bypassing accumulation — the bypass is for the
        WAITING requirement, not for validity checking, which stays the
        existing engine's job."""
        now = now if now is not None else time.time()

        self.context.recent_utterances.append(utterance)
        self.context.recent_timestamps.append(now)
        self._prune_expired(now)
        self._decay_absent(candidates, now)

        if explicit_reference is not None:
            return self._handle_explicit_reference(explicit_reference, now)

        ambiguous_keys = _is_ambiguous(candidates, self.config)

        for cand in candidates:
            self._update_evidence(cand, now, ambiguous=cand.key in ambiguous_keys)

        return self._decide(ambiguous_keys)

    # ── Evidence accumulation (point 5) ─────────────────────────────

    def _update_evidence(self, cand: ScriptureCandidate, now: float, ambiguous: bool) -> CandidateEvidence:
        ev = self.context.candidate_evidence.get(cand.key)
        if ev is None:
            ev = CandidateEvidence(key=cand.key, text=cand.text, first_seen=now)
            self.context.candidate_evidence[cand.key] = ev

        ev.text = cand.text or ev.text
        # Seed the EMA with the raw score on a brand-new candidate's
        # FIRST sighting, rather than blending from the dataclass's 0.0
        # default (alpha*score + (1-alpha)*0 = 0.5*score) — that was a
        # real bug, not intentional conservatism: it silently halved the
        # w_historical component for every first-ever sighting of a
        # candidate, on top of w_consistency correctly being 0 with only
        # one data point (a real trend needs >= 2 points). The two
        # together made HIGH_CONFIDENCE structurally close to
        # unreachable in a single utterance even for a strong, highly
        # distinctive match — confirmed broadly, not just anecdotally:
        # running all 98 semantic/paraphrase cases across this project's
        # three eval testsets as isolated single utterances through the
        # real wired engine, only 31 still displayed in one utterance
        # before this fix; the other 66 include many that were genuine,
        # correct single-quote matches before the context layer existed.
        # A proper EMA equals the observed value at t=1 by definition —
        # this was a seeding bug, not a threshold this project chose.
        if not ev.score_history:
            ev.ema = cand.semantic_score
        else:
            ev.ema = _update_ema(ev.ema, cand.semantic_score, self.config.ema_alpha)
        ev.score_history.append(cand.semantic_score)
        ev.timestamps.append(now)
        ev.absences = 0
        ev.last_seen = now

        if ambiguous:
            # This utterance's own signal doesn't distinguish this
            # candidate from a close rival — still record the score (so
            # a later, unambiguous utterance sees real history) but
            # don't let the streak grow off an undecided moment.
            ev.streak = max(0, ev.streak - 1)
        else:
            ev.streak += 1

        contextual = _contextual_alignment(
            cand.text or ev.text, self.context.recent_utterances,
            embed_fn=self._embed_fn, similarity_fn=self._similarity_fn,
        )
        consistency = _consistency_bonus(ev.score_history)

        if len(ev.score_history) < 2:
            # No real trend is possible from a single data point —
            # w_consistency's share is redistributed across the other
            # three components (renormalized to still sum to 1.0)
            # instead of simply being discarded. Without this, a
            # genuinely strong, unambiguous FIRST utterance was
            # structurally capped well below the display bar regardless
            # of how clear the match was — confirmed broadly, not
            # anecdotally: of 98 semantic/paraphrase cases across this
            # project's three eval testsets run as isolated single
            # utterances through the real wired engine, this and the EMA-
            # seeding fix above were both needed before genuinely strong
            # single-utterance matches could reach HIGH_CONFIDENCE at all.
            active = self.config.w_current + self.config.w_historical + self.config.w_contextual
            evidence = (
                self.config.w_current * cand.semantic_score
                + self.config.w_historical * ev.ema
                + self.config.w_contextual * contextual
            ) / active
        else:
            evidence = (
                self.config.w_current * cand.semantic_score
                + self.config.w_historical * ev.ema
                + self.config.w_contextual * contextual
                + self.config.w_consistency * consistency
            )
        ev.last_evidence = max(0.0, min(1.0, evidence))

        required_streak = _required_streak(cand, self.config)
        ev.state = self._next_state(ev, required_streak, ambiguous)
        return ev

    def _next_state(self, ev: CandidateEvidence, required_streak: int, ambiguous: bool) -> CandidateState:
        cfg = self.config
        if ambiguous and ev.last_evidence < cfg.stable_threshold:
            return CandidateState.INSUFFICIENT_CONTEXT

        if ev.last_evidence >= cfg.high_confidence_threshold and ev.streak >= required_streak:
            return CandidateState.HIGH_CONFIDENCE
        if ev.last_evidence >= cfg.stable_threshold and ev.streak >= required_streak:
            return CandidateState.STABLE_CANDIDATE
        if ev.last_evidence >= cfg.contextual_support_threshold:
            return CandidateState.CONTEXTUAL_SUPPORT
        return CandidateState.CANDIDATE

    # ── Lifecycle: expiry, absence decay, contradiction ─────────────

    def _prune_expired(self, now: float) -> None:
        stale = [
            k for k, ev in self.context.candidate_evidence.items()
            if now - ev.last_seen > self.config.evidence_timeout_s
        ]
        for k in stale:
            del self.context.candidate_evidence[k]

    def _decay_absent(self, candidates: List[ScriptureCandidate], now: float) -> None:
        present = {c.key for c in candidates}
        to_drop = []
        for k, ev in self.context.candidate_evidence.items():
            if k in present:
                continue
            ev.absences += 1
            ev.streak = 0
            ev.ema *= self.config.absence_decay
            ev.last_evidence *= self.config.absence_decay
            if ev.absences >= self.config.absence_prune_after:
                ev.state = CandidateState.REJECTED
                to_drop.append(k)
        for k in to_drop:
            del self.context.candidate_evidence[k]

    # ── Explicit reference bypass (point 10) ─────────────────────────

    def _handle_explicit_reference(self, cand: ScriptureCandidate, now: float) -> DecisionResult:
        ev = self._update_evidence(cand, now, ambiguous=False)
        ev.state = CandidateState.HIGH_CONFIDENCE
        ev.last_evidence = max(ev.last_evidence, 0.97)
        self.context.current_verse = cand.key
        self.context.current_verse_evidence = ev.last_evidence
        self.context.confidence_history.append(ev.last_evidence)
        return DecisionResult(
            decision=Decision.DISPLAY,
            verse=cand.key,
            evidence=ev.last_evidence,
            state=CandidateState.HIGH_CONFIDENCE,
            reason="Explicit scripture reference — bypasses accumulation, not plausibility checking.",
        )

    # ── Decision derivation + hysteresis (points 8, 9, 12, 13) ───────

    def _decide(self, ambiguous_keys: List[VerseKey]) -> DecisionResult:
        cfg = self.config
        candidates = list(self.context.candidate_evidence.values())

        # CONTEXTUAL_SUPPORT is included here, not just STABLE_CANDIDATE/
        # HIGH_CONFIDENCE — a real, corroborated-enough-to-clear-0.45
        # candidate should reach the operator as a building detection,
        # the same "surface it, don't silently drop it" principle this
        # project already established the hard way (hybrid.py's own
        # history: showing every raw, un-gated candidate was tried and
        # reverted as noise, but showing nothing below the display bar
        # was ALSO reverted for discarding genuine near-misses with zero
        # trace — see _run_semantic's history). Bare CANDIDATE state
        # (below 0.45, often a single noisy sighting) stays unsurfaced —
        # that is the noise case that got reverted.
        leading = None
        for ev in candidates:
            if ev.state not in (
                CandidateState.HIGH_CONFIDENCE,
                CandidateState.STABLE_CANDIDATE,
                CandidateState.CONTEXTUAL_SUPPORT,
            ):
                continue
            if leading is None or ev.last_evidence > leading.last_evidence:
                leading = ev

        if leading is None:
            if ambiguous_keys:
                return self._result(Decision.WAIT_FOR_CONTEXT, None, 0.0, None,
                                     "Multiple close, low-specificity candidates this utterance — "
                                     "waiting for the sermon to disambiguate.", ambiguous_keys)
            if self.context.current_verse:
                return self._result(Decision.KEEP_CURRENT, self.context.current_verse,
                                     self.context.current_verse_evidence,
                                     self.context.candidate_evidence.get(self.context.current_verse, CandidateEvidence(self.context.current_verse)).state,
                                     "No candidate currently clears the evidence bar — holding what's on screen.")
            return self._result(Decision.NO_CONFIDENT_MATCH, None, 0.0, None,
                                 "No candidate clears the evidence bar and nothing is on screen.")

        # An operator-review signal for a candidate that's been sitting
        # at STABLE for a long time without quite reaching HIGH_CONFIDENCE
        # — surfaced instead of silently waiting forever (point 8).
        if (
            leading.state == CandidateState.STABLE_CANDIDATE
            and leading.streak >= cfg.human_review_streak
            and leading.last_evidence >= cfg.human_review_min_factor * cfg.high_confidence_threshold
        ):
            return self._result(Decision.HUMAN_CONFIRMATION_REQUIRED, leading.key, leading.last_evidence,
                                 leading.state,
                                 f"Sustained for {leading.streak} utterances at "
                                 f"{leading.last_evidence:.2f} without reaching high confidence — "
                                 "likely the right verse, but flagging for operator judgment "
                                 "rather than auto-deciding.")

        if leading.state in (CandidateState.STABLE_CANDIDATE, CandidateState.CONTEXTUAL_SUPPORT):
            decision = Decision.WAIT_FOR_CONTEXT if not self.context.current_verse else Decision.KEEP_CURRENT
            return self._result(decision, leading.key, leading.last_evidence, leading.state,
                                 "Building evidence, hasn't reached the display bar yet.")

        # leading.state == HIGH_CONFIDENCE
        if self.context.current_verse is None:
            return self._promote(leading, "No verse currently on screen and this candidate cleared "
                                            "the high-confidence bar.")

        if leading.key == self.context.current_verse:
            self.context.current_verse_evidence = leading.last_evidence
            return self._result(Decision.KEEP_CURRENT, leading.key, leading.last_evidence, leading.state,
                                 "Same verse already displayed — reconfirmed, not re-triggered.")

        # Switching candidate (point 9's hysteresis)
        if (
            leading.last_evidence > self.context.current_verse_evidence + cfg.switch_margin
            and leading.streak >= cfg.min_streak_to_switch
        ):
            return self._promote(
                leading,
                f"New candidate exceeds displayed verse by {leading.last_evidence - self.context.current_verse_evidence:.2f} "
                f"(> switch_margin {cfg.switch_margin}) and has held for {leading.streak} utterances."
            )

        return self._result(Decision.KEEP_CURRENT, self.context.current_verse,
                             self.context.current_verse_evidence,
                             self.context.candidate_evidence.get(self.context.current_verse, leading).state,
                             f"{leading.key} leads this utterance ({leading.last_evidence:.2f}) but hasn't "
                             f"cleared the switch margin/streak over the displayed verse "
                             f"({self.context.current_verse_evidence:.2f}) — holding.")

    def _promote(self, ev: CandidateEvidence, reason: str) -> DecisionResult:
        self.context.current_verse = ev.key
        self.context.current_verse_evidence = ev.last_evidence
        self.context.confidence_history.append(ev.last_evidence)
        return self._result(Decision.DISPLAY, ev.key, ev.last_evidence, ev.state, reason)

    @staticmethod
    def _result(decision, verse, evidence, state, reason, ambiguous_with=None) -> DecisionResult:
        return DecisionResult(
            decision=decision, verse=verse, evidence=evidence, state=state,
            reason=reason, ambiguous_with=ambiguous_with or [],
        )
