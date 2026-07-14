"""User-facing settings — a tiny JSON file the UI edits live.

Only keys in DEFAULTS are accepted, so a stray POST can't grow the file
into a junk drawer. Values are read fresh on every use (no caching): the
file is small and this keeps toggle flips instant across the ws pipeline.
"""
import json

from . import config

DEFAULTS = {
    # "Connect devices through Lucy": when she detects a connect command she
    # sends the chosen device a Wake-on-LAN signal instead of just talking.
    "connect_devices": False,
    # Which device to wake; "" = auto (the only / the named one).
    "connect_target": "",
}


def load():
    try:
        data = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save(patch):
    cur = load()
    cur.update({k: v for k, v in (patch or {}).items() if k in DEFAULTS})
    config.DATA_DIR.mkdir(exist_ok=True)
    config.SETTINGS_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return cur
