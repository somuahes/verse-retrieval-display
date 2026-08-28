# Bible AI — Full System Documentation

Regenerated from a direct read-through and live testing of the codebase on
**2026-08-01**, superseding the 2026-07-15 version in full. Every claim
below was either read directly from the current source or verified by
actually running it this session (not assumed from the prior document,
which had drifted out of date on several load-bearing facts — the ASR
backend, the hallucination-mitigation stack, two silent voice-command
bugs, and the state of the Twi objective all changed since it was
written). Historical build narrative — what was tried, reverted, and why —
lives in `PROGRESS.md`; this file is a snapshot of what the code does
*right now*.

**Addendum, 2026-08-07** (UI layer only — ASR/retrieval sections below are
unchanged from 2026-08-01 and were not re-verified this pass): §3.4 and §2
updated for four new Operator Panel features (Browse, Session History,
Queue reorder/save-load, Save-Image) and a `tests/` integration suite —
see `PROGRESS.md` §22 for the full narrative.

**Addendum, 2026-08-23** (diagnostic only, no code changed): §5 gained a
new known issue — an external mixer board connected as the audio input
source is wired in via analog 3.5mm (not USB; no new device is enumerated,
Windows just relabels the existing onboard jack `Microphone` → `aux`), and
currently produces no usable signal (near-silent capture test). See
`PROGRESS.md` §25.

