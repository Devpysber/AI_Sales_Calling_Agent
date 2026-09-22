"""Export and select-all must mean the same thing as the filters on screen."""

import pytest


@pytest.fixture(scope="module")
def book(client):
    """An agent with a small, deliberately mixed book of leads."""
    agent = client.post("/api/agents", json={"name": "Filter desk"}).json()
    base = f"/api/agents/{agent['id']}"
    for name, phone, status in (("Asha", "+919000000101", "New"),
                                ("Bhavna", "+919000000102", "Interested"),
                                ("Chetan", "+919000000103", "Interested")):
        assert client.post(f"{base}/leads", json={"name": name, "phone": phone, "status": status}).status_code == 200
    return base


def test_export_returns_only_the_filtered_rows(client, book):
    everything = client.get(f"{book}/leads/export")
    assert everything.status_code == 200
    assert "Asha" in everything.text and "Bhavna" in everything.text

    filtered = client.get(f"{book}/leads/export", params={"status": "Interested"})
    assert "Bhavna" in filtered.text and "Chetan" in filtered.text
    assert "Asha" not in filtered.text, "a filtered export must not include rows the filter excluded"


def test_search_narrows_the_export_too(client, book):
    csv = client.get(f"{book}/leads/export", params={"search": "Chetan"}).text
    assert "Chetan" in csv and "Bhavna" not in csv


def test_matching_ids_cover_the_whole_filtered_set(client, book):
    everything = client.get(f"{book}/leads/ids").json()["ids"]
    assert len(everything) == 3

    interested = client.get(f"{book}/leads/ids", params={"status": "Interested"}).json()["ids"]
    assert len(interested) == 2
    assert set(interested) < set(everything)

    # The ids are real and usable by the bulk endpoints that receive them.
    updated = client.post(f"{book}/leads/bulk/update", json={"ids": interested, "qualification": "Hot"}).json()
    assert updated["updated"] == 2


def test_matching_ids_respects_its_limit(client, book):
    capped = client.get(f"{book}/leads/ids", params={"limit": 2}).json()
    assert len(capped["ids"]) == 2 and capped["limit"] == 2


def test_do_not_call_can_be_switched_off_again(client, book):
    """The edit form posts the stage with every save, so the flag must follow the switch, not the stage."""
    lead = client.post(f"{book}/leads", json={"name": "Suppressed", "phone": "+919000000111"}).json()
    url = f"{book}/leads/{lead['id']}"
    assert client.patch(url, json={"status": "Do Not Call"}).json()["do_not_call"] is True
    back = client.patch(url, json={"status": "Do Not Call", "do_not_call": False}).json()
    assert back["do_not_call"] is False and back["status"] == "Do Not Call"


def test_a_call_status_only_a_real_call_can_reach_is_refused(client, book):
    lead = client.post(f"{book}/leads", json={"name": "Hand typed", "phone": "+919000000112"}).json()
    url = f"{book}/leads/{lead['id']}"
    assert client.patch(url, json={"call_status": "In Progress"}).status_code == 400
    assert client.patch(url, json={"status": "Nowhere"}).status_code == 400
    assert client.patch(url, json={"qualification": "Lukewarm"}).status_code == 400
    assert client.patch(url, json={"call_status": "No Answer"}).status_code == 200


def test_an_import_marked_do_not_call_is_never_dialled(client, book):
    import io

    csv = "name,phone,status\nStop Calling,+919000000113,DNC\nFine To Call,+919000000114,New\n"
    files = {"file": ("leads.csv", io.BytesIO(csv.encode()), "text/csv")}
    result = client.post(f"{book}/leads/import", files=files, data={"queue_for_calls": "true"}).json()
    assert result["created"] == 2, result
    rows = {l["phone"]: l for l in client.get(f"{book}/leads", params={"search": "90000001"}).json()["items"]}
    stop = rows["+919000000113"]
    assert stop["do_not_call"] is True and stop["status"] == "Do Not Call" and stop["call_status"] != "Pending"
    assert rows["+919000000114"]["call_status"] == "Pending"
