"""
Runtime issues an admin can see and heal from the web app, without a developer.

Anything that fails at run time (a tool a colleague asked for on a call, an email, a scheduler
tick, a provider) is reported here with enough data to retry it. `detect()` adds the problems
that are visible from state alone (provider marked dead, inverted calling hours, stuck calls,
calls with no summary). `heal()` runs the remedy for a kind; what has no remedy is escalated
with its detail so it can be handed to a developer in one paste.
"""
import contextlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta

from app.core import store
from app.core.logging import get_logger
from app.services import events
from app.services.settings_service import SettingsService

log = get_logger(__name__)

ISSUES_KEY = "issues"
MAX_ISSUES = 1000
SCHEDULER_TICK_KEY = "scheduler_last_tick"

# kind -> (title, what heal does; None = no automatic remedy, escalate)
KINDS = {
    "tool_failed": ("Call tool failed", "Re-runs the tool with cleaned-up arguments"),
    "turn_error": ("Live call turn crashed", None),
    "email_failed": ("Email failed", "Sends the email again"),
    "email_unconfigured": ("Email not configured", None),
    "llm_dead": ("OpenRouter unusable (tools off)", "Clears the block and checks the provider"),
    "calling_hours": ("Calling hours invalid", "Resets to 9:00–21:00, every day"),
    "stale_calls": ("Calls stuck as live", "Closes calls that never got a hangup"),
    "summary_pending": ("Calls without AI summary", "Re-runs the summaries"),
    "scheduler_stalled": ("Automation scheduler stalled", "Runs a scheduler tick now"),
    "scheduler_error": ("Automation job crashed", "Runs the job again"),
    "public_url": ("Public URL unreachable", None),
    "inbound_disconnected": ("Inbound calls not reaching the app", "Points the Plivo number back at this app's webhooks"),
}
INBOUND_CHECK_KEY = "heal_inbound_check"   # store: last Plivo inbound-status check, so a Settings refresh is not a Plivo API call


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> list[dict]:
    return list(SettingsService().get_state(ISSUES_KEY) or [])


def _save(issues: list[dict]) -> None:
    SettingsService().set_state(ISSUES_KEY, issues[-MAX_ISSUES:])


def seed_issues_row() -> None:
    """Ensure the state.issues row exists so `_locked_issues`'s FOR UPDATE always has a row to lock;
    call once at startup so two concurrent first-ever reports never both INSERT the same PK."""
    from app.core.database import get_db
    from app.models.app_setting import AppSetting
    with get_db() as db:
        if db.get(AppSetting, f"state.{ISSUES_KEY}") is None:
            db.add(AppSetting(key=f"state.{ISSUES_KEY}", value="[]"))


@contextlib.contextmanager
def _locked_issues():
    """Read-modify-write the issues list in one transaction under a row lock, so concurrent
    reporters/healers across processes (uvicorn workers, scheduler worker) never clobber each other."""
    from sqlalchemy import select
    from app.core.database import get_db
    from app.models.app_setting import AppSetting
    with get_db() as db:
        key = f"state.{ISSUES_KEY}"
        row = db.execute(select(AppSetting).where(AppSetting.key == key).with_for_update()).scalar_one_or_none()
        if row is None:
            row = AppSetting(key=key, value="[]")
            db.add(row)
            db.flush()
        issues = list(json.loads(row.value or "[]"))
        yield issues
        row.value = json.dumps(issues[-MAX_ISSUES:])


