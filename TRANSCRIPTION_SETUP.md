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

---

## Log file

All transcription events are logged to `bible_ai.log` in the project
root. Check this file for debugging audio issues, model load times, or
confidence scores on individual chunks.
