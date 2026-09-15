"""Website form lead capture."""


def test_website_form_creates_and_dedupes_leads(client, base, monkeypatch):
    info = client.get(f"{base}/intake").json()
    agent_id = int(base.rsplit("/", 1)[1])
    url = f"/api/public/agents/{agent_id}/leads"

    anon = client.__class__(client.app)
    assert anon.post(url, params={"token": "wrong"}, json={"phone": "9876500001"}).status_code == 403
    assert anon.options(url).headers["access-control-allow-origin"] == "*"

    res = anon.post(url, params={"token": info["token"]}, json={"name": "Web Lead", "phone": "9876500001", "message": "2BHK price?"},
                    headers={"Origin": "https://skyline.example"})
    assert res.status_code == 200 and res.json()["created"] and not res.json()["calling"]
    lead = client.get(f"{base}/leads/{res.json()['lead_id']}").json()
    assert lead["source"] == "website:skyline.example" and "2BHK price?" in lead["notes"] and lead["call_status"] == "Pending"

    again = anon.post(url, params={"token": info["token"]}, data={"phone": "+91 98765 00001", "message": "Call me after 5"}).json()
    assert again["lead_id"] == lead["id"] and not again["created"]
    assert "Call me after 5" in client.get(f"{base}/leads/{lead['id']}").json()["notes"]

    assert anon.post(url, params={"token": info["token"]}, json={"phone": "abc"}).status_code == 400
    rotated = client.post(f"{base}/intake/rotate").json()
    assert rotated["token"] != info["token"]
    assert anon.post(url, params={"token": info["token"]}, json={"phone": "9876500002"}).status_code == 403


def test_nurture_and_automation_defaults(client, base):
    cfg = client.get(f"{base}/automation").json()["settings"]
    assert cfg["nurture_after_days"] == 3 and cfg["speed_to_lead_enabled"] is False
    from app.services import scheduler
    assert "nurture" in scheduler.JOBS


def test_lead_views_and_phone_validation(client, base):
    from app.services.crm_service import normalize_phone
    assert normalize_phone("91627507903") is None           # Indian number missing a digit
    assert normalize_phone("9876543210") == "+919876543210"
    assert normalize_phone("+14155550100") == "+14155550100"  # other countries still accepted

    counts = client.get(f"{base}/leads/views").json()
    assert {"website", "never_called", "hot_uncalled", "callbacks", "meetings", "attention", "dnc"} <= set(counts)
    web = client.get(f"{base}/leads", params={"view": "website"}).json()
    assert web["total"] == counts["website"] and all(l["source"].startswith("website") for l in web["items"])
    assert all(l["phone_valid"] for l in web["items"])


def test_speed_to_lead_schedules_call_and_invalid_numbers_never_dial(client, base, monkeypatch):
    from app.services.call_service import CallError, CallService

    agent_id = int(base.rsplit("/", 1)[1])
    client.put(f"{base}/automation", json={"speed_to_lead_enabled": True, "speed_to_lead_min_seconds": 60, "speed_to_lead_max_seconds": 120})
    monkeypatch.setattr("app.services.call_service.within_calling_hours", lambda cfg, now=None: True)
    token = client.get(f"{base}/intake").json()["token"]
    res = client.__class__(client.app).post(f"/api/public/agents/{agent_id}/leads", params={"token": token},
                                            json={"name": "Speedy", "phone": "9876500099"}).json()
    lead = client.get(f"{base}/leads/{res['lead_id']}").json()
    assert res["calling"] and lead["callback_at"] and lead["call_status"] == "Pending"

    from app.core.database import get_db
    from app.models.lead import Lead
    with get_db() as db:
        broken = Lead(agent_id=agent_id, phone="+91627507903", status="New", language="en-IN", retry_count=0)
        db.add(broken)
        db.flush()
        broken_id = broken.id
    try:
        CallService(agent_id).start(broken_id)
        raise AssertionError("invalid number was dialled")
    except CallError as e:
        assert "not a complete phone number" in str(e)


def test_import_analyze_update_and_queue(client, base):
    import io
    csv = ("Full Name,Mobile,Email,Call Language,Stage,Tags\n"
           "Import One,9876511111,bad-email,Hindi,Interested,a\n"
           "Import One Again,9876511111,,English,,b\n"
           "Broken,91627507903,,,,\n")
    files = lambda: {"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")}
    preview = client.post(f"{base}/leads/import/preview", files=files()).json()
    assert preview["mapping"]["Call Language"] == "language" and preview["mapping"]["Stage"] if "Stage" in preview["mapping"] else True
    a = preview["analysis"]
    assert (a["ready"], a["duplicates"], a["invalid"]) == (1, 1, 1)

    r = client.post(f"{base}/leads/import", files=files(), data={"tags": "camp", "queue_for_calls": "true"}).json()
    assert (r["created"], r["skipped_duplicates"], len(r["errors"])) == (1, 1, 1)
    lead = client.get(f"{base}/leads", params={"search": "9876511111"}).json()["items"][0]
    assert lead["email"] is None and lead["language"] == "hi-IN" and lead["call_status"] == "Pending"
    assert set(lead["tags"]) == {"a", "camp"}

    r = client.post(f"{base}/leads/import", files=files(), data={"on_duplicate": "update", "tags": "wave2"}).json()
    assert r["updated"] == 2 and r["created"] == 0
    lead = client.get(f"{base}/leads", params={"search": "9876511111"}).json()["items"][0]
    assert {"a", "camp", "b", "wave2"} <= set(lead["tags"])
