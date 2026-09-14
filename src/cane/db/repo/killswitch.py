"""สวิตช์หยุดฉุกเฉิน — latched ติดแล้วติดเลย ปลดด้วยมือเท่านั้น (spec/06)

## `latch()` ยิงซ้ำต้องไม่เคยล้มเหลว

spec/10:186 เขียนไว้ตรงๆ ว่าการกดหยุดฉุกเฉินซ้ำคืน 200 ไม่ใช่ error · เหตุผลอยู่ที่
สถานการณ์ที่ปุ่มนี้ถูกใช้จริง: มีบางอย่างผิดปกติ คนกำลังกดรัว ถ้าครั้งที่สองโยน
exception คนจะไม่รู้ว่าครั้งแรกติดหรือไม่ติด · **ครั้งแรกชนะ** — เหตุผลและคนกดของ
ครั้งแรกถูกเก็บไว้ ครั้งต่อๆ มาไม่เขียนทับ เพราะสาเหตุแรกคือสาเหตุที่อธิบายเหตุการณ์

## `unlatch()` ที่นี่ไม่ตรวจการพิมพ์ชื่อ profile

spec/06 บอกว่าต้องพิมพ์ชื่อ profile ยืนยัน — นั่นเป็นด่านของ **คอนโซล** (ใบ 23) ไม่ใช่
ของชั้นนี้ · ชั้นนี้มีด่านที่แข็งกว่าและอยู่ต่ำกว่า: trigger `kill_switch_guard`
ที่ฐานปฏิเสธการปลดเมื่อผู้กระทำไม่ใช่ `cane_console` ต่อให้โค้ดเรียกฟังก์ชันนี้จาก
เส้นทางของ engine มันก็ปลดไม่ได้

## ไม่มีแถว = ไม่ latched

profile ที่ยังไม่เคยถูกกดสวิตช์เลยไม่มีแถว · `is_latched()` จึงคืน `False` ไม่ใช่โยน
exception — สวิตช์ที่ไม่เคยถูกแตะคือสวิตช์ที่ไม่ได้กด ซึ่งเป็นคำตอบที่ถูกต้อง ไม่ใช่
สถานะที่หายไป
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from cane.db.schema import kill_switch
from cane.db.types import now_ms


@dataclass(frozen=True, slots=True)
class KillSwitch:
    """สภาพของสวิตช์ ณ ตอนอ่าน · `latched_ts`/`reason` มีค่าเมื่อ `latched` เท่านั้น"""

    profile: str
    latched: bool
    latched_by: str | None = None
    latched_ts: int | None = None
    reason: str | None = None


def read(conn: Connection, profile: str) -> KillSwitch:
    """สภาพปัจจุบัน · ไม่มีแถว = ไม่ latched (ดูหัวไฟล์)"""
    row = conn.execute(
        select(
            kill_switch.c.latched,
            kill_switch.c.latched_by,
            kill_switch.c.latched_ts,
            kill_switch.c.reason,
        ).where(kill_switch.c.profile == profile)
    ).one_or_none()
    if row is None:
        return KillSwitch(profile=profile, latched=False)
    return KillSwitch(
        profile=profile,
        latched=row.latched,
        latched_by=row.latched_by,
        latched_ts=row.latched_ts,
        reason=row.reason,
    )


def is_latched(conn: Connection, profile: str) -> bool:
    """ทางลัดของ `read().latched` — ชั้น risk เรียกก่อนยิง**ทุก**ออเดอร์"""
    return read(conn, profile).latched


def latch(
    conn: Connection, profile: str, *, reason: str, by: str | None = None
) -> KillSwitch:
    """กดสวิตช์ · **ยิงซ้ำไม่ล้มเหลวและไม่เขียนทับเหตุผลเดิม** (ดูหัวไฟล์)

    `reason` บังคับเพราะ CHECK ของตารางบังคับ — สวิตช์ที่ติดโดยไม่มีที่มาคือสิ่งที่
    คนอ่านคอนโซลแล้วไม่กล้าปลดและไม่กล้าปล่อยไว้
    """
    if not reason:
        raise ValueError("latch ต้องมีเหตุผล — สวิตช์ที่ติดโดยไม่มีที่มาปลดไม่ลง")

    now = now_ms()
    statement = (
        insert(kill_switch)
        .values(
            profile=profile,
            latched=True,
            latched_by=by,
            latched_ts=now,
            reason=reason,
            updated_ts=now,
        )
        # ครั้งแรกชนะ · แถวที่ latched อยู่แล้วไม่ถูกแตะเลย (`where` ตัดออกไป) แถวที่
        # ยังไม่ latched ถูกยกขึ้น — `DO UPDATE ... WHERE` จึงเป็น no-op จริงๆ ไม่ใช่
        # การเขียนค่าเดิมทับ ซึ่งจะทำให้ `updated_ts` ขยับทุกครั้งที่มีคนกดรัว
        .on_conflict_do_update(
            index_elements=[kill_switch.c.profile],
            set_={
                "latched": True,
                "latched_by": by,
                "latched_ts": now,
                "reason": reason,
                "updated_ts": now,
            },
            where=kill_switch.c.latched.is_(False),
        )
    )
    conn.execute(statement)
    return read(conn, profile)


def unlatch(conn: Connection, profile: str) -> KillSwitch:
    """ปลดสวิตช์ · **ฐานปฏิเสธถ้าผู้กระทำไม่ใช่ `cane_console`** (trigger ของ 0008)

    ยิงตอนที่ยังไม่ latched เป็น no-op คืนสภาพปัจจุบัน — เหตุผลเดียวกับ `latch()`
    คือปุ่มความปลอดภัยต้องไม่ทำให้คนสับสนว่ากดติดหรือเปล่า
    """
    conn.execute(
        kill_switch.update()
        .where(kill_switch.c.profile == profile)
        .values(latched=False, latched_ts=None, reason=None, updated_ts=now_ms())
    )
    return read(conn, profile)
