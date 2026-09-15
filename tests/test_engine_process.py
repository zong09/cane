"""เกณฑ์สองข้อที่ process ปลอมพิสูจน์ไม่ได้ — spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 4 และ 7

ที่เหลือของใบ 18 ทดสอบด้วย `spawn` ที่ inject เข้าไป (`test_engine_state.py`) เพราะนั่น
เร็วกว่าและไม่ฝากความหวังไว้กับระบบปฏิบัติการ · **สองข้อนี้ทำแบบนั้นไม่ได้**:

- ข้อ 4 อ้างว่า "ฆ่า process ทิ้งแล้ว heartbeat หยุด" — process ปลอมไม่มีวันตายจริง
  ข้อที่อยากรู้คือ **ลูปตัวจริงหยุดเขียนตอนถูกฆ่า** ซึ่งเป็นสมบัติของ OS ไม่ใช่ของเรา
- ข้อ 7 อ้างว่า latch อยู่รอดข้ามขอบเขตของ process — มี process จริงตัวที่สองเท่านั้น
  ที่แสดงได้

จึงช้ากว่าเทสต์อื่นทั้งชุดรวมกัน (~สิบวินาที) และติด marker `slow` ไว้ด้วย:

    uv run --env-file .env --extra dev pytest -q -m "not slow"

**ใช้ `db_engine` ไม่ใช่ `db`** — process ลูกต่อ DB ด้วย connection ของตัวเอง มันมองไม่เห็น
ทรานแซกชันที่ยังไม่ commit ของเทสต์ · ผลจึงพ้นจาก rollback และต้องเก็บกวาดเองใน
`finally` ตามแบบ `test_risk.py` ที่ทำเรื่องเดียวกันด้วยเหตุผลเดียวกัน
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from cane.db.repo import enginestate, killswitch
from cane.engine.state import HEARTBEAT_PERIOD_S, CRASHED, RUNNING
from cane.engine.supervisor import Supervisor, spawn_subprocess

pytestmark = [pytest.mark.db, pytest.mark.slow]

PROFILE = "paper"

#: รอให้ผลปรากฏนานที่สุดเท่าไหร่ · สี่รอบเพราะเกณฑ์บอกว่า "ภายในสองรอบ heartbeat"
#: และการเผื่ออีกเท่าตัวคือส่วนต่างระหว่าง "ช้าผิดปกติ" กับ "ไม่เกิดขึ้น"
DEADLINE_S = 4 * HEARTBEAT_PERIOD_S


@pytest.fixture
def engine_process(db_engine):
    """สตาร์ท engine จริงหนึ่งตัว แล้วรับประกันว่ามันถูกเก็บกวาดไม่ว่าเทสต์จะจบยังไง

    `SIGKILL` ที่นี่คือที่เดียวที่ชอบธรรม — `Process` ตัวจริงไม่มีเมท็อดนั้นเลย
    (ดู `test_the_process_protocol_has_no_way_to_send_sigkill`) แต่เทสต์ที่ทิ้ง
    process ค้างไว้จะทำให้เทสต์ถัดไปเห็น heartbeat ที่ไม่ได้เป็นของมัน
    """
    started: list = []

    def start() -> object:
        with db_engine.begin() as conn:
            enginestate.set_should_run(conn, PROFILE, should_run=True)
        process = spawn_subprocess(PROFILE)
        started.append(process)
        return process

    try:
        yield start
    finally:
        for process in started:
            process.kill()
            process.wait(timeout=10)
        with db_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM engine_state WHERE profile = :p"), {"p": PROFILE}
            )
            conn.execute(
                text("DELETE FROM kill_switch WHERE profile = :p"), {"p": PROFILE}
            )


def wait_for_status(db_engine, supervisor, wanted: str) -> str:
    """ถามซ้ำจนกว่าจะได้สถานะที่รอ หรือหมดเวลา · คืนสถานะสุดท้ายที่เห็น"""
    deadline = time.monotonic() + DEADLINE_S
    status = "(ยังไม่เคยถาม)"
    while time.monotonic() < deadline:
        with db_engine.connect() as conn:
            status = next(
                view.status
                for view in supervisor.status(conn)
                if view.profile == PROFILE
            )
        if status == wanted:
            return status
        time.sleep(0.5)
    return status


def test_killing_the_engine_process_turns_the_status_to_crashed(db_engine, engine_process):
    """เกณฑ์ข้อ 4 — ฆ่าแล้วสถานะต้องเป็น `crashed` ภายในสองรอบ heartbeat

    เป็นข้อที่ทั้งใบมีไว้เพื่อ · หน้าจอที่ยังเขียน `running` ให้ process ที่ตายไปแล้ว
    คือการโกหกที่ทำให้ไม่มีใครไปกดสตาร์ท (spec/10 §`engine.running` ไม่ใช่ค่าเดียว มันเป็นสองค่า)

    สังเกตว่า supervisor **ยังถือ handle อยู่** ตอนที่รายงาน `crashed` — ตั้งใจให้เป็น
    อย่างนั้น เพราะสถานะต้องมาจาก heartbeat ไม่ใช่จากการมี handle
    """
    supervisor = Supervisor()
    process = engine_process()
    supervisor._handles[PROFILE] = process

    assert wait_for_status(db_engine, supervisor, RUNNING) == RUNNING

    process.kill()
    process.wait(timeout=10)

    assert wait_for_status(db_engine, supervisor, CRASHED) == CRASHED


def test_nothing_starts_the_killed_engine_back_up_on_its_own(db_engine, engine_process):
    """เกณฑ์ข้อ 4 ครึ่งหลัง — **และไม่มีการสตาร์ทใหม่เอง**

    spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile: สิ่งที่ทำให้มันตายรอบแรกยังอยู่ที่เดิม
    การปลุกคืนอัตโนมัติจึงเปลี่ยนความล้มเหลวที่มองเห็นให้เป็นลูปที่ไม่มีใครเห็น

    ถามสถานะซ้ำๆ ตลอดช่วงหนึ่ง เพราะการถามคือเส้นทางเดียวที่โค้ดจะเผลอ spawn ได้
    """
    supervisor = Supervisor()
    process = engine_process()
    supervisor._handles[PROFILE] = process
    wait_for_status(db_engine, supervisor, RUNNING)

    process.kill()
    process.wait(timeout=10)
    assert wait_for_status(db_engine, supervisor, CRASHED) == CRASHED

    with db_engine.connect() as conn:
        before = enginestate.read(conn, PROFILE).last_heartbeat_ts
    time.sleep(2 * HEARTBEAT_PERIOD_S)
    with db_engine.connect() as conn:
        after = enginestate.read(conn, PROFILE)

    assert after.last_heartbeat_ts == before, "มีใคร spawn ตัวใหม่ขึ้นมาเต้นแทน"
    assert after.should_run is True, "เจตนาต้องคงอยู่ — คนสั่งรันไว้และยังไม่ได้สั่งหยุด"


def test_a_latched_kill_switch_outlives_the_engine_process_around_it(db_engine, engine_process):
    """เกณฑ์ข้อ 7 — รีสตาร์ท engine ขณะ latched แล้ว latch ยังอยู่

    spec/06 เขียนว่า latched ไม่หายเมื่อ process restart · นั่นเป็นคำกล่าวเกี่ยวกับ
    **ขอบเขตของ process** ซึ่งพิสูจน์ได้ด้วย process จริงสองตัวเท่านั้น

    trigger ของ migration 0008 กันฝั่ง engine ไว้แล้ว ข้อนี้จึงไม่ได้ตรวจสิทธิ์ แต่ตรวจ
    ว่าเส้นทางสตาร์ททั้งเส้น (เขียนเจตนา → spawn → เต้น) ไม่มีใครเผลอล้างแถวนั้นทิ้ง
    """
    with db_engine.begin() as conn:
        killswitch.latch(conn, PROFILE, reason="เทสต์ใบ 18", by="tester")

    supervisor = Supervisor()
    first = engine_process()
    supervisor._handles[PROFILE] = first
    assert wait_for_status(db_engine, supervisor, RUNNING) == RUNNING

    first.terminate()
    first.wait(timeout=10)
    supervisor.forget(PROFILE)

    second = engine_process()
    supervisor._handles[PROFILE] = second
    assert wait_for_status(db_engine, supervisor, RUNNING) == RUNNING

    with db_engine.connect() as conn:
        after = killswitch.read(conn, PROFILE)

    assert after.latched is True
    assert after.reason == "เทสต์ใบ 18", "เหตุผลของครั้งแรกต้องไม่ถูกเขียนทับด้วย"


def test_an_engine_started_with_nobody_asking_for_it_exits_without_pretending_to_live(
    db_engine,
):
    """ผลตามมาของ "ไม่มีแถว = ไม่รัน" ที่ต้องเห็นด้วยตา ไม่ใช่แค่ในเทสต์ฟังก์ชัน

    `cane engine run` ที่สั่งด้วยมือบนฐานที่ยังไม่มีใครกด start ต้องออกทันทีด้วย
    รหัส 0 **และต้องไม่เคยเขียน heartbeat เลย** ไม่งั้นคอนโซลจะเห็น `stopping` ของ
    process ที่ไม่มีใครเรียกมา ซึ่งเป็นสถานะที่อธิบายที่มาไม่ได้
    """
    with db_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM engine_state WHERE profile = :p"), {"p": PROFILE}
        )

    process = spawn_subprocess(PROFILE)
    try:
        assert process.wait(timeout=DEADLINE_S) == 0
    finally:
        process.kill()

    with db_engine.connect() as conn:
        assert enginestate.read(conn, PROFILE).last_heartbeat_ts is None
