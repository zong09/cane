"""auth ที่ฐาน — สิทธิ์ trigger และ repository (spec/09)

ครึ่งหนึ่งของไฟล์นี้ยิงให้ล้ม · ข้อบังคับที่ประกาศไว้แต่ไม่มีใครลองยิงให้ล้ม คือ
ข้อบังคับที่ไม่รู้ว่ามีผลหรือเปล่า (ADR 23) — และ `alembic upgrade` ที่ผ่าน
ไม่ได้แปลว่า GRANT กับ trigger ที่เขียนไว้ทำงาน

เทสต์ที่เกี่ยวกับสิทธิ์ต้อง `SET LOCAL ROLE cane_console` ใน savepoint ของตัวเอง
เหมือน `test_db_grants.py` — fixture `db` ต่อด้วยสิทธิ์ของ login user ซึ่งทำได้ทุกอย่าง
ถ้าลืมสวม role เทสต์จะเขียวโดยไม่ได้ทดสอบอะไรเลย
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from cane.auth import secrets as auth_secrets
from cane.auth import totp
from cane.db.repo import audit, auth_tokens, backup_codes, login_attempts
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import AUTH_TABLES

pytestmark = pytest.mark.db

NOW = 1_789_000_000_000
MINUTE = 60_000
KEY = {"CANE_SECRET_KEY": "k" * 48}

#: matrix ย่อสำหรับเทสต์ — ไม่ใช่ของจริง ของจริงมาจาก `cane auth seed-permissions`
SMALL_MATRIX = {
    "ADMIN": {"engine_control": True, "view_overview": True},
    "TRADER": {"engine_control": False, "view_overview": True},
    "VIEWER": {"engine_control": False, "view_overview": True},
    "AUDITOR": {"engine_control": False, "view_overview": True},
    "OWNER": {"engine_control": True, "view_overview": True},
}


@pytest.fixture(autouse=True)
def clean_auth(db):
    """เริ่มทุกเทสต์จากโดเมน auth ที่ว่าง — fixture `db` rollback ให้ท้ายเทสต์อยู่แล้ว"""
    for table in AUTH_TABLES:
        db.execute(table.delete())


def _as_console(conn) -> None:
    conn.execute(text('SET LOCAL ROLE "cane_console"'))


def make_user(db, *, email="someone@example.com", role="TRADER", enrolled=True):
    user_id = users_repo.create(
        db,
        email=email,
        name="คนทดสอบ",
        role=role,
        created_ts=NOW,
        password_hash=auth_secrets.hash_password("รหัสผ่านที่ยาวพอ"),
    )
    if enrolled:
        users_repo.enrol_totp(
            db,
            user_id,
            secret_enc=auth_secrets.encrypt_secret(totp.new_secret(), KEY),
            enrolled_ts=NOW,
        )
    return user_id


# ── สิ่งที่ฐานต้องปฏิเสธ ─────────────────────────────────────────────────────


@pytest.mark.parametrize("table", ["user_audit_log", "login_attempts"])
@pytest.mark.parametrize("verb", ["UPDATE {t} SET ts = 1", "DELETE FROM {t}"])
def test_the_console_cannot_rewrite_what_it_already_wrote(db, table, verb):
    """append-only ด้วยสิทธิ์ ไม่ใช่ด้วยข้อตกลง (ADR 23)

    ตารางที่บอกว่า "ใครทำอะไร" ซึ่งคนที่ทำแก้ได้เอง ไม่ได้บอกอะไรเลย
    """
    with pytest.raises(ProgrammingError, match="permission denied"):
        with db.begin_nested():
            _as_console(db)
            db.execute(text(verb.format(t=table)))


def test_the_console_cannot_change_the_email_of_an_account(db):
    """อีเมลคือตัวตนที่ audit log อ้างถึง — ไม่อยู่ใน `GRANT UPDATE` ของ 0010"""
    user_id = make_user(db)

    with pytest.raises(ProgrammingError, match="permission denied"):
        with db.begin_nested():
            _as_console(db)
            db.execute(
                text("UPDATE users SET email = :e WHERE id = :i"),
                {"e": "someone-else@example.com", "i": user_id},
            )


def test_the_console_cannot_edit_the_contents_of_a_permission_version(db):
    """แก้ตารางสิทธิ์ = สร้างเวอร์ชันใหม่ (ADR 18) ไม่ใช่ `UPDATE` ทับ"""
    version_id = perms.insert_version(db, SMALL_MATRIX, created_ts=NOW)

    with pytest.raises(ProgrammingError, match="permission denied"):
        with db.begin_nested():
            _as_console(db)
            db.execute(
                text("UPDATE role_permissions SET allowed = true WHERE version_id = :v"),
                {"v": version_id},
            )


def test_suspending_the_last_active_owner_is_refused_by_the_database(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 7

    ระบบที่ไม่มี OWNER แก้ profile และสลับ `dry_run` ไม่ได้อีกเลย และไม่มีทางกู้
    จากในคอนโซล — ข้อบังคับที่การพลาดครั้งเดียวทำให้กู้ไม่ได้ต้องอยู่ที่ฐาน
    """
    owner_id = make_user(db, email="owner@example.com", role="OWNER")

    with pytest.raises(IntegrityError, match="OWNER"):
        with db.begin_nested():
            users_repo.set_status(db, owner_id, "suspended")


