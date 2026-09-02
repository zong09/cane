"""ข้อบังคับของ DB ต้องปฏิเสธจริง ไม่ใช่แค่ประกาศไว้

ไฟล์นี้ทดสอบสิ่งที่ **JSONL ทำไม่ได้** — ตอนเก็บเป็นไฟล์ append-only เป็นแค่ข้อตกลง
ที่โค้ดผิดพลาดเขียนทับได้ ตอนย้ายลง Postgres มันกลายเป็นสิทธิ์ที่ DB ปฏิเสธให้

**ข้อบังคับที่ประกาศไว้แต่ไม่มีใครลองยิงให้ล้ม คือข้อบังคับที่ไม่รู้ว่ามีผลหรือเปล่า**
เทสต์ที่ยืนยันการปฏิเสธจึงสำคัญเท่าเทสต์ที่ยืนยันว่า insert ผ่าน

ทุกเทสต์สวม role ด้วย `SET LOCAL ROLE` ใน savepoint ของตัวเอง — `LOCAL` ถูกยกเลิก
ตอน savepoint ถูก rollback จึงไม่รั่วไปเทสต์ถัดไปและไม่ต้องมี DSN ชุดที่สอง
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from cane.data.ohlcv import Bar
from cane.db.engine import make_engine
from cane.db.repo.bars import insert_bars

pytestmark = pytest.mark.db

SYMBOL = "BTC/USDT"
TIMEFRAME = "1d"
OPEN_TS = 1_788_000_000_000
DAY_MS = 86_400_000

_INSERT_BAR = text(
    """
    INSERT INTO bars (symbol, timeframe, open_ts, close_ts,
                      open, high, low, close, volume, created_ts)
    VALUES (:symbol, :timeframe, :open_ts, :close_ts,
            :open, :high, :low, :close, :volume, :created_ts)
    """
)

_BAR = {
    "symbol": SYMBOL,
    "timeframe": TIMEFRAME,
    "open_ts": OPEN_TS,
    "close_ts": OPEN_TS + DAY_MS,
    "open": 77_000,
    "high": 78_000,
    "low": 76_500,
    "close": 77_500,
    "volume": 1234.5,
    "created_ts": OPEN_TS,
}


def _as_role(conn, role: str) -> None:
    conn.execute(text(f'SET LOCAL ROLE "{role}"'))


def _refusal(conn, role: str, statement: str):
    """รัน statement ในสิทธิ์ของ role แล้วคืน exception ที่ DB โต้กลับมา"""
    with pytest.raises(ProgrammingError) as caught:
        with conn.begin_nested():
            _as_role(conn, role)
            conn.execute(text(statement))
    return caught.value


# ── role engine เขียนได้ แต่แก้ของที่เขียนแล้วไม่ได้ ──────────────────────────


def test_the_engine_role_can_insert_and_read_facts(db):
    with db.begin_nested():
        _as_role(db, "cane_engine")
        db.execute(_INSERT_BAR, _BAR)
        count = db.execute(
            text("SELECT count(*) FROM bars WHERE open_ts = :ts"), {"ts": OPEN_TS}
        ).scalar_one()

    assert count == 1


def test_the_engine_role_can_reinsert_a_bar_without_update_privilege(db):
    """`ON CONFLICT DO NOTHING` ต้องไม่ต้องการสิทธิ์ `UPDATE`

    นี่คือเหตุผลที่ repository เลือก `DO NOTHING` ไม่ใช่ `DO UPDATE` — ถ้าเป็น
    upsert จริง engine จะยิงไม่ได้เลยเพราะไม่มีสิทธิ์ UPDATE ตรรกะกับข้อบังคับ
    ต้องพูดตรงกัน ไม่ใช่ตรรกะที่ใจดีกว่าที่ DB ยอม
    """
    dup = Bar(
        open_ts=OPEN_TS,
        close_ts=OPEN_TS + DAY_MS,
        open=77_000.0,
        high=78_000.0,
        low=76_500.0,
        close=77_500.0,
        volume=1234.5,
    )

    with db.begin_nested():
        _as_role(db, "cane_engine")
        first = insert_bars(db, SYMBOL, TIMEFRAME, [dup])
        second = insert_bars(db, SYMBOL, TIMEFRAME, [dup])

    assert (first, second) == (1, 0)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE bars SET close = 0",
        "DELETE FROM bars",
        "UPDATE funding_observations SET rate = 0",
        "DELETE FROM funding_observations",
    ],
)
def test_the_engine_role_cannot_rewrite_history(db, statement):
    error = _refusal(db, "cane_engine", statement)

    assert isinstance(error.orig, psycopg.errors.InsufficientPrivilege)


def test_the_engine_role_cannot_change_the_schema_either(db):
    """migration รันด้วยสิทธิ์ของ login user — role ของแอปแก้โครงสร้างไม่ได้"""
    error = _refusal(db, "cane_engine", "ALTER TABLE bars ADD COLUMN sneaky int")

    assert isinstance(error.orig, psycopg.errors.InsufficientPrivilege)


# ── role ที่ make_engine() สวมให้ ต้องอยู่ยาว ไม่หลุดกลาง pool ────────────────


@pytest.fixture
def engine_role_engine(db_engine):
    """Engine ที่สวม `cane_engine` ตามเส้นทางที่ของจริงใช้

    เทสต์ข้างบนสวม role ด้วย `SET LOCAL ROLE` เอง ซึ่ง**ไม่ใช่กลไกที่ prod ใช้** —
    ของจริงตั้ง role ตอน connect ผ่าน `make_engine(role=...)` ถ้าไม่มีเทสต์ที่วิ่ง
    ผ่านเส้นทางนั้น คำโฆษณาว่า "DB ปฏิเสธให้" จะจริงแต่กับโค้ดที่ prod ไม่ได้เดิน
    """
    engine = make_engine(role="engine")
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("role", "expected"),
    [("engine", "cane_engine"), ("console", "cane_console")],
)
def test_make_engine_wears_the_role_it_was_built_with(db_engine, role, expected):
    engine = make_engine(role=role)
    try:
        with engine.connect() as conn:
            got = conn.execute(text("SELECT current_user")).scalar_one()
    finally:
        engine.dispose()

    assert got == expected


def test_the_role_survives_a_rollback_and_the_trip_back_to_the_pool(engine_role_engine):
    """**เคยรั่วจริง** — `SET ROLE` เป็นคำสั่ง transactional

    psycopg3 ไม่ใช่ autocommit การยิง `SET ROLE` ตอน connect จึงเปิดทรานแซกชัน
    ขึ้นมาเงียบๆ · รอบแรกที่ผู้เรียกออกจาก `with engine.connect()` โดยไม่ commit
    SQLAlchemy สั่ง `ROLLBACK` แล้ว role หลุด — connection กลับเข้า pool ในสภาพ
    เจ้าของตาราง และ checkout ครั้งถัดไปมีสิทธิ์ UPDATE/DELETE เต็มมือ

    เทสต์ตัวนี้คือสิ่งที่จับได้ ตัวเทสต์อื่นทั้งไฟล์ผ่านหมดตอนที่บั๊กนี้ยังอยู่
    """
    with engine_role_engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        # ออกจาก block โดยไม่ commit = SQLAlchemy สั่ง ROLLBACK ให้

    with engine_role_engine.connect() as conn:
        after_rollback = conn.execute(text("SELECT current_user")).scalar_one()

    assert after_rollback == "cane_engine"


def test_an_engine_wearing_the_engine_role_is_refused_an_update(engine_role_engine):
    """เส้นทางเดียวกับที่ engine ของจริงเดิน ไม่ใช่ `SET LOCAL ROLE` ในเทสต์"""
    with pytest.raises(ProgrammingError) as caught:
        with engine_role_engine.begin() as conn:
            conn.execute(text("UPDATE bars SET close = 0"))

    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)


def test_the_login_user_underneath_is_still_visible(engine_role_engine):
    """`session_user` ยังเป็น login user — `RESET ROLE` ยกสิทธิ์คืนได้ใน dev

    เขียนไว้ให้ชัดว่ากลไกนี้กัน **ความผิดพลาดของโค้ด** ไม่ใช่กันคนที่ตั้งใจ
    (ADR 23) ใน prod ให้ผูก group เข้ากับ login user ที่ไม่ได้เป็นเจ้าของตาราง
    """
    with engine_role_engine.connect() as conn:
        row = conn.execute(text("SELECT current_user, session_user")).one()

    assert row.current_user == "cane_engine"
    assert row.session_user != row.current_user


# ── role console อ่านได้ แต่ไม่ใช่คนเขียนข้อเท็จจริง ─────────────────────────


def test_the_console_role_can_read_facts(db):
    with db.begin_nested():
        _as_role(db, "cane_console")
        db.execute(text("SELECT count(*) FROM bars")).scalar_one()


def test_the_console_role_cannot_write_facts(db):
    """คอนโซลเป็นคนดูและคนแก้ config ไม่ใช่คนบันทึกว่าตลาดเกิดอะไรขึ้น"""
    error = _refusal(db, "cane_console", "DELETE FROM bars")

    assert isinstance(error.orig, psycopg.errors.InsufficientPrivilege)


# ── CHECK ระดับ schema: funding ที่หายห้ามกลายเป็นศูนย์ ──────────────────────


def test_a_zero_rate_with_a_reason_is_refused(db):
    """rate=0 คู่กับ unavailable_reason คือการเดาที่ CHECK ต้องปฏิเสธ

    นี่คือกฎของ `data/funding.py` ที่ยกจาก docstring ขึ้นมาเป็นข้อบังคับของ schema
    """
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO funding_observations
                        (symbol, observed_ts, rate, unavailable_reason, created_ts)
                    VALUES ('BTC/USDT', 1, 0, 'timeout', 1)
                    """
                )
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_an_observation_with_neither_rate_nor_reason_is_refused(db):
    """แถวที่ไม่มีค่าและไม่บอกว่าทำไม คือแถวที่อ่านย้อนหลังไม่ได้ความ"""
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO funding_observations
                        (symbol, observed_ts, created_ts)
                    VALUES ('BTC/USDT', 1, 1)
                    """
                )
            )


# ── CHECK ระดับ schema: แท่งที่เป็นไปไม่ได้ต้องไม่เข้าตาราง ──────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("close_ts", OPEN_TS),  # ปิดก่อนหรือพร้อมเปิด
        ("high", 76_000),  # high ต่ำกว่า low
        ("low", 77_800),  # low สูงกว่า close
        ("volume", -1),
    ],
)
def test_an_impossible_bar_is_refused(db, field, value):
    """แท่งที่ขัดนิยามของตัวเองต้องล้มตอน insert ไม่ใช่ไปโป๊ะที่ indicator

    ถ้าปล่อยเข้าไป สูตร Action Zone จะคำนวณต่อบนแท่งที่เป็นไปไม่ได้แล้วให้สัญญาณ
    ที่หน้าตาปกติ — ความผิดพลาดชนิดที่ไม่มีใครจับได้จากรายงาน
    """
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(_INSERT_BAR, {**_BAR, field: value})

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)
