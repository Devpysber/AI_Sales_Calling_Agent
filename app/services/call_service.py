"""
Call lifecycle: place calls, run conversation turns, record outcomes.
A CallService bound to an agent only sees and places that agent's calls.
"""

import contextlib
import json
import re
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


def _valid_date(value) -> str | None:
    """Normalise an LLM-extracted follow-up date (YYYY-MM-DD); drop anything unparsable, past or more than a year out."""
    try:
        d = datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = datetime.now(IST).date()
    if d < today or d > today + timedelta(days=365):
        return None
    return d.strftime("%Y-%m-%d")


def _next_callback_slot(cfg: dict, now: datetime | None = None) -> str:
    """Two hours from now, pushed to the next opening of calling hours when that falls outside them."""
    now = now or datetime.now(IST)
    dt = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
    days = cfg.get("calling_days") or [0, 1, 2, 3, 4, 5]
    start = int(cfg.get("calling_hours_start", 9))
    end = int(cfg.get("calling_hours_end", 21))
    for _ in range(8):
        if dt.hour < start:
            dt = dt.replace(hour=start, minute=0)
        elif dt.hour >= end:
            dt = (dt + timedelta(days=1)).replace(hour=start, minute=0)
        if dt.weekday() in days and start <= dt.hour < end:
            break
        dt = (dt + timedelta(days=1)).replace(hour=start, minute=0)
    return dt.strftime("%Y-%m-%d %H:%M")


def _merge_text(existing: str | None, new: str | None, cap: int = 2000) -> str | None:
    """Append sentences the stored text does not already contain; a short call must not wipe earlier discovery notes."""
    existing = (existing or "").strip()
    new = (new or "").strip()
    if not new:
        return None
    if not existing:
        return new[:cap]
    low = existing.lower()
    added = [part.strip() for part in re.split(r"(?<=[.!?।])\s+|\n+", new)
             if part.strip() and part.strip().lower() not in low]
    if not added:
        return None
    return (existing + " " + " ".join(added))[:cap]


# Customer lines that mean "never call me again": marked from the transcript so a failed or mislabelled summary
# cannot leave them dialable.
DNC_RE = re.compile(r"(don'?t|do not|never|stop) call(ing)?( me)?|remove my number|call mat (karo|karna|kijiye)|dobara call mat"
                    r"|फिर से (कॉल|call) मत|मुझे (कॉल|call) मत", re.I)

_credit_alert_at: dict[str, float] = {}


def _is_credit_error(err: Exception) -> bool:
    text = str(err).lower()
    return "402" in text or "credit" in text or "insufficient" in text or "quota" in text or "payment required" in text


def merge_call_context(session_lead: dict, fresh: dict | None) -> dict:
    """
    Refresh the lead from the CRM without losing this call's context. The CRM row carries no `call_goal`, so using
    it raw drops the instruction that tells the agent which details to ask a new caller for.
    """
    if not fresh:
        return session_lead
    carried = {k: v for k, v in (session_lead or {}).items()
               if k in ("collect", "call_purpose", "call_goal") and v is not None}
    lead = {**fresh, **carried}
    if lead.get("collect") is not None and lead.get("call_purpose") == "inbound":
        # Recomputed against the fresh row, so details already saved during this call drop off the ask list.
        lead["call_goal"] = agent.call_goal(lead, "inbound_new")
    return lead


