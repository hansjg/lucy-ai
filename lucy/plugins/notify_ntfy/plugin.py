"""Push notifications to a phone via ntfy — Lucy's only outbound path to a
device she can't reach on the LAN.

No cloud account, nothing to build: the phone installs the ntfy app and
subscribes to one topic. On the public server the topic IS the credential,
so it must be long and random (settings.new_ntfy_topic) and it lives only in
data/settings.json, which is gitignored.
"""
import httpx

from lucy.core import config, settings
from lucy.shared.plugin_base import LucyPlugin


class Plugin(LucyPlugin):
    async def start(self):
        if not settings.load().get("ntfy_topic"):
            print("  notify.push    no topic yet - set one in settings to reach a phone")

    async def call(self, action, **params):
        if action != "push":
            raise NotImplementedError(action)

        topic = (settings.load().get("ntfy_topic") or "").strip()
        if not topic:
            # Loud on purpose: a silent no-op here would let Lucy claim she
            # notified someone when nothing ever left the machine.
            raise RuntimeError("no ntfy topic configured — open settings, create "
                               "one, and subscribe to it in the ntfy app")

        headers = {"Title": str(params.get("title") or "Lucy")[:120]}
        if params.get("tag"):
            headers["Tags"] = str(params["tag"])[:60]
        if params.get("priority"):
            headers["Priority"] = str(params["priority"])[:10]

        body = str(params.get("message") or "")[:2000]
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{config.NTFY_URL}/{topic}",
                                  content=body.encode("utf-8"), headers=headers)
            r.raise_for_status()
        return {"pushed": True, "title": headers["Title"], "chars": len(body)}
