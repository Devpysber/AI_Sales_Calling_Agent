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
    assert {"a", "camp"} <= set(lead["tags"]) and r["batch_tag"] in lead["tags"]

    r = client.post(f"{base}/leads/import", files=files(), data={"on_duplicate": "update", "tags": "wave2"}).json()
    assert r["updated"] == 2 and r["created"] == 0
    lead = client.get(f"{base}/leads", params={"search": "9876511111"}).json()["items"][0]
    assert {"a", "camp", "b", "wave2"} <= set(lead["tags"])


def test_call_queue_order_and_remove(client, base):
    ids = [client.post(f"{base}/leads", json={"name": f"Q{i}", "phone": f"98765222{i:02d}"}).json()["id"] for i in range(3)]
    client.post(f"{base}/leads/bulk/queue", json={"ids": ids})
    client.post(f"{base}/leads/queue/order", json={"ids": [ids[2], ids[0], ids[1]]})
    queue = client.get(f"{base}/leads/queue").json()
    mine = [i["id"] for i in queue["items"] if i["id"] in ids]
    assert mine == [ids[2], ids[0], ids[1]] and queue["items"][0]["position"] == 1
    client.post(f"{base}/leads/queue/remove", json={"ids": [ids[0]]})
    assert ids[0] not in [i["id"] for i in client.get(f"{base}/leads/queue").json()["items"]]


def test_schedule_queue_and_follow_up_time(client, base):
    from datetime import datetime, timedelta
    from app.services.call_service import IST

    lead = client.post(f"{base}/leads", json={"name": "Scheduled", "phone": "9876533333"}).json()
    at = (datetime.now(IST) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
    r = client.post(f"{base}/leads/bulk/queue", json={"ids": [lead["id"]], "at": at}).json()
    assert r["at"] == at.replace("T", " ") and "Scheduled" in r["eta"]
    got = client.get(f"{base}/leads/{lead['id']}").json()
    assert got["callback_at"] == at.replace("T", " ") and got["call_status"] == "Pending"

    later = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    got = client.patch(f"{base}/leads/{lead['id']}", json={"callback_at": later}).json()
    assert got["callback_at"] == later and got["follow_up_date"] == later[:10]
    assert client.patch(f"{base}/leads/{lead['id']}", json={"callback_at": "2001-01-01 10:00"}).status_code == 400
    assert client.patch(f"{base}/leads/{lead['id']}", json={"callback_at": ""}).json()["callback_at"] in ("", None)


def test_search_matches_any_phone_format(client, base):
    client.post(f"{base}/leads", json={"name": "Format Test", "phone": "9876544444", "city": "Indore"})
    for q in ["98765 44444", "09876544444", "+91-9876544444", "format", "indore"]:
        items = client.get(f"{base}/leads", params={"search": q}).json()["items"]
        assert any(l["name"] == "Format Test" for l in items), q
    board = client.get(f"{base}/leads/board", params={"search": "98765 44444", "per_column": 100}).json()
    assert sum(c["total"] for c in board.values()) >= 1


def test_any_builder_field_names_land_on_the_lead(client, base):
    """A form written for a clinic, a school or a showroom must not need renaming to work."""
    agent_id = int(base.rsplit("/", 1)[1])
    token = client.post(f"{base}/intake/rotate").json()["token"]
    res = client.post(f"/api/public/agents/{agent_id}/leads", params={"token": token},
                      data={"patient_name": "Neha Rao", "mobile": "9812377001", "e-mail": "neha@x.com", "clinic": "Smile Dental",
                            "branch": "Indore", "treatment": "root canal quote", "utm_medium": "google-ads",
                            "preferred_slot": "Saturday 11am", "_wpcf7_version": "5.9", "g-recaptcha-response": "xyz",
                            "website": ""})
    assert res.status_code == 200, res.text
    lead = client.get(f"{base}/leads", params={"search": "9812377001"}).json()["items"][0]
    assert lead["name"] == "Neha Rao" and lead["email"] == "neha@x.com"
    assert lead["company"] == "Smile Dental" and lead["city"] == "Indore"
    assert "root canal quote" in (lead.get("requirements") or "") + (lead.get("notes") or "")
    notes = lead.get("notes") or ""
    assert "Saturday 11am" in notes                  # unknown fields are kept for the agent
    assert "wpcf7" not in notes.lower() and "recaptcha" not in notes.lower()
