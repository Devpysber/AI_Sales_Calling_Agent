"""Hedged OpenRouter requests must stop billing a losing model as soon as another one wins:
not-yet-started attempts (and cap-trim retries, which re-POST from inside the same future) must
see the shared cancel signal and skip their next billed call instead of running to completion."""

import threading
import time

from app.core.config import settings
from app.services import llm


def test_openrouter_model_skips_the_post_once_cancelled(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        raise AssertionError("should never POST once cancelled")

    monkeypatch.setattr(llm._client, "post", fake_post)
    cancel = threading.Event()
    cancel.set()
    try:
        llm._openrouter_model("some-model", [{"role": "user", "content": "hi"}], False, 50, 0.1, cancel=cancel)
        assert False, "expected LLMError"
    except llm.LLMError as e:
        assert "cancelled" in str(e)
    assert calls == []


def test_openrouter_sets_cancel_and_abandons_pending_once_one_model_wins(monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_models", "fast-model,slow-model")
    monkeypatch.setattr(llm, "HEDGE_AFTER_SECONDS", 0.05)
    seen_cancel = {}

    def fake_model(model, messages, json_mode, max_tokens, temperature, shrunk=0, cancel=None):
        seen_cancel[model] = cancel
        if model == "fast-model":
            time.sleep(0.15)  # longer than HEDGE_AFTER_SECONDS, so slow-model gets launched first
            return llm.LLMResult("ok", "openrouter", model, 1)
        # the slower hedge: give the winner time to return and set `cancel` before this checks it
        for _ in range(50):
            if cancel is not None and cancel.is_set():
                raise llm.LLMError("observed cancel before finishing")
            time.sleep(0.01)
        return llm.LLMResult("too-late", "openrouter", model, 1)

    monkeypatch.setattr(llm, "_openrouter_model", fake_model)
    result = llm._openrouter([{"role": "user", "content": "hi"}], False, 50, 0.1, timeout=5)
    assert result.text == "ok"
    assert "slow-model" in seen_cancel
    # give the background hedge thread a moment to observe the cancel signal
    time.sleep(0.3)