def test_moving_the_last_active_owner_to_another_role_is_refused_too(db):
    """ระงับกับย้าย role ให้ผลเดียวกันคือไม่มี OWNER เหลือ — trigger จับทั้งสองทาง"""
    owner_id = make_user(db, email="owner@example.com", role="OWNER")

    with pytest.raises(IntegrityError, match="OWNER"):
        with db.begin_nested():
            users_repo.set_role(db, owner_id, "ADMIN")


def test_suspending_an_owner_is_fine_when_another_active_owner_remains(db):
    owner_id = make_user(db, email="owner@example.com", role="OWNER")
    make_user(db, email="owner2@example.com", role="OWNER")

    users_repo.set_status(db, owner_id, "suspended")

    assert users_repo.count_active_owners(db) == 1


def test_the_last_owner_can_still_log_in_while_the_floor_trigger_is_armed(db):
    """trigger ยิงที่ `BEFORE UPDATE` ทุกครั้ง — ต้องไม่ขวางการเขียนคอลัมน์อื่น

    ถ้าเงื่อนไขในtrigger เขียนกว้างไป OWNER คนสุดท้ายจะ login ไม่ได้ ซึ่งเป็น
    การล็อกตัวเองออกจากระบบด้วยกลไกที่มีไว้กันการล็อกตัวเองออกจากระบบ
    """
    owner_id = make_user(db, email="owner@example.com", role="OWNER")

    users_repo.touch_login(db, owner_id, NOW)
    users_repo.set_totp_counter(db, owner_id, 42)

    assert users_repo.by_id(db, owner_id).last_login_ts == NOW


