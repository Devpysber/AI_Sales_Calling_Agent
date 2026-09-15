"""
Built-in scheduler.

Jobs run per agent with that agent's own automation settings: auto-dial new
leads, retry unanswered calls, meeting reminders, daily report. Safe to run
on several instances: a leader lock in the shared store ensures only one
instance executes jobs at a time.
"""

import asyncio
import os
import socket
from datetime import datetime, timedelta

from app.core import store
from app.core.logging import get_logger
from app.services import agents, events
from app.services.call_service import IST, CallError, CallService, within_calling_hours
from app.services.crm_service import CRMService
from app.services.notification_service import send_email
from app.services.settings_service import SettingsService

log = get_logger(__name__)
TICK_SECONDS = 20
LOCK_TTL = 60
OWNER = f"{socket.gethostname()}:{os.getpid()}"


def _dial(agent_id: int, leads: list[dict], trigger: str, limit: int) -> str:
    calls = CallService(agent_id)
    placed, skipped = 0, []
    for lead in leads:
        if placed >= limit:
            break
        try:
            calls.start(lead["id"], trigger=trigger, actor="scheduler")
            placed += 1
        except CallError as e:
            skipped.append(str(e))
            if "limit" in str(e).lower():
                break
    return f"{placed} call(s) placed" + (f", {len(skipped)} skipped ({skipped[0]})" if skipped else "")


def job_auto_dial(agent_id, cfg, force=False):
    if not force and not within_calling_hours(cfg):
        return "outside calling hours"
    leads = CRMService(agent_id).pending_for_dial(cfg["max_calls_per_run"] * 3)
    return _dial(agent_id, leads, "auto_dial", cfg["max_calls_per_run"]) if leads else "no pending leads"


def job_retry_calls(agent_id, cfg, force=False):
    if not force and not within_calling_hours(cfg):
        return "outside calling hours"
    leads = CRMService(agent_id).retry_candidates(cfg["max_retries"], cfg["retry_min_gap_minutes"], cfg["max_calls_per_run"] * 3)
    return _dial(agent_id, leads, "retry", cfg["max_calls_per_run"]) if leads else "no leads to retry"


def job_callbacks(agent_id, cfg, force=False):
    """Call back leads at the time they asked for ("call me in 10 minutes"). Always on, inside calling hours."""
    if not force and not within_calling_hours(cfg):
        return "outside calling hours"
    crm, calls = CRMService(agent_id), CallService(agent_id)
    placed, skipped = 0, []
    for lead in crm.due_callbacks(datetime.now(IST).strftime("%Y-%m-%d %H:%M"), cfg["max_calls_per_run"]):
        try:
            calls.start(lead["id"], trigger="callback", actor="scheduler", purpose="follow_up")
            placed += 1
        except CallError as e:
            skipped.append(str(e))
            if "limit" in str(e).lower() or "in progress" in str(e).lower():
                continue  # try again next tick
        # One attempt per promised time: a no-answer is picked up by the normal retry job.
        crm.update(lead["id"], {"callback_at": None}, actor="system")
    if not placed and not skipped:
        return "no callbacks due"
    return f"{placed} callback(s) placed" + (f", {len(skipped)} skipped ({skipped[0]})" if skipped else "")


def job_meeting_reminder(agent_id, cfg, force=False):
    persona = agents.get_profile(agent_id)
    tomorrow = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d")
    sent = 0
    for lead in CRMService(agent_id).meetings_on(tomorrow):
        if lead["email"]:
            send_email(lead["email"], f"Reminder: your meeting with {persona['company_name']} tomorrow",
                       f"Hi {lead['name'] or 'there'},\n\nThis is a reminder of your meeting with {persona['company_name']} "
                       f"on {lead['meeting_at']} IST.\n\nRegards,\n{persona['agent_name']}\n{persona['company_name']}",
                       lead_id=lead["id"])
            sent += 1
    return f"{sent} reminder(s) sent for {tomorrow}"


