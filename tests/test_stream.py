"""Real-time stream pipeline: answer XML, μ-law codec and a full caller turn over the WebSocket."""

import asyncio
import base64
import json

import numpy as np


def test_mulaw_roundtrip():
    from app.services.voice_stream import mulaw_to_pcm16, pcm16_to_mulaw
    pcm = (np.sin(np.linspace(0, 300, 8000)) * 20000).astype("<i2")
    back = np.frombuffer(mulaw_to_pcm16(pcm16_to_mulaw(pcm.tobytes())), "<i2")
    assert np.corrcoef(pcm, back)[0, 1] > 0.999


def test_answer_returns_bidirectional_stream(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "voice_mode", "stream")
    xml = client.post("/api/plivo/answer", data={"From": "919000000001", "To": "918000000000", "CallUUID": "s-1"}).text
    assert "<Stream" in xml and 'bidirectional="true"' in xml and "wss://agent.test/api/plivo/stream?sid=" in xml
    assert "<GetInput" not in xml


def test_stream_rejects_unknown_session(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/api/plivo/stream?sid=nope") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4404


def test_stream_conversation_turn(client, base, monkeypatch):
    from app.core.config import settings
    from app.services import call_session, voice_stream

    monkeypatch.setattr(settings, "voice_mode", "stream")
    monkeypatch.setattr(settings, "turn_end_grace_ms", 10)

    class FakeSTT:
        """Says 'what is the price' after the first audio frame."""
        def __init__(self, language):
            self.queue: asyncio.Queue = asyncio.Queue()
            self.spoke = False
            self.ws = self

        async def connect(self):
            return self

        async def send_pcm(self, pcm):
            if not self.spoke:
                self.spoke = True
                for m in ({"type": "events", "data": {"signal_type": "START_SPEECH"}},
                          {"type": "events", "data": {"signal_type": "END_SPEECH"}},
                          {"type": "data", "data": {"transcript": "what is the price"}}):
                    await self.queue.put(json.dumps(m))

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.queue.get()

        async def close(self):
            pass

    class FakeTTSSocket:
        def __init__(self):
            self.out: asyncio.Queue = asyncio.Queue()

        async def send(self, raw):
            m = json.loads(raw)
            if m["type"] == "text":
                await self.out.put(json.dumps({"type": "audio", "data": {"audio": base64.b64encode(b"" * 400).decode()}}))
            elif m["type"] == "flush":
                await self.out.put(json.dumps({"type": "event", "data": {"event_type": "final"}}))

        async def recv(self):
            return await self.out.get()

    class FakeTTS:
        def __init__(self, language, speaker):
            self.sock = FakeTTSSocket()

        def warm(self):
            pass

        async def get(self):
            return self.sock

        async def reset(self):
            pass

        async def ping(self):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(voice_stream, "SarvamSTT", FakeSTT)
    monkeypatch.setattr(voice_stream, "SarvamTTS", FakeTTS)
    xml = client.post("/api/plivo/answer", data={"From": "919000000002", "To": "918000000000", "CallUUID": "s-2"}).text
    sid = xml.split("sid=")[1].split("<")[0].strip()

    with client.websocket_connect(f"/api/plivo/stream?sid={sid}") as ws:
        ws.send_text(json.dumps({"event": "start", "start": {"streamId": "st-1", "callId": "s-2"}}))
        greeting = json.loads(ws.receive_text())
        assert greeting["event"] == "playAudio" and greeting["media"]["contentType"] == "audio/x-mulaw"
        ws.send_text(json.dumps({"event": "media", "media": {"payload": base64.b64encode(b"\xff" * 160).decode()}}))
        events = []
        for _ in range(10):
            events.append(json.loads(ws.receive_text()))
            if sum(e["event"] == "checkpoint" for e in events) >= 2:
                break
        assert any(e["event"] == "playAudio" for e in events)
        ws.send_text(json.dumps({"event": "stop"}))

    history = call_session.get(sid)["history"]
    assert [t["role"] for t in history] == ["assistant", "customer", "assistant"]
    assert history[1]["text"] == "what is the price"


def test_silence_gate_sends_speech_with_preroll_and_tail():
    from app.services.voice_stream import SilenceGate
    gate = SilenceGate()
    quiet, loud = bytes(320), (np.ones(160) * 3000).astype("<i2").tobytes()
    assert sum(len(gate.process(quiet)) for _ in range(100)) == 0      # silence is not billed
    burst = gate.process(loud)
    assert len(burst) == 1 + SilenceGate.PREROLL                        # onset keeps 200 ms pre-roll
    tail = sum(len(gate.process(quiet)) for _ in range(200))
    assert tail == SilenceGate.TAIL                                      # trailing silence for END_SPEECH


def test_compact_history_soon_saves_compacted_upto(monkeypatch):
    """After a background compaction, the next turn's window must anchor on compacted_upto,
    not resend the turns compact_history already summarised."""
    from app.services import agent, voice_stream

    stream = voice_stream.CallStream.__new__(voice_stream.CallStream)
    stream.session_id = "compact-test"
    stream.session = {"history": [{"role": "customer", "text": f"turn {i}"} for i in range(agent.COMPACT_AFTER_TURNS)],
                       "summary": None}
    stream.compacted_at = 0
    saved = {}
    stream.save_session = lambda **k: saved.update(k)

    monkeypatch.setattr(agent, "compact_history", lambda history, prior=None, since=0: "caller wants pricing")
    captured = []
    monkeypatch.setattr(voice_stream.asyncio, "create_task", lambda coro: captured.append(coro))

    stream.compact_history_soon()
    assert captured, "compaction should have been scheduled"
    asyncio.run(captured[0])

    assert saved.get("summary") == "caller wants pricing"
    assert saved.get("compacted_upto") == agent.compacted_upto(stream.session["history"])
