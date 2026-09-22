"""Runtime issues: reported on failure, listed for the admin, healed by re-running what failed."""
from app.services import agent_tools, heal_service


def test_failed_tool_is_reported_and_healable(base):
    agent_id = int(base.rsplit("/", 1)[1])
    before = {i["id"] for i in heal_service.list_issues()}
    out = agent_tools.execute_tool("set_automation", '{"switches": "warp_drive", "on": "off"}', agent_id, role="team")
    assert out.startswith("Failed")
    new = [i for i in heal_service.list_issues() if i["id"] not in before]
    assert new and new[0]["kind"] == "tool_failed" and new[0]["healable"]
    # Nothing to fix for a made-up switch: heal reports the failure again and escalates rather than claims success.
    results = heal_service.heal(new[0]["id"])
    assert results and results[0]["ok"] is False
    assert any(i["id"] == new[0]["id"] and i["status"] == "escalated" for i in heal_service.list_issues())
    assert "warp_drive" in heal_service.escalation_text()


def test_normalised_args_from_markup_succeed(base):
    agent_id = int(base.rsplit("/", 1)[1])
    # sarvam markup: list as a string, boolean as a word, tool name miscased
    out = agent_tools.execute_tool("SetAutomation", '{"switches": "retry, nurture", "on": "off"}', agent_id, role="team")
    assert "off" in out and not out.startswith("Failed")
    out = agent_tools.execute_tool("set_automation", '{"switches": "[\\"retry\\"]", "on": "true"}', agent_id, role="team")
    assert "Retry calls now on" in out


def test_calling_hours_detected_and_healed(base):
    from app.services import agents
    agent_id = int(base.rsplit("/", 1)[1])
    agents._update_group(agent_id, "automation", {"calling_hours_start": 20, "calling_hours_end": 8}, "test", "test")
    issues = heal_service.detect()
    hit = next(i for i in issues if i["kind"] == "calling_hours" and i["agent_id"] == agent_id)
    results = heal_service.heal(hit["id"])
    assert results[0]["ok"]
    cfg = agents.get_automation(agent_id)
    assert (cfg["calling_hours_start"], cfg["calling_hours_end"]) == (9, 21)


def test_dismiss_and_api(client):
    issue = heal_service.report("turn_error", "boom", data={"error": "boom"})
    listed = client.get("/api/system/issues", params={"detect": "false"}).json()
    assert any(i["id"] == issue["id"] for i in listed["issues"])
    res = client.post("/api/system/issues/heal", json={"id": issue["id"]}).json()
    assert res["results"][0]["ok"] is False  # no remedy: escalated for a developer
    assert "boom" in res["escalation"]
    assert client.post(f"/api/system/issues/{issue['id']}/dismiss").json()["ok"]
    assert all(i["id"] != issue["id"] for i in heal_service.list_issues())


def test_export_and_fix_ack(client):
    issue = heal_service.report("turn_error", "kaboom", data={"error": "kaboom"})
    heal_service.heal(issue["id"])  # no remedy -> escalated
    token = client.get("/api/system/issues/token").json()["token"]
    assert client.get("/api/system/issues/export").status_code == 401
    exported = client.get("/api/system/issues/export", headers={"X-Heal-Token": token}).json()
    assert any(i["id"] == issue["id"] for i in exported["issues"])
    assert client.post(f"/api/system/issues/{issue['id']}/fix", headers={"X-Heal-Token": token},
                       json={"pr_url": "https://github.com/x/y/pull/1"}).json()["ok"]
    assert all(i["id"] != issue["id"] for i in client.get("/api/system/issues/export", headers={"X-Heal-Token": token}).json()["issues"])
    assert next(i for i in heal_service.list_issues() if i["id"] == issue["id"])["fix_pr"].endswith("/pull/1")


def test_export_rejects_wrong_token(client):
    heal_service.report("turn_error", "nope", data={"error": "nope"})
    token = client.get("/api/system/issues/token").json()["token"]
    bad = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert client.get("/api/system/issues/export", headers={"X-Heal-Token": bad}).status_code == 401


def test_export_redacts_customer_data(client):
    issue = heal_service.report("turn_error", "KeyError at rahul@x.com (caller said: call +91 98765 43210)",
                                data={"error": "KeyError", "text": "call +91 98765 43210", "body": "rahul@x.com"})
    heal_service.heal(issue["id"])
    token = client.get("/api/system/issues/token").json()["token"]
    row = next(i for i in client.get("/api/system/issues/export", headers={"X-Heal-Token": token}).json()["issues"] if i["id"] == issue["id"])
    dumped = str(row)
    assert "rahul@x.com" not in dumped and "98765" not in dumped and row["data"]["body"] == "<omitted>"


