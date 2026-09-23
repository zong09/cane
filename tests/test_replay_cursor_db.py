"""ตาราง `replay_cursor` กับด่านสิทธิ์ของมัน — ของจริงที่ Postgres บังคับ ไม่ใช่ข้อตกลง

`replay_cursor` เป็นตารางที่เขียนทับได้ (ADR 23 ยกเว้นให้) สิ่งที่ทำให้มันไม่กลายเป็นช่องโหว่คือ
**GRANT ระดับคอลัมน์** ของ migration 0011 ซึ่งมองไม่เห็นจาก `schema.py` เลย · ไฟล์นี้จึงเป็น
ที่เดียวที่พิสูจน์ว่ามันมีอยู่จริงและทำงาน

ตรวจโดยสวม role จริงด้วย `SET LOCAL ROLE` ในทรานแซกชันของตัวเอง ตามแบบเดียวกับ
`test_db_grants.py` — ไม่ใช่การอ่านไฟล์ migration แล้วเชื่อว่ามันถูกรัน

**คำสั่งที่ถูกปฏิเสธทำให้ทรานแซกชันของ Postgres เป็นหมัน** เทสต์ที่คาดการปฏิเสธจึงต้องอยู่ใน
savepoint (`begin_nested`) และห้ามมี assert ที่ต้องยิง SQL ต่อท้ายใน savepoint นั้น
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from cane.db.repo import replay_cursor as rc

pytestmark = pytest.mark.db

PROFILE = "paper"
AS_OF = 1_788_000_000_000
END_TS = 1_788_100_000_000


@pytest.fixture
def clean(db):
    """ไม่มีแถวของ `paper` ตอนเริ่ม · fixture `db` rollback ให้เองตอนจบ"""
    db.execute(text("DELETE FROM replay_cursor WHERE profile = :p"), {"p": PROFILE})
    return db


def _as_role(conn, role: str) -> None:
    conn.execute(text(f'SET LOCAL ROLE "{role}"'))


def _refusal(conn, role: str, statement: str):
    """รัน statement ในสิทธิ์ของ role แล้วคืน exception ที่ DB ตอบกลับมา"""
    with pytest.raises(ProgrammingError) as caught:
        with conn.begin_nested():
            _as_role(conn, role)
            conn.execute(text(statement))
    return caught.value


# ── ยังไม่มีแถว ──────────────────────────────────────────────────────────


def test_read_returns_none_when_no_row(clean):
    """ไม่มีแถว = ยังไม่เคยรัน replay ใน scratch นี้ — คืน `None` ไม่ใช่ยกข้อผิดพลาด"""
    assert rc.read(clean, PROFILE) is None


def test_start_then_read_returns_the_values(clean):
    """แถวที่ `start()` แล้วต้องอ่านกลับมาได้ครบทุกคอลัมน์"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    got = rc.read(clean, PROFILE)

    assert got is not None
    assert got.profile == PROFILE
    assert got.as_of_ms == AS_OF
    assert got.end_ts == END_TS
    assert got.updated_ts > 0


def test_start_twice_raises_replay_cursor_exists(clean):
    """ครั้งแรกชนะ — replay ที่รันไปแล้วใน scratch เดิมต้องไม่รันซ้ำ"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    with pytest.raises(rc.ReplayCursorExists):
        rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)


# ── advance: เลื่อนความคืบหน้าไปข้างหน้า ──────────────────────────────────


def test_advance_moves_forward_and_bumps_updated_ts(clean):
    """`advance()` เลื่อน `as_of_ms` ไปข้างหน้าและประทับ `updated_ts` ใหม่"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)
    before = rc.read(clean, PROFILE)

    rc.advance(clean, PROFILE, AS_OF + 1_000)

    after = rc.read(clean, PROFILE)
    assert after.as_of_ms == AS_OF + 1_000
    assert after.updated_ts >= before.updated_ts


