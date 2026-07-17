"""Daily transcript store + folder-based cloud sync.

One markdown file per day in data/transcripts/. Every heard utterance is
appended immediately (no buffering), so a crash never loses more than the
segment being transcribed.

Cloud model: the user wires up a synced folder (Drive/OneDrive/Dropbox/
rclone mount) and points config.SYNC_DIR at it. We push changed day-files
there and pull back days we don't have — the cloud client does the rest.
"""
import shutil, threading, time
from datetime import datetime, date
from pathlib import Path

import config

_lock = threading.Lock()
_dirty = False
_last_push = None          # ISO time of last successful push
_terminate = threading.Event()

config.TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)


def _day_path(day=None) -> Path:
    day = day or date.today().isoformat()
    return config.TRANSCRIPT_DIR / f"{day}.md"


def append(text, ts=None):
    """Append one heard utterance to today's transcript."""
    global _dirty
    text = (text or "").strip()
    if not text:
        return
    stamp = time.strftime("%H:%M:%S", time.localtime(ts or time.time()))
    with _lock:
        path = _day_path()
        is_new = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# God Ears — transcript {date.today().isoformat()}\n\n")
            f.write(f"- **{stamp}** {text}\n")
        _dirty = True


def read_day(day=None) -> str:
    path = _day_path(day)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def tail(n=20, day=None):
    lines = [l for l in read_day(day).splitlines() if l.startswith("- ")]
    return lines[-n:]


def list_days():
    return sorted(p.stem for p in config.TRANSCRIPT_DIR.glob("*.md"))


def stats():
    body = read_day()
    lines = [l for l in body.splitlines() if l.startswith("- ")]
    words = sum(len(l.split()) - 2 for l in lines) if lines else 0
    return {"date": date.today().isoformat(), "segments": len(lines), "words": max(words, 0)}


# ── Cloud sync ────────────────────────────────────────────

def _sync_dir() -> Path | None:
    if not config.SYNC_DIR:
        return None
    p = Path(config.SYNC_DIR)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except OSError:
        return None


def pull():
    """Copy in any cloud day-files that are missing or newer locally."""
    remote = _sync_dir()
    if not remote:
        return 0
    pulled = 0
    for src in remote.glob("*.md"):
        dst = config.TRANSCRIPT_DIR / src.name
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime + 1:
            with _lock:
                shutil.copy2(src, dst)
            pulled += 1
    return pulled


def push_all():
    """Copy out local day-files that are missing or newer in the cloud."""
    global _dirty, _last_push
    remote = _sync_dir()
    if not remote:
        return 0
    pushed = 0
    for src in config.TRANSCRIPT_DIR.glob("*.md"):
        dst = remote / src.name
        if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime + 1:
            with _lock:
                shutil.copy2(src, dst)
            pushed += 1
    if pushed:
        _last_push = datetime.now().strftime("%H:%M:%S")
    _dirty = False
    return pushed


def sync_now() -> str:
    if not _sync_dir():
        return "sync is off — set SYNC_DIR in config.py to a cloud-synced folder"
    pulled = pull()
    pushed = push_all()
    return f"sync done — pushed {pushed}, pulled {pulled} file(s)"


def _sync_loop():
    while not _terminate.wait(config.SYNC_INTERVAL_S):
        if _dirty:
            try:
                push_all()
            except OSError:
                pass  # cloud folder briefly unavailable — retry next tick


def start():
    try:
        pull()
    except OSError:
        pass
    threading.Thread(target=_sync_loop, daemon=True).start()


def stop():
    _terminate.set()
    try:
        push_all()
    except OSError:
        pass


def sync_status():
    return {
        "enabled": bool(_sync_dir()),
        "dir": str(config.SYNC_DIR) if config.SYNC_DIR else None,
        "last_push": _last_push,
        "pending": _dirty,
    }
