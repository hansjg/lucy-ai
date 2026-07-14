"""Streaming transcription — live-caption speed AND whole sentences.

A rolling audio buffer is re-transcribed about once a second. Words that two
consecutive passes agree on are committed (the LocalAgreement policy used by
live-captioning systems); the still-unstable tail is shown in the UI as a
live caption. Committed words are flushed to the daily transcript at
sentence boundaries — so lines are full sentences, not chunks cut wherever
the audio happened to pause.
"""
import re, threading, time
from collections import deque

import config
import feed
import stt
import transcripts

_SENT_END = re.compile(r"[.!?…]['\")\]]*$")
_NORM = re.compile(r"[^a-z0-9']+")

_GUARD_S = 0.8         # never commit words this close to the buffer's live edge
_TAIL_QUIET_S = 0.8    # this much quiet at the edge = utterance finished
_MAX_BUFFER_S = 20     # anti-stall: force commits past this
_FLUSH_AGE_S = 12      # pending text older than this is flushed without punctuation
_FLUSH_CHARS = 220     # pending text longer than this is flushed without punctuation
_MIN_LINE_WORDS = 8    # don't cut a line at "." until it's a real sentence —
                       # Whisper punctuates fragments aggressively on short buffers


def _norm(w):
    return _NORM.sub("", w.lower())


# On borderline audio Whisper sometimes echoes the STT_HINTS prompt back as
# output ("God Ears, Vivobook, dexalab."). Lines that are mostly hint words
# are prompt leaks, not speech.
_HINT_WORDS = {_norm(w) for w in re.findall(r"[A-Za-z']+", config.STT_HINTS)} | {"lucy"}

# Whisper's stock hallucinations on music/faint audio — junk when they are
# the ENTIRE line (inside a sentence they're legitimate speech).
_ARTIFACT_LINES = {
    "thank you", "thanks for watching", "thanks for having me",
    "thank you for watching", "thank you guys for having me",
    "ill see you in the next video", "see you next time",
}


def _is_hint_echo(text):
    ws = [n for n in (_norm(w) for w in text.split()) if n]
    if not ws:
        return True
    return sum(1 for w in ws if w in _HINT_WORDS) / len(ws) >= 0.6


