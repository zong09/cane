"""ความคืบหน้าของ replay ย้อนหลัง — แถวดิบต่อ profile ไม่ใช่สถานะ (ADR 29)

## เป็นตัวบอกความคืบหน้ากับตัวกันรันซ้ำ ไม่ใช่จุด resume

`as_of_ms` บอกว่า replay เดินเสร็จถึงแท่งไหน `end_ts` บอกว่ากำลังไปถึงไหน · แต่เงินสดกับ
สถานะไม้ของ replay อยู่ในหน่วยความจำของ `PaperBroker` ไม่ได้อยู่ในฐาน จึงเอาแถวนี้ไปพา
process ใหม่เดินต่อจากกลางทางไม่ได้ — process ตายแล้วต้องเริ่มใหม่ใน scratch database
ที่สร้างใหม่ · แถวนี้มีไว้กันไม่ให้ replay ซ้ำใน scratch เดิม เพราะ `insert_decision` ไม่มี
`ON CONFLICT` (ADR 27 §27.1) รันซ้ำแล้วทุกแท่งจะมีสองแถวโดยไม่มีอะไรฟ้อง

## ไม่มีคอลัมน์ "สถานะ" และจะไม่มี

ไม่มี `running` / `finished` — คนอ่านคิดเองจาก `as_of_ms >= end_ts` · เก็บสถานะลงตาราง
เมื่อไหร่ก็จะได้ค่าที่ค้างอยู่ตอน process ตายกลางทาง ซึ่งเป็นการโกหกชนิดเดียวกับที่
`engine_state` เลี่ยงไว้

## สิทธิ์ระดับคอลัมน์ (migration 0011)

engine เขียน `as_of_ms` / `updated_ts` ได้ แต่เปลี่ยน `end_ts` / `profile` ไม่ได้ · console อ่านได้
อย่างเดียว · ไฟล์นี้ไม่ commit — ผู้เรียกเป็นเจ้าของทรานแซกชันตามสัญญาของชั้น repo
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select, update
from sqlalchemy.dialects.postgresql import insert

from cane.db.schema import replay_cursor
from cane.db.types import now_ms


@dataclass(frozen=True, slots=True)
class ReplayCursor:
    """แถวตามที่มันเป็น · `as_of_ms >= end_ts` คือ replay เดินถึงปลายทางแล้ว"""

    profile: str
    as_of_ms: int
    end_ts: int
    updated_ts: int


class ReplayCursorExists(Exception):
    """มี replay รันไปแล้วใน scratch นี้ — ต้อง drop database แล้วสร้างใหม่"""


def read(conn: Connection, profile: str) -> ReplayCursor | None:
    """แถวของ profile เดียว · ไม่มีแถว = ยังไม่เคยรัน replay ใน scratch นี้"""
    row = conn.execute(
        select(
            replay_cursor.c.as_of_ms,
            replay_cursor.c.end_ts,
            replay_cursor.c.updated_ts,
        ).where(replay_cursor.c.profile == profile)
    ).one_or_none()
    if row is None:
        return None
    return ReplayCursor(
        profile=profile,
        as_of_ms=row.as_of_ms,
        end_ts=row.end_ts,
        updated_ts=row.updated_ts,
    )


def start(conn: Connection, profile: str, *, as_of_ms: int, end_ts: int) -> None:
    """ประกาศจุดเริ่มของ replay — **ของ engine เท่านั้น** (role `cane_engine`)

    ครั้งแรกชนะ · มีแถวอยู่แล้วยก `ReplayCursorExists` เพื่อกันการรันซ้ำใน scratch เดิม
    ไม่ใช่เขียนทับ เพราะการเขียนทับจะทำให้ replay ที่รันไปแล้วดูเหมือนยังไม่เคยรัน
    """
    # นับด้วย `RETURNING` ไม่ใช่ `rowcount` — psycopg 3 คืน -1 เมื่อไม่มีแถวเข้า (ดู `bars.insert_bars`)
    inserted = conn.execute(
        insert(replay_cursor)
        .values(
            profile=profile,
            as_of_ms=as_of_ms,
            end_ts=end_ts,
            updated_ts=now_ms(),
        )
        .on_conflict_do_nothing(index_elements=[replay_cursor.c.profile])
        .returning(replay_cursor.c.profile)
    ).all()
    if not inserted:
        raise ReplayCursorExists(
            f"profile {profile!r} เคยรัน replay ใน database นี้แล้ว — "
            "ต้อง drop scratch database แล้วสร้างใหม่ก่อนรันอีกครั้ง"
        )


def advance(conn: Connection, profile: str, as_of_ms: int) -> None:
    """เลื่อนความคืบหน้าไปข้างหน้า — **ของ engine เท่านั้น** (role `cane_engine`)

    `WHERE as_of_ms < :new` กันไม่ให้ถอยหลังหรืออยู่ที่เดิม · `SET` เอ่ยถึงแค่ `as_of_ms`
    กับ `updated_ts` เพราะ engine มีสิทธิ์ UPDATE สองคอลัมน์นี้เท่านั้น · เลื่อนเลย `end_ts`
    ไม่ต้องดักที่นี่ — CHECK `end_ts >= as_of_ms` ของตารางปฏิเสธเอง
    """
    moved = conn.execute(
        update(replay_cursor)
        .where(
            replay_cursor.c.profile == profile,
            replay_cursor.c.as_of_ms < as_of_ms,
        )
        .values(as_of_ms=as_of_ms, updated_ts=now_ms())
        .returning(replay_cursor.c.as_of_ms)
    ).all()
    if not moved:
        raise ValueError(
            f"เลื่อนความคืบหน้าของ {profile!r} ไปที่ {as_of_ms} ไม่ได้ — "
            "ไม่มีแถว หรือค่าใหม่ไม่ได้อยู่หน้าค่าเดิม"
        )
