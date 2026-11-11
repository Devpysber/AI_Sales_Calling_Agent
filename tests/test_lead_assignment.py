"""Assignable users list and bulk lead assignment."""

from app.core.database import get_db
from app.models.user import Organization, User
from app.services import user_service

PASSWORD = "Pw-1234567890"


def _make_user(email, role, org_id=None):
    org_id = org_id or user_service.default_org()["id"]
    with get_db() as db:
        user = User(organization_id=org_id, email=email, role=role, status="active", name=email.split("@")[0],
                    password_hash=None)
        db.add(user)
        db.flush()
        return user.id


def _lead(client, base, phone):
    res = client.post(f"{base}/leads", json={"phone": phone, "name": phone})
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_assignable_users_lists_active_same_org_users(client, base):
    uid = _make_user("assignable@perm.test", "sales_exec")
    res = client.get(f"{base}/leads/assignable-users")
    assert res.status_code == 200
    assert uid in {u["id"] for u in res.json()}


def test_bulk_assign_to_valid_same_org_user(client, base):
    uid = _make_user("rep-bulk@perm.test", "sales_exec")
    a, b = _lead(client, base, "+919800030001"), _lead(client, base, "+919800030002")
    res = client.post(f"{base}/leads/bulk/assign", json={"ids": [a, b], "user_id": uid})
    assert res.status_code == 200
    assert res.json()["updated"] == 2
    assert client.get(f"{base}/leads/{a}").json()["owner_user_id"] == uid
    assert client.get(f"{base}/leads/{b}").json()["owner_user_id"] == uid


def test_bulk_assign_to_other_org_user_rejected(client, base):
    with get_db() as db:
        org_b = Organization(name="Org C")
        db.add(org_b)
        db.flush()
        org_b_id = org_b.id
    other_org_uid = _make_user("rep-otherorg@perm.test", "sales_exec", org_id=org_b_id)
    lead_id = _lead(client, base, "+919800030003")
    res = client.post(f"{base}/leads/bulk/assign", json={"ids": [lead_id], "user_id": other_org_uid})
    assert res.status_code == 400
    assert client.get(f"{base}/leads/{lead_id}").json()["owner_user_id"] is None
