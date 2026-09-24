"""หน้า ผู้ใช้ (ใบ 20 · PR ที่สาม) — ตัด session · แก้ตารางสิทธิ์

spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 4 (ตัดแล้ว request ถัดไปเด้งออก) และข้อ 8
(diff ที่แตะคอลัมน์ OWNER ถูกปฏิเสธที่ endpoint ไม่ใช่แค่ปุ่มปิดใน UI)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select

from cane.auth import secrets as auth_secrets
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import permission_versions
from cane.db.types import now_ms

sys.path.insert(0, str(Path(__file__).parent))
from test_console_web_db import BoundDb, FakeProcess, client, owner, right_now_code  # noqa: E402,F401
from test_users_actions_db import acting_as, audit_rows, counter  # noqa: E402
from test_users_page_db import a_user  # noqa: E402

pytestmark = pytest.mark.db


def open_session(db: Connection, user_id: int) -> tuple[int, str]:
    token = auth_secrets.new_token()
    return sessions_repo.create(db, user_id=user_id, token=token, now=now_ms(), ip="203.0.113.44"), token


def active_id(db: Connection) -> int:
    return perms.active_version(db).id


# ── ตัด session ───────────────────────────────────────────────────────────────


def test_revoking_a_session_bounces_it_on_its_next_request(db: Connection, client: TestClient) -> None:
    admin = a_user(db, "admin@example.com", role="ADMIN")
    session_id, token = open_session(db, admin)

    with client:
        response = client.request("DELETE", f"/api/sessions/{session_id}", data=right_now_code())

    assert response.status_code == 200
    assert sessions_repo.lookup(db, token, now=now_ms()) is None
    [row] = audit_rows(db, "session.revoke")
    assert row.step_up_verified is True and row.target == "admin@example.com"
    assert f'data-session="{session_id}"' not in response.text


def test_nobody_revokes_their_own_session_from_the_sessions_tab(db: Connection, client: TestClient, owner) -> None:
    mine, me = owner
    with client:
        response = client.request("DELETE", f"/api/sessions/{mine.id}", data=right_now_code())

    assert "ของตัวเอง" in response.text
    assert counter(db, me.id) is None
    assert sessions_repo.live_for_user(db, me.id, now=now_ms())


def test_an_admin_cannot_revoke_an_owners_session(db: Connection, owner) -> None:
    mine, _ = owner
    admin = a_user(db, "admin@example.com", role="ADMIN")
    with acting_as(db, admin) as c:
        response = c.request("DELETE", f"/api/sessions/{mine.id}", data=right_now_code())
        tab = c.get("/partials/users?tab=sessions").text

    assert "เฉพาะ OWNER" in response.text
    assert sessions_repo.by_id(db, mine.id).revoked_ts is None
    # ปุ่ม `ตัดออก` ไม่ขึ้นที่แถวของ OWNER และแถวของตัวเอง — เหลือศูนย์ปุ่ม
    assert ">ตัดออก</button>" not in tab


def test_a_session_that_is_already_gone_is_refused_before_the_code_is_spent(
    db: Connection, client: TestClient, owner
) -> None:
    _, me = owner
    admin = a_user(db, "admin@example.com", role="ADMIN")
    session_id, _ = open_session(db, admin)
    sessions_repo.revoke(db, session_id, now_ms())

    with client:
        response = client.request("DELETE", f"/api/sessions/{session_id}", data=right_now_code())

    assert "ไม่ได้เปิดอยู่" in response.text
    assert counter(db, me.id) is None


# ── ตารางสิทธิ์ ───────────────────────────────────────────────────────────────


def test_saving_the_matrix_writes_a_new_active_version_and_reaches_open_sessions(
    db: Connection, client: TestClient
) -> None:
    before = active_id(db)
    auditor = a_user(db, "audit@example.com", role="AUDITOR")
    _, token = open_session(db, auditor)

    with client:
        response = client.post(
            "/api/users/permissions",
            data={"base": before, "flip": ["AUDITOR:export_records", "TRADER:engine_control"], **right_now_code()},
        )

    assert response.status_code == 200
    after = active_id(db)
    assert after != before
    matrix = perms.matrix_of(db, after)
    assert matrix["AUDITOR"]["export_records"] is False and matrix["TRADER"]["engine_control"] is True
    # เวอร์ชันเดิมยังอ่านกลับได้ (decisions.md ข้อ 18) — ไม่ถูกเขียนทับ
    assert perms.matrix_of(db, before)["AUDITOR"]["export_records"] is True
    _, seen = sessions_repo.lookup(db, token, now=now_ms())
    assert not perms.allowed(db, role=seen.role, cap="export_records")
    [row] = audit_rows(db, "permissions.save")
    assert row.step_up_verified is True
    assert sorted(row.detail["changes"]) == ["AUDITOR:export_records:off", "TRADER:engine_control:on"]


def test_a_diff_that_touches_the_owner_column_is_refused_at_the_endpoint(
    db: Connection, client: TestClient, owner
) -> None:
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 8 — ยิงตรงโดยไม่ผ่าน UI"""
    _, me = owner
    before = active_id(db)
    with client:
        response = client.post(
            "/api/users/permissions", data={"base": before, "flip": ["OWNER:edit_profile"], **right_now_code()}
        )

    assert "คอลัมน์ OWNER" in response.text
    assert active_id(db) == before
    assert counter(db, me.id) is None


