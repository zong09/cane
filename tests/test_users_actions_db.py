"""หน้า ผู้ใช้ (ใบ 20 · PR ที่สอง) — เชิญ เปลี่ยน role ระงับ ปลดระงับ ปลดล็อก reset 2FA

ทุก action ขอ step-up ในคำขอนั้นเอง · ด่านที่ไม่ต้องใช้รหัส (อีเมลซ้ำ, ระงับตัวเอง,
OWNER คนสุดท้าย, ADMIN แตะ OWNER) ต้องปฏิเสธ**ก่อน**ใช้รหัส ไม่งั้นคนกดเสียรหัสรอบนั้นฟรี
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select

from cane.api.app import create_app
from cane.api.deps import signed_in
from cane.auth import secrets as auth_secrets
from cane.db.repo import login_attempts
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import auth_tokens, user_audit_log
from cane.db.types import now_ms

sys.path.insert(0, str(Path(__file__).parent))
from test_console_web_db import BoundDb, FakeProcess, client, owner, right_now_code  # noqa: E402,F401
from test_users_page_db import a_user  # noqa: E402

pytestmark = pytest.mark.db

WRONG = {"step_up_code": "000000"}


def acting_as(db: Connection, user_id: int) -> TestClient:
    token = auth_secrets.new_token()
    sessions_repo.create(db, user_id=user_id, token=token, now=now_ms())
    app = create_app(db=BoundDb(db), spawn=lambda profile: FakeProcess(1))
    app.dependency_overrides[signed_in] = lambda: sessions_repo.lookup(db, token, now=now_ms())
    return TestClient(app)


def audit_rows(db: Connection, action: str):
    return db.execute(select(user_audit_log).where(user_audit_log.c.action == action)).all()


def counter(db: Connection, user_id: int) -> int | None:
    return users_repo.by_id(db, user_id).totp_last_counter


def enrol_link(text: str) -> str:
    match = re.search(r"/enrol/([A-Za-z0-9_\-]+)", text)
    assert match, "ต้องแสดงลิงก์ /enrol/… ให้เห็นครั้งเดียว"
    return match.group(0)


# ── เชิญ ──────────────────────────────────────────────────────────────────────


def test_inviting_creates_a_pending_account_named_after_the_email_and_shows_the_link_once(
    db: Connection, client: TestClient
) -> None:
    with client:
        response = client.post("/api/users", data={"email": "Nok@Example.com", "role": "TRADER", **right_now_code()})
        link = enrol_link(response.text)
        enrol = client.get(link)

    assert response.status_code == 200
    invited = users_repo.by_email(db, "nok@example.com")
    assert (invited.name, invited.role, invited.status) == ("Nok", "TRADER", "pending")
    assert enrol.status_code == 200  # ลิงก์ใช้ได้จริง และยังให้ตั้งรหัสผ่าน
    assert 'name="password"' in enrol.text
    [row] = audit_rows(db, "user.invite")
    assert row.step_up_verified is True and row.target == "nok@example.com"


def test_a_wrong_code_invites_nobody(db: Connection, client: TestClient) -> None:
    with client:
        response = client.post("/api/users", data={"email": "nok@example.com", "role": "TRADER", **WRONG})

    assert response.status_code == 403
    assert users_repo.by_email(db, "nok@example.com") is None
    assert "รหัส 6 หลักไม่ถูกต้อง" in response.text


def test_an_email_that_already_has_an_account_is_refused_before_the_code_is_spent(
    db: Connection, client: TestClient, owner
) -> None:
    _, me = owner
    with client:
        response = client.post("/api/users", data={"email": "owner@example.com", "role": "VIEWER", **right_now_code()})

    assert "อีเมลนี้มีบัญชีอยู่แล้ว" in response.text
    assert counter(db, me.id) is None


def test_a_malformed_email_is_refused(db: Connection, client: TestClient) -> None:
    with client:
        response = client.post("/api/users", data={"email": "not-an-email", "role": "VIEWER", **right_now_code()})

    assert "รูปแบบอีเมลไม่ถูกต้อง" in response.text
    assert len(users_repo.everyone(db)) == 1


# ── เฉพาะ OWNER แตะ OWNER ─────────────────────────────────────────────────────


def test_an_admin_cannot_invite_an_owner(db: Connection, owner) -> None:
    admin = a_user(db, "admin@example.com", role="ADMIN")
    with acting_as(db, admin) as c:
        response = c.post("/api/users", data={"email": "alt@example.com", "role": "OWNER", **right_now_code()})

    assert "เฉพาะ OWNER" in response.text
    assert users_repo.by_email(db, "alt@example.com") is None
    assert counter(db, admin) is None


def test_an_admin_cannot_suspend_an_owner_or_promote_anyone_to_owner(db: Connection, owner) -> None:
    _, me = owner
    admin = a_user(db, "admin@example.com", role="ADMIN")
    trader = a_user(db, "trader@example.com", role="TRADER")
    with acting_as(db, admin) as c:
        suspend = c.post(f"/api/users/{me.id}/suspend", data=right_now_code())
        promote = c.post(f"/api/users/{trader}/role", data={"role": "OWNER", **right_now_code()})

    assert "เฉพาะ OWNER" in suspend.text and "เฉพาะ OWNER" in promote.text
    assert users_repo.by_id(db, me.id).status == "active"
    assert users_repo.by_id(db, trader).role == "TRADER"


# ── เปลี่ยน role ──────────────────────────────────────────────────────────────


def test_a_role_change_reaches_the_open_session_on_its_next_request(db: Connection, client: TestClient) -> None:
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 5"""
    admin = a_user(db, "admin@example.com", role="ADMIN")
    token = auth_secrets.new_token()
    sessions_repo.create(db, user_id=admin, token=token, now=now_ms())

    with client:
        response = client.post(f"/api/users/{admin}/role", data={"role": "VIEWER", **right_now_code()})

    assert response.status_code == 200
    _, seen = sessions_repo.lookup(db, token, now=now_ms())
    assert seen.role == "VIEWER"
    assert not perms.allowed(db, role=seen.role, cap="manage_users")
    [row] = audit_rows(db, "user.role")
    assert row.step_up_verified is True and row.detail == {"from": "ADMIN", "to": "VIEWER"}


