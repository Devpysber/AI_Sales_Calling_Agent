"""Admin profile and password change."""


def test_profile_and_password_change(client):
    p = client.get("/api/auth/profile").json()
    assert p["username"] == "admin" and p["password_source"] == "environment"

    assert client.put("/api/auth/profile", json={"display_name": "Ops Lead", "email": "not-an-email"}).status_code == 400
    saved = client.put("/api/auth/profile", json={"display_name": "Ops Lead", "email": "Ops@Example.com", "role": "Head of sales", "current_password": "test-pass"}).json()
    assert saved["display_name"] == "Ops Lead" and saved["login_email"] == "ops@example.com"
    # Email is now the sign-in identifier; the username no longer works
    assert client.post("/api/auth/login", json={"username": "admin", "password": "test-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "ops@example.com", "password": "test-pass"}).status_code == 200
    # Changing the sign-in email needs the current password
    assert client.put("/api/auth/profile", json={"email": "new@example.com"}).status_code == 400
    assert client.put("/api/auth/profile", json={"email": "new@example.com", "current_password": "test-pass",
                                                "display_name": "Ops Lead"}).status_code == 200
    assert client.get("/api/auth/me").json()["display_name"] == "Ops Lead"

    assert client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "NewPass-2026"}).status_code == 400
    assert client.post("/api/auth/password", json={"current_password": "test-pass", "new_password": "alllowercase1"}).status_code == 400
    assert client.post("/api/auth/password", json={"current_password": "test-pass", "new_password": "NewPass-2026"}).status_code == 200
    assert client.get("/api/auth/profile").json()["password_source"] == "dashboard"

    # Old password stops working, new one signs in; restore for other tests
    assert client.post("/api/auth/login", json={"email": "new@example.com", "password": "test-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "new@example.com", "password": "NewPass-2026"}).status_code == 200
    assert client.post("/api/auth/password", json={"current_password": "NewPass-2026", "new_password": "Test-pass-restored1"}).status_code == 200
    from app.core import auth
    profile = auth._profile()
    profile.pop("password_hash", None)
    profile.pop("email", None)
    auth._save_profile(profile)
    assert client.post("/api/auth/login", json={"username": "admin", "password": "test-pass"}).status_code == 200
