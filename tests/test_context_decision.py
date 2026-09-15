"""
Demonstrates the context-decision layer (app/retrieval/context_decision.py)
against the exact scenarios given in its design brief: that accumulated,
sustained evidence changes the display decision compared to judging each
utterance in isolation, and that the system stays conservative under
ambiguity and around an already-displayed verse.

Uses synthetic ScriptureCandidate sequences rather than a live ASR/
semantic-engine run — this module operates purely on whatever candidates
the existing engine already produced, so its behavior is fully testable
without loading the embedding model.
"""

from app.retrieval.context_decision import (
    CandidateState,
    ContextManager,
    Decision,
    DecisionConfig,
    ScriptureCandidate,
)

JOSHUA = ("Joshua", 1, 9)
PSALM_34 = ("Psalms", 34, 8)
PSALM_100 = ("Psalms", 100, 5)
NAHUM = ("Nahum", 1, 7)
JOHN_3_16 = ("John", 3, 16)
ROMANS_5_8 = ("Romans", 5, 8)


def cand(key, score, lex=0.4, text=""):
    book, chapter, verse = key
    return ScriptureCandidate(
        book=book, chapter=chapter, verse=verse, text=text,
        semantic_score=score, lexical_score=lex, final_score=score,
        method="semantic_lexical",
    )


# ════════════════════════════════════════════════════════════
# Point 5 — evidence accumulation: a rising streak beats an isolated spike
# ════════════════════════════════════════════════════════════

def test_moderate_single_utterance_does_not_display():
    """A MODERATE, still-somewhat-generic isolated first mention should
    NOT jump straight to DISPLAY — point 12's 'when uncertain, do not
    change the projection'. Note this is deliberately a moderate score
    (0.60), not a maximal one: point 12 also says HIGH CONFIDENCE ->
    DISPLAY unconditionally, so a genuinely strong, specific single
    utterance legitimately CAN display on its own (see
    test_strong_specific_single_utterance_displays_immediately below) —
    the real value of accumulation is rescuing MODERATE, uncertain
    evidence into confidence over multiple utterances, not an absolute
    'always wait' rule regardless of strength."""
    cm = ContextManager()
    result = cm.process_utterance(
        "Today I want to talk about courage.",
        [cand(JOSHUA, 0.60, lex=0.20, text=JOSHUA_1_9_TEXT)],
    )
    assert result.decision != Decision.DISPLAY


def test_strong_specific_single_utterance_displays_immediately():
    """The flip side of the above: a single utterance that is both
    highly semantically similar AND lexically specific (not generic
    vocabulary — see MIN_SEMANTIC_TOKENS/specificity gate) is genuinely
    HIGH CONFIDENCE evidence on its own, and point 12's own decision
    table (HIGH CONFIDENCE -> DISPLAY) does not gate that on utterance
    count. Confirmed necessary broadly, not just here: running this
    project's three eval testsets' 98 semantic/paraphrase cases as
    isolated single utterances through the real wired engine, many were
    genuine one-sentence quotes that should display immediately."""
    cm = ContextManager()
    result = cm.process_utterance(
        "He told Joshua to be strong and of a good courage.",
        [cand(JOSHUA, 0.91, lex=0.55, text=JOSHUA_1_9_TEXT)],
    )
    assert result.decision == Decision.DISPLAY


JOSHUA_1_9_TEXT = (
    "Be strong and of a good courage, be not afraid, neither be thou "
    "dismayed: for the LORD thy God is with thee whithersoever thou goest."
)


