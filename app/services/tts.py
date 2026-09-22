"""
Sarvam AI text-to-speech (bulbul) with gTTS fallback.

Generated audio is kept in the shared store (so any replica can serve it
to Plivo) under a random id; fixed prompts are cached by content hash.

If Sarvam TTS returns a quota/credit error (402/429) the call is
automatically retried with Google gTTS (free, no API key required).
"""

import base64
import hashlib
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor

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

# Languages that share one script, so no amount of character counting can tell them apart: only what the
# call is actually speaking can. Keyed by the code SCRIPTS returns for that block.
SHARED_SCRIPT = {"hi-IN": {"hi-IN", "mr-IN"}}

SCRIPTS = [(0x0900, 0x097F, "hi-IN"), (0x0980, 0x09FF, "bn-IN"), (0x0A00, 0x0A7F, "pa-IN"), (0x0A80, 0x0AFF, "gu-IN"),
           (0x0B00, 0x0B7F, "od-IN"), (0x0B80, 0x0BFF, "ta-IN"), (0x0C00, 0x0C7F, "te-IN"), (0x0C80, 0x0CFF, "kn-IN"),
           (0x0D00, 0x0D7F, "ml-IN")]

AUDIO_TTL = 30 * 60
CACHE_TTL = 7 * 24 * 3600
_client = httpx.Client(timeout=httpx.Timeout(20, connect=5))


class FallbackAudio(bytes):
    """Audio from the Edge fallback voice: served now, never cached for a week under the Sarvam key."""


class TTSError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Fallback TTS: Microsoft Edge TTS (neural voices, free, no API key needed)
# Paired with miniaudio for clean MP3 → WAV / PCM conversion.
# ---------------------------------------------------------------------------

# Best Edge TTS neural voice per Indian language code, as (male, female) pairs
_EDGE_VOICE_MAP: dict[str, tuple[str, str]] = {
    "hi-IN": ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),
    "en-IN": ("en-IN-PrabhatNeural", "en-IN-NeerjaNeural"),
    "bn-IN": ("bn-IN-BashkarNeural", "bn-IN-TanishaaNeural"),
    "ta-IN": ("ta-IN-ValluvarNeural", "ta-IN-PallaviNeural"),
    "te-IN": ("te-IN-MohanNeural", "te-IN-ShrutiNeural"),
    "kn-IN": ("kn-IN-GaganNeural", "kn-IN-SapnaNeural"),
    "ml-IN": ("ml-IN-MidhunNeural", "ml-IN-SobhanaNeural"),
    "mr-IN": ("mr-IN-ManoharNeural", "mr-IN-AarohiNeural"),
    "gu-IN": ("gu-IN-NiranjanNeural", "gu-IN-DhwaniNeural"),
    "pa-IN": ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),  # Edge has no pa-IN; Hindi closest
    "od-IN": ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"),  # Edge has no od-IN; Hindi closest
}


def _is_quota_error(err_text: str) -> bool:
    """Return True when Sarvam's response indicates a credits/quota failure."""
    markers = ("402", "insufficient_quota", "no credits", "insufficient credit")
    low = err_text.lower()
    return any(m in low for m in markers)


def _edge_mp3_bytes(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    """Return MP3 bytes from Microsoft Edge TTS (neural voice, no API key)."""
    import asyncio
    import io
    import edge_tts
    from app.services.agent import FEMALE_SPEAKERS

    lang_code = language or detect_language(text)
    male_voice, female_voice = _EDGE_VOICE_MAP.get(lang_code, ("hi-IN-MadhurNeural", "hi-IN-SwaraNeural"))
    female = (speaker or "").strip().lower() in FEMALE_SPEAKERS
    voice = female_voice if female else male_voice

    async def _run() -> bytes:
        buf = io.BytesIO()
        communicate = edge_tts.Communicate(text[:2500], voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, _run()).result(timeout=20)
        return loop.run_until_complete(_run())
    except RuntimeError:
        return asyncio.run(_run())


def _mp3_to_wav(mp3_bytes: bytes, sample_rate: int = 22050) -> bytes:
    """Decode MP3 bytes to WAV bytes using miniaudio (no ffmpeg needed)."""
    import io
    import wave
    import miniaudio

    decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=1, sample_rate=sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(decoded.samples)
    return buf.getvalue()


def _mp3_to_pcm8k(mp3_bytes: bytes) -> bytes:
    """Decode MP3 → raw 16-bit mono PCM at 8 kHz using miniaudio."""
    import miniaudio

    decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16,
                               nchannels=1, sample_rate=8000)
    return bytes(decoded.samples)