def test_failed_send_email_reports_only_email_failed_not_tool_failed(base, monkeypatch):
    """A failed send_email must not also get a tool_failed issue, or heal() would resend it twice
    (once for email_failed's own retry, once for tool_failed re-running the tool)."""
    agent_id = int(base.rsplit("/", 1)[1])
    monkeypatch.setattr(agent_tools, "send_email", lambda *a, **k: "failed: boom")
    before = {i["id"] for i in heal_service.list_issues()}
    out = agent_tools.execute_tool(
        "send_email", '{"to": "a@b.com", "subject": "hi", "body": "hello"}', agent_id, role="team")
    assert out.startswith("Failed to send email")
    new = [i for i in heal_service.list_issues() if i["id"] not in before]
    assert not any(i["kind"] == "tool_failed" for i in new)


def test_detect_bump_false_does_not_inflate_count_or_last_at(client):
    issue = heal_service.report("turn_error", "first", data={"error": "first"})
    assert issue["count"] == 1
    first_last_at = issue["last_at"]
    bumped = heal_service.report("turn_error", "second", agent_id=issue.get("agent_id"), data={"error": "second"}, bump=False)
    assert bumped["id"] == issue["id"]
    assert bumped["count"] == 1  # not incremented
    assert bumped["last_at"] == first_last_at  # not refreshed


def test_email_failed_keeps_separate_issues_per_recipient(client):
    """Two failed sends to different recipients must not collapse into one issue whose data
    only remembers the latest — else heal resends just the last email and marks both fixed."""
    first = heal_service.report("email_failed", "To a@x.com: hi -> failed", data={"to": "a@x.com", "body": "hi"})
    second = heal_service.report("email_failed", "To b@x.com: hi -> failed", data={"to": "b@x.com", "body": "hi"})
    assert first["id"] != second["id"]
    assert first["data"]["to"] == "a@x.com" and second["data"]["to"] == "b@x.com"
    # A third failure for the same recipient still bumps the existing issue rather than duplicating it.
    third = heal_service.report("email_failed", "To a@x.com: hi -> failed again", data={"to": "a@x.com", "body": "hi"})
    assert third["id"] == first["id"] and third["count"] == 2


def test_heal_does_not_clobber_a_report_that_lands_during_a_remedy(client, monkeypatch):
    """A remedy that reports a fresh issue mid-run must survive heal()'s own final write (see heal_service.heal)."""
    issue = heal_service.report("turn_error", "slow one", data={"error": "slow"})

    from app.services import heal_service as hs
    real_heal_one = hs._heal_one

    def fake_heal_one(it):
        if it["id"] == issue["id"]:
            hs.report("scheduler_error", "landed mid-remedy", data={"job": "x"})
        return real_heal_one(it)

    monkeypatch.setattr(hs, "_heal_one", fake_heal_one)
    hs.heal(issue["id"])
    assert any(i["kind"] == "scheduler_error" and i["detail"] == "landed mid-remedy" for i in hs.list_issues())


def test_summary_heal_closes_silent_calls_and_reports_the_rest(client, base, monkeypatch):
    from app.core.database import get_db
    from app.models.call import Call
    from app.services import llm
    agent_id = int(base.rsplit("/", 1)[1])
    with get_db() as db:
        db.add(Call(agent_id=agent_id, direction="inbound", trigger="inbound", status="Completed", session_id="s-90", from_number="+919000000090",
                    to_number="+918000000000", transcript='[{"role":"assistant","text":"Hello?"}]', error="summary_pending:3 x"))
        db.add(Call(agent_id=agent_id, direction="inbound", trigger="inbound", status="Completed", session_id="s-91", from_number="+919000000091",
                    to_number="+918000000000", transcript='[{"role":"customer","text":"hi"}]', error="summary_pending:3 402 credits"))
    monkeypatch.setattr(llm, "complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("402 no credits")))
    issue = next(i for i in heal_service.detect() if i["kind"] == "summary_pending")
    result = heal_service.heal(issue["id"])[0]
    assert result["ok"] is False and "402" in result["note"]
    with get_db() as db:
        silent = db.scalar(__import__("sqlalchemy").select(Call).where(Call.from_number == "+919000000090"))
        assert silent.summary.startswith("No conversation")


def test_disconnected_inbound_number_is_detected_and_healed(client, monkeypatch):
    from app.core import store
    from app.services import plivo_service
    state = {"connected": False}
    class FakePlivo:
        def __init__(self, *a, **k): pass
        def inbound_status(self, number=None):
            return {"number": "+918000000000", "connected": state["connected"], "app_name": "agent_flow_old", "app_id": "a1", "previous_app": None}
        def connect_inbound(self, number=None):
            state["connected"] = True
            return self.inbound_status(number)
    monkeypatch.setattr(plivo_service, "PlivoService", FakePlivo)
    store.delete(heal_service.INBOUND_CHECK_KEY)
    issue = next(i for i in heal_service.detect() if i["kind"] == "inbound_disconnected")
    assert "agent_flow_old" in issue["detail"] and issue["healable"]
    result = heal_service.heal(issue["id"])[0]
    assert result["ok"] and "now sends incoming calls" in result["note"]
    assert not [i for i in heal_service.detect() if i["kind"] == "inbound_disconnected" and i["status"] == "open"]
