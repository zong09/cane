"""เจตนาของคนกับ heartbeat ของ engine — แถวดิบ ไม่ใช่สถานะ (spec/10 §5. state ที่อยู่ในตาราง)

## ไม่มีคำว่า `running` ในไฟล์นี้

สถานะที่คอนโซลแสดงคิดจากสองคอลัมน์นี้ที่ `cane/engine/state.py` ซึ่งเป็นฟังก์ชันบริสุทธิ์
ที่ไม่รู้จัก `Connection` เลย · แยกไว้เพราะตารางเก็บ **สิ่งที่วัดได้** ส่วนสถานะคือ
**การตีความ** ที่ spec/10 §2. สาม state ที่คนละเรื่องกัน ระบุว่าต้องคิดใหม่ทุกครั้งที่ถาม

## สองฟังก์ชันเขียนต้องแตะคนละคอลัมน์ ไม่ใช่เพราะมารยาท

`set_should_run()` เป็นของคอนโซล `beat()` เป็นของ engine · GRANT ระดับคอลัมน์ของ
migration 0009 จะปฏิเสธถ้าตัวใดเอ่ยถึงคอลัมน์ของอีกฝั่ง แม้แต่ในรูป `INSERT` ที่ใส่ค่า
เท่าเดิม · ผลคือ `beat()` ที่เผลอเขียน `should_run=True` "ให้แถวมันเกิด" จะพังดังๆ ที่ฐาน
แทนที่จะเปลี่ยน engine ให้กลายเป็นคอนโซลของตัวเองเงียบๆ

## ไม่มีแถว = ยังไม่มีใครสั่งอะไร = ไม่รัน

ตามแบบ `killswitch.is_latched()` ที่คืน `False` เมื่อไม่มีแถว · ค่าตั้งต้นที่ปลอดภัย
ของเจตนาคือ "ไม่รัน" — บอทที่เทรดเพราะไม่มีใครบอกว่าอย่าเทรดคือความล้มเหลวแบบ
fail-open ที่มีเงินติดอยู่ด้วย · และมันทำให้ `derive_status()` ได้ `stopped` ออกมาพอดี
โดยไม่ต้องมีสาขาพิเศษ

**ไฟล์นี้ไม่ import อะไรจาก `killswitch.py` และต้องไม่ import ตลอดไป** — การ start/stop
กับการ latch/unlatch เป็นคนละ state คนละ lifecycle (spec/10 §`engine.should_run` ≠ `kill_switch.latched`)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from cane.db.schema import engine_state
from cane.db.types import now_ms


@dataclass(frozen=True, slots=True)
class EngineState:
    """แถวตามที่มันเป็น · `last_heartbeat_ts = None` คือ **ยังไม่เคยเดิน** ไม่ใช่ "เดินนานมาแล้ว" """

    profile: str
    should_run: bool
    last_heartbeat_ts: int | None = None
    blocked_reason: str | None = None


def _row_to_state(profile: str, row) -> EngineState:  # noqa: ANN001 - row ของ SQLAlchemy
    return EngineState(
        profile=profile,
        should_run=row.should_run,
        last_heartbeat_ts=row.last_heartbeat_ts,
        blocked_reason=row.blocked_reason,
    )


def read(conn: Connection, profile: str) -> EngineState:
    """แถวของ profile เดียว · ไม่มีแถว = ไม่รัน ยังไม่เคยเต้น (ดูหัวไฟล์)"""
    row = conn.execute(
        select(
            engine_state.c.should_run,
            engine_state.c.last_heartbeat_ts,
            engine_state.c.blocked_reason,
        ).where(engine_state.c.profile == profile)
    ).one_or_none()
    if row is None:
        return EngineState(profile=profile, should_run=False)
    return _row_to_state(profile, row)


def read_all(conn: Connection) -> dict[str, EngineState]:
    """**ทุกแถวด้วย query เดียว** — คีย์คือ profile · profile ที่ยังไม่มีแถวจะไม่อยู่ในผล

    spec/10 §6. สัญญาของ API บังคับว่า `/api/engine/status` คืนทั้งสอง profile ใน
    คำขอเดียว เหตุผลคือการ์ด PROFILE แสดงสถานะของอีกโหมดด้วย ถ้ายิงสองครั้งจะได้ภาพ
    ที่ต่างเวลากันเล็กน้อยทุกครั้ง · ที่นี่จึงเป็น query เดียวจริงๆ ไม่ใช่ลูปเรียก `read()`

    คืนเฉพาะแถวที่มีอยู่จริง **ไม่เติมค่าตั้งต้นให้ profile ที่ยังไม่มีแถว** เพราะชั้นนี้
    รายงานว่าตารางมีอะไร ส่วนรายชื่อ profile ที่ต้องแสดงเสมอเป็นเรื่องของชั้นที่แสดง
    (`cane/engine/supervisor.py`) ซึ่งเป็นที่ที่ spec ผูกกฎข้อนั้นไว้
    """
    rows = conn.execute(
        select(
            engine_state.c.profile,
            engine_state.c.should_run,
            engine_state.c.last_heartbeat_ts,
            engine_state.c.blocked_reason,
        )
    ).all()
    return {row.profile: _row_to_state(row.profile, row) for row in rows}


def set_should_run(conn: Connection, profile: str, *, should_run: bool) -> None:
    """ประกาศเจตนา — **ของคอนโซลเท่านั้น** (role `cane_console`)

    ต่างจาก `killswitch.latch()` ตรงที่ **ไม่ใช่ครั้งแรกชนะ** · เจตนามีไว้ให้ทับ
    คนกด stop แล้วกด start ใหม่ต้องได้ค่าล่าสุด ไม่ใช่ค่าที่ใครตั้งไว้ก่อน

    ไม่เอ่ยถึง `last_heartbeat_ts` หรือ `blocked_reason` แม้แต่ในค่าที่ใส่ — ดูหัวไฟล์
    """
    conn.execute(
        insert(engine_state)
        .values(profile=profile, should_run=should_run)
        .on_conflict_do_update(
            index_elements=[engine_state.c.profile],
            set_={"should_run": should_run},
        )
    )


def beat(conn: Connection, profile: str, *, blocked_reason: str | None = None) -> None:
    """บอกว่ายังเดินอยู่ — **ของ engine เท่านั้น** (role `cane_engine`)

    `blocked_reason` เขียนทุกครั้งที่เต้น ไม่ใช่เฉพาะตอนติด · การเต้นที่ปกติจึงล้าง
    เหตุของรอบก่อนให้เอง ไม่มีขั้นตอน "ปลดบล็อก" แยกที่จะมีวันลืมเรียก

    ไม่เอ่ยถึง `should_run` เลย — engine สั่งให้ตัวเองรันไม่ได้ ดูหัวไฟล์
    """
    conn.execute(
        insert(engine_state)
        .values(
            profile=profile,
            last_heartbeat_ts=now_ms(),
            blocked_reason=blocked_reason,
        )
        .on_conflict_do_update(
            index_elements=[engine_state.c.profile],
            set_={
                "last_heartbeat_ts": now_ms(),
                "blocked_reason": blocked_reason,
            },
        )
    )
