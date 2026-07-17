import os, asyncio, tempfile
from lucy.core import config
from lucy.shared.plugin_base import LucyPlugin

# Windows can't create symlinks without Developer Mode — make HF copy instead
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

# CUDA DLL paths must be registered before faster_whisper imports
for _p in config.CUDA_DLL_PATHS:
    if os.path.exists(_p):
        os.add_dll_directory(_p)

from faster_whisper import WhisperModel


class Plugin(LucyPlugin):
    model = None
    model_name = "?"

    async def start(self):
        def _load():
            for name, compute, device in config.WHISPER_CHAIN:
                try:
                    print(f"Loading Whisper STT ({name}/{compute}/{device})...")
                    m = WhisperModel(name, compute_type=compute, device=device)
                    print(f"Whisper ready: {name} on {device}")
                    return m, name
                except Exception as e:
                    print(f"  {name}/{device} failed ({str(e)[:120]}) - trying next")
            raise RuntimeError("no Whisper model could be loaded")
        self.model, self.model_name = await asyncio.to_thread(_load)

    async def call(self, action, **params):
        if action == "transcribe":
            return await asyncio.to_thread(self._transcribe, params["wav_bytes"],
                                           params.get("hints") or "")
        raise NotImplementedError(action)

    def _transcribe(self, wav_bytes, hints=""):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_bytes)
            tmp = f.name
        try:
            segs, _ = self.model.transcribe(
                tmp,
                beam_size=5,
                language="en",
                vad_filter=True,   # strips silence/noise — big accuracy win on mic audio
                vad_parameters={"min_silence_duration_ms": 300},
                initial_prompt=(config.STT_HINTS + " " + hints).strip(),
                condition_on_previous_text=False,   # curbs hallucination loops
            )
            parts = []
            for s in segs:
                # hallucination guard: whisper invents phrases on borderline audio
                if s.no_speech_prob > 0.66 or s.avg_logprob < -1.2:
                    continue
                parts.append(s.text)
            return " ".join(p.strip() for p in parts).strip()
        finally:
            os.unlink(tmp)
