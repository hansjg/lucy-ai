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

            ranked = sorted(((_cos(emb, p["embedding"]), p) for p in self.profiles),
                            key=lambda x: -x[0])
            if not ranked:
                return {"speaker": None, "score": 0.0}
            best_sim, best = ranked[0]
            runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
            margin = (best_sim - runner_up) if len(ranked) > 1 else 1.0

            # Sharing Lucy raises the bar. With one enrolled voice a false
            # accept only means she uses the wrong name; with several it also
            # decides whose files she reaches for — and relatives score close.
            threshold = (config.SPEAKER_THRESHOLD_MULTI if len(self.profiles) > 1
                         else config.SPEAKER_THRESHOLD)

            if best_sim < threshold:
                return {"speaker": None, "closest": best["name"],
                        "score": round(best_sim, 3), "reason": "below threshold"}

            # Two profiles this close cannot be told apart honestly. Naming one
            # anyway is how the wrong person gets addressed — and, in profiles
            # mode, pointed at someone else's space.
            if len(ranked) > 1 and margin < config.SPEAKER_UPDATE_MARGIN:
                return {"speaker": None, "closest": best["name"],
                        "score": round(best_sim, 3), "margin": round(margin, 3),
                        "reason": f"too close to {ranked[1][1]['name']}"}

            # Learn ONLY from a clearly confident, unambiguous match. The EMA
            # used to run on every accept, so a single borderline hit would drag
            # the profile toward the impostor and make the next one easier.
            if best_sim >= config.SPEAKER_UPDATE_MIN and margin >= config.SPEAKER_UPDATE_MARGIN:
                old = np.asarray(best["embedding"])
                new = 0.85 * old + 0.15 * np.asarray(emb)
                best["embedding"] = list(new / (np.linalg.norm(new) + 1e-9) * np.linalg.norm(old))
                best["samples"] = best.get("samples", 1) + 1
                best["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
                self._save_profiles()

            return {"speaker": best["name"], "score": round(best_sim, 3),
                    "margin": round(margin, 3)}

        if action == "enroll":
            name = str(params["name"]).strip()[:24]
            emb = self._embed(params["wav_bytes"])
            if emb is None:
                return {"ok": False, "error": "utterance too short to fingerprint"}
            existing = next((p for p in self.profiles if p["name"].lower() == name.lower()), None)

            # Enrolling a voice that already scores like SOMEONE ELSE is the
            # failure mode nobody notices: from then on Lucy silently confuses
            # two people. Say it out loud at enrolment instead of pretending.
            if not existing:
                clash = next(((p["name"], _cos(emb, p["embedding"]))
                              for p in self.profiles
                              if _cos(emb, p["embedding"]) >= config.SPEAKER_COLLISION), None)
                if clash and not params.get("force"):
                    return {"ok": False, "collision": clash[0],
                            "score": round(clash[1], 3),
                            "error": f"this voice sounds a lot like {clash[0]} "
                                     f"({clash[1]:.2f}) — I couldn't reliably tell "
                                     f"you apart"}
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
