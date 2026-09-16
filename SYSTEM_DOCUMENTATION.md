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

**Addendum, 2026-09-14** (Twi ASR reachability/tuning + a Twi
semantic-search safety gate; still uncommitted on `main` at time of
writing): the experimental `w2vbert` Twi backend is now actually
reachable from the running app (previously it existed in code but
`main_ui.py` had no language selector and `factory.py` defaulted Twi to
the still-unimplemented Khaya placeholder), and got a real accuracy/speed
pass (CPU-aware threading, decode serialization, silence trimming, CTC
beam search + Twi-Bible-vocabulary lexicon correction, both new levers
unvalidated against real audio). Twi number words extended from a
hand-capped 1–10 to the full 1–176 range needed by any verse number,
via programmatic compounding rules supplied by the user. A real,
previously-working Twi safety gate — disabling semantic (paraphrase)
search entirely while the TWI version is active, since the embedding
model is English-only and was silently scoring Twi text as confident
nonsense — was found missing (lost in an earlier revert) and re-added.
See `PROGRESS.md` §27 for the full narrative, including one fix
(a split-reference regression affecting bare "verse N" jumps) that
hasn't yet been re-verified against the real `HybridEngine` end-to-end.

**Addendum, 2026-09-15** (Operator UI only — ASR/retrieval sections below
are unchanged from 2026-08-28/2026-09-14 and were not re-verified this
pass, except one small `hybrid.py` addition noted where it applies): a
long operator-driven UI simplification/fix pass — §2 and §3.4 rewritten
below to match. Highlights: the redundant "Live Output" mirror panel
removed (Preview already showed the same thing); the projector display
now shows its first verse instantly instead of a wasted fade animation,
and its default background is no longer flat black; Go Live now
auto-opens the display if needed and is the *only* path to the projector
(Fullscreen and the separate "Push to Live" button were both removed by
explicit request, the latter twice — see `PROGRESS.md` §29 for why that
one is worth reading before touching again); the Queue feature (ordered
program lists) was removed entirely, replaced by Browse opened by
default; the UI is locked to light mode only for now; Semantic Detections
is now fully decoupled from AI Detections and shows the raw top-3 ranked
semantic candidates per utterance (new `hybrid.py`
`set_semantic_candidates_callback()`) instead of a filtered mirror of
whatever got displayed. All verified against the real `OperatorWindow`
(offscreen Qt, real fonts, real screen-resolution screenshots) and the
full `pytest` suite, not just read from the diff. See `PROGRESS.md` §29
for the full narrative, including a mid-session discovery that another,
separate session was committing to this same repo concurrently (one
commit, `6c1b8ae`, landed on `main` mid-pass) — worth knowing before
assuming the working tree only reflects what any single session did.

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
      Operator UI (main_ui.py) — Preview / AI Detections / Semantic Detections
        │  Browse (click Book→Chapter→Verse) opens by default in the
        │  slot the removed Queue feature used to occupy; also opens
        │  Session History (verses actually pushed live), Theme Designer
                        │  (operator clicks ▶, then Go Live — the ONLY
                        │   path anything takes to reach the projector;
                        │   auto-opens the display if it isn't already)
                        ▼
        DisplayWindow (projector view, windowed only, themeable —
                        no Fullscreen toggle as of 2026-09-15)
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

**Twi ASR (experimental, and — as of 2026-09-14 — actually reachable from
the app)**: `app/asr/backends/w2vbert.py`, a free offline fallback for
when Khaya (the hosted API, still an unimplemented placeholder) is
unavailable. `app/asr/factory.py`'s `create_transcriber("twi", ...)` now
defaults to `backend="w2vbert"`, and `main_ui.py` gained a "Language:"
selector (English / Twi) in the microphone card, independent of the
Bible-version dropdown, so an operator can actually pick this at
runtime — previously it existed in code but nothing in the UI could
select it. Base model:
[`ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1`](https://huggingface.co/ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1),
converted to CTranslate2 int8. Measured at 73.6% WER on its original
30-sentence test set — meaningfully worse than Khaya, not a recommended
primary choice. Reworked 2026-09-14 for speed and accuracy: CPU-aware
`intra_threads` (was hardcoded for one specific machine), decodes
serialized against the dispatcher's own concurrency, leading/trailing
silence trimmed before decode, greedy argmax replaced with CTC beam
search (`pyctcdecode`, with an automatic greedy fallback), plus a
post-decode correction pass against a Twi-Bible-word vocabulary
(`rapidfuzz`, same technique validated elsewhere in this codebase for
book-name fuzzy matching). **Neither the beam search nor the lexicon
correction has been validated against real Twi audio** — no WER
benchmark exists for either yet; both fail safe to the prior
greedy/uncorrected behavior. **Transcription accuracy on real speech is
otherwise still NOT verified** — no Twi audio available in this
environment to test against; mechanically confirmed working (loads,
runs, correctly suppresses silence via the same VAD/confidence-gate
stack) but genuinely unknown whether it's good enough for live use. See
`PROGRESS.md` §27 and `TRANSCRIPTION_SETUP.md` for the full detail,
including the one-time online model fetch required before first use
(the app runs fully offline afterward, `HF_HUB_OFFLINE=1`).

A separate, now-unused Akan Whisper fine-tune
([`GiftMark/akan-whisper-model`](https://huggingface.co/GiftMark/akan-whisper-model))
was evaluated in an earlier session and is still present under
`models/faster-whisper/akan-whisper` (drops into the local `faster-whisper`
path via `Config(model_size="akan-whisper", backend="local")`) but is not
what `factory.py` wires up for Twi by default — `w2vbert` above is.

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
  - **New `_semantic_enabled()` gate, 2026-09-14**: disables semantic
    (paraphrase) search entirely whenever the active version is TWI —
    `semantic.py` has no language awareness and runs the same
    English-only embedding model regardless of version, so a Twi query
    was silently scoring confident-looking nonsense rather than erroring.
    This existed once before but was lost in the `1c8bce2` revert; this
    session re-added it. Direct-reference matching is unaffected — TWI
    still works for anything with an actual spoken reference. See
    `PROGRESS.md` §27.

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

Engine wiring (§3.2 gating, priority order) unchanged. The manual/unified
search box (`_book_mode_lookup`) respects the same Twi language gating as
the voice pipeline — typing a Twi book name while on an English version
behaves the same as saying it would: it doesn't resolve, rather than
silently displaying English text for a Twi-named reference. The panel
layout itself changed substantially on 2026-09-15 (below); Browse/History
(added 2026-08-07) are otherwise unchanged in behavior.

**Preview / Live model, current as of 2026-09-15**: every verse lands in
Preview first regardless of source (voice detection, ▶ on a
Detections/Search/Browse row). **Go Live is the only path anything takes
to reach the projector** — there is no separate one-off "push just this
verse while staying in manual mode" control (a "Push to Live" button
existed for exactly that and was removed twice by explicit operator
request, despite the tradeoff — see `PROGRESS.md` §29). Turning Go Live
ON immediately pushes whatever's currently in Preview and auto-opens the
projector `DisplayWindow` if it isn't already open (previously this could
silently no-op if the operator hadn't clicked "Open Display" first).
Manually switching Bible version via the UI now redisplays the current
verse/live position immediately too, matching the voice-triggered
`process("read in BBE")` path (previously only the *next* detection
picked up a manually-switched version).

**Panels, current as of 2026-09-15**:

- **AI Detections** — deterministic hits only (direct reference/
  navigation/command). Semantic hits used to also appear here as a
  secondary mirror; they no longer do (see below) — decoupled, not
  filtered.
- **Semantic Detections** — fully independent panel, populated by a new
  dedicated channel (`hybrid.py`'s `set_semantic_candidates_callback()`,
  firing `semantic.search_top_k()`'s raw top-3 per utterance that reaches
  step 5) rather than a mirror of whatever `_run_semantic` decided to
  display. Each utterance's top 3 ranked candidates render as separate
  cards (`#1`/`#2`/`#3` prefix, display-only — never written into the
  card's real `book` field, which its own ▶ still needs for a correct
  DB lookup). Detection cards' "heard:" transcript text is shown in full
  now, not truncated to 90 characters — added specifically so an operator
  can evaluate match accuracy against the complete phrase that triggered
  it, not a clipped preview.
- **Browse** — unchanged in behavior, but now opens by default (in the
  AUX slot the removed Queue feature used to occupy) instead of requiring
  a manual click.
- **Queue feature removed entirely** — the 2026-08-07 reorder/save-load
  feature, its UI, `_add_to_queue`/etc. wiring, and its "+" buttons across
  Detections/Search/Browse are gone, along with their tests.
  `app/ui/queue_store.py` (the JSON program-list persistence module) still
  exists on disk and still has its own passing tests, but nothing in the
  UI calls it anymore.
- **Save current slide as image** — moved from the removed Live Output
  card onto the Preview card's own header (`🖼` icon button); behavior
  (`DisplayWindow.grab()` to a PNG, named after the reference) unchanged.
- **"Live Output" mirror panel removed** — it duplicated Preview once a
  verse went live; Preview already shows every verse regardless of Live
  state, so the mirror was redundant.
- **Fullscreen toggle removed** — `DisplayWindow` now only ever opens
  windowed (drag/resize onto the projector manually); its
  `enter_fullscreen()`/`exit_fullscreen()`/`toggle_fullscreen()` methods
  still exist on the class, just unused by the UI.
- **Theme locked to light mode only, for now** — the topbar's dark/system
  toggle buttons were removed (not just hidden); `style_kit.py`'s shared
  secondary-window palette (Browse/History) was switched to match, since
  it used to be hardcoded dark independent of the main window's own
  toggle and stayed dark even after the main window went light.
- **Projector display**: shows its first verse (after opening, or after a
  clear) instantly — it used to run a full fade-out on nothing followed
  by a fade-in, wasting `FADE_MS` (400ms) twice for no visible reason.
  Default background changed from flat black to a themed color (Classic
  Gold: oxford blue `#14213D`; Modern Minimal: dark slate `#1C2333`) —
  both were black by accident (an unset/never-customized field), not
  by design.
- **Layout**: Preview and Search cards now share equal stretch height in
  the right column (both have genuinely variable-length content); several
  buttons (Clear ×3, "N new ▲" badges) had their height hardcoded to a
  pixel value that clipped their own text under some font/DPI conditions
  — fixed by removing the fixed height in favor of each button's natural
  `sizeHint()`, which can't reclip regardless of the system's font
  rendering. The Browse panel's first-open resize is capped to the
  screen's actual available width, since asking for more (e.g. base
  window width + Browse's own 650px) doesn't get a wider window, just an
  OS-level clamp *after* the layout had already divided up the
  uncapped, wider figure — silently squeezing every other panel below
  its intended size.

**Visual identity**: `app/ui/style_kit.py` — one shared palette (light,
see above), QSS helpers, and a generated app icon, used by every
secondary window (Browse, History) except the Theme Designer
(intentionally left on its own plain chrome, properties-inspector style).

**2026-09-16 follow-up pass** (small, operator-requested fixes on top of
2026-09-15's UI simplification above):

- The Navigation card's "Repeat" button was removed — unused by the
  operator. The underlying voice command (`REPEAT`/"read that again" in
  `hybrid.py`) is a separate feature and was left alone.
- **Browse version sync.** `BrowsePanel` had its own version combo, set
  once from `initial_version` at panel-creation time with no way to learn
  about a later switch — so switching the active Bible version (manual
  dropdown or voice) after opening Browse left its combo silently stale.
  Fixed with a new `BrowsePanel.set_version()`, called from
  `_on_engine_status()`'s existing `"version_switch"` handler (already
  the single callback both the manual and voice-triggered switch paths
  land in), so Browse now always reflects whatever version is actually
  active.
- **Browse chapters are now a vertical list**, not a 6-column grid
  (`CHAPTER_COLUMNS` in `browser_window.py`), matching the vertical
  Books/Verses columns either side of it.
- **Window sizing is now screen-aware.** `OperatorWindow` used a
  hardcoded `setMinimumSize(1680, 760)` / `resize(1750, 860)` tuned for
  one desktop monitor — reported as "everything can't fit" on a smaller
  screen. New `style_kit.fit_to_screen(widget, ideal_w, ideal_h, min_w,
  min_h)` caps both the initial size and the resize-down floor to the
  actual screen's `availableGeometry()` and centers the window in
  whatever space that leaves; `ThemeDesigner`'s `resize(1400, 860)` was
  switched to the same helper. Verified by simulating an 800×600 screen
  (Qt's offscreen platform) against the real `OperatorWindow`/
  `ThemeDesigner` classes: `OperatorWindow` now fits exactly; `
  ThemeDesigner` still snapped back to ~1558px wide because its layout
  has a fixed 960×540 live-preview `QFrame` plus a fixed 220px sidebar
  with no `QSplitter` between them — Qt grows a window back to its
  layout's real minimum regardless of an earlier `resize()` call, so
  capping the *initial* size alone can't fix a window whose content has
  its own hard floor. Making that preview canvas itself resizable is a
  larger, deliberately out-of-scope change (its own docstring already
  flags the fixed-size preview as deferred).

**Test coverage**: `tests/` (52 cases as of this session, excluding one
new file deliberately not yet passing — see `PROGRESS.md` §29) plus a
root `conftest.py`, exercising the panels/features above against the real
`HybridEngine` and real `bible.db`, not mocks. A Windows DLL-load-order
conflict between PyQt5 and torch (both bundle their own MSVC/OpenMP/MKL
runtimes; whichever loads into the process first wins the search order)
is why `conftest.py` forces import order explicitly — see its own
comments, unchanged this session.

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
| 4 | Twi-to-English cross-lingual semantic mapping | ⚠️ Started, not complete | Twi Bible data, book names, structural words (incl. full 1–176 number compounding as of 2026-09-14), and an experimental, now UI-reachable Twi ASR model (`w2vbert`) all added and working (§3.1–3.3); Twi *semantic/topic* search is explicitly disabled (not just lower-quality — see `_semantic_enabled()`, §3.2) pending an English-only embedding model swap, deferred by operator choice; Twi ASR transcription quality still unverified against real audio |
| 5 | Three-state operational model | ✅ Met | `SessionState.state` — exactly 3 values, 1:1 with the objective |
| 6 | Voice-controlled verse progression | ✅ Met (exceeded) | NEXT/PREV/LAST/REPEAT/STOP + verse-jump + ranges; two silent no-op bugs fixed this session so this actually works from the app's default state |
| 7 | Multiple Bible versions (KJV, NIV, NLT) | ⚠️ Partial | KJV+BBE+**TWI** now have data; NIV/NLT recognized but blocked by licensing, not engineering |
| 8 | Manual override for accuracy control | ✅ Met | `manual_display()`/`manual_search()`; UI defaults to Manual mode |
| 9 | Evaluate system performance | ✅ Met | 3 test sets (118 cases) + a real WER measurement this session — the first genuine transcription-accuracy number this project has had |

**6 of 9 fully met, 2 partial (one licensing, one genuinely in-progress —
Twi), 1 upgraded from "not started" to "in progress" this session.**

---

## 5. Known issues, ranked by what actually affects a live demo

1. **Twi topic/paraphrase search is now disabled outright, not just
   lower-quality.** As of 2026-09-14, `hybrid.py._semantic_enabled()`
   turns off semantic search entirely while TWI is active (see §3.2) —
   deliberately, since the prior "search anyway, at lower quality" state
   was silently returning confident-looking wrong answers, not just weak
   ones. Root cause unchanged: English-only embedding model. Fix still
   scoped the same way (multilingual model swap + full reindex of all
   three versions), still deferred by the operator. Direct verse-reference
   lookup is unaffected.
2. **Twi ASR transcription quality is still unverified**, now against a
   different (and, as of 2026-09-14, more actively tuned) model. The
   default offline Twi backend is now `w2vbert` (73.6% WER on its own
   small test set — see §3.1), reachable from the app's UI for the first
   time this session, plus two new, also-unvalidated accuracy levers (CTC
   beam search, Twi-Bible-vocabulary lexicon correction). Still no
   published WER for the tuned pipeline and no Twi audio available in
   this environment to test real accuracy against. Needs the operator (or
   a Twi speaker) to test with real speech before any of this is
   trustworthy for live use — the single most-repeated open item across
   every Twi-ASR session so far.
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
10. **Go Live is now the only path to the projector, as of 2026-09-15** —
    genuinely worth ranking near the top of "affects a live demo": there
    is no one-off "push just this verse" control anymore (see §3.4),
    only the Go Live toggle. An operator who leaves it OFF (the safe
    default) and only clicks ▶ will see verses land in Preview and
    nothing project — already confirmed as a real point of confusion
    once mid-session (reported as "the verse is not displaying"). Not a
    bug — a deliberate simplification made at the operator's explicit,
    repeated request — but a demo walkthrough should say this out loud
    before anyone reaches for ▶ expecting it alone to project something.

11. **Theme Designer can still open larger than a small screen.** As of
    2026-09-16, `OperatorWindow`'s sizing is screen-aware
    (`style_kit.fit_to_screen()`, see §3.4), but `ThemeDesigner`'s fixed
    960×540 preview canvas + fixed 220px sidebar give it a hard layout
    minimum around 1550px wide that the same fix can't override — Qt
    re-grows the window to that minimum once shown, regardless of the
    size it was constructed with. Only affects the Theme Designer window,
    not the main Operator Panel. Fix would mean making the preview canvas
    itself resizable (currently deferred by design — see
    `theme_designer.py`'s own docstring).

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
