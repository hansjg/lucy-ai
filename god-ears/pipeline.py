"""Glue between the ears and everything else.

A single worker thread drains the segment queue: transcribe -> append to
the daily transcript -> if it was a question, answer it out loud.
Serializing through one queue keeps Whisper, Ollama and the speaker from
fighting each other.
"""
import asyncio, queue, re, threading, time

import brain
import config
import feed
import stt
import transcripts
import tts

_q = queue.Queue()
_ears = None

# Strip the wake phrase (as Whisper may spell it) off the front of a question.
# Anchored to the start (allowing filler words) so a question that merely
# MENTIONS Lucy mid-sentence is left intact.
_WAKE_RE = re.compile(
    r"^(?:(?:okay|ok|so|um|uh|hey|hay|hi|a)[\s,!.]*)*"
    r"(?:lucy|luci|lousy|god\s*ears)\b[\s,.!?]*",
    re.IGNORECASE)


def init(ears):
    global _ears
    _ears = ears
    threading.Thread(target=_worker, daemon=True).start()


def on_segment(pcm, kind, ts):
    _q.put((pcm, kind, ts))


def on_wake(in_speech):
    feed.push("wake", "wake phrase heard")
    if not in_speech:
        # wake phrase alone, said in a gap — invite the question
        _ears.await_question()
        threading.Thread(target=speak, args=("Yes?",), daemon=True).start()


def speak(text):
    if not text:
        return
    try:
        mp3 = asyncio.run(tts.synthesize(text))
        _ears.tts_active = True
        tts.play_mp3(mp3)
    except Exception as e:
        print(f"[tts] speech failed: {e}")
    finally:
        time.sleep(0.3)   # let the room's echo of our own voice die down
        _ears.tts_active = False


def _strip_wake(text):
    m = _WAKE_RE.match(text or "")
    return (text[m.end():] if m else text or "").strip()


def _worker():
    while True:
        pcm, kind, ts = _q.get()
        if kind != "question":
            continue   # ambient text is owned by the streaming transcriber
        try:
            text = stt.transcribe(pcm)
        except Exception as e:
            print(f"[pipeline] transcribe failed: {e}")
            continue

        # (no transcripts.append here — the streamer hears this audio too)
        question = _strip_wake(text)
        if not question:
            if not text:
                speak("I didn't catch that.")
            else:
                # bare "hey lucy" — invite the actual question
                _ears.await_question()
                speak("Yes?")
            continue

        feed.push("question", question, ts)
        try:
            answer = asyncio.run(brain.ask_text(question)) \
                     or "I don't have an answer for that."
        except Exception as e:
            print(f"[pipeline] brain failed: {e}")
            answer = "Sorry — my brain isn't reachable right now."
        feed.push("answer", answer)
        speak(answer)


def queue_depth():
    return _q.qsize()
