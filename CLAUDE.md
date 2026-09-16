# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A live sermon-companion app: it listens to a preacher through a microphone, transcribes speech (cloud-first via Groq, local fallback), detects when a Bible verse is being read or paraphrased, and pushes it to a full-screen projector display, with a human operator always sitting between detection and the screen. Supports English (KJV/BBE) and, experimentally, Twi/Akan (TWI version + Twi ASR).

Full architecture/status detail lives in `SYSTEM_DOCUMENTATION.md` (regenerated each session from a direct read-through + live testing — treat it as authoritative over this file for anything about *current* behavior/known issues). `TRANSCRIPTION_SETUP.md` covers ASR setup in depth. `PROGRESS.md` is the historical build narrative (what was tried/reverted and why). Read the relevant one before making non-trivial changes to ASR or retrieval — both have a history of subtle, previously-fixed-then-reverted bugs.

**Do not edit `app/asr/transcriber.py` unassisted** — the user has in-progress work there.

## Commands

```powershell
# Run the app (the one canonical entry point)
.\venv311\Scripts\python.exe app\ui\main_ui.py
.\venv311\Scripts\python.exe app\ui\main_ui.py --version BBE     # start in a different version
.\venv311\Scripts\python.exe app\ui\main_ui.py --list-devices    # list audio input devices

# Tests (offscreen Qt + DLL setup handled by root conftest.py)
.\venv311\Scripts\python.exe -m pytest
.\venv311\Scripts\python.exe -m pytest tests\test_twi_and_splitref_fixes.py
.\venv311\Scripts\python.exe -m pytest tests\test_twi_and_splitref_fixes.py::test_verse_jump_after_complete_reference_is_not_recombined

# Rebuild bible.db from data/*.json (needed after cloning, or after editing data/*.json)
.\venv311\Scripts\python.exe -c "from app.database.db import build_database; build_database()"

# Standalone ASR transcriber (prints transcripts to console only -- does NOT feed the retrieval pipeline)
.\venv311\Scripts\python.exe -m app.asr.transcriber --list-devices
.\venv311\Scripts\python.exe -m app.asr.transcriber --model base.en
.\venv311\Scripts\python.exe -m app.asr.transcriber --model akan-whisper --backend local

# Standalone evaluation harnesses (never wired into the live app; run manually)
.\venv311\Scripts\python.exe -m app.evaluation.accuracy_eval
.\venv311\Scripts\python.exe -m app.evaluation.accuracy_eval --testset path\to\set.csv --version KJV
.\venv311\Scripts\python.exe -m app.evaluation.latency_bench
```

Use `venv311` — `venv/` is a stale, broken environment (wrong Python version path); do not try to fix or use it.

`models/` is gitignored (large binaries). First-time setup after cloning, while online, before `main_ui.py` will start (it forces `HF_HUB_OFFLINE=1` process-wide once retrieval loads, so nothing can auto-download inside the real app afterward) — see `TRANSCRIPTION_SETUP.md` for exact commands to fetch the sentence-transformer embedding model, a faster-whisper model, and (optionally) the Twi `w2vbert` model.

## Architecture

```
Microphone ──▶ BibleAITranscriber (Groq cloud primary, local faster-whisper fallback)
                        │  Silero VAD pre-filter + signal-driven, silence-endpointed utterances
                        ▼
              HybridEngine.process(text)   [app/retrieval/hybrid.py]
                        │
   1. Version change   → "read in BBE" / "switch to Twi bible"
   2. Navigation       → "next verse" / "stop display" / etc.
   3. Direct reference → "John 3:16" / "Yohane 3:16" (Twi vocab gated to TWI version)
   4. Verse jump       → "verse 20" (needs an existing position)
   5. Semantic search  → paraphrase/quote match, app/retrieval/semantic.py (disabled while TWI active)
   6. No match         → operator notified, manual override
                        │
                        ▼
        SQLite bible.db  (versions + verses tables: KJV, BBE, TWI)
                        │
                        ▼
      Operator UI (app/ui/main_ui.py) — Preview / AI Detections / Semantic Detections
        Browse is opened by default in place of the removed Queue feature
        also opens: Session History, Theme Designer
        (operator clicks ▶, then Go Live — the only path to the projector)
                        ▼
        DisplayWindow (projector view, themeable)
```

`app/ui/main_ui.py` is the single canonical entry point — no other launcher exists in the codebase.

