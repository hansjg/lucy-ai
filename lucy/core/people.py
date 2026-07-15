"""People — voice profiles, private spaces, and consent.

The one rule this module exists to enforce:

    Voice says WHO is probably talking. It never says what they may open.

Speaker matching is a similarity score, not proof of identity — a recording
of Jeff scores like Jeff, and relatives sit close together in embedding
space. So a voice match only ever selects someone's OWN space (a wrong guess
is embarrassing, not harmful). Reaching into another person's space asks
that person, every time, unless they said otherwise in advance and recently
(see auto_allowed).
"""
import json, re, time, uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import config


def _now():
    return datetime.now().isoformat(timespec="seconds")


def slug(name):
    """'Jeff Smith' -> 'jeff-smith'; the on-disk folder name."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s or "someone"


# ── profile store ─────────────────────────────────────────
def load():
    try:
        data = json.loads(config.PROFILES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save(profiles):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PROFILES_PATH.write_text(json.dumps(profiles, indent=2), encoding="utf-8")


def get(name):
    if not name:
        return None
    low = str(name).lower()
    return next((p for p in load() if p["name"].lower() == low), None)


def ensure(name):
    """Profile for name, created (with its space) if new."""
    p = get(name)
    if p:
        return p
    profiles = load()
    p = {"name": str(name)[:24], "space": slug(name), "created": _now(),
         "devices": [], "auto_allow": []}
    profiles.append(p)
    save(profiles)
    (config.SPACES_DIR / p["space"]).mkdir(parents=True, exist_ok=True)
    return p


def update(name, **fields):
    profiles = load()
    low = str(name).lower()
    for p in profiles:
        if p["name"].lower() == low:
            p.update(fields)
            save(profiles)
            return p
    return None


def names():
    return [p["name"] for p in load()]


# ── spaces ────────────────────────────────────────────────
def space_dir(name):
    p = get(name)
    return config.SPACES_DIR / (p["space"] if p else slug(name))


def ensure_space(name):
    d = space_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _entries(folder, owner):
    try:
        files = [f for f in folder.iterdir() if f.is_file()]
    except Exception:
        return []
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "path": str(f), "bytes": f.stat().st_size,
             "owner": owner} for f in files]


def common_files():
    """The shared pile everyone can see — Lucy's original flat folder."""
    return _entries(config.SHARED_DIR, None)


def visible_files(speaker):
    """What this person may see without asking: their own space, then common.

    speaker=None (unrecognised voice, or profiles mode off) sees only the
    common pile — an unknown voice must never be handed a private space.
    """
    files = []
    if speaker and get(speaker):
        files += _entries(space_dir(speaker), speaker)
    return files + common_files()


def all_private_files():
    """Every person's space — for resolving "Jeff's budget" by name."""
    out = []
    for p in load():
        out += _entries(config.SPACES_DIR / p["space"], p["name"])
    return out


def owner_of(path):
    """Who owns a file on disk, or None if it sits in the common pile."""
    try:
        rel = Path(path).resolve().relative_to(config.SPACES_DIR.resolve())
    except Exception:
        return None
    top = rel.parts[0] if rel.parts else None
    return next((p["name"] for p in load() if p["space"] == top), None)


# ── consent ───────────────────────────────────────────────
# Pending requests live in memory on purpose: they are short-lived, and one
# that outlived a restart would be a stale "yes" waiting to happen.
_pending = {}


def auto_allowed(owner, requester):
    """Has owner pre-approved requester, and is that still in date?

    Scoped per person and expiring by design — a blanket "allow everything
    forever" would turn a voice score into unattended access to real files.
    """
    p = get(owner)
    if not p or not requester:
        return False
    for rule in p.get("auto_allow", []):
        if rule.get("requester", "").lower() != str(requester).lower():
            continue
        try:
            if datetime.fromisoformat(rule["expires"]) > datetime.now():
                return True
        except Exception:
            continue
    return False


def grant_auto(owner, requester, hours=24):
    p = ensure(owner)
    rules = [r for r in p.get("auto_allow", [])
             if r.get("requester", "").lower() != str(requester).lower()]
    expires = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    rules.append({"requester": str(requester)[:24], "expires": expires})
    update(owner, auto_allow=rules)
    return expires


def revoke_auto(owner, requester=None):
    p = get(owner)
    if not p:
        return 0
    rules = p.get("auto_allow", [])
    keep = ([] if requester is None else
            [r for r in rules
             if r.get("requester", "").lower() != str(requester).lower()])
    update(owner, auto_allow=keep)
    return len(rules) - len(keep)


def request_access(requester, owner, resource):
    """Record a pending ask. Returns its id."""
    rid = uuid.uuid4().hex[:8]
    _pending[rid] = {"id": rid, "requester": requester, "owner": owner,
                     "resource": resource, "asked": time.time(),
                     "state": "pending"}
    return rid


def pending_for(owner):
    """Live requests waiting on this person. Expired ones are dropped — an
    expired request is a NO; silence never becomes consent."""
    now = time.time()
    for rid, r in list(_pending.items()):
        if now - r["asked"] > config.ACCESS_REQUEST_TTL_S:
            del _pending[rid]
    return [r for r in _pending.values()
            if str(r["owner"]).lower() == str(owner).lower()
            and r["state"] == "pending"]


def resolve(rid, allow):
    r = _pending.pop(rid, None)
    if r:
        r["state"] = "allowed" if allow else "denied"
    return r