def report(kind: str, detail: str, *, agent_id: int | None = None, call_id: int | None = None,
           data: dict | None = None, title: str | None = None, bump: bool = True) -> dict:
    """Record (or bump) an open issue; the same kind + agent + title counts up rather than piling up."""
    try:
        with _locked_issues() as issues:
            title = title or KINDS.get(kind, (kind, None))[0]
            # Dedupe also on data["to"] and data["number"] when present: two email_failed reports for
            # the same agent/title but different recipients, or two disconnected phone numbers, must stay
            # separate issues — otherwise the later one overwrites the earlier and heal fixes only the last.
            data_key = ((data or {}).get("to"), (data or {}).get("number"))
            for it in issues:
                if (it["status"] == "open" and it["kind"] == kind and it.get("agent_id") == agent_id
                        and it["title"] == title
                        and ((it.get("data") or {}).get("to"), (it.get("data") or {}).get("number")) == data_key):
                    it.update(count=int(it.get("count") or 1) + (1 if bump else 0),
                              last_at=_now() if bump else it["last_at"], detail=str(detail)[:1000],
                              data=data if data is not None else it.get("data"), call_id=call_id or it.get("call_id"))
                    return it
            issue = {"id": uuid.uuid4().hex[:12], "kind": kind, "title": title, "detail": str(detail)[:1000],
                     "agent_id": agent_id, "call_id": call_id, "data": data, "status": "open", "count": 1,
                     "first_at": _now(), "last_at": _now(), "healable": bool(KINDS.get(kind, (None, None))[1]),
                     "remedy": KINDS.get(kind, (None, None))[1], "result": None}
            issues.append(issue)
            events.record("issue.reported", title, str(detail)[:500], agent_id=agent_id, call_id=call_id, actor="system")
            return issue
    except Exception:  # noqa: BLE001 - reporting a problem must never create one
        log.exception("Failed to report issue %s", kind)
        return {}


def list_issues(include_closed: bool = False) -> list[dict]:
    issues = _load()
    if not include_closed:
        issues = [i for i in issues if i["status"] in ("open", "escalated")]
    return sorted(issues, key=lambda i: i["last_at"], reverse=True)


def dismiss(issue_id: str) -> bool:
    with _locked_issues() as issues:
        for it in issues:
            if it["id"] == issue_id:
                it.update(status="dismissed", result="Dismissed by admin")
                return True
        return False


# ---------------- detection ----------------

def detect() -> list[dict]:
    """Find what is wrong right now from state alone and report it; returns the open list."""
    from app.services import agents, llm
    from app.services.call_service import CallService, public_url_reachable
    from app.services.notification_service import email_configured
    checks = []
    with contextlib.suppress(Exception):
        if llm._openrouter_dead():
            checks.append(("llm_dead", "OpenRouter answered 401/402 recently; live calls run without tools until it clears.", {}))
    with contextlib.suppress(Exception):
        wants_email = any((agents.get_automation(a).get("ai_auto_emails", True)) for a in agents.ids(active_only=True))
        if wants_email and not email_configured():
            checks.append(("email_unconfigured", "AI emails are on but no email provider is configured (Email service page).", {}))
    with contextlib.suppress(Exception):
        for agent_id in agents.ids(active_only=True):
            cfg = agents.get_automation(agent_id)
            start, end = int(cfg.get("calling_hours_start", 9)), int(cfg.get("calling_hours_end", 21))
            days = cfg.get("calling_days")
            if start >= end or not (0 <= start <= 23 and 1 <= end <= 24) or (isinstance(days, list) and not days):
                checks.append(("calling_hours", f"Agent {agent_id}: hours {start}–{end}, days {days}; every automation is silently off.",
                               {"agent_id": agent_id}))
    with contextlib.suppress(Exception):
        from sqlalchemy import select
        from app.core.database import get_db
        from app.models.call import Call
        from app.services.call_service import ACTIVE, _utcnow
        with get_db() as db:
            stale = db.scalars(select(Call.id).where(Call.status.in_(ACTIVE), Call.created_at < _utcnow() - timedelta(minutes=20))).all()
            missing = db.scalars(select(Call.id).where(Call.status == "Completed", Call.summary.is_(None), Call.trigger != "internal",
                                                       Call.created_at >= _utcnow() - timedelta(hours=48))).all()
        if stale:
            checks.append(("stale_calls", f"{len(stale)} call(s) still marked live after 20 minutes.", {"count": len(stale)}))
        if missing:
            checks.append(("summary_pending", f"{len(missing)} completed call(s) in the last 48h have no AI summary.", {"count": len(missing)}))
    with contextlib.suppress(Exception):
        last = store.get_json(SCHEDULER_TICK_KEY)
        from app.services.scheduler import TICK_SECONDS
        if last and time.time() - float(last) > TICK_SECONDS * 6:
            checks.append(("scheduler_stalled", f"No scheduler tick for {int(time.time() - float(last))}s.", {}))
    with contextlib.suppress(Exception):
        # Every number calls can arrive on: the default line plus each agent's own. A number whose Plivo
        # application no longer points here (someone pressed Restore, or a re-provisioned app) rings nothing.
        cached = store.get_json(INBOUND_CHECK_KEY)
        if cached is None:
            from app.services.plivo_service import PlivoService
            from app.core.config import settings as _settings
            numbers = {"".join(c for c in (_settings.plivo_phone_number or "") if c.isdigit())}
            numbers |= {"".join(c for c in (agents.get(a) or {}).get("phone_number", "") or "" if c.isdigit()) for a in agents.ids(active_only=True)}
            svc = PlivoService()
            cached = []
            for number in sorted(n for n in numbers if n):
                st = svc.inbound_status(number)
                if not st.get("connected"):
                    cached.append({"number": st["number"], "app": st.get("app_name") or st.get("app_id") or "no application"})
            store.set_json(INBOUND_CHECK_KEY, cached, ttl=300)
        for row in cached:
            checks.append(("inbound_disconnected", f"{row['number']}: incoming calls go to \"{row['app']}\", not to this app. Press Heal to connect inbound.",
                           {"number": row["number"]}))
    with contextlib.suppress(Exception):
        if not public_url_reachable():
            checks.append(("public_url", "The app's public URL does not answer; Plivo cannot reach calls or audio.", {}))
    for kind, detail, data in checks:
        report(kind, detail, agent_id=data.get("agent_id"), data=data, bump=False)
    return list_issues()


