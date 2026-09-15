"""
Real-time phone conversation over a Plivo bidirectional audio stream.

    caller audio (μ-law 8 kHz) --> Sarvam streaming STT (VAD + transcript)
                                        |
                                 end of caller turn
                                        |
                             LLM (agent.respond, thread)
                                        |
                  Sarvam TTS per sentence, in parallel (PCM 8 kHz)
                                        |
    caller <-- playAudio (μ-law) <------+

Turn taking:
- Sarvam emits START_SPEECH / END_SPEECH and a transcript right after END_SPEECH.
  The agent answers TURN_END_GRACE_MS after a transcript unless the caller starts
  speaking again (their words are then merged into one turn).
- Barge-in: if the caller starts speaking while the agent is talking, queued
  audio is cleared (clearAudio) and the in-flight reply is cancelled.
- Silence: after the agent finishes speaking, SILENCE_PROMPT_SECONDS of quiet
  triggers "could you repeat"; after MAX_SILENT_PROMPTS the call ends politely.

Call state still lives in the shared session store, so /api/plivo/hangup and
the end-of-call summary work exactly as in gather mode.
"""

import asyncio
import base64
import contextlib
import json
import re
import threading
import time
from urllib.parse import urlencode

import numpy as np
import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.logging import get_logger
from app.services import agent, agents, call_session, tts
from app.services.call_service import CallService, session_agent

log = get_logger(__name__)

# Calls streaming on this process, by session id: live supervision (monitor, guide, take over) attaches here.
LIVE: dict[str, "CallStream"] = {}

SILENCE_PROMPT_SECONDS = 8.0
MAX_SILENT_PROMPTS = 2
PLAY_CHUNK_BYTES = 1600          # 200 ms of 8 kHz μ-law per playAudio message
SENTENCE_SPLIT = re.compile(r"(?<=[।.!?])\s+")
SENTENCE_END = re.compile(r"[।!?]|\.(?=\s)")
CLAUSE_END = re.compile(r"[,;:]\s")
LANGUAGE_SWITCH_CONFIDENCE = 0.6   # Sarvam language_probability needed to follow the caller into another language
SPOKEN_ONLY = ("Answer out loud in 1-2 short sentences of plain speech. Do not call any tool. "
               "If a meeting or callback time was agreed, repeat the day and time back to confirm it.")

# ---------------- G.711 μ-law ----------------


def _build_mulaw_decode() -> np.ndarray:
    u = ~np.arange(256, dtype=np.uint8)
    sign, exponent, mantissa = u & 0x80, (u >> 4) & 0x07, u & 0x0F
    magnitude = ((mantissa.astype(np.int32) << 3) + 0x84) << exponent.astype(np.int32)
    return np.where(sign != 0, 0x84 - magnitude, magnitude - 0x84).astype(np.int16)


_MULAW_DECODE = _build_mulaw_decode()


def mulaw_to_pcm16(data: bytes) -> bytes:
    return _MULAW_DECODE[np.frombuffer(data, dtype=np.uint8)].astype("<i2").tobytes()


