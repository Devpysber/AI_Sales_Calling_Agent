"""
Sarvam AI text-to-speech (bulbul).

Generated audio is kept in the shared store (so any replica can serve it
to Plivo) under a random id; fixed prompts are cached by content hash.
"""

import base64
import hashlib
import uuid

import httpx

from app.core import store
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

SPEAKERS = ["aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit", "dev",
            "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa", "kabir", "aayan", "shubh", "advait", "anand",
            "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham",
            "rupali"]

LANGUAGES = {"en-IN": "English", "hi-IN": "Hindi", "bn-IN": "Bengali", "ta-IN": "Tamil", "te-IN": "Telugu",
             "kn-IN": "Kannada", "ml-IN": "Malayalam", "mr-IN": "Marathi", "gu-IN": "Gujarati", "pa-IN": "Punjabi",
             "od-IN": "Odia"}

SCRIPTS = [(0x0900, 0x097F, "hi-IN"), (0x0980, 0x09FF, "bn-IN"), (0x0A00, 0x0A7F, "pa-IN"), (0x0A80, 0x0AFF, "gu-IN"),
           (0x0B00, 0x0B7F, "od-IN"), (0x0B80, 0x0BFF, "ta-IN"), (0x0C00, 0x0C7F, "te-IN"), (0x0C80, 0x0CFF, "kn-IN"),
           (0x0D00, 0x0D7F, "ml-IN")]

AUDIO_TTL = 30 * 60
CACHE_TTL = 7 * 24 * 3600
_client = httpx.Client(timeout=httpx.Timeout(20, connect=5))


class TTSError(RuntimeError):
    pass


def detect_language(text: str, default: str = "en-IN") -> str:
    for char in text:
        code = ord(char)
        for start, end, lang in SCRIPTS:
            if start <= code <= end:
                return lang
    return default


def _sarvam_synthesize(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    if not settings.sarvam_api_key:
        raise TTSError("SARVAM_API_KEY not set")
    body = {
        "text": text[:2500],
        "target_language_code": language or detect_language(text),
        "model": settings.sarvam_tts_model,
        "speech_sample_rate": settings.sarvam_tts_sample_rate,
    }
    if speaker:
        body["speaker"] = speaker
    last = None
    for _ in range(2):
        try:
            res = _client.post("https://api.sarvam.ai/text-to-speech",
                               headers={"api-subscription-key": settings.sarvam_api_key}, json=body)
            if res.status_code >= 400:
                raise TTSError(f"Sarvam TTS {res.status_code}: {res.text[:200]}")
            return b"".join(base64.b64decode(chunk) for chunk in res.json()["audios"])
        except TTSError as e:
            last = e
            if "400" in str(e):
                break
        except Exception as e:
            last = TTSError(str(e))
    raise last


def _sarvam_synthesize_pcm(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    """Raw 16-bit little-endian mono PCM at 8 kHz, ready for the phone stream."""
    if not settings.sarvam_api_key:
        raise TTSError("SARVAM_API_KEY not set")
    body = {"text": text[:2500], "target_language_code": language or detect_language(text), "model": settings.sarvam_tts_model,
            "speech_sample_rate": 8000, "output_audio_codec": "linear16"}
    if speaker:
        body["speaker"] = speaker
    res = _client.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": settings.sarvam_api_key}, json=body)
    if res.status_code >= 400:
        raise TTSError(f"Sarvam TTS {res.status_code}: {res.text[:200]}")
    pcm = b"".join(base64.b64decode(chunk) for chunk in res.json()["audios"])
    return pcm[44:] if pcm[:4] == b"RIFF" else pcm


def cached_pcm(text: str, language: str, speaker: str, usage: dict | None = None) -> bytes:
    """Content-addressed cache of phone-stream audio for fixed lines (greetings, prompts). usage: billed chars on a miss."""
    key = "pcm:" + hashlib.sha256(f"{settings.tts_engine}|{settings.sarvam_tts_model}|{speaker}|{language}|{text}".encode()).hexdigest()[:40]
    audio = store.get_bytes(key)
    if audio is None:
        audio = synthesize_pcm(text, language, speaker)
        store.set_bytes(key, audio, ttl=CACHE_TTL)
        if usage is not None:
            usage["tts_chars"] = usage.get("tts_chars", 0) + len(text)
    return audio


def store_audio(audio: bytes) -> str:
    audio_id = uuid.uuid4().hex
    store.set_bytes(f"audio:{audio_id}", audio, ttl=AUDIO_TTL)
    return audio_id


def cached_audio_id(text: str, language: str, speaker: str) -> str:
    """
    Content-addressed cache for fixed prompts (greetings, re-prompts).
    """
    key = hashlib.sha256(f"{settings.tts_engine}|{settings.sarvam_tts_model}|{settings.sarvam_tts_sample_rate}|{speaker}|{language}|{text}"
                         .encode()).hexdigest()[:40]
    if store.get_bytes(f"audio:{key}") is None:
        store.set_bytes(f"audio:{key}", synthesize(text, language, speaker), ttl=CACHE_TTL)
    return key


def load_audio(audio_id: str) -> bytes | None:
    return store.get_bytes(f"audio:{audio_id}")


def audio_url(audio_id: str) -> str:
    return f"{settings.base_url}/api/media/audio/{audio_id}.wav"



# ---------------- engine switch ----------------

def _local() -> bool:
    return settings.tts_engine.lower() == "indicf5"


def _pcm16(audio, rate_in: int, rate_out: int) -> bytes:
    import numpy as np
    from scipy.signal import resample_poly

    if rate_in != rate_out:
        audio = resample_poly(audio, rate_out, rate_in)
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _wav(pcm: bytes, rate: int) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)
    return buf.getvalue()


def warmup():
    """Load the local model at startup so the first call does not wait for it."""
    if not _local():
        return
    from app.services import indicf5

    try:
        indicf5.warmup()
    except Exception as e:  # noqa: BLE001 - startup must not fail on TTS
        log.warning("IndicF5 warmup failed: %s", e)


def synthesize(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    """WAV audio from the configured engine."""
    language = language or detect_language(text)
    if _local():
        from app.services import indicf5

        try:
            rate = settings.sarvam_tts_sample_rate
            return _wav(_pcm16(indicf5.generate(text, language, speaker), indicf5.SAMPLE_RATE, rate), rate)
        except Exception as e:  # noqa: BLE001
            raise TTSError(f"IndicF5: {e}") from e
    return _sarvam_synthesize(text, language, speaker)


def synthesize_pcm(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    """Raw 16-bit little-endian mono PCM at 8 kHz, ready for the phone stream."""
    language = language or detect_language(text)
    if _local():
        from app.services import indicf5

        try:
            return _pcm16(indicf5.generate(text, language, speaker), indicf5.SAMPLE_RATE, 8000)
        except Exception as e:  # noqa: BLE001
            raise TTSError(f"IndicF5: {e}") from e
    return _sarvam_synthesize_pcm(text, language, speaker)
