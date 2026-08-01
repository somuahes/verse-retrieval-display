# Bible AI — Operator Workflow Layer: Progress Log

Reference document for everything changed in this build session. Covers the
foundation work, all nine planned features (one skipped by explicit choice),
and known limitations/next steps.

**Hard constraint honored throughout**: `HybridEngine`'s matching/decision
logic was touched exactly once, in one place, adding three metadata fields
to verse dicts already being returned — no branching, threshold, or
state-transition logic was changed. See "hybrid.py diff" below.

---

## 0. Foundation / preliminary work

The app had two parallel operator UIs: `app/ui/operator_panel.py` (wired to
`main.py`, the actual launcher) and `app/ui/main_ui.py` (a more built-out but
completely unwired, unused, self-contained implementation). After discussion,
**`main_ui.py` was chosen as the canonical, live entry point** going forward.
`main.py` and `operator_panel.py` are now legacy/unmaintained (not deleted,
just not the target of any further work).

### Bugs found and fixed along the way
- **`main_ui.py` could never actually run.** Its `sys.path` fix (needed so
  `app.*` imports resolve when the file is launched directly) was placed
  *after* the imports that needed it. Fixed by moving it to the top of the
  file, before any `app.*` import.
- **Broken duplicate `DisplayWindow`.** `main_ui.py` had its own embedded
  copy of the projector window with a different API (`.clear_verse()`
  instead of `.clear()`, plain `.show()` instead of `.launch()`'s
  fullscreen-on-second-screen behavior). Swapped for the real
  `app/ui/display_window.py`.
- **Dead feedback-loop code removed** — `main_ui.py` had UI wired to
  `HybridEngine.record_outcome()` / `run_threshold_update()`, methods that
  don't exist on `HybridEngine` (silently no-op'd via `hasattr` guards).
  Not part of any requested feature, so removed rather than built out.
- **`main.py`'s `transcriber.stop_stream()` call was broken** — the
  transcriber class only defines `start()`/`stop()`. Fixed by moving mic
  lifecycle ownership into the panel itself (Start/Stop button + device
  picker), matching the pattern `main_ui.py` already had.
- **Both project venvs (`venv/`, `venv311/`) were broken** — created on a
  different machine, pointing at a nonexistent Python install path.
  `venv311/pyvenv.cfg` was repaired to point at the actual local Python
  3.11.9 install; `venv/` (needs Python 3.12, not installed here) was left
  alone since `venv311` covers everything needed.

### How to run
```powershell
cd "C:\Users\apets\Downloads\final yr project\bible_ai_project"
.\venv311\Scripts\python.exe app\ui\main_ui.py
```
Optional: `--version BBE` to start in a different Bible version,
`--list-devices` to list audio input devices.

---

## 1. AI Detections panel

**`app/retrieval/hybrid.py` diff (the only permitted change, and the only
change made to this file all session)**: at the two existing points where a
genuine match is found — direct reference and semantic — three metadata
keys are attached to the verse dict already being passed to `_display()`:
`match_type` (`"direct"` or `"semantic"`), `confidence` (float — fixed
`0.97` for direct, `final_score` for semantic), `matched_at` (timestamp).
Also added: `DIRECT_MATCH_CONFIDENCE = 0.97` constant. Navigation/repeat/
version-switch/manual-display verses do **not** get these fields, which is
how the UI tells a genuine detection apart from a navigation echo.

**`app/ui/main_ui.py`**: new third column, "AI Detections" — `DetectionCard`
widget (reference, DIRECT/SEMANTIC source badge, confidence %, colored
confidence-tier edge bar, timestamp, ▶/+ actions), scrollable panel with
newest-first ordering, a 30-card cap, filters anything under 35%
confidence, and a "N new ▲" badge when you've scrolled away from the top.

### Confidence tiers (added after a threshold investigation — see below)
Cards get a green "HIGH CONFIDENCE" (≥65%) or amber "POSSIBLE MATCH" (<65%)
left-edge bar, because — as documented below — no clean confidence
threshold actually separates correct detections from false positives with
the current matching model.

---

## 2. Preview / Live Output split

**`app/ui/main_ui.py`**: "NOW DISPLAYING" card renamed to "PREVIEW"; new
black "LIVE OUTPUT" mini-card mirrors the real projector state; "Go Live"
toggle in the topbar, also bound to the `L` key (verified it doesn't
hijack typing in text fields — checks `QApplication.focusWidget()` before
acting). ON = Preview and Live stay synced. OFF = Preview updates freely,
Live stays put until "Push to Live →" is clicked.

All promotion paths (detection ▶, Queue ▶, Search Enter) funnel through one
`_promote_to_preview()` method, so Preview always gets it first and Live
only follows per the Go Live rule — never bypassed.

### Projector window: windowed-by-default, explicit fullscreen toggle
`app/ui/display_window.py`'s `launch()` originally went straight to
fullscreen on the second monitor. Changed so `launch()` opens as a normal,
resizable, movable window (centered on the second monitor if one exists,
otherwise wherever the OS puts it) — it never unexpectedly covers the
operator's only monitor. A separate `enter_fullscreen()` /
`exit_fullscreen()` / `toggle_fullscreen()` (plus an `is_fullscreen`
property) actually goes fullscreen, wired to a new "⛶ Fullscreen" button
in `main_ui.py`'s topbar (disabled until the display is open, label swaps
to "⛶ Exit Fullscreen" while active). Verified: opens windowed, toggles
both directions, button enable state tracks display visibility correctly.

---

## 3. Queue

**`app/ui/main_ui.py`**: `QueueItem` widget, ordered list, add via + from
Detections, ▶ sends to Preview **without** removing the item (no auto-pop,
per spec), ✕ removes it. Search → Queue wiring deferred until Feature 5
rebuilt the search UI (the old search output was one static HTML blob, not
individually selectable — wiring "+" onto it would have been thrown-away
work).

---

## 4. Auto / Manual display mode

**`app/ui/main_ui.py`**: topbar toggle, persisted via `QSettings`,
**defaults to Manual** (see threshold investigation below for why).

- **Manual**: engine detections land in the AI Detections panel only —
  nothing reaches Preview/Live without an explicit ▶ from Detections,
  Queue, or Search.
- **Auto**: highest-confidence detection is pushed automatically, gated by
  a cooldown (`AUTO_COOLDOWN_DIRECT = 1.5s`, `AUTO_COOLDOWN_SEMANTIC = 4.0s`)
  and a stricter auto-only confidence floor (`AUTO_SEMANTIC_MIN_CONFIDENCE
  = 0.65`) — a clearly higher-confidence match can override an active
  cooldown instead of waiting it out.

This is UI-level policy only (in `main_ui.py`) — `hybrid.py`'s own
selection logic is never touched by this feature.

---

## 5. Bible search upgrades

**`app/ui/main_ui.py`**: replaced the old plain keyword-search card with
Book mode (reference lookup via `reference_extractor.extract_reference()`,
book-name autocomplete via `QCompleter`, digit-only input jumps to that
verse number within the last-shown chapter) and Context mode (real
semantic search via `SemanticEngine.search_top_k()`), toggled by `Tab`.
Enter → Preview. Two Enters within 0.6s → Preview **and** forces Go Live
on. + adds the top result to Queue.

Fixed one gap found during testing: Book-mode reference lookups originally
called `db_get_verse()` directly, which doesn't update the engine's
session position — so a subsequent digit-only "jump to verse N" had no
chapter to jump within. Fixed by having `_promote_search_result()` also
call `self._engine.session.update_position(...)`, keeping the Nav card's
verse-jump and Book-mode's digit-jump consistent with whatever was last
shown via search.

---

## 6. Confidence & source badges everywhere pre-Live

**`app/ui/main_ui.py`**: shared `_make_badge_pair()` helper, wired into
`QueueItem`, `SearchResultItem`, and the PREVIEW card (badges hidden
gracefully when a verse has no `match_type`, e.g. a plain Book-mode
reference lookup with nothing to grade). Context-mode search results
(`search_top_k()`) don't carry `match_type`/`confidence` natively — those
keys are only added inside `hybrid.py`'s own pipeline — so they're
normalized in the UI layer only (`c.setdefault("match_type", "semantic")`,
`c.setdefault("confidence", c.get("final_score", 0))`), never touching
`semantic.py` or `hybrid.py`.

---

## 7. Theme system (reduced first pass — by agreement)

Full spec (5 shadow layers, gradients, image blur/overlay, text-transform/
decoration, layout modes, verse-number superscript, zoom/pan canvas) was
flagged upfront as needing a phased cut. Built now:

- **`app/ui/theme_model.py`** (new) — `Theme` dataclass: font family/
  weight/size, text color, alignment, solid-or-image background (with
  cover/contain/stretch fit), reference position (above/below), padding,
  element spacing, content-area %. 3 starter themes (Classic Gold, Modern
  Minimal, High Contrast).
- **`app/ui/theme_store.py`** (new) — JSON CRUD: save/load/list/rename/
  duplicate/delete/import/export, under a `themes/` folder.
- **`app/ui/display_window.py`** — rewritten to render entirely from the
  active `Theme` (was hardcoded before). Auto-fit-to-screen font sizing is
  unchanged logic, just parameterized off the theme's base size and
  content-area %.
- **`app/ui/theme_designer.py`** (new) — library list (left) / live 960×540
  preview reusing the real `DisplayWindow` as an embedded widget (center)
  / Text-Background-Layout tabs (right). Draft + dirty-tracking + save/
  discard, all CRUD actions, and a confirmation prompt before closing with
  unsaved changes.
- **`app/ui/main_ui.py`** — "🎨 Themes…" button opens the Designer; active
  theme choice persists across restarts via `QSettings`.

