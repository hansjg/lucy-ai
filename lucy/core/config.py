from pathlib import Path

# Repo root — everything resolves from here so Lucy can be launched from any cwd
ROOT = Path(__file__).resolve().parents[2]

# ── Server ────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 8000
STATIC_DIR = ROOT / "static"

# ── Node network (Phase 1) ────────────────────────────────
DATA_DIR        = ROOT / "data"
DB_PATH         = DATA_DIR / "lucy.db"
NODE_TOKEN_PATH = DATA_DIR / "node_token.txt"
NODE_STALE_SECS = 15   # heartbeat older than this = node treated as away
TASK_TIMEOUT_S  = 30   # default remote task timeout

# ── Reliability (Phase 3) ─────────────────────────────────
TASK_RETRIES    = 2     # extra attempts after the first failure
RETRY_BACKOFF_S = 1.5   # base backoff between attempts
BATTERY_PENALTY     = 25   # score penalty for running on battery
LOW_BATTERY_PENALTY = 50   # extra penalty below 20%
MDNS_SERVICE = "_lucy._tcp.local."

# ── Planner (Phase 2) ─────────────────────────────────────
# Capabilities the planner may invoke on its own. files.transfer is allowed
# because it can only touch the dedicated shared folders on each device.
AUTOPLAN_CAPS = {"monitoring.system", "files.transfer"}

# ── User settings & device wake ───────────────────────────
SETTINGS_PATH    = DATA_DIR / "settings.json"      # UI-editable toggles
KNOWN_NODES_PATH = DATA_DIR / "known_nodes.json"   # name -> ip/mac for WoL
WAKE_WAIT_S      = 45   # how long Lucy watches for a woken device to join

# ── Notifications (phone) ─────────────────────────────────
# ntfy needs no account: the phone subscribes to one long random topic and
# Lucy POSTs to it. On the public server the topic IS the credential.
NTFY_URL = "https://ntfy.sh"

# ── People: voice profiles & private spaces ───────────────
PROFILES_PATH = DATA_DIR / "profiles.json"       # who Lucy knows, and their space
SPACES_DIR    = DATA_DIR / "shared" / "_spaces"  # one folder per person

# Voice tells Lucy WHO is talking. It does not unlock anything by itself: a
# recording can pass it, and relatives sound alike (SPEAKER_THRESHOLD 0.65 sits
# only ~0.10 above a measured impostor). So the bar rises once more than one
# person is enrolled, and reaching into someone else's space always needs that
# person's consent rather than a good-enough voice score.
SPEAKER_THRESHOLD_MULTI = 0.75   # accept score when 2+ people are enrolled
SPEAKER_COLLISION       = 0.70   # a new voice this close to an existing one is ambiguous
SPEAKER_UPDATE_MIN      = 0.80   # only learn from a clearly confident match...
SPEAKER_UPDATE_MARGIN   = 0.12   # ...that also beats the runner-up by this much
ENROLL_SAMPLES_MULTI    = 3      # samples required to enrol while sharing Lucy
ACCESS_REQUEST_TTL_S    = 600    # unanswered consent request expires (silence = no)

# ── File sharing (ecosystem) ──────────────────────────────
SHARED_DIR  = DATA_DIR / "shared"   # the core's shared drop folder
MAX_FILE_MB = 200

# ── CUDA DLL search paths for faster-whisper on Windows ──
CUDA_DLL_PATHS = [
    r"C:\Users\hansj\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\nvidia\cublas\bin",
    r"C:\Users\hansj\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\nvidia\cudnn\bin",
    r"C:\Users\hansj\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12",
]

# ── Models / voices ───────────────────────────────────────
# STT: try the ChatGPT-class model first, degrade gracefully on low VRAM.
# (model_name, compute_type, device) — first one that loads wins.
WHISPER_CHAIN = [
    ("large-v3-turbo", "int8_float16", "cuda"),   # ~1.2GB VRAM, near large-v3 quality
    ("medium",         "int8_float16", "cuda"),
    ("base",           "float16",      "cuda"),
    ("base",           "int8",         "cpu"),
]
# Domain words Whisper should spell correctly; speaker names are added live.
STT_HINTS = "Lucy, Vivobook, dexalab, node, GPU, VRAM."
OLLAMA_URL    = "http://localhost:11434/api/chat"
# Preference-ordered — the plugin uses the first one Ollama actually has,
# so a missing/broken upgrade silently falls back to the old brain.
LLM_CHAIN     = ["gemma3:4b", "qwen2.5vl:3b"]
LLM_MODEL     = LLM_CHAIN[-1]   # legacy references / guaranteed-installed fallback
TTS_VOICE     = "en-US-AvaNeural"

# ── Vision (LocateAnything live detection) ────────────────
VISION_MODEL_ID       = "nvidia/LocateAnything-3B"
VISION_MAX_NEW_TOKENS = 256    # ~12 tokens per box; also caps worst-case latency
VISION_DEFAULT_QUERY  = "all objects"
VISION_IDLE_UNLOAD_S  = 180    # free the 6GB card for Whisper/LLM when unused

# ── Speaker recognition (voice identity) ──────────────────
SPEAKER_MODEL_PATH = DATA_DIR / "models" / "speaker_campp.onnx"
VOICES_PATH        = DATA_DIR / "voices.json"
SPEAKER_THRESHOLD  = 0.65   # cosine sim to accept identity (measured: genuine ~0.85, impostor ~0.55)
SPEAKER_MIN_SECS   = 0.8    # utterances shorter than this aren't fingerprinted

# ── Memory ────────────────────────────────────────────────
MEMORY_DIR      = ROOT / "ai_memory"
SUMMARY_DIR     = MEMORY_DIR / "summaries"
MAX_DAILY_TURNS = 12

# ── Wake word (Vosk keyword spotting) ─────────────────────
VOSK_MODEL_PATH = ROOT / "vosk-model"
WAKE_PHRASES    = ["hey lucy", "hey luci", "a lucy", "hey lousy"]  # phonetic variants
WAKE_COOLDOWN   = 4  # seconds between triggers
