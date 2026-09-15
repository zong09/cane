"""ตาราง `engine_state` กับด่านสิทธิ์ของมัน — ของจริงที่ Postgres บังคับ ไม่ใช่ข้อตกลง

`engine_state` เป็นตารางที่สองของระบบที่เขียนทับได้ คู่กับ `kill_switch` · สิ่งที่
ทำให้มันไม่กลายเป็นช่องโหว่คือ **GRANT ระดับคอลัมน์** ของ migration 0009 ซึ่งมองไม่เห็น
จาก `schema.py` เลย · ไฟล์นี้จึงเป็นที่เดียวที่พิสูจน์ว่ามันมีอยู่จริงและทำงาน

ตรวจโดยสวม role จริงด้วย `SET LOCAL ROLE` ในทรานแซกชันของตัวเอง ตามแบบเดียวกับ
`test_risk.py` — ไม่ใช่การอ่านไฟล์ migration แล้วเชื่อว่ามันถูกรัน

**คำสั่งที่ถูกปฏิเสธทำให้ทรานแซกชันของ Postgres เป็นหมัน** เทสต์ที่คาดการปฏิเสธจึง
ต้องเป็นคำสั่งสุดท้ายของเทสต์นั้น ห้ามมี assert ที่ต้องยิง SQL ต่อท้าย
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from cane.db.repo import enginestate, killswitch

pytestmark = pytest.mark.db

PROFILE = "paper"


@pytest.fixture
def clean(db):
    """ไม่มีแถวของ `paper` ตอนเริ่ม · fixture `db` rollback ให้อยู่แล้วตอนจบ"""
    db.execute(text("DELETE FROM engine_state WHERE profile = :p"), {"p": PROFILE})
    db.execute(text("DELETE FROM kill_switch WHERE profile = :p"), {"p": PROFILE})
    return db


# ── แถวที่ยังไม่มี ──────────────────────────────────────────────────────────


def test_a_profile_with_no_row_is_not_meant_to_run(clean):
    """ค่าตั้งต้นที่ปลอดภัยคือ "ไม่รัน" — fail-closed แบบเดียวกับ `is_latched()`

    ถ้าไม่มีแถวแปลว่า "รัน" บอทจะเริ่มเทรดหลังติดตั้งใหม่โดยไม่มีใครกดอะไรเลย ·
    และ `stopped` กับ `crashed` จะแยกกันไม่ออกเพราะไม่มีสถานะ `stopped` ให้ไปถึง
    """
    got = enginestate.read(clean, PROFILE)

    assert got == enginestate.EngineState(profile=PROFILE, should_run=False)


def test_reading_every_profile_takes_one_query_so_the_two_answers_share_a_moment(clean):
    """spec/10 §6. สัญญาของ API — ทั้งสอง profile ในคำขอเดียว

    เหตุผลในสเปกคือถ้ายิงสองครั้งจะได้ภาพที่ต่างเวลากันเล็กน้อยทุกครั้ง · นับ
    statement จริงเพราะ "คืน dict ที่มีสองคีย์" ทำได้ด้วยลูปเรียก `read()` สองรอบ
    ซึ่งผ่าน assert เรื่องรูปร่างแต่ไม่ผ่านเจตนา
    """
    enginestate.set_should_run(clean, "live", should_run=True)
    enginestate.set_should_run(clean, PROFILE, should_run=False)

    statements: list[str] = []
    event_target = clean.engine

    from sqlalchemy import event

    def record(conn, cursor, statement, *_):  # noqa: ANN001 - callback ของ SQLAlchemy
        if "engine_state" in statement:
            statements.append(statement)

    event.listen(event_target, "before_cursor_execute", record)
    try:
        got = enginestate.read_all(clean)
    finally:
        event.remove(event_target, "before_cursor_execute", record)

    assert set(got) == {"live", PROFILE}
    assert len(statements) == 1, f"ต้องยิงครั้งเดียว ไม่ใช่ {len(statements)}"


# ── ด่านสิทธิ์ระดับคอลัมน์ — migration 0009 ──────────────────────────────────


def test_the_engine_role_can_report_its_own_heartbeat(clean):
    clean.execute(text("SET LOCAL ROLE cane_engine"))

    enginestate.beat(clean, PROFILE)

    assert enginestate.read(clean, PROFILE).last_heartbeat_ts is not None


def test_a_row_the_engine_created_for_itself_starts_out_not_running(clean):
    """**นี่คือเหตุผลที่ `server_default` ของ `should_run` เป็นของจำเป็น**

    engine ต้องแทรกแถวแรกของตัวเองได้ (profile ที่ยังไม่เคยเดินไม่มีแถว) แต่แถวนั้น
    ต้องไม่กลายเป็นการประกาศว่าจะรัน · `GRANT INSERT` ของ engine ไม่มีคอลัมน์นี้
    อยู่ในรายการ ค่าจึงมาจาก DEFAULT เสมอ
    """
    clean.execute(text("SET LOCAL ROLE cane_engine"))

    enginestate.beat(clean, PROFILE)

    assert enginestate.read(clean, PROFILE).should_run is False


def test_the_engine_role_cannot_declare_that_it_should_run(clean):
    """engine ที่ตั้งเจตนาให้ตัวเองได้คือ engine ที่หยุดไม่ได้

    กด stop ตั้ง `should_run = false` แล้ว engine ตั้งกลับเป็น `true` ที่ต้นรอบถัดไป
    ปุ่มหยุดก็ไม่มีความหมาย · GRANT ระดับคอลัมน์ทำให้เขียนโค้ดแบบนั้นแล้วพังทันที
    ไม่ใช่พังเงียบๆ
    """
    clean.execute(text("SET LOCAL ROLE cane_engine"))

    with pytest.raises(ProgrammingError, match="should_run"):
        enginestate.set_should_run(clean, PROFILE, should_run=True)


def test_the_console_role_cannot_forge_a_heartbeat(clean):
    """คอนโซลที่ปลอม heartbeat ได้คือคอนโซลที่แสดง `running` ให้ process ที่ตายแล้ว

    heartbeat คือหลักฐานชิ้นเดียวว่า process ยังเดิน · ถ้าฝั่งที่แสดงผลเขียนมันได้
    หลักฐานกับผู้รายงานก็เป็นคนเดียวกัน
    """
    clean.execute(text("SET LOCAL ROLE cane_console"))

    with pytest.raises(ProgrammingError, match="last_heartbeat_ts"):
        enginestate.beat(clean, PROFILE)


def test_the_console_role_can_move_the_intent(clean):
    clean.execute(text("SET LOCAL ROLE cane_console"))

    enginestate.set_should_run(clean, PROFILE, should_run=True)

    assert enginestate.read(clean, PROFILE).should_run is True


# ── เจตนากับ kill switch ไม่แตะกัน ──────────────────────────────────────────
# spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 2 และ 7


def test_starting_the_engine_leaves_a_latched_kill_switch_exactly_where_it_was(clean):
    """เกณฑ์ข้อ 2 — สตาร์ทตอน latched ได้ตามปกติ และ latch ยังเป็น `true`

    spec/10 §`engine.should_run` ≠ `kill_switch.latched`: ถ้าการสตาร์ทปลด latch ไปด้วย
    คำว่า latched ก็ไม่มีความหมาย เพราะทางออกจากมันจะเป็นการกดสองปุ่มที่ไม่มีใคร
    ตั้งใจให้เป็นการปลด
    """
    killswitch.latch(clean, PROFILE, reason="เทสต์", by="tester")

    enginestate.set_should_run(clean, PROFILE, should_run=True)

    after = killswitch.read(clean, PROFILE)
    assert after.latched is True
    assert after.reason == "เทสต์", "เหตุผลของครั้งแรกต้องไม่ถูกแตะด้วย"


def test_stopping_the_engine_leaves_the_kill_switch_exactly_where_it_was(clean):
    killswitch.latch(clean, PROFILE, reason="เทสต์", by="tester")
    enginestate.set_should_run(clean, PROFILE, should_run=True)

    enginestate.set_should_run(clean, PROFILE, should_run=False)

    assert killswitch.is_latched(clean, PROFILE) is True


def test_latching_the_kill_switch_does_not_stop_the_engine(clean):
    """spec/10 §`engine.should_run` ≠ `kill_switch.latched` — kill switch ไม่หยุด process

    latch แล้ว engine ยังเดินอยู่ ยังบันทึกการตัดสินใจที่ลงท้ายด้วยเหตุผลว่าถูก
    kill switch กั้น · ประวัติที่ขาดหายไปตอนฉุกเฉินคือประวัติที่ขาดหายไปตรงที่
    อยากอ่านที่สุด
    """
    enginestate.set_should_run(clean, PROFILE, should_run=True)

    killswitch.latch(clean, PROFILE, reason="เทสต์", by="tester")

    assert enginestate.read(clean, PROFILE).should_run is True


# ── รูปร่างของตาราง — เกณฑ์ข้อ 9 ─────────────────────────────────────────────


def test_the_table_holds_exactly_the_four_columns_the_spec_lists(db):
    """spec/10 §5. state ที่อยู่ในตาราง ระบุสี่คอลัมน์ และ §7 ข้อ 9 ห้ามเก็บตัวสรุป

    คำบรรยายใบ 18 ขอให้เก็บ `daily_loss` กับ `consecutive_losses` ต่อ profile ด้วย ·
    สเปกปฏิเสธไว้ตรงตัวเพราะตารางสรุปคือแหล่งความจริงที่สองที่จะขัดกับแหล่งแรกใน
    วันที่มีบั๊ก · เทสต์นี้คือสิ่งเดียวที่กันไม่ให้มันแอบกลับเข้ามา

    **ไม่มีคอลัมน์ "สถานะ" ด้วย** — `running`/`crashed` คิดใหม่ทุกครั้งที่ถาม
    ค่าที่เก็บไว้จะค้างอยู่ตอน process ตายกลางทาง ซึ่งคือการโกหกที่ทั้งหน้ามีไว้กัน
    """
    rows = db.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'engine_state' ORDER BY column_name"
        )
    ).scalars().all()

    assert rows == [
        "blocked_reason",
        "last_heartbeat_ts",
        "profile",
        "should_run",
    ]


def test_a_blocked_reason_that_is_only_whitespace_is_refused_by_the_database(clean):
    """`blocked` ที่ไม่มีเหตุผลกำกับคือสิ่งที่คนอ่านแล้วไม่รู้ว่าต้องไปแก้อะไร

    ช่องว่างล้วนแย่กว่า `NULL` เพราะมันดูเหมือนมีค่าแต่ไม่มี — CHECK ที่ฐานจึงตัด
    ตั้งแต่ก่อนเข้าตาราง ไม่ใช่ปล่อยให้หน้าจอไปแสดงช่องว่าง
    """
    clean.execute(text("SET LOCAL ROLE cane_engine"))

    with pytest.raises(Exception, match="blocked_reason_has_a_story"):
        enginestate.beat(clean, PROFILE, blocked_reason="   ")
