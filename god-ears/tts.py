"""Text-to-speech out of the machine's speakers.

edge-tts synthesizes MP3; miniaudio decodes it; pyaudio plays it. Playback
is synchronous on purpose — the caller flips ears.tts_active around it so
God Ears never transcribes her own voice.
"""
import re

import edge_tts

import config

_MD_JUNK = re.compile(r"[*_`#>|]+")
_BRACKETS = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # [text](url) -> text
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿]+"
)


def clean_for_speech(text: str) -> str:
    text = _BRACKETS.sub(r"\1", text)
    text = _MD_JUNK.sub(" ", text)
    text = _EMOJI.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


async def synthesize(text: str) -> bytes:
    communicate = edge_tts.Communicate(clean_for_speech(text), config.TTS_VOICE)
    audio = b""
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return audio


def play_mp3(mp3: bytes):
    """Blocking playback through the default output device."""
    import miniaudio
    import pyaudio

    dec = miniaudio.decode(mp3)   # -> s16 PCM
    pa = pyaudio.PyAudio()
    try:
        stream = pa.open(rate=dec.sample_rate, channels=dec.nchannels,
                         format=pyaudio.paInt16, output=True)
        stream.write(dec.samples.tobytes())
        stream.stop_stream()
        stream.close()
    finally:
        pa.terminate()
