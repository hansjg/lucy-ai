# GOD EARS 👂

A standalone, always-listening AI. Completely separate from Lucy — its own
process, its own port (**http://localhost:8100**), its own data. It hears the
room all day, writes a timestamped transcript per day, syncs transcripts to
the cloud, and answers questions about what it heard — by voice ("hey lucy" /
"god ears") or by typing into its PowerShell-style terminal UI.

## Run

```
run.bat            ← starts Ollama if needed, launches God Ears, opens the UI
```

Want it to start by itself at login? Run `autostart_install.bat` once.
(To undo, delete "God Ears.lnk" from `shell:startup`.)

## The 3 buttons

| Button | What it does |
|---|---|
| ▶ **Run** | resume hearing after a pause |
| ⏸ **Pause** | stop hearing — mic input is discarded, nothing is transcribed |
| ⏹ **Terminate** | final cloud sync, then the process exits (restart via run.bat) |

God Ears **auto-runs**: launching it starts the listening immediately.

## Asking questions

- **Voice**: say *"hey lucy …question…"* or *"god ears …question…"*. A bare
  "hey lucy" gets a "Yes?" and it waits ~10 s for your question. Answers are
  spoken through the speakers.
- **Typed**: type into the terminal at http://localhost:8100. Commands:
  `/tail`, `/day 2026-07-10`, `/days`, `/sync`, `/clear`, `/help`.

> Heads-up: if regular Lucy (localhost:8000) is running at the same time, she
> also reacts to "hey lucy". Use **"god ears"** to address only this program,
> or trim `WAKE_PHRASES` in [config.py](config.py).

## Cloud sync — one manual step

God Ears deliberately doesn't talk to any cloud API. Instead it pushes/pulls
its daily transcript files to a **folder**, and your cloud client keeps that
folder synced:

1. Set up Google Drive for desktop / OneDrive / Dropbox (or an rclone mount).
2. In [config.py](config.py) set e.g.
   `SYNC_DIR = r"G:\My Drive\god-ears-transcripts"`.
3. That's it. Changed transcripts are pushed every 60 s and on Terminate;
   missing days are pulled back at startup (and via `/sync`).

Transcripts live locally in `data/transcripts/YYYY-MM-DD.md`.

## About the speech recognition

The notes pointed at NVIDIA **LocateAnything-3B** (NVlabs/Eagle) — that model
is a *vision* model (it locates objects in images) and can't transcribe
speech. God Ears instead uses the actual state of the art that runs on the
RTX 4050: **Whisper large-v3-turbo** on CUDA (the same proven chain Lucy
uses, falling back to smaller models if VRAM is tight). If you ever want to
try NVIDIA's real ASR line (Parakeet / Canary via NeMo), it's a drop-in
change in `stt.py`.

## Anti-hallucination design

- Whisper: VAD filter, no cross-segment conditioning, and per-segment
  `no_speech_prob` / `avg_logprob` guards — junk audio produces *nothing*
  rather than invented sentences.
- Answers: temperature 0.1, the model only sees real transcript excerpts,
  must cite timestamps like `[14:03:22]`, and is instructed to say
  **"I didn't hear that"** instead of guessing.
- God Ears mutes its own ears while speaking, so it never transcribes itself.

## Files

```
god_ears.py     entrypoint (uvicorn on :8100)
server.py       FastAPI endpoints (/api/status, /control, /ask, /feed, …)
audio.py        mic loop, utterance segmentation, Vosk wake-word
pipeline.py     transcribe → store → (answer + speak) worker
stt.py          faster-whisper chain + hallucination guards
brain.py        Ollama Q&A grounded in the day's transcripts
tts.py          edge-tts → speakers
transcripts.py  daily markdown files + folder cloud-sync
feed.py         live event feed for the UI
static/         the prettier-PowerShell terminal
```
