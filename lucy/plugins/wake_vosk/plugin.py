import os, json, time, threading
from lucy.core import config
from lucy.shared.plugin_base import LucyPlugin


class Plugin(LucyPlugin):
    """Listens on the server microphone and emits a 'wake' event when
    one of the wake phrases is spotted. Runs as a daemon thread."""

    async def start(self):
        try:
            from vosk import Model, KaldiRecognizer
        except ImportError:
            print("Vosk not installed — wake word disabled")
            return

        if not os.path.exists(config.VOSK_MODEL_PATH):
            print(f"Vosk model not found at {config.VOSK_MODEL_PATH} — wake word disabled")
            return

        model = Model(str(config.VOSK_MODEL_PATH))
        keywords = config.WAKE_PHRASES + ["[unk]"]
        self.rec = KaldiRecognizer(model, 16000, json.dumps(keywords))
        self.rec.SetWords(False)
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        import pyaudio
        try:
            pa = pyaudio.PyAudio()
            stream = pa.open(rate=16000, channels=1, format=pyaudio.paInt16,
                             input=True, frames_per_buffer=4000)
            print("Wake word listener active — say 'Hey Lucy' to trigger")
            last_wake = 0
            while True:
                pcm = stream.read(4000, exception_on_overflow=False)
                if self.rec.AcceptWaveform(pcm):
                    result = json.loads(self.rec.Result()).get("text", "")
                else:
                    result = json.loads(self.rec.PartialResult()).get("partial", "")

                if not result:
                    continue

                matched = any(phrase in result for phrase in config.WAKE_PHRASES)
                now = time.time()
                if matched and (now - last_wake) > config.WAKE_COOLDOWN:
                    last_wake = now
                    print(f"Wake word detected! '{result}'")
                    self.ctx.emit("wake")
        except Exception as e:
            print(f"Wake listener error: {e}")