**Deferred to a follow-up pass** (tell me if you want any of these next):
shadow layers, stroke, gradients, image blur/opacity/overlay, line-height/
letter-spacing/text-transform/decoration, vertical/horizontal/subtitle-
overlay layout modes, verse-number superscript, zoom/pan canvas controls.

---

## 8. NIV / NLT Bible data — SKIPPED (your choice)

Researched licensing before writing any code: neither NIV (Biblica) nor
NLT (Tyndale) can legally be bulk-cached offline in this app's architecture
(local SQLite + embedding index) — both official channels (API.Bible,
api.nlt.to) license *live fetch only*, not local caching, which conflicts
with the offline-only constraint for this whole project. Public-domain
modern-English alternatives exist and were identified (Berean Standard
Bible — CC0; World English Bible — public domain), but you chose to skip
this feature entirely for now rather than substitute or build a pipeline
without data. `version_detector.py` and `db.py` still support NIV/NLT by
code; there's just no data imported. No files were touched for this item.

---

## 9. Formal accuracy & latency evaluation harness

**`app/evaluation/`** — new standalone package, never imported by the live
app or operator panel:

- **`pipeline.py`** — a parallel, read-only replication of `hybrid.py`'s
  direct-then-semantic priority, with per-stage timing. Imports
  `SEMANTIC_CONFIDENCE` directly from `hybrid.py` (not duplicated as a
  constant) so the threshold used in evaluation can never silently drift
  out of sync with production. Deliberately excludes navigation/version-
  switch/state-tracking — accuracy cases are independent utterances, not
  multi-turn conversations.
- **`dataset_schema.py`** — CSV schema (`id, input_type, input, case_type,
  expected_book, expected_chapter, expected_verse, notes`) and a template
  writer. `input_type` is `text` or `audio`; `case_type` is `direct`,
  `semantic`, or `no_match_expected` (for false-positive testing).
- **`report.py`** — shared CSV/JSON writers + a plain-text summary-table
  printer.
- **`accuracy_eval.py`** — loads a labeled test set, runs every case
  through the real pipeline, reports exact-match accuracy overall and
  split by direct vs semantic, a confusion table of what it got wrong,
  false-positive rate, no-match rate. Saves timestamped CSV + JSON to
  `app/evaluation/results/`.
- **`latency_bench.py`** — stage-by-stage timing (direct-reference
  extraction, semantic search, DB lookup, and ASR when audio cases are
  present), reports which Whisper model sizes are actually installed
  locally (only `base.en` right now) and benchmarks across all of them if
  more than one is present.
- **`testsets/starter_testset.csv`** — 31 hand-assembled cases, sourced
  from `reference_extractor.py`'s own validated test examples,
  `semantic.py`'s phrase/event maps, and the real paraphrase/false-positive
  findings from the threshold investigation below.

### Real results from this session (KJV, `base.en`, starter set)
```
Total test cases                               31
Exact-match accuracy (direct+semantic)         95.5%
Direct-reference accuracy                      100.0%  (n=10)
Semantic-match accuracy                        91.7%   (n=12)
False-positive rate (no-match-expected cases)  44.4%   (n=9)
No-match rate (expected a match, got none)     4.5%
```
Latency: direct-reference extraction ~0.6ms mean, semantic search ~174ms
mean (dominates total latency), DB lookup ~17ms mean.

**For a credible thesis result**: 31 cases is a reasonable starter, but aim
for **at least 150–200 direct-reference and 150–200 semantic cases** —
and, more importantly, **draw them from real recorded sermon audio**, not
typed text. This starter set can't measure ASR mis-transcription at all,
which is a major real-world error source; accuracy on clean text will
look better than accuracy in an actual service.

### Run it
```powershell
.\venv311\Scripts\python.exe -m app.evaluation.accuracy_eval
.\venv311\Scripts\python.exe -m app.evaluation.latency_bench
```

---

## The false-positive / threshold investigation (context for Features 1, 4, 9)

Midway through the build, real usage surfaced that the app displays verses
for generic sermon small-talk too often. Investigated by feeding realistic
phrases through the real pipeline (not guessing):

- Confirmed pre-existing, not a regression: same behavior would occur on
  the untouched `hybrid.py`.
- Tried raising `SEMANTIC_CONFIDENCE` — **doesn't work cleanly**. A real
  false positive ("i believe god is with us today" → Acts 27:25) scores
  **0.667**, higher than four genuine paraphrase matches (0.630, 0.625,
  0.544, 0.529). Any threshold that blocks the false positive also blocks
  most real matches.
- Tried a stricter lexical/word-overlap floor — **also doesn't separate
  cleanly**, for the same reason: short generic Christian phrases
  ("faith", "god", "love") share vocabulary with short verses by chance,
  inflating overlap scores regardless of genuine relevance.
- Conclusion: this is a real precision ceiling of the embedding model
  (`all-MiniLM-L6-v2`) at short phrase lengths, not a tunable bug. The
  actual mitigation is operator-in-the-loop, which is exactly what
  Features 2 and 4 (Preview/Live staging, Manual-mode-by-default) provide.
- This is also why Feature 1's confidence tiers frame things as
  "possible match" rather than pretending a single number means "correct."
- **If you want a principled fix later**: fine-tuning the embedding model
  on labeled hard negatives (generic phrases that wrongly matched) is the
  right lever — not more threshold-tuning. Discussed but not built (new
  scope, not one of the original 9 features); the same labeled test set
  from Feature 9 would double as training data.

---

## Files touched this session

**Created:**
- `app/ui/theme_model.py`, `app/ui/theme_store.py`, `app/ui/theme_designer.py`
- `app/evaluation/__init__.py`, `pipeline.py`, `dataset_schema.py`,
  `report.py`, `accuracy_eval.py`, `latency_bench.py`,
  `testsets/starter_testset.csv`
- `themes/` (created at runtime by `theme_store.py`, holds the starter
  theme JSON files)
- `PROGRESS.md` (this file)

**Rewritten:**
- `app/ui/main_ui.py` (bug fixes, then all UI-side feature work)
- `app/ui/display_window.py` (theme-driven rendering)

**Modified (metadata-only diff, as constrained):**
- `app/retrieval/hybrid.py`

**Simplified, then left as legacy/unmaintained (your call to deprioritize):**
- `main.py`, `app/ui/operator_panel.py`

**Untouched:**
- `app/retrieval/semantic.py`, `app/retrieval/reference_extractor.py`,
  `app/retrieval/version_detector.py`, `app/database/db.py`,
  `app/database/import_kjv.py`, `app/asr/transcriber.py`

---

## 10. Bounded worker pools: transcription dispatch + speculative semantic search

Follow-up request, re-covering ground from `SYSTEM_DOCUMENTATION.md` §8f/§8g
(adaptive fast/slow-preacher handling, hybrid direct+indirect detection, a
rolling sentence-context buffer, ~1s/~4s latency budgets, every detection
visible in the AI Detections panel) plus one new, explicit ask: "parallel
processing to optimise the response of the system."

Investigation confirmed everything from §8f/§8g is already live and working
as designed — `app/asr/transcriber.py` still has the signal-driven
(silence-endpointed) segmentation described there (despite that section's
own note about a since-superseded revert), `hybrid.py`'s two-speed pipeline
and rolling-context blending are unchanged, and the AI Detections panel
already surfaces navigation/verse-jump/version-switch/manual alongside
direct/semantic detections. The transcript-hallucination example re-raised
this session ("Good morning... I didn't say Amen... Jesus, Amen") was
confirmed to be the same historical bug report from §8f/§8d, not a fresh
failure against current code — so it was not re-investigated.

**The one confirmed, genuine gap**: no `ThreadPoolExecutor`/`asyncio`/worker
pool existed anywhere in `app/`. Transcription dispatch spawned a raw,
unbounded `threading.Thread` per utterance; `hybrid.py`'s semantic search
(embed + FAISS, ~50–170ms) only started once the grace-period timer fired,
serially *after* the wait instead of overlapping with it. Fixed both,
without touching any matching/threshold/priority logic:

**`app/asr/transcriber.py`**:
- `_finalize()`'s per-utterance `threading.Thread(...).start()` replaced
  with `self._executor.submit(...)` against a `ThreadPoolExecutor(
  max_workers=2)` created in `__init__`. Caps concurrent decode threads
  (each already internally multithreaded via `cpu_threads=8`) instead of
  letting them proliferate unbounded under rapid speech / forced
  `max_utterance_seconds` cuts.
- `_transcribe_utterance()` now checks `self.stop_event.is_set()` right
  before firing `callback_fn` — a decode that finishes after `stop()` was
  clicked no longer pushes a stale result into the engine.
- `stop()` now calls `self._executor.shutdown(wait=False,
  cancel_futures=True)` and replaces it with a fresh executor, so a
  subsequent `start()` still has a usable pool.

**`app/retrieval/hybrid.py`** — speculative semantic search during the
grace period:
- `_queue_semantic()` now also kicks off `self.semantic.search(...)` in the
  background (`self._spec_executor`, `max_workers=1`) against the group
  text/context as it stands the moment each utterance is queued, tagged
  with `self._spec_key = (context_text, context_book)`.
- `_run_semantic()` reuses that result via `self._spec_future.result()`
  when `_spec_key` still matches what it's about to search for and the
  future is done; otherwise it falls back to the exact synchronous
  `self.semantic.search()` call as before. Strictly additive — the fallback
  guarantees identical behavior whenever the speculative result doesn't
  cleanly apply (group text changed since kickoff, still running, or the
  `immediate=True` operator-input path which never queues one), so this can
  only ever hide latency, never change what gets matched.
