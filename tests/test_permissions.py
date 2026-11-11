"""Users, roles, permissions and organisation isolation."""

import hashlib
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core import auth
from app.core.database import get_db
from app.core.permissions import PERMISSIONS, can, make_principal, require
from app.main import app
from app.models.user import Organization, User
from app.services import agents, user_service
from app.services.crm_service import CRMService
from app.services.settings_service import SettingsService

PASSWORD = "Pw-1234567890"
DENIED = "You don't have permission to do this."


@pytest.fixture(autouse=True)
def no_login_throttle(monkeypatch):
    """These tests sign in as many users from one address; the 10-per-15-minutes limit is covered in test_flow."""
    monkeypatch.setattr(auth.store, "rate_limited", lambda *a, **k: False)


def _make_user(email, role, org_id=None, legacy=None):
    org_id = org_id or user_service.default_org()["id"]
    with get_db() as db:
        user = User(organization_id=org_id, email=email, role=role, status="active", name=email.split("@")[0],
                    password_hash=auth._hash_password(PASSWORD), legacy_team_id=legacy)
        db.add(user)
        db.flush()
        return user.id


def _login(email, password=PASSWORD):
    c = TestClient(app)
    res = c.post("/api/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return c


def _lead(client, base, phone):
    res = client.post(f"{base}/leads", json={"phone": phone, "name": phone})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _legacy_hash(password, salt="abc123"):
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 240_000).hex()}"


def _req(role, user="team"):
    return SimpleNamespace(state=SimpleNamespace(principal=make_principal(1, 1, role), user=user))


def test_bootstrap_adopts_admin_and_team_members_and_is_idempotent(client):
    members = [{"id": f"m-boot-{i}", "name": f"Rep {i}", "email": f"rep{i}@boot.test", "password_hash": _legacy_hash("x" * 10),
                "phone": ""} for i in (1, 2)]
    state = SettingsService()
    before = state.get_state("team_members")
    state.set_state("team_members", members)
    try:
        user_service.ensure_bootstrap()
        user_service.ensure_bootstrap()
        with get_db() as db:
            rows = db.scalars(select(User).where(User.legacy_team_id.like("m-boot-%")).order_by(User.id)).all()
            owners = db.scalars(select(User).where(User.role == "owner")).all()
            orgs = db.scalars(select(Organization)).all()
        assert [(r.email, r.role) for r in rows] == [("rep1@boot.test", "sales_exec"), ("rep2@boot.test", "sales_exec")]
        assert len(owners) == 1 and len(orgs) >= 1 and owners[0].organization_id == rows[0].organization_id
        assert rows[0].password_hash == members[0]["password_hash"]
    finally:
        state.set_state("team_members", before or [])
        with get_db() as db:
            db.execute(delete(User).where(User.legacy_team_id.like("m-boot-%")))


def test_both_legacy_hash_formats_verify():
    assert user_service._check_hash(auth._hash_password("secret-Pass1"), "secret-Pass1")
    assert not user_service._check_hash(auth._hash_password("secret-Pass1"), "nope")
    assert user_service._check_hash(_legacy_hash("secret-Pass1"), "secret-Pass1")
    assert not user_service._check_hash(_legacy_hash("secret-Pass1"), "nope")
    assert not user_service._check_hash("garbage", "x")


def test_login_through_users_table_sets_uid_and_me_reports_role(client):
    uid = _make_user("viewer-me@perm.test", "viewer")
    c = _login("Viewer-Me@perm.test")
    payload = auth.read_token(c.cookies.get(auth.COOKIE).strip('"'))
    assert payload["uid"] == uid and payload["u"] == "team" and payload["org"]
    me = c.get("/api/auth/me").json()
    assert me["role_key"] == "viewer" and me["permissions"] == sorted(PERMISSIONS["viewer"])
    assert me["organization"]["id"] == payload["org"]
    assert TestClient(app).post("/api/auth/login", json={"email": "viewer-me@perm.test", "password": "wrong"}).status_code == 401


def test_admin_me_has_role_key_and_permissions(client):
    me = client.get("/api/auth/me").json()
    assert me["role_key"] == "owner" and "billing.manage" in me["permissions"] and me["role"]
    assert me["organization"]["name"]


def test_legacy_cookie_without_uid_still_works(client):
    c = TestClient(app)
    c.cookies.set(auth.COOKIE, auth.make_token({"u": "admin", "exp": int(time.time()) + 600}))
    assert c.get("/api/agents").status_code == 200
    assert c.get("/api/auth/me").json()["role_key"] == "owner"


def test_viewer_reads_but_cannot_write(client, base):
    lead_id = _lead(client, base, "+919800010001")
    _make_user("viewer-rw@perm.test", "viewer")
    c = _login("viewer-rw@perm.test")
    assert c.get(f"{base}/leads/{lead_id}").status_code == 200
    assert c.patch(f"{base}/leads/{lead_id}", json={"notes": "hi"}).status_code == 403
    assert c.post(f"{base}/leads", json={"phone": "+919800010002"}).status_code == 403
    assert c.post("/api/auth/logout").status_code == 200  # /api/auth/* stays open