class Streamer:
    def __init__(self):
        self._lock = threading.Lock()
        self._frames = []      # 100 ms int16 pcm frames
        self._rms = []         # rms per frame
        self._epoch = 0.0      # wall-clock time of _frames[0]
        self._last_add = 0.0
        self._prev = []        # last pass's uncommitted words (times rel. to buffer)
        self._pending = []     # committed words awaiting sentence flush: (word, wall_ts)
        self._recent = deque(maxlen=12)   # last committed norms — survives flushes
        self._last_line = ""   # last flushed line (normalized), kills repeat spam
        self._done_s = 0.0     # buffer time already committed but not yet trimmed
        self.partial = ""      # live caption: text not yet committed
        self.buffer_s = 0.0

    # called from the mic thread for every running, non-TTS frame
    def add(self, frame, rms):
        with self._lock:
            if not self._frames:
                self._epoch = time.time()
                self._prev = []
            self._frames.append(frame)
            self._rms.append(rms)
            self._last_add = time.time()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    # ── internals ─────────────────────────────────────────
    def _drop(self, n):
        fs = config.FRAME_MS / 1000
        with self._lock:
            del self._frames[:n]
            del self._rms[:n]
            self._epoch += n * fs
        self._done_s = max(0.0, self._done_s - n * fs)

    def _loop(self):
        fs = config.FRAME_MS / 1000
        while True:
            time.sleep(0.15)
            with self._lock:
                frames = list(self._frames)
                rms = list(self._rms)
                epoch = self._epoch
                stale = time.time() - self._last_add > 2.0   # paused / muted
            dur = len(frames) * fs
            self.buffer_s = round(dur, 1)

            if not frames:
                self.partial = ""
                self._flush(force=True)
                continue

            # buffer holds no voice-like audio at all: a genuine lull only
            # counts after 1.5s — a shorter all-quiet buffer is just the gap
            # between two sentences and must not chop the pending line
            if max(rms) < config.SPEECH_RMS_MIN:
                if dur >= 1.5 or stale:
                    self._drop(len(frames))
                    self._prev = []
                    self.partial = ""
                    self._flush(force=True)
                continue

            if dur < 1.2 and not stale:
                continue

            words = stt.transcribe_words(b"".join(frames))
            # audio before _done_s is already committed — a fresh pass over
            # it produces re-transcriptions, not new speech
            words = [w for w in words if w["e"] > self._done_s + 0.05]

            # loud non-speech (music) passes the RMS gate but yields no
            # words — without this the buffer would grow without bound
            if not words and dur > 8:
                self._drop(max(0, len(frames) - int(2.0 / fs)))
                self._prev = []
                self.partial = ""
                continue

            tail = rms[-max(1, int(_TAIL_QUIET_S / fs)):]
            tail_quiet = stale or max(tail) < config.SPEECH_RMS_MIN

            if tail_quiet and not words:
                # noise/music the guards rejected — throw the buffer away
                self._drop(len(frames))
                self._prev = []
                self.partial = ""
                self._flush(force=True)
                continue

            if tail_quiet:
                commit, rest = words, []          # utterance over: all stable
            else:
                agree = 0
                for a, b in zip(self._prev, words):
                    if _norm(a["w"]) != _norm(b["w"]):
                        break
                    agree += 1
                commit = [w for w in words[:agree] if w["e"] < dur - _GUARD_S]
                if dur > _MAX_BUFFER_S and not commit:
                    # agreement stalled on hard audio — commit anyway, bounded lag
                    commit = [w for w in words if w["e"] < dur - 2.0]
                rest = words[len(commit):]

            # seam echo: after a trim, Whisper re-completes the clipped tail
            # of the last commit as whole words. Compare against _recent
            # (survives flushes) and drop up to an 8-word repeat.
            if commit and self._recent:
                recent = list(self._recent)
                for k in range(min(8, len(commit), len(recent)), 0, -1):
                    if [_norm(w["w"]) for w in commit[:k]] == recent[-k:]:
                        commit = commit[k:]
                        break

            if commit:
                for w in commit:
                    self._pending.append((w["w"].strip(), epoch + w["s"]))
                    self._recent.append(_norm(w["w"]))
                self._done_s = commit[-1]["e"]

            # Trim policy: cut ONLY at real silence inside the committed
            # region — cutting mid-speech clips words and breeds echoes.
            # Gapless speech just lets the buffer ride until a pause, with
            # a forced boundary cut past 15s as the safety valve.
            ncut = 0
            dlim = min(len(rms), int(self._done_s / fs))
            for i in range(dlim - 1, -1, -1):
                if rms[i] < config.SPEECH_RMS_MIN:
                    ncut = i
                    break
            if not ncut and dur > 15 and dlim > 0:
                ncut = dlim
            if tail_quiet and not rest:
                # utterance over — but only clear audio the words actually
                # covered; a guard-rejected trailing chunk gets one retry
                # (the anti-stall drop is the backstop if it keeps failing)
                last_loud = next((i for i in range(len(rms) - 1, -1, -1)
                                  if rms[i] >= config.SPEECH_RMS_MIN), -1)
                covered = (last_loud < 0 or
                           (commit and commit[-1]["e"] >= (last_loud + 1) * fs - 0.6))
                if covered:
                    ncut = len(frames)
                elif commit:
                    ncut = max(ncut, int(commit[-1]["e"] / fs))
            if ncut:
                self._drop(ncut)
                rest = [{**w, "s": max(0.0, w["s"] - ncut * fs),
                         "e": max(0.0, w["e"] - ncut * fs)} for w in rest]

            self._prev = rest
            self.partial = " ".join(w["w"].strip() for w in rest).strip()
            # no force here: micro-pauses between sentences must not chop
            # lines — real lulls flush via the all-quiet branch above
            self._flush()

    def _flush(self, force=False):
        """Emit complete sentences from pending words; the unfinished tail
        stays pending unless forcing (utterance ended / age / size)."""
        if not self._pending:
            return
        age = time.time() - self._pending[0][1]
        size = sum(len(w) + 1 for w, _ in self._pending)

        out, line, line_ts, next_start = [], [], None, 0
        for i, (w, ts) in enumerate(self._pending):
            if line_ts is None:
                line_ts = ts
            line.append(w)
            if _SENT_END.search(w) and len(line) >= _MIN_LINE_WORDS:
                out.append((" ".join(line), line_ts))
                line, line_ts = [], None
                next_start = i + 1

        remainder = self._pending[next_start:]
        if remainder and (force or age > _FLUSH_AGE_S or size > _FLUSH_CHARS):
            out.append((" ".join(w for w, _ in remainder), remainder[0][1]))
            remainder = []
        self._pending = remainder

        for text, ts in out:
            text = text.strip()
            if not text or stt.is_loop_junk(text) or _is_hint_echo(text):
                continue
            key = " ".join(n for n in (_norm(w) for w in text.split()) if n)
            if key == self._last_line or key in _ARTIFACT_LINES:
                continue   # repeat spam / stock hallucination
            self._last_line = key
            transcripts.append(text, ts)
            feed.push("heard", text, ts)