def pcm16_to_mulaw(data: bytes) -> bytes:
    x = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.int32)
    sign = np.where(x < 0, 0x80, 0)
    x = np.minimum(np.abs(x), 32635) + 0x84
    exponent = np.clip(np.floor(np.log2(x)).astype(np.int32) - 7, 0, 7)
    mantissa = (x >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


# Caller asks to switch language ("हिन्दी में बोलो", "speak in English", "ગુજરાતીમાં"): name -> code.
LANGUAGE_REQUESTS = [
    (re.compile(r"hindi|हिन्दी|हिंदी|હિન્દી|હિંદી", re.I), "hi-IN"),
    (re.compile(r"english|इंग्लिश|अंग्रेज़ी|अंग्रेजी|ઇંગ્લિશ|અંગ્રેજી", re.I), "en-IN"),
    (re.compile(r"gujarati|गुजराती|ગુજરાતી", re.I), "gu-IN"),
    (re.compile(r"marathi|मराठी", re.I), "mr-IN"),
    (re.compile(r"tamil|तमिल|தமிழ்", re.I), "ta-IN"),
    (re.compile(r"telugu|तेलुगु|తెలుగు", re.I), "te-IN"),
    (re.compile(r"bengali|bangla|बंगाली|বাংলা", re.I), "bn-IN"),
    (re.compile(r"kannada|कन्नड़|ಕನ್ನಡ", re.I), "kn-IN"),
    (re.compile(r"malayalam|मलयालम|മലയാളം", re.I), "ml-IN"),
    (re.compile(r"punjabi|पंजाबी|ਪੰਜਾਬੀ", re.I), "pa-IN"),
]


def requested_language(text: str) -> str | None:
    """Language the caller asks the agent to speak. With two names ("not Gujarati, in Hindi") the last one wins."""
    hits = sorted((m.start(), code) for pattern, code in LANGUAGE_REQUESTS for m in pattern.finditer(text))
    return hits[-1][1] if hits else None


NAME_PATTERNS = [
    re.compile(r"(?:my name is|i am|i'm|this is|name's)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)"),
    re.compile(r"(?:मेरा नाम|मेरा नाम है)\s+([\u0900-\u097F]+(?:\s+[\u0900-\u097F]+)?)"),
    re.compile(r"(?:मैं)\s+([\u0900-\u097F]{2,}(?:\s+[\u0900-\u097F]{2,})?)\s+(?:बोल रहा|बोल रही)"),
]
NOT_NAMES = {"है", "हूँ", "हूं", "interested", "busy", "fine", "good", "calling", "looking"}


def spoken_name(text: str) -> str | None:
    """A name the caller states about themselves ("my name is Neha", "मेरा नाम नेहा है"); None when unsure."""
    for pattern in NAME_PATTERNS:
        m = pattern.search(text)
        if m:
            name = " ".join(w for w in m.group(1).split() if w.lower() not in NOT_NAMES and w not in NOT_NAMES).strip()
            if 2 <= len(name) <= 40:
                return name
    return None


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


# ---------------- Sarvam streaming STT ----------------


class SilenceGate:
    """
    Decides which 20 ms frames are worth sending to paid speech-to-text.
    Voiced frames pass, plus PREROLL frames before speech (soft word onsets) and TAIL frames after it
    (Sarvam needs trailing silence to emit END_SPEECH). Long silences - most of the time the agent is
    talking - are not sent at all. The noise floor adapts to the line.
    """

    PREROLL = 10      # 200 ms
    TAIL = 60         # 1.2 s

    def __init__(self):
        self.noise = 200.0
        self.preroll: list[bytes] = []
        self.tail_left = 0

    def process(self, pcm: bytes) -> list[bytes]:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        voiced = rms > max(350.0, self.noise * 2.5)
        if not voiced:
            self.noise = 0.95 * self.noise + 0.05 * rms
        if voiced:
            out = self.preroll + [pcm]
            self.preroll = []
            self.tail_left = self.TAIL
            return out
        if self.tail_left > 0:
            self.tail_left -= 1
            return [pcm]
        self.preroll = (self.preroll + [pcm])[-self.PREROLL:]
        return []


class SarvamSTT:
    """One streaming recognition socket; reconnects transparently if Sarvam closes it."""

    def __init__(self, language: str):
        self.language = language if language in tts.LANGUAGES else "unknown"
        self.ws = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        query = urlencode({"language-code": self.language, "model": settings.sarvam_stt_model, "mode": "transcribe",
                           "sample_rate": "8000", "input_audio_codec": "pcm_s16le", "vad_signals": "true"})
        return f"wss://api.sarvam.ai/speech-to-text/ws?{query}"

    async def connect(self):
        async with self._lock:
            if self.ws is None or self.ws.state.name != "OPEN":
                self.ws = await websockets.connect(self.url, additional_headers={"Api-Subscription-Key": settings.sarvam_api_key},
                                                   open_timeout=5, ping_interval=20, max_queue=256)
        return self.ws

    async def send_pcm(self, pcm: bytes):
        ws = self.ws
        if ws is None:
            return
        try:
            await ws.send(json.dumps({"audio": {"data": base64.b64encode(pcm).decode(), "sample_rate": "8000",
                                                "encoding": "audio/wav"}}))
        except websockets.ConnectionClosed:
            pass

    async def close(self):
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


class SarvamTTS:
    """
    Sarvam streaming TTS socket (text in, 8 kHz mu-law audio out), opened ahead of time so the
    first reply of a turn does not pay the connect cost.
    """

    def __init__(self, language: str, speaker: str | None):
        self.language = language if language in tts.LANGUAGES else "en-IN"
        self.speaker = speaker
        self.ws = None
        self._connecting: asyncio.Task | None = None

    @property
    def url(self) -> str:
        return f"wss://api.sarvam.ai/text-to-speech/ws?{urlencode({'model': settings.sarvam_tts_model, 'send_completion_event': 'true'})}"

    async def _open(self):
        ws = await websockets.connect(self.url, additional_headers={"Api-Subscription-Key": settings.sarvam_api_key},
                                      open_timeout=5, ping_interval=20, max_queue=512)
        config = {"target_language_code": self.language, "speech_sample_rate": "8000", "output_audio_codec": "mulaw",
                  "min_buffer_size": 30, "max_chunk_length": 150}
        if self.speaker:
            config["speaker"] = self.speaker
        await ws.send(json.dumps({"type": "config", "data": config}))
        return ws

    def warm(self):
        if (self.ws is None or self.ws.state.name != "OPEN") and (self._connecting is None or self._connecting.done()):
            self._connecting = asyncio.create_task(self._open())

    async def get(self):
        if self.ws is not None and self.ws.state.name == "OPEN":
            return self.ws
        self.warm()
        try:
            self.ws = await self._connecting
        finally:
            self._connecting = None
        return self.ws

    async def reset(self):
        """Drop everything in flight (barge-in) and open a fresh socket in the background."""
        old, self.ws = self.ws, None
        if old is not None:
            with contextlib.suppress(Exception):
                await old.close()
        self.warm()

    async def ping(self):
        if self.ws is not None and self.ws.state.name == "OPEN":
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"type": "ping"}))

    async def close(self):
        if self._connecting and not self._connecting.done():
            self._connecting.cancel()
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()


