"""
Agent workspaces: create/list/update/delete agents, and each agent's profile,
playground, automation, analytics and activity.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import workspace
from app.core.auth import actor
from app.services import agent, agents, analytics, events, scheduler, tts
from app.services.call_service import within_calling_hours
from app.services.crm_service import CRMService
from app.services.llm import LLMError

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    color: str | None = None
    phone_number: str | None = None
    profile: dict | None = None
    copy_from: int | None = None


class AgentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    color: str | None = None
    phone_number: str | None = None
    status: str | None = None


@router.get("")
def list_agents():
    return {"agents": agents.list_agents(), "voices": tts.SPEAKERS, "languages": tts.LANGUAGES}


@router.post("")
def create_agent(body: AgentIn, request: Request):
    if body.copy_from and not agents.exists(body.copy_from):
        raise HTTPException(400, "The agent to copy from does not exist.")
    data = body.model_dump(exclude_none=True)
    try:
        return agents.create(data, actor=actor(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@router.get("/overview")
def overview(days: int = Query(14, ge=7, le=60)):
    return agents.overview(days)


@router.get("/{agent_id}")
def get_agent(agent_id: int = Depends(workspace)):
    return {**agents.get(agent_id), "profile": agents.get_profile(agent_id)}


@router.patch("/{agent_id}")
def update_agent(body: AgentPatch, request: Request, agent_id: int = Depends(workspace)):
    try:
        return agents.update(agent_id, body.model_dump(exclude_unset=True), actor=actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{agent_id}")
def delete_agent(request: Request, agent_id: int = Depends(workspace)):
    agents.delete(agent_id, actor=actor(request))
    return {"ok": True}


# ---------------- profile & playground ----------------

@router.get("/{agent_id}/profile")
def get_profile(agent_id: int = Depends(workspace)):
    return {"profile": agents.get_profile(agent_id), "voices": tts.SPEAKERS, "languages": tts.LANGUAGES}


@router.put("/{agent_id}/profile")
def update_profile(values: dict, request: Request, agent_id: int = Depends(workspace)):
    try:
        return agents.update_profile(agent_id, values, actor=actor(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


class Preview(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: str | None = None
    speaker: str | None = None


@router.post("/{agent_id}/voice-preview")
async def voice_preview(body: Preview, agent_id: int = Depends(workspace)):
    speaker = body.speaker or agents.get_profile(agent_id)["voice_speaker"]
    try:
        audio = await asyncio.to_thread(tts.synthesize, body.text, body.language, speaker)
    except Exception as e:
        raise HTTPException(502, str(e))
    return Response(audio, media_type="audio/wav")


class Turn(BaseModel):
    role: str
    text: str


class PlaygroundMessage(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = []
    lead_id: int | None = None
    speak: bool = True


@router.post("/{agent_id}/playground")
async def playground(body: PlaygroundMessage, agent_id: int = Depends(workspace)):
    """Talk to this agent in the browser exactly as it behaves on calls (same prompt, knowledge and voice)."""
    lead = (CRMService(agent_id).get(body.lead_id) if body.lead_id else None) or {"name": "Test Prospect"}
    history = [t.model_dump() for t in body.history]
    try:
        result = await asyncio.to_thread(agent.respond, agent_id, history, body.message, lead)
    except LLMError as e:
        raise HTTPException(502, str(e))
    if body.speak:
        persona = agents.get_profile(agent_id)
        try:
            language = result["language"] or tts.detect_language(result["reply"])
            audio = await asyncio.to_thread(tts.synthesize, result["reply"], language, persona["voice_speaker"])
            result["audio_url"] = f"/api/media/audio/{tts.store_audio(audio)}.wav"
        except Exception as e:
            result["audio_error"] = str(e)
    return result


@router.get("/{agent_id}/greeting")
def greeting_preview(lead_id: int | None = None, language: str = "en-IN", agent_id: int = Depends(workspace)):
    lead = (CRMService(agent_id).get(lead_id) if lead_id else None) or {"name": "Rahul"}
    return {"text": agent.greeting(agent_id, lead, language)}


# ---------------- automation ----------------

@router.get("/{agent_id}/automation")
def automation(agent_id: int = Depends(workspace)):
    cfg = agents.get_automation(agent_id)
    return {"settings": cfg, "jobs": scheduler.job_status(agent_id), "within_calling_hours": within_calling_hours(cfg)}


@router.put("/{agent_id}/automation")
def update_automation(values: dict, request: Request, agent_id: int = Depends(workspace)):
    try:
        return agents.update_automation(agent_id, values, actor=actor(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@router.post("/{agent_id}/automation/run/{job}")
async def run_job(job: str, request: Request, agent_id: int = Depends(workspace)):
    if job not in scheduler.JOBS:
        raise HTTPException(404, "Unknown job")
    return {"job": job, "result": await asyncio.to_thread(scheduler.run_job, agent_id, job, True, actor(request))}


# ---------------- insights ----------------

@router.get("/{agent_id}/analytics")
def analytics_report(days: int = Query(30, ge=7, le=180), agent_id: int = Depends(workspace)):
    return analytics.report(agent_id, days)


@router.get("/{agent_id}/activity")
def activity(type: str | None = None, lead_id: int | None = None, before_id: int | None = None, limit: int = 50,
             agent_id: int = Depends(workspace)):
    return events.list_events(agent_id, lead_id=lead_id, type_prefix=type, before_id=before_id, limit=min(limit, 200))
