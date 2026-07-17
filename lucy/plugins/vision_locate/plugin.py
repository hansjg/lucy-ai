"""Real-time open-vocabulary detection — NVIDIA LocateAnything-3B.

The model runs in a dedicated worker process (see worker.py for why: torch's
and ctranslate2's bundled cuDNN builds clash inside one Windows process and
the first inference hard-kills the server). This plugin only manages that
process: spawn on first frame, JSON-lines RPC, kill after
VISION_IDLE_UNLOAD_S without a frame so the 6GB card goes back to
Whisper/LLM. A worker crash costs one detection, never the assistant.

detect never blocks on the spawn: while the worker is warming up it answers
{"state": "loading"} so the browser loop keeps polling and shows progress.
"""
import os, sys, json, time, asyncio

from lucy.core import config
from lucy.shared.plugin_base import LucyPlugin

_LOAD_TIMEOUT_S = 300     # spawn + imports + 4-bit quantized load
_DETECT_TIMEOUT_S = 90    # one frame, worst case (cold CUDA kernels)
_CRASH_WINDOW_S = 300     # this many seconds with...
_CRASH_LIMIT = 3          # ...this many worker deaths = sticky error


class Plugin(LucyPlugin):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.proc = None
        self.state = "cold"        # cold | loading | ready | error
        self.load_error = None
        self.last_used = 0.0
        self.crashes = []          # timestamps of unexpected worker deaths
        self._rpc_lock = asyncio.Lock()

    async def start(self):
        # No weights at boot — the first detect() call spawns the worker.
        asyncio.get_running_loop().create_task(self._idle_reaper())
        print("vision.locate lazy-ready - worker spawns on first frame")

    async def stop(self):
        await self._kill_worker()

    # ── actions ───────────────────────────────────────────
    async def call(self, action, **params):
        if action == "status":
            return {"state": self.state, "error": self.load_error}

        if action == "release":
            await self._kill_worker()
            self.state = "cold"        # also clears a sticky load error
            self.load_error = None
            self.crashes = []
            return {"state": "cold"}

        if action == "detect":
            if self.state == "ready":
                res = await self._rpc_detect(params)
                if res is not None:
                    return res
                # worker died — state was downgraded, fall through
            if self.state == "cold":
                self.state = "loading"
                self.load_error = None
                asyncio.get_running_loop().create_task(self._spawn_task())
            if self.state == "error":
                return {"state": "error", "error": self.load_error}
            return {"state": "loading"}

        raise NotImplementedError(action)

    # ── worker lifecycle ──────────────────────────────────
    async def _spawn_task(self):
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._stderr_log = open(config.DATA_DIR / "vision_worker.log", "ab")
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", "-m", "lucy.plugins.vision_locate.worker",
                cwd=str(config.ROOT),
                env={**os.environ,
                     "VISION_MODEL_ID": config.VISION_MODEL_ID,
                     "VISION_MAX_NEW_TOKENS": str(config.VISION_MAX_NEW_TOKENS)},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=self._stderr_log,
            )
            line = await asyncio.wait_for(self.proc.stdout.readline(),
                                          _LOAD_TIMEOUT_S)
            msg = json.loads(line) if line else {}
            if msg.get("event") != "ready":
                raise RuntimeError(f"worker exited during load "
                                   f"(rc={self.proc.returncode})")
            self.state = "ready"
            self.last_used = time.time()
            print("vision.locate: worker ready")
        except Exception as e:
            self.state = "error"
            self.load_error = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"vision.locate: worker failed to start - {self.load_error}")
            await self._kill_worker()

    async def _kill_worker(self):
        proc, self.proc = self.proc, None
        log, self._stderr_log = getattr(self, "_stderr_log", None), None
        if proc is not None and proc.returncode is None:
            try:
                proc.stdin.write(b'{"cmd": "exit"}\n')
                await proc.stdin.drain()
                await asyncio.wait_for(proc.wait(), 5)
            except Exception:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        if log is not None:
            try:
                log.close()
            except Exception:
                pass

    def _note_crash(self):
        now = time.time()
        self.crashes = [t for t in self.crashes if now - t < _CRASH_WINDOW_S]
        self.crashes.append(now)
        if len(self.crashes) >= _CRASH_LIMIT:
            self.state = "error"
            self.load_error = (f"vision worker crashed {len(self.crashes)} "
                               f"times in {_CRASH_WINDOW_S // 60} min — "
                               "toggle detection off and on to retry")
        else:
            self.state = "cold"    # next frame respawns it

    async def _rpc_detect(self, params):
        """One request/response on the worker pipe; None means the worker
        died and the caller should treat the frame as a (re)load trigger."""
        async with self._rpc_lock:
            if self.proc is None or self.proc.returncode is not None:
                self._note_crash()
                return None
            try:
                req = {"cmd": "detect",
                       "image_b64": params.get("image_b64", ""),
                       "query": ((params.get("query") or "").strip()
                                 or config.VISION_DEFAULT_QUERY)}
                self.proc.stdin.write(
                    json.dumps(req, ensure_ascii=True).encode() + b"\n")
                await self.proc.stdin.drain()
                line = await asyncio.wait_for(self.proc.stdout.readline(),
                                              _DETECT_TIMEOUT_S)
                if not line:
                    raise RuntimeError("worker pipe closed")
            except Exception as e:
                print(f"vision.locate: worker lost mid-detect ({e})")
                await self._kill_worker()
                self._note_crash()
                return None
            self.last_used = time.time()
            return json.loads(line)

    async def _idle_reaper(self):
        while True:
            await asyncio.sleep(30)
            if (self.state == "ready"
                    and time.time() - self.last_used > config.VISION_IDLE_UNLOAD_S):
                print("vision.locate: idle - releasing worker")
                await self._kill_worker()
                self.state = "cold"
