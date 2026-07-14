import base64
import edge_tts
from lucy.core import config
from lucy.shared.plugin_base import LucyPlugin
from lucy.shared.text import clean_for_tts


class Plugin(LucyPlugin):
    async def call(self, action, **params):
        if action == "synthesize":
            return await self._synthesize(params["text"])
        raise NotImplementedError(action)

    async def _synthesize(self, text):
        communicate = edge_tts.Communicate(clean_for_tts(text), config.TTS_VOICE)
        audio = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio += chunk["data"]
        return base64.b64encode(audio).decode()
