import asyncio
import contextlib
import base64
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import require_admin, workspace
from app.core.auth import actor
from app.services import agents, events, team_service
from app.services.call_service import CallError, CallService, within_calling_hours
from app.services.voice_stream import LIVE

router = APIRouter(prefix="/api/agents/{agent_id}/calls", tags=["calls"])


class StartCall(BaseModel):
    lead_id: int
    purpose: str | None = None  # "confirm_meeting" | "follow_up"; None = normal sales call


@router.post("")
async def start(body: StartCall, request: Request, agent_id: int = Depends(workspace)):
    try:
        return await asyncio.to_thread(CallService(agent_id).start, body.lead_id, "manual", actor(request), body.purpose)
    except CallError as e:
        raise HTTPException(400, str(e))


@router.get("")
def list_calls(lead_id: int | None = None, status: str | None = None, direction: str | None = None,
               search: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
               agent_id: int = Depends(workspace)):
    return CallService(agent_id).list_calls(lead_id, status, direction, search, page, page_size)

@router.delete("", dependencies=[Depends(require_admin)])
def delete_all_calls(agent_id: int = Depends(workspace)):
    return CallService(agent_id).delete_history()


@router.get("/stats")
def stats(days: int = Query(14, ge=1, le=90), agent_id: int = Depends(workspace)):
    data = CallService(agent_id).stats(days)
    data["within_calling_hours"] = within_calling_hours(agents.get_automation(agent_id))
    return data


@router.get("/{call_id}")
def get(call_id: int, agent_id: int = Depends(workspace)):
    call = CallService(agent_id).get(call_id)
    if not call:
        raise HTTPException(404, "Call not found.")
    call["events"] = events.list_events(agent_id, call_id=call_id, limit=50)
    return call


@router.post("/{call_id}/hangup")
async def hangup(call_id: int, agent_id: int = Depends(workspace)):
    try:
        await asyncio.to_thread(CallService(agent_id).hangup, call_id)
        return {"ok": True}
    except CallError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Plivo error: {e}")




@router.websocket("/{call_id}/monitor")
async def monitor_call(websocket: WebSocket, call_id: int, agent_id: int):
    """Live supervision of one call: state + both audio tracks out, supervisor commands and microphone audio in."""
    from app.core.auth import COOKIE, auth_enabled, read_token
    from app.services import team_service

    # HTTP auth middleware does not run for WebSockets: check the session cookie here.
    payload = read_token(websocket.cookies.get(COOKIE)) if auth_enabled() else {"u": "admin"}
    try:
        allowed = bool(payload) and (payload.get("u") != "team" or (agent_id in payload.get("unlocked", []) and team_service.by_id(payload.get("team_id"))))
    except Exception:  # noqa: BLE001 - a lookup that fails is a denied connection, not a hung socket
        allowed = False
    if not allowed:
        await websocket.close(code=4401)
        return
    stream = next((s for s in LIVE.values() if s.agent_id == agent_id and str(s.session.get("call_id")) == str(call_id)), None)
    await websocket.accept()
    if not stream:
        from app.services import live_bridge
        owned = await asyncio.to_thread(CallService(agent_id).get, call_id) is not None
        if owned and await live_bridge.is_live_elsewhere(call_id):
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                await live_bridge.relay(websocket, call_id)  # call runs on another replica
            return
        with contextlib.suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "ended"})
            await websocket.close(code=1000)
        return

    queue: asyncio.Queue = asyncio.Queue()
    stream.monitors[queue] = {"listen": True}
    took_over = False

    async def receive():
        nonlocal took_over
        try:
            while True:
                msg = json.loads(await websocket.receive_text())
                action = msg.get("action")
                if action == "human_audio":
                    await stream.human_audio(base64.b64decode(msg.get("audio") or ""))
                elif action == "listen":
                    stream.monitors[queue]["listen"] = bool(msg.get("on"))
                elif action:
                    try:
                        await stream.control(action, text=msg.get("text", ""), now=bool(msg.get("now")))
                        if action in ("takeover", "release"):
                            took_over = action == "takeover"
                    except ValueError as e:
                        await websocket.send_json({"type": "error", "message": str(e)})
        except (WebSocketDisconnect, RuntimeError):
            pass

    async def send():
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                if event.get("type") == "ended":
                    return
        except Exception:  # noqa: BLE001 - socket closed
            pass

    try:
        await websocket.send_json({**stream.state(), "can_transfer": stream.can_transfer(),
                                   "transfer_number": team_service.transfer_line(stream.persona, stream.agent_id) or None})
        done, pending = await asyncio.wait([asyncio.create_task(receive()), asyncio.create_task(send())],
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    finally:
        stream.monitors.pop(queue, None)
        # A supervisor who took the call over and then lost the socket left the call in human mode
        # forever: the AI never spoke again and the caller sat on a silent line.
        if took_over and not stream.monitors and getattr(stream, "mode", "ai") == "human":
            with contextlib.suppress(Exception):
                await stream.control("release", by="system")
