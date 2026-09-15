"""Supervision across replicas: events and commands travel over Redis pub/sub."""

import asyncio
import json

import fakeredis.aioredis
import pytest


class FakeStream:
    closed = False

    def __init__(self):
        self.commands = []

    async def control(self, action, text="", now=False, by="admin"):
        if action == "bad":
            raise ValueError("nope")
        self.commands.append((action, text))

    async def human_audio(self, pcm):
        self.commands.append(("audio", len(pcm)))


@pytest.mark.parametrize("_", [0])
def test_bridge_relays_commands_and_events(monkeypatch, _):
    from app.core.config import settings
    from app.services import live_bridge

    server = fakeredis.FakeServer()
    monkeypatch.setattr(settings, "redis_url", "redis://fake")
    monkeypatch.setattr(live_bridge, "_client", fakeredis.aioredis.FakeRedis(server=server))

    async def scenario():
        stream = FakeStream()
        bridge = live_bridge.CallBridge(stream, 42)
        bridge.start()
        await asyncio.sleep(0.2)
        assert await live_bridge.is_live_elsewhere(42)

        r = live_bridge.client()
        sub = r.pubsub()
        await sub.subscribe("va:live:evt:42")
        await bridge.emit({"type": "state", "mode": "ai"})
        got = None
        for _ in range(20):
            msg = await sub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg:
                got = json.loads(msg["data"])
                break
        assert got == {"type": "state", "mode": "ai"}
        assert json.loads(await r.get("va:live:state:42"))["mode"] == "ai"

        await r.publish("va:live:cmd:42", json.dumps({"action": "guide", "text": "offer a demo"}))
        for _ in range(30):
            if stream.commands:
                break
            await asyncio.sleep(0.1)
        assert stream.commands == [("guide", "offer a demo")]

        stream.closed = True
        await bridge.stop()

    asyncio.run(scenario())


def test_production_checks(monkeypatch):
    from app import main
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", "short")
    monkeypatch.setattr(settings, "public_base_url", "http://insecure")
    problems = " ".join(main.production_problems())
    assert "SECRET_KEY" in problems and "https" in problems
    monkeypatch.setattr(settings, "environment", "development")
    assert main.production_problems() == []