### ASR — `app/asr/`
- `transcriber.py` — `BibleAITranscriber` + `Config`. Signal-driven (silence-endpointed) segmentation, not a fixed window. `Config.backend="auto"` (default) prefers Groq cloud (`whisper-large-v3`) when `GROQ_API_KEY` is set, with an automatic half-open-circuit-breaker fallback to local `faster-whisper` (retries cloud every `cloud_retry_interval_s`). Anti-hallucination stack: Silero VAD pre-filter before either backend, `Config.initial_prompt` off by default (was causing prompt-echo fabrication), `_is_prompt_echo()`, post-decode `avg_logprob`/`no_speech_prob` gates, temperature pinned to 0, repetition-loop detection.
- `factory.py` — `create_transcriber(language, **overrides)`: the English/Twi split point. `"en"` → `backend="auto"`; `"twi"` → `backend="w2vbert"` (Khaya is still an unimplemented placeholder). Extending a language only ever means touching its backend file under `app/asr/backends/`, never this function or `transcriber.py`.
- `backends/` — one file per backend (`local_whisper.py`, `groq_cloud.py`, `khaya.py` [unimplemented], `w2vbert.py` [experimental Twi, CTC beam search + Twi-Bible-vocabulary fuzzy correction via `rapidfuzz`]), plus `base.py`/`registry.py`.
- Model resolution: `models/faster-whisper/<size>`, falling back to `models/<size>`.

### Retrieval — `app/retrieval/`
- `reference_extractor.py` — regex + alias-table extraction of `(book, chapter, verse)`. Per-language bundles (`aliases_en.py`, `aliases_twi.py`) merged at import time; every public function takes `allowed_languages` to restrict matching by active Bible version. `_fuzzy_lookup()` (via `rapidfuzz`, 82% similarity floor + 6-char minimum) catches near-miss ASR garbling on book/structural words without colliding with ordinary sermon words.
- `version_detector.py` — `detect_version()`, `detect_navigation()`, `detect_verse_jump()`/`detect_range()`, `SessionState`.
- `semantic.py` — `SemanticEngine`: phrase map → event map → FAISS + lexical blend. Embedding model is `all-MiniLM-L6-v2` (English-only, loaded from `models/` offline) — this is why Twi semantic/paraphrase search is disabled rather than just lower-quality. Sets `HF_HUB_OFFLINE=1` process-wide at import time.
- `hybrid.py` — `HybridEngine`, the orchestrator (priority order above). `_allowed_languages()` gates Twi vocabulary recognition to whenever the TWI version is actually active. `_semantic_enabled()` disables semantic search entirely while TWI is active. Navigation/verse-jump/version-switch-redisplay fall back to `session.current_book/chapter/verse` when nothing is Live yet (not just `_live_position`), since Go Live defaults off and everything lands in Preview first — manually switching version via the UI redisplays the current position immediately too, not just the voice-triggered path. `set_semantic_candidates_callback()` fires the top-3 ranked `semantic.search_top_k()` candidates for every utterance that reaches step 5, independent of the display decision — feeds the UI's Semantic Detections panel (see below), not a mirror of what actually got displayed. Also exposes read-only DB helpers used by the UI (`db_list_books`, `db_chapter_count`, `db_get_chapter`, `db_get_verse`, `db_get_next_verse`, etc.) — the Browse window, History, and evaluation pipeline all go through these rather than touching `app/database/db.py` directly.

### Database — `app/database/db.py`
Single `verses` table (no language column — book/chapter/verse/text, keyed by `version_id`) plus a `versions` table. `build_database()` regenerates `bible.db` from `data/{en_kjv,en_bbe,tw_asante}.json`, idempotent per version (skips a version already imported), commits per-version so a bad later file doesn't roll back earlier ones. `bible.db` is gitignored — always rebuild after cloning or editing the JSON data files, never hand-edit the JSON either without knowing verse counts are validated elsewhere (KJV 31,100 / BBE 31,104 / TWI 31,104).

### UI — `app/ui/`
`main_ui.py` (canonical) wires the engine to: Preview, AI Detections (deterministic nav/direct-reference/command hits only), and Semantic Detections (fully decoupled from AI Detections — driven by `set_semantic_candidates_callback` above, shows the raw top-3 ranked semantic candidates per utterance rather than a filtered mirror of AI Detections) panels; `display_window.py` (the projector view); `browser_window.py` (Book→Chapter→Verse browse, opened by default in the AUX slot the removed Queue feature used to occupy); `history_window.py` (verses actually pushed live, distinct from AI Detections which caps at 30 and includes never-pushed verses); `theme_designer.py`/`theme_model.py`/`theme_store.py` (JSON themes under `themes/`). `style_kit.py` holds one shared palette + QSS helpers used by every secondary window except Theme Designer (deliberately left on plain chrome, properties-inspector style) — currently light/white, kept in sync with `main_ui.py`'s own light-only mode (dark/system removed from the topbar for now). The Queue feature (multi-verse ordered program lists) was removed entirely, including its UI, storage wiring, and tests; `queue_store.py` still exists on disk but nothing calls it anymore. There is no Fullscreen toggle or separate "Push to Live" button — Go Live is the *only* path anything takes to reach the projector (see Known constraints below).

