import json
import httpx
from lucy.core import config
from lucy.shared.plugin_base import LucyPlugin


class Plugin(LucyPlugin):
    model = config.LLM_MODEL   # safe default until start() resolves the chain

    async def start(self):
        try:
            tags_url = config.OLLAMA_URL.replace("/api/chat", "/api/tags")
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(tags_url)
                installed = [m["name"] for m in r.json().get("models", [])]
            for want in config.LLM_CHAIN:
                if want in installed:
                    self.model = want
                    break
            print(f"LLM brain: {self.model}"
                  + ("" if self.model == config.LLM_CHAIN[0] else " (fallback)"))
        except Exception as e:
            print(f"LLM resolve failed ({e}) — using {self.model}")

    def stream(self, action, **params):
        if action == "chat":
            return self._chat(params["messages"], params.get("options"))
        raise NotImplementedError(action)

    async def _chat(self, messages, options=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": options or {"temperature": 0.8},
        }
        async with httpx.AsyncClient(timeout=90) as client:
            async with client.stream("POST", config.OLLAMA_URL, json=payload) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        done = data.get("done", False)
                        if token:
                            yield token
                        if done:
                            break
                    except Exception:
                        continue
