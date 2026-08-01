from app.asr.transcriber import UltraLowLatencyTranscriber, Config
from app.retrieval.hybrid import HybridEngine
import sys
import os
import threading
import time
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

print("Step 1: Loading HybridEngine...")
engine = HybridEngine()
print("Engine ready.")

print("\nStep 2: Loading Whisper...")
t = UltraLowLatencyTranscriber(Config())
print("Whisper ready.")

print("\nStep 3: Starting mic stream...")
t.start()
print("Mic started. Listening for 5 seconds...")
time.sleep(5)
t.stop_stream()
print("\nSUCCESS — everything works together")