def test_sales_exec_sees_only_assigned_leads(client, base):
    agent_id = int(base.rsplit("/", 1)[1])
    uid = _make_user("rep-own@perm.test", "sales_exec")
    other = _make_user("rep-other@perm.test", "sales_exec")
    mine, theirs, unassigned = (_lead(client, base, p) for p in ("+919800020001", "+919800020002", "+919800020003"))
    assert client.post(f"{base}/leads/{mine}/assign", json={"user_id": uid}).status_code == 200
    assert client.post(f"{base}/leads/{theirs}/assign", json={"user_id": other}).status_code == 200
    assert client.get(f"{base}/leads/{mine}").json()["owner_user_id"] == uid

    rep = _login("rep-own@perm.test")
    seen = {l["id"] for l in rep.get(f"{base}/leads?page_size=100").json()["items"]}
    assert mine in seen and unassigned in seen and theirs not in seen  # unassigned leads stay shared
    assert rep.get(f"{base}/leads/{mine}").status_code == 200
    assert rep.get(f"{base}/leads/{theirs}").status_code == 404
    assert rep.get(f"{base}/leads/{unassigned}").status_code == 200
    assert rep.patch(f"{base}/leads/{theirs}", json={"notes": "x"}).status_code == 404
    assert rep.post(f"{base}/leads/{mine}/assign", json={"user_id": other}).status_code == 403
    made = rep.post(f"{base}/leads", json={"phone": "+919800020004"}).json()
    assert made["owner_user_id"] == uid  # a rep's own new lead stays visible to them
    assert CRMService(agent_id).get(theirs)["owner_user_id"] == other  # an unscoped service still sees it


def test_sales_exec_cannot_see_the_team_leaderboard(client, base):
    """The leaderboard names every rep individually, unlike the aggregate /analytics routes - a
    sales_exec (no leads.view_all, scoped to their own leads) must not see colleagues' numbers."""
    _make_user("rep-leaderboard@perm.test", "sales_exec")
    _make_user("mgr-leaderboard@perm.test", "manager")
    rep = _login("rep-leaderboard@perm.test")
    mgr = _login("mgr-leaderboard@perm.test")
    assert rep.get(f"{base}/analytics/leaderboard").status_code == 403
    assert mgr.get(f"{base}/analytics/leaderboard").status_code == 200
    assert client.get(f"{base}/analytics/leaderboard").status_code == 200  # admin/owner session


def test_owner_of_another_organisation_cannot_reach_this_agent(client, base):
    agent_id = int(base.rsplit("/", 1)[1])
    with get_db() as db:
        org_b = Organization(name="Org B")
        db.add(org_b)
        db.flush()
        org_b_id = org_b.id
    _make_user("owner-b@perm.test", "owner", org_id=org_b_id)
    theirs = agents.create({"name": "Org B only agent"}, organization_id=org_b_id)

    b = _login("owner-b@perm.test")
    assert b.get(f"/api/agents/{agent_id}").status_code == 404
    assert b.get(f"{base}/leads").status_code == 404
    assert b.get(f"/api/agents/{theirs['id']}").status_code == 200
    assert {a["organization_id"] for a in b.get("/api/agents").json()["agents"]} <= {org_b_id, None}
    assert agent_id not in [a["id"] for a in b.get("/api/agents").json()["agents"]]
    assert theirs["id"] not in [a["id"] for a in client.get("/api/agents").json()["agents"]]
    assert client.get(f"/api/agents/{theirs['id']}").status_code == 404
    made = b.post("/api/agents", json={"name": "Org B second"}).json()
    assert made["organization_id"] == org_b_id


def test_require_denies_a_missing_permission():
    assert not can(_req("viewer"), "leads.edit") and can(_req("owner"), "billing.manage")
    assert "billing.manage" not in PERMISSIONS["admin"] and "leads.view_all" not in PERMISSIONS["sales_exec"]
    with pytest.raises(HTTPException) as err:
        require("automation.manage")(_req("sales_exec"))
    assert err.value.status_code == 403 and err.value.detail == DENIED
    require("automation.manage", legacy_team=True)(_req("sales_exec"))   # a Sales Team Account keeps today's access
    require("leads.edit")(_req("sales_exec"))
    with pytest.raises(HTTPException):
        require("leads.edit")(SimpleNamespace(state=SimpleNamespace()))   # no principal, no access


def test_removed_team_member_session_is_rejected(client):
    member = {"id": "m-gone", "name": "Gone", "email": "gone@perm.test", "password_hash": _legacy_hash(PASSWORD)}
    state = SettingsService()
    before = state.get_state("team_members") or []
    state.set_state("team_members", [*before, member])
    try:
        c = _login("gone@perm.test")   # signs in through the users row, password from the team record
        assert c.get("/api/auth/me").json()["user"] == "team"
        state.set_state("team_members", before)
        assert c.get("/api/auth/me").status_code == 401
        assert TestClient(app).post("/api/auth/login", json={"email": "gone@perm.test", "password": PASSWORD}).status_code == 401
    finally:
        state.set_state("team_members", before)
