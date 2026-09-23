"""ตัวอ่านของหน้ารายงาน — สิ่งที่ VIEW `closed_trades` ตอบเองไม่ได้

VIEW รู้แค่ ledger · หน้ารายงานต้องรู้อีกสามเรื่องที่อยู่คนละตาราง:

1. **ไม้นี้ถูกตัดสินที่ config เวอร์ชันไหน** — เพื่อหาร % ด้วย bucket ของเวอร์ชันนั้น ไม่ใช่
   ของเวอร์ชันที่ active วันนี้ · ถ้าหารด้วยของวันนี้ การแก้ bucket หนึ่งครั้งจะเขียนผล
   ย้อนหลังทั้งหน้าใหม่ทั้งที่ไม่มีไม้ไหนเปลี่ยน
2. **ทุนของเวอร์ชันนั้นเท่าไร** — ผลรวม bucket ทั้งสองฝั่งของเหรียญที่เปิดใช้ (ADR 8)
3. **ไม้ที่ยังถืออยู่** — VIEW ตั้งใจไม่รวม แต่หน้ารายงานต้องแยกไว้ให้เห็นว่ามีอยู่
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, Text, and_, cast, func, select

from cane.db.repo.ledger import closed_trades_v
from cane.db.schema import config_symbols, decision_flip, decisions, fills
from cane.db.types import pct_from_db, price_from_db


@dataclass(frozen=True, slots=True)
class Origin:
    """แถว `decisions` ที่เปิดไม้หนึ่งไม้ — แท่งเดียวกัน เหรียญเดียวกัน ฝั่งเดียวกัน"""

    config_version_id: int
    size_pct: float | None
    #: แท่งที่เปิดเป็นจุดสัญญาณของฝั่งนั้นจริงหรือเปล่า · `False` ที่ไม่ใช่ cold start คือบั๊ก
    on_signal: bool
    cold_start: str | None


def origins(conn: Connection, profile: str) -> dict[str, Origin]:
    """`trade_id` → แถวที่ตัดสินเปิดไม้นั้น · ไม้ที่หาแถวไม่เจอไม่อยู่ใน dict

    แท่งเดียวกันมีได้หลายแถว (process ที่กลับมาในแท่งเดิมเขียนแถวใหม่ ไม่ทับของเดิม) ·
    เลือกแถวที่ `id` สูงสุด — กติกาเดียวกับ `latest_decision()` ที่แถวหลังชนะ
    """
    v = closed_trades_v.c
    d = decisions.c
    stmt = (
        select(
            v.trade_id,
            d.config_version_id,
            d.size_pct_final,
            d.long_signal,
            d.short_signal,
            d.cold_start,
            v.side,
        )
        .select_from(
            closed_trades_v.join(
                decisions,
                and_(
                    d.profile == v.profile,
                    d.market == v.market,
                    d.symbol == v.symbol,
                    d.bar_close_ts == v.open_bar_close_ts,
                    # `side` ของ VIEW เป็น TEXT (ตัดจาก `trade_id`) ส่วนของหัวเป็น ENUM
                    cast(d.side, Text) == v.side,
                ),
            )
        )
        .where(v.profile == profile)
        .distinct(v.trade_id)
        .order_by(v.trade_id, d.id.desc())
    )
    return {
        row.trade_id: Origin(
            config_version_id=row.config_version_id,
            size_pct=None if row.size_pct_final is None else pct_from_db(row.size_pct_final),
            on_signal=row.long_signal if row.side == "long" else row.short_signal,
            cold_start=row.cold_start,
        )
        for row in conn.execute(stmt)
    }


def capital_of(conn: Connection, version_ids: set[int]) -> dict[int, Decimal]:
    """เวอร์ชัน → ผลรวม `bucket_quote_long + bucket_quote_short` ของเหรียญที่เปิดใช้

    spot ไม่มี bucket ฝั่ง short (ADR 26) จึงนับเป็นศูนย์ ไม่ใช่ทำให้ทั้งแถวเป็น `NULL`
    """
    if not version_ids:
        return {}
    s = config_symbols.c
    stmt = (
        select(
            s.config_version_id,
            func.sum(s.bucket_quote_long + func.coalesce(s.bucket_quote_short, 0)).label("cap"),
        )
        .where(s.config_version_id.in_(version_ids), s.enabled.is_(True))
        .group_by(s.config_version_id)
    )
    return {row.config_version_id: row.cap for row in conn.execute(stmt)}


@dataclass(frozen=True, slots=True)
class OpenTrade:
    symbol: str
    side: str
    qty: float
    #: `None` เมื่อขาเปิดไม่ได้บอก leverage (ไม่ควรเกิดบน perp) — ไม่เดาเป็น 1
    margin: Decimal | None


def open_trades(conn: Connection, profile: str) -> list[OpenTrade]:
    """ไม้ที่ fill ใบสุดท้ายยังเหลือ `position_qty_after > 0` — นิยามเดียวกับ `open_trade_id()`"""
    f = fills.c
    last = (
        select(f.trade_id, f.symbol, f.position_qty_after)
        .where(f.profile == profile)
        .distinct(f.trade_id)
        .order_by(f.trade_id, f.fill_ts.desc(), f.id.desc())
        .subquery()
    )
    entry = (
        select(
            f.trade_id,
            (func.sum(f.qty * f.px) / func.sum(f.qty)).label("entry_px"),
            func.max(f.leverage).label("leverage"),
        )
        .where(f.profile == profile, f.leg == "open")
        .group_by(f.trade_id)
        .subquery()
    )
    stmt = (
        select(last.c.trade_id, last.c.symbol, last.c.position_qty_after,
               entry.c.entry_px, entry.c.leverage)
        .select_from(last.join(entry, entry.c.trade_id == last.c.trade_id))
        .where(last.c.position_qty_after > 0)
        .order_by(last.c.symbol)
    )
    return [
        OpenTrade(
            symbol=row.symbol,
            side=row.trade_id.split(":")[2],
            qty=price_from_db(row.position_qty_after),
            margin=(
                None
                if not row.leverage
                else row.position_qty_after * row.entry_px / row.leverage
            ),
        )
        for row in conn.execute(stmt)
    ]


@dataclass(frozen=True, slots=True)
class FlipCount:
    total: int
    completed: int


def flips(conn: Connection, profile: str, *, since_ts: int | None, until_ts: int | None) -> FlipCount:
    """การกลับข้างในช่วงนั้น กับจำนวนที่ครบทั้งสองขา · ขอบเวลาเป็น `bar_close_ts` รวมทั้งสองข้าง"""
    stmt = (
        select(
            func.count().label("total"),
            func.count().filter(decision_flip.c.aborted.is_(False)).label("completed"),
        )
        .select_from(
            decision_flip.join(
                decisions,
                and_(
                    decisions.c.id == decision_flip.c.decision_id,
                    decisions.c.profile == decision_flip.c.profile,
                ),
            )
        )
        .where(decisions.c.profile == profile)
    )
    if since_ts is not None:
        stmt = stmt.where(decisions.c.bar_close_ts >= since_ts)
    if until_ts is not None:
        stmt = stmt.where(decisions.c.bar_close_ts <= until_ts)
    row = conn.execute(stmt).one()
    return FlipCount(total=row.total, completed=row.completed)