def test_rising_evidence_across_utterances_eventually_displays():
    """The scenario from the brief: as the sermon moves from a general
    topic ('talk about courage') through supporting statements toward
    language that echoes the actual verse, rising evidence should be
    treated as materially stronger than a single isolated high score,
    and should reach DISPLAY only once, at the end, after accumulation —
    not on the first mention."""
    cm = ContextManager()
    utterances = [
        ("Today I want to talk about courage.", 0.60),
        ("There are times when we become afraid.", 0.68),
        ("He told Joshua to be strong and of good courage.", 0.80),
        ("Do not be afraid, for the LORD your God is with you wherever you go.", 0.93),
    ]
    decisions = []
    for text, score in utterances:
        r = cm.process_utterance(text, [cand(JOSHUA, score, lex=0.45, text=JOSHUA_1_9_TEXT)])
        decisions.append(r.decision)

    assert decisions[0] != Decision.DISPLAY, "must not fire on the first, general utterance alone"
    assert decisions[-1] == Decision.DISPLAY, "sustained rising evidence should clear the bar by the end"
    assert decisions.count(Decision.DISPLAY) == 1, "should display once, at the point evidence is sufficient"


def test_moderate_isolated_utterance_does_not_beat_the_same_score_sustained():
    """An isolated MODERATE score (0.60 — the same value the rising
    sequence above starts at) alone should not display; the same
    starting score, reinforced across several corroborating utterances,
    should. Demonstrates accumulation genuinely rescues a moderate,
    uncertain signal into confidence — the case strong single-utterance
    evidence (test_strong_specific_single_utterance_displays_immediately)
    doesn't need help with, and shouldn't be confused with."""
    cm_spike = ContextManager()
    r_spike = cm_spike.process_utterance(
        "Today I want to talk about courage.",
        [cand(JOSHUA, 0.60, lex=0.20, text=JOSHUA_1_9_TEXT)],
    )

    cm_sustained = ContextManager()
    utterances = [
        ("Today I want to talk about courage.", 0.60),
        ("There are times when we become afraid.", 0.68),
        ("He told Joshua to be strong and of good courage.", 0.80),
        ("Do not be afraid, for the LORD your God is with you wherever you go.", 0.93),
    ]
    r_sustained = None
    for text, score in utterances:
        r_sustained = cm_sustained.process_utterance(text, [cand(JOSHUA, score, lex=0.45, text=JOSHUA_1_9_TEXT)])

    assert r_spike.decision != Decision.DISPLAY
    assert r_sustained.decision == Decision.DISPLAY


# ════════════════════════════════════════════════════════════
# Point 6 — ambiguous common statements wait for disambiguating context
# ════════════════════════════════════════════════════════════

def test_ambiguous_close_candidates_wait_instead_of_displaying():
    """'God is good' -> three close, generic candidates. None should be
    displayed on the strength of this one utterance alone."""
    cm = ContextManager()
    result = cm.process_utterance(
        "God is good.",
        [
            cand(PSALM_34, 0.84, lex=0.15, text="O taste and see that the LORD is good"),
            cand(PSALM_100, 0.82, lex=0.15, text="For the LORD is good"),
            cand(NAHUM, 0.79, lex=0.15, text="The LORD is good, a strong hold in the day of trouble"),
        ],
    )
    assert result.decision in (Decision.WAIT_FOR_CONTEXT, Decision.NO_CONFIDENT_MATCH)
    assert result.decision != Decision.DISPLAY


def test_disambiguating_followup_resolves_to_the_right_verse():
    """After the ambiguous opener, a distinctive follow-up ('taste and
    see') should let Psalm 34:8 pull ahead and eventually display,
    without the other two ever having been shown."""
    cm = ContextManager()
    cm.process_utterance(
        "God is good.",
        [
            cand(PSALM_34, 0.84, lex=0.15, text="O taste and see that the LORD is good"),
            cand(PSALM_100, 0.82, lex=0.15, text="For the LORD is good"),
            cand(NAHUM, 0.79, lex=0.15, text="The LORD is good, a strong hold in the day of trouble"),
        ],
    )
    result = cm.process_utterance(
        "Taste and see that the Lord is good.",
        [cand(PSALM_34, 0.93, lex=0.55, text="O taste and see that the LORD is good")],
    )
    assert result.decision == Decision.DISPLAY
    assert result.verse == PSALM_34


# ════════════════════════════════════════════════════════════
# Point 9 — hysteresis around an already-displayed verse
# ════════════════════════════════════════════════════════════

