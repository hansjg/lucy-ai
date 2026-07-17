"""Push notifications to Lucy's own PWA — no third-party app, no fee.

Mirrors notify_ntfy's contract (same capability, same loud-failure style):
if this plugin has no subscription for `to`, it raises and app.py's
_notify_owner tries notify_ntfy next (when configured).
"""
import json

from py_vapid import Vapid
from pywebpush import webpush, WebPushException

from lucy.core import config, settings, push_subs
from lucy.shared.plugin_base import LucyPlugin


class Plugin(LucyPlugin):
    async def start(self):
        # VAPID keys are invisible plumbing the browser fetches itself
        # (unlike ntfy's topic, nobody types or shares this) — generate
        # once on first boot rather than requiring a settings-panel step.
        if not settings.load().get("vapid_private_key"):
            settings.save(settings.new_vapid_keys())
            print("  notify.push    generated a VAPID keypair for Web Push")

    async def call(self, action, **params):
        if action != "push":
            raise NotImplementedError(action)

        to = params.get("to")
        sub = push_subs.get_subscription(to) if to else None
        if not sub:
            raise RuntimeError(f"no push subscription for '{to}' — they "
                               f"haven't installed the mobile app yet")

        priv_pem = settings.load().get("vapid_private_key")
        if not priv_pem:
            raise RuntimeError("no VAPID keys generated yet")
        # webpush()'s vapid_private_key str path expects a headerless
        # base64 key and chokes on our stored PEM text, so reconstruct the
        # Vapid object ourselves and pass that instead (see settings.new_vapid_keys).
        vapid_key = Vapid.from_pem(priv_pem.encode())

        payload = {
            "title": str(params.get("title") or "Lucy")[:120],
            "body": str(params.get("message") or "")[:500],
            "requestId": params.get("request_id"),   # None for plain notices
        }
        try:
            webpush(subscription_info=sub, data=json.dumps(payload),
                   vapid_private_key=vapid_key,
                   vapid_claims={"sub": config.VAPID_CLAIMS_SUBJECT})
        except WebPushException as e:
            if getattr(e.response, "status_code", None) == 410:
                push_subs.remove_subscription(to)   # phone unsubscribed/uninstalled
            raise RuntimeError(f"push to {to} failed: {e}") from e
        return {"pushed": True, "to": to}
