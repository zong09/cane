"""คอนโซล + auth ต่อกันจริง (spec/09 §10. เกณฑ์ยืนยันความถูกต้อง)

ปิดเกณฑ์ทั้งสี่ข้อที่ใบ 20 เขียนไว้ว่า "เสร็จเมื่อ" — ผ่าน HTTP จริง คุกกี้จริง
และ Postgres จริง · เทสต์ที่จำลองทุกชั้นจะไม่จับข้อผิดพลาดของการต่อสายระหว่างชั้น
ซึ่งเป็นที่ที่ auth พังบ่อยที่สุด
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection

from cane.api.app import create_app
from cane.api.deps import SESSION_COOKIE
from cane.auth import secrets as auth_secrets
from cane.auth import totp
from cane.auth.matrix import DEFAULT_MATRIX
from cane.db.repo import login_attempts
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import AUTH_TABLES
from cane.db.types import now_ms

pytestmark = pytest.mark.db

PASSWORD = "รหัสผ่านที่ยาวพอสมควร"


class _Borrowed:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def __enter__(self) -> Connection:
        return self._conn

    def __exit__(self, *_: object) -> bool:
        return False


class BoundDb:
    """ยืม connection ของ fixture `db` — แอปต้องเห็นของที่เทสต์เพิ่งเขียน"""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def connect(self) -> _Borrowed:
        return _Borrowed(self._conn)

    def begin(self) -> _Borrowed:
        return _Borrowed(self._conn)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def terminate(self) -> None:
        pass


@pytest.fixture(autouse=True)
def clean_auth(db: Connection) -> None:
    for table in AUTH_TABLES:
        db.execute(table.delete())
    perms.activate(db, perms.insert_version(db, DEFAULT_MATRIX, created_ts=now_ms()))


@pytest.fixture
def app(db: Connection):
    return create_app(db=BoundDb(db), spawn=lambda profile: FakeProcess(1))


@pytest.fixture
def secret() -> str:
    return totp.new_secret()


def make_account(db, secret, *, email, role, enrolled=True) -> int:
    user_id = users_repo.create(
        db,
        email=email,
        name="คนทดสอบ",
        role=role,
        created_ts=now_ms(),
        password_hash=auth_secrets.hash_password(PASSWORD),
    )
    if enrolled:
        users_repo.enrol_totp(
            db,
            user_id,
            secret_enc=auth_secrets.encrypt_secret(secret),
            enrolled_ts=now_ms(),
        )
    return user_id


def code_now(secret: str) -> str:
    return totp.code(secret, totp.counter_at(now_ms()))


def next_code(secret: str) -> str:
    """รหัสของช่วงถัดไป · ยังอยู่ในหน้าต่าง ±1 ที่ตัวตรวจรับ

    จำเป็นเมื่อทำ step-up ต่อจาก login ในเทสต์เดียวกัน — รหัสที่เพิ่งใช้ login
    ไปแล้วใช้ซ้ำไม่ได้ ซึ่งถูกต้องตามสเปกและถูกตรึงไว้ที่
    `test_a_code_already_spent_on_login_cannot_be_reused_for_step_up`
    """
    return totp.code(secret, totp.counter_at(now_ms()) + 1)


def log_in(client: TestClient, secret: str, *, email: str) -> None:
    first = client.post("/login", data={"email": email, "password": PASSWORD})
    assert first.status_code == 200, first.text
    ticket = first.text.split('name="ticket" value="')[1].split('"')[0]
    second = client.post("/login/verify", data={"ticket": ticket, "code": code_now(secret)})
    assert second.status_code == 204, second.text


# ── เกณฑ์ข้อ 1: ยังไม่ผูก 2FA เข้าไม่ได้เลย ──────────────────────────────────


def test_an_account_without_two_factor_cannot_reach_the_console_at_all(db, app, secret):
    make_account(db, secret, email="pending@example.com", role="OWNER", enrolled=False)

    with TestClient(app) as client:
        response = client.post(
            "/login", data={"email": "pending@example.com", "password": PASSWORD}
        )

        assert response.status_code == 401
        assert 'name="ticket"' not in response.text
        assert client.cookies.get(SESSION_COOKIE) is None
        assert client.get("/overview", follow_redirects=False).status_code == 303


# ── เกณฑ์ข้อ 2: ล็อกข้ามเครื่อง ──────────────────────────────────────────────


def test_the_lock_survives_a_completely_fresh_client(db, app, secret):
    """ล้างคุกกี้ เปลี่ยนเครื่อง เปิดใหม่ — บัญชียังล็อกอยู่ เพราะล็อกที่บัญชี"""
    make_account(db, secret, email="locked@example.com", role="OWNER")

    with TestClient(app) as first_device:
        for _ in range(login_attempts.MAX_FAILURES):
            first_device.post(
                "/login", data={"email": "locked@example.com", "password": "ผิด"}
            )

    with TestClient(app) as another_device:
        response = another_device.post(
            "/login", data={"email": "locked@example.com", "password": PASSWORD}
        )

    assert response.status_code == 401
    assert "ล็อก" in response.text


# ── เกณฑ์ข้อ 3: VIEWER คุม engine ไม่ได้ ─────────────────────────────────────


def test_a_viewer_gets_403_from_the_engine_control_endpoint(db, app, secret):
    """สิทธิ์จริงจากตาราง ไม่ใช่ตัวปลอม — `engine_control` ไม่มีให้ VIEWER"""
    make_account(db, secret, email="viewer@example.com", role="VIEWER")

    with TestClient(app) as client:
        log_in(client, secret, email="viewer@example.com")
        response = client.post(
            "/api/paper/engine/start", data={"step_up_code": code_now(secret)}
        )

    assert response.status_code == 403


def test_an_admin_with_a_valid_code_may_control_the_engine(db, app, secret):
    make_account(db, secret, email="admin@example.com", role="ADMIN")

    with TestClient(app) as client:
        log_in(client, secret, email="admin@example.com")
        response = client.post(
            "/api/paper/engine/start", data={"step_up_code": next_code(secret)}
        )

    assert response.status_code == 200
    assert "engine paper" in response.text


def test_a_code_already_spent_on_login_cannot_be_reused_for_step_up(db, app, secret):
    """ช่วงเวลาเดียวกันใช้ได้ครั้งเดียวทั้งระบบ ไม่ใช่ครั้งเดียวต่อจุดที่ใช้

    ถ้าแยกตัวนับของ login กับของ step-up คนที่แอบเห็นรหัสตอนคนอื่น login จะเอาไป
    ยืนยัน action ที่ต้อง step-up ได้ในอีก 30 วินาทีถัดมา
    """
    make_account(db, secret, email="admin@example.com", role="ADMIN")

    with TestClient(app) as client:
        used = code_now(secret)
        log_in(client, secret, email="admin@example.com")
        response = client.post("/api/paper/engine/start", data={"step_up_code": used})

    assert response.status_code == 403


def test_an_admin_without_a_code_is_still_refused(db, app, secret):
    make_account(db, secret, email="admin@example.com", role="ADMIN")

    with TestClient(app) as client:
        log_in(client, secret, email="admin@example.com")
        response = client.post("/api/paper/engine/start")

    assert response.status_code == 403


# ── เกณฑ์ข้อ 4: ตัด session แล้ว request ถัดไปเด้งออก ────────────────────────


def test_revoking_a_session_bounces_the_very_next_request(db, app, secret):
    user_id = make_account(db, secret, email="owner@example.com", role="OWNER")

    with TestClient(app) as client:
        log_in(client, secret, email="owner@example.com")
        assert client.get("/overview").status_code == 200

        sessions_repo.revoke_all_for_user(db, user_id, now_ms())

        assert client.get("/overview", follow_redirects=False).status_code == 303


def test_suspending_a_user_bounces_them_without_touching_their_cookie(db, app, secret):
    make_account(db, secret, email="owner@example.com", role="OWNER")
    user_id = make_account(db, secret, email="admin@example.com", role="ADMIN")

    with TestClient(app) as client:
        log_in(client, secret, email="admin@example.com")
        assert client.get("/overview").status_code == 200

        users_repo.set_status(db, user_id, "suspended")

        assert client.get("/overview", follow_redirects=False).status_code == 303


def test_a_role_change_lands_on_the_next_request_without_a_new_login(db, app, secret):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 5 — เพราะ role ไม่เคยอยู่ในคุกกี้"""
    make_account(db, secret, email="owner@example.com", role="OWNER")
    user_id = make_account(db, secret, email="who@example.com", role="ADMIN")

    with TestClient(app) as client:
        log_in(client, secret, email="who@example.com")
        assert client.get("/users").status_code == 200

        users_repo.set_role(db, user_id, "VIEWER")

        assert client.get("/users").status_code == 403


