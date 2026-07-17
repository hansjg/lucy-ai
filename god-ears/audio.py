"""The ears: continuous microphone capture with utterance segmentation and
Vosk wake-word spotting.

One daemon thread owns the mic. An adaptive RMS gate (noise-floor tracking
with hysteresis + pre-roll) cuts the stream into utterances; every closed
utterance is handed to the pipeline. Vosk chews the same frames in parallel
looking for the wake phrases.

States: running (hearing) / paused (mic open, everything discarded).
"""
import json, math, struct, threading, time
from collections import deque

import config


def _rms(frame: bytes) -> float:
    n = len(frame) // 2
    if not n:
        return 0.0
    samples = struct.unpack(f"<{n}h", frame)
    return math.sqrt(sum(s * s for s in samples) / n)


class Ears:
    def __init__(self, on_segment, on_wake, on_frame=None):
        """on_segment(pcm, kind, ts) — question utterances (after a wake).
        on_wake(in_speech) — wake phrase spotted.
        on_frame(frame, rms) — every live frame, feeds the streaming
        transcriber which owns all ambient text."""
        self.on_segment = on_segment
        self.on_wake = on_wake
        self.on_frame = on_frame
        self.state = "starting"          # starting | running | paused
        self.tts_active = False          # pipeline sets this while speaking
        self.wake_ok = False
        self.noise_floor = 200.0
        self.in_speech = False
        self._wake_pending = False
        self._await_q_until = 0.0
        self._last_wake = 0.0
        self._stop = threading.Event()

    # ── controls ──────────────────────────────────────────
    def run(self):
        if self.state != "starting":
            self.state = "running"

    def pause(self):
        if self.state != "starting":
            self.state = "paused"

    def stop(self):
        self._stop.set()

    def await_question(self):
        self._await_q_until = time.time() + config.QUESTION_WINDOW_S

    def status(self):
        return {
            "state": self.state,
            "speaking": self.tts_active,
            "in_speech": self.in_speech,
            "noise_floor": round(self.noise_floor),
            "wake_ok": self.wake_ok,
        }

    # ── lifecycle ─────────────────────────────────────────
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _init_vosk(self):
        try:
            from vosk import Model, KaldiRecognizer
        except ImportError:
            print("[ears] vosk not installed — wake word disabled")
            return None
        if not config.VOSK_MODEL_PATH.exists():
            print(f"[ears] vosk model missing at {config.VOSK_MODEL_PATH} — wake word disabled")
            return None
        rec = KaldiRecognizer(Model(str(config.VOSK_MODEL_PATH)),
                              config.SAMPLE_RATE,
                              json.dumps(config.WAKE_PHRASES + ["[unk]"]))
        rec.SetWords(False)
        self.wake_ok = True
        return rec

    def _loop(self):
        import pyaudio

        frame_samples = config.SAMPLE_RATE * config.FRAME_MS // 1000
        frame_secs = config.FRAME_MS / 1000
        preroll = deque(maxlen=max(1, int(config.PREROLL_S / frame_secs)))

        rec = self._init_vosk()
        pa = pyaudio.PyAudio()
        try:
            stream = pa.open(rate=config.SAMPLE_RATE, channels=1,
                             format=pyaudio.paInt16, input=True,
                             frames_per_buffer=frame_samples)
        except OSError as e:
            print(f"[ears] cannot open microphone: {e}")
            self.state = "error"
            return

        self.state = "running"
        print("[ears] listening — say 'hey lucy' or 'god ears'")

        seg, quiet, seg_start = [], 0.0, 0.0

        def _emit(frames, start_ts, quiet_tail):
            voiced = len(frames) * frame_secs - quiet_tail
            if voiced < config.MIN_SEGMENT_S:
                return
            if not (self._wake_pending or time.time() < self._await_q_until):
                return   # ambient audio is transcribed by the streamer
            self._wake_pending = False
            self._await_q_until = 0.0
            try:
                self.on_segment(b"".join(frames), "question", start_ts)
            except Exception as e:
                print(f"[ears] on_segment error: {e}")

        while not self._stop.is_set():
            frame = stream.read(frame_samples, exception_on_overflow=False)

            if self.state != "running" or self.tts_active:
                seg, quiet = [], 0.0
                self.in_speech = False
                preroll.clear()
                continue

            # ── wake spotting ─────────────────────────────
            # Final results only: partials refire on noise and cause
            # false "Yes?" interruptions in an always-on listener.
            if rec is not None:
                heard = ""
                if rec.AcceptWaveform(frame):
                    heard = json.loads(rec.Result()).get("text", "")
                now = time.time()
                if (heard and (now - self._last_wake) > config.WAKE_COOLDOWN
                        and any(p in heard for p in config.WAKE_PHRASES)):
                    self._last_wake = now
                    self._wake_pending = self.in_speech
                    print(f"[ears] wake! ({heard!r})")
                    try:
                        self.on_wake(self.in_speech)
                    except Exception as e:
                        print(f"[ears] on_wake error: {e}")

            # ── utterance segmentation (adaptive RMS gate) ─
            rms = _rms(frame)

            if self.on_frame:
                try:
                    self.on_frame(frame, rms)
                except Exception as e:
                    print(f"[ears] on_frame error: {e}")

            # Noise floor follows quiet levels immediately but rises only
            # very slowly — hours of continuous speech (a playing video)
            # must not inflate it and choke the gate.
            if rms < self.noise_floor:
                self.noise_floor = max(40.0, rms)
            else:
                self.noise_floor = min(500.0, self.noise_floor + (rms - self.noise_floor) * 0.005)

            if not self.in_speech:
                preroll.append(frame)
                if rms > max(config.SPEECH_RMS_MIN, self.noise_floor * config.FLOOR_TRIGGER):
                    self.in_speech = True
                    seg = list(preroll)
                    quiet = 0.0
                    seg_start = time.time() - len(preroll) * frame_secs
            else:
                seg.append(frame)
                release = max(config.SPEECH_RMS_MIN * 0.75,
                              self.noise_floor * config.FLOOR_RELEASE)
                quiet = quiet + frame_secs if rms < release else 0.0

                if quiet >= config.SILENCE_END_S:
                    # natural pause — utterance over
                    self.in_speech = False
                    _emit(seg, seg_start, quiet)
                    seg = []
                    preroll.clear()
                elif len(seg) * frame_secs >= config.MAX_SEGMENT_S:
                    # speech hasn't paused (video / monologue): cut here and
                    # keep rolling, carrying a short overlap across the seam
                    _emit(seg, seg_start, 0.0)
                    keep = max(1, int(config.SEGMENT_OVERLAP_S / frame_secs))
                    seg = seg[-keep:]
                    seg_start = time.time() - keep * frame_secs
                    quiet = 0.0

        stream.stop_stream()
        stream.close()
        pa.terminate()
