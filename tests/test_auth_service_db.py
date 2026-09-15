"""login สองขั้น การล็อก และ step-up (spec/09 §5. login สองขั้น)

ปิดเกณฑ์ของ spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 1, 2 และ 6 ที่ชั้นที่ตัดสินจริง
— ส่วนที่เป็น HTTP อยู่ที่ `test_console_auth.py` ของใบเดียวกัน
"""

from __future__ import annotations

import pytest

from cane.auth import secrets as auth_secrets
from cane.auth import service, totp
from cane.db.repo import backup_codes, login_attempts
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import AUTH_TABLES

pytestmark = pytest.mark.db

NOW = 1_789_000_000_000
PASSWORD = "รหัสผ่านที่ยาวพอสมควรจริงๆ"
EMAIL = "trader@example.com"


@pytest.fixture(autouse=True)
def clean_auth(db):
    for table in AUTH_TABLES:
        db.execute(table.delete())


@pytest.fixture
def secret() -> str:
    return totp.new_secret()


@pytest.fixture
def account(db, secret):
    """บัญชีที่ผูก 2FA เรียบร้อยแล้ว พร้อม backup code หนึ่งชุด"""
    user_id = users_repo.create(
        db,
        email=EMAIL,
        name="คนเทรด",
        role="TRADER",
        created_ts=NOW,
        password_hash=auth_secrets.hash_password(PASSWORD),
    )
    users_repo.enrol_totp(
        db,
        user_id,
        secret_enc=auth_secrets.encrypt_secret(secret),
        enrolled_ts=NOW,
    )
    return users_repo.by_id(db, user_id)


def code_now(secret: str, now: int = NOW) -> str:
    return totp.code(secret, totp.counter_at(now))


def sign_in(db, secret, *, now=NOW, password=PASSWORD, email=EMAIL):
    first = service.begin_login(db, email=email, password=password, now=now)
    if not isinstance(first, service.Ticket):
        return first
    return service.complete_login(db, ticket=first.token, code=code_now(secret, now), now=now)


# ── ขั้นที่ 1 ────────────────────────────────────────────────────────────────


def test_a_correct_password_yields_a_ticket_not_a_session(db, account, secret):
    """"ขั้นที่ 1 ผ่านแล้วยังไม่มีสิทธิ์อะไรเลย" — สิ่งที่ได้คือตั๋ว ไม่ใช่ session"""
    result = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW)

    assert isinstance(result, service.Ticket)
    assert sessions_repo.lookup(db, result.token, now=NOW) is None


@pytest.mark.parametrize(
    "email,password",
    [
        (EMAIL, "รหัสผ่านผิด"),
        ("ไม่มีคนนี้@example.com", PASSWORD),
        ("ไม่มีคนนี้@example.com", "รหัสผ่านผิด"),
    ],
)
def test_every_kind_of_stage_one_failure_looks_the_same(db, account, email, password):
    """การบอกว่า "ไม่มีบัญชีนี้" คือการยืนยันรายชื่อผู้ใช้ให้คนที่เดาอีเมล"""
    result = service.begin_login(db, email=email, password=password, now=NOW)

    assert result == service.Failure(locked_until=None)


def test_an_account_that_has_not_enrolled_totp_cannot_get_in_at_all(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 1 — แม้รหัสผ่านถูก

    เงื่อนไขนี้อยู่ที่ขั้นตอน login ไม่ใช่ที่หน้าจอ · บัญชีที่เชิญมาแล้วแต่ยังไม่
    สแกน QR ต้องไม่มีทางเข้าได้เลย ไม่ใช่เข้าได้แล้วเจอคำเตือน
    """
    users_repo.create(
        db,
        email="pending@example.com",
        name="ยังไม่ผูก",
        role="ADMIN",
        created_ts=NOW,
        password_hash=auth_secrets.hash_password(PASSWORD),
    )

    result = service.begin_login(
        db, email="pending@example.com", password=PASSWORD, now=NOW
    )

    assert isinstance(result, service.Failure)


def test_a_suspended_account_cannot_get_in_either(db, account):
    users_repo.create(
        db, email="owner@example.com", name="เจ้าของ", role="OWNER", created_ts=NOW
    )
    users_repo.set_status(db, account.id, "suspended")

    assert isinstance(
        service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW), service.Failure
    )


# ── ขั้นที่ 2 ────────────────────────────────────────────────────────────────


def test_the_two_stages_together_produce_a_session_that_resolves(db, account, secret):
    result = sign_in(db, secret)

    assert isinstance(result, service.SignedIn)
    found = sessions_repo.lookup(db, result.token, now=NOW + 1)
    assert found is not None and found[1].email == EMAIL


def test_a_ticket_cannot_be_spent_twice(db, account, secret):
    first = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW)
    service.complete_login(db, ticket=first.token, code=code_now(secret), now=NOW)

    again = service.complete_login(
        db, ticket=first.token, code=code_now(secret, NOW + 60_000), now=NOW + 60_000
    )

    assert isinstance(again, service.Failure)


def test_a_totp_code_that_already_signed_someone_in_cannot_do_it_again(db, account, secret):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 6"""
    assert isinstance(sign_in(db, secret), service.SignedIn)

    second = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW + 1000)
    result = service.complete_login(
        db, ticket=second.token, code=code_now(secret), now=NOW + 1000
    )

    assert isinstance(result, service.Failure)


def test_a_backup_code_works_in_place_of_the_authenticator(db, account, secret):
    codes = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=account.id, codes=codes, now=NOW)
    ticket = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW)

    result = service.complete_login(db, ticket=ticket.token, code=codes[0], now=NOW)

    assert isinstance(result, service.SignedIn)
    assert backup_codes.remaining(db, account.id) == totp.BACKUP_CODE_COUNT - 1


