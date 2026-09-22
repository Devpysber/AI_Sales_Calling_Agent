from unittest.mock import MagicMock, patch

from app.services.notification_service import _send_smtp


def test_send_smtp_html_uses_sender_company_name():
    """SMTP emails must carry the sending company's name in the HTML header, not the 'Samvaad AI' fallback."""
    smtp_instance = MagicMock()
    smtp_instance.__enter__.return_value = smtp_instance
    with patch("app.services.notification_service.smtplib.SMTP", return_value=smtp_instance):
        _send_smtp("to@example.com", "Subject", "Body text", 'Skyline Realty <noreply@example.com>')

    sent_message = smtp_instance.send_message.call_args[0][0]
    html_part = next(p for p in sent_message.iter_parts() if p.get_content_type() == "text/html")
    html_body = html_part.get_content()
    assert "Skyline Realty" in html_body
