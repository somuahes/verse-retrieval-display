# Bible AI — Transcription Pipeline: Setup & Run Guide

## Quick-start (Windows, CPU-only)

```bat
:: 1. Create virtual environment
python -m venv venv311
venv311\Scripts\activate

:: 2. Install CPU-only PyTorch (smaller download, no CUDA needed)
pip install torch --index-url https://download.pytorch.org/whl/cpu

:: 3. Install all other dependencies
pip install -r requirements.txt
```

The first time the **standalone transcriber** (`python -m app.asr.transcriber`)
runs, it downloads the faster-whisper model weights for whatever
`--model` you asked for automatically. Subsequent runs load from the
local `models/faster-whisper/<size>` cache instantly.

**That auto-download does NOT happen for the real app (`main_ui.py`)** —
`app/retrieval/semantic.py` sets `HF_HUB_OFFLINE=1` (and friends)
process-wide at import time, deliberately, so the app never makes a
network call once it's actually running at a venue. `main_ui.py` imports
that module (via `hybrid.py`) before any model gets loaded, so **every**
download attempt in that process — the sentence-transformer, faster-whisper,
all of it — is blocked, not just huggingface_hub's own telemetry. `models/`
is also gitignored (see `.gitignore`), so a fresh clone starts with none
of this present. **First-time setup after cloning, before running
`main_ui.py`, needs three things fetched while online:**

```bat
:: 1. Sentence-transformer embedding model (semantic search) -- required,
::    the app will not start without this:
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2').save('models/sentence-transformer/all-MiniLM-L6-v2')"

:: 2. A faster-whisper English model -- default is base.en. Runs the
::    standalone transcriber briefly (a separate process, no offline
::    flag set there) purely to trigger its normal auto-download; Ctrl+C
::    once "Listening..." appears, the model is cached by then:
python -m app.asr.transcriber --model base.en

:: 3. Bible database -- regenerate from the committed data/*.json:
python -c "from app.database.db import build_database; build_database()"
```

Twi ASR (`akan-whisper`) is optional and separate — see "Twi
transcription" further down; skip it if you're not testing Twi.

---

## Running the transcriber standalone

```bat
:: List your audio devices first:
python -m app.asr.transcriber --list-devices

:: Start live transcription (system default mic, base.en model):
python -m app.asr.transcriber

:: Specify a microphone by device index:
python -m app.asr.transcriber --device 2

:: Use a smaller/faster model:
python -m app.asr.transcriber --model tiny.en

:: Tune segmentation/decoding:
python -m app.asr.transcriber --beam 5 --endpoint-silence-ms 350 --max-utterance-seconds 5

:: Twi/Akan speech (see "Twi transcription" below) -- force local so it
:: doesn't try Groq (which doesn't have this model):
python -m app.asr.transcriber --model akan-whisper --backend local
```

Standalone mode (running the module directly) only prints each
transcribed utterance to the console — it does not feed the Bible
retrieval pipeline. To see verses actually detected and displayed, run
the real app instead:

```powershell
.\venv311\Scripts\python.exe app\ui\main_ui.py
```

---

## Using the transcriber in your own code

The real classes are `BibleAITranscriber` and `Config` (both in
`app.asr.transcriber`) — not `Transcriber`/`TranscriberConfig`, and
there is no `.transcribe_file()` or `.start_live()` method.

```python
from app.asr.transcriber import BibleAITranscriber, Config

def on_transcript(text: str):
    print(f"[LIVE] {text}")
    # Feed to your Bible retrieval pipeline here,
    # e.g. hybrid_engine.process(text)

cfg = Config(
    model_size="base.en",   # tiny.en / base.en / small / distil-small.en / medium
    device_index=None,      # None = system default input device
    beam_size=5,
    endpoint_silence_ms=350,
    max_utterance_seconds=5.0,
)

t = BibleAITranscriber(cfg)
t.set_callback(on_transcript)
t.start()          # non-blocking — returns immediately, runs on its own stream
# ... later ...
t.stop()
```

