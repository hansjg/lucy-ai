"""In-memory activity feed the terminal UI polls for live updates."""
import threading, time
from collections import deque

_lock   = threading.Lock()
_events = deque(maxlen=400)
_nextid = 1


def push(kind, text, ts=None):
    """kind: heard | wake | question | answer | system"""
    global _nextid
    with _lock:
        ev = {
            "id": _nextid,
            "kind": kind,
            "text": text,
            "t": time.strftime("%H:%M:%S", time.localtime(ts or time.time())),
        }
        _events.append(ev)
        _nextid += 1
    return ev


def since(after_id):
    with _lock:
        return [e for e in _events if e["id"] > int(after_id)]
