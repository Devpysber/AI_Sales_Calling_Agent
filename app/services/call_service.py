"""
Call lifecycle: place calls, run conversation turns, record outcomes.
A CallService bound to an agent only sees and places that agent's calls.
"""

import contextlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models.call import Call
from app.models.lead import Lead
from app.services import agent, agents, call_session, events, tts
from app.services.crm_service import CRMService

log = get_logger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

ACTIVE = ("Queued", "Ringing", "In Progress")
RETRIABLE = ("No Answer", "Busy", "Failed")
PLIVO_STATUS = {"completed": "Completed", "busy": "Busy", "no-answer": "No Answer", "timeout": "No Answer",
                "failed": "Failed", "cancel": "Canceled", "canceled": "Canceled", "machine": "No Answer"}

# Bounded worker pool for AI turns (LLM + TTS are network-bound)
_turn_pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="turn")


class CallError(Exception):
    pass


# Pipeline order the AI may only advance through; exits (lost, not interested, DNC) are always allowed.
JOURNEY = ["New", "Contacted", "Interested", "Follow Up", "Meeting Booked", "Closed Won"]
EXIT_STAGES = {"Not Interested", "Do Not Call", "Closed Lost"}


def _ai_may_move(current: str | None, proposed: str) -> bool:
    """AI stage changes never move a lead backwards (a short follow-up call must not undo a booked meeting)."""
    if proposed in EXIT_STAGES:
        return current != "Closed Won"
    if proposed not in JOURNEY:
        return False
    if current not in JOURNEY:
        return current is None  # closed/exit stages are only reopened by a person
    return JOURNEY.index(proposed) >= JOURNEY.index(current)


def _usage_fields(usage: dict | None) -> dict:
    if not usage:
        return {}
    return {"tts_chars": int(usage.get("tts_chars") or 0), "stt_seconds": round(float(usage.get("stt_seconds") or 0), 1),
            "llm_requests": int(usage.get("llm_requests") or 0)}


