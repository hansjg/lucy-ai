"""Q&A brain: Ollama, hard-grounded in the day's transcripts.

Anti-hallucination measures:
- temperature 0.1 and a system prompt that forbids answering about heard
  events from anything but the transcript excerpts provided
- timestamps must be cited, and "I didn't hear that" is the required
  fallback when the transcript has no answer
- keyword retrieval keeps the context on-topic instead of dumping noise
"""
import json, re, time
from datetime import date, datetime, timedelta

import httpx

import config
import transcripts

model = config.LLM_CHAIN[-1]   # safe default until resolve() runs

_STOPWORDS = set("""a an and are as at be but by did do does for from had has have
he her his how i if in is it its me my of on or our she so that the their them
they this to was we were what when where which who why will with you your today
yesterday about say said tell""".split())


async def resolve():
    """Pick the first LLM in the chain that Ollama actually has."""
    global model
    try:
        tags_url = config.OLLAMA_URL.replace("/api/chat", "/api/tags")
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(tags_url)
            installed = [m["name"] for m in r.json().get("models", [])]
        for want in config.LLM_CHAIN:
            if want in installed:
                model = want
                break
        print(f"[brain] LLM: {model}"
              + ("" if model == config.LLM_CHAIN[0] else " (fallback)"))
    except Exception as e:
        print(f"[brain] LLM resolve failed ({e}) — using {model}")


def _keywords(question):
    words = re.findall(r"[a-zA-Z']+", question.lower())
    return {w for w in words if len(w) > 3 and w not in _STOPWORDS}


def _score(line, kws):
    low = line.lower()
    return sum(1 for k in kws if k in low)


_TS_RE = re.compile(r"- \*\*(\d\d):(\d\d):(\d\d)\*\*")


def _annotate_age(text):
    """Append '(N min ago)' to each of today's transcript lines — small
    models can't be trusted to do clock arithmetic themselves."""
    now = datetime.now()

    def _tag(m):
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        age = (now - now.replace(hour=h, minute=mi, second=s)).total_seconds()
        if age < 0:
            return m.group(0)
        mins = int(age // 60)
        label = ("just now" if mins < 1
                 else f"{mins} min ago" if mins < 60
                 else f"{mins // 60} h {mins % 60} min ago")
        return f"{m.group(0)} ({label})"
    return _TS_RE.sub(_tag, text)


def build_context(question) -> str:
    """Today's transcript (or its most relevant slice) + matching lines
    from recent days, capped at MAX_CONTEXT_CHARS."""
    kws = _keywords(question)
    parts = []

    today = _annotate_age(transcripts.read_day())
    if today and len(today) <= config.MAX_CONTEXT_CHARS * 3 // 4:
        parts.append(today)
    elif today:
        lines = [l for l in today.splitlines() if l.startswith("- ")]
        recent = lines[-40:]
        scored = sorted((l for l in lines[:-40] if _score(l, kws)),
                        key=lambda l: -_score(l, kws))[:40]
        parts.append(f"# Today ({date.today().isoformat()}) — relevant excerpts\n"
                     + "\n".join(scored + ["…"] + recent))

    # matching lines from past days
    if kws:
        for d in range(1, config.SEARCH_DAYS + 1):
            day = (date.today() - timedelta(days=d)).isoformat()
            body = transcripts.read_day(day)
            if not body:
                continue
            hits = [l for l in body.splitlines()
                    if l.startswith("- ") and _score(l, kws)]
            if hits:
                parts.append(f"# {day} — matching lines\n" + "\n".join(hits[:15]))

    ctx = "\n\n".join(parts).strip()
    return ctx[-config.MAX_CONTEXT_CHARS:] if ctx else "(no transcript yet today)"


def _system_prompt(question):
    now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
    return f"""You are God Ears, an always-on listening assistant. Below are timestamped
transcripts of what your microphone actually heard. Current time: {now}.

Rules — follow them strictly:
1. Questions about what was said, heard, or happened must be answered ONLY
   from the transcript below. Cite timestamps like [14:03:22].
2. If the transcript doesn't contain the answer, say plainly that you didn't
   hear it. NEVER invent, guess, or fill gaps.
3. Transcripts can contain speech-recognition errors — read charitably, but
   don't over-interpret garbled lines.
3b. Each of today's lines is tagged with its age, e.g. "(3 min ago)". Trust
   these tags for anything time-related: "the last few minutes" means the
   lines tagged "just now" or a few "min ago" — they are at the BOTTOM.
4. For general-knowledge questions unrelated to the audio, answer briefly and
   honestly; say when you're unsure.
5. Keep spoken-style answers short and natural.

TRANSCRIPT:
{build_context(question)}"""


async def ask(question):
    """Stream answer tokens."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(question)},
            {"role": "user", "content": question},
        ],
        "stream": True,
        "options": dict(config.QA_OPTIONS),
    }
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", config.OLLAMA_URL, json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                token = data.get("message", {}).get("content", "")
                if token:
                    yield token
                if data.get("done"):
                    break


async def ask_text(question) -> str:
    parts = []
    async for tok in ask(question):
        parts.append(tok)
    return "".join(parts).strip()
