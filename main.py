import os, gc, json, datetime, base64, tempfile, asyncio, re

# Add CUDA DLL search paths for faster-whisper on Windows
_cuda_paths = [
    r"C:\Users\hansj\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\nvidia\cublas\bin",
    r"C:\Users\hansj\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\site-packages\nvidia\cudnn\bin",
    r"C:\Users\hansj\AppData\Local\Programs\Ollama\lib\ollama\cuda_v12",
]
for _p in _cuda_paths:
    if os.path.exists(_p):
        os.add_dll_directory(_p)
from faster_whisper import WhisperModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import edge_tts
import httpx
import requests
import uvicorn

# ─── Config ───────────────────────────────────────────────
WHISPER_MODEL   = "base"
OLLAMA_URL      = "http://localhost:11434/api/chat"
LLM_MODEL       = "qwen2.5vl:3b"
VISION_MODEL    = "minicpm-v"
TTS_VOICE       = "en-US-AvaNeural"
MEMORY_DIR      = "ai_memory"
SUMMARY_DIR     = os.path.join(MEMORY_DIR, "summaries")
MAX_DAILY_TURNS = 12

os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(SUMMARY_DIR, exist_ok=True)
os.makedirs("static", exist_ok=True)

print("Loading Whisper STT…")
try:
    stt = WhisperModel(WHISPER_MODEL, compute_type="float16", device="cuda")
    print("Whisper running on GPU")
except Exception as e:
    print(f"GPU failed ({e}), falling back to CPU")
    stt = WhisperModel(WHISPER_MODEL, compute_type="int8", device="cpu")
print("Ready - visit http://localhost:8000")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Memory ───────────────────────────────────────────────
def get_today_path():
    return os.path.join(MEMORY_DIR, f"{datetime.date.today().isoformat()}.json")

def load_today_memory():
    p = get_today_path()
    return json.load(open(p, "r", encoding="utf-8")) if os.path.exists(p) else []

def save_today_memory(history):
    with open(get_today_path(), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def get_time_context():
    now = datetime.datetime.now()
    return f"Current Date & Time: {now.strftime('%A, %B %d, %Y at %H:%M')}. "

def get_past_context():
    ctx = []
    today = load_today_memory()
    if today:
        ctx.extend([f"User: {t['user']}\nAI: {t['ai']}" for t in today])
    summaries = sorted([f for f in os.listdir(SUMMARY_DIR) if f.endswith(".md")], reverse=True)
    for s in summaries[:2]:
        with open(os.path.join(SUMMARY_DIR, s), "r", encoding="utf-8") as f:
            ctx.append(f.read().strip())
    return "\n\n".join(ctx) if ctx else "No past conversations recorded."

def summarize_daily_log():
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    y_path = os.path.join(MEMORY_DIR, f"{yesterday}.json")
    s_path = os.path.join(SUMMARY_DIR, f"{datetime.date.today().strftime('%Y-%m')}_summary.md")
    if not os.path.exists(y_path) or os.path.exists(s_path):
        return
    try:
        logs = json.load(open(y_path, "r", encoding="utf-8"))
        text = " ".join([f"U: {l['user']} AI: {l['ai']}" for l in logs])
        requests.post(OLLAMA_URL, json={
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": f"Summarize into 3-4 key points:\n{text}"}],
            "stream": False
        }, timeout=60)
    except Exception:
        pass

# ─── Streaming LLM ────────────────────────────────────────
async def stream_ollama(user_text, image_b64=None):
    system_prompt = (
        f"{get_time_context()}"
        "You are Lucy, a helpful AI assistant with a calm, natural female voice. "
        "Talk like a real person — casual, direct, warm, no filler phrases. "
        "Keep replies short unless the user asks for detail. "
        "Never use numbered lists, bullet points, asterisks, emojis, or markdown formatting. "
        "When listing options, use natural connectors like 'first', 'then', 'or', 'and' instead of numbers. "
        f"Past context: {get_past_context()}."
    )
    if image_b64:
        content = (user_text or "Briefly describe what you see.") + " Be concise."
        msg = {"role": "user", "content": content, "images": [image_b64]}
    else:
        msg = {"role": "user", "content": user_text}

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, msg],
        "stream": True,
        "options": {"temperature": 0.8}
    }
    async with httpx.AsyncClient(timeout=90) as client:
        async with client.stream("POST", OLLAMA_URL, json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    done  = data.get("done", False)
                    if token:
                        yield token
                    if done:
                        break
                except Exception:
                    continue

# ─── TTS & STT ────────────────────────────────────────────
def clean_for_tts(text):
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)  # **bold**, *italic*
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text)     # __bold__, _italic_
    text = re.sub(r'`{1,3}.*?`{1,3}', '', text)          # `code`
    text = re.sub(r'#+\s*', '', text)                     # ## headings
    text = re.sub(r'^\s*\d+[\.\)]\s*', '', text)         # leading "1. " "2) " etc.
    text = re.sub(r'\n\s*\d+[\.\)]\s*', ' ', text)       # inline "\n2. "
    text = re.sub(r'\s+', ' ', text).strip()
    return text