def test_advance_backwards_is_refused(clean):
    """ถอยหลังไม่ได้ — `WHERE as_of_ms < :new` ไม่เจอแถวให้แก้ จึงยก `ValueError`"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    with pytest.raises(ValueError):
        rc.advance(clean, PROFILE, AS_OF - 1_000)


def test_advance_to_the_same_value_is_refused(clean):
    """ค่าเดิมไม่นับเป็นการเดินหน้า — ต้องไม่เขียนแถว"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    with pytest.raises(ValueError):
        rc.advance(clean, PROFILE, AS_OF)


def test_advance_past_end_ts_is_refused_by_the_database(clean):
    """CHECK `end_ts >= as_of_ms` ต้องปฏิเสธการเลื่อนเลย `end_ts`"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    with pytest.raises(IntegrityError) as caught:
        with clean.begin_nested():
            rc.advance(clean, PROFILE, END_TS + 1_000)

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


# ── CHECK ระดับสคีมา: epoch ms ต้องไม่ใช่ศูนย์หรือติดลบ ─────────────────────


def test_a_non_positive_as_of_ms_is_refused(clean):
    """`as_of_ms <= 0` คือวินาทีที่ถูกแปลงเป็นเลขมา — ต้องไม่ลงตาราง"""
    with pytest.raises(IntegrityError) as caught:
        with clean.begin_nested():
            clean.execute(
                text(
                    "INSERT INTO replay_cursor "
                    "(profile, as_of_ms, end_ts, updated_ts) "
                    "VALUES (:p, 0, :end, 1)"
                ),
                {"p": PROFILE, "end": 1},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


# ── ด่านสิทธิ์ระดับคอลัมน์ — migration 0011 ──────────────────────────────


def test_the_engine_role_can_insert_and_advance(clean):
    """engine ต้อง `start()` (INSERT) แล้ว `advance()` (UPDATE `as_of_ms`) ได้"""
    with clean.begin_nested():
        _as_role(clean, "cane_engine")
        rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)
        rc.advance(clean, PROFILE, AS_OF + 1_000)

    assert rc.read(clean, PROFILE).as_of_ms == AS_OF + 1_000


def test_the_engine_role_cannot_change_end_ts(clean):
    """engine ต้องเปลี่ยน `end_ts` ไม่ได้ — สิทธิ์ UPDATE ครอบคลุมแค่ `as_of_ms` กับ `updated_ts`"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    error = _refusal(
        clean,
        "cane_engine",
        "UPDATE replay_cursor SET end_ts = 2 WHERE profile = 'paper'",
    )

    assert isinstance(error.orig, psycopg.errors.InsufficientPrivilege)


def test_the_engine_role_cannot_delete_a_cursor(clean):
    """reset replay คือ drop scratch database ไม่ใช่ลบแถว — DELETE ต้องถูกปฏิเสธ"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    error = _refusal(
        clean,
        "cane_engine",
        "DELETE FROM replay_cursor WHERE profile = 'paper'",
    )

    assert isinstance(error.orig, psycopg.errors.InsufficientPrivilege)


def test_the_console_role_can_read(clean):
    """console อ่านความคืบหน้าของ replay ได้ — สิทธิ์ SELECT"""
    rc.start(clean, PROFILE, as_of_ms=AS_OF, end_ts=END_TS)

    with clean.begin_nested():
        _as_role(clean, "cane_console")
        got = clean.execute(
            text("SELECT as_of_ms FROM replay_cursor WHERE profile = :p"),
            {"p": PROFILE},
        ).scalar_one()

    assert got == AS_OF


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO replay_cursor (profile, as_of_ms, end_ts, updated_ts) "
        "VALUES ('paper', 1, 1, 1)",
        "UPDATE replay_cursor SET as_of_ms = 1 WHERE profile = 'paper'",
        "DELETE FROM replay_cursor WHERE profile = 'paper'",
    ],
)
def test_the_console_role_cannot_write(clean, statement):
    """console เป็นคนดู ไม่ใช่คนเขียน — INSERT/UPDATE/DELETE ต้องถูกปฏิเสธ"""
    error = _refusal(clean, "cane_console", statement)

    assert isinstance(error.orig, psycopg.errors.InsufficientPrivilege)
