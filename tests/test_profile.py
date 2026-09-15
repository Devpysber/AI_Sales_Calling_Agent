"""Admin profile and password change."""


def test_profile_and_password_change(client):
    p = client.get("/api/auth/profile").json()
    assert p["username"] == "admin" and p["password_source"] == "environment"

    assert client.put("/api/auth/profile", json={"display_name": "Ops Lead", "email": "not-an-email"}).status_code == 400
    saved = client.put("/api/auth/profile", json={"display_name": "Ops Lead", "email": "ops@example.com", "role": "Head of sales"}).json()
    assert saved["display_name"] == "Ops Lead"
    assert client.get("/api/auth/me").json()["display_name"] == "Ops Lead"

    assert client.post("/api/auth/password", json={"current_password": "wrong", "new_password": "NewPass-2026"}).status_code == 400
    assert client.post("/api/auth/password", json={"current_password": "test-pass", "new_password": "alllowercase1"}).status_code == 400
    assert client.post("/api/auth/password", json={"current_password": "test-pass", "new_password": "NewPass-2026"}).status_code == 200
    assert client.get("/api/auth/profile").json()["password_source"] == "dashboard"

    # Old password stops working, new one signs in; restore for other tests
    assert client.post("/api/auth/login", json={"username": "admin", "password": "test-pass"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "NewPass-2026"}).status_code == 200
    assert client.post("/api/auth/password", json={"current_password": "NewPass-2026", "new_password": "Test-pass-restored1"}).status_code == 200
    from app.core import auth
    profile = auth._profile()
    profile.pop("password_hash", None)
    auth._save_profile(profile)
    assert client.post("/api/auth/login", json={"username": "admin", "password": "test-pass"}).status_code == 200