def job_daily_report(agent_id, cfg, force=False):
    if not cfg["daily_report_email"]:
        return "no report email configured"
    name = (agents.get(agent_id) or {}).get("name", "Agent")
    crm, calls = CRMService(agent_id).stats(), CallService(agent_id).stats(days=1)
    q = crm["by_qualification"]
    body = (f"{name} · daily report · {datetime.now(IST):%d %b %Y}\n\n"
            f"Calls: {calls['today']['total']} · Connected: {calls['today']['connected']} · "
            f"Talk time: {calls['today']['talk_seconds'] // 60} min\n"
            f"Leads: {crm['total']} · Pending: {crm['pending']} · Meetings: {crm['meetings']}\n"
            f"Hot {q.get('Hot', 0)} · Warm {q.get('Warm', 0)} · Cold {q.get('Cold', 0)}\n")
    return send_email(cfg["daily_report_email"], f"{name}: voice agent daily report", body)


JOBS = {"auto_dial": job_auto_dial, "retry_calls": job_retry_calls, "callbacks": job_callbacks,
        "meeting_reminder": job_meeting_reminder, "daily_report": job_daily_report}
LABELS = {"auto_dial": "Auto-dial", "retry_calls": "Retry calls", "callbacks": "Callbacks", "meeting_reminder": "Meeting reminders",
          "daily_report": "Daily report"}


def _state_key(agent_id: int, job: str) -> str:
    return f"last_run.{agent_id}.{job}"


def run_job(agent_id: int, name: str, force: bool = False, actor: str = "scheduler") -> str:
    cfg = agents.get_automation(agent_id)
    try:
        result = JOBS[name](agent_id, cfg, force=force)
    except Exception as e:
        log.exception("Job %s failed for agent %s", name, agent_id)
        result = f"error: {e}"
    SettingsService().set_state(_state_key(agent_id, name), {"at": datetime.now(IST).isoformat(timespec="seconds"), "result": result})
    events.record("automation.run", f"{LABELS[name]}: {result}", agent_id=agent_id, actor=actor,
                  data={"job": name, "manual": force})
    return result


def job_status(agent_id: int) -> dict:
    state = SettingsService()
    return {name: {"label": LABELS[name], **(state.get_state(_state_key(agent_id, name)) or {})} for name in JOBS}


def _due(agent_id: int, cfg: dict) -> list[str]:
    now = datetime.now(IST)
    state = SettingsService()

    def last(job):
        value = state.get_state(_state_key(agent_id, job))
        return datetime.fromisoformat(value["at"]) if value else None

    due = []
    # Callbacks are checked every tick but only logged when something was due (see tick()).
    for job, enabled, minutes in (("auto_dial", "auto_dial_enabled", "auto_dial_interval_minutes"),
                                  ("retry_calls", "retry_enabled", "retry_interval_minutes")):
        if cfg[enabled] and (not last(job) or now - last(job) >= timedelta(minutes=cfg[minutes])):
            due.append(job)
    for job, enabled, hour in (("meeting_reminder", "meeting_reminder_enabled", "meeting_reminder_hour"),
                               ("daily_report", "daily_report_enabled", "daily_report_hour")):
        if cfg[enabled] and now.hour >= cfg[hour] and (not last(job) or last(job).date() < now.date()):
            due.append(job)
    return due


def tick():
    for agent_id in agents.ids(active_only=True):
        try:
            cfg = agents.get_automation(agent_id)
            for job in _due(agent_id, cfg):
                run_job(agent_id, job)
            if CRMService(agent_id).due_callbacks(datetime.now(IST).strftime("%Y-%m-%d %H:%M"), 1):
                run_job(agent_id, "callbacks")
        except agents.AgentNotFound:
            continue  # deleted mid-tick
    CallService().expire_stale()


async def scheduler_loop():
    log.info("Scheduler started (%s)", OWNER)
    while True:
        try:
            if await asyncio.to_thread(store.acquire_lock, "scheduler", OWNER, LOCK_TTL):
                await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Scheduler tick failed")
        await asyncio.sleep(TICK_SECONDS)