def test_logging_out_ends_the_session(db, app, secret):
    make_account(db, secret, email="owner@example.com", role="OWNER")

    with TestClient(app) as client:
        log_in(client, secret, email="owner@example.com")
        assert client.post("/logout").status_code == 204
        assert client.get("/overview", follow_redirects=False).status_code == 303


# ── ผูก 2FA ผ่านลิงก์ ────────────────────────────────────────────────────────


def test_the_invite_link_is_the_only_way_a_pending_owner_becomes_usable(db, app):
    """เส้นทางตั้งเครื่องครั้งแรกทั้งเส้น — CLI สร้างบัญชี ลิงก์เปิดใช้ แล้วจึง login ได้"""
    from cane.db.repo import auth_tokens

    user_id = users_repo.create(
        db,
        email="first@example.com",
        name="เจ้าของคนแรก",
        role="OWNER",
        created_ts=now_ms(),
        password_hash=auth_secrets.hash_password(PASSWORD),
    )
    token = auth_secrets.new_token()
    auth_tokens.issue(db, user_id=user_id, kind="invite", token=token, now=now_ms())

    with TestClient(app) as client:
        page = client.get(f"/enrol/{token}")
        assert page.status_code == 200
        fresh_secret = page.text.split('name="secret" value="')[1].split('"')[0]

        done = client.post(
            f"/enrol/{token}",
            data={"secret": fresh_secret, "code": code_now(fresh_secret)},
        )
        assert done.status_code == 200
        assert "backup code" in done.text

        assert users_repo.by_id(db, user_id).status == "active"
        log_in(client, fresh_secret, email="first@example.com")
        assert client.get("/overview").status_code == 200

        # ลิงก์ใช้ได้ครั้งเดียว
        assert client.get(f"/enrol/{token}").status_code == 404
