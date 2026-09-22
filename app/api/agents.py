"""
Agent workspaces: create/list/update/delete agents, and each agent's profile,
playground, automation, analytics and activity.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Path
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import workspace, require_admin
from app.core import store
from app.core.auth import actor
from app.core.config import settings
from app.services import agent, agents, analytics, events, scheduler, tts
from app.services.call_service import within_calling_hours
from app.services.crm_service import CRMService
from app.services.llm import LLMError
from app.services.tts import TTSError

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
def list_agents(request: Request):
    user = getattr(request.state, "user", "admin")
    all_a = agents.list_agents()
    payload = getattr(request.state, "token_payload", {})
    if user == "team":
        unlocked = payload.get("unlocked", [])
        reduced = []
        for a in all_a:
            if a["id"] in unlocked:
                reduced.append({**a, "locked": False})
                continue
            # Locked workspace: name and colour for the switcher, nothing about its numbers or persona.
            reduced.append({
                "id": a["id"], "name": a["name"], "color": a.get("color"), "status": a.get("status"), "locked": True,
                "stats": {k: (None if k == "last_call_at" else 0) for k in (a.get("stats") or {"leads": 0, "hot": 0, "meetings": 0, "calls_today": 0, "connected_today": 0, "live": 0, "documents": 0, "last_call_at": None})},
                "persona": {k: "" for k in (a.get("persona") or {"agent_name": "", "company_name": "", "voice_speaker": "", "default_language": ""})},
                "setup": {k: False for k in (a.get("setup") or {})},
            })
        all_a = reduced
    return {"agents": all_a, "voices": tts.SPEAKERS, "languages": tts.LANGUAGES}


@router.post("")
def create_agent(body: AgentIn, request: Request, response: Response):
    user = getattr(request.state, "user", "")
    payload = getattr(request.state, "token_payload", {})
    team_id = payload.get("team_id")
    
    if body.copy_from and not agents.exists(body.copy_from):
        raise HTTPException(400, "The agent to copy from does not exist.")
    if body.copy_from and user == "team" and body.copy_from not in payload.get("unlocked", []):
        raise HTTPException(403, "LOCKED")
    if user == "team" and (body.profile or {}).get("agent_password"):
        raise HTTPException(403, "Administrator access required.")

    # A team member may only create the number of workspaces the admin allowed them (one by default).
    if user == "team":
        from app.services import team_service
        member = team_service.by_id(team_id) if team_id else None
        if not member:
            raise HTTPException(403, "Your team account was not found. Ask an administrator to sign you in again.")
        limit = team_service.agent_limit(member)
        used = agents.created_count(team_id)
        if used >= limit:
            raise HTTPException(403, f"You have used all {limit} agent{'s' if limit != 1 else ''} allowed on your account. "
                                     "Ask an administrator to raise your limit.")

    data = body.model_dump(exclude_none=True)
    try:
        from app.core.auth import actor
        result = agents.create(data, actor=actor(request), created_by=team_id if user == "team" else "admin")
        
        unlocked = payload.get("unlocked", [])
        if result["id"] not in unlocked:
            unlocked.append(result["id"])
            payload["unlocked"] = unlocked
            from app.core.auth import COOKIE, TTL, make_token
            https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
            response.set_cookie(COOKIE, make_token(payload), max_age=TTL, httponly=True, samesite="lax", secure=https, path="/")

        # The client seeds its agent list with this response and the shell reads .stats/.persona/.setup from
        # it before the refetch lands, so return the same shape as GET /api/agents, not the bare row.
        summary = next((a for a in agents.list_agents() if a["id"] == result["id"]), result)
        if user == "team":
            summary["locked"] = False
        return summary
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@router.get("/live")
def live(request: Request):
    """Live calls across agents, for the incoming-call banner (polled often, kept cheap)."""
    user = getattr(request.state, "user", "admin")
    payload = getattr(request.state, "token_payload", {})
    unlocked = payload.get("unlocked", []) if user == "team" else None
    # The newest event rides along so every open page can refresh when the agent changes something
    # from a live call (automation switched, lead updated, details sent) without a push channel.
    return {"live_calls": agents.live_calls(unlocked), "latest_event": events.latest(unlocked)}


@router.get("/overview")
def overview(request: Request, days: int = Query(14, ge=7, le=60)):
    user = getattr(request.state, "user", "admin")
    payload = getattr(request.state, "token_payload", {})
    unlocked = payload.get("unlocked", []) if user == "team" else None
    return agents.overview(days, unlocked)


@router.get("/{agent_id}")
def get_agent(agent_id: int = Depends(workspace)):
    return {**agents.get(agent_id), "profile": agents.get_profile(agent_id)}


@router.patch("/{agent_id}")
def update_agent(body: AgentPatch, request: Request, agent_id: int = Depends(workspace)):
    try:
        return agents.update(agent_id, body.model_dump(exclude_unset=True), actor=actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


class Unlock(BaseModel):
    password: str

@router.post("/{agent_id}/unlock")
def unlock_agent(body: Unlock, request: Request, response: Response, agent_id: int = Path(..., ge=1)):
    if not agents.exists(agent_id):
        raise HTTPException(404, "Agent not found.")
        
    user = getattr(request.state, "user", "")
    if user == "admin" or user == "api":
        return {"ok": True}
        
    if user == "team":
        p = agents.get_profile(agent_id)
        t_pass = (p.get("agent_password") or "").strip()
        if not t_pass:
            raise HTTPException(400, "This agent does not have a password set.")
        import hmac
        if hmac.compare_digest(body.password, t_pass):
            payload = getattr(request.state, "token_payload", {})
            unlocked = payload.get("unlocked", [])
            if agent_id not in unlocked:
                unlocked.append(agent_id)
                payload["unlocked"] = unlocked
                from app.core.auth import COOKIE, TTL, make_token
                https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
                response.set_cookie(COOKIE, make_token(payload), max_age=TTL, httponly=True, samesite="lax", secure=https, path="/")
            return {"ok": True}
            
    raise HTTPException(403, "Invalid password.")


@router.delete("/{agent_id}", dependencies=[Depends(require_admin)])
def delete_agent(request: Request, agent_id: int = Depends(workspace)):
    agents.delete(agent_id, actor=actor(request))
    return {"ok": True}


# ---------------- profile & playground ----------------

@router.get("/{agent_id}/profile")
def get_profile(request: Request, agent_id: int = Depends(workspace)):
    from app.services import team_service
    profile = dict(agents.get_profile(agent_id))
    if getattr(request.state, "user", "") not in ("admin", "api"):
        profile.pop("agent_password", None)  # the vault password gates team members; they never see it
    # Who the transfer number actually reaches. The names live in Sales Team Accounts, so a routing
    # page reading the profile alone could only ever show a bare number, or an empty row.
    contacts = []
    for part in str(profile.get("transfer_number") or "").split(","):
        number = part.strip()
        if number:
            contacts.append({"phone": number, "name": team_service.name_for(number, agent_id)})
    return {"profile": profile, "transfer_contacts": contacts,
            "voices": tts.SPEAKERS, "languages": tts.LANGUAGES}


@router.put("/{agent_id}/profile")
def update_profile(values: dict, request: Request, agent_id: int = Depends(workspace)):
    if "agent_password" in values and getattr(request.state, "user", "") not in ("admin", "api"):
        raise HTTPException(403, "Administrator access required.")
    try:
        return agents.update_profile(agent_id, values, actor=actor(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


class Preview(BaseModel):
    text: str = Field(..., max_length=1000)
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


class PlaygroundMessage(BaseModel):
    message: str = ""
    purpose: str | None = None
    lead_id: int | None = None
    history: list[dict] = Field(default_factory=list)


def _playground_identity(request: Request) -> str | None:
    """Who the monthly rehearsal budget belongs to; None when it does not apply (admin, API token, auth off)."""
    user = getattr(request.state, "user", None)
    if user != "team":
        return None
    payload = getattr(request.state, "token_payload", {}) or {}
    return f"team:{payload.get('team_id')}"


def playground_usage(request: Request) -> dict:
    """Rehearsals started this calendar month against the limit; a rehearsal is the first customer line of a session."""
    from datetime import datetime, timezone

    # Admin sets the allowance on Settings -> Secrets (stored with the pricing); the env value is the default.
    from app.services.settings_service import SettingsService
    stored = (SettingsService().get_state("secrets") or {}).get("playground_monthly_limit")
    try:
        limit = int(float(stored)) if stored not in (None, "") else int(settings.playground_monthly_limit or 0)
    except (TypeError, ValueError):
        limit = int(settings.playground_monthly_limit or 0)
    now = datetime.now(timezone.utc)
    resets = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
              .replace(year=now.year + (now.month == 12), month=1 if now.month == 12 else now.month + 1))
    identity = _playground_identity(request)
    if not identity or not limit:
        return {"used": 0, "limit": limit, "remaining": None, "resets_at": resets.isoformat(), "exempt": True}
    used = int(store.get_json(f"playground:{identity}:{now:%Y-%m}", 0) or 0)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used), "resets_at": resets.isoformat(), "exempt": False}


def _count_playground_try(request: Request, usage: dict):
    identity = _playground_identity(request)
    if not identity or usage.get("exempt"):
        return
    from datetime import datetime, timezone

    key = f"playground:{identity}:{datetime.now(timezone.utc):%Y-%m}"
    store.set_json(key, usage["used"] + 1, ttl=40 * 24 * 3600)


@router.get("/{agent_id}/playground/usage")
def playground_usage_endpoint(request: Request, agent_id: int = Depends(workspace)):
    return playground_usage(request)


@router.post("/{agent_id}/playground")
async def playground(body: PlaygroundMessage, request: Request, agent_id: int = Depends(workspace)):
    """Talk to this agent in the browser exactly as it behaves on calls (same prompt, knowledge and voice)."""
    usage = playground_usage(request)
    new_try = not any(t.get("role") == "customer" for t in body.history)
    if new_try and not usage["exempt"] and usage["used"] >= usage["limit"]:
        raise HTTPException(429, f"PLAYGROUND_LIMIT: you have used all {usage['limit']} rehearsals for this month. "
                                 f"The allowance resets on {usage['resets_at'][:10]}.")
    lead = (CRMService(agent_id).get(body.lead_id) if body.lead_id else None) or {"name": "Test Prospect"}
    goal = agent.call_goal(lead, body.purpose)
    if goal:
        lead = {**lead, "call_purpose": body.purpose, "call_goal": goal}
    history = list(body.history)
    # The rehearsal follows the same cost guardrails as a live call: characters the agent has spoken so far
    # against TTS_CHARS_PER_CALL pick the same steer voice_stream would give at that point.
    from app.services.voice_stream import BUDGET_GUIDANCE, STEER_GUIDANCE
    spoken = sum(len(t.get("text") or "") for t in history if t.get("role") == "assistant")
    char_budget = int(settings.tts_chars_per_call or 0)
    guidance = None
    if char_budget and spoken >= char_budget:
        guidance = BUDGET_GUIDANCE
    elif char_budget and spoken >= 0.75 * char_budget:
        guidance = STEER_GUIDANCE
    try:
        # Same knowledge path as a live turn (keyword search, no embedding round-trip): the rehearsal shows the
        # latency and answers a real call gets, and a turn is not held up to a second for a paid embedding.
        res = await asyncio.to_thread(agent.respond, agent_id, history, body.message, lead, use_embeddings=False, guidance=guidance)
    except LLMError as e:
        raise HTTPException(502, str(e))
    res["spoken_chars"] = spoken + len(res.get("reply") or "")
    res["char_budget"] = char_budget
    res["steer"] = "budget" if guidance is BUDGET_GUIDANCE else "steer" if guidance is STEER_GUIDANCE else None
    # A voice outage (quota, network) must not hide the text reply: return it without audio and say why.
    try:
        profile = agents.get_profile(agent_id)
        language = res.get("language") or tts.detect_language(res["reply"], profile.get("default_language") or "en-IN")
        audio_id = await asyncio.to_thread(tts.prepare_audio_id, res["reply"], language, profile["voice_speaker"])
        res["audio_url"] = tts.audio_url(audio_id) if audio_id else ""
    except TTSError as e:
        res["audio_url"] = ""
        res["audio_error"] = str(e)
    if new_try:
        _count_playground_try(request, usage)
        usage = playground_usage(request)
    res["usage"] = usage
    return res


@router.get("/{agent_id}/greeting")
def greeting_preview(lead_id: int | None = None, language: str = "en-IN", purpose: str | None = None,
                     agent_id: int = Depends(workspace)):
    lead = (CRMService(agent_id).get(lead_id) if lead_id else None) or ({} if purpose == "inbound" else {"name": "Rahul"})
    if purpose == "confirm_meeting" and not lead.get("meeting_at"):
        purpose = "follow_up"  # same rule as dialling: nothing to confirm without a meeting time
    if purpose in ("inbound", "confirm_meeting", "follow_up"):
        lead = {**lead, "call_purpose": purpose}
    return {"text": agent.greeting(agent_id, lead, language)}


# ---------------- automation ----------------

@router.get("/{agent_id}/automation")
def automation(agent_id: int = Depends(workspace)):
    cfg = agents.get_automation(agent_id)
    return {"settings": cfg, "jobs": scheduler.job_status(agent_id), "within_calling_hours": within_calling_hours(cfg)}


@router.get("/{agent_id}/inbound-owner")
def get_inbound_owner(agent_id: int = Depends(workspace)):
    """Which agent answers calls to this agent's line (shared numbers: many dial out, one answers)."""
    number = agents.caller_id(agent_id)
    default = "".join(c for c in (settings.plivo_phone_number or "") if c.isdigit())
    digits = lambda a: "".join(c for c in (a.get("phone_number") or "") if c.isdigit())  # noqa: E731
    rows = agents.list_agents()
    # Agents on this line: on the default number, everyone without a number of their own; on an agent's own
    # number, only the agents that carry that exact number.
    sharing = [a for a in rows if digits(a) == number or (number == default and digits(a) == "")]
    # Who answers: the designated agent, else (own number) the first agent carrying it — the same fallback
    # for_inbound() applies on a real call, so the page never shows "no agent designated" for a line that has one.
    owner = agents.inbound_owner(number) if number else None
    if owner is None and number and number != default:
        owner = next((a["id"] for a in sharing if digits(a) == number), None)
    return {"number": number, "owner_id": owner, "own_number": bool(number and number != default),
            "sharing": [{"id": a["id"], "name": a["name"]} for a in sharing]}


@router.put("/{agent_id}/inbound-owner")
def put_inbound_owner(request: Request, agent_id: int = Depends(workspace)):
    """Make this agent the one that answers inbound calls on its line."""
    number = agents.caller_id(agent_id)
    try:
        return agents.set_inbound_owner(number, agent_id, actor=actor(request))
    except ValueError as e:
        raise HTTPException(400, str(e))


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
