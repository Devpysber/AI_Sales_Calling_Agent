"""
Live supervision across API replicas.

A call's audio stream lives on whichever replica Plivo connected to, but a supervisor's
browser can land on any replica. With Redis configured, the replica running the call
publishes its events (state, transcript, audio while someone listens) and listens for
commands on per-call channels; any other replica bridges a supervisor to it.

    live:evt:<call_id>     call -> supervisors   (JSON events)
    live:cmd:<call_id>     supervisors -> call   (JSON commands)
    live:on:<call_id>      key: call is running somewhere (TTL, refreshed)
    live:ears:<call_id>    key: a remote supervisor is listening (TTL, refreshed)
    live:state:<call_id>   key: last state snapshot for new supervisors

Without Redis (single process) none of this runs and supervision stays in-process.
"""

import asyncio
import contextlib
import json

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)
PREFIX = "va:live:"
ALIVE_TTL = 30
EARS_TTL = 20

_client = None


def enabled() -> bool:
    return bool(settings.redis_url)


def client():
    global _client
    if _client is None:
        import redis.asyncio as aioredis
        _client = aioredis.Redis.from_url(settings.redis_url, socket_timeout=5, health_check_interval=30)
    return _client


def _k(kind: str, call_id) -> str:
    return f"{PREFIX}{kind}:{call_id}"


# ---------------- replica running the call ----------------

class CallBridge:
    """Attached to a CallStream: mirrors events to Redis and applies remote commands."""

    def __init__(self, stream, call_id):
        self.stream, self.call_id = stream, call_id
        self.task: asyncio.Task | None = None
        self.remote_listening = False

    def start(self):
        if enabled() and self.call_id:
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        r = client()
        pubsub = r.pubsub()
        await pubsub.subscribe(_k("cmd", self.call_id))
        try:
            ticks = 0
            while not self.stream.closed:
                if ticks % 10 == 0:
                    await r.set(_k("on", self.call_id), "1", ex=ALIVE_TTL)
                    self.remote_listening = bool(await r.exists(_k("ears", self.call_id)))
                ticks += 1
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg.get("data"):
                    await self._apply(json.loads(msg["data"]))
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 - supervision must never break the call
            log.warning("Live bridge for call %s stopped: %s", self.call_id, e)
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await r.delete(_k("on", self.call_id))

    async def _apply(self, cmd: dict):
        action = cmd.get("action")
        try:
            if action == "human_audio":
                import base64
                await self.stream.human_audio(base64.b64decode(cmd.get("audio") or ""))
            elif action == "listen":
                self.remote_listening = bool(cmd.get("on"))
            elif action:
                await self.stream.control(action, text=cmd.get("text", ""), now=bool(cmd.get("now")), by="admin")
        except ValueError as e:
            await self.emit({"type": "error", "message": str(e)})

    async def emit(self, event: dict):
        if not (enabled() and self.call_id):
            return
        with contextlib.suppress(Exception):
            r = client()
            data = json.dumps(event, ensure_ascii=False)
            await r.publish(_k("evt", self.call_id), data)
            if event.get("type") == "state":
                await r.set(_k("state", self.call_id), data, ex=ALIVE_TTL * 20)

    def emit_nowait(self, event: dict):
        if enabled() and self.call_id and (event.get("type") != "audio" or self.remote_listening):
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(self.emit(event))

    async def stop(self):
        if self.task:
            self.task.cancel()


# ---------------- replica serving a supervisor ----------------

async def is_live_elsewhere(call_id) -> bool:
    if not enabled():
        return False
    with contextlib.suppress(Exception):
        return bool(await client().exists(_k("on", call_id)))
    return False


async def relay(websocket, call_id):
    """Bridge a supervisor WebSocket to a call running on another replica."""
    r = client()
    pubsub = r.pubsub()
    await pubsub.subscribe(_k("evt", call_id))
    snapshot = await r.get(_k("state", call_id))
    if snapshot:
        await websocket.send_text(snapshot.decode() if isinstance(snapshot, bytes) else snapshot)

    async def downstream():
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg.get("data"):
                text = msg["data"].decode() if isinstance(msg["data"], bytes) else msg["data"]
                await websocket.send_text(text)
                if '"ended"' in text:
                    return
            elif not await r.exists(_k("on", call_id)):
                await websocket.send_json({"type": "ended"})
                return

    async def upstream():
        listening = False

        async def keep_ears():
            while True:
                if listening:
                    await r.set(_k("ears", call_id), "1", ex=EARS_TTL)
                await asyncio.sleep(EARS_TTL / 2)

        ears = asyncio.create_task(keep_ears())
        try:
            while True:
                raw = await websocket.receive_text()
                cmd = json.loads(raw)
                if cmd.get("action") == "listen":
                    listening = bool(cmd.get("on"))
                    if listening:
                        await r.set(_k("ears", call_id), "1", ex=EARS_TTL)
                await r.publish(_k("cmd", call_id), raw)
        finally:
            ears.cancel()

    tasks = [asyncio.create_task(downstream()), asyncio.create_task(upstream())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe()