def _edge_synthesize_wav(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    """Synthesise speech with Edge TTS and return WAV bytes."""
    mp3 = _edge_mp3_bytes(text, language, speaker)
    return _mp3_to_wav(mp3)


def _edge_synthesize_pcm(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    """Synthesise speech with Edge TTS and return raw 16-bit PCM at 8 kHz."""
    mp3 = _edge_mp3_bytes(text, language, speaker)
    return _mp3_to_pcm8k(mp3)



def detect_language(text: str, default: str = "en-IN") -> str:
    for char in text:
        code = ord(char)
        for start, end, lang in SCRIPTS:
            if start <= code <= end:
                return lang
    return default


def _sarvam_synthesize(text: str, language: str | None = None, speaker: str | None = None) -> bytes:
    if not settings.sarvam_api_key:
        log.warning("SARVAM_API_KEY not set – falling back to Edge TTS")
        return FallbackAudio(_edge_synthesize_wav(text, language, speaker))
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
                err_msg = f"Sarvam TTS {res.status_code}: {res.text[:200]}"
                if _is_quota_error(res.text + str(res.status_code)):
                    log.warning("%s – switching to Edge TTS fallback", err_msg)
                    return FallbackAudio(_edge_synthesize_wav(text, language, speaker))
                raise TTSError(err_msg)
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
        log.warning("SARVAM_API_KEY not set – falling back to Edge TTS for PCM")
        return FallbackAudio(_edge_synthesize_pcm(text, language, speaker))
    body = {"text": text[:2500], "target_language_code": language or detect_language(text), "model": settings.sarvam_tts_model,
            "speech_sample_rate": 8000, "output_audio_codec": "linear16"}
    if speaker:
        body["speaker"] = speaker
    res = _client.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": settings.sarvam_api_key}, json=body)
    if res.status_code >= 400:
        err_msg = f"Sarvam TTS {res.status_code}: {res.text[:200]}"
        if _is_quota_error(res.text + str(res.status_code)):
            log.warning("%s – switching to Edge TTS fallback for PCM", err_msg)
            return FallbackAudio(_edge_synthesize_pcm(text, language, speaker))
        raise TTSError(err_msg)
    pcm = b"".join(base64.b64decode(chunk) for chunk in res.json()["audios"])
    return pcm[44:] if pcm[:4] == b"RIFF" else pcm


def cached_pcm(text: str, language: str, speaker: str, usage: dict | None = None) -> bytes:
    """Content-addressed cache of phone-stream audio for fixed lines (greetings, prompts). usage: billed chars on a miss."""
    key = "pcm:" + hashlib.sha256(f"{settings.tts_engine}|{settings.sarvam_tts_model}|{speaker}|{language}|{text}".encode()).hexdigest()[:40]
    audio = store.get_bytes(key)
    if audio is None:
        audio = synthesize_pcm(text, language, speaker)
        fallback = isinstance(audio, FallbackAudio)
        store.set_bytes(key, audio, ttl=AUDIO_TTL if fallback else CACHE_TTL)
        if usage is not None and not fallback:
            usage["tts_chars"] = usage.get("tts_chars", 0) + len(text)
    return audio


def store_audio(audio: bytes) -> str:
    audio_id = uuid.uuid4().hex
    store.set_bytes(f"audio:{audio_id}", audio, ttl=AUDIO_TTL)
    return audio_id


# Playground audio is rendered off the request path: prepare_audio_id() returns the id at once so the
# text reply is not held up by synthesis, and kicks the render off in the background so that by the time
# the browser asks for the file it is usually already cached. One in-flight render per id: a browser
# fetch that arrives mid-render waits on the same future instead of synthesising twice.
_render_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tts-render")
_renders: dict[str, Future] = {}
_renders_lock = threading.Lock()


def _render(audio_id: str) -> bytes | None:
    audio = store.get_bytes(f"audio:{audio_id}")
    if audio is not None:
        return audio
    params = store.get_json(f"tts_job:{audio_id}")
    if not params:
        return None
    audio = synthesize(params["text"], params["language"], params["speaker"])
    store.set_bytes(f"audio:{audio_id}", audio, ttl=AUDIO_TTL if isinstance(audio, FallbackAudio) else CACHE_TTL)
    return audio


def _render_shared(audio_id: str) -> Future:
    with _renders_lock:
        fut = _renders.get(audio_id)
        created = fut is None
        if created:
            fut = _render_pool.submit(_render, audio_id)
            _renders[audio_id] = fut
    if created:
        # Registered outside the lock: a future that already finished runs the callback inline, which
        # would try to take the same (non-reentrant) lock.
        def forget(_f, key=audio_id):
            with _renders_lock:
                if _renders.get(key) is _f:
                    _renders.pop(key, None)
        fut.add_done_callback(forget)
    return fut


def prepare_audio_id(text: str, language: str, speaker: str) -> str:
    """
    Returns an audio ID instantly; synthesis starts in the background and is served from cache by load_audio().
    """
    key = hashlib.sha256(f"{settings.tts_engine}|{settings.sarvam_tts_model}|{settings.sarvam_tts_sample_rate}|{speaker}|{language}|{text}"
                         .encode()).hexdigest()[:40]
    if store.get_bytes(f"audio:{key}") is None:
        store.set_json(f"tts_job:{key}", {"text": text, "language": language, "speaker": speaker}, ttl=CACHE_TTL)
        _render_shared(key)
    return key


def cached_audio_id(text: str, language: str, speaker: str) -> str:
    """
    Audio id for a line Plivo will fetch by URL moments later (legacy voice mode). The render starts in
    the background at once; load_audio() joins it if Plivo asks before it is done.
    """
    return prepare_audio_id(text, language, speaker)


def load_audio(audio_id: str) -> bytes | None:
    audio = store.get_bytes(f"audio:{audio_id}")
    if audio is not None:
        return audio
    if store.get_json(f"tts_job:{audio_id}") is None:
        return None
    try:
        return _render_shared(audio_id).result(timeout=40)
    except Exception as e:  # noqa: BLE001 - TTSError, httpx errors, futures.TimeoutError
        log.warning("Playground audio %s failed: %s", audio_id, e)
        # A render still running past the wait keeps its job so a later fetch is served from cache;
        # a hard failure drops it so every replay does not re-synthesise and re-fail.
        if not isinstance(e, TimeoutError):
            store.delete(f"tts_job:{audio_id}")
        return None


def audio_url(audio_id: str) -> str:
    base = settings.public_base_url.rstrip("/") if settings.public_base_url else ""
    return f"{base}/api/media/audio/{audio_id}.wav"



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