def test_the_last_active_owner_cannot_move_out_of_owner(db: Connection, client: TestClient, owner) -> None:
    _, me = owner
    a_user(db, "later@example.com", role="OWNER", enrolled=False)  # OWNER ที่ยังไม่ active ไม่นับ

    with client:
        response = client.post(f"/api/users/{me.id}/role", data={"role": "ADMIN", **right_now_code()})

    assert "OWNER ที่ใช้งานอยู่คนสุดท้าย" in response.text
    assert users_repo.by_id(db, me.id).role == "OWNER"
    assert counter(db, me.id) is None


def test_choosing_the_same_role_changes_nothing(db: Connection, client: TestClient) -> None:
    admin = a_user(db, "admin@example.com", role="ADMIN")
    with client:
        response = client.post(f"/api/users/{admin}/role", data={"role": "ADMIN", **right_now_code()})

    assert "role เดิม" in response.text
    assert audit_rows(db, "user.role") == []


# ── ระงับ / ปลดระงับ ──────────────────────────────────────────────────────────


def test_suspending_cuts_every_open_session_of_that_person(db: Connection, client: TestClient) -> None:
    trader = a_user(db, "trader@example.com", role="TRADER")
    token = auth_secrets.new_token()
    sessions_repo.create(db, user_id=trader, token=token, now=now_ms())

    with client:
        response = client.post(f"/api/users/{trader}/suspend", data=right_now_code())

    assert response.status_code == 200
    assert users_repo.by_id(db, trader).status == "suspended"
    assert sessions_repo.lookup(db, token, now=now_ms()) is None
    assert audit_rows(db, "user.suspend")[0].step_up_verified is True


def test_nobody_can_suspend_themselves(db: Connection, client: TestClient, owner) -> None:
    _, me = owner
    a_user(db, "second@example.com", role="OWNER")  # ไม่ใช่ด่าน OWNER คนสุดท้ายที่กั้น
    with client:
        response = client.post(f"/api/users/{me.id}/suspend", data=right_now_code())

    assert "ระงับตัวเองไม่ได้" in response.text
    assert users_repo.by_id(db, me.id).status == "active"
    assert counter(db, me.id) is None


