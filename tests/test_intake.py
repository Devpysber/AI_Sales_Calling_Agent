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
