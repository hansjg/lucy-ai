"""User-facing settings — a tiny JSON file the UI edits live.

Only keys in DEFAULTS are accepted, so a stray POST can't grow the file
into a junk drawer. Values are read fresh on every use (no caching): the
file is small and this keeps toggle flips instant across the ws pipeline.
"""
import json
import secrets

from . import config


def new_ntfy_topic():
    """A topic long enough that nobody guesses their way into your pushes."""
    return "lucy-" + secrets.token_hex(12)


def new_vapid_keys():
    """A fresh VAPID keypair for Web Push, in the exact forms each consumer
    needs: the public half as the raw 65-byte uncompressed P-256 point,
    base64url-encoded with no padding (what pushManager.subscribe() expects
    as applicationServerKey — malformed encoding here is the #1 real-world
    Web Push bug). The private half is stored as PEM text; notify_webpush
    must reconstruct it with Vapid.from_pem() before signing — passing the
    PEM string straight to pywebpush's webpush() fails, since it tries to
    base64-decode it as a headerless key."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    from py_vapid.utils import b64urlencode

    v = Vapid()
    v.generate_keys()
    public_raw = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {
        "vapid_private_key": v.private_pem().decode(),
        "vapid_public_key": b64urlencode(public_raw),
    }


DEFAULTS = {
    # "Connect devices through Lucy": when she detects a connect command she
    # sends the chosen device a Wake-on-LAN signal instead of just talking.
    "connect_devices": False,
    # Which device to wake; "" = auto (the only / the named one).
    "connect_target": "",
    # "Separate profiles by voice": off = Lucy behaves exactly as she always
    # has (one person, one shared pile of files). On = each enrolled voice
    # gets its own space, and crossing into someone else's needs their yes.
    "profiles_mode": False,
    # ntfy topic the phone subscribes to. On the public server this string IS
    # the credential — long and random, never committed (data/ is gitignored).
    "ntfy_topic": "",
    # VAPID keypair for Lucy's own Web Push PWA. Private key never leaves
    # this file; public key is handed to the browser's pushManager.subscribe().
    "vapid_public_key": "",
    "vapid_private_key": "",
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