Segmentation is signal-driven (silence-endpointed), not a fixed rolling
window: audio accumulates into a per-utterance buffer while the speaker
is talking, and a sustained pause (`endpoint_silence_ms`) finalizes and
dispatches that exact utterance for transcription. There is no
file-transcription entry point — this is a live-microphone class only.

### Optional: cloud-first with automatic local fallback (Groq)

`Config.backend` is `"auto"` by default: if `GROQ_API_KEY` is set (env
var, or `Config.groq_api_key`), transcription runs against Groq's cloud
Whisper API (`Config.groq_model`, default `"whisper-large-v3"`) as the
primary path.

If a cloud request fails mid-session (no internet, an outage, a rate
limit), the transcriber drops to the local model immediately for that
and subsequent utterances — no restart needed. It then keeps retrying
cloud in the background every `Config.cloud_retry_interval_s` seconds
(default 30s) and switches back to cloud automatically the moment a
retry succeeds, so a session comes back online on its own once internet
/ Groq is reachable again.

Set `backend="local"` to force local-only regardless of any configured
key (no cloud calls, no retries). Set `backend="cloud"` to force cloud
and skip local model loading entirely at startup (still falls back to
local at runtime the same way if a request fails, as long as local model
files are present).

### Twi transcription (experimental, untested on real audio)