def _valid_meeting(value) -> str | None:
    """Normalise an LLM-extracted meeting time; drop anything unparsable, in the past, or more than a year out."""
    try:
        dt = datetime.strptime(str(value or "").strip()[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    now = datetime.now(IST).replace(tzinfo=None)
    if dt < now or dt > now + timedelta(days=365):
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def within_calling_hours(cfg: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(IST)
    # Defaults, not [], because a partial or legacy automation dict must not make every inbound
    # call raise inside the answer webhook (Plivo drops the call when the XML never arrives).
    days = cfg.get("calling_days") or [0, 1, 2, 3, 4, 5]
    start = cfg.get("calling_hours_start", 9)
    end = cfg.get("calling_hours_end", 21)
    return now.weekday() in days and start <= now.hour < end


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
        # We rang and they could not take it: greet like a person picking the thread back up, rather
        # than opening with the same pitch as if the earlier attempt never happened.
        if purpose is None and lead.get("call_status") in RETRIABLE and (lead.get("retry_count") or 0) > 0:
            purpose = "missed_previous"
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
        # Checked before anything is saved: a colleague trying the agent out must not become a lead,
        # or every test call lands in the CRM and counts as a customer in the pipeline.
        from app.services import team_service
        is_admin = team_service.is_admin_number(from_number)
        internal = is_admin or team_service.is_team_number(from_number, agent_id)
        team_name = team_service.name_for(from_number, agent_id)

        lead = None if internal else crm.find_by_phone(from_number)
        if lead is None and not internal:
            # Unknown to this agent: reuse what another agent already knows about the number, then save the caller
            known_elsewhere = CRMService(None).find_by_phone(from_number) or {}
            seed = {k: known_elsewhere[k] for k in ("name", "company", "city", "email", "language") if known_elsewhere.get(k)}
            with contextlib.suppress(ValueError):
                lead = crm.create({**seed, "phone": from_number, "source": "inbound call", "status": "New"}, actor="system")
        persona = agents.get_profile(agent_id)
        language = (lead or {}).get("language") or persona["default_language"]
        # A colleague ringing their own agent gets a walkthrough, not a sales call.
        if internal:
            purpose = "admin" if is_admin else "team"
            context = {"phone": from_number, "call_purpose": purpose, "team_name": team_name or "",
                       "name": team_name or ""}
            context["call_goal"] = agent.call_goal(context, purpose)
        else:
            collect = persona.get("inbound_collect") or ["name", "requirement"]
            missing = [f for f in collect if not (lead or {}).get(agent.COLLECT_FIELDS.get(f, f))]
            context = {**(lead or {"phone": from_number}), "call_purpose": "inbound", "collect": collect}
            context["call_goal"] = agent.call_goal(context, "inbound_new" if missing else "inbound")
        session = call_session.create(agent_id=agent_id, lead_id=lead and lead["id"], lead=context, language=language)
        with get_db() as db:
            call = Call(agent_id=agent_id, lead_id=lead and lead["id"], session_id=session["id"], direction="inbound",
                        trigger="internal" if internal else "inbound",
                        from_number="+" + from_number.lstrip("+"), to_number="+" + to_number.lstrip("+"),
                        call_uuid=call_uuid, status="In Progress", answered_at=_utcnow())
            db.add(call)
            db.flush()
            call_id = call.id
        who = team_name or (lead or {}).get("name") or "+" + from_number.lstrip("+")
        events.record("call.inbound", f"{'Team check-in from' if internal else 'Inbound call from'} {who}",
                      agent_id=agent_id, lead_id=lead and lead["id"], call_id=call_id)
        return call_session.update(session["id"], call_id=call_id)

    def transfer_label(self, number: str | None) -> str:
        """'Ashish Sharma (+919584516352)' when we know the colleague, otherwise just the number."""
        if not number:
            return "your team"
        from app.services import team_service
        first = number.split(",")[0].strip()
        name = team_service.name_for(first, self.agent_id)
        return f"{name} ({first})" if name else first

    def mark_transferred(self, call_id: int, detail: str, trigger: str | None = "transfer", number: str | None = None):
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is None:
                return
            if trigger == "forward":
                call.trigger = "forwarded"
            if number:
                # Stored on the call so every list can name the line that took over, not our own inbound number.
                call.transferred_to = number.split(",")[0].strip()[:32]
            agent_id, lead_id = call.agent_id, call.lead_id
        events.record("call.transferred", detail, agent_id=agent_id, lead_id=lead_id, call_id=call_id)

    def on_recording(self, call_id: int, url: str):
        self._set(call_id, recording_url=url)

    def on_hangup(self, call_id: int, plivo_status: str, duration: int, cause: str | None, call_uuid: str | None):
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is None:
                return
            # Plivo retries hangup callbacks and hangup() may have already closed the call: one run per call.
            if call.ended_at is not None or call.status not in ACTIVE:
                return
            session_id, lead_id, agent_id, answered = call.session_id, call.lead_id, call.agent_id, call.answered_at is not None
            answered_at = call.answered_at
            trigger = call.trigger
        # Webhooks arrive on an unscoped service; everything below (automation, emails, CRM) needs the call's agent.
        if self.agent_id is None and agent_id is not None:
            self.agent_id = agent_id
            self.crm = CRMService(agent_id)
        session = call_session.get(session_id) or {}
        history = session.get("history", [])
        latencies = session.get("latencies") or []

        status = PLIVO_STATUS.get((plivo_status or "").lower(), "Completed")
        if cause and "machine" in cause.lower():
            status = "No Answer"
        if status == "Completed" and not answered:
            status = "No Answer"
        if duration == 0 and answered and answered_at:
            duration = int((_utcnow() - answered_at).total_seconds())

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
                if trigger in ("callback", "nurture", "auto_dial", "queue", "retry"):
                    cfg = agents.get_automation(agent_id)
                    if cfg.get("retry_enabled") and updates["retry_count"] <= cfg.get("max_retries", 3):
                        delay = cfg.get("retry_interval_minutes", 60)
                        next_at = (_utcnow() + timedelta(minutes=delay) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M")
                        updates["callback_at"] = next_at
                        # A time the customer was promised and we could not keep: tell them in writing.
                        if trigger == "callback":
                            self._missed_call_email(lead_id, lead, next_at)
            elif status == "Completed":
                updates["retry_count"] = 0
                if lead.get("status") == "New":
                    updates["status"] = "Contacted"
            self.crm.update(lead_id, updates, actor="system", touch=True)

        events.record("call.ended", f"Call {status.lower()}", f"{duration}s · {cause or ''}".strip(" ·"),
                      agent_id=agent_id, lead_id=lead_id, call_id=call_id, data={"status": status, "duration": duration})

        # Live calls log the caller as "customer", the voicemail path as "user": counting only one of them
        # used to schedule the summary twice on some calls (double LLM spend, duplicate team alerts).
        customer_turns = sum(1 for t in history if t["role"] in ("customer", "user"))
        # Colleague test calls get no summary: a role-played customer must not trigger team mails or callbacks.
        if (status == "Completed" or status == "Failed") and customer_turns and trigger != "internal":
            self._submit_summary(call_id, lead_id, history)

    def _submit_summary(self, call_id: int, lead_id: int | None, history: list[dict]):
        def _done(f):
            if f.exception() is not None:
                log.error("Post-call summary for call %s crashed: %r", call_id, f.exception())
        _turn_pool.submit(self._summarize, call_id, lead_id, history).add_done_callback(_done)

    def resummarize_pending(self, hours: int = 48, max_attempts: int = 3) -> int:
        """
        Re-run the post-call summary for completed calls that never got one (provider out of credit, timeout).
        Meant for an hourly scheduler job; attempts are counted in the error column so it stops after max_attempts.
        """
        cutoff = _utcnow() - timedelta(hours=hours)
        todo = []
        with get_db() as db:
            query = self._scoped(select(Call).where(Call.status == "Completed", Call.summary.is_(None),
                                                    Call.trigger != "internal", Call.created_at >= cutoff))
            for call in db.scalars(query):
                error = call.error or ""
                if not error.startswith("summary_pending"):
                    continue
                attempts = int((re.search(r"summary_pending:(\d+)", error) or [None, "0"])[1])
                if attempts >= max_attempts or not call.transcript:
                    continue
                try:
                    history = json.loads(call.transcript)
                except ValueError:
                    continue
                if any(t.get("role") in ("customer", "user") for t in history):
                    todo.append((call.id, call.lead_id, call.agent_id, history))
        for call_id, lead_id, agent_id, history in todo:
            with contextlib.suppress(Exception):
                CallService(agent_id)._summarize(call_id, lead_id, history)
        return len(todo)

    def _team_handover(self, call_id: int, lead_id: int | None, summary: dict):
        """
        Deliver what the caller asked a human to do.

        The agent cannot phone a colleague mid-call, so "I'll tell the team right now" is only true if
        something actually sends it afterwards. This is that something: it always fires when the model
        reports a team_action, independent of whether it remembered to draft an email.
        """
        action = str(summary.get("team_action") or "").strip()
        if not action:
            return
        urgent = str(summary.get("urgent") or "").lower() in ("true", "yes", "1")
        lead = (self.crm.get(lead_id) or {}) if lead_id else {}
        
        who = lead.get("name") or lead.get("phone")
        if not who:
            from app.services import team_service
            with get_db() as db:
                call_obj = db.get(Call, call_id)
                number = call_obj.from_number if call_obj else None
            if number:
                who = team_service.name_for(number, self.agent_id) or number
        who = who or "A caller"

        from app.services.notification_service import notify_team
        events.record("call.handover", f"Action for the team: {action}", f"from {who}" + (" • urgent" if urgent else ""),
                      agent_id=self.agent_id, lead_id=lead_id, call_id=call_id, actor="ai")
        persona = agents.get_profile(self.agent_id)
        lines = [f"{who} asked for someone on the team to act.", "", f"What they need: {action}", ""]
        for label, key in (("Phone", "phone"), ("Email", "email"), ("City", "city"), ("Company", "company")):
            if lead.get(key):
                lines.append(f"{label}: {lead[key]}")
        if summary.get("summary"):
            lines += ["", f"Call summary: {summary['summary']}"]
        subject = ("URGENT: " if urgent else "") + f"{who} needs a callback - {persona['company_name']}"
        with contextlib.suppress(Exception):
            notify_team(subject, "\n".join(lines), lead_id=lead_id, agent_id=self.agent_id)

    def _missed_call_email(self, lead_id: int, lead: dict, next_at: str):
        """Tell a lead we rang at the time they asked for, missed them, and when we will try again."""
        if not agents.get_automation(self.agent_id).get("ai_auto_emails", True):
            return
        to = (lead.get("email") or "").strip()
        if not to:
            return
        from app.services.notification_service import send_email
        persona = agents.get_profile(self.agent_id)
        body = (f"Hi {lead.get('name') or 'there'},\n\n"
                f"We called at the time you asked for but could not reach you. No problem at all.\n\n"
                f"We will try again on {next_at} IST. If another time suits you better, just reply to this "
                f"email or call us back and we will fit around you.\n\nRegards,\n"
                f"{persona['agent_name']}\n{persona['company_name']}")
        with contextlib.suppress(Exception):
            send_email(to, f"We tried to reach you - {persona['company_name']}", body,
                       lead_id=lead_id, agent_id=self.agent_id, actor="ai")

    def _confirm_meeting_email(self, lead_id: int, meeting_at: str, summary: dict):
        """
        A booked meeting always gets a written confirmation, whether or not the model asked for one.

        Callers ask for "follow-up email ke through" mid-call and the model often forgets to put it in
        send_email; the meeting itself is the promise, so the email is sent from the booking.
        """
        if not agents.get_automation(self.agent_id).get("ai_auto_emails", True):
            return
        lead = self.crm.get(lead_id) or {}
        to = (lead.get("email") or "").strip()
        if not to:
            return
        # The model already listed a mail to the lead: leave it to that one instead of sending twice.
        planned = summary.get("send_email") or []
        planned = [planned] if isinstance(planned, dict) else (planned if isinstance(planned, list) else [])
        if any(isinstance(e, dict) and "lead" in str(e.get("to", "")).lower() for e in planned):
            return
        from app.services.notification_service import send_email
        persona = agents.get_profile(self.agent_id)
        when = meeting_at.replace(" ", " at ")
        body = (f"Hi {lead.get('name') or 'there'},\n\n"
                f"Thanks for speaking with us. Your meeting with {persona['company_name']} is confirmed for {when} IST.\n\n"
                + (f"What we noted: {summary['requirements']}\n\n" if summary.get("requirements") else "")
                + f"If anything changes, just reply to this email or call us back.\n\nRegards,\n"
                  f"{persona['agent_name']}\n{persona['company_name']}")
        with contextlib.suppress(Exception):
            status = send_email(to, f"Your meeting with {persona['company_name']} on {meeting_at[:10]}",
                                body, lead_id=lead_id, agent_id=self.agent_id, actor="ai")
            log.info("Meeting confirmation to %s: %s", to, status)

    def _summarize(self, call_id: int, lead_id: int | None, history: list[dict]):
        try:
            self._summarize_inner(call_id, lead_id, history)
        except Exception:
            log.exception("Post-call summary for call %s failed", call_id)
            raise

    def _mark_summary_failed(self, call_id: int, lead_id: int | None, err: Exception):
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is not None:
                attempts = int((re.search(r"summary_pending:(\d+)", call.error or "") or [None, "0"])[1]) + 1
                call.error = f"summary_pending:{attempts} {str(err)[:200]}"
                if not call.outcome:
                    call.outcome = "summary_pending"
        events.record("call.summary_failed", "Summary not generated", str(err)[:200], agent_id=self.agent_id,
                      lead_id=lead_id, call_id=call_id, actor="system")
        if _is_credit_error(err):
            # One alert an hour, not one per call, while every provider is out of credit.
            if time.monotonic() - _credit_alert_at.get("llm", -1e9) > 3600:
                _credit_alert_at["llm"] = time.monotonic()
                from app.services.notification_service import notify_team
                with contextlib.suppress(Exception):
                    notify_team("AI provider out of credits",
                                f"Post-call summaries are failing: {str(err)[:300]}\n\nTop up the LLM provider; "
                                "calls from the last 48 hours are summarised automatically once it works again.",
                                agent_id=self.agent_id)

    def _live_dnc(self, lead_id: int | None, history: list[dict]) -> bool:
        """The caller said not to call again: mark it from the transcript, whatever the summary model decides."""
        if not lead_id:
            return False
        said = any(DNC_RE.search(t.get("text") or "") for t in history if t.get("role") in ("customer", "user"))
        if said:
            current = self.crm.get(lead_id) or {}
            if not current.get("do_not_call"):
                self.crm.update(lead_id, {"do_not_call": True, "status": "Do Not Call"}, actor="ai",
                                event_type="ai.crm_update", title="AI marked Do Not Call (caller asked not to be called again)")
        return said

    def _summarize_inner(self, call_id: int, lead_id: int | None, history: list[dict]):
        # Idempotent: a duplicate hangup or a scheduler retry that raced a success must not send everything twice.
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is not None and call.summary:
                return
        asked_dnc = self._live_dnc(lead_id, history)
        try:
            s = agent.summarize(history)
        except Exception as e:
            log.warning("Summary failed for call %s: %s", call_id, e)
            self._mark_summary_failed(call_id, lead_id, e)
            return
        if asked_dnc:
            s["outcome"] = "do_not_call"
        qualification = s.get("qualification") if s.get("qualification") in ("Hot", "Warm", "Cold") else None
        self._set(call_id, summary=s.get("summary") or "(no summary text)", qualification=qualification,
                  sentiment=s.get("sentiment"), outcome=s.get("outcome"))
        with get_db() as db:
            call = db.get(Call, call_id)
            if call is not None and (call.error or "").startswith("summary_pending"):
                call.error = None
        self._team_handover(call_id, lead_id, s)
        if lead_id:
            updates = {"summary": s["summary"]} if s.get("summary") else {}
            follow_up = _valid_date(s.get("follow_up_date"))
            if follow_up:
                updates["follow_up_date"] = follow_up
            meeting_at = _valid_meeting(s.get("meeting_at"))
            if meeting_at:
                updates["meeting_at"] = meeting_at
            # Details the caller gave about themselves fill in empty fields (a known lead's data is never overwritten;
            # a garbled spoken email must not replace one a person typed in).
            current = self.crm.get(lead_id) or {}
            for key in ("name", "company", "city", "email"):
                value = str(s.get(key) or "").strip()
                if value and not current.get(key) and len(value) <= 120:
                    updates[key] = value
            if s.get("email") and current.get("email") and str(s.get("email_corrected") or "").lower() in ("true", "yes", "1"):
                updates["email"] = str(s["email"]).strip()[:120]
            # Requirements and objections accumulate across calls; a 40-second confirmation must not wipe discovery notes.
            for key in ("requirements", "objections"):
                merged_text = _merge_text(current.get(key), s.get(key))
                if merged_text is not None:
                    updates[key] = merged_text
            # Budget and timeline are restated on most calls: keep one current line each instead of
            # appending a near-duplicate after every conversation.
            notes_lines = [ln for ln in (current.get("notes") or "").splitlines() if ln.strip()]
            for key, label in (("budget", "Budget"), ("timeline", "Timeline")):
                value = str(s.get(key) or "").strip()
                if not value:
                    continue
                line = f"{label}: {value}"
                notes_lines = [ln for ln in notes_lines if not ln.strip().startswith(f"{label}:")
                               and f"{label}:" not in ln]
                notes_lines.append(line)
            merged = "\n".join(notes_lines)
            if merged != (current.get("notes") or ""):
                updates["notes"] = merged
            callback_at = _valid_callback(s.get("callback_at"))
            if not callback_at and (s.get("outcome") == "callback_requested" or str(s.get("callback_at") or "").strip()):
                # A callback was promised but the model's time is unparsable or out of range: book the next slot
                # inside calling hours rather than silently dropping the promise, and flag it for a person to fix.
                try:
                    cfg = agents.get_automation(self.agent_id)
                except Exception:
                    cfg = {}
                callback_at = _next_callback_slot(cfg)
                events.record("callback.unclear", f"Callback time unclear — scheduled for {callback_at}",
                              f"Model gave: {str(s.get('callback_at') or '')[:60] or 'no time'}",
                              agent_id=self.agent_id, lead_id=lead_id, call_id=call_id, actor="ai")
            if not callback_at and str(s.get("team_action") or "").strip():
                # They asked the team to act and to be told the outcome, but named no time. Without a
                # slot nothing dials them back and the promise is silently dropped.
                soon = 15 if str(s.get("urgent") or "").lower() in ("true", "yes", "1") else 60
                callback_at = (datetime.now(IST) + timedelta(minutes=soon)).strftime("%Y-%m-%d %H:%M")
                if soon == 15 and str(s.get("team_action") or "").strip():
                    self._urgent_team_alert(lead_id, call_id, str(s.get("team_action")))
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
                updates["status"] = "Do Not Call"
                updates.pop("callback_at", None)
                callback_at = None
            self.crm.update(lead_id, updates, actor="ai", event_type="ai.summary",
                            title=f"AI call summary · {qualification or 'unqualified'} · {s.get('outcome', '')}".strip(" ·"))
            if meeting_at:
                events.record("meeting.booked", f"Meeting booked for {meeting_at}", agent_id=self.agent_id, lead_id=lead_id,
                              call_id=call_id, actor="ai")
                self._confirm_meeting_email(lead_id, meeting_at, s)
            if agents.get_automation(self.agent_id).get("ai_auto_emails", True):
                emails = s.get("send_email") or []
                if isinstance(emails, dict):
                    emails = [emails]
                if isinstance(emails, list):
                    for e in emails:
                        if not isinstance(e, dict) or not e.get("to") or not e.get("body"):
                            continue
                        target = str(e["to"]).lower()
                        subject = e.get("subject", f"Update regarding call with {updates.get('name') or current.get('name') or current.get('phone')}")
                        recipients = []
                        if "lead" in target:
                            if updates.get("email") or current.get("email"):
                                recipients.append(updates.get("email") or current.get("email"))
                        else:
                            from app.core.auth import login_email
                            profile = agents.get_profile(self.agent_id)
                            team_members = profile.get("team_members") or []
                            for m in team_members:
                                if m.get("email"):
                                    recipients.append(m["email"])
                            if not recipients:
                                recipients.append(login_email())  # team/admin default to the system owner's email
                        
                        from app.services.notification_service import send_email
                        for recipient in set(recipients):
                            try:
                                send_email(recipient, subject, e["body"], lead_id=lead_id, agent_id=self.agent_id, actor="ai")
                                events.record("email.sent", f"Sent email to {target} ({recipient})", lead_id=lead_id, call_id=call_id, actor="ai")
                            except Exception as err:
                                log.error("Failed to send post-call email to %s: %s", recipient, err)
                                events.record("email.failed", f"Failed to send email to {target}: {err}", lead_id=lead_id, call_id=call_id, actor="system")
            if callback_at:
                events.record("callback.scheduled", f"Callback scheduled for {callback_at}", lead_id=lead_id, call_id=call_id, actor="ai")

    def _urgent_team_alert(self, lead_id: int, original_call_id: int, team_action: str):
        from app.services.plivo_service import PlivoService
        from app.services import agents, call_session
        
        profile = agents.get_profile(self.agent_id)
        raw_numbers = profile.get("transfer_number", "")
        transfer_numbers = [agents.phone_digits(n) for n in raw_numbers.replace(" ", "").split(",")]
        transfer_numbers = [n for n in transfer_numbers if n]
        if not transfer_numbers:
            return
            
        team_number = f"+{transfer_numbers[0]}"
        lead = self.crm.get(lead_id) or {}
        
        # Create a session for the outbound alert call
        alert_session = call_session.create(agent_id=self.agent_id, lead_id=lead_id, lead=lead, language="en-IN")
        alert_session["team_action"] = team_action
        alert_session["original_call_id"] = original_call_id
        alert_session["customer_phone"] = lead.get("phone")
        call_session.save(alert_session)
        
        try:
            # No cid: this is a separate leg to a colleague. Reusing the customer's call id would
            # let the alert leg's ring/hangup webhooks overwrite that call's status and duration.
            PlivoService().dial(team_number, alert_session["id"], None, max_minutes=3, endpoint="team-alert")
            log.info("Initiated urgent team alert to %s for call %s", team_number, original_call_id)
        except Exception as e:
            log.error("Failed to dial urgent team alert: %s", e)

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
            # The CRM row carries no call_goal/call_purpose/collect: merge, so gather-mode turns keep the call's brief.
            fresh = self.crm.get(session["lead_id"]) if session.get("lead_id") else None
            lead = merge_call_context(session.get("lead") or {}, fresh) or {}
            # Live calls skip the embedding round-trip (~1s); keyword search answers instantly
            result = agent.respond(agent_id, session["history"], text, lead, use_embeddings=False,
                                   summary=session.get("summary"))
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
            self._compact_history(session_id)
        except Exception as e:
            log.exception("Turn failed for session %s", session_id)
            session = call_session.get(session_id) or session
            call_session.add_turn(session, "customer", text)
            session["pending"] = {"state": "error", "error": str(e)[:300]}
            call_session.save(session)

    def _compact_history(self, session_id: str):
        """Fold the turns that fell out of the prompt window into one summary line, off the reply path.

        The caller is already listening to the reply this was queued behind, so a long call keeps
        paying for the last few turns plus a paragraph rather than the whole transcript.
        """
        session = call_session.get(session_id)
        if not session:
            return
        turns = len(session["history"])
        if turns < agent.COMPACT_AFTER_TURNS or turns - int(session.get("compacted_at") or 0) < agent.COMPACT_EVERY_TURNS:
            return

        def run():
            try:
                summary = agent.compact_history(session["history"], session.get("summary"))
            except Exception as e:  # noqa: BLE001 - the summary is an optimisation, not state we need
                log.warning("History compaction failed for %s: %s", session_id[:8], e)
                return
            if summary:
                call_session.update(session_id, summary=summary, compacted_at=turns)

        _turn_pool.submit(run)

    def _apply_turn_signals(self, session: dict, result: dict):
        lead_id = session.get("lead_id")
        if not lead_id:
            return
        crm = result["crm_update"]
        updates = {k: crm[k] for k in ("requirements", "objections", "follow_up_date", "email") if crm.get(k)}
        meeting_at = _valid_meeting(crm.get("meeting_at"))
        if meeting_at:
            updates["meeting_at"] = meeting_at
        if result["qualification"]:
            updates["qualification"] = result["qualification"]
        if result["intent"] == "do_not_call":
            updates["do_not_call"] = True
            updates["status"] = "Do Not Call"
        elif result["intent"] == "not_interested":
            updates["status"] = "Not Interested"
        elif meeting_at:
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

    def _with_team_name(self, data: dict) -> dict:
        """Name the colleague a call was handed to, so lists can say who took it instead of a bare number."""
        from app.services import team_service
        if data.get("transferred_to"):
            data["transferred_to_name"] = team_service.name_for(data["transferred_to"], data.get("agent_id"))
        if data.get("trigger") == "internal" and not data.get("lead_name"):
            data["lead_name"] = team_service.name_for(data.get("from_number"), data.get("agent_id"))
        return data

    def get(self, call_id: int) -> dict | None:
        with get_db() as db:
            row = db.execute(self._scoped(select(Call, Lead.name).outerjoin(Lead, Lead.id == Call.lead_id))
                             .where(Call.id == call_id)).first()
            if not row:
                return None
            data = self._with_team_name(row[0].to_dict(row[1]))
            session_id = row[0].session_id
        if data["status"] in ACTIVE:
            session = call_session.get(session_id)
            if session:
                data["transcript"] = session["history"]  # live transcript
        return data

    def delete_history(self) -> dict:
        from sqlalchemy import delete
        with get_db() as db:
            db.execute(delete(Call).where(Call.agent_id == self.agent_id))
            db.commit()
        return {"ok": True}

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
            if search and search.strip():
                from app.services.crm_service import search_terms
                like, phone_like = search_terms(search)
                condition = Lead.name.ilike(like) | Lead.company.ilike(like) | Call.to_number.ilike(like) | Call.from_number.ilike(like) \
                    | Call.summary.ilike(like) | Call.outcome.ilike(like)
                if phone_like:
                    condition = condition | Call.to_number.like(phone_like) | Call.from_number.like(phone_like)
                query = query.where(condition)
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.execute(query.order_by(Call.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
            return {"items": [self._with_team_name(c.to_dict(n, with_transcript=False)) for c, n in rows], "total": total,
                    "page": page, "page_size": page_size}

    def active_count(self) -> int:
        self.expire_stale()
        with get_db() as db:
            return db.scalar(self._scoped(select(func.count(Call.id))).where(Call.status.in_(ACTIVE))) or 0

    def has_active(self, lead_id: int) -> bool:
        with get_db() as db:
            return bool(db.scalar(select(func.count(Call.id)).where(Call.lead_id == lead_id, Call.status.in_(ACTIVE))))

    def expire_stale(self):
        """Calls still active 20 minutes after they started never got a hangup callback: close them properly."""
        cutoff = _utcnow() - timedelta(minutes=20)
        with get_db() as db:
            stale = [(c.id, c.agent_id, c.call_uuid, c.answered_at is not None)
                     for c in db.scalars(select(Call).where(Call.status.in_(ACTIVE), Call.created_at < cutoff))]
        for call_id, agent_id, uuid, answered in stale:
            with contextlib.suppress(Exception):
                CallService(agent_id).on_hangup(call_id, "completed" if answered else "no_answer", 0,
                                                "No hangup callback received", uuid)

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