**As of 2026-09-16**: the Navigation card's "Repeat" button was removed (unused by the operator; the voice-triggered "read that again"/`REPEAT` command in `hybrid.py` is untouched). `BrowsePanel` now exposes `set_version()`, called from `main_ui.py`'s `_on_engine_status()` on every `"version_switch"` event — so Browse's own version combo stays in sync whether the version changed via the manual dropdown or a voice-triggered "read in BBE", instead of silently going stale on whatever version Browse happened to be opened with. Browse's Chapters column is now a single vertical list (`CHAPTER_COLUMNS = 1`) rather than a 6-wide grid. `style_kit.fit_to_screen()` caps a window's initial size/position to the actual screen it opens on (used by `OperatorWindow` and `ThemeDesigner`, replacing hardcoded `resize()`/`setMinimumSize()` calls tuned for one developer monitor) — verified against a simulated small screen. `ThemeDesigner` still has a hard structural floor around ~1550px wide from its fixed 960×540 live-preview canvas + fixed 220px sidebar (no splitter between them), so it can still open larger than a very small screen; fixing that would mean making the preview canvas itself resizable, which is out of scope for now (see `theme_designer.py`'s own docstring on the preview being non-dynamic by design).

### Evaluation — `app/evaluation/`
Standalone, never wired into the live app: `accuracy_eval.py` and `latency_bench.py` run against `pipeline.py` (a stateless replica of the direct-reference → semantic → DB-lookup flow) over labeled test sets in `app/evaluation/testsets/` (starter/stress/generalization), writing CSV+JSON to `app/evaluation/results/`. This is the only source of accuracy/latency numbers on typed/short-clip text; real transcription accuracy (WER) requires a real audio transcript instead, done ad hoc, not through this harness.

### Tests — `tests/` + root `conftest.py`
Exercise real `HybridEngine` + real `bible.db` (no mocks — this is the established convention, not an exception). Root `conftest.py` forces `QT_QPA_PLATFORM=offscreen` and pre-imports `app.retrieval.hybrid` before pytest imports any test module — this is load-bearing on Windows: whichever of PyQt5's or torch's bundled DLLs (MSVC/OpenMP/MKL) loads into the process first wins the DLL search order, and getting this wrong fails with `WinError 1114`. If a new test file imports PyQt5 or torch directly, don't reorder these imports without understanding why.

## Known constraints worth knowing before touching related code

- **Go Live is the only path to the projector.** With it OFF (the safe-by-default state), nothing reaches `DisplayWindow` — not even an explicit ▶ on a detection/search/Browse result, which only ever promotes to Preview. There is no separate one-off "push this single verse live while staying in manual mode" control; it existed as a "Push to Live" button and was removed twice by explicit user request despite this exact tradeoff being explained after the first removal caused a "verse not displaying" report. Don't reintroduce it without being asked, but also don't be surprised if this comes up again.
- The UI is locked to light mode only for now — dark/system removed from the topbar's theme row (not just hidden). `main_ui.py`'s `_apply_theme`/`_switch_theme` still exist and work if dark mode is restored later; `style_kit.py`'s palette would need switching back to `THEMES["dark"]`'s values at the same time (see its docstring).
- Twi semantic/paraphrase search is deliberately disabled (`hybrid.py`'s `_semantic_enabled()`), not just lower-quality — the embedding model is English-only. Don't re-enable it without a multilingual model swap + reindex.
- Twi ASR (`w2vbert`) transcription accuracy on real speech is unverified (73.6% WER on its own small test set). Don't present it as production-ready.
- A handful of Twi vocabulary entries (Pentateuch naming convention, some OT book titles, `NAV_PHRASES`) are explicitly flagged in-code as unverified drafts.
- `base.en` is the active default English ASR model, not `distil-small.en` — a deliberate speed-over-accuracy tradeoff.
- `venv/` (Python 3.12) is broken/unused; `venv311` is the only working environment.