def test_unsuspending_brings_back_the_same_role(db: Connection, client: TestClient) -> None:
    trader = a_user(db, "trader@example.com", role="TRADER", status="suspended")
    with client:
        response = client.post(f"/api/users/{trader}/unsuspend", data=right_now_code())

    assert response.status_code == 200
    back = users_repo.by_id(db, trader)
    assert (back.status, back.role) == ("active", "TRADER")


def test_unsuspending_someone_who_never_finished_enrolling_leaves_them_pending(db: Connection, client: TestClient) -> None:
    invited = a_user(db, "invited@example.com", role="VIEWER", enrolled=False, status="suspended")
    with client:
        client.post(f"/api/users/{invited}/unsuspend", data=right_now_code())

    assert users_repo.by_id(db, invited).status == "pending"


# ── ปลดล็อก ───────────────────────────────────────────────────────────────────


def test_unlocking_clears_the_lock_and_the_button_only_shows_on_a_locked_row(db: Connection, client: TestClient) -> None:
    trader = a_user(db, "trader@example.com", role="TRADER")
    for _ in range(login_attempts.MAX_FAILURES):
        login_attempts.record(db, email="trader@example.com", ok=False, kind="password", ts=now_ms(), user_id=trader)

    with client:
        before = client.get("/partials/users?tab=people").text
        response = client.post(f"/api/users/{trader}/unlock", data=right_now_code())
        after = client.get("/partials/users?tab=people").text

    assert before.count("ปลดล็อก") == 1
    assert response.status_code == 200
    assert login_attempts.lock_state(db, "trader@example.com").locked_until is None
    assert "ปลดล็อก" not in after


# ── reset 2FA ─────────────────────────────────────────────────────────────────


def test_resetting_2fa_sends_the_account_back_to_pending_with_a_totp_only_link(
    db: Connection, client: TestClient
) -> None:
    trader = a_user(db, "trader@example.com", role="TRADER")
    token = auth_secrets.new_token()
    sessions_repo.create(db, user_id=trader, token=token, now=now_ms())

    with client:
        response = client.post(f"/api/users/{trader}/reset-2fa", data=right_now_code())
        enrol = client.get(enrol_link(response.text))

    back = users_repo.by_id(db, trader)
    assert (back.status, back.totp_enrolled_ts) == ("pending", None)
    assert sessions_repo.lookup(db, token, now=now_ms()) is None
    kinds = db.execute(select(auth_tokens.c.kind).where(auth_tokens.c.user_id == trader)).scalars().all()
    assert kinds == ["reset_2fa"]
    assert enrol.status_code == 200 and 'name="password"' not in enrol.text


def test_nobody_resets_their_own_2fa_from_the_users_page(db: Connection, client: TestClient, owner) -> None:
    _, me = owner
    with client:
        response = client.post(f"/api/users/{me.id}/reset-2fa", data=right_now_code())

    assert "ของตัวเอง" in response.text
    assert users_repo.by_id(db, me.id).status == "active"


# ── สิทธิ์ ────────────────────────────────────────────────────────────────────


def test_a_trader_gets_403_on_every_user_action(db: Connection, owner) -> None:
    _, me = owner
    trader = a_user(db, "trader@example.com", role="TRADER")
    with acting_as(db, trader) as c:
        codes = [
            c.post("/api/users", data={"email": "x@example.com", "role": "VIEWER", **right_now_code()}).status_code,
            c.post(f"/api/users/{me.id}/role", data={"role": "VIEWER", **right_now_code()}).status_code,
            c.post(f"/api/users/{me.id}/suspend", data=right_now_code()).status_code,
            c.post(f"/api/users/{me.id}/unsuspend", data=right_now_code()).status_code,
            c.post(f"/api/users/{me.id}/unlock", data=right_now_code()).status_code,
            c.post(f"/api/users/{me.id}/reset-2fa", data=right_now_code()).status_code,
        ]
    assert codes == [403] * 6
