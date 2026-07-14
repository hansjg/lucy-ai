"""God Ears configuration.

God Ears is a standalone always-listening transcriber + Q&A assistant.
It shares nothing at runtime with Lucy (localhost:8000) — it only reuses
the same model recipes that are proven to work on this machine.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── Server ────────────────────────────────────────────────
# Lucy owns :8000 — God Ears lives on its own port.
HOST = "127.0.0.1"
PORT = 8100
STATIC_DIR = ROOT / "static"

# ── Transcript storage ────────────────────────────────────
DATA_DIR       = ROOT / "data"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"   # one markdown file per day

# ── Cloud sync ────────────────────────────────────────────
# God Ears never talks to a cloud API itself. Point SYNC_DIR at any folder
# that a cloud client already keeps in sync (Google Drive for desktop,
# OneDrive, Dropbox, or an rclone mount) and God Ears will push its daily
# transcripts there and pull back any it doesn't have locally.
# None = sync disabled until you set it up.
SYNC_DIR = None            # e.g. r"G:\My Drive\god-ears-transcripts"
SYNC_INTERVAL_S = 60       # push today's file at most this often (when changed)

# ── Audio capture ─────────────────────────────────────────
# Tuned for near-real-time transcription of CONTINUOUS audio (a video
# playing, a long conversation), not just isolated utterances.
SAMPLE_RATE   = 16000
FRAME_MS      = 100        # mic read granularity
PREROLL_S     = 0.5        # audio kept from before speech was detected
SILENCE_END_S = 0.6        # this much quiet closes an utterance
MAX_SEGMENT_S = 10         # cut here and keep rolling — small chunks = low latency
SEGMENT_OVERLAP_S = 0.3    # carried across a rolling cut so no word is lost
MIN_SEGMENT_S = 0.3        # shorter than this = discarded as noise
SPEECH_RMS_MIN = 120       # absolute int16 RMS floor for "this is a voice"
FLOOR_TRIGGER  = 3.0       # speech starts at noise_floor * this
FLOOR_RELEASE  = 1.8       # speech ends below noise_floor * this

# ── STT — same chain Lucy uses (proven on the RTX 4050) ──
# The NVIDIA "LocateAnything-3B" model from the notes is a vision model
# (object grounding in images), not speech recognition — Whisper
# large-v3-turbo is the actual state of the art that fits this GPU.
CUDA_DLL_PATHS = [
    r"C:\Users\hansj\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\nvidia\cublas\bin",
    r"C:\Users\hansj\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\nvidia\cudnn\bin",
    r"C:\Users\hansj\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12",
]
WHISPER_CHAIN = [
    ("large-v3-turbo", "int8_float16", "cuda"),
    ("medium",         "int8_float16", "cuda"),
    ("base",           "float16",      "cuda"),
    ("base",           "int8",         "cpu"),
]
STT_HINTS = "Lucy, God Ears, Vivobook, dexalab."
STT_BEAM  = 2   # beam 5 is slower for near-identical accuracy on turbo

# ── Wake word (Vosk keyword spotting, reuses Lucy's model) ─
VOSK_MODEL_PATH = Path(r"D:\Users\ruxtg\vosk-model")
# "hey lucy" is the wake call per the notes; "god ears" variants also work.
# NOTE: if Lucy is running at the same time, she reacts to "hey lucy" too —
# use "god ears" to address only this program, or edit this list.
# Loose variants like "a lucy" / "god is" fire on ambient noise — keep the
# list tight; an always-on listener pays for every false wake with a "Yes?".
WAKE_PHRASES = [
    "hey lucy", "hey luci", "hey lousy",
    "god ears",
]
WAKE_COOLDOWN     = 4    # seconds between wake triggers
QUESTION_WINDOW_S = 10   # after a bare wake, wait this long for the question

# ── LLM (Ollama, same brain chain as Lucy) ────────────────
OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_CHAIN  = ["gemma3:4b", "qwen2.5vl:3b"]
QA_OPTIONS = {"temperature": 0.1, "num_ctx": 8192}   # cold + wide context = fewer hallucinations

# ── Retrieval (grounding answers in transcripts) ──────────
MAX_CONTEXT_CHARS = 12000   # transcript context budget per question
SEARCH_DAYS       = 7       # how many past days to search for relevant lines

# ── TTS ───────────────────────────────────────────────────
TTS_VOICE = "en-US-AvaNeural"
