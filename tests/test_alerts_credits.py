"""Provider balances must never make the dashboard wait on three HTTP calls."""

import time

from app.core import store
from app.services import alerts


def _balances(value: str) -> list[dict]:
    return [{"provider": "Sarvam", "label": "Balance", "value": value, "level": "ok",
             "detail": "", "facts": [], "action": None, "balance": 1, "unit": "credits"}]


def test_a_fresh_answer_is_served_from_the_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(alerts, "_plivo", lambda: None)
    monkeypatch.setattr(alerts, "_openrouter", lambda: None)
    monkeypatch.setattr(alerts, "_sarvam", lambda: calls.append(1) or _balances("100")[0])

    store.set_json("alerts:credits:v2", {"providers": _balances("cached"), "checked_at": int(time.time())})
    assert alerts.credits()["providers"][0]["value"] == "cached"
    assert not calls, "a warm cache must not call a provider"


def test_an_expired_answer_is_still_shown_while_a_new_one_is_fetched(monkeypatch):
    slow = []

    def sarvam():
        slow.append(1)
        return _balances("fresh")[0]

    monkeypatch.setattr(alerts, "_plivo", lambda: None)
    monkeypatch.setattr(alerts, "_openrouter", lambda: None)
    monkeypatch.setattr(alerts, "_sarvam", sarvam)

    store.set_json("alerts:credits:v2", None)          # expired
    store.store.delete(store.PREFIX + "alerts:credits:v2")
    store.set_json("alerts:credits:last", {"providers": _balances("previous"), "checked_at": 1})

    served = alerts.credits()
    assert served["providers"][0]["value"] == "previous", "the request must not wait for the providers"

    for _ in range(50):                                 # the refresh runs behind the request
        if slow:
            break
        time.sleep(0.02)
    assert slow, "an expired answer must trigger a refresh"


def test_with_nothing_cached_the_providers_are_asked(monkeypatch):
    monkeypatch.setattr(alerts, "_plivo", lambda: None)
    monkeypatch.setattr(alerts, "_openrouter", lambda: None)
    monkeypatch.setattr(alerts, "_sarvam", lambda: _balances("first")[0])

    for key in ("alerts:credits:v2", "alerts:credits:last"):
        store.store.delete(store.PREFIX + key)
    assert alerts.credits()["providers"][0]["value"] == "first"


def test_dead_openrouter_is_tried_last_for_offline_work(monkeypatch):
    from app.services import llm
    monkeypatch.setattr(llm, "_openrouter_dead", lambda: True)
    assert llm.provider_order("openrouter,sarvam") == ["sarvam", "openrouter"]
    assert llm.provider_order("openrouter") == ["openrouter"]  # the only provider is still tried
    monkeypatch.setattr(llm, "_openrouter_dead", lambda: False)
    assert llm.provider_order("openrouter,sarvam") == ["openrouter", "sarvam"]