def test_a_permission_version_cannot_take_a_capability_away_from_owner(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 8 — ที่ฐาน ไม่ใช่แค่ปุ่มปิดใน UI"""
    broken = {**SMALL_MATRIX, "OWNER": {"engine_control": False}}

    with pytest.raises(IntegrityError, match="OWNER"):
        with db.begin_nested():
            perms.insert_version(db, broken, created_ts=NOW)


def test_an_account_cannot_be_active_without_a_password_and_totp(db):
    """"ยังไม่ผูก 2FA เข้าไม่ได้เลย" เป็นสภาพที่เขียนลงฐานไม่ได้ ไม่ใช่เงื่อนไขในโค้ด"""
    user_id = make_user(db, enrolled=False)

    with pytest.raises(IntegrityError, match="ck_users_active_means_fully_enrolled"):
        with db.begin_nested():
            users_repo.set_status(db, user_id, "active")


def test_two_permission_versions_cannot_be_active_at_once(db):
    first = perms.insert_version(db, SMALL_MATRIX, created_ts=NOW)
    second = perms.insert_version(db, SMALL_MATRIX, created_ts=NOW + 1)
    perms.activate(db, first)
    perms.activate(db, second)

    assert perms.active_version(db).id == second


# ── การล็อกบัญชี ─────────────────────────────────────────────────────────────


def _fail(db, email, ts, ip="203.0.113.9"):
    login_attempts.record(db, email=email, ok=False, kind="password", ts=ts, ip=ip)


def test_five_consecutive_failures_lock_the_account_for_fifteen_minutes(db):
    email = "locked@example.com"
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, email, NOW + i)

    state = login_attempts.lock_state(db, email)

    assert state.consecutive_failures == 5
    assert state.is_locked(NOW + 5)
    assert not state.is_locked(NOW + 4 + login_attempts.LOCK_MS)


def test_four_failures_do_not_lock(db):
    email = "fine@example.com"
    for i in range(4):
        _fail(db, email, NOW + i)

    assert not login_attempts.lock_state(db, email).is_locked(NOW + 4)


def test_one_more_failure_after_the_lock_expires_locks_again_immediately(db):
    """spec/09 §การล็อกบัญชี — "ครบ 15 นาทีแล้วลองใหม่ได้ โดยตัวนับยังไม่ถูกล้าง"

    การนับแบบหน้าต่างเลื่อน ("ผิดกี่ครั้งใน 15 นาทีที่ผ่านมา") จะผ่านเทสต์ข้างบน
    ทุกข้อแต่ตกข้อนี้ เพราะของเก่าหลุดหน้าต่างไปแล้วตัวนับกลับเป็นศูนย์เอง
    """
    email = "again@example.com"
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, email, NOW + i)

    later = NOW + login_attempts.LOCK_MS + MINUTE
    assert not login_attempts.lock_state(db, email).is_locked(later)

    _fail(db, email, later)
    assert login_attempts.lock_state(db, email).is_locked(later + 1)


def test_the_lock_follows_the_account_not_the_device_or_the_address(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 2 — เปลี่ยนเครื่อง/ล้างคุกกี้ก็ยังล็อก"""
    email = "roaming@example.com"
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, email, NOW + i, ip=f"198.51.100.{i}")

    assert login_attempts.lock_state(db, email).is_locked(NOW + 10)


def test_a_successful_login_is_the_thing_that_clears_the_counter(db):
    email = "cleared@example.com"
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, email, NOW + i)

    login_attempts.record(db, email=email, ok=True, kind="totp", ts=NOW + 100)

    assert login_attempts.lock_state(db, email).consecutive_failures == 0


def test_an_admin_unlock_clears_the_counter_without_looking_like_a_login(db):
    email = "unlocked@example.com"
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, email, NOW + i)

    login_attempts.clear(db, email=email, ts=NOW + 100)

    assert login_attempts.lock_state(db, email).consecutive_failures == 0
    kinds = db.execute(
        text("SELECT kind FROM login_attempts WHERE email = :e AND ok ORDER BY ts"),
        {"e": email},
    ).scalars().all()
    assert kinds == ["unlock"]


def test_an_email_with_no_account_is_counted_the_same_way(db):
    """ไม่งั้นหน้าจอที่ "ไม่เคยล็อก" กลายเป็นคำตอบว่าอีเมลนี้ไม่มีในระบบ"""
    email = "nobody@example.com"
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, email, NOW + i)

    assert login_attempts.lock_state(db, email).is_locked(NOW + 5)


def test_the_counter_is_not_case_sensitive(db):
    """ถ้าเป็น การล็อกเลี่ยงได้ด้วยการพิมพ์ใหญ่สลับเล็ก"""
    for i in range(login_attempts.MAX_FAILURES):
        _fail(db, "MiXeD@Example.COM", NOW + i)

    assert login_attempts.lock_state(db, "mixed@example.com").is_locked(NOW + 5)


# ── สิทธิ์ ───────────────────────────────────────────────────────────────────


def test_nothing_is_allowed_while_no_permission_version_is_active(db):
    """fail closed — ฐานที่ migrate แล้วแต่ยังไม่ seed ต้องปฏิเสธทุกอย่าง"""
    perms.insert_version(db, SMALL_MATRIX, created_ts=NOW)  # บันทึกแต่ไม่เปิดใช้

    assert not perms.allowed(db, role="ADMIN", cap="engine_control")


def test_owner_is_allowed_everything_even_before_the_table_is_read(db):
    """spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role ระบุว่าข้อนี้อยู่ในโค้ด"""
    assert perms.allowed(db, role="OWNER", cap="engine_control")
    assert perms.allowed(db, role="OWNER", cap="ไม่มีสิทธิ์ชื่อนี้อยู่จริง")


