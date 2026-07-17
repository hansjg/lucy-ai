"""Speech-to-text: faster-whisper with the same anti-hallucination recipe
Lucy uses (VAD filter, no cross-segment conditioning, per-segment
no_speech/logprob guards)."""
import io, os, tempfile, threading, wave

import config

# Windows can't create symlinks without Developer Mode — make HF copy instead
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

# CUDA DLL paths must be registered before faster_whisper imports
for _p in config.CUDA_DLL_PATHS:
    if os.path.exists(_p):
        os.add_dll_directory(_p)

from faster_whisper import WhisperModel

state = "loading"        # loading | ready | error
model = None
model_name = "-"

# Whisper's signature artifact on music/noise: one short phrase looped
# ("I'm sorry, I'm sorry, I'm sorry."). Anything that is just the same
# phrase 3+ times is junk, not speech.
import re
_LOOP_RE = re.compile(r"^(.{2,30}?)(?:[,.!?\s]+\1){2,}[,.!?\s]*$", re.IGNORECASE)


def is_loop_junk(text: str) -> bool:
    return bool(_LOOP_RE.match(text))


# the streamer and one-shot question transcription share one GPU model
_model_lock = threading.Lock()


def _load():
    global state, model, model_name
    for name, compute, device in config.WHISPER_CHAIN:
        try:
            print(f"[stt] loading Whisper ({name}/{compute}/{device})...")
            m = WhisperModel(name, compute_type=compute, device=device)
            model, model_name, state = m, f"{name}@{device}", "ready"
            print(f"[stt] ready: {model_name}")
            return
        except Exception as e:
            print(f"[stt]   {name}/{device} failed ({str(e)[:120]}) - trying next")
    state = "error"
    print("[stt] no Whisper model could be loaded — transcription disabled")


def load_in_background():
    threading.Thread(target=_load, daemon=True).start()


def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(config.SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def transcribe(pcm: bytes) -> str:
    """Raw int16 mono PCM -> text. Returns '' for silence/noise/junk."""
    if model is None:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(pcm_to_wav(pcm))
        tmp = f.name
    try:
        with _model_lock:
            segs, _ = model.transcribe(
                tmp,
                beam_size=config.STT_BEAM,
                language="en",
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                initial_prompt=config.STT_HINTS,
                condition_on_previous_text=False,   # curbs hallucination loops
            )
            parts = []
            for s in segs:
                # hallucination guard: whisper invents phrases on borderline audio
                if s.no_speech_prob > 0.66 or s.avg_logprob < -1.2:
                    continue
                parts.append(s.text)
        text = " ".join(p.strip() for p in parts).strip()
        if _LOOP_RE.match(text):
            return ""
        return text
    finally:
        os.unlink(tmp)


def transcribe_words(pcm: bytes) -> list:
    """Raw PCM -> [{'w': word, 's': start, 'e': end}] with times relative to
    the buffer start. Greedy decode: the streamer re-transcribes every second,
    so agreement between passes replaces beam search."""
    if model is None:
        return []
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(pcm_to_wav(pcm))
        tmp = f.name
    try:
        with _model_lock:
            segs, _ = model.transcribe(
                tmp,
                beam_size=1,
                language="en",
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                word_timestamps=True,
                initial_prompt=config.STT_HINTS,
                condition_on_previous_text=False,
            )
            words = []
            for s in segs:
                if s.no_speech_prob > 0.66 or s.avg_logprob < -1.2:
                    continue
                for w in (s.words or []):
                    words.append({"w": w.word, "s": w.start, "e": w.end})
        return words
    finally:
        os.unlink(tmp)