class _LocalSocket:
    """
    Socket-shaped wrapper over local IndicF5 so reply() works unchanged. Text is buffered into
    sentences (the first one may be cut at a clause to get audio out sooner) and each chunk is
    synthesized on the GPU as soon as it is complete, in order.
    """

    FIRST_CLAUSE_CHARS = 25

    def __init__(self, language: str, speaker: str | None):
        self.language, self.speaker = language, speaker
        self.state = type("State", (), {"name": "OPEN"})()
        self.buffer = ""
        self.first = True
        self.jobs: asyncio.Queue = asyncio.Queue()
        self.out: asyncio.Queue = asyncio.Queue()
        self.worker = asyncio.create_task(self._work())

    def _cut(self, final: bool) -> list[str]:
        chunks = []
        while True:
            m = SENTENCE_END.search(self.buffer)
            if not m and self.first and len(self.buffer) >= self.FIRST_CLAUSE_CHARS:
                m = CLAUSE_END.search(self.buffer, self.FIRST_CLAUSE_CHARS - 10)
            if not m:
                break
            chunks.append(self.buffer[:m.end()])
            self.buffer = self.buffer[m.end():]
            self.first = False
        if final and self.buffer.strip():
            chunks.append(self.buffer)
            self.buffer = ""
        return [c.strip() for c in chunks if re.search(r"[^\W\d_]", c)]

    async def send(self, raw: str):
        msg = json.loads(raw)
        if msg.get("type") == "text":
            self.buffer += msg["data"]["text"]
            for chunk in self._cut(final=False):
                await self.jobs.put(chunk)
        elif msg.get("type") == "flush":
            for chunk in self._cut(final=True):
                await self.jobs.put(chunk)
            await self.jobs.put(None)

    async def _work(self):
        try:
            while True:
                chunk = await self.jobs.get()
                if chunk is None:
                    await self.out.put(json.dumps({"type": "event", "data": {"event_type": "final"}}))
                    self.first = True
                    continue
                language = tts.detect_language(chunk, self.language)
                pcm = await asyncio.to_thread(tts.synthesize_pcm, chunk, language, self.speaker)
                mulaw = await asyncio.to_thread(pcm16_to_mulaw, pcm)
                await self.out.put(json.dumps({"type": "audio", "data": {"audio": base64.b64encode(mulaw).decode()}}))
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 - surfaced to reply()
            await self.out.put(json.dumps({"type": "error", "data": str(e)}))

    async def recv(self) -> str:
        return await self.out.get()

    async def close(self):
        self.state.name = "CLOSED"
        self.worker.cancel()


class LocalTTS(SarvamTTS):
    """Same interface as SarvamTTS, backed by the local IndicF5 model."""

    def warm(self):
        if self.ws is None or self.ws.state.name != "OPEN":
            self.ws = _LocalSocket(self.language, self.speaker)

    async def get(self):
        self.warm()
        return self.ws

    async def ping(self):
        pass


def make_tts(language: str, speaker: str | None):
    return LocalTTS(language, speaker) if settings.tts_engine.lower() == "indicf5" else SarvamTTS(language, speaker)


class ReplyFilter:
    """Cleans streamed LLM text for speech: drops <tags> and tool-call markup, detects the end-of-call marker."""

    def __init__(self):
        self.pending = ""
        self.end_call = False
        self.transfer = False
        self.muted = False

    def feed(self, delta: str) -> str:
        self.pending += delta
        out = ""
        while self.pending:
            if self.muted:
                # Tool-call arguments often mention end_call/meeting fields: never end the call from them.
                self.pending = self.pending[-16:]
                return out
            lt = self.pending.find("<")
            if lt < 0:
                out, self.pending = out + self.pending, ""
                break
            out += self.pending[:lt]
            gt = self.pending.find(">", lt)
            if gt < 0:
                self.pending = self.pending[lt:]
                if len(self.pending) > 40:  # a stray "<", not a tag
                    out, self.pending = out + self.pending, ""
                break
            tag = self.pending[lt:gt + 1].lower()
            self.pending = self.pending[gt + 1:]
            if tag == "<end>":
                self.end_call = True
            if tag == "<transfer>":
                self.transfer = True
            if "tool" in tag:
                self.muted = True
        return out

    def flush(self) -> str:
        rest, self.pending = ("" if self.muted else self.pending), ""
        return rest


