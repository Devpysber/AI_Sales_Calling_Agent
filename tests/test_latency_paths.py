"""Prompt compaction, prefetched retrieval and the slow-reply alert."""

from app.services import agent, alerts, rag


def turns(n: int) -> list[dict]:
    return [{"role": "customer" if i % 2 == 0 else "assistant", "text": f"line {i}"} for i in range(n)]


def test_retrieval_query_is_shared_so_a_prefetch_hits():
    history = [{"role": "assistant", "text": "What is your budget?"}]
    assert agent.retrieval_query(history, "yes tell me") == "What is your budget? yes tell me"
    assert agent.retrieval_query(history, "what does a full service cost for two cars") == \
        "what does a full service cost for two cars"


def test_compact_history_only_folds_turns_past_the_window(monkeypatch):
    calls = []

    def fake_complete(messages, **kw):
        calls.append(messages)
        return type("R", (), {"text": "Wants a 2BHK in Bhopal. Budget 50 lakh."})()

    monkeypatch.setattr(agent.llm, "complete", fake_complete)

    # Nothing has fallen out of the window yet: no LLM call, previous summary kept.
    assert agent.compact_history(turns(10), "old summary") == "old summary"
    assert calls == []

    summary = agent.compact_history(turns(agent.MAX_HISTORY_TURNS + 4), None)
    assert summary == "Wants a 2BHK in Bhopal. Budget 50 lakh."
    folded = calls[0][1]["content"]
    assert "line 0" in folded and "line 3" in folded
    assert "line 17" not in folded  # inside the window: still sent verbatim as a turn


def test_compact_history_since_skips_already_summarised_turns(monkeypatch):
    calls = []

    def fake_complete(messages, **kw):
        calls.append(messages)
        return type("R", (), {"text": "summary"})()

    monkeypatch.setattr(agent.llm, "complete", fake_complete)

    history = turns(agent.MAX_HISTORY_TURNS + 20)
    agent.compact_history(history, "old summary", since=10)
    folded = calls[0][1]["content"]
    assert "line 0" not in folded and "line 9" not in folded  # already covered by prior summary
    assert "line 10" in folded  # first turn past the prior compaction edge


def test_a_summary_reaches_the_prompt_as_earlier_in_this_call(monkeypatch):
    monkeypatch.setattr(agent.rag, "search", lambda *a, **k: [])
    monkeypatch.setattr(agent.agents, "get_profile", lambda _id: {"agent_name": "A", "company_name": "Acme",
                                                                 "default_language": "en-IN"})
    monkeypatch.setattr(agent, "_system_prompt", lambda *a, **k: "SYSTEM")
    messages, _ = agent.build_messages(1, turns(2), "and the price?", {}, summary="Wants a 2BHK.")
    assert "# Earlier in this call" in messages[0]["content"]
    assert "Wants a 2BHK." in messages[0]["content"]
    messages, _ = agent.build_messages(1, turns(2), "and the price?", {})
    assert "Earlier in this call" not in messages[0]["content"]


def test_a_prefetched_query_embedding_is_reused_without_a_second_round_trip(monkeypatch):
    embeds = []

    def fake_embed(queries, timeout=None, task="document", provider=None):
        embeds.append(queries[0])
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(rag.llm, "embed", fake_embed)
    rag._embed_cache.clear()
    assert rag._embed_query("what does a service cost", 2.5) == [0.1, 0.2, 0.3]
    assert rag._embed_query("what does a service cost", 0.3) == [0.1, 0.2, 0.3]
    assert embeds == ["what does a service cost"]  # second turn paid nothing


def test_reply_path_joins_an_inflight_prefetch_instead_of_a_second_embed_call(monkeypatch):
    import threading

    started = threading.Event()
    release = threading.Event()
    embeds = []

    def fake_embed(queries, timeout=None, task="document", provider=None):
        embeds.append(queries[0])
        started.set()
        release.wait(2)
        return [[0.4, 0.5, 0.6]]

    monkeypatch.setattr(rag.llm, "embed", fake_embed)
    monkeypatch.setattr(rag, "_load_index", lambda agent_id: type("Idx", (), {
        "vectors": __import__("numpy").array([[1.0, 0.0]], dtype="float32"),
        "has_vector": __import__("numpy").array([True]),
    })())
    rag._embed_cache.clear()
    rag._inflight.clear()

    rag.prefetch(1, "what is the warranty", timeout=2.0)
    assert started.wait(2)  # prefetch is now mid-flight

    release.set()
    # Same agent as the prefetch: the in-flight map is keyed per agent/provider, since one agent's
    # vector is meaningless against another's passages.
    assert rag._embed_query("what is the warranty", 2.0, agent_id=1) == [0.4, 0.5, 0.6]
    assert embeds == ["what is the warranty"]  # the reply path never started a second embed


def test_search_only_upgrades_to_embeddings_when_the_caller_allowed_a_real_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(rag, "_embed_query", lambda q, t, provider=None, agent_id=None: calls.append(t) or [1.0, 0.0])
    monkeypatch.setattr(rag, "_load_index", lambda agent_id: type("Idx", (), {
        "ids": ["c1"], "texts": ["t"], "titles": ["d"], "tfs": [{}],
        "lengths": __import__("numpy").array([1.0]), "df": {},
        "vectors": __import__("numpy").array([[1.0, 0.0]], dtype="float32"),
        "has_vector": __import__("numpy").array([True]),
    })())

    # Live-path budget (0.3s, matches LIVE_EMBED_TIMEOUT): no BM25 hit must not trigger a synchronous upgrade.
    rag.search(1, "haan theek hai", use_embeddings=False, embed_timeout=0.3)
    assert calls == []

    # A caller that already allowed a real round trip still gets the cross-language upgrade.
    rag.search(1, "haan theek hai", use_embeddings=False, embed_timeout=1.0)
    assert calls == [1.5]


def test_slow_agents_uses_p95_and_needs_a_sample():
    class FakeDB:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _query):
            return type("R", (), {"all": lambda _self: self.rows})()

    fast = [(1, 900.0)] * 10
    assert alerts._slow_agents(FakeDB(fast)) == []

    # One agent slow in its tail, another with too little data to judge.
    rows = [(1, 800.0)] * 9 + [(1, 9000.0)] + [(2, 8000.0)] * 2
    slow = alerts._slow_agents(FakeDB(rows))
    assert [agent_id for agent_id, _, _ in slow] == [1]
    assert slow[0][1] == 9000.0 and slow[0][2] == 10
