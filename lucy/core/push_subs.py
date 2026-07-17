"""Per-person Web Push subscriptions — mirrors nodes.py's known_nodes store.

Keyed by profile name (not slug) so it lines up with people.py's own lookups.
"""
import json

from . import config


def _load():
    try:
        return json.loads(config.PUSH_SUBSCRIPTIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.PUSH_SUBSCRIPTIONS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_subscription(name, subscription_info):
    data = _load()
    data[name] = subscription_info
    _save(data)


def get_subscription(name):
    return _load().get(name)


def remove_subscription(name):
    data = _load()
    removed = data.pop(name, None) is not None
    _save(data)
    return removed