# ---------------- the call ----------------


class CallStream:
    def __init__(self, ws: WebSocket, session_id: str):
        self.ws = ws
        self.session_id = session_id
        self.session = call_session.get(session_id)
        self.agent_id = session_agent(self.session) if self.session else None
        self.persona = agents.get_profile(self.agent_id) if self.agent_id else {}
        self.stream_id: str | None = None
        self.call_uuid: str | None = None
        # Auto-detect: the caller may answer in another language than the lead's; replies follow what they speak.
        self.stt = SarvamSTT("unknown")
        self.gate = SilenceGate() if settings.stt_silence_gate else None
        self.tts = make_tts(self.session.get("language", "en-IN") if self.session else "en-IN", self.persona.get("voice_speaker"))

        self.agent_speaking = False        # audio queued/playing at Plivo
        self.caller_speaking = False
        self.heard: list[str] = []         # transcripts of the caller's current turn
        self.reply_task: asyncio.Task | None = None
        self.commit_task: asyncio.Task | None = None
        self.mark = 0
        self.hangup_on_mark: str | None = None
        self.transfer_on_mark: str | None = None
        self.transferred = False
        self.quiet_since = time.monotonic()
        self.silent_prompts = 0
        self.closed = False
        self.usage = {"tts_chars": 0, "stt_seconds": 0.0, "llm_requests": 0, **((self.session or {}).get("usage") or {})}
        self.speech_ended_at: float | None = None

        # Live supervision
        self.mode = "ai"                   # "ai" answers the caller; "human" = supervisor speaks, AI stays silent
        self.guidance: str | None = None   # one-shot instruction for the next AI reply
        self.direction: str | None = None  # standing instruction for every AI reply until cleared
        self.monitors: dict[asyncio.Queue, dict] = {}
        from app.services.live_bridge import CallBridge
        self.bridge = CallBridge(self, self.session.get("call_id") if self.session else None)

    # ----- plumbing -----

    def meter(self, key: str, amount: float = 1):
        """Billable usage for cost tracking; saved on the call record when it ends."""
        self.usage[key] = self.usage.get(key, 0) + amount

    def lang_key(self) -> str:
        return "hi" if (self.session.get("language") or "").startswith("hi") else "en"

    async def send(self, message: dict):
        if not self.closed:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(message))

    async def play_pcm(self, pcm: bytes):
        await self.send_mulaw(await asyncio.to_thread(pcm16_to_mulaw, pcm), pcm)

    async def send_mulaw(self, mulaw: bytes, pcm: bytes | None = None):
        """Queue audio for the caller and mirror it to supervisors who are listening."""
        self.agent_speaking = True
        for i in range(0, len(mulaw), PLAY_CHUNK_BYTES):
            await self.send({"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                                             "payload": base64.b64encode(mulaw[i:i + PLAY_CHUNK_BYTES]).decode()}})
        self.publish_audio("agent", pcm if pcm is not None else mulaw, is_mulaw=pcm is None)

    # ----- live supervision -----

    def state(self) -> dict:
        return {"type": "state", "mode": self.mode, "agent_speaking": self.agent_speaking, "caller_speaking": self.caller_speaking,
                "thinking": bool(self.reply_task and not self.reply_task.done()), "direction": self.direction,
                "guidance": self.guidance}

    def publish(self, event: dict):
        for queue in list(self.monitors):
            if queue.qsize() < 400:
                queue.put_nowait(event)
        self.bridge.emit_nowait(event)  # supervisors connected to other replicas

    def publish_state(self):
        self.publish(self.state())

    def publish_audio(self, track: str, audio: bytes, is_mulaw: bool = False):
        listeners = [q for q, opts in self.monitors.items() if opts.get("listen")]
        if not listeners and not self.bridge.remote_listening:
            return
        pcm = mulaw_to_pcm16(audio) if is_mulaw else audio
        event = {"type": "audio", "track": track, "pcm": base64.b64encode(pcm).decode()}
        for queue in listeners:
            if queue.qsize() < 400:
                queue.put_nowait(event)
        self.bridge.emit_nowait(event)

    def turn(self, role: str, text: str, by: str | None = None):
        call_session.add_turn(self.session, role, text)
        if by:
            self.session["history"][-1]["by"] = by
        self.publish({"type": "turn", "role": role, "text": text, "by": by})

    async def control(self, action: str, text: str = "", now: bool = False, by: str = "admin") -> dict:
        """Supervisor commands. Scripted lines cost no LLM tokens; guidance steers the next AI reply."""
        text = (text or "").strip()
        if action == "takeover":
            self.mode = "human"
            await self.interrupt(force=True)
        elif action == "release":
            self.mode = "ai"
            self.quiet_since = time.monotonic()
        elif action == "guide":
            if not text:
                raise ValueError("Write an instruction for the agent.")
            self.guidance = text
            if now:
                await self.interrupt(force=True)
                self.reply_task = asyncio.create_task(self.reply(None, time.monotonic()))
        elif action == "direction":
            self.direction = text or None
        elif action == "say":
            if not text:
                raise ValueError("Write what the agent should say.")
            await self.interrupt(force=True)
            language = tts.detect_language(text, self.session.get("language") or "en-IN")
            pcm = await asyncio.to_thread(tts.synthesize_pcm, text, language, self.persona.get("voice_speaker"))
            self.meter("tts_chars", len(text))
            await self.play_pcm(pcm)
            self.turn("assistant", text, by=by)
            self.save_session()
            await self.checkpoint()
        elif action == "transfer":
            if not self.can_transfer():
                raise ValueError("Set a transfer number for this agent first (Inbound & transfer page).")
            from app.api.plivo import TRANSFER_LINES
            await self.interrupt(force=True)
            self.mode = "human"
            line = TRANSFER_LINES[self.lang_key()]
            self.turn("assistant", line, by=by)
            await self.say_fixed(line)
            self.transfer_on_mark = f"m{self.mark}"
        elif action == "stop_speaking":
            await self.interrupt(force=True)
        elif action == "end":
            from app.api.plivo import PROMPTS
            await self.interrupt(force=True)
            self.mode = "human"
            await self.say_fixed(PROMPTS["goodbye"][self.lang_key()], hangup=True)
        else:
            raise ValueError(f"Unknown action {action!r}")
        self.publish_state()
        return self.state()

    async def human_audio(self, pcm: bytes):
        """Supervisor's microphone (16-bit PCM, 8 kHz) straight to the caller, only while they have the call."""
        if self.mode == "human" and pcm:
            await self.send({"event": "playAudio", "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000,
                                                             "payload": base64.b64encode(pcm16_to_mulaw(pcm)).decode()}})

    async def checkpoint(self, hangup: bool = False, transfer: bool = False) -> str:
        self.mark += 1
        name = f"m{self.mark}"
        if hangup:
            self.hangup_on_mark = name
        if transfer:
            self.transfer_on_mark = name
        await self.send({"event": "checkpoint", "streamId": self.stream_id, "name": name})
        return name

    async def clear_audio(self):
        self.agent_speaking = False
        await self.send({"event": "clearAudio", "streamId": self.stream_id})

    async def say_fixed(self, text: str, hangup: bool = False):
        language = tts.detect_language(text, self.session.get("language") or "en-IN")
        pcm = await asyncio.to_thread(tts.cached_pcm, text, language, self.persona.get("voice_speaker"), self.usage)
        await self.play_pcm(pcm)
        await self.checkpoint(hangup=hangup)

    def can_transfer(self) -> bool:
        return bool("".join(c for c in self.persona.get("transfer_number", "") if c.isdigit()))

    async def transfer_call(self):
        """Hand the caller to the agent's human number. Plivo replaces the stream with a <Dial>, ending this socket."""
        if self.transferred or not self.call_uuid or not self.can_transfer():
            return
        self.transferred = True
        from app.services.plivo_service import PlivoService
        try:
            await asyncio.to_thread(PlivoService().transfer, self.call_uuid, self.session_id, self.session.get("call_id"))
            self.save_session(transferred=True)
            if self.session.get("call_id"):
                await asyncio.to_thread(CallService(self.agent_id).mark_transferred, self.session["call_id"],
                                        f"Transferred to {self.persona.get('transfer_number')}")
            log.info("Transferred session %s to %s", self.session_id[:8], self.persona.get("transfer_number"))
        except Exception as e:  # noqa: BLE001 - keep the AI on the line if Plivo refuses
            self.transferred = False
            log.error("Transfer failed for %s: %s", self.session_id[:8], e)

    async def hangup(self):
        if self.call_uuid:
            from app.services.plivo_service import PlivoService
            with contextlib.suppress(Exception):
                await asyncio.to_thread(PlivoService().hangup, self.call_uuid)

    def save_session(self, **fields):
        fresh = call_session.get(self.session_id) or self.session
        fresh.update(fields)
        fresh["history"] = self.session["history"]
        fresh["latencies"] = self.session.get("latencies", [])
        fresh["language"] = self.session.get("language")
        fresh["usage"] = self.usage
        call_session.save(fresh)
        self.session = fresh

    # ----- lifecycle -----

    async def run(self):
        if not self.session:
            await self.ws.close()
            return
        LIVE[self.session_id] = self
        self.bridge.start()
        self.tts.warm()
        try:
            await self.stt.connect()
        except Exception as e:
            log.error("Sarvam STT connect failed for %s: %s", self.session_id[:8], e)
        tasks = [asyncio.create_task(self.plivo_loop()), asyncio.create_task(self.stt_loop()),
                 asyncio.create_task(self.silence_loop())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.closed = True
            LIVE.pop(self.session_id, None)
            self.publish({"type": "ended"})
            await self.bridge.stop()
            for t in tasks + [self.reply_task, self.commit_task]:
                if t and not t.done():
                    t.cancel()
            await self.stt.close()
            await self.tts.close()
            log.info("Stream closed for session %s", self.session_id[:8])

    async def plivo_loop(self):
        try:
            while True:
                msg = json.loads(await self.ws.receive_text())
                event = msg.get("event")
                if event == "media":
                    payload = (msg.get("media") or {}).get("payload")
                    if payload:
                        pcm = mulaw_to_pcm16(base64.b64decode(payload))
                        self.publish_audio("caller", pcm)
                        for frame in (self.gate.process(pcm) if self.gate else [pcm]):
                            await self.stt.send_pcm(frame)
                            self.usage["stt_seconds"] += len(frame) / 16000  # 8 kHz, 16-bit
                elif event == "start":
                    start = msg.get("start") or {}
                    self.stream_id = start.get("streamId") or msg.get("streamId")
                    self.call_uuid = start.get("callId") or msg.get("callId")
                    log.info("Stream started session=%s call=%s", self.session_id[:8], self.call_uuid)
                    asyncio.create_task(self.greet())
                elif event == "playedStream":
                    if msg.get("name") == f"m{self.mark}":
                        self.agent_speaking = False
                        self.quiet_since = time.monotonic()
                        self.publish_state()
                    if msg.get("name") and msg.get("name") == self.hangup_on_mark:
                        await self.hangup()
                        return
                    if msg.get("name") and msg.get("name") == self.transfer_on_mark:
                        await self.transfer_call()
                        return
                elif event == "stop":
                    return
        except (WebSocketDisconnect, RuntimeError):
            return

    async def stt_loop(self):
        while not self.closed:
            try:
                ws = await self.stt.connect()
                async for raw in ws:
                    await self.on_stt(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Sarvam STT stream dropped (%s), reconnecting", e)
                await asyncio.sleep(0.3)

    async def silence_loop(self):
        ticks = 0
        while True:
            await asyncio.sleep(0.5)
            ticks += 1
            if ticks % 30 == 0:
                await self.tts.ping()
            busy = self.mode != "ai" or self.agent_speaking or self.caller_speaking or self.heard or (self.reply_task and not self.reply_task.done())
            if busy:
                self.quiet_since = time.monotonic()
                continue
            if time.monotonic() - self.quiet_since < SILENCE_PROMPT_SECONDS:
                continue
            self.silent_prompts += 1
            from app.api.plivo import PROMPTS
            caller_spoke = any(t["role"] != "assistant" for t in self.session.get("history", []))
            if self.silent_prompts > (MAX_SILENT_PROMPTS if caller_spoke else 1):
                await self.say_fixed(PROMPTS["goodbye"][self.lang_key()], hangup=True)
                return
            await self.say_fixed(PROMPTS["repeat"][self.lang_key()])
            self.quiet_since = time.monotonic()

    # ----- conversation -----

    async def greet(self):
        history = self.session["history"]
        # Resumed after an unanswered transfer: speak the latest agent line, not the original greeting
        text = history[-1]["text"] if history and history[-1]["role"] == "assistant" and len(history) > 1 else \
            next((t["text"] for t in history if t["role"] == "assistant"), None)
        if not text:
            text = agent.greeting(self.agent_id, self.session.get("lead") or {}, self.session.get("language") or "en-IN")
            self.turn("assistant", text)
            self.save_session()
        try:
            await self.say_fixed(text)
        except Exception as e:
            log.error("Greeting TTS failed: %s", e)

    async def on_stt(self, msg: dict):
        kind, data = msg.get("type"), msg.get("data") or {}
        if kind == "events":
            signal = data.get("signal_type")
            if signal == "START_SPEECH":
                self.caller_speaking = True
                self.silent_prompts = 0
                if self.commit_task and not self.commit_task.done():
                    self.commit_task.cancel()
                if self.mode == "ai" and (self.agent_speaking or (self.reply_task and not self.reply_task.done())):
                    await self.interrupt()
                self.publish_state()
            elif signal == "END_SPEECH":
                self.caller_speaking = False
                self.speech_ended_at = time.monotonic()
                self.publish_state()
        elif kind == "data":
            text = (data.get("transcript") or "").strip()
            spoken = data.get("language_code")
            if (text and spoken in tts.LANGUAGES and spoken != self.session.get("language")
                    and (data.get("language_probability") or 0) >= LANGUAGE_SWITCH_CONFIDENCE and len(text.split()) >= 2):
                await self.switch_language(spoken)
            if text and self.mode != "ai":
                # Supervisor has the call: log what the caller said, the AI does not answer
                self.turn("customer", text)
                self.save_session()
            elif text:
                self.publish({"type": "heard", "text": text})
                self.heard.append(text)
                if self.commit_task and not self.commit_task.done():
                    self.commit_task.cancel()
                self.commit_task = asyncio.create_task(self.commit_turn())
        elif kind == "error":
            log.warning("Sarvam STT error: %s", data)

    async def interrupt(self, force: bool = False):
        """Caller (or supervisor) talks over the agent: stop audio and drop the reply in flight."""
        if self.reply_task and not self.reply_task.done():
            self.reply_task.cancel()
        if self.agent_speaking or force:
            await self.clear_audio()
            await self.tts.reset()
            log.info("Barge-in on session %s", self.session_id[:8])

    async def switch_language(self, language: str):
        """Follow the caller's language: recognition, voice, prompts and the lead record."""
        if language == self.session.get("language") or language not in tts.LANGUAGES:
            return
        log.info("Language switch %s -> %s on session %s", self.session.get("language"), language, self.session_id[:8])
        self.session["language"] = language
        await self.tts.close()
        self.tts = make_tts(language, self.persona.get("voice_speaker"))
        self.tts.warm()
        self.save_session()
        if self.session.get("lead_id"):
            with contextlib.suppress(Exception):
                await asyncio.to_thread(CallService(self.agent_id).crm.update, self.session["lead_id"], {"language": language}, "ai",
                                        "lead.updated", f"Language switched to {tts.LANGUAGES[language]} on request")

    async def capture_caller_details(self, text: str):
        """New caller says their name: save it on the lead right away and let the agent use it from the next reply."""
        lead = self.session.get("lead") or {}
        if lead.get("name") or not self.session.get("lead_id"):
            return
        name = spoken_name(text)
        if not name:
            return
        lead["name"] = name
        collect = lead.get("collect")
        if collect is not None:
            lead["call_goal"] = agent.call_goal(lead, "inbound_new")
        self.session["lead"] = lead
        self.save_session()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(CallService(self.agent_id).crm.update, self.session["lead_id"], {"name": name}, "ai",
                                    "lead.updated", f"Caller introduced themselves as {name}")
        self.publish({"type": "caller", "name": name})

    async def commit_turn(self):
        await asyncio.sleep(settings.turn_end_grace_ms / 1000)
        if self.caller_speaking or not self.heard:
            return
        text = " ".join(self.heard)
        self.heard = []
        await self.capture_caller_details(text)
        wanted = requested_language(text)
        if wanted:
            await self.switch_language(wanted)
        if self.reply_task and not self.reply_task.done():
            self.reply_task.cancel()
        self.reply_task = asyncio.create_task(self.reply(text, self.speech_ended_at or time.monotonic()))

    async def reply(self, text: str | None, speech_ended_at: float):
        """
        LLM text streams straight into streaming TTS; audio chunks go to the caller as they arrive.
        text=None: the supervisor asked the agent to speak now (no new caller words).
        """
        if self.mode != "ai":
            return
        log.info("Customer said (%s): %s", self.session_id[:8], text)
        language = self.session.get("language") or "en-IN"
        supervised = bool(self.direction or self.guidance)
        guidance = " ".join(g for g in (self.direction, self.guidance) if g) or None
        self.guidance = None
        self.publish_state()
        prompt_text = text if text is not None else "(The customer is listening. Continue the call now, following the supervisor instruction.)"
        lead = self.session.get("lead") or {}
        if self.session.get("lead_id"):
            with contextlib.suppress(Exception):
                lead = await asyncio.to_thread(CallService(self.agent_id).crm.get, self.session["lead_id"]) or lead

        loop = asyncio.get_running_loop()
        deltas: asyncio.Queue = asyncio.Queue()
        stop = threading.Event()
        history = list(self.session["history"])

        def pump():
            try:
                for delta in agent.respond_stream(self.agent_id, history, prompt_text, lead, guidance, language):
                    if stop.is_set():
                        return
                    loop.call_soon_threadsafe(deltas.put_nowait, delta)
                loop.call_soon_threadsafe(deltas.put_nowait, None)
            except Exception as e:  # noqa: BLE001 - surfaced to the event loop
                loop.call_soon_threadsafe(deltas.put_nowait, e)

        threading.Thread(target=pump, daemon=True, name="llm-stream").start()
        self.meter("llm_requests")
        cleaner = ReplyFilter()
        spoken: list[str] = []
        first_audio_ms = None
        feeder = None
        try:
            tts_ws = await self.tts.get()

            async def feed():
                unsent = ""  # Sarvam rejects whitespace-only text messages

                async def push(chunk: str, final: bool = False):
                    nonlocal unsent
                    unsent += chunk
                    # Send whole words only: a lone vowel sign or "?" is rejected by Sarvam and would be lost
                    cut = len(unsent) if final else max(unsent.rfind(" "), unsent.rfind("\n")) + 1
                    ready = unsent[:cut]
                    if ready.strip() and re.search(r"[^\W\d_]|[ऀ-෿]", ready):
                        spoken.append(ready)
                        self.meter("tts_chars", len(ready))
                        await tts_ws.send(json.dumps({"type": "text", "data": {"text": ready}}))
                        unsent = unsent[cut:]

                while (item := await deltas.get()) is not None:
                    if isinstance(item, Exception):
                        raise item
                    await push(cleaner.feed(item))
                await push(cleaner.flush(), final=True)
                if "".join(spoken).strip():
                    await tts_ws.send(json.dumps({"type": "flush"}))

            feeder = asyncio.create_task(feed())
            while True:
                if feeder.done():
                    if feeder.exception():
                        raise feeder.exception()
                    if not "".join(spoken).strip():
                        break  # only markup: no audio will come
                try:
                    raw = await asyncio.wait_for(tts_ws.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "audio":
                    audio = base64.b64decode(msg["data"]["audio"])
                    if first_audio_ms is None:
                        first_audio_ms = int((time.monotonic() - speech_ended_at) * 1000)
                    await self.send_mulaw(audio)
                elif msg.get("type") == "event" and (msg.get("data") or {}).get("event_type") == "final":
                    break
                elif msg.get("type") == "error":
                    raise RuntimeError(f"Sarvam TTS: {msg.get('data')}")
        except asyncio.CancelledError:
            stop.set()
            if feeder:
                feeder.cancel()
            if text is not None:
                self.heard.insert(0, text)  # caller kept talking: answer everything together
            raise
        except Exception as e:
            stop.set()
            if feeder:
                feeder.cancel()
            log.error("Reply failed for session %s: %s", self.session_id[:8], e)
            await self.tts.reset()
            if text is not None:
                self.turn("customer", text)
            self.save_session()
            from app.api.plivo import PROMPTS
            await self.say_fixed(PROMPTS["error"][self.lang_key()], hangup=True)
            return

        reply = " ".join("".join(spoken).split())
        if not reply and not cleaner.end_call:
            # The model emitted a tool call instead of speech (typically when a meeting is agreed).
            # Ask once more, streaming and short, with an explicit spoken-only instruction.
            with contextlib.suppress(Exception):
                retry = ReplyFilter()
                spoken_only = ((guidance + " ") if guidance else "") + SPOKEN_ONLY
                self.meter("llm_requests")
                raw = await asyncio.to_thread(lambda: "".join(agent.respond_stream(self.agent_id, history, prompt_text, lead, spoken_only, language)))
                reply = " ".join((retry.feed(raw) + retry.flush()).split())
                cleaner.end_call = retry.end_call
                if reply:
                    language = tts.detect_language(reply, self.session.get("language") or "en-IN")
                    await self.play_pcm(await asyncio.to_thread(tts.synthesize_pcm, reply, language, self.persona.get("voice_speaker")))
                    self.meter("tts_chars", len(reply))
        if not reply:
            # Still nothing to say: goodbye if the model ended the call, otherwise ask the caller to repeat (never hang up).
            from app.api.plivo import PROMPTS
            reply = PROMPTS["goodbye" if cleaner.end_call else "repeat"][self.lang_key()]
            await self.say_fixed(reply)
        if text is not None:
            self.turn("customer", text)
        self.turn("assistant", reply, by="ai-guided" if supervised else None)
        if first_audio_ms is not None:
            self.session["latencies"] = (self.session.get("latencies") or []) + [first_audio_ms]
        self.save_session(silent_prompts=0)
        log.info("Turn: first audio %sms after caller stopped, session=%s", first_audio_ms, self.session_id[:8])
        if cleaner.transfer and self.can_transfer():
            await self.checkpoint(transfer=True)
        else:
            await self.checkpoint(hangup=cleaner.end_call)
