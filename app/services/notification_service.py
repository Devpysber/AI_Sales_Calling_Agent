"""
Email notifications via Resend (preferred) or SMTP; every attempt is logged to the activity feed.
"""

import contextlib
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

import httpx

from app.core.config import settings
from app.services import events


def email_provider() -> str | None:
    if settings.resend_api_key and (settings.email_from or settings.smtp_from):
        return "resend"
    if settings.smtp_host and settings.smtp_from:
        return "smtp"
    return None


def email_configured() -> bool:
    return email_provider() is not None


def email_detail() -> str:
    provider = email_provider()
    if provider == "resend":
        return f"Resend · from {settings.email_from or settings.smtp_from}"
    if provider == "smtp":
        return f"SMTP {settings.smtp_host} · from {settings.smtp_from}"
    return "Not configured: set RESEND_API_KEY and EMAIL_FROM (or SMTP_*). Reminders and reports are logged to Activity only."


def sender(display_name: str | None = None) -> str:
    """EMAIL_FROM with the display name replaced by the sending agent's company, e.g. "Skyline Realty <noreply@…>"."""
    configured_name, address = parseaddr(settings.email_from or settings.smtp_from)
    name = " ".join((display_name or "").replace("<", "").replace(">", "").split()) or configured_name
    return formataddr((name, address)) if name else address


def _html_template(body: str) -> str:
    html_body = body.replace('\n', '<br>')
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #eaeaea; border-radius: 12px; overflow: hidden; background: #ffffff;">
        <div style="padding: 32px 40px; color: #333333; font-size: 16px; line-height: 1.6;">
            {html_body}
        </div>
        <div style="background: #f9f9f9; padding: 24px 40px; text-align: center; border-top: 1px solid #eaeaea; font-size: 13px; color: #888888;">
            Sent via Psyber Voice AI Caller<br>
            <a href="https://aicaller.psyber.in" style="color: #5b4bf5; text-decoration: none;">aicaller.psyber.in</a>
        </div>
    </div>
    """

def _send_resend(to: str, subject: str, body: str, from_: str):
    payload = {"from": from_, "to": [to], "subject": subject, "text": body, "html": _html_template(body)}
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to
    res = httpx.post("https://api.resend.com/emails", json=payload, timeout=20,
                     headers={"Authorization": f"Bearer {settings.resend_api_key}"})
    if res.status_code >= 400:
        raise RuntimeError(f"Resend {res.status_code}: {res.text[:200]}")


def _send_smtp(to: str, subject: str, body: str, from_: str):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = from_, to, subject
    if settings.email_reply_to:
        message["Reply-To"] = settings.email_reply_to
    message.set_content(body)
    message.add_alternative(_html_template(body), subtype='html')
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_email(to: str, subject: str, body: str, lead_id: int | None = None, agent_id: int | None = None,
               actor: str = "system") -> str:
    """
    agent_id: the email is sent in that agent's name (its company, else the agent name).
    actor: who sent it — "ai" for AI follow-ups, "user" for a manual send, "system" for reports and
    reminders. The Email Centre counts and filters by this, so it must be set by the caller.
    """
    provider = email_provider()
    display = None
    if agent_id:
        from app.services import agents
        with contextlib.suppress(Exception):
            display = agents.get_profile(agent_id).get("company_name") or (agents.get(agent_id) or {}).get("name")
    from_ = sender(display)
    if provider is None:
        status = "logged only (email not configured)"
    else:
        try:
            (_send_resend if provider == "resend" else _send_smtp)(to, subject, body, from_)
            status = f"sent via {provider}"
        except Exception as e:
            status = f"failed: {e}"
    events.record("email", f"Email to {to}: {subject}", f"{status} · from {from_}", agent_id=agent_id, lead_id=lead_id,
                  actor=actor, data={"body": body[:2000], "from": from_})
    return status


def team_recipients(role: str | None = None) -> list[str]:
    """
    Everyone who should hear when a caller needs a person: the account owner plus the team members.

    Without this only the admin login address was told, so a message a caller left for "the team"
    never reached the people who could act on it.
    """
    from app.core.auth import login_email
    from app.services.settings_service import SettingsService

    seen: list[str] = []
    for address in [login_email()] + [
            m.get("email", "") for m in (SettingsService().get_state("team_members") or [])
            if not role or (m.get("role") or "").lower() == role.lower()]:
        address = (address or "").strip().lower()
        if address and address not in seen:
            seen.append(address)
    return seen


def notify_team(subject: str, body: str, lead_id: int | None = None, agent_id: int | None = None,
                role: str | None = None) -> list[str]:
    """Send one message to every team recipient. Returns the addresses that accepted it."""
    delivered = []
    for address in team_recipients(role):
        if email_sent(send_email(address, subject, body, lead_id=lead_id, agent_id=agent_id, actor="ai")):
            delivered.append(address)
    return delivered


def email_sent(status: str) -> bool:
    """True only when the provider actually accepted the message (send_email never raises, so callers check this)."""
    return status.startswith("sent via")