def test_a_viewer_cannot_control_the_engine(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 3 — ที่ชั้นสิทธิ์ ส่วนที่ HTTP อยู่ที่ใบเดียวกันคนละไฟล์"""
    perms.activate(db, perms.insert_version(db, SMALL_MATRIX, created_ts=NOW))

    assert not perms.allowed(db, role="VIEWER", cap="engine_control")
    assert perms.allowed(db, role="ADMIN", cap="engine_control")


def test_a_capability_missing_from_the_active_version_falls_to_the_safe_side(db):
    perms.activate(db, perms.insert_version(db, SMALL_MATRIX, created_ts=NOW))

    assert not perms.allowed(db, role="ADMIN", cap="toggle_dry_run")


# ── ตั๋วและลิงก์ ─────────────────────────────────────────────────────────────


def test_a_login_ticket_works_once_and_only_once(db):
    user_id = make_user(db)
    token = auth_secrets.new_token()
    auth_tokens.issue(db, user_id=user_id, kind="login", token=token, now=NOW)

    assert auth_tokens.consume(db, token=token, kind="login", now=NOW + 1) == user_id
    assert auth_tokens.consume(db, token=token, kind="login", now=NOW + 2) is None


def test_a_login_ticket_expires_after_five_minutes(db):
    user_id = make_user(db)
    token = auth_secrets.new_token()
    auth_tokens.issue(db, user_id=user_id, kind="login", token=token, now=NOW)

    assert (
        auth_tokens.consume(
            db, token=token, kind="login", now=NOW + auth_tokens.LOGIN_TTL_MS + 1
        )
        is None
    )


def test_a_ticket_cannot_be_redeemed_as_a_different_kind(db):
    """ตั๋วขั้นที่ 1 ต้องไม่กลายเป็นลิงก์ตั้งรหัสผ่านใหม่ — สองอย่างปลดล็อกคนละเรื่อง"""
    user_id = make_user(db)
    token = auth_secrets.new_token()
    auth_tokens.issue(db, user_id=user_id, kind="login", token=token, now=NOW)

    assert auth_tokens.consume(db, token=token, kind="reset_password", now=NOW + 1) is None


def test_issuing_a_new_link_kills_the_previous_one(db):
    """spec/09 §7. คำเชิญ · reset 2FA · รหัสผ่านที่ลืม — การออกใหม่ฆ่าลิงก์เดิม"""
    user_id = make_user(db)
    old, new = auth_secrets.new_token(), auth_secrets.new_token()
    auth_tokens.issue(db, user_id=user_id, kind="invite", token=old, now=NOW)
    auth_tokens.issue(db, user_id=user_id, kind="invite", token=new, now=NOW + 1)

    assert auth_tokens.consume(db, token=old, kind="invite", now=NOW + 2) is None
    assert auth_tokens.consume(db, token=new, kind="invite", now=NOW + 2) == user_id


# ── backup code ──────────────────────────────────────────────────────────────


def test_a_backup_code_dies_after_one_use(db):
    user_id = make_user(db)
    codes = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=user_id, codes=codes, now=NOW)

    assert backup_codes.consume(db, user_id=user_id, code=codes[0], now=NOW + 1)
    assert not backup_codes.consume(db, user_id=user_id, code=codes[0], now=NOW + 2)
    assert backup_codes.remaining(db, user_id) == totp.BACKUP_CODE_COUNT - 1


def test_a_code_typed_in_lowercase_without_the_dash_still_works(db):
    user_id = make_user(db)
    codes = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=user_id, codes=codes, now=NOW)

    typed = codes[0].lower().replace("-", " ")
    assert backup_codes.consume(db, user_id=user_id, code=typed, now=NOW + 1)


def test_a_new_set_kills_the_whole_old_set_rather_than_topping_it_up(db):
    user_id = make_user(db)
    old = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=user_id, codes=old, now=NOW)
    backup_codes.consume(db, user_id=user_id, code=old[0], now=NOW + 1)

    new = totp.new_backup_codes()
    backup_codes.replace_set(db, user_id=user_id, codes=new, now=NOW + 2)

    assert backup_codes.remaining(db, user_id) == totp.BACKUP_CODE_COUNT
    assert not backup_codes.consume(db, user_id=user_id, code=old[1], now=NOW + 3)
    assert backup_codes.consume(db, user_id=user_id, code=new[0], now=NOW + 3)


# ── session ──────────────────────────────────────────────────────────────────


def _open_session(db, user_id, *, now=NOW):
    token = auth_secrets.new_token()
    session_id = sessions_repo.create(db, user_id=user_id, token=token, now=now)
    return token, session_id


def test_a_live_session_resolves_to_its_user(db):
    user_id = make_user(db)
    token, _ = _open_session(db, user_id)

    found = sessions_repo.lookup(db, token, now=NOW + 1)

    assert found is not None
    assert found[1].id == user_id


def test_a_revoked_session_is_gone_on_the_very_next_request(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 4"""
    user_id = make_user(db)
    token, session_id = _open_session(db, user_id)

    sessions_repo.revoke(db, session_id, NOW + 1)

    assert sessions_repo.lookup(db, token, now=NOW + 2) is None


def test_suspending_a_user_ends_their_sessions_without_waiting_for_expiry(db):
    """spec/09 §6. session — "ระงับผู้ใช้ = ตัด session ทันที" """
    make_user(db, email="owner@example.com", role="OWNER")
    user_id = make_user(db)
    token, _ = _open_session(db, user_id)

    users_repo.set_status(db, user_id, "suspended")

    assert sessions_repo.lookup(db, token, now=NOW + 1) is None


def test_changing_a_role_takes_effect_on_the_next_request_without_a_new_login(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 5 — เพราะ role ไม่เคยถูกแคชไว้ในคุกกี้"""
    user_id = make_user(db, role="VIEWER")
    token, _ = _open_session(db, user_id)
    assert sessions_repo.lookup(db, token, now=NOW + 1)[1].role == "VIEWER"

    users_repo.set_role(db, user_id, "ADMIN")

    assert sessions_repo.lookup(db, token, now=NOW + 2)[1].role == "ADMIN"


def test_an_expired_session_stops_working_by_itself(db):
    user_id = make_user(db)
    token, _ = _open_session(db, user_id)

    assert sessions_repo.lookup(db, token, now=NOW + sessions_repo.LIFETIME_MS) is None


def test_the_session_id_is_not_accidentally_the_user_id(db):
    """สอง `id` ในคำสั่ง join เดียวกัน — ถ้าไม่เติมคำนำหน้า อันหนึ่งจะทับอีกอันเงียบๆ"""
    make_user(db, email="first@example.com")
    user_id = make_user(db, email="second@example.com")
    token, session_id = _open_session(db, user_id)

    session, user = sessions_repo.lookup(db, token, now=NOW + 1)

    assert session.id == session_id
    assert user.id == user_id


# ── audit ────────────────────────────────────────────────────────────────────


def test_a_detail_that_looks_like_a_credential_reaches_the_table_already_masked(db):
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 9

    ตารางนี้ลบไม่ได้ · ค่าที่หลุดลงไปแล้วอยู่ถาวร การกรองตอนอ่านจึงไม่นับ
    """
    audit.record(
        db,
        action="notify.reveal_credential",
        ts=NOW,
        detail={"api_key": "sk-ค่าจริงที่ไม่ควรลง", "channel": "line"},
    )

    stored = audit.recent(db)[0].detail

    assert stored["api_key"] == "***"
    assert stored["channel"] == "line"


def test_the_masking_uses_the_same_filter_as_the_trading_system(db):
    """ตัวกรองสองตัวคือกฎสองชุดที่จะต่างกันในวันที่มีใครแก้ตัวหนึ่ง"""
    audit.record(db, action="t", ts=NOW, detail={"nested": {"secret": "ค่า"}})

    assert audit.recent(db)[0].detail["nested"]["secret"] == "***"


def test_step_up_is_recorded_as_a_column_not_hoped_for(db):
    user_id = make_user(db)
    audit.record(db, action="engine.start", ts=NOW, actor_user_id=user_id)
    audit.record(
        db, action="engine.stop", ts=NOW + 1, actor_user_id=user_id, step_up_verified=True
    )

    entries = {entry.action: entry.step_up_verified for entry in audit.recent(db)}

    assert entries == {"engine.start": False, "engine.stop": True}
