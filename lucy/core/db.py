"""SQLite persistence — tasks, task log, known nodes. WAL mode, thread-safe."""
import sqlite3, threading, datetime
from . import config

_lock = threading.Lock()
_conn = None


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def init():
    global _conn
    config.DATA_DIR.mkdir(exist_ok=True)
    _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.executescript("""
    CREATE TABLE IF NOT EXISTS nodes(
        name TEXT PRIMARY KEY, os TEXT, arch TEXT,
        first_seen TEXT, last_seen TEXT
    );
    CREATE TABLE IF NOT EXISTS tasks(
        id TEXT PRIMARY KEY, capability TEXT, action TEXT, params TEXT,
        node TEXT, state TEXT, created TEXT, finished TEXT,
        result TEXT, error TEXT, duration_ms INTEGER
    );
    CREATE TABLE IF NOT EXISTS task_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT, ts TEXT, event TEXT, detail TEXT
    );
    """)
    _conn.commit()


def upsert_node(name, os_name, arch):
    with _lock:
        _conn.execute(
            "INSERT INTO nodes(name, os, arch, first_seen, last_seen) VALUES(?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen, os=excluded.os, arch=excluded.arch",
            (name, os_name, arch, _now(), _now()))
        _conn.commit()


def all_nodes():
    with _lock:
        rows = _conn.execute(
            "SELECT name, os, last_seen FROM nodes ORDER BY last_seen DESC").fetchall()
    return [{"name": r[0], "os": r[1], "last_seen": r[2]} for r in rows]


def task_created(task_id, capability, action, params, node):
    with _lock:
        _conn.execute(
            "INSERT INTO tasks(id, capability, action, params, node, state, created) VALUES(?,?,?,?,?,?,?)",
            (task_id, capability, action, params, node, "running", _now()))
        _conn.execute("INSERT INTO task_log(task_id, ts, event, detail) VALUES(?,?,?,?)",
                      (task_id, _now(), "dispatched", node))
        _conn.commit()


def task_finished(task_id, ok, result=None, error=None, duration_ms=None):
    with _lock:
        _conn.execute(
            "UPDATE tasks SET state=?, finished=?, result=?, error=?, duration_ms=? WHERE id=?",
            ("done" if ok else "failed", _now(), result, error, duration_ms, task_id))
        _conn.execute("INSERT INTO task_log(task_id, ts, event, detail) VALUES(?,?,?,?)",
                      (task_id, _now(), "done" if ok else "failed", error or ""))
        _conn.commit()


def log_event(task_id, event, detail=""):
    with _lock:
        _conn.execute("INSERT INTO task_log(task_id, ts, event, detail) VALUES(?,?,?,?)",
                      (task_id, _now(), event, detail))
        _conn.commit()


def recent_tasks(n=30):
    with _lock:
        rows = _conn.execute(
            "SELECT id, capability, action, node, state, created, duration_ms, error "
            "FROM tasks ORDER BY created DESC LIMIT ?", (n,)).fetchall()
    return [
        {"id": r[0], "capability": r[1], "action": r[2], "node": r[3],
         "state": r[4], "created": r[5], "duration_ms": r[6], "error": r[7]}
        for r in rows
    ]
