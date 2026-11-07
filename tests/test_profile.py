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


def test_new_caller_details_are_capped_and_ordered(client, base):
    res = client.put(f"{base}/profile", json={"inbound_collect": ["city", "email", "requirement", "name"]})
    assert res.status_code == 200
    assert client.get(f"{base}/profile").json()["profile"]["inbound_collect"] == ["name", "requirement", "city", "email"]
    res = client.put(f"{base}/profile", json={"inbound_collect": ["name", "requirement", "city", "company", "email"]})
    assert res.status_code == 400 and "at most 4" in res.text


def test_an_old_nine_detail_list_reads_back_capped(client, base):
    from app.services import agents
    agent_id = int(base.rsplit("/", 1)[1])
    agents._update_group(agent_id, "profile", {"inbound_collect": ["city", "company", "email", "budget", "timeline", "callback_time", "source", "name", "requirement"]}, "test", "test")
    assert client.get(f"{base}/profile").json()["profile"]["inbound_collect"] == ["name", "requirement", "city", "company"]


def test_persona_save_snapshots_the_prior_instructions_and_restore_round_trips(client, base):
    agent_id = int(base.rsplit("/", 1)[1])
    assert client.put(f"{base}/profile", json={"instructions": "v1 instructions"}).status_code == 200
    before = client.get(f"{base}/persona/history").json()["history"]  # snapshot of the default instructions

    assert client.put(f"{base}/profile", json={"instructions": "v2 instructions"}).status_code == 200
    history = client.get(f"{base}/persona/history").json()["history"]
    assert len(history) == len(before) + 1 and history[0]["instructions"] == "v1 instructions"

    assert client.put(f"{base}/profile", json={"instructions": "v3 instructions"}).status_code == 200
    history = client.get(f"{base}/persona/history").json()["history"]
    # Most recent first
    assert [h["instructions"] for h in history[:2]] == ["v2 instructions", "v1 instructions"]

    snapshot_id = history[0]["id"]
    restored = client.post(f"{base}/persona/history/{snapshot_id}/restore").json()
    assert restored["instructions"] == "v2 instructions"
    assert client.get(f"{base}/profile").json()["profile"]["instructions"] == "v2 instructions"
    # Restoring itself snapshots what it replaced ("v3 instructions")
    history = client.get(f"{base}/persona/history").json()["history"]
    assert history[0]["instructions"] == "v3 instructions"

    assert client.post(f"{base}/persona/history/999999/restore").status_code == 404


def test_persona_history_is_capped_and_does_not_crash(client, base):
    from app.services import agents
    agent_id = int(base.rsplit("/", 1)[1])
    for i in range(agents.MAX_PERSONA_SNAPSHOTS + 10):
        client.put(f"{base}/profile", json={"instructions": f"version {i}"})
    history = client.get(f"{base}/persona/history").json()["history"]
    assert len(history) <= agents.MAX_PERSONA_SNAPSHOTS