`models/faster-whisper/akan-whisper` is [GiftMark/akan-whisper-model](
https://huggingface.co/GiftMark/akan-whisper-model) — a Whisper-small
fine-tune for Akan/Twi, converted to CTranslate2 format so it drops into
the exact same local-decode path as every other model here. No code
changes were needed for this — `_init_local_backend` already loads
whatever folder `Config.model_size` names.

One real gotcha, confirmed by inspecting its `generation_config.json`:
this model was fine-tuned by repurposing Whisper's **English** language
slot (`<|en|>`, token 50259) to mean "decode Twi" — it doesn't have a
real Akan/Twi language token of its own. So it must be used with
`language="en"` (`Config.language`'s existing default — nothing to
change), NOT a Twi/Akan language code. Passing anything else defeats the
fine-tuning and likely gets normal English (or garbage) output instead.

```bat
python -m app.asr.transcriber --model akan-whisper --backend local
```

Verified so far: loads correctly via `faster_whisper.WhisperModel`,
survives the existing GPU-unusable → CPU/int8 fallback cascade
unchanged, and the existing hallucination defenses (the Silero VAD
pre-filter, faster-whisper's own internal VAD, and the post-decode
confidence gates) all correctly suppress silence with this model too —
none of that logic is Whisper-checkpoint-specific. On raw silence with
VAD disabled (only to see what the bare model does), it produced
Twi-script text ("Mɛkyɛn."), not English — confirming the language="en"
behavior above actually triggers Twi decoding, not just theoretically.

NOT yet verified: real transcription accuracy on actual spoken Twi. The
model card publishes no WER, and there's no way to test that without
real Twi audio and a Twi speaker to judge the output — try it against
real speech and see.

**Update, since this was written**: two *other* Akan-labeled Whisper
fine-tunes were downloaded and actually benchmarked (30 held-out
sentences from `ghananlpcommunity/twi-speech-text-multispeaker-16k`,
measured WER/CER directly, not self-reported) —
`teckedd/whisper_small-waxal_akan-asr` (96.3% WER) and
`ghananlpcommunity/whisper-large-v3-turbo-akan-ct2` (93.1% WER). Both
are unusable. A third, non-Whisper candidate
(`ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1-onnx-int8`, CPU-friendly
quantized ONNX) measured 73.6% WER on the same test set — a real
improvement, but still not accurate enough to trust for verse-matching
on its own. `GiftMark/akan-whisper-model` above has never been put
through this same benchmark; if picking it back up, use the same
dataset/methodology for a fair comparison against these numbers rather
than judging it on vibes.

Given none of the offline options are good enough yet, live Twi ASR
uses Khaya's hosted API instead — see below.

### Twi transcription (Khaya API, not Whisper-based)

None of the offline candidates above are accurate enough to rely on.
`backend="khaya"` uses [GhanaNLP's Khaya API](
https://translation.ghananlp.org) instead — a hosted ASR service
actually trained on Ghanaian languages (unlike the Whisper fine-tunes
above, which repurpose a slot Whisper was never trained on). Verified
directly against a real test call: a perfect transcription on a test
clip, dramatically better than any offline candidate measured so far.

Setup:
1. Sign up at https://translation.ghananlp.org and subscribe to the
   **Developer** (free) product — 100 calls/month, rate-limited to 10
   requests/minute. A credit card is asked for at signup but a free
   subscription shouldn't need to charge it; email
   subscriptions@khaya.ai if you want to activate without one.
2. Get your subscription key from the portal's profile/subscriptions
   page, then set it as an environment variable (mirrors `GROQ_API_KEY`):
   ```powershell
   $env:KHAYA_API_KEY = "your-subscription-key"
   ```
   Treat this like any other secret — don't paste it into chat, commit
   it, or screenshot a page that shows it in plaintext.
3. Run with `--backend khaya --language tw` (or whatever Khaya language
   code applies — `GET /languages` on the API lists supported codes).

```bat
python -m app.asr.transcriber --backend khaya --language tw
```

Unlike the Groq cloud path, a failed Khaya request does **not** fall
back to a local model — it drops that utterance instead. Falling back
to a local Whisper model with no real Twi support would silently
produce confident English-shaped garbage on Twi audio, which is worse
than producing nothing (same reasoning as the anti-hallucination gates
elsewhere in `transcriber.py`). Khaya's response also carries no
per-segment confidence scores the way Groq/local Whisper's does, so the
numeric anti-hallucination gates are inert for this backend by
construction — only the language-independent checks (VAD pre-filter,
repetition-loop detection, word-count floor) still apply.

Khaya's "standard" ASR product (what this uses) is designed for single
sentences with fast responses — a good match for this project's
per-utterance dispatch. Two other Khaya ASR products exist
(`v2`/"most accurate" and `v3`/general Ghanaian-languages) but are
explicitly documented as long-form/batch-oriented and "not suited for
real-time applications" — don't swap the endpoint to those for live
use without re-verifying that constraint no longer applies.

### Twi transcription, offline fallback (`backend="w2vbert"`)

For testing when Khaya is unavailable (free-tier quota exhausted, no
internet) — **not a recommended primary choice**. Uses
`ghananlpcommunity/w2v-bert-2.0_twi_alpha_v1_farmerline-ct2`, a
genuinely Twi-fine-tuned Wav2Vec2-BERT model (not a
repurposed-language-slot hack like the two candidates in the section
above), converted to CTranslate2 for faster CPU inference (same
technique `faster-whisper` uses for Whisper). Free, fully offline, no
API key, no quota — but measured directly against this project's own
30-sentence test set at **73.6% WER** (37.7%--39.4% CER across two
separate measurements) — meaningfully better than the two Whisper-based
Akan candidates (93-96% WER) but nowhere near Khaya's quality (a live
test transcribed a full sentence perfectly).

Speed history worth knowing if this ever needs revisiting: the plain
ONNX build of this same model (int8-quantized) measured ~13s to decode
a single 7s clip on this project's hardware — clearly too slow for live
dispatch. Switching to this CTranslate2 build with `intra_threads=16`
(the empirically-found sweet spot for this machine's 8 physical cores —
going higher, e.g. 24/32, measured *worse*, 8.9s/7.7s, from thread
contention) cut that to **~5.9s average** (best run 5.5s) — a real,
more-than-2x improvement, but still not true real-time. A 7s clip
taking ~6s to decode means the app will still gradually fall behind
during continuous speech, just far less severely than the ~13-16s ONNX
baseline. Same underlying weights/accuracy either way — this only
changes inference speed. The README on that HF repo's own usage example
uses the wrong CTranslate2 class (`Wav2Vec2` instead of the correct
`Wav2Vec2Bert`) and passes a raw numpy array where a `StorageView` is
required — don't copy it directly if revisiting this.

Downloads on first use if not cached (~1.2GB); budget for this
machine's known flaky huggingface.co connection on that first run.

```bat
python -m app.asr.transcriber --backend w2vbert --language ak
```

Selectable from the operator panel too: the "Twi engine" dropdown next
to the microphone device selector (only consulted when TWI is the
active Bible version) lets an operator switch between Khaya and this
offline fallback without touching config or restarting the app —
useful for testing, or as a stopgap while waiting out a quota reset.

Also evaluated and rejected as *not* an improvement: Meta's MMS
(`facebook/mms-1b-all` with the `aka` language adapter) — a real,
dedicated Akan adapter, not a repurposed slot, but measured at 74.0%
WER / 37.7% CER on the same test set (statistically the same as
w2v-bert-2.0 above) while requiring a 3.86GB download and taking
30+ minutes to transcribe just 30 short clips on this CPU. Same
accuracy tier, far worse practicality — not worth the tradeoff, so it
was not integrated.

### Fuzzy book-name/structural-word matching (`reference_extractor.py`)

Live testing against this offline `w2vbert` backend (its 73.6% WER
means real garbling, not just occasional misses) surfaced a real gap:
exact-string alias matching missed book names the moment the ASR
output was even one character off — e.g. it transcribed "Genesis" as
**"gyenisis"** instead of the recognized "gyenesis," so the reference
resolved to nothing at all despite the rest of the utterance (chapter/
verse numbers) being perfectly readable.

Fix: `_fuzzy_lookup()` in `reference_extractor.py` adds an approximate-
match fallback (via `rapidfuzz`), tried only when the exact lookup
already failed — for both book names (the token scanner) and
structural words ("ti"/"nkyekyɛmu" and their variants, in
`_text_normalise`).

**Deliberately conservative**, because a real false-positive risk was
found and measured directly, not assumed: at the similarity threshold
needed to catch heavier garbling, ordinary sermon words started
colliding with book names — "father" scored 67 against "esther,"
"nation" scored 67 against "lamentations," the *same* range as the
genuine fix needed for "kyeɛmu" (a garbled "nkyekyemu") at 67. Ratio
alone can't safely separate a real typo from a coincidentally-similar
common word. Two guards, both required:
- **Minimum candidate length: 6 characters.** Short candidates
  ("yes," "mar," "yak") are exactly where the false-positive risk from
  the book-alias exclusions elsewhere in `aliases_twi.py` lives —
  gating on length keeps those out of fuzzy matching entirely,
  regardless of ratio.
- **Minimum similarity ratio: 82%.** High enough to exclude the
  "father"/"nation" collisions (67%) while still catching real ASR
  typos ("gyenisis" vs "gyenesis" = 88%, "nkyikyemu" vs "nkyekyemu" =
  89%). This means some heavier garbling (e.g. "kyeɛmu," missing a
  whole syllable) still won't resolve — a deliberate tradeoff, not an
  oversight: a wrong verse shown to a congregation is worse than a
  missed one.

Also fixed along the way: an early version of the ambiguity check
compared raw fuzzy-match scores between candidates, which wrongly
rejected cases where two *different spellings of the same book*
both scored high (e.g. "collossians" and "colosians" — both mean
Colossians, but scored 90.9 and 90.0, triggering a false "too
ambiguous" rejection). Fixed to compare the actual resolved
book/value, not the raw alias string — only rejects when close-scoring
candidates disagree on the actual answer.

**Verified, not just implemented**: a full-corpus test built a
simulated one-character typo for every book with an alias long enough
to be eligible for fuzzy matching (38 of 66 — the other 28 only have
short abbreviations, excluded from fuzzy matching by design) and
confirmed all 38 correctly resolve to the right book. The existing
29-case self-test suite still passes unchanged, and explicit
false-positive checks (ordinary sentences using "father," "nation,"
"yes," "ate") confirm none of them trigger a false match.

---

## Log file

All transcription events are logged to `bible_ai.log` in the project
root. Check this file for debugging audio issues, model load times, or
confidence scores on individual chunks.
