"""A 401/402 embeddings response must mark OpenRouter dead, not raise KeyError('data')."""

from app.services import llm

# conftest's autouse fake_ai fixture replaces llm.embed with a stub for every test;
# grab the real function here, at import time, before any fixture runs.
_real_embed = llm.embed


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.text = str(self._body)

    def json(self):
        return self._body


def test_no_credits_marks_openrouter_dead_and_returns_none(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "key")
    monkeypatch.setattr(llm, "_openrouter_dead", lambda: False)
    monkeypatch.setattr(llm._client, "post", lambda *a, **k: _Resp(402, {"error": "no credits"}))

    marked = []
    monkeypatch.setattr(llm, "_mark_openrouter_dead", lambda reason: marked.append(reason))

    assert _real_embed(["hello"]) is None
    assert marked, "a 402 response must mark OpenRouter dead"


def test_unrelated_error_status_does_not_mark_openrouter_dead(monkeypatch):
    monkeypatch.setattr(llm.settings, "openrouter_api_key", "key")
    monkeypatch.setattr(llm, "_openrouter_dead", lambda: False)
    monkeypatch.setattr(llm._client, "post", lambda *a, **k: _Resp(500, {"error": "boom"}))

    marked = []
    monkeypatch.setattr(llm, "_mark_openrouter_dead", lambda reason: marked.append(reason))

    assert _real_embed(["hello"]) is None
    assert not marked, "a 5xx response should not be treated as an account error"