def test_only_an_owner_edits_the_matrix(db: Connection, owner) -> None:
    admin = a_user(db, "admin@example.com", role="ADMIN")
    before = active_id(db)
    with acting_as(db, admin) as c:
        response = c.post(
            "/api/users/permissions", data={"base": before, "flip": ["ADMIN:toggle_dry_run"], **right_now_code()}
        )
        tab = c.get("/partials/users?tab=perms").text

    assert "OWNER เท่านั้น" in response.text
    assert active_id(db) == before
    assert "แก้ไขสิทธิ์" not in tab


def test_a_draft_made_on_an_older_version_is_refused(db: Connection, client: TestClient) -> None:
    stale = active_id(db)
    perms.activate(db, perms.insert_version(db, perms.matrix_of(db, stale), created_ts=now_ms()))

    with client:
        response = client.post(
            "/api/users/permissions", data={"base": stale, "flip": ["TRADER:engine_control"], **right_now_code()}
        )

    assert "ถูกแก้ไปแล้ว" in response.text
    assert perms.matrix_of(db, active_id(db))["TRADER"]["engine_control"] is False


def test_saving_with_no_change_is_refused(db: Connection, client: TestClient) -> None:
    before = active_id(db)
    with client:
        response = client.post("/api/users/permissions", data={"base": before, **right_now_code()})

    assert "ยังไม่มีการเปลี่ยน" in response.text
    assert db.execute(select(permission_versions.c.id)).scalars().all() == [before]


def test_the_draft_marks_changed_cells_and_counts_them(db: Connection, client: TestClient) -> None:
    with client:
        response = client.get("/partials/users?tab=perms&edit=1&flip=ADMIN:export_records&flip=VIEWER:read_decisions")

    assert response.text.count('data-changed="true"') == 2
    assert "เปลี่ยน 2 ช่อง" in response.text
    assert "บันทึกและยืนยัน 2FA" in response.text


def test_a_matrix_saved_by_someone_else_while_the_code_is_checked_wins(
    db: Connection, client: TestClient, monkeypatch
) -> None:
    """ตัวหลังต้องเจอ `base` ไม่ตรงในทรานแซกชันที่ล็อกเวอร์ชันแล้ว ไม่ใช่ทับของอีกคนเงียบๆ"""
    from cane.api import user_actions

    base = active_id(db)
    real = user_actions.service.verify_step_up

    def verify_then_race(conn, user, code, *, now):
        ok = real(conn, user, code, now=now)
        other = perms.matrix_of(db, base)
        other["VIEWER"]["read_decisions"] = False
        perms.activate(db, perms.insert_version(db, other, created_ts=now_ms()))
        return ok

    monkeypatch.setattr(user_actions.service, "verify_step_up", verify_then_race)
    with client:
        response = client.post(
            "/api/users/permissions", data={"base": base, "flip": ["TRADER:engine_control"], **right_now_code()}
        )

    assert "ถูกแก้ไปแล้ว" in response.text
    latest = perms.matrix_of(db, active_id(db))
    assert latest["VIEWER"]["read_decisions"] is False and latest["TRADER"]["engine_control"] is False
    [refused] = audit_rows(db, "permissions.save_refused")
    assert refused.step_up_verified is True


def test_the_console_role_holds_the_grants_every_user_action_needs(db: Connection, client: TestClient) -> None:
    """เทสต์อื่นรันด้วย login user · คอนโซลจริงรันด้วย `cane_console` ที่ได้ GRANT แคบกว่า —
    `FOR UPDATE` ต้องมีสิทธิ์ UPDATE อย่างน้อยหนึ่งคอลัมน์ และการบันทึกตารางต้อง INSERT สองตาราง
    กับ UPDATE `is_active` · ข้อนี้พิสูจน์ว่าเส้นทาง route → repo → ตาราง ผ่านด้วย role จริง"""
    from sqlalchemy import text

    admin = a_user(db, "admin@example.com", role="ADMIN")
    trader = a_user(db, "trader@example.com", role="TRADER")
    session_id, token = open_session(db, admin)
    base = active_id(db)

    with db.begin_nested():
        db.execute(text('SET LOCAL ROLE "cane_console"'))
        assert db.execute(text("SELECT current_user")).scalar_one() == "cane_console"
        with client:
            saved = client.post(
                "/api/users/permissions", data={"base": base, "flip": ["TRADER:engine_control"], **right_now_code()}
            )
            users_repo.set_totp_counter(db, users_repo.by_email(db, "owner@example.com").id, 0)
            cut = client.request("DELETE", f"/api/sessions/{session_id}", data=right_now_code())
            users_repo.set_totp_counter(db, users_repo.by_email(db, "owner@example.com").id, 0)
            moved = client.post(f"/api/users/{trader}/role", data={"role": "VIEWER", **right_now_code()})

    assert (saved.status_code, cut.status_code, moved.status_code) == (200, 200, 200)
    assert active_id(db) != base
    assert sessions_repo.lookup(db, token, now=now_ms()) is None
    assert users_repo.by_id(db, trader).role == "VIEWER"
