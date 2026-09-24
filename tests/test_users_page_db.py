"""หน้า ผู้ใช้ (ใบ 20 · PR แรก) — อ่านอย่างเดียว 4 แท็บจากตารางจริง

ยังไม่มี mutation · ปุ่มเชิญ/เปลี่ยน role/ระงับ/ตัด session/แก้สิทธิ์มากับ PR ถัดไป
ไฟล์นี้จึงยืนยันแค่ว่าทุกแท็บอ่านจากที่ที่ spec/09 บอกว่าเป็นความจริง
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection

from cane.api.app import create_app
from cane.api.deps import signed_in
from cane.auth import secrets as auth_secrets
from cane.auth.matrix import DEFAULT_MATRIX
from cane.db.repo import audit
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.types import now_ms

sys.path.insert(0, str(Path(__file__).parent))
from test_console_web_db import BoundDb, FakeProcess, client, owner  # noqa: E402,F401

pytestmark = pytest.mark.db


def a_user(db: Connection, email: str, *, role: str, enrolled: bool = True, status: str | None = None) -> int:
    user_id = users_repo.create(
        db,
        email=email,
        name=email.split("@")[0],
        role=role,
        created_ts=now_ms(),
        password_hash=auth_secrets.hash_password("รหัสผ่านที่ยาวพอ"),
    )
    if enrolled:
        users_repo.enrol_totp(
            db, user_id, secret_enc=auth_secrets.encrypt_secret("JBSWY3DPEHPK3PXP"), enrolled_ts=now_ms()
        )
    if status is not None:
        users_repo.set_status(db, user_id, status)
    return user_id


def test_the_users_page_has_a_body_with_every_account_and_the_four_counts(
    db: Connection, client: TestClient
) -> None:
    a_user(db, "admin@example.com", role="ADMIN")
    a_user(db, "invited@example.com", role="VIEWER", enrolled=False)
    a_user(db, "gone@example.com", role="OWNER", status="suspended")

    with client:
        response = client.get("/users")

    assert response.status_code == 200
    assert "เนื้อหน้านี้เป็นของใบ" not in response.text
    for email in ("owner@example.com", "admin@example.com", "invited@example.com", "gone@example.com"):
        assert email in response.text
    assert "4 บัญชี" in response.text
    # ใช้งานอยู่ 2 · owner ที่ยิงจริงได้ 1 (คนที่ถูกระงับไม่นับ) · รอรับเชิญ 1 · ยังไม่ตั้ง 2FA 1
    assert 'data-kpi="active">2<' in response.text
    assert 'data-kpi="owners">1<' in response.text
    assert 'data-kpi="pending">1<' in response.text
    assert 'data-kpi="no2fa">1<' in response.text


def test_the_permission_tab_reads_the_active_version_not_the_default_matrix(
    db: Connection, client: TestClient
) -> None:
    changed = deepcopy(DEFAULT_MATRIX)
    changed["TRADER"]["export_records"] = True
    perms.activate(db, perms.insert_version(db, changed, created_ts=now_ms()))

    with client:
        response = client.get("/partials/users?tab=perms")

    assert response.status_code == 200
    row = response.text.split('data-cap="export_records"')[1].split("</div>\n  </div>")[0]
    # OWNER ADMIN TRADER VIEWER AUDITOR
    assert row.count('data-allowed="true"') == 4
    assert row.count('data-allowed="false"') == 1


def test_the_session_tab_lists_live_sessions_of_everyone_and_marks_this_one(
    db: Connection, client: TestClient, owner
) -> None:
    admin = a_user(db, "admin@example.com", role="ADMIN")
    now = now_ms()
    sessions_repo.create(db, user_id=admin, token="live", now=now, ip="203.0.113.44", user_agent="Firefox")
    revoked = sessions_repo.create(db, user_id=admin, token="revoked", now=now, ip="198.51.100.9")
    sessions_repo.revoke(db, revoked, now)
    sessions_repo.create(db, user_id=admin, token="expired", now=now - sessions_repo.LIFETIME_MS - 1, ip="192.0.2.7")

    with client:
        response = client.get("/partials/users?tab=sessions")

    assert response.status_code == 200
    assert "203.0.113.44" in response.text and "Firefox" in response.text
    assert "198.51.100.9" not in response.text and "192.0.2.7" not in response.text
    assert response.text.count("data-session=") == 2
    assert response.text.count("เครื่องนี้") == 1
    assert "ที่มา (IP)" in response.text


def test_the_log_tab_names_the_actor_and_puts_the_newest_row_first(
    db: Connection, client: TestClient, owner
) -> None:
    _, me = owner
    audit.record(db, action="login.ok", ts=1_000, actor_user_id=me.id, ip="203.0.113.44")
    audit.record(db, action="engine.stop", ts=2_000, actor_user_id=me.id, target="paper", step_up_verified=True)
    audit.record(db, action="login.stage1_failed", ts=3_000, detail={"email": "who@example.com"})

    with client:
        response = client.get("/partials/users?tab=log")

    assert response.status_code == 200
    text = response.text
    assert text.index("login.stage1_failed") < text.index("engine.stop") < text.index("login.ok")
    assert "เจ้าของ" in text
    assert "1970-01-01 00:00:03" in text


def test_a_viewer_cannot_open_the_users_body(db: Connection, owner) -> None:
    viewer = a_user(db, "viewer@example.com", role="VIEWER")
    token = auth_secrets.new_token()
    sessions_repo.create(db, user_id=viewer, token=token, now=now_ms())
    app = create_app(db=BoundDb(db), spawn=lambda profile: FakeProcess(1))
    app.dependency_overrides[signed_in] = lambda: sessions_repo.lookup(db, token, now=now_ms())

    with TestClient(app) as viewer_client:
        assert viewer_client.get("/partials/users?tab=people").status_code == 403
        assert viewer_client.get("/users").status_code == 403
