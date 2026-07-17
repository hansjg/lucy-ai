"""FastAPI server — the pretty PowerShell face of God Ears on :8100."""
import asyncio, threading, time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import audio
import brain
import config
import feed
import pipeline
import stt
import streamer
import transcripts

ears: audio.Ears = None
stream: streamer.Streamer = None
uvicorn_server = None      # set by god_ears.py so /api/control can stop it


@asynccontextmanager
async def lifespan(app):
    global ears, stream
    stt.load_in_background()
    transcripts.start()
    asyncio.create_task(brain.resolve())
    stream = streamer.Streamer()
    stream.start()
    ears = audio.Ears(on_segment=pipeline.on_segment, on_wake=pipeline.on_wake,
                      on_frame=stream.add)
    pipeline.init(ears)
    ears.start()          # auto-run: God Ears starts hearing immediately
    feed.push("system", "God Ears online — listening")
    yield
    ears.stop()
    transcripts.stop()


app = FastAPI(lifespan=lifespan)


class ControlReq(BaseModel):
    action: str            # run | pause | terminate


class AskReq(BaseModel):
    question: str


@app.get("/")
async def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/status")
async def status():
    return {
        "ears": ears.status() if ears else {"state": "starting"},
        "stt": {"state": stt.state, "model": stt.model_name},
        "llm": brain.model,
        "today": transcripts.stats(),
        "sync": transcripts.sync_status(),
        "queue": pipeline.queue_depth(),
        "stream": {"buffer_s": stream.buffer_s if stream else 0},
    }


@app.post("/api/control")
async def control(req: ControlReq):
    if req.action == "run":
        ears.run()
        feed.push("system", "listening resumed")
    elif req.action == "pause":
        ears.pause()
        feed.push("system", "listening paused")
    elif req.action == "terminate":
        feed.push("system", "terminating — final cloud sync, then goodbye")

        def _die():
            time.sleep(0.7)               # let the response and feed poll land
            transcripts.stop()            # final push to cloud
            if uvicorn_server:
                uvicorn_server.should_exit = True
        threading.Thread(target=_die, daemon=True).start()
    return await status()


@app.post("/api/ask")
async def ask(req: AskReq):
    q = req.question.strip()

    async def gen():
        try:
            async for tok in brain.ask(q):
                yield tok
        except Exception as e:
            yield f"\n[brain unreachable: {e}]"
    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.get("/api/feed")
async def get_feed(after: int = 0):
    return {"events": feed.since(after),
            "live": stream.partial if stream else ""}


@app.get("/api/transcript", response_class=PlainTextResponse)
async def transcript(date: str = None):
    return transcripts.read_day(date) or f"(no transcript for {date or 'today'})"


@app.get("/api/days")
async def days():
    return {"days": transcripts.list_days()}


@app.post("/api/sync", response_class=PlainTextResponse)
async def sync():
    return transcripts.sync_now()


app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
