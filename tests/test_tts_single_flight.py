"""cached_pcm() must not pay for two syntheses when several threads ask for the same cold cache key
at once (a dial burst all requesting the same greeting line) -- the second-and-later callers should
share the first one's result instead of each re-synthesizing."""

import threading
import time

from app.services import tts


def test_concurrent_cold_requests_for_the_same_line_synthesize_only_once(monkeypatch):
    calls = []
    lock = threading.Lock()

    def fake_synthesize_pcm(text, language, speaker):
        with lock:
            calls.append(text)
        time.sleep(0.1)  # hold the single-flight lock long enough for other threads to queue behind it
        return b"audio-bytes"

    monkeypatch.setattr(tts, "synthesize_pcm", fake_synthesize_pcm)
    # Use a store backed by a plain dict so this test doesn't depend on Redis being configured.
    cache: dict[str, bytes] = {}
    monkeypatch.setattr(tts.store, "get_bytes", lambda key: cache.get(key))
    monkeypatch.setattr(tts.store, "set_bytes", lambda key, value, ttl=None: cache.__setitem__(key, value))

    results = []
    threads = [threading.Thread(target=lambda: results.append(tts.cached_pcm("Hello, thanks for calling", "en-IN", "aditya")))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"expected exactly one synthesis for 8 concurrent cold requests, got {len(calls)}"
    assert all(r == b"audio-bytes" for r in results)