# ---------------- healing ----------------

def _heal_one(issue: dict) -> tuple[bool, str]:
    kind, data = issue["kind"], issue.get("data") or {}
    if kind == "tool_failed":
        from app.services.agent_tools import execute_tool
        result = execute_tool(data.get("name") or "", json.dumps(data.get("args") or {}), int(data.get("agent_id") or issue.get("agent_id") or 0),
                              data.get("role") or "team")
        return (not result.lower().startswith(("failed", "tool ", "access denied"))), result[:300]
    if kind == "email_failed":
        from app.services.notification_service import send_email, email_sent
        if not data.get("to"):
            return False, "No email details saved to resend."
        status = send_email(data["to"], data.get("subject") or "", data.get("body") or "", lead_id=data.get("lead_id"),
                            agent_id=data.get("agent_id") or issue.get("agent_id"), actor=data.get("actor") or "system")
        return email_sent(status), status
    if kind == "llm_dead":
        from app.services import llm
        store.delete(llm.OPENROUTER_DEAD_KEY)
        try:
            llm.complete([{"role": "user", "content": "Say OK."}], max_tokens=3, timeout=10)
        except Exception as e:  # noqa: BLE001
            return False, f"Provider still failing: {str(e)[:200]}. Check OpenRouter credits / key in Settings."
        return True, "OpenRouter answers again; tools are back on live calls."
    if kind == "calling_hours":
        from app.services import agents
        agent_id = data.get("agent_id") or issue.get("agent_id")
        agents.update_automation(int(agent_id), {"calling_hours_start": 9, "calling_hours_end": 21, "calling_days": [0, 1, 2, 3, 4, 5, 6]}, actor="heal")
        return True, f"Agent {agent_id}: calling hours reset to 9:00–21:00, all days."
    if kind == "stale_calls":
        from app.services.call_service import CallService
        CallService().expire_stale()
        return True, "Stuck calls closed."
    if kind == "summary_pending":
        from sqlalchemy import select
        from app.core.database import get_db
        from app.models.call import Call
        from app.services.call_service import CallService, _utcnow
        n = CallService().resummarize_pending(force=True)
        with get_db() as db:
            left = db.execute(select(Call.id, Call.error).where(Call.status == "Completed", Call.summary.is_(None), Call.trigger != "internal",
                                                                Call.created_at >= _utcnow() - timedelta(hours=48))).all()
        if not left:
            return True, f"Summaries written for {n} call(s)."
        why = next((e for _, e in left if e), "") or "provider gave no summary"
        return False, f"{n} retried, {len(left)} still without a summary: {why[:200]}. Check the Summary LLM order / provider credits in Runtime tuning."
    if kind == "inbound_disconnected":
        from app.services.plivo_service import PlivoService
        st = PlivoService().connect_inbound(data.get("number"))
        store.delete(INBOUND_CHECK_KEY)
        return bool(st.get("connected")), (f"{st['number']} now sends incoming calls to this app." if st.get("connected")
                                           else f"Plivo still points {st['number']} at {st.get('app_name') or 'another application'}.")
    if kind == "scheduler_stalled":
        from app.services.scheduler import tick
        tick()
        return True, "Scheduler tick ran."
    if kind == "scheduler_error":
        from app.services.scheduler import run_job
        if not data.get("job"):
            return False, "No job recorded."
        out = run_job(int(data.get("agent_id") or issue.get("agent_id")), data["job"], force=True, actor="heal")
        return (not out.startswith("error:")), out[:300]
    return False, "No automatic remedy: escalated with details for a developer."


