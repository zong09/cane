"""ตาราง `funding_observations` — บันทึกทั้งตอนได้ค่าและตอนดึงไม่ได้

`data/funding.py` คืน `FundingRate` ที่ `rate is None` หมายถึง "ไม่มีข้อมูล" ไม่ใช่
ศูนย์ · ตารางนี้บังคับความต่างนั้นด้วย `CHECK` ระดับ schema เขียน `rate = 0` พร้อม
`unavailable_reason` ไม่ได้เลย — ต้นทุนที่หายต้องหายเสียงดัง
"""

from __future__ import annotations

from sqlalchemy import Connection, select

from cane.data.funding import FundingRate
from cane.db.schema import funding_observations as obs_t
from cane.db.types import now_ms, rate_from_db, rate_to_db, store_symbol


def record_observation(
    conn: Connection,
    observation: FundingRate,
    *,
    observed_ts: int | None = None,
    created_ts: int | None = None,
) -> None:
    """เขียนการสังเกตหนึ่งครั้ง — **ไม่ dedupe** โดยเจตนา

    การสังเกตซ้ำที่เวลาเดียวกันคือหลักฐานว่าโปรเซสรีสตาร์ทกลางแท่งแล้วดึงใหม่
    ไม่ใช่ข้อเท็จจริงเดียวกันสองใบ ถ้าใส่ UNIQUE + upsert แถวแรกจะถูกทับและร่องรอย
    การรีสตาร์ทจะหายไป · ตัวที่ต้อง dedupe จริงคือ `funding_charges` (เงินที่ถูกหัก)
    ซึ่งอยู่ในใบ 11 ไม่ใช่ตารางนี้ที่เป็นแค่การอ่านค่า
    """
    stamp = now_ms()
    conn.execute(
        obs_t.insert().values(
            symbol=store_symbol(observation.symbol),
            observed_ts=stamp if observed_ts is None else observed_ts,
            rate=None if observation.rate is None else rate_to_db(observation.rate),
            next_funding_ts=observation.next_funding_ts,
            unavailable_reason=observation.unavailable_reason,
            created_ts=stamp if created_ts is None else created_ts,
        )
    )


def latest_observation(conn: Connection, symbol: str) -> FundingRate | None:
    """การสังเกตล่าสุดของ symbol · ไม่มีเลย = `None`

    ตัดสินด้วย `observed_ts` ก่อน แล้ว `id` เป็นตัวตัดสินเสมอกัน — สองแถวที่เวลา
    เดียวกันเกิดได้ (ดู `record_observation`) และตัวที่เขียนหลังคือตัวที่ควรถูกอ่าน

    คืน `FundingRate` ที่ `rate is None` ตามที่บันทึกไว้ ไม่แปลงเป็น 0 ให้เอง —
    ผู้เรียกต้องเห็นว่า "ไม่มีข้อมูล" ต่างจาก "อัตราเป็นศูนย์"
    """
    stmt = (
        select(obs_t)
        .where(obs_t.c.symbol == store_symbol(symbol))
        .order_by(obs_t.c.observed_ts.desc(), obs_t.c.id.desc())
        .limit(1)
    )
    row = conn.execute(stmt).first()
    if row is None:
        return None
    return FundingRate(
        symbol=row.symbol,
        rate=None if row.rate is None else rate_from_db(row.rate),
        next_funding_ts=row.next_funding_ts,
        unavailable_reason=row.unavailable_reason,
    )