async def synthesize(text):
    communicate = edge_tts.Communicate(clean_for_tts(text), TTS_VOICE)
    audio = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return base64.b64encode(audio).decode()

def transcribe_bytes(wav_bytes):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        tmp = f.name
    try:
        segs, _ = stt.transcribe(tmp, beam_size=3, language="en")
        return " ".join(s.text for s in segs).strip()
    finally:
        os.unlink(tmp)

# ─── Routes ───────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    history = load_today_memory()
    try:
        while True:
            msg = await ws.receive_json()

            msg_type = msg.get("type")

            if msg_type == "text":
                text = msg.get("text", "").strip()
                if not text:
                    continue
                image_b64 = msg.get("image")
                print(f"Text msg received. Image attached: {bool(image_b64)}, size: {len(image_b64) if image_b64 else 0} chars")
                await ws.send_json({"type": "status", "state": "thinking"})

            elif msg_type == "audio":
                await ws.send_json({"type": "status", "state": "thinking"})
                text = await asyncio.to_thread(transcribe_bytes, base64.b64decode(msg["data"]))
                if not text:
                    await ws.send_json({"type": "status", "state": "idle"})
                    continue
                image_b64 = msg.get("image")
                await ws.send_json({"type": "transcript", "text": text})

            else:
                continue

            full_reply = ""
            sentence_buf = ""
            tts_buf = ""
            first_token = True
            audio_tasks = []
            MIN_TTS = 20   # minimum chars before flushing any chunk

            async def send_audio_when_ready(tts_task):
                data = await tts_task
                await ws.send_json({"type": "audio_chunk", "data": data})

            def flush_tts(text):
                cleaned = clean_for_tts(text)
                if not cleaned:
                    return
                t = asyncio.create_task(synthesize(cleaned))
                audio_tasks.append(asyncio.create_task(send_audio_when_ready(t)))

            async for token in stream_ollama(text, image_b64):
                if first_token:
                    await ws.send_json({"type": "reply_start"})
                    first_token = False

                full_reply   += token
                sentence_buf += token
                await ws.send_json({"type": "token", "text": token})

                # flush on sentence end or newline — never on comma alone
                is_end = bool(re.search(r'(?<!\d)[.!?…]["»]?\s*$', sentence_buf))
                is_newline = '\n' in sentence_buf

                if is_end or is_newline:
                    chunk = sentence_buf.strip()
                    sentence_buf = ""
                    if chunk:
                        tts_buf += (" " if tts_buf else "") + chunk
                        if len(tts_buf) >= MIN_TTS:
                            flush_tts(tts_buf)
                            tts_buf = ""

            # flush anything remaining
            tail = (tts_buf + " " + sentence_buf).strip() if sentence_buf.strip() else tts_buf.strip()
            if tail:
                flush_tts(tail)

            await ws.send_json({"type": "reply_end"})

            # Wait for all audio to finish sending
            if audio_tasks:
                await asyncio.gather(*audio_tasks)

            history.append({"user": text, "ai": full_reply})
            if len(history) > MAX_DAILY_TURNS * 2:
                history = history[-MAX_DAILY_TURNS:]
            save_today_memory(history)
            gc.collect()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await ws.send_json({"type": "error", "message": repr(e)})
        except Exception:
            pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