def test_a_wrong_second_factor_fails_without_burning_a_backup_code(db, account, secret):
    codes = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=account.id, codes=codes, now=NOW)
    ticket = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW)

    result = service.complete_login(db, ticket=ticket.token, code="000000", now=NOW)

    assert isinstance(result, service.Failure)
    assert backup_codes.remaining(db, account.id) == totp.BACKUP_CODE_COUNT


# ── การล็อก ──────────────────────────────────────────────────────────────────


def test_five_wrong_passwords_lock_the_account_and_the_right_one_then_fails(db, account):
    """ล็อกที่บัญชี — รหัสผ่านที่ถูกต้องระหว่างล็อกก็ยังไม่ผ่าน"""
    for i in range(login_attempts.MAX_FAILURES):
        service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + i)

    result = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW + 10)

    assert isinstance(result, service.Failure)
    assert result.locked_until is not None


def test_hammering_while_locked_does_not_push_the_unlock_time_further_away(db, account):
    """ไม่งั้นการยิงรัวลงโทษเจ้าของบัญชีตัวจริง ไม่ใช่คนที่ยิง"""
    for i in range(login_attempts.MAX_FAILURES):
        service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + i)
    first = service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + 10)

    for extra in range(20):
        service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + 20 + extra)
    later = service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + 100)

    assert later.locked_until == first.locked_until


def test_passing_stage_one_does_not_clear_the_counter(db, account, secret):
    """ถ้าล้างที่ขั้นที่ 1 คนที่รู้รหัสผ่านแต่ไม่มีอุปกรณ์ 2FA จะยิงขั้นที่ 2 ได้ไม่จำกัด"""
    for i in range(login_attempts.MAX_FAILURES - 1):
        service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + i)

    service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW + 5)

    assert login_attempts.lock_state(db, EMAIL).consecutive_failures == 4


def test_a_complete_login_is_what_clears_the_counter(db, account, secret):
    for i in range(login_attempts.MAX_FAILURES - 1):
        service.begin_login(db, email=EMAIL, password="ผิด", now=NOW + i)

    assert isinstance(sign_in(db, secret, now=NOW + 10), service.SignedIn)
    assert login_attempts.lock_state(db, EMAIL).consecutive_failures == 0


def test_wrong_second_factors_count_towards_the_same_lock_as_wrong_passwords(db, account, secret):
    """สเปกนับทั้งสองขั้นเข้าตัวนับเดียวกัน — ไม่ใช่โควตาคนละชุด"""
    for i in range(login_attempts.MAX_FAILURES):
        ticket = service.begin_login(db, email=EMAIL, password=PASSWORD, now=NOW + i)
        service.complete_login(db, ticket=ticket.token, code="000000", now=NOW + i)

    assert login_attempts.lock_state(db, EMAIL).is_locked(NOW + 10)


# ── step-up ──────────────────────────────────────────────────────────────────


def test_step_up_accepts_the_authenticator_code(db, account, secret):
    assert service.verify_step_up(db, account, code_now(secret), now=NOW)


def test_step_up_refuses_a_backup_code(db, account, secret):
    """spec/09 §backup code — รหัสที่พิมพ์เก็บไว้ในกระเป๋าไม่ได้ตอบว่าใครนั่งอยู่ตอนนี้"""
    codes = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=account.id, codes=codes, now=NOW)

    assert not service.verify_step_up(db, account, codes[0], now=NOW)
    assert backup_codes.remaining(db, account.id) == totp.BACKUP_CODE_COUNT


def test_a_code_spent_on_step_up_cannot_then_be_used_to_sign_in(db, account, secret):
    """counter ตัวเดียวกันทั้งสองทาง — ไม่งั้น step-up กลายเป็นช่องอุ่นรหัสไว้ใช้ต่อ"""
    assert service.verify_step_up(db, account, code_now(secret), now=NOW)

    fresh = users_repo.by_id(db, account.id)
    assert not service.verify_step_up(db, fresh, code_now(secret), now=NOW)


def test_step_up_refuses_when_the_account_has_no_totp(db):
    user_id = users_repo.create(
        db, email="bare@example.com", name="เปล่า", role="VIEWER", created_ts=NOW
    )

    assert not service.verify_step_up(db, users_repo.by_id(db, user_id), "123456", now=NOW)


# ── ผูก 2FA ──────────────────────────────────────────────────────────────────


def test_enrolment_only_completes_when_a_code_from_the_app_comes_back(db):
    """ถ้าเปิดใช้บัญชีตั้งแต่ตอนแสดง QR จะได้บัญชีที่เจ้าของเข้าไม่ได้และปลดเองไม่ได้"""
    user_id = users_repo.create(
        db, email="new@example.com", name="ใหม่", role="VIEWER", created_ts=NOW
    )
    user = users_repo.by_id(db, user_id)

    fresh_secret, codes = service.enrol(db, user=user, password=PASSWORD, now=NOW)
    assert users_repo.by_id(db, user_id).status == "pending"

    assert not service.confirm_enrolment(
        db, user=user, secret=fresh_secret, codes=codes, code="000000", now=NOW
    )
    assert users_repo.by_id(db, user_id).status == "pending"

    assert service.confirm_enrolment(
        db,
        user=user,
        secret=fresh_secret,
        codes=codes,
        code=code_now(fresh_secret),
        now=NOW,
    )
    assert users_repo.by_id(db, user_id).status == "active"
    assert backup_codes.remaining(db, user_id) == totp.BACKUP_CODE_COUNT
