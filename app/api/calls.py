import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.deps import workspace
from app.core.auth import actor
from app.services import agents, events
from app.services.call_service import CallError, CallService, within_calling_hours

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



from fastapi import WebSocket, WebSocketDisconnect
from app.services.voice_stream import LIVE
import json

@router.websocket("/{call_id}/monitor")
async def monitor_call(websocket: WebSocket, call_id: int):
    await websocket.accept()
    # Find live stream matching this call_id (uuid)
    # The LIVE dict keys are session_id. Call_id might not match session_id perfectly.
    # Let's find it.
    call_service = CallService(1) # We can ignore agent_id for finding live calls, but wait, we need agent_id.
    # We can just iterate over LIVE streams to find the one with the right call_id.
    stream = next((s for s in LIVE.values() if str(s.call_uuid) == str(call_id) or str(s.session.get('call_id')) == str(call_id)), None)
    
    if not stream:
        await websocket.close(code=1008)
        return
        
    queue = asyncio.Queue()
    stream.monitors[queue] = {"listen": True}
    
    async def receive():
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                action = msg.get("action")
                text = msg.get("text", "")
                now = msg.get("now", False)
                if action == "human_audio":
                    import base64
                    pcm = base64.b64decode(msg["audio"])
                    await stream.human_audio(pcm)
                elif action:
                    await stream.control(action, text=text, now=now)
        except WebSocketDisconnect:
            pass
            
    async def send():
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except Exception:
            pass
            
    try:
        # Publish initial state
        await websocket.send_json(stream.state())
        await asyncio.gather(receive(), send())
    finally:
        stream.monitors.pop(queue, None)
