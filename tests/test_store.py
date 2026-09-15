import json

import fakeredis

from app.core import store


def test_redis_store_semantics(monkeypatch):
    redis_store = store.RedisStore.__new__(store.RedisStore)
    redis_store.client = fakeredis.FakeRedis()
    monkeypatch.setattr(store, "store", redis_store)

    store.set_json("session:abc", {"history": [1, 2]}, ttl=60)
    assert store.get_json("session:abc") == {"history": [1, 2]}
    assert redis_store.client.ttl("va:session:abc") > 0

    store.set_bytes("audio:x", b"RIFF", ttl=60)
    assert store.get_bytes("audio:x") == b"RIFF"

    assert store.acquire_lock("scheduler", "node-a", 30) is True
    assert store.acquire_lock("scheduler", "node-b", 30) is False   # other replica blocked
    assert store.acquire_lock("scheduler", "node-a", 30) is True    # owner renews

    assert not any(store.rate_limited("login:1.2.3.4", limit=3, window=60) for _ in range(3))
    assert store.rate_limited("login:1.2.3.4", limit=3, window=60)
    assert json.loads(redis_store.client.get("va:session:abc"))["history"] == [1, 2]


def test_parse_tool_call_markup():
    from app.services.llm import parse_json
    text = ('<tool_call>json\n<arg_key>reply</arg_key>\n<arg_value>Theek hai, kal 4 baje.</arg_value>\n'
            '<arg_key>end_call</arg_key>\n<arg_value>true</arg_value>\n'
            '<arg_key>crm_update</arg_key>\n<arg_value>{"meeting_at": "2026-09-15 16:00"}</arg_value>')
    data = parse_json(text)
    assert data["reply"] == "Theek hai, kal 4 baje." and data["end_call"] is True
    assert data["crm_update"]["meeting_at"] == "2026-09-15 16:00"