def _valid_callback(value) -> str | None:
    """Normalise an LLM-extracted callback time; drop anything unparsable or more than 30 days out."""
    try:
        dt = datetime.strptime(str(value or "").strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    now = datetime.now(IST).replace(tzinfo=None)
    if dt < now - timedelta(minutes=5) or dt > now + timedelta(days=30):
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def within_calling_hours(cfg: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(IST)
    return now.weekday() in cfg["calling_days"] and cfg["calling_hours_start"] <= now.hour < cfg["calling_hours_end"]


def _utcnow():
    return datetime.utcnow()


_reachability: dict[str, float | bool] = {"checked_at": 0.0, "ok": False}


def public_url_reachable() -> bool:
    """
    Whether PUBLIC_BASE_URL answers /api/health. Cached briefly so bulk and scheduled
    dialling don't make a self-request per call.
    """
    now = time.monotonic()
    ttl = 60 if _reachability["ok"] else 10
    if now - float(_reachability["checked_at"]) < ttl:
        return bool(_reachability["ok"])
    try:
        ok = bool(settings.base_url) and httpx.get(settings.base_url + "/api/health", timeout=4).status_code == 200
    except Exception:
        ok = False
    _reachability.update(checked_at=now, ok=ok)
    return ok


def session_agent(session: dict) -> int | None:
    """Agent that owns a live call session (sessions created before workspaces carry only the call id)."""
    if session.get("agent_id"):
        return session["agent_id"]
    with get_db() as db:
        return db.scalar(select(Call.agent_id).where(Call.id == session.get("call_id"))) if session.get("call_id") else None


class CallService:
    """Calls of one agent. agent_id=None is unscoped: only for Plivo webhooks that carry a call id."""

    def __init__(self, agent_id: int | None = None):
        self.agent_id = agent_id
        self.crm = CRMService(agent_id)

    def _scoped(self, query):
        return query.where(Call.agent_id == self.agent_id) if self.agent_id is not None else query

    # ---------------- placing calls ----------------

    def start(self, lead_id: int, trigger: str = "manual", actor: str = "admin", purpose: str | None = None) -> dict:
        from app.services.plivo_service import PlivoService

        if self.agent_id is None:
            raise CallError("Calls must be placed by an agent.")
        lead = self.crm.get(lead_id)
        if not lead:
            raise CallError("Lead not found.")
        if lead["do_not_call"]:
            raise CallError("This lead is marked Do Not Call.")
        if lead.get("phone_valid") is False:
            raise CallError(f"{lead['phone']} is not a complete phone number. Edit the lead and fix it before calling.")
        workspace = agents.require(self.agent_id)
        if workspace["status"] != "active":
            raise CallError("This agent is paused. Resume it to place calls.")
        cfg = agents.get_automation(self.agent_id)
        if self.active_count() >= cfg["max_concurrent_calls"]:
            raise CallError(f"Concurrent call limit reached ({cfg['max_concurrent_calls']}). Raise it on the Automation page.")
        if self.has_active(lead_id):
            raise CallError("A call to this lead is already in progress.")

        # Plivo fetches the answer XML from PUBLIC_BASE_URL; if it can't, the callee hears a hang-up.
        if not public_url_reachable():
            raise CallError(f"Plivo can't reach {settings.base_url or 'PUBLIC_BASE_URL'}. Start the tunnel "
                            "(ngrok) or fix PUBLIC_BASE_URL, then try again.")

        plivo = PlivoService()
        persona = agents.get_profile(self.agent_id)
        caller = agents.caller_id(self.agent_id)
        language = lead["language"] or persona["default_language"]
        goal = agent.call_goal(lead, purpose)
        if goal:
            lead = {**lead, "call_purpose": purpose, "call_goal": goal}
        session = call_session.create(agent_id=self.agent_id, lead_id=lead_id, lead=lead, language=language)

        with get_db() as db:
            call = Call(agent_id=self.agent_id, lead_id=lead_id, session_id=session["id"], trigger=trigger,
                        direction="outbound", from_number="+" + caller, to_number=lead["phone"], status="Queued")
            db.add(call)
            db.flush()
            call_id = call.id
        call_session.update(session["id"], call_id=call_id)
        # Cache the shared prompts while the phone rings (the per-lead greeting is made on answer: no TTS for unanswered calls).
        _turn_pool.submit(self._prewarm_audio, lead, language, persona)

        try:
            request_uuid = plivo.dial(lead["phone"], session["id"], call_id, persona["max_call_minutes"],
                                      persona["detect_voicemail"], from_number=caller)
        except Exception as e:
            self._set(call_id, status="Failed", error=str(e), ended_at=_utcnow())
            self.crm.update(lead_id, {"call_status": "Failed", "retry_count": lead["retry_count"] + 1}, actor="system", touch=True)
            events.record("call.failed", "Call could not be placed", str(e), agent_id=self.agent_id, lead_id=lead_id,
                          call_id=call_id, actor=actor)
            raise CallError(f"Plivo rejected the call: {e}") from e

        self._set(call_id, request_uuid=request_uuid)
        self.crm.update(lead_id, {"call_status": "Queued"}, actor="system", touch=True)
        events.record("call.started", f"Calling {lead['name'] or lead['phone']}", f"Trigger: {trigger}",
                      agent_id=self.agent_id, lead_id=lead_id, call_id=call_id, actor=actor)
        return {"call_id": call_id, "lead_id": lead_id, "status": "Queued", "request_uuid": request_uuid}

    def hangup(self, call_id: int):
        from app.services.plivo_service import PlivoService
        call = self.get(call_id)
        if not call or call["status"] not in ACTIVE:
            raise CallError("Call is not active.")
        with get_db() as db:
            row = db.get(Call, call_id)
            uuid = row.call_uuid or row.request_uuid
        if not uuid:
            raise CallError("Call has no Plivo id yet.")
        try:
            PlivoService().hangup(uuid)
        except Exception as e:
            if "not found" not in str(e).lower():
                raise
            # Plivo already dropped the call but its hangup webhook never reached us.
            self.on_hangup(call_id, "failed", 0, "Call ended before webhook was received", call_uuid=None)

    # ---------------- webhook lifecycle ----------------

    def on_ring(self, call_id: int):
        call = self._set(call_id, status="Ringing")
        if call and call.lead_id:
            self.crm.update(call.lead_id, {"call_status": "Ringing"}, actor="system")

    def on_answer(self, call_id: int, call_uuid: str | None) -> dict | None:
        call = self._set(call_id, status="In Progress", call_uuid=call_uuid, answered_at=_utcnow())
        if call and call.lead_id:
            self.crm.update(call.lead_id, {"call_status": "In Progress"}, actor="system")
            events.record("call.answered", "Call answered", agent_id=call.agent_id, lead_id=call.lead_id, call_id=call_id)
        return call_session.get(call.session_id) if call else None

    def create_inbound(self, from_number: str, to_number: str, call_uuid: str) -> dict | None:
        # The dialled number decides the agent; the caller is matched only against that agent's leads.
        agent_id = agents.for_inbound(to_number)
        if agent_id is None:
            return None
        crm = CRMService(agent_id)
        lead = crm.find_by_phone(from_number)
        if lead is None:
            # Unknown caller: save them now so the conversation, summary and follow-ups attach to a real lead
            with contextlib.suppress(ValueError):
                lead = crm.create({"phone": from_number, "source": "inbound call", "status": "New"}, actor="system")
        persona = agents.get_profile(agent_id)
        language = (lead or {}).get("language") or persona["default_language"]
        known = bool(lead and lead.get("name"))
        context = {**(lead or {"phone": from_number}), "call_purpose": "inbound"}
        context["call_goal"] = agent.call_goal(context, "inbound" if known else "inbound_new")
        session = call_session.create(agent_id=agent_id, lead_id=lead and lead["id"], lead=context, language=language)
        with get_db() as db:
            call = Call(agent_id=agent_id, lead_id=lead and lead["id"], session_id=session["id"], direction="inbound",
                        trigger="inbound", from_number="+" + from_number.lstrip("+"), to_number="+" + to_number.lstrip("+"),
                        call_uuid=call_uuid, status="In Progress", answered_at=_utcnow())
            db.add(call)
            db.flush()
            call_id = call.id
        events.record("call.inbound", f"Inbound call from {lead['name'] if lead else from_number}",
                      agent_id=agent_id, lead_id=lead and lead["id"], call_id=call_id)
        return call_session.update(session["id"], call_id=call_id)

    def mark_transferred(self, call_id: int, detail: str, trigger: str | None = "transfer"):
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is None:
                return
            if trigger == "forward":
                call.trigger = "forwarded"
            agent_id, lead_id = call.agent_id, call.lead_id
        events.record("call.transferred", detail, agent_id=agent_id, lead_id=lead_id, call_id=call_id)

    def on_recording(self, call_id: int, url: str):
        self._set(call_id, recording_url=url)

    def on_hangup(self, call_id: int, plivo_status: str, duration: int, cause: str | None, call_uuid: str | None):
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is None:
                return
            session_id, lead_id, agent_id, answered = call.session_id, call.lead_id, call.agent_id, call.answered_at is not None
        session = call_session.get(session_id) or {}
        history = session.get("history", [])
        latencies = session.get("latencies") or []

        status = PLIVO_STATUS.get((plivo_status or "").lower(), "Completed")
        if cause and "machine" in cause.lower():
            status = "No Answer"
        if status == "Completed" and not answered:
            status = "No Answer"

        self._set(call_id, status=status, duration=duration, hangup_cause=cause, call_uuid=call_uuid,
                  ended_at=_utcnow(), transcript=json.dumps(history, ensure_ascii=False),
                  avg_latency_ms=round(sum(latencies) / len(latencies)) if latencies else None,
                  **_usage_fields((session or {}).get("usage")))
        if session:
            call_session.update(session_id, ended=True)

        if lead_id:
            lead = self.crm.get(lead_id) or {}
            updates = {"call_status": status}
            if status in RETRIABLE:
                updates["retry_count"] = (lead.get("retry_count") or 0) + 1
            elif status == "Completed":
                updates["retry_count"] = 0
                if lead.get("status") == "New":
                    updates["status"] = "Contacted"
            self.crm.update(lead_id, updates, actor="system", touch=True)

        events.record("call.ended", f"Call {status.lower()}", f"{duration}s · {cause or ''}".strip(" ·"),
                      agent_id=agent_id, lead_id=lead_id, call_id=call_id, data={"status": status, "duration": duration})

        customer_turns = sum(1 for t in history if t["role"] == "customer")
        if status == "Completed" and customer_turns:
            _turn_pool.submit(self._summarize, call_id, lead_id, history)

    def _summarize(self, call_id: int, lead_id: int | None, history: list[dict]):
        try:
            s = agent.summarize(history)
        except Exception as e:
            log.warning("Summary failed for call %s: %s", call_id, e)
            return
        qualification = s.get("qualification") if s.get("qualification") in ("Hot", "Warm", "Cold") else None
        self._set(call_id, summary=s.get("summary"), qualification=qualification,
                  sentiment=s.get("sentiment"), outcome=s.get("outcome"))
        if lead_id:
            updates = {k: s.get(k) for k in ("summary", "requirements", "objections", "meeting_at", "follow_up_date", "email")
                       if s.get(k)}
            # Details the caller gave about themselves fill in empty fields (a known lead's data is never overwritten)
            current = self.crm.get(lead_id) or {}
            for key in ("name", "company", "city"):
                value = str(s.get(key) or "").strip()
                if value and not current.get(key) and len(value) <= 120:
                    updates[key] = value
            callback_at = _valid_callback(s.get("callback_at"))
            if callback_at:
                updates["callback_at"] = callback_at
                updates.setdefault("follow_up_date", callback_at[:10])
            if qualification:
                updates["qualification"] = qualification
            if s.get("status"):
                current = (self.crm.get(lead_id) or {}).get("status")
                if _ai_may_move(current, s["status"]):
                    updates["status"] = s["status"]
            if s.get("outcome") == "do_not_call":
                updates["do_not_call"] = True
            self.crm.update(lead_id, updates, actor="ai", event_type="ai.summary",
                            title=f"AI call summary · {qualification or 'unqualified'} · {s.get('outcome', '')}".strip(" ·"))
            if s.get("meeting_at"):
                events.record("meeting.booked", f"Meeting booked for {s['meeting_at']}", lead_id=lead_id, call_id=call_id, actor="ai")
            if callback_at:
                events.record("callback.scheduled", f"Callback scheduled for {callback_at}", lead_id=lead_id, call_id=call_id, actor="ai")

    def _prewarm_audio(self, lead: dict, language: str, persona: dict):
        from app.api.plivo import PROMPTS  # local import: api layer imports this module
        key = "hi" if language.startswith("hi") else "en"
        # Only shared lines: they are cached once for every call. The per-lead greeting is synthesized on answer,
        # so unanswered calls (often half of all dials) cost no TTS at all.
        texts = [PROMPTS["repeat"][key], PROMPTS["goodbye"][key]]
        for text in texts:
            try:
                if settings.voice_mode == "stream":
                    tts.cached_pcm(text, tts.detect_language(text, language), persona["voice_speaker"])
                else:
                    tts.cached_audio_id(text, tts.detect_language(text, language), persona["voice_speaker"])
            except Exception as e:
                log.warning("Audio prewarm failed for %r: %s", text[:40], e)

    # ---------------- conversation turns ----------------

    def begin_turn(self, session_id: str, text: str):
        session = call_session.get(session_id)
        if not session:
            return
        session["pending"] = {"state": "processing", "text": text}
        session["silent_prompts"] = 0
        call_session.save(session)
        _turn_pool.submit(self._run_turn, session_id, text)

    def _run_turn(self, session_id: str, text: str):
        session = call_session.get(session_id)
        if not session:
            return
        agent_id = session.get("agent_id") or session_agent(session)
        try:
            persona = agents.get_profile(agent_id)
            lead = (self.crm.get(session["lead_id"]) if session.get("lead_id") else None) or session.get("lead") or {}
            # Live calls skip the embedding round-trip (~1s); keyword search answers instantly
            result = agent.respond(agent_id, session["history"], text, lead, use_embeddings=False)
            language = result["language"] or tts.detect_language(result["reply"], session["language"])
            audio_id = tts.store_audio(tts.synthesize(result["reply"], language, persona["voice_speaker"]))

            session = call_session.get(session_id) or session
            call_session.add_turn(session, "customer", text)
            call_session.add_turn(session, "assistant", result["reply"])
            session["language"] = language
            session["latencies"] = (session.get("latencies") or []) + [result["total_ms"]]
            session["pending"] = {"state": "ready", "audio_id": audio_id, "end_call": result["end_call"]}
            call_session.save(session)

            self._apply_turn_signals(session, result)
        except Exception as e:
            log.exception("Turn failed for session %s", session_id)
            session = call_session.get(session_id) or session
            call_session.add_turn(session, "customer", text)
            session["pending"] = {"state": "error", "error": str(e)[:300]}
            call_session.save(session)

    def _apply_turn_signals(self, session: dict, result: dict):
        lead_id = session.get("lead_id")
        if not lead_id:
            return
        crm = result["crm_update"]
        updates = {k: crm[k] for k in ("requirements", "objections", "meeting_at", "follow_up_date", "email") if crm.get(k)}
        if result["qualification"]:
            updates["qualification"] = result["qualification"]
        if result["intent"] == "do_not_call":
            updates["do_not_call"] = True
            updates["status"] = "Do Not Call"
        elif result["intent"] == "not_interested":
            updates["status"] = "Not Interested"
        elif crm.get("meeting_at"):
            updates["status"] = "Meeting Booked"
        elif result["intent"] in ("interested", "pricing", "meeting"):
            updates["status"] = "Interested"
        if updates:
            before = self.crm.get(lead_id) or {}
            changed = {k: v for k, v in updates.items() if before.get(k) != v}
            if changed:
                self.crm.update(lead_id, changed, actor="ai", event_type="ai.crm_update",
                                title="AI updated " + ", ".join(k.replace("_", " ") for k in changed))
                if "meeting_at" in changed:
                    events.record("meeting.booked", f"Meeting booked for {changed['meeting_at']}", lead_id=lead_id,
                                  call_id=session.get("call_id"), actor="ai")

    # ---------------- queries ----------------

    def _set(self, call_id: int, **fields):
        with get_db() as db:
            call = db.get(Call, int(call_id))
            if call is None or (self.agent_id is not None and call.agent_id != self.agent_id):
                return None
            for key, value in fields.items():
                if value is not None:
                    setattr(call, key, value)
            return call

    def get(self, call_id: int) -> dict | None:
        with get_db() as db:
            row = db.execute(self._scoped(select(Call, Lead.name).outerjoin(Lead, Lead.id == Call.lead_id))
                             .where(Call.id == call_id)).first()
            if not row:
                return None
            data = row[0].to_dict(row[1])
            session_id = row[0].session_id
        if data["status"] in ACTIVE:
            session = call_session.get(session_id)
            if session:
                data["transcript"] = session["history"]  # live transcript
        return data

    def list_calls(self, lead_id=None, status=None, direction=None, search=None, page=1, page_size=25) -> dict:
        with get_db() as db:
            query = self._scoped(select(Call, Lead.name).outerjoin(Lead, Lead.id == Call.lead_id))
            if lead_id:
                query = query.where(Call.lead_id == lead_id)
            if status == "active":
                query = query.where(Call.status.in_(ACTIVE))
            elif status:
                query = query.where(Call.status == status)
            if direction:
                query = query.where(Call.direction == direction)
            if search:
                like = f"%{search}%"
                query = query.where((Lead.name.ilike(like)) | (Call.to_number.ilike(like)) | (Call.from_number.ilike(like)))
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.execute(query.order_by(Call.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            return {"items": [c.to_dict(n, with_transcript=False) for c, n in rows], "total": total,
                    "page": page, "page_size": page_size}

    def active_count(self) -> int:
        self.expire_stale()
        with get_db() as db:
            return db.scalar(self._scoped(select(func.count(Call.id))).where(Call.status.in_(ACTIVE))) or 0

    def has_active(self, lead_id: int) -> bool:
        with get_db() as db:
            return bool(db.scalar(select(func.count(Call.id)).where(Call.lead_id == lead_id, Call.status.in_(ACTIVE))))

    def expire_stale(self):
        cutoff = _utcnow() - timedelta(minutes=20)
        with get_db() as db:
            for call in db.scalars(select(Call).where(Call.status.in_(ACTIVE), Call.created_at < cutoff)):
                call.status, call.error, call.ended_at = "Failed", "No hangup callback received", _utcnow()

    def stats(self, days: int = 14) -> dict:
        today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
        today_utc = (today - timedelta(hours=5, minutes=30)).replace(tzinfo=None)
        start_utc = today_utc - timedelta(days=days - 1)
        with get_db() as db:
            calls = db.execute(self._scoped(select(Call.created_at, Call.status, Call.duration, Call.outcome))
                               .where(Call.created_at >= start_utc)).all()
            latency = db.scalar(self._scoped(select(func.avg(Call.avg_latency_ms)))
                                .where(Call.created_at >= today_utc - timedelta(days=7)))
            active = db.scalar(self._scoped(select(func.count(Call.id))).where(Call.status.in_(ACTIVE))) or 0
        series = {}
        for d in range(days):
            day = (today - timedelta(days=days - 1 - d)).strftime("%Y-%m-%d")
            series[day] = {"date": day, "total": 0, "connected": 0, "unanswered": 0, "meetings": 0}
        outcomes = {}
        today_total = today_connected = today_talk = 0
        for created, status, duration, outcome in calls:
            day = (created + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            if day in series:
                series[day]["total"] += 1
                series[day]["connected"] += status == "Completed"
                series[day]["unanswered"] += status in RETRIABLE
                series[day]["meetings"] += outcome == "meeting_booked"
            if outcome:
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
            if created >= today_utc:
                today_total += 1
                today_connected += status == "Completed"
                today_talk += duration or 0
        return {
            "active": active,
            "today": {"total": today_total, "connected": today_connected, "talk_seconds": today_talk},
            "series": list(series.values()),
            "outcomes": outcomes,
            "avg_latency_ms": round(latency) if latency else None,
        }
