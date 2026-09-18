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