def heal(issue_id: str | None = None) -> list[dict]:
    """Heal one issue, or every open one; each result says what happened in plain words."""
    todo = [it for it in _load() if it["status"] in ("open", "escalated") and (not issue_id or it["id"] == issue_id)]
    results = []
    for it in todo:
        try:
            ok, note = _heal_one(it)
        except Exception as e:  # noqa: BLE001 - a remedy that throws is a failed remedy, reported as such
            log.exception("Heal failed for %s", it["kind"])
            ok, note = False, f"Remedy failed: {str(e)[:300]}"
        with _locked_issues() as issues:
            cur = next((i for i in issues if i["id"] == it["id"]), None)
            if cur:
                cur.update(status="healed" if ok else "escalated", result=note, healed_at=_now() if ok else None)
        events.record("issue.healed" if ok else "issue.escalated", it["title"], note, agent_id=it.get("agent_id"), call_id=it.get("call_id"), actor="admin")
        results.append({"id": it["id"], "kind": it["kind"], "title": it["title"], "ok": ok, "note": note})
    return results


def escalation_text() -> str:
    """Every escalated issue as one block an admin can paste to a developer."""
    lines = []
    for it in list_issues():
        if it["status"] == "escalated":
            lines.append(f"[{it['kind']}] {it['title']} (x{it.get('count', 1)}, last {it['last_at']}, agent {it.get('agent_id')}, call {it.get('call_id')})\n"
                         f"  {it['detail']}\n  data: {json.dumps(it.get('data'), ensure_ascii=False, default=str)[:800]}\n  result: {it.get('result')}")
    return "\n".join(lines)


# ---------------- developer hand-off (cloud fix agent) ----------------

EXPORT_TOKEN_KEY = "heal_export_token"


def export_token() -> str:
    """A read-only token the cloud fix agent presents: HEAL_EXPORT_TOKEN from the environment, else one created in the DB."""
    from app.core.config import settings
    if settings.heal_export_token:
        return settings.heal_export_token
    svc = SettingsService()
    token = svc.get_state(EXPORT_TOKEN_KEY)
    if not token:
        token = uuid.uuid4().hex
        svc.set_state(EXPORT_TOKEN_KEY, token)
    return token


def rotate_export_token() -> str:
    """A new token, stored where the panel reads it (runtime overrides win over .env and the DB fallback)."""
    from app.core import runtime
    token = uuid.uuid4().hex
    runtime.save({"heal_export_token": token})
    SettingsService().set_state(EXPORT_TOKEN_KEY, token)
    return token


_PII = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"), (re.compile(r"\+?\d[\d\s-]{8,}\d"), "<phone>")]
_DROP_KEYS = {"body", "text", "to", "subject"}   # email bodies and caller speech never leave the server


def _redact(value):
    """Emails and phone numbers masked, free-text fields dropped: the fix agent needs shapes and errors, not customers."""
    if isinstance(value, dict):
        return {k: ("<omitted>" if k in _DROP_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        for pattern, mask in _PII:
            value = pattern.sub(mask, value)
    return value


def export() -> dict:
    """Escalated issues with what a developer agent needs and nothing about customers: kind, error, argument shapes, counts."""
    from app.core.config import settings
    items = [{k: _redact(it.get(k)) for k in ("id", "kind", "title", "detail", "data", "agent_id", "call_id", "count", "first_at", "last_at", "result", "fix_pr")}
             for it in list_issues() if it["status"] == "escalated" and not it.get("fix_pr")]
    return {"version": settings.app_version, "issues": items}


def ack_fix(issue_id: str, pr_url: str, note: str | None = None) -> bool:
    """The fix agent opened a PR for this issue: link it so the panel shows 'Fix PR' instead of a red row."""
    with _locked_issues() as issues:
        for it in issues:
            if it["id"] == issue_id:
                it.update(fix_pr=pr_url, result=(note or f"Fix proposed: {pr_url}")[:500])
                events.record("issue.fix_proposed", it["title"], pr_url, agent_id=it.get("agent_id"), call_id=it.get("call_id"), actor="fix-agent")
                return True
        return False