- Factored `_current_context_book()` out of `_run_semantic` so both the
  speculative kickoff and the real resolution compute the identical value.

**Verification**:
- `py_compile` clean on both files.
- `python -m app.evaluation.accuracy_eval` re-run: **identical to the
  existing baseline** (95.5% / 100.0% direct / 91.7% semantic / 33.3% FP /
  4.5% miss) — expected, since `app/evaluation/pipeline.py` is a standalone
  replica that doesn't use `HybridEngine`, so it can't exercise the
  speculative-search path directly, but confirms nothing else regressed.
- Ad-hoc script exercising the real `HybridEngine` directly (no mic/GUI):
  confirmed a phrase-map match (Romans 8:28) resolves correctly through the
  grace-period path, a second grouped utterance correctly re-finds the same
  verse but is suppressed by the existing anti-flicker "not higher than
  current on-screen confidence" guard (§8e item 3 — unaffected by this
  change), and a direct reference (John 3:16) still resolves synchronously
  and instantly.
- Ad-hoc script feeding synthetic audio blocks straight into
  `_audio_callback()` (same technique as §8f's own endpointer diagnostic —
  no real microphone/`sd.InputStream` involved): confirmed two
  back-to-back utterances dispatch through the pool without error, `stop()`
  drains/replaces the executor cleanly, and the replacement executor
  accepts new work afterward.
- **Not verified**: real live-audio timing improvement, and whether the
  speculative search actually finishes before the grace timer fires in
  practice under real CPU load — same category of gap noted after §8f,
  needs the user's own live testing to confirm the latency win is real
  rather than just theoretically sound.

---

## 11. Real-sermon evaluation, repetition-loop hallucination fix, rolling 3-sentence context

Prompted by a real transcript pasted from live testing (a ~20-minute sermon
recording) and two follow-up requests: "it's not detecting the messages
that well again," and a revised, more specific latency/context spec —
semantic latency should be **3s** (down from 4s), and *every* utterance
routed to semantic search should be judged against the last **three
sentences**, not just the previous-group blend from §10/§8f. Direct
reference stays at ~1s, unchanged, fed the current utterance only.

### Evaluation finding: a second, distinct Whisper hallucination mode

The pasted transcript surfaced a failure mode never seen or fixed before —
different from the silence-hallucination §8d/§8f already addressed. Real
segments of the transcript degenerated into a repeated single word dozens
to hundreds of times in a row: `"...like, hey, hey, hey, hey, hey, hey,
hey, ..."` (~150+ repeats), `"who has been who, who, who, who, who, ..."`
(~150+ repeats), and `"...you go la, la, la, la, la, ..."` (~90+ repeats).
This is a well-documented Whisper/CTranslate2 decode-loop failure, distinct
from silence-hallucination: the model gets stuck repeating a token it's
genuinely "confident" about, so `avg_logprob`/`no_speech_prob` — the two
gates already in `_transcribe_utterance` — don't catch it; both can look
perfectly healthy on a repeated token.

Traced into the installed `faster-whisper` (1.2.1) source directly
(`transcribe.py`) rather than assumed:
- `Segment.compression_ratio` exists and is computed for every segment, and
  `compression_ratio_threshold=2.4` was already being passed into
  `model.transcribe()` — but with `temperature` pinned to a single value
  `(0.0,)` (deliberately, per §8d — no fallback ladder), the library's own
  `for...else` fallback logic (`transcribe.py:1515-1528`) has nothing to
  fall back *to*: an over-threshold segment is logged at debug level
  internally and then returned completely unfiltered. This threshold was
  silently a no-op for our actual output.
- The same version exposes `repetition_penalty` and `no_repeat_ngram_size`
  as direct `transcribe()` parameters (both off by default:
  `repetition_penalty=1`, `no_repeat_ngram_size=0`), which operate on
  logits at decode time — they don't add the "sample a fluent fabrication"
  risk a temperature increase would (see §8d's reasoning for why that was
  rejected), because they're deterministic constraints, not a change to
  the sampling distribution.

### Fix — three independent layers, `app/asr/transcriber.py`

1. **`repetition_penalty=1.2`, `no_repeat_ngram_size=4`** (new `Config`
   fields, wired into the `model.transcribe()` call). `no_repeat_ngram_size=4`
   is a hard mathematical cap: once a 4-token sequence has occurred, decoding
   it again is blocked outright — for a single repeated word this caps it
   at exactly 4 consecutive occurrences, comfortably above genuine
   call-and-response repeats ("Amen, Amen, Amen" = 3) while making a
   100+ repeat run structurally impossible. `repetition_penalty` is a
   softer, complementary discouragement.
2. **`compression_ratio_threshold` promoted from a bare literal to a named
   `Config` field, and — critically — enforced ourselves** as a genuine
   post-decode gate in `_transcribe_utterance`'s segment-filtering loop
   (alongside the existing `avg_logprob`/`no_speech_prob` checks), since
   passing it into `transcribe()` alone was confirmed to do nothing given
   the pinned temperature. A segment whose own `compression_ratio` exceeds
   the threshold is dropped and logged, exactly like the existing gates.
