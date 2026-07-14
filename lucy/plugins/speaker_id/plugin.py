"""Voice identity — fingerprints utterances with a speaker-embedding model
(WeSpeaker CAM++, 512-dim) and matches them against enrolled profiles in
data/voices.json. Profiles improve over time: each recognized utterance
nudges the stored embedding toward the speaker's true voice (EMA).
"""
import io, json, wave, asyncio, datetime
import numpy as np
from lucy.core import config
from lucy.shared.plugin_base import LucyPlugin


def _wav_to_float32(wav_bytes):
    """16kHz mono PCM16 wav (what the browser sends) -> float32 [-1, 1]."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return rate, pcm.astype(np.float32) / 32768.0


def _cos(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class Plugin(LucyPlugin):
    extractor = None
    profiles = []   # [{name, embedding, samples, created, updated}]

    async def start(self):
        if not config.SPEAKER_MODEL_PATH.exists():
            print(f"Speaker model missing at {config.SPEAKER_MODEL_PATH} — voice identity off")
            raise RuntimeError("speaker model not downloaded")
        import sherpa_onnx
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(config.SPEAKER_MODEL_PATH), num_threads=2)
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self._load_profiles()
        print(f"Voice identity ready — {len(self.profiles)} enrolled voice(s)")

    # ── profile store ─────────────────────────────────────
    def _load_profiles(self):
        if config.VOICES_PATH.exists():
            self.profiles = json.loads(config.VOICES_PATH.read_text(encoding="utf-8"))
        else:
            self.profiles = []

    def _save_profiles(self):
        config.VOICES_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.VOICES_PATH.write_text(json.dumps(self.profiles), encoding="utf-8")

    # ── embedding ─────────────────────────────────────────
    def _embed(self, wav_bytes):
        rate, samples = _wav_to_float32(wav_bytes)
        if len(samples) < rate * config.SPEAKER_MIN_SECS:
            return None
        s = self.extractor.create_stream()
        s.accept_waveform(rate, samples)
        s.input_finished()
        return list(self.extractor.compute(s))

    # ── actions ───────────────────────────────────────────
    async def call(self, action, **params):
        return await asyncio.to_thread(self._call_sync, action, params)

    def _call_sync(self, action, params):
        if action == "identify":
            emb = self._embed(params["wav_bytes"])
            if emb is None:
                return {"speaker": None, "reason": "too short"}
            best, best_sim = None, -1.0
            for p in self.profiles:
                sim = _cos(emb, p["embedding"])
                if sim > best_sim:
                    best, best_sim = p, sim
            if best and best_sim >= config.SPEAKER_THRESHOLD:
                # EMA update: profiles track the voice as it's heard more
                old = np.asarray(best["embedding"])
                new = 0.85 * old + 0.15 * np.asarray(emb)
                best["embedding"] = list(new / (np.linalg.norm(new) + 1e-9) * np.linalg.norm(old))
                best["samples"] = best.get("samples", 1) + 1
                best["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
                self._save_profiles()
                return {"speaker": best["name"], "score": round(best_sim, 3)}
            return {"speaker": None, "closest": best["name"] if best else None,
                    "score": round(best_sim, 3)}

        if action == "enroll":
            name = str(params["name"]).strip()[:24]
            emb = self._embed(params["wav_bytes"])
            if emb is None:
                return {"ok": False, "error": "utterance too short to fingerprint"}
            existing = next((p for p in self.profiles if p["name"].lower() == name.lower()), None)
            now = datetime.datetime.now().isoformat(timespec="seconds")
            if existing:
                old = np.asarray(existing["embedding"])
                new = 0.5 * old + 0.5 * np.asarray(emb)
                existing["embedding"] = list(new)
                existing["samples"] = existing.get("samples", 1) + 1
                existing["updated"] = now
            else:
                self.profiles.append({"name": name, "embedding": emb, "samples": 1,
                                      "created": now, "updated": now})
            self._save_profiles()
            return {"ok": True, "name": name, "voices": len(self.profiles)}

        if action == "list":
            return [{"name": p["name"], "samples": p.get("samples", 1),
                     "updated": p.get("updated")} for p in self.profiles]

        if action == "forget":
            name = str(params["name"]).strip().lower()
            before = len(self.profiles)
            self.profiles = [p for p in self.profiles if p["name"].lower() != name]
            self._save_profiles()
            return {"ok": True, "removed": before - len(self.profiles)}

        raise NotImplementedError(action)
