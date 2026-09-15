"""
Email notifications via SMTP (logged to the activity feed either way).
"""

import smtplib
from email.message import EmailMessage

from app.core.config import settings
from app.services import events


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_email(to: str, subject: str, body: str, lead_id: int | None = None) -> str:
    if not email_configured():
        status = "logged only (SMTP not configured)"
    else:
        try:
            message = EmailMessage()
            message["From"], message["To"], message["Subject"] = settings.smtp_from, to, subject
            message.set_content(body)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
            status = "sent"
        except Exception as e:
            status = f"failed: {e}"
    events.record("email", f"Email to {to}: {subject}", status, lead_id=lead_id, data={"body": body[:2000]})
    return status