**Addendum, 2026-08-28** (§3.2 retrieval logic only — no ASR/UI change):
`hybrid.py`'s mid-phrase-pause split-utterance combining (previously
direct-reference-only) now also covers navigation and verse-jump
commands, and a bare book mention with no resolvable verse ("in the book
of Ezekiel") now scopes the next semantic search to that book for 90s
instead of carrying no search weight at all. `semantic.py`'s short-query
display bar (0.62→0.75) and verbatim-substring-match floor (10→20 chars)
were both raised after confirmed live false positives. All changes
verified against the real engine (42/42 pytest, `accuracy_eval` starter
set unchanged at 87.0%/0% false-positive, stress/generalization sets
unchanged within noise of the pre-existing baseline) — see `PROGRESS.md`
§26 for the full narrative.

---

## 1. What the system is

A **live sermon-companion app**: it listens to a preacher through a
microphone, transcribes speech (cloud-first via Groq, with automatic local
fallback), detects when a Bible verse is being read or paraphrased, and
pushes that verse to a full-screen projector display for the congregation —
with a human operator sitting between detection and the screen at all
times. As of this session it also supports Twi (Akan): a Twi Bible
version, Twi voice commands/references (gated so they only apply while the
Twi version is active), and an experimental Twi ASR model — see §3.1 and
§3.2.

**One canonical entry point**:

```powershell
.\venv311\Scripts\python.exe app\ui\main_ui.py
```

Root `main.py` + `app/ui/operator_panel.py` is a separate, simpler, earlier
implementation that nothing else in the codebase imports — legacy,
unmaintained. **Recommended before handoff/submission**: delete it or move
it to an `archive/` folder (still open — not touched this session).

---

## 2. Architecture

```
Microphone ──▶ BibleAITranscriber (Groq cloud primary, local fallback)
                        │  (signal-driven, silence-endpointed utterances)
                        │  (Silero VAD pre-filter before either backend)
                        ▼
              HybridEngine.process(text)
                        │
   ┌────────────────────┼──────────────────────────────────────────┐
   │ 1. Version change   │ "read in BBE" / "switch to Twi bible"     │
   │ 2. Navigation       │ "next verse" / "stop display" / etc.      │
   │ 3. Direct reference │ "John 3:16" / "Yohane 3:16" (TWI-gated)   │
   │ 4. Verse jump       │ "verse 20" (needs existing position)      │
   │ 5. Semantic search  │ paraphrase/quote → semantic.py            │
   │ 6. No match         │ → operator notified, manual override      │
   └────────────────────┴──────────────────────────────────────────┘
                        │
                        ▼
        SQLite bible.db  (verses, versions tables — KJV/BBE/TWI)
                        │
                        ▼
      Operator UI (main_ui.py) — Preview / Queue / AI Detections
        │  also opens: Browse (click Book→Chapter→Verse),
        │  Session History (verses actually pushed live), Theme Designer
                        │  (operator clicks ▶ / Go Live)
                        ▼
        DisplayWindow (projector view, windowed by default, themeable)
```

The sentence-transformer embedding model and FAISS indexes still load
entirely from `models/` on disk, offline. ASR is no longer offline-only by
default: `Config.backend="auto"` (the default) prefers Groq's cloud
Whisper API when `GROQ_API_KEY` is set, with local `faster-whisper` as an
automatic, self-healing fallback (§3.1) — a deliberate change from the
previous fully-offline-by-default design, made this session at the
operator's request.

---

## 3. Component-by-component

### 3.1 ASR — `app/asr/transcriber.py`

`BibleAITranscriber` + `Config` dataclass. Signal-driven
(silence-endpointed) segmentation, still not a fixed rolling window.

**Backend, rebuilt this session**: `Config.backend="auto"` (default)
prefers Groq's cloud Whisper API (`whisper-large-v3`) when `GROQ_API_KEY`
is set — this is now a genuine primary path, not a GPU-less fallback. A
failed cloud request drops to local **immediately** for that and
subsequent utterances (`_fallback_to_local`), but this is a half-open
circuit breaker, not a permanent downgrade: `_should_probe_cloud`/
`_recover_to_cloud` retry cloud in the background every
`cloud_retry_interval_s` (30s) and switch back automatically the moment a
retry succeeds. `_dispatch_worker_count()` sizes the decode thread pool
per-backend (4 cloud/I/O-bound, 2 local/CPU-bound) and resizes correctly
across a runtime backend flip. `backend="local"`/`"cloud"` still force one
path explicitly with no fallback/probing.

**Anti-hallucination stack, meaningfully changed this session after being
tested against real sermon audio for the first time** (previously only
verified against synthetic silence/noise, §20g of `PROGRESS.md`):
- **`Config.initial_prompt` now defaults to `None`** (was previously
  always-on, a bare Bible-book-name word list). Direct A/B evidence: with
  even the shrunk prompt, ~a third of a real transcript was the prompt
  sentence itself (or a garbled paraphrase of it) replacing real content;
  without it, zero fabricated content on the same clip. Kept as an opt-in
  field, documented with the A/B evidence in its own comment.
- **`_has_speech()`** — a real Silero VAD pass (via faster-whisper's
  bundled model) now runs before *either* backend, not just local's own
  `vad_filter=True` (which never covered cloud at all). Catches ambient
  noise/room tone that clears the segmenter's cheap RMS volume gate
  without being real speech — the actual trigger for most observed
  hallucination, more than the prompt itself.
- **`_is_prompt_echo()`** — defense-in-depth in `_passes_segment_gates`:
  if a prompt *is* configured, a segment matching or contained in the
  whole prompt sentence is dropped as a likely echo rather than reaching
  the transcript.
- Post-decode confidence gates eased back (`avg_logprob` -1.0→-1.2,
  `no_speech_prob` 0.70→0.80) now that VAD covers the main hallucination
  risk upstream — the tighter values were confirmed dropping real, merely
  quiet speech (whole sentences vanishing with no garbled trace).
- `temperature` still pinned to `0.0`, `repetition_penalty`/
  `no_repeat_ngram_size` at decode time, and `_has_repetition_loop()` as a
  text-level layer — all unchanged from prior sessions.

**First genuine WER measurement this project has had**: 3.47% (16 edits /
461 reference words: 9 substitutions, 2 deletions, 5 insertions), **zero
hallucinated content** — every remaining error is an ordinary mishearing
or a segmentation-boundary artifact. Measured against a real sermon
transcript the operator supplied, after the three fixes above.

**Twi ASR (new, experimental)**: `models/faster-whisper/akan-whisper` —
[`GiftMark/akan-whisper-model`](https://huggingface.co/GiftMark/akan-whisper-model),
a public `whisper-small` fine-tune for Akan/Twi, converted to CTranslate2
int8 format. Drops into the exact same local-decode path as any other
model (`Config(model_size="akan-whisper", backend="local")`) — zero code
changes were needed. Must be invoked with `language="en"` (already the
default) because the fine-tune repurposes Whisper's English language slot
to mean "decode Twi" rather than adding a real Akan token — confirmed both
by reading its `generation_config.json` and empirically (raw silence with
VAD off produced Twi-script text, not English). **Transcription accuracy
on real speech is NOT verified** — no published WER, no Twi audio
available in this environment to test against; mechanically confirmed
working (loads, runs, survives the GPU-unusable→CPU fallback cascade,
correctly suppresses silence via the same VAD/confidence-gate stack) but
genuinely unknown whether it's good enough for live use.

**Model path resolution**: `models/faster-whisper/<size>`, falling back to
`models/<size>`. Locally available: `base.en` (active default), `akan-whisper`
(new, see above), `distil-small.en`, `small` (multilingual), and a Vosk
model (unused, different engine).

### 3.2 Retrieval — `app/retrieval/`

- **`reference_extractor.py`** — regex + alias-table extraction of
  `(book, chapter, verse)`. **Rebuilt this session** into a per-language
  bundle system (`_LangBundle`/`_get_bundle`): book-name aliases, number
  words, and chapter/verse structural-marker words ("chapter"/"verse", or
  Twi's "ti"/"nkyekyɛmu") now live in per-language files
  (`aliases_en.py`, `aliases_twi.py`) merged at import time, and every
  public extraction function takes an `allowed_languages` parameter
  (default `None` = all languages) so a caller can restrict matching —
  used to gate Twi vocabulary to only the Twi Bible version (see below).
  29/29 self-test cases pass (the one pre-existing failure noted in the
  prior version of this document was resolved along the way).
- **`version_detector.py`** — `detect_version()`, `detect_navigation()`,
  `detect_verse_jump()`/`detect_range()`, `SessionState`. `VERSION_MAP`
  and `NAV_MAP` now also draw from the per-language alias files above
  (Twi entries added, **not yet gated by active version** — open item,
  see §5). 31/31 self-test cases pass.
- **`semantic.py`** — `SemanticEngine`, phrase map → event map → FAISS +
  lexical. **Bug fixed this session**: `clean_text()` used an ASCII-only
  regex that silently destroyed any non-English verse text (Twi's ɛ/ɔ
  characters replaced with spaces, splitting/destroying words) —
  corrupted the lexical half of hybrid search scoring for every verse in
  a non-English version. Fixed via a Unicode-aware character class;
  confirmed byte-identical on English, confirmed Twi words survive intact.
  **Embedding model is still English-only** (`all-MiniLM-L6-v2`) — Twi
  topic/paraphrase search does not yet match KJV's quality as a result (an
  exact phrase from TWI's own John 3:16 scored *lower* semantically than
  unrelated verses in one measured case); a multilingual model swap + full
  reindex is scoped but explicitly deferred by the operator. Direct
  verse-reference lookup is unaffected by this gap.
- **`hybrid.py`** — `HybridEngine`, the orchestrator.
  - **Two silent no-op bugs fixed this session**, same root cause: voice
    navigation (`_navigate` — NEXT/PREV/LAST/REPEAT), verse-jump (step 4),
    and `_switch_version`'s "redisplay current verse" step all refused to
    act whenever nothing had been explicitly pushed **Live**
    (`_live_position is None`) — silently, with no operator feedback.
    Since Go Live defaults OFF and every verse lands in Preview first,
    this meant voice navigation and version-switch redisplay never worked
    at all from the app's normal starting state. All three now fall back
    to `session.current_book/chapter/verse` (updated by every `_display()`
    call regardless of Live/Preview) when nothing's live yet, while still
    preferring the live position when something actually is live.
    Functional-tested against the real DB, not just unit-level.
  - **New `_allowed_languages()` helper** computes `{"en", "twi"}` vs.
    `{"en"}` from `session.active_version` and feeds `reference_extractor`
    calls at both the direct-reference and split-reference call sites, and
    (separately) `main_ui.py`'s manual search box — so a Twi book name
    only resolves while the Twi version is actually active, matching what
    an operator means by "the Twi version," not just recognized
    regardless of what's on screen.
  - `SEMANTIC_CONFIDENCE`/`SEMANTIC_CONFIDENCE_HI` and the rest of the
    matching/threshold logic are unchanged from the prior session.

### 3.3 Database — `app/database/`

`db.py` (schema + query helpers). `bible.db`: **KJV** (31,100 verses),
**BBE** (31,104 verses), and — new this session — **TWI** (Asante Twi,
31,104 verses, `data/tw_asante.json`, sourced by the operator's project
partner). 93,308 verses total. Validated before trusting it: correct
canonical book-name convention (matches the pipeline's existing English
`book` field), zero empty verse strings, Genesis 1:1 and John 3:16 spot-
checked as recognizable, correct Twi. No schema change was needed — the
`verses` table has no language column and never needed one.

NIV/NLT/ESV/NASB/AMP/GNT/MSG/NKJV remain recognized-but-not-installed for
the same licensing reasons as the prior session (unchanged, not
revisited).

### 3.4 UI — `app/ui/main_ui.py` (canonical)

Core layout/engine wiring unchanged from the prior session. The manual/
unified search box (`_book_mode_lookup`) respects the same Twi language
gating as the voice pipeline (§3.2) — typing a Twi book name while on an
English version behaves the same as saying it would: it doesn't resolve,
rather than silently displaying English text for a Twi-named reference.

**Four features added 2026-08-07**, all UI-layer only — no change to
`HybridEngine`'s matching/decision logic:

- **Queue reorder + save/load.** ▲/▼ per row reorders in place; Save…/
  Load… persist the Queue as a JSON program-list file (default folder
  `programs/`, mirroring `themes/`). Persistence logic lives in the new
  `app/ui/queue_store.py`, not inline in the click handler — testable
  without driving a real file-picker dialog.
- **Save current slide as image.** `🖼 Save Image` on the Live Output card
  grabs the projector window (`DisplayWindow.grab()`) to a PNG, named
  after the reference by default.
- **Session History window** (`app/ui/history_window.py`) — every verse
  actually pushed live this run (distinct from AI Detections, which caps
  at 30 and includes verses never sent live), with Refresh/Export
  (JSON/CSV/TXT)/Clear.
- **Browse window** (`app/ui/browser_window.py`) — click Book → Chapter →
  Verse instead of typing a reference; a chapter-preview pane (all verses,
  each individually sendable) closes the one interaction gap the unified
  Search box didn't cover. Reads the database only through three new
  `hybrid.py` functions — `db_list_books`, `db_chapter_count`,
  `db_get_chapter` — added as thin, unindexed-logic query wrappers
  alongside the existing `db_get_verse`/`db_get_next_verse`/etc.; nothing
  in the matching/decision pipeline was touched.

**Visual identity**: `app/ui/style_kit.py` (new) — one shared dark/gold
palette, QSS helpers, and a generated app icon, used by every secondary
window (Browse, History) so the app has one consistent identity instead
of each window styling itself independently. The Theme Designer is
intentionally left on its own plain chrome (a properties-inspector-style
tool, same precedent as before).

**Test coverage, new this session**: `tests/` (39 cases, 4 files) plus a
root `conftest.py` — exercises the Queue/History/Browse/Save-Image
features above against the real `HybridEngine` and real `bible.db`, not
mocks. See `PROGRESS.md` §22 for the full run and a genuine bug the test
suite surfaced along the way (a Windows DLL-load-order conflict between
PyQt5 and torch when both are imported in one pytest process).

### 3.5 Evaluation — `app/evaluation/`

Unchanged this session — `accuracy_eval.py`/`latency_bench.py` against the
stateless `pipeline.py` replica, three test sets (starter/stress/
generalization). Not re-run this session since no change was made to the
scoring/threshold logic they exercise; the real accuracy signal this
session came from a real transcript + WER measurement instead (§3.1),
which these harnesses can't produce since they test typed/short-clip text,
not audio.

---

## 4. Objective-by-objective status

| # | Objective | Status | Evidence |
|---|---|---|---|
| 1 | Capture & convert live audio to text | ✅ Met | Cloud-first (Groq) with automatic local fallback; 3.47% WER measured against real audio this session |
| 2 | Apply semantic embedding for context understanding | ✅ Met | `all-MiniLM-L6-v2`, local, FAISS `IndexFlatIP` (English only — see Twi caveat below) |
| 3 | Direct reference + semantic context matching | ✅ Met | Priority-ordered in `HybridEngine`, direct always wins |
| 4 | Twi-to-English cross-lingual semantic mapping | ⚠️ Started, not complete | Twi Bible data, book names, structural words, and an experimental Twi ASR model all added and working this session (§3.1–3.3); Twi *semantic/topic* search still blocked on an English-only embedding model, deferred by operator choice; Twi ASR transcription quality unverified |
| 5 | Three-state operational model | ✅ Met | `SessionState.state` — exactly 3 values, 1:1 with the objective |
| 6 | Voice-controlled verse progression | ✅ Met (exceeded) | NEXT/PREV/LAST/REPEAT/STOP + verse-jump + ranges; two silent no-op bugs fixed this session so this actually works from the app's default state |
| 7 | Multiple Bible versions (KJV, NIV, NLT) | ⚠️ Partial | KJV+BBE+**TWI** now have data; NIV/NLT recognized but blocked by licensing, not engineering |
| 8 | Manual override for accuracy control | ✅ Met | `manual_display()`/`manual_search()`; UI defaults to Manual mode |
| 9 | Evaluate system performance | ✅ Met | 3 test sets (118 cases) + a real WER measurement this session — the first genuine transcription-accuracy number this project has had |

**6 of 9 fully met, 2 partial (one licensing, one genuinely in-progress —
Twi), 1 upgraded from "not started" to "in progress" this session.**

---

## 5. Known issues, ranked by what actually affects a live demo

1. **Twi topic/paraphrase search doesn't yet match English's quality.**
   Root cause identified precisely (English-only embedding model producing
   noisy similarity scores on Twi text), fix scoped (multilingual model
   swap + full reindex of all three versions), explicitly deferred by the
   operator to a later session. Direct verse-reference lookup is
   unaffected.
2. **Twi ASR transcription quality is unverified.** The model mechanically
   works (loads, runs, correctly suppresses silence) but there's no
   published WER and no Twi audio available in this environment to test
   real accuracy against. Needs the operator (or a Twi speaker) to test
   with real speech before it's trustworthy for live use.
3. **Several Twi vocabulary entries are unverified drafts, clearly
   flagged in-code** — the Pentateuch book-naming convention, a handful
   of native-word OT book titles, and all Twi navigation-command phrases
   (`NAV_PHRASES`). Not a blocker for what's already confirmed working
   (NT book names, direct references, the Twi Bible text itself), but
   should not be presented as verified translations.
4. **Twi navigation commands (`NAV_MAP`) are not gated by active
   version**, unlike book-name references — flagged to the operator as an
   open consistency question, not yet decided either way.
5. **Two duplicate UI implementations.** `main_ui.py` is canonical;
   `main.py`+`operator_panel.py` is legacy, unimported elsewhere. Not
   touched this session — still recommend deleting/archiving before
   submission.
6. **`base.en` is the active default English model, not
   `distil-small.en`.** Unchanged this session — still a deliberate
   speed-over-accuracy choice from a prior session, worth revisiting.
7. **`venv/` (Python 3.12) is still broken** — unchanged, still points at
   a nonexistent interpreter path. `venv311` remains the working
   environment.
8. **NIV/NLT gap is a licensing constraint**, unchanged from the prior
   session — stated explicitly so it reads as a researched decision.
9. **External mixer board audio input is unresolved.** Connected via
   analog 3.5mm into the onboard mic/line-in jack (no new USB device
   enumerates; Windows relabels the existing default input `Microphone` →
   `aux`), but a direct capture test off that device returned near-silent
   levels (peak 0.0033) — no usable signal yet. Likely a fader/routing
   issue on the mixer itself, not narrowed down further. See `PROGRESS.md`
   §25.

`TRANSCRIPTION_SETUP.md`, flagged stale in the prior version of this
document, was corrected this session (it now documents the real
`BibleAITranscriber`/`Config` API and the new Twi-ASR setup) — no longer
an open issue.

---

## 6. Where training actually fits

Still zero models *trained from scratch* in this system — the Twi ASR
model added this session (§3.1) is a third party's fine-tune of
`openai/whisper-small`, found and integrated, not trained here. All
matching logic outside that model remains hand-tuned thresholds and
curated lookup tables. The generalization gap measured in a prior session
(77.4% zero-curation vs. 100% curated) is still the clearest evidence for
why that matters going forward. Full roadmap — bi-encoder fine-tuning, a
cross-encoder re-ranker, learning the score-blend weights, and now
concretely a multilingual embedding swap for Twi semantic search — is
written up in the progress report (`PROGRESS.md`, and its own earlier
"Where Training Fits" artifact), ordered by effort vs. payoff.