def test_slightly_higher_rival_does_not_immediately_replace_displayed_verse():
    cm = ContextManager()
    for _ in range(3):
        cm.process_utterance(
            "For God so loved the world that he gave his only Son.",
            [cand(JOHN_3_16, 0.80, lex=0.5, text="For God so loved the world")],
        )
    assert cm.context.current_verse == JOHN_3_16

    result = cm.process_utterance(
        "God demonstrates his love for us.",
        [cand(ROMANS_5_8, 0.83, lex=0.3, text="God commendeth his love toward us")],
    )
    assert result.decision == Decision.KEEP_CURRENT
    assert cm.context.current_verse == JOHN_3_16


def test_sustained_stronger_rival_eventually_switches():
    cm = ContextManager()
    for _ in range(3):
        cm.process_utterance(
            "For God so loved the world that he gave his only Son.",
            [cand(JOHN_3_16, 0.80, lex=0.5, text="For God so loved the world")],
        )
    assert cm.context.current_verse == JOHN_3_16

    decisions = []
    for _ in range(6):
        r = cm.process_utterance(
            "While we were yet sinners, God commended his love toward us.",
            [cand(ROMANS_5_8, 0.95, lex=0.6, text="God commendeth his love toward us, "
                                                   "in that while we were yet sinners, Christ died for us")],
        )
        decisions.append(r.decision)

    assert Decision.DISPLAY in decisions
    assert cm.context.current_verse == ROMANS_5_8


# ════════════════════════════════════════════════════════════
# Point 10 — explicit references bypass accumulation
# ════════════════════════════════════════════════════════════

def test_explicit_reference_displays_immediately():
    cm = ContextManager()
    result = cm.process_utterance(
        "John chapter 3 verse 16.",
        candidates=[],
        explicit_reference=cand(JOHN_3_16, 0.97, lex=1.0, text="For God so loved the world"),
    )
    assert result.decision == Decision.DISPLAY
    assert result.verse == JOHN_3_16


# ════════════════════════════════════════════════════════════
# Point 7 — generic vocabulary requires more corroboration
# ════════════════════════════════════════════════════════════

def test_generic_wording_needs_more_streak_than_distinctive_wording():
    """Two candidates reaching the same semantic_score every utterance,
    differing only in lexical_score (specificity) — the low-specificity,
    highly-generic one ('God is good ... he loves everyone') should NOT
    reach DISPLAY within the same number of utterances the distinctive,
    higher-lexical one ('perfect love casteth out fear') does; point 7's
    'generic language requires more context' should show up as a real
    gap in when each is allowed to display. The generic one sustaining
    for many utterances without displaying should instead surface as
    HUMAN_CONFIRMATION_REQUIRED (point 8) rather than silently stalling
    forever or auto-displaying on weak grounds."""
    cm_generic = ContextManager()
    cm_specific = ContextManager()

    generic_first_display = None
    specific_first_display = None
    final_generic_decision = None
    for i in range(6):
        rg = cm_generic.process_utterance(
            "God is good and he loves everyone very much today.",
            [cand(("1 John", 4, 8), 0.75, lex=0.10,
                  text="He that loveth not knoweth not God; for God is love.")],
        )
        rs = cm_specific.process_utterance(
            "Perfect love casts out all fear from our hearts.",
            [cand(("1 John", 4, 18), 0.75, lex=0.50,
                  text="There is no fear in love; but perfect love casteth out fear")],
        )
        final_generic_decision = rg.decision
        if generic_first_display is None and rg.decision == Decision.DISPLAY:
            generic_first_display = i
        if specific_first_display is None and rs.decision == Decision.DISPLAY:
            specific_first_display = i

    assert specific_first_display is not None, "the distinctive paraphrase should reach DISPLAY"
    assert generic_first_display is None or generic_first_display > specific_first_display, (
        "generic wording must not out-pace distinctive wording to DISPLAY"
    )
    assert final_generic_decision != Decision.DISPLAY, (
        "sustained but purely generic wording should not auto-display within this window"
    )