3. **`_has_repetition_loop()` / text-level guard in `_is_bad_output()`** —
   a cheap, library-independent third layer: 5+ of the exact same word
   back-to-back anywhere in the final assembled text (after joining all of
   an utterance's segments) is rejected outright. Catches the failure mode
   even if a loop happens to straddle a segment boundary and dilute any
   single segment's own compression ratio below threshold.

No change to `endpoint_silence_ms`, `temperature` (still pinned), VAD
thresholds, or `initial_prompt` — this is additive, targeting a failure
mode those existing controls don't cover.

### Rolling 3-sentence semantic context, `app/retrieval/hybrid.py`

Replaced the §8f/§10 "previous finalized group + current group" blend with
an explicit, always-on rolling window:
- New `self._sentence_history: deque(maxlen=ROLLING_CONTEXT_SENTENCES)`
  (`ROLLING_CONTEXT_SENTENCES = 3`). `process()` appends every utterance
  it sees — direct reference, navigation, version switch, or semantic-bound,
  "all the time" — right at the top of the locked section, before any
  routing decision. This is a genuine behavior change from §8f/§10: context
  used to come only from utterances that specifically went down the
  semantic path; now the rolling window reflects the actual speech
  timeline regardless of type (e.g. "Turn to Romans chapter 8" — a direct
  reference — now still informs the next semantic utterance's context, on
  the reasoning that it's genuinely relevant context even though it
  resolved a different way).
- New `_rolling_context_text()` returns `" ".join(self._sentence_history)`
  — used both by the speculative kickoff in `_queue_semantic` and the real
  resolution in `_fire_semantic_group`/`_run_semantic` (§10's speculative-
  search reuse logic needed no further changes: it already compares
  computed context_text/context_book for an exact match before reusing a
  cached result, so it transparently works with the new context source).
- `_prev_semantic_text` (the old single-previous-group field) removed
  entirely, replaced by the rolling buffer.
- `_cancel_pending_semantic()` no longer resets any "previous text" state —
  deliberately: `_sentence_history` tracks the speech timeline, not the
  semantic-matching state machine, so a decisive fast-path action (e.g. a
  direct reference firing) shouldn't erase context a later semantic
  utterance would still want.
- Direct-reference extraction (step 3) is unchanged — still keyed to the
  current utterance alone, never blended, per the existing "stays fast,
  can't be re-triggered by an unrelated earlier mention" reasoning.
- `SEMANTIC_MAX_LATENCY`: **4.0 → 3.0**. `SEMANTIC_GRACE_PERIOD` (1.3s) is
  unchanged and still comfortably inside the tighter ceiling.

### Verification

- `py_compile` clean on both files.
- `python -m app.evaluation.accuracy_eval` re-run: **identical to the
  existing baseline** (95.5% / 100.0% direct / 91.7% semantic / 33.3% FP /
  4.5% miss) — again expected, since `pipeline.py` doesn't exercise
  `HybridEngine` directly.
- Ad-hoc script against the real `HybridEngine`: confirmed
  `SEMANTIC_MAX_LATENCY == 3.0` and `ROLLING_CONTEXT_SENTENCES == 3` at
  runtime; fed 3 utterances and confirmed `_sentence_history` holds all 3
  in order; fed a 4th and confirmed the oldest entry rolled off (`deque`
  maxlen behavior); confirmed semantic search still resolves correctly
  (Romans 8:28 via phrase_map) against the rolling-context text.
- Ad-hoc check of `_has_repetition_loop()` directly: a synthetic `"hey "
  * 20` string is correctly flagged as a repetition loop; `"Amen, Amen,
  Amen, hallelujah"` is correctly left alone.
- **Not verified**: whether the repetition-loop fix actually eliminates
  the failure mode against real audio (the pasted transcript was text, not
  re-run through the live pipeline with these changes) — same category of
  gap as §10, needs live/recorded-audio testing to confirm. If it recurs,
  the next lever is lowering `repetition_penalty`/`no_repeat_ngram_size`
  further or profiling what specific audio characteristic (crowd
  noise/singing/PA feedback) triggers it, since neither was captured
  alongside the transcript.

---

## 12. Split-reference bug: a direct reference broken across two utterances

Reported live: "Let's go to Leviticus 27" didn't show, and the system felt
slow generally. Traced with a direct test of `extract_reference()`
(`app/retrieval/reference_extractor.py`) rather than guessed at:

```
extract_reference("Let's go to Leviticus 27")   -> ('Leviticus', 27, 1)   # fine alone
extract_reference("Let's go to Leviticus")      -> None                  # book, no chapter
extract_reference("27")                         -> None                  # chapter, no book
```

**Root cause**: signal-driven ASR endpointing (`app/asr/transcriber.py`)
has no concept of "this is one spoken reference" — if the speaker pauses
between the book name and the chapter/verse number (natural cadence, e.g.
giving the congregation a moment to find the book), the two halves become
two separate utterances. Direct-reference extraction is deliberately kept
to the current utterance alone (§8f — for speed and to avoid stale
re-triggering), so **neither** utterance contains a complete pattern, both
return `None`, and the utterance falls through to the slow ~3s semantic
path — which then correctly finds no match, since "Let's go to Leviticus"
isn't a paraphrase of any verse. That's the same event the operator
experienced as two complaints ("didn't show" + "feels slow") — it's one
bug: a real reference silently missing the fast path and burning the full
semantic timeout for nothing.

**Fix — `app/retrieval/hybrid.py`**: new `_extract_split_reference(text)`,
tried only when the single-utterance `extract_reference(text)` in step 3
comes back empty:
- Only triggers when the current utterance is a short trailing fragment
  (≤4 words) — cheap to rule out on the common case, and keeps the change
  narrow.
- Combines it with the second-to-last entry in `self._sentence_history`
  (the rolling buffer from §11 — already tracks "all the time" regardless
  of match type, so the previous utterance is always available here) and
  re-runs `extract_reference()` on the joined text.
- Requires a real book alias to already be present in the previous
  utterance for `extract_reference()` to find anything at all in the
  combined text — this is what keeps it safe: a random short utterance
  ("Amen", "praise the Lord") can't accidentally revive an unrelated
  reference from earlier, because there's no book name for it to combine
  with in the first place. If a match is found, `_display()`'s existing
  duplicate-key skip already makes a stale re-match to something already
  on screen a harmless no-op, not a visible flicker.
- `source_text` on the resulting verse dict is the combined phrase (both
  utterances), not just the fragment — so the AI Detections panel's
  "heard: ..." line shows what was actually said.

### Verification

- `py_compile` clean.
- `python -m app.evaluation.accuracy_eval`: identical to baseline (95.5% /
  100.0% / 91.7% / 33.3% FP / 4.5% miss) — this harness's cases are all
  single-utterance by construction, so it can't exercise the split case,
  but confirms no regression to the unsplit path.
- Ad-hoc script against the real `HybridEngine`: confirmed the unsplit
  case ("Let's go to Leviticus 27" as one utterance) still resolves
  unchanged; confirmed a fresh-engine split case ("You know what, let's go
  to Leviticus" then "27" as two separate `process()` calls) now resolves
  to `Leviticus 27:1`, tagged `match_type="direct"`, with `source_text`
  showing the combined phrase. Two negative checks confirmed the fallback
  stays silent when the previous utterance has no book alias at all, and
  when the trailing utterance is longer than 4 words.
- **Not verified**: real-world impact on the "feels slow" complaint beyond
  this specific mechanism — if slowness persists on utterances that
  *aren't* a split reference, that needs profiling against real audio
  (raw Whisper decode time on this machine, whether `silence_threshold`/
  `endpoint_silence_ms` are well-calibrated for the actual recording
  environment), which text-only testing can't surface. CPU isn't an
  obvious bottleneck (12 logical cores available vs. `cpu_threads=8`).

---

## 13. NKJV — recognized, not imported (licensing, same pattern as NIV/NLT/GNT)

Asked to add NKJV. Researched before writing any code (per the existing
policy in item 8/§4.7): Thomas Nelson (© 1982) caps fair-use quotation at
**500 verses total** — this app bulk-caches the entire Bible (~31,100
verses) into SQLite + a FAISS index for offline search, ~60x over that
limit, with no indication their standard permissions page covers bulk
offline caching (anything outside their guidelines requires a written
request). Same shape of blocker as NIV (Biblica, live-fetch-only API),
NLT (Tyndale, same), and GNT (American Bible Society, same 500-verse cap).

Given the choice between skipping entirely, substituting a public-domain
modern-English alternative, or recognizing the phrase without data, the
last was chosen: `app/retrieval/version_detector.py`'s `VERSION_MAP`
gained `"new king james"` / `"new king james version"` / `"nkjv"` → `NKJV`,
mirroring how `GNT` is already handled. No data imported, no other files
touched. Verified against a running `HybridEngine`: `process("read in
NKJV", immediate=True)` correctly resolves through the existing
`_switch_version` "not installed" fallback (`status="version_error"`,
`"NKJV not installed — staying on KJV"`), instead of the phrase going
unrecognized as before.

---

## 14. The real root cause behind §12: bare numbers were being dropped before they ever reached the pipeline

§12 fixed *how* a split reference gets recombined, but two more real
examples ("Genesis 24, 16" → displayed as `24:1`; "Proverbs 31:10" →
displayed as `31`) showed §12's fallback wasn't actually firing in
practice. Explicit feedback alongside these reports: fixes should handle
speech generally, not the one example just reported — speech isn't
linear, so a fix scoped to "book name alone, then a number" (§12's
original framing) was itself too narrow a case of a bigger pattern.

Traced properly this time — tested `extract_reference()` directly first
(`"Genesis 24 16"`, `"Proverbs 31 10"`, comma/colon/space variants) and
confirmed the parser itself is correct; the bug wasn't there. Then tested
the full split scenario through the real `HybridEngine` end-to-end
(`process("Proverbs 31")` then `process("10")`) rather than reasoning
about it — and found the actual cause: **a bare verse number spoken as its
own utterance never reaches `hybrid.py` at all.** Two stacked filters,
both intended to reject meaningless noise, both too blunt for this domain:

1. **`app/asr/transcriber.py`: `Config.min_words = 2`** rejects *any*
   single-word utterance in `_is_bad_output()` — including "10", "16",
   "five." A verse number spoken alone after a natural pause (extremely
   common cadence: "Proverbs 31..." *pause* "...10") is exactly one word
   and was silently discarded before ever reaching `callback_fn`. This is
   the same root cause behind "the transcriber is missing some words" —
   the word very likely *was* heard; it was thrown away downstream of
   recognition, not missed by it.
2. **`app/retrieval/hybrid.py`: `process()`'s `len(text.strip()) < 3`**
   floor — redundant with #1 for live ASR, but would independently drop
   "10" (2 chars) even if it arrived via a different input path.

**Fix — general, not tied to any book/example:**
- `transcriber.py`: new `_is_bare_number(text)` — true if every token in
  the utterance is a digit string or a common number word (a small,
  self-contained set, not imported from `reference_extractor.py`, to keep
  the ASR layer decoupled from Bible-reference-parsing concerns). `_is_bad_output()`'s
  `min_words` check is skipped when this is true. A bare number is
  meaningful in this domain regardless of word count; the floor still
  applies to everything else (single stray words like "a," "the," "um"
  are still filtered, unchanged).
- `hybrid.py`: `process()`'s length floor changed from "≥3 characters" to
  "non-empty" — real noise is filtered upstream in the transcriber's own
  VAD/confidence/repetition gates; an extra blunt length check here had
  no real job left except dropping legitimate short numbers.

### Verification

- `py_compile` clean on both files.
- Direct check: `_is_bad_output("10")`, `_is_bad_output("16")`,
  `_is_bad_output("five")` all now `False`; `_is_bad_output("a")`,
  `_is_bad_output("the")` still `True` (genuine noise still filtered).
- End-to-end against the real `HybridEngine`: `process("Genesis 24")` then
  `process("16")` now correctly resolves to `Genesis 24:16`; a fresh
  engine with `process("Proverbs 31")` then `process("10")` now correctly
  resolves to `Proverbs 31:10`. Both via §12's fallback, which was already
  correct — it just never used to receive the second utterance.
- `python -m app.evaluation.accuracy_eval`: identical to baseline (95.5% /
  100.0% / 91.7% / 33.3% FP / 4.5% miss).

### Known related risk, not yet evidenced or fixed

Same general shape of problem could affect other multi-utterance spoken
patterns this session didn't specifically test — e.g. a verse *range*
split across a pause ("verse 1" ... "to 5"), or a navigation phrase split
mid-command. Not fixed speculatively here (no concrete report to test
against yet, and speculative fixes without a failing case to verify
against are exactly the kind of overfitting this section was trying to
move away from) — worth revisiting if a real example surfaces.

---

## 15. Semantic context: word-count-based rolling window instead of sentence-count-based

Follow-up: "let semantic take 13 words instead of sentences... it is not
being smart enough." The §11 rolling buffer fed semantic search the last
3 *utterances* — but utterance length in real speech varies enormously
(a two-word interjection vs. a long run-on sentence), so "3 utterances"
gave wildly inconsistent amounts of actual context from one moment to the
next: sometimes too little (three short utterances), sometimes too much
irrelevant material (three long ones). A fixed word count is consistent
regardless of how speech happens to be chunked.

**`app/retrieval/hybrid.py`**:
- New `ROLLING_CONTEXT_WORDS = 13` and `self._word_history: deque(maxlen=13)`.
  `process()` now does `self._word_history.extend(text.split())` for
  every utterance, in addition to (not instead of) the existing
  `self._sentence_history.append(text)` — the two buffers serve different
  consumers and were kept separate rather than collapsed into one:
  - `_word_history` (word granularity) → `_rolling_context_text()` →
    semantic search context (`_queue_semantic`'s speculative kickoff,
    `_run_semantic`/`_fire_semantic_group`'s real resolution).
  - `_sentence_history` (whole-utterance granularity, `ROLLING_CONTEXT_SENTENCES
    = 3`, unchanged) → `_extract_split_reference()` (§12/§14), which needs
    a complete previous utterance's text to recombine with the current
    one for `extract_reference()`, not a word-count window that could cut
    a reference in half.
- Being a `deque(maxlen=13)`, `_word_history` naturally spans utterance
  boundaries: if the current utterance alone is longer than 13 words, the
  window is just its own tail; if recent utterances were short, it
  reaches back across several of them to fill 13 words. No special-casing
  by utterance length needed — the fixed-size deque handles both ends of
  the range the same way.
- Neither buffer is cleared by `_cancel_pending_semantic()` — same
  reasoning as §11: they track the actual speech timeline, not the
  semantic-matching state machine.

### Verification

- `py_compile` clean.
- Ad-hoc script against the real `HybridEngine`: fed two utterances
  totaling more than 13 words, confirmed `_word_history` caps at exactly
  13 and correctly evicts the oldest words first, spanning both
  utterances (not aligned to either utterance's own boundary). Confirmed
  `_sentence_history`/`_extract_split_reference` (Proverbs 31 → 10) is
  unaffected by this change — still resolves correctly.
- `python -m app.evaluation.accuracy_eval`: identical to baseline (95.5% /
  100.0% / 91.7% / 33.3% FP / 4.5% miss).
- **Not verified**: whether 13 words is actually the right number for
  real sermon paraphrase matching — chosen because it's what was asked
  for, not derived from measurement. If semantic accuracy doesn't
  improve in real use, this is the first constant to revisit, ideally
  against real recorded-audio test cases rather than guessed at again.

---

## 16. Chapter-only references no longer blindly default to verse 1 when the utterance also paraphrases a specific verse

Reported live: "when he says there is something in a particular book like
in Romans 12 we should not see ourselves more highly than we ought to
think it should be able to give a verse by looking through that chapter
first" — a real sermon pattern: the preacher names a chapter and, in the
same breath, paraphrases a specific verse within it, without ever stating
the verse number.

**Root cause, confirmed by testing `extract_reference()` directly first**:
`extract_reference("... in Romans 12 we should not see ourselves more
highly ...")` returns `('Romans', 12, 1)` — the token scanner finds "Romans
12" anywhere in the sentence and instantly commits to chapter:1, silently
discarding the paraphrase that follows. Direct reference is step 3 in the
priority chain and returns immediately on a match, so semantic search
(step 5) never even runs for that utterance — the correct verse (Romans
12:3, "For I say... not to think of himself more highly than he ought to
think") was never in contention.

**Fix — general, based on whether a verse was actually spoken, not on
which book/chapter it happened to be:**

- **`app/retrieval/reference_extractor.py`**: `_extract_all_references_verbose()`
  (internal) now tracks, per match, whether the verse number was
  explicitly parsed or defaulted to 1 because none was found. Exposed via
  two new public functions — `extract_reference_verbose(text) ->
  (book, chapter, verse, verse_explicit)` and `extract_all_references_verbose`
  — while `extract_reference`/`extract_all_references` (used everywhere
  else in the codebase) keep their original 3-tuple signature unchanged,
  now implemented as thin wrappers that strip the 4th field. `_add_result`
  was changed to return whether it actually appended (vs. a no-op dupe/
  out-of-range skip), so the parallel `explicit_flags` list stays in
  lockstep with `results` without duplicating the entire function body.
- **`app/retrieval/semantic.py`**: new `SemanticEngine.search_within_chapter(query,
  book, chapter, threshold)` — scoped strictly to one book+chapter, reusing
  the phrase/event maps, `MIN_SEMANTIC_TOKENS` floor, and the same Rule
  1/Rule 2 display-threshold math as `search()`. Deliberately does **not**
  fall back to an unscoped full-corpus search the way `search()`'s
  `context_book` pass does — if nothing decent is found within the named
  chapter, the caller's own chapter:1 default is a safer fallback than a
  same-thread, no-more-informed corpus-wide guess. `search_top_k()` gained
  a matching optional `chapter` parameter. The shared Rule 1/Rule 2 check
  (previously duplicated verbatim twice inside `search()`) was factored
  into `_clears_display_threshold()`, used by all three call sites now.
- **`app/retrieval/reference_extractor.py`**: new `strip_reference_words(text,
  book)` — removes the spoken book name (any alias for it, not just the
  canonical form), bare numbers, and structural words ("chapter", "verse").
  **This turned out to be the load-bearing piece, caught by testing a
  negative case before trusting the positive one**: a first version of
  this fix passed the *reported* case (Romans 12 + paraphrase → correctly
  found 12:3) but a same-session regression check on a genuine bare
  chapter jump ("let's go to Romans 12") wrongly matched 12:21 instead of
  falling back to verse 1. Cause: every verse's own embedding text is
  built as `"{book} chapter {chapter} verse {verse}. {text}"` (see
  `build_embedding_text`) — leaving "Romans 12" *in* the query meant it
  trivially self-matched against that literal boilerplate in every
  verse's embedding for that chapter, regardless of real paraphrase
  content. Stripping the reference mention out of the query before the
  scoped search fixed it.
- **`app/retrieval/hybrid.py`**: step 3 now calls `extract_reference_verbose()`
  instead of `extract_reference()`. When `verse_explicit` is `False`,
  it calls `search_within_chapter(strip_reference_words(text, book), book,
  chapter)` before falling back to the verse-1 default. If the scoped
  search finds something, the result is tagged `match_type="semantic"`
  with the real `final_score` as confidence (honest to the operator — the
  chapter is certain, spoken explicitly, but the specific verse is a
  scored guess, not a deterministic lookup); if it finds nothing, the
  chapter:1 default is used exactly as before, tagged `match_type="direct"`
  at the usual fixed confidence. The split-reference fallback (§12/§14)
  is unaffected — it always resolves fragments too short to carry real
  paraphrase content, so it skips this check and trusts its result
  directly.

### Verification

- `py_compile` clean on all three files.
- `python -m app.retrieval.reference_extractor` (the module's own 29-case
  self-test suite, unaffected by the refactor): 28/29 pass — the one
  failure ("Matthew chapter six verse thirty three" → parses verse as 30,
  not 33) is a **pre-existing bug**, confirmed unrelated to this session's
  changes (nothing here touches word-number parsing), not fixed here —
  flagged for a future session.
- Three ad-hoc cases against the real `HybridEngine`: (A) the exact
  reported sentence now correctly resolves to Romans 12:3 (`match_type=
  "semantic"`, score 0.545); (B) a genuine bare chapter jump ("let's go to
  Romans 12") still correctly defaults to 12:1 (`match_type="direct"`) —
  this is the case that caught the embedding self-match bug above; (C) an
  explicit full reference ("Romans chapter 12 verse 3") still resolves
  instantly with no scoped search triggered at all.
- `python -m app.evaluation.accuracy_eval`: identical to baseline (95.5% /
  100.0% / 91.7% / 33.3% FP / 4.5% miss) — the harness's direct-reference
  test cases are all either fully explicit or genuine bare jumps, so this
  doesn't newly exercise the chapter-scoped path, but confirms no
  regression to either.
- **Not verified**: real-world impact — needs a broader set of "chapter
  named + verse paraphrased, no number given" test cases (ideally from
  real audio) to know how often this actually fires correctly versus
  missing or over-triggering in practice.

---

## 17. Fast preachers: worst-case detection latency bounded by a hard cap that was 10x the target

Reported: "when the preacher talks fast it seems you are not detecting
the scriptures fast enough and accurate enough."

**Investigated mechanically before changing anything** — fed synthetic
audio straight into `_audio_callback()` (same technique as the §8f
endpointer diagnostic and §14's verification) simulating continuous fast
speech with only ~100ms gaps between "sentences," well under
`endpoint_silence_ms` (350ms). Result: the segmenter **never finalized
naturally** — the only dispatch came from the `max_utterance_seconds`
hard cap, at the old default of 10.0s. Confirmed, not assumed: a fast,
low-pause preacher can genuinely go the *entire* 10 seconds without a
sustained-enough gap to trigger the normal endpoint-silence path, meaning
the hard cap — not the endpoint — becomes what actually governs dispatch
timing for that speaking style. At 10.0s, that's up to 10x the project's
own ~1s direct-reference / ~3s semantic latency targets, and the
resulting long clip is more likely to merge multiple distinct thoughts
together — plausibly explaining the accuracy complaint too: more context
for the decoder to drift over, and a direct-reference match that grabs
whichever reference appears first in the blob rather than the one
actually intended.

(A synthetic real-Whisper decode-time-vs-clip-length benchmark was also
attempted, to get real numbers rather than reason abstractly — discarded
as unreliable: `vad_filter=True` on pure random-noise "audio" behaved
inconsistently, presumably detecting no speech in some clips by chance
and skipping real decode work, since VAD's behavior on non-speech noise
isn't meaningful. Not citing those numbers since they weren't measuring
anything real.)

**Fix**: `app/asr/transcriber.py`'s `Config.max_utterance_seconds`
lowered **10.0 → 5.0** — roughly halves the worst-case bound while
staying well above typical single-sentence speaking duration, so
normal-paced speech isn't cut mid-thought any more often than before.
Re-ran the same synthetic continuous-speech scenario against the new
default and confirmed the forced cut now fires at 5.0s instead of 10.0s.

**Deliberately not changed**: `endpoint_silence_ms` — its own comment
already warns against lowering it without real testing ("too low starts
cutting speech off mid-word"), and this session had no real audio to
validate a lower value against. `beam_size` — tempting to raise back
toward its pre-tuning value of 5 given the accuracy half of the
complaint, but there's no specific evidence beam width (as opposed to
utterance length/merging) is the actual bottleneck here, and raising it
would slow down every decode, fast and slow speech alike, for an
untargeted gain.

### Verification

- `py_compile` clean.
- Synthetic mechanical check (not real audio): confirmed the previous
  10.0s cap-triggering behavior first (establishing the bug is real), then
  confirmed the new 5.0s default triggers a forced cut at 5.0s in the same
  continuous-speech scenario.
- `python -m app.evaluation.accuracy_eval`: identical to baseline — expected,
  since this harness runs text through the pipeline directly and never
  exercises ASR timing/segmentation at all.
- **Not verified, and can't be from this session**: whether 5.0s is
  actually the right number, and whether this fixes the reported
  complaint in practice. This needs the user's own testing against a real
  fast preacher's audio — the single most-repeated gap across this
  entire session's fixes is the lack of real recorded sermon audio to
  validate ASR-layer changes against (§6/item 8 in `SYSTEM_DOCUMENTATION.md`,
  unresolved since it was first flagged). If detection is still slow or
  inaccurate for fast speech after this, the next step should be building
  a small set of real fast-speech audio test cases before tuning further
  by guesswork.

---

## 18. A real threshold bug: hybrid.py was silently re-rejecting matches semantic.py itself had already accepted

Reported: "missing correct matches (no match found)" — a paraphrase the
preacher gives clearly enough that a verse should be found, isn't.

**Root cause, found via the exact known miss this session's own
evaluation harness had been reporting on every single run** (`semantic-12`:
"nothing can separate us from the love of..." → expected Romans 8:35, got
no match, visible in every accuracy_eval table from §10 onward without
anyone chasing it down). Tested `SemanticEngine.search()` directly rather
than guessing: it correctly finds Romans 8:35 — `similarity=0.5554,
lexical_score=0.45, final_score=0.529` — which clears its own Rule 1
(`final >= 0.50 AND sim >= 0.45 AND lex >= 0.05`, see `semantic.py`'s
module docstring) with room to spare. `hybrid.py`'s `_run_semantic` then
re-checked the returned candidate against `SEMANTIC_CONFIDENCE = 0.53` —
a *second*, independently-tuned threshold, numerically close to but
higher than semantic.py's own `DISPLAY_THRESHOLD = 0.50` — and
`0.529 < 0.53` by a hair, so a candidate semantic.py itself had already
vetted and accepted was silently thrown away one layer up.

This is a general bug, not specific to Romans 8:35: any candidate with
`final_score` in `[0.50, 0.53)` — a real, non-trivial band — would clear
`semantic.py`'s own bar and then get discarded by `hybrid.py`'s
redundant, misaligned re-check. No comment anywhere documented *why*
hybrid.py's number needed to be higher than semantic.py's own threshold;
it reads like two constants tuned independently in two different files
that happened to drift apart, not a deliberate design decision.

**Fix**: `SEMANTIC_CONFIDENCE` in `hybrid.py` changed **0.53 → 0.50**,
matching `semantic.py`'s own `DISPLAY_THRESHOLD` exactly, removing the
redundant stricter re-check. `SEMANTIC_CONFIDENCE_HI` (0.72, the
"leaving a locked-in book" bar — a genuinely different concept with no
equivalent in semantic.py to align against) was left untouched.

### Verification

- `py_compile` clean.
- `python -m app.evaluation.accuracy_eval`, full before/after comparison:

  | Metric | Before | After |
  |---|---|---|
  | Exact-match accuracy | 95.5% | **100.0%** |
  | Direct-reference accuracy | 100.0% | 100.0% (unaffected, as expected) |
  | Semantic-match accuracy | 91.7% | **100.0%** |
  | No-match rate (missed matches) | 4.5% | **0.0%** |
  | False-positive rate | 33.3% | 33.3% (**unchanged**) |

  The false-positive rate not moving at all is the important part — it's
  the same exact 3 false positives as every prior run this session, not a
  new one introduced by loosening the threshold. That's strong evidence
  this was a pure bug (recall lost for zero precision benefit), not a
  precision/recall trade-off masquerading as a bug. If a future false
  positive does turn up near this threshold, this is the first place to
  look, but there's no evidence of that yet.
- **This changes the evaluation baseline going forward.** Every
  "identical to baseline" verification note in §10 through §17 above
  refers to the *old* 95.5%/91.7%/33.3%FP/4.5%-miss numbers, current as
  of when each of those sessions ran — accurate history, not stale
  errors. From this point on, **100% / 100% / 33.3% FP / 0% miss** is the
  number to compare future changes against.

---

## 19. Visibility: the 13-word rolling context (§15) was invisible during live testing

Asked directly: "does it hear the 13 words" — while testing live. It
does, but there was no way to actually *see* that during a real session:
`context_text` (the string §15's `_word_history` builds and passes to
`self.semantic.search()`) was never logged at a level the live app
actually shows — only at `DEBUG`, and only on the no-match branch. An
operator watching the console during a real test had no way to confirm
the rolling buffer was doing anything.

**Fix**: one `log.info()` line added at the top of `_run_semantic()`,
before the search runs: `"Semantic context (N/13 words): '...'"` — shows
the actual word count against the cap and the exact text being searched,
every time semantic search actually runs (fast-path direct/nav/version
hits never reach this method, so it doesn't spam the log on every
utterance — only on the ones that matter for this).

### Verification

- `py_compile` clean.
- Direct test against the real `HybridEngine`: fed two utterances
  totaling more than 13 words, confirmed the log line reports `13/13
  words` and the printed text correctly spans across both utterances
  (starts mid-way through the second one, since the buffer evicted the
  earlier words from the first) — matches §15's own verification, now
  visible in real time instead of only provable by reading `_word_history`
  directly in a script.
- `python -m app.evaluation.accuracy_eval`: identical to the current
  baseline (100.0% / 100.0% / 33.3% FP / 0.0% miss) — log-only change, no
  logic touched.

---

## 20. Progress-report session: live bug hunting, the §15 rolling window removed for real, an actual architecture fix, and two new test sets

A long session driving the app live (not just reading code) to prepare a
progress presentation. Ground rule throughout: every claim in the resulting
report had to trace back to a command actually run this session, not an
assumption — several findings below only surfaced because of that
discipline.

### 20a. Accuracy gap closed on the existing starter set

`starter_testset.csv` had grown to 32 cases (a 17-word full-sentence
paraphrase, `semantic-13`, added since §19) and was sitting at 91.3%
(21/23 matchable), two known recall misses. Curated two phrase-map entries
("move a mountain" → Matthew 17:20, "nothing can separate us... love of
god" → Romans 8:35, both independently verified against the real KJV text
first) → **100.0% (23/23)**.

### 20b. Live-only bug: rolling word history never cleared, diluting later utterances

Reproduced by running several utterances back-to-back on one shared
`HybridEngine` (not a fresh engine per case, unlike the eval harness) — an
unrelated filler utterance right after an unresolved "law"-themed one
picked up a "law"-related verse at 53% confidence, captioned only with the
filler's own few words. Root cause: `_word_history` (§15) persisted
indefinitely except for the fixed `maxlen=13` eviction — a leftover word
from an already-decided *or already-failed* group could silently ride into
a later, unrelated utterance's score, and the "heard" caption only ever
showed the newest fragment, not the actual (wider) text that had been
searched. First patched narrowly (clear `_word_history` on every resolve);
**then, per explicit follow-up request ("the 13 words thing is affecting
the way the retrieval works, let's rid it"), removed the rolling window
entirely** — semantic search is now scoped strictly to
`_pending_group_texts`, the current still-open grace-period group, never
anything from before it. `_run_semantic` collapsed from two separate
strings (`context_text` for searching, `source_text` for display) to one —
what gets searched and what's shown as "heard" are the same string by
construction, closing the misattribution bug at the root instead of
patching around it. `ROLLING_CONTEXT_WORDS`, `_word_history`,
`_rolling_context_text()` all removed; `search()`'s and
`search_within_chapter()`'s now-dead `threshold` parameters removed too
(nothing external ever passed one).

**Verification**: reproduced the exact contamination scenario (unrelated
filler right after an unresolved utterance) and confirmed it now correctly
returns no match. Re-ran all direct-reference and semantic starter cases
back-to-back on one shared engine — no regressions. `accuracy_eval.py`
unaffected (100.0%/100.0%/22.2% FP unchanged), as expected since
`pipeline.py` is stateless and never exercised this path either way.

### 20c. Live-only bug, found but deliberately not fixed: `SEMANTIC_CONFIDENCE_HI` blocks legitimate topic changes

Same back-to-back methodology surfaced a second, real issue: once
`verse_tracking` locks onto a book, a semantic match to a *different* book
needs 0.72 confidence, not the normal 0.50 — by design (§8d), to resist
display jitter. Two real, correctly-ranked-#1 paraphrases were blocked by
it (Deuteronomy 31:6 at 0.542, Philippians 4:19 at 0.549). **Not retuned**
— `pipeline.py` is stateless and can't validate a change to session-state
behavior, and a same-night threshold change with no way to catch a
regression is exactly the kind of risk this session was trying to avoid.
Documented as open, with the reason, rather than either ignored or
hastily patched.

### 20d. Offline guarantee was backwards

`semantic.py` imported `sentence_transformers` two lines *before* setting
its own offline env flags — exactly backwards from its own comment ("force
offline mode before any HuggingFace import") — and never set
`HF_HUB_OFFLINE`, the flag `huggingface_hub` itself actually checks
(`TRANSFORMERS_OFFLINE` alone doesn't stop it). At a venue with genuinely
no internet, this is a silent hang trying to reach huggingface.co before
falling back to cache, not an instant local load. Fixed: reordered, added
`HF_HUB_OFFLINE` + `HF_HUB_DISABLE_TELEMETRY`. **Verified for real, not
just trusted**: monkeypatched `socket.socket.connect`/`create_connection`
to raise on any call, then loaded the full model stack (`SemanticEngine` +
`BibleAITranscriber`) under that block — zero connection attempts, full
successful load.

### 20e. ASR model swap, then a user-directed revert

`base.en` (74M, the smallest English-only Whisper tier) was the active
default despite `distil-small.en` (317MB) sitting fully downloaded and
unused — the model-path loader only ever checked
`models/faster-whisper/<size>`, one directory level away from where that
model actually lives. Fixed the path resolution to fall back to
`models/<size>`, and switched the default to `distil-small.en` — confirmed
it loads and decodes a synthetic 4s clip in 0.7s (0.17× real-time) before
trusting it. **Later reverted by direct edit** back to `base.en`, with
`endpoint_silence_ms` tightened 450→350ms and `beam_size` lowered 5→3 — a
deliberate speed-over-accuracy tradeoff, not undone or second-guessed here.
The path-resolution fix stays regardless — it's what makes `distil-small.en`
reachable by name whenever that tradeoff should flip back.

### 20f. Two new, permanent test sets — and a real architecture fix found through them

**`semantic_stress_testset.csv`** (55 cases, user-authored real sermon-style
paraphrases): 60.0% on first run. Every failure traced to a specific,
verified root cause before deciding what to do about it — mostly a
too-blunt `MIN_SEMANTIC_TOKENS` floor blocking short-but-clear phrases
despite good underlying scores, or near-misses under the display threshold
by as little as 0.003. Curated 15 phrase-map entries, each individually
checked against real KJV text → **81.8%**. Remaining 10 failures
categorized rather than left unexplained: 5 defensible (genuine parallel
verses — e.g. "the soul that sinneth, it shall die" is *literally repeated*
within Ezekiel 18 itself), 2 matching the test author's own "too
vague"/"vague" flags, 2 real recall gaps, 1 weak paraphrase on the input
side.

Explicit follow-up direction: *"avoid adding to the phrase map, I think the
system should be smart enough — where can training come in."* Built a
second, **Claude-authored, zero-curation** set —
**`generalization_testset.csv`** (31 fresh cases, book names/verse numbers
independently checked against this DB's own numbering, which turned out to
diverge from standard KJV numbering in two places — "many are called, but
few are chosen" sits at Matthew 22:**13** here, not 22:14; "the spirit is
willing" at Matthew 26:**40**, not 26:41). First run: 74.2%, no phrase-map
additions attempted.

Instead, traced two of the failures ("let him kiss me with the kisses of
his mouth", "eat drink and be merry" — both **verbatim quotes**,
`lexical_score == 1.0`) to a genuine logic bug: `_clears_display_threshold`
required `similarity >= 0.45` as an independent condition even when the
lexical evidence alone was overwhelming (both cases: sim landed a few
thousandths under 0.45, final score well above 0.50). A second, redundant
copy of the same filter inside `search_top_k()` was silently discarding the
candidate even earlier, before its lexical score was computed at all.
**Fixed both** — the display gate now accepts `sim >= 0.45 OR lex >= 0.95`,
and `search()`/`search_within_chapter()` no longer pre-filter `search_top_k`
candidates on raw similarity (costs nothing extra: the FAISS index is exact
and already returns the same top-K regardless of any threshold argument).

**Verification**: re-ran all three test sets. Starter: unchanged, 100.0%,
same 2 false positives (22.2%) — confirms zero new false positives from
loosening the gate. Stress: unchanged, 81.8% (none of its remaining
failures were verbatim-quote cases). Generalization: **74.2% → 77.4%**,
from one logic change, zero curation, on content the fix wasn't targeted
at — the clearest evidence this session that a real architecture fix
generalizes further than another curated phrase-map entry ever could.

### 20g. Transcriber accuracy — what's verifiable without real audio

No recorded sermon audio exists in the repo, so word-error-rate could not
be honestly measured. What *is* testable without it: fed pure digital
silence, quiet sub-gate noise, and louder noise that forces a real decode
pass, directly into `_transcribe_utterance()` — all three correctly
produced nothing, no fabricated sentence, consistent with the
logprob/no-speech/compression-ratio/repetition-loop gates (§8d/§11)
actually working under the current `base.en` config. A genuine WER
benchmark remains open — needs real or realistic recorded sermon audio with
hand-transcribed ground truth, which doesn't exist yet.

### 20h. Documentation

`SYSTEM_DOCUMENTATION.md` fully regenerated (was dated 2026-07-14 and had
drifted on several load-bearing facts — the old rolling-context window,
`SEMANTIC_CONFIDENCE=0.53`, `SEMANTIC_CONFIDENCE_HI=0.60`, `base.en` as the
only model on disk — all superseded by this session). `TRANSCRIPTION_SETUP.md`
confirmed still stale (documents a `Transcriber`/`TranscriberConfig` API
that no longer exists anywhere in the code) — flagged for deletion/rewrite,
not edited here since it wasn't in scope tonight.

### Not verified

- Whether `distil-small.en` would measurably improve real transcription
  quality versus the currently-active `base.en` + tightened endpointing —
  needs real audio, same gap as always.
- Whether `SEMANTIC_CONFIDENCE_HI` should actually be lowered — needs a
  session-aware evaluation harness built first (see §20c), not guessed at.
- The 2 remaining real recall gaps and 1 weak-paraphrase case in the
  generalization set — root-caused but not fixed; candidates for the
  training roadmap (bi-encoder fine-tuning) rather than more curation.

---

## 21. Cloud-first ASR, hallucination fixes measured against real audio, two silent-navigation bugs, and the Twi objective (§4) actually started

### 21a. ASR backend: Groq cloud-first with automatic local fallback/recovery

`Config.backend="auto"` already preferred Groq's cloud Whisper when
`GROQ_API_KEY` was set, but a failed cloud request permanently switched the
session to local for the rest of the run (a one-way circuit breaker) —
requested behavior was "work online, only fall back locally when there's no
internet," implying recovery, not a permanent downgrade. Rebuilt as a
half-open circuit breaker: `_fallback_to_local()` drops to local
immediately on a cloud failure, but `_should_probe_cloud()` /
`_recover_to_cloud()` retry cloud in the background every
`cloud_retry_interval_s` (30s default) and switch back automatically the
moment a probe succeeds — no restart needed. `_dispatch_worker_count()`
sizes the decode thread pool per-backend (4 for cloud, since it's I/O-bound
waiting on Groq's HTTP round-trip; 2 for local, CPU-bound) and
`_resize_executor_for_backend()` keeps the pool correctly sized across a
runtime cloud↔local flip, not just at startup.

### 21b. Hallucination on real sermon audio — root-caused with actual before/after transcripts, not synthetic silence

The operator supplied a real ~20-line sermon transcript run through the
live pipeline (Groq cloud backend) alongside the expected ground-truth
text — the first real accuracy signal this project has had (§20g/§11
could only test synthetic silence/noise). Two distinct, separable failure
modes found:

1. **`Config.initial_prompt` echo.** Even the already-shrunk bare-word-list
   prompt (5 book names, down from ~18) got echoed back verbatim as its own
   "transcription" on real pauses — 4 of ~19 lines in one test were the
   prompt sentence itself, replacing real content, plus 2 more garbled
   paraphrases of it. A direct A/B test (same clip, prompt vs. no prompt)
   settled it: with the prompt, ~a third of the transcript was fabricated;
   without it, zero fabricated content — every deviation from ground truth
   was an ordinary mishearing. **`initial_prompt` now defaults to `None`**
   (was previously always-on); kept as an opt-in field, not deleted,
   documented with this exact A/B evidence in its own comment.
2. **Ambient noise reaching the decoder at all.** The segmenter's
   `silence_threshold` is a cheap RMS/volume gate — continuous room tone,
   HVAC hum, or breath is often louder than that threshold without being
   speech, so it still got endpointed into an "utterance" and sent to
   Whisper, which hallucinated fluent fabricated text instead of producing
   nothing (the exact failure mode the module's own docstring already
   named). Added `_has_speech()` — a proper Silero VAD pass via
   faster-whisper's bundled model, run before **either** backend, not just
   local's own `vad_filter=True` (which never covered the cloud path at
   all). Smoke-tested against synthetic silence and low-level noise: both
   correctly return zero speech timestamps.

Also fixed while in there: **the `initial_prompt` field itself was broken**
— a prior edit that commented out its content left `initial_prompt: str =
()`, an empty *tuple* silently mismatching the declared `str` type,
which would have been passed straight into `faster_whisper`/Groq as a
malformed value. Added `_is_prompt_echo()` as defense-in-depth in
`_passes_segment_gates` (whole-prompt containment check, not fuzzy word
overlap — confirmed via smoke test it doesn't false-positive on a
legitimate line like "Turn with me to the book of Acts") for anyone who
opts back into a prompt later. Eased the post-decode confidence gates
(`avg_logprob` -1.0→-1.2, `no_speech_prob` 0.70→0.80, partial rollback
toward their pre-§11 values) now that VAD catches the main hallucination
risk upstream — the tighter gates were independently confirmed to be
dropping real, merely-quiet speech (whole sentences missing with no
garbled trace, e.g. the Romans 1:16 quote and an entire paragraph vanished
from the first real transcript tested).

**Measured result** (word-level Levenshtein diff, reference vs. hypothesis,
on the operator's second real-audio test after all three fixes): **3.47%
WER** (16 edits / 461 reference words — 9 substitutions, 2 deletions, 5
insertions), **zero hallucinated content** — every remaining error is an
ordinary mishearing or a segmentation-boundary artifact (a word doubled or
dropped right at an utterance cut). This is the first genuine WER number
this project has ever had (§20g/§11's synthetic-audio testing couldn't
produce one) and confirms the fix, not just the theory behind it.

### 21c. Two silent no-op bugs: voice commands that did nothing from the app's normal starting state

Both traced to the same root cause pattern, found back-to-back:

- **Navigation** (`_navigate` in `hybrid.py`) refused to act (except STOP)
  whenever `self._live_position` was `None` — silently, with only an
  internal log line, no operator-facing feedback. `_live_position` is only
  set once a verse is explicitly pushed **Live**, and `main_ui.py`'s `Go
  Live` toggle defaults OFF with every verse landing in Preview first — so
  "next verse" / "previous verse" / "repeat that" / etc. never worked at
  all until the operator had gone Live at least once, which is not the
  app's normal flow. Same bug independently found in the verse-jump step
  ("verse 20").
- **Version switch** (`_switch_version`) had the identical gate on its own
  "redisplay the current verse in the new version" step — switching Bible
  version silently left whatever was in Preview showing the *old*
  version's text.

Fixed all three the same way: prefer `_live_position` when something
actually is live (unchanged, still the correct anchor for what's on the
physical projector screen), fall back to `session.current_book/chapter/
verse` — updated by every `_display()` call regardless of Live/Preview —
when nothing's live yet. Functional-tested against the real Bible DB (not
just unit-level): a verse shown via direct reference but never pushed
Live now correctly advances on "next verse" and correctly re-renders in
the new version on a version switch. `__init__`'s comment on
`_live_position` rewritten to state the fallback explicitly, since the
old comment asserted the now-fixed behavior as the intended design.

### 21d–g. Objective #4 (Twi) — actually started this session, not just scoped

Previous documentation (§4/SYSTEM_DOCUMENTATION.md §5.1) listed this as
"not started, largest open gap." Concrete progress, in the order it was
built:

**Bible text** — a complete Twi Bible (`data/tw_asante.json`, 66 books,
31,104 verses, sourced by the operator's project partner) added as a
third installed version, `TWI`, in `db.py`'s `VERSIONS` registry.
Validated before trusting it: correct book-name convention (matches the
pipeline's existing canonical English `book` field, e.g. `"Genesis"`,
`"John"` — not the Twi Bible's own possible internal naming), zero empty
verse strings, Genesis 1:1 and John 3:16 read as recognizable, correct
Twi. `bible.db` rebuilt (93,308 verses total across KJV/BBE/TWI now) and
verified via the actual live functions (`db_list_versions`/`db_get_verse`
in `hybrid.py`, not `db.py`'s own less-used helpers).

**Voice/text vocabulary — book names, navigation, version-switch phrases**
— `reference_extractor.py`'s `_BOOK_ALIASES` and `version_detector.py`'s
`NAV_MAP`/`VERSION_MAP` were flat English-only dicts with no i18n hook.
Turned out not to need one: they're just `phrase → canonical-value`
lookups, so Twi is "more entries in the same dict," not new matching
logic — a preacher can code-switch mid-sentence and the matcher doesn't
care which language a given key came from. Refactored into a
per-language-file pattern (`aliases_en.py`, `aliases_twi.py`, merged at
import time) before adding a third language becomes a repeat of this same
exercise. Confidence flagged per block, not uniformly: NT book names
(Mateo, Marko, Luka, Yohane, Roma, ...) and most OT proper nouns are
reasonably confident transliterations; the Pentateuch-numbering question
and several native-word OT titles (Psalms/Proverbs/Judges/Chronicles) are
marked `⚠ VERIFY` — genuinely uncertain, not silently asserted as fact.
`NAV_PHRASES`/`VERSION_PHRASES` (Twi nav commands, "switch to Twi bible")
are lower-confidence still (compositional phrases, not proper nouns) and
explicitly flagged as an unreviewed draft. All new entries checked for
key collisions against the existing English tables (none found) and
smoke-tested through `extract_reference`/`detect_navigation`/
`detect_version` directly, including a real false-positive check ("the
Asante people gathered" correctly does NOT trigger a version switch).

**Language gating** — initial testing surfaced a real design problem: a
Twi book name resolved correctly regardless of which Bible version was
currently active, so "Yohane 3:16" while on KJV displayed KJV's *English*
text, which is confusing and not what an operator means by "the Twi
version." Fixed with a `_LangBundle` system in `reference_extractor.py` —
book-name/number/structural-word matching gets scoped to an
`allowed_languages` set (`{"en"}` normally, `{"en", "twi"}` only while
`session.active_version == "TWI"`, computed once via a new
`HybridEngine._allowed_languages()` helper and threaded through both the
voice pipeline (`hybrid.py`, two call sites) and the manual search box
(`main_ui.py`'s `_book_mode_lookup`, previously not gated at all). Bundles
are cached per distinct language-set rather than rebuilt per call.
English stays always-allowed regardless of active version (no ambiguity
about "which English version" the way there could be about which
non-English language is meant). Verified end-to-end against the real
pipeline: Twi reference blocked on KJV, works after switching to TWI,
English references unaffected either way.

Same pass added support for a spoken pattern the operator supplied
directly (not guessed): `"Yohane ti 3 nkyekyɛmu 16"` — Twi's own
chapter/verse marker words ("ti"/"nkyekyɛmu", not "chapter"/"verse").
Implemented as a `STRUCTURAL_WORDS` table (mirroring the pattern above)
that normalizes either language's marker words to the literal English
ones before the *existing* regex patterns run — needed zero new patterns,
same mechanism English's "chapters"→"chapter" synonym already used.

**A real, unrelated bug found while testing Twi topic search**:
`semantic.py`'s `clean_text()` used an ASCII-only regex
(`[^a-z0-9\s:']`) that silently destroyed any non-English verse text —
Twi's ɛ/ɔ characters got replaced with spaces, splitting single words
into garbled fragments (`"Ahyɛaseɛ"` → `"ahy"` + `"ase"`) and reducing
some words to nothing (`"bɔɔ"` → `"b"`, then dropped by the length-1
filter). This corrupted the lexical half of the hybrid search score for
*every* verse in a non-English version, not just ones actually queried.
Fixed by switching to `\w` (Unicode-aware in Python 3's `re` module by
default) instead of the ASCII-only class — confirmed byte-for-byte
identical output on English, confirmed Twi words survive intact after the
fix. Digging further after the fix revealed the **larger, still-open**
reason Twi topic/paraphrase search doesn't yet match KJV's quality: the
embedding model (`all-MiniLM-L6-v2`) is English-only and produces
essentially noisy similarity scores on Twi text (an exact phrase lifted
straight from TWI's own John 3:16 scored *lower* semantically than
several unrelated verses) — fixing that needs a multilingual
sentence-transformer swap and a full reindex of all three versions,
explicitly deferred by the operator ("we will do semantic later") rather
than attempted this session. Direct verse-reference lookup (the more
commonly used path) was unaffected by this gap and confirmed working.

### 21h. Twi ASR — a real model found, converted, and load-tested (transcription quality itself not yet verified)

Whisper (local or Groq) has no genuine Akan/Twi support — confirmed via
web research rather than assumed, since this is exactly the kind of claim
worth checking rather than reasoning from training-data recall alone.
Found `GiftMark/akan-whisper-model` (Hugging Face) — a real, public
`openai/whisper-small` fine-tune for Akan/Twi, 16kHz input matching this
project's existing pipeline exactly. Downloaded and converted to
CTranslate2 int8 format (`models/faster-whisper/akan-whisper/`) via
`ctranslate2.converters.transformers.TransformersConverter` (the CLI
wrapper `ct2-transformers-converter.exe` failed silently with no
diagnostic output in this environment; calling the same converter class
directly from Python worked and gave visibility into progress). **Needed
zero code changes to `transcriber.py`** — `Config.model_size` already
generically loads whatever folder it names via the existing
`_init_local_backend`, so this is a drop-in alongside `base.en`/
`tiny.en`/etc., usable today via
`python -m app.asr.transcriber --model akan-whisper --backend local`.

One real integration detail, found by reading the model's
`generation_config.json` rather than assumed: it was fine-tuned by
repurposing Whisper's **English** language slot (`<|en|>`, token 50259)
to mean "decode Twi" rather than adding a genuine Akan token — so it must
be invoked with `language="en"` (already `Config`'s existing default,
nothing to change) or the fine-tuning is defeated. Verified empirically,
not just from the config: on raw silence with VAD disabled, it produced
Twi-script text (`"Mɛkyɛn."`), not English, confirming `language="en"`
genuinely triggers Twi decoding. Also confirmed the full existing
anti-hallucination stack (§21b's VAD gate, faster-whisper's own internal
VAD, the post-decode confidence gates) applies automatically and
correctly suppresses silence with this model too, since none of that
logic is Whisper-checkpoint-specific.

**Not verified**: real transcription accuracy on actual spoken Twi. The
model card publishes no WER and there's no way to test that without real
Twi audio and a Twi speaker to judge the output against — genuinely
unknown whether this model is good enough to use live, only that it
mechanically works and produces plausible-looking Akan script.
`TRANSCRIPTION_SETUP.md` updated with setup/run instructions and this
exact caveat.

### Not verified (this session)

- Twi ASR transcription quality/WER — needs real Twi audio, which doesn't
  exist in this environment (§21h).
- The `⚠ VERIFY`-flagged Twi book names (Pentateuch numbering convention,
  several native-word OT titles) and all Twi `NAV_PHRASES`/
  `VERSION_PHRASES` — drafts pending a native Twi speaker's review, not
  confirmed correct.
- Whether Twi navigation commands (`NAV_MAP`) should be gated by active
  version the same way book names now are (§21d-g only covers reference
  extraction) — flagged to the operator, not yet decided.
- Semantic/topic search parity for Twi — root cause identified (English-
  only embedding model), fix scoped (multilingual model swap + full
  reindex), explicitly deferred by the operator.
