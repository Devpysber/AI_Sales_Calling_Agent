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

    def fake_embed(queries, timeout=None):
        embeds.append(queries[0])
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(rag.llm, "embed", fake_embed)
    rag._embed_cache.clear()
    assert rag._embed_query("what does a service cost", 2.5) == [0.1, 0.2, 0.3]
    assert rag._embed_query("what does a service cost", 0.3) == [0.1, 0.2, 0.3]
    assert embeds == ["what does a service cost"]  # second turn paid nothing


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
