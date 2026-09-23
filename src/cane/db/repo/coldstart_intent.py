"""เจตนาเลือกเส้นทาง cold start ของ run ถัดไป — ใช้แล้วหายไป (ADR 35)

คอนโซลเขียนด้วย `choose()` · engine อ่านแล้วลบด้วย `consume()` ที่แท่งแรกของ run ของเหรียญนั้น ·
สิทธิ์ของสองทางนี้ถูกบังคับที่ฐาน (migration 0013) ไม่ใช่ที่นี่
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, and_, select
from sqlalchemy.dialects.postgresql import insert

from cane.db.schema import cold_start_intent as intent_t
from cane.db.types import store_symbol

#: ทางที่เลือกได้วันนี้ · `wait_1h` ยังไม่มีใน engine (ADR 35 §ตัดสินแล้ว) — ตรงกับ CHECK ของ 0013
ROUTES = ("trailing", "skip")


@dataclass(frozen=True, slots=True)
class Intent:
    profile: str
    market: str
    symbol: str
    route: str
    #: ชื่อคนเลือก ณ ตอนเลือก · id อยู่ที่ `user_audit_log`
    chosen_by: str
    chosen_ts: int


def _key(profile: str, market: str, symbol: str):  # noqa: ANN202
    return and_(
        intent_t.c.profile == profile,
        intent_t.c.market == market,
        intent_t.c.symbol == store_symbol(symbol),
    )


def choose(
    conn: Connection, *, profile: str, market: str, symbol: str, route: str, by: str,
    now: int,
) -> bool:
    """เลือกทาง · คืน `True` เมื่อเจตนาเปลี่ยนจริง

    เลือกซ้ำค่าเดิม = no-op ที่ไม่ขยับ `chosen_ts` และไม่เปลี่ยนคนเลือก (spec/10 §6. สัญญาของ API
    — "เลือกซ้ำค่าเดิม = no-op") · เลือกค่าใหม่ = ทับ
    """
    if route not in ROUTES:
        raise ValueError(f"เส้นทางต้องเป็นหนึ่งใน {ROUTES} ไม่ใช่ {route!r}")
    stmt = (
        insert(intent_t)
        .values(
            profile=profile, market=market, symbol=store_symbol(symbol), route=route,
            chosen_by=by, chosen_ts=now,
        )
        .on_conflict_do_update(
            index_elements=[intent_t.c.profile, intent_t.c.market, intent_t.c.symbol],
            set_={"route": route, "chosen_by": by, "chosen_ts": now},
            where=intent_t.c.route != route,
        )
        # `RETURNING` ไม่ใช่ `rowcount` — แถวที่ `WHERE` ของ `DO UPDATE` ตัดทิ้งไม่ถูกคืน จึงแยก
        # "เปลี่ยน" ออกจาก "ค่าเดิม" ได้ตรงๆ โดยไม่พึ่งว่า driver นับแถวของ upsert อย่างไร
        .returning(intent_t.c.route)
    )
    return conn.execute(stmt).first() is not None


def read(conn: Connection, *, profile: str, market: str, symbol: str) -> Intent | None:
    """เจตนาที่รออยู่ของเหรียญนี้ พร้อมชื่อคนเลือก · ไม่มี = ใช้ config"""
    row = conn.execute(select(intent_t).where(_key(profile, market, symbol))).one_or_none()
    if row is None:
        return None
    return Intent(
        profile=row.profile, market=row.market, symbol=row.symbol, route=row.route,
        chosen_by=row.chosen_by, chosen_ts=row.chosen_ts,
    )


def consume(conn: Connection, *, profile: str, market: str, symbol: str) -> str | None:
    """อ่านแล้วลบในคำสั่งเดียว · คืนทางที่เลือกไว้ หรือ `None` ถ้าไม่มีใครเลือก

    **ของ engine เท่านั้น** (role `cane_engine` มี `DELETE` · คอนโซลไม่มี) · ผู้เรียกต้องเรียกใน
    ทรานแซกชันเดียวกับที่เขียนแถว `decisions` ของแท่งนั้น ไม่งั้นเจตนาอาจหายไปทั้งที่แท่งนั้น
    ไม่ได้ถูกบันทึก
    """
    return conn.execute(
        intent_t.delete().where(_key(profile, market, symbol)).returning(intent_t.c.route)
    ).scalar_one_or_none()
