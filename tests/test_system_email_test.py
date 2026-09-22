"""Admin can verify the email provider from the UI, before the first real customer email."""


def test_no_admin_email_on_file_refuses(client, monkeypatch):
    monkeypatch.setattr("app.core.auth.login_email", lambda: "")
    res = client.post("/api/system/email/test")
    assert res.status_code == 400 and "email" in res.json()["detail"].lower()


def test_sends_to_the_logged_in_admin(client, monkeypatch):
    monkeypatch.setattr("app.core.auth.login_email", lambda: "owner@example.com")
    sent = []
    monkeypatch.setattr("app.services.notification_service.send_email",
                        lambda to, subject, body, **k: sent.append((to, subject)) or "sent via test")
    res = client.post("/api/system/email/test")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {"ok": True, "to": "owner@example.com", "status": "sent via test"}
    assert sent == [("owner@example.com", "Test email")]
