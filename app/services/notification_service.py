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


def _send_resend(to: str, subject: str, body: str, from_: str):
    payload = {"from": from_, "to": [to], "subject": subject, "text": body}
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
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def send_email(to: str, subject: str, body: str, lead_id: int | None = None, agent_id: int | None = None) -> str:
    """agent_id: the email is sent in that agent's name (its company, else the agent name)."""
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
                  data={"body": body[:2000], "from": from_})
    return status
