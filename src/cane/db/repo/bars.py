"""ตาราง `bars` — แท่งที่ปิดแล้ว เข้าแล้วไม่แก้

แทน `data/cache.py` (JSON ไฟล์ต่อ symbol/timeframe) ที่จะเลิกใช้ตอนแก้ใบ 02
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Connection, select
from sqlalchemy.dialects.postgresql import insert

from cane.data.ohlcv import Bar
from cane.db.schema import bars as bars_t
from cane.db.types import now_ms, price_from_db, price_to_db, store_symbol


def insert_bars(
    conn: Connection,
    market: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    *,
    created_ts: int | None = None,
) -> int:
    """เขียนแท่งลงตาราง ซ้ำแล้วข้าม — คืนจำนวนแถวที่เข้าใหม่จริง

    **`ON CONFLICT DO NOTHING` ไม่ใช่ upsert** สองเหตุผลที่ต้องเป็นทางนี้:

    1. แท่งที่ปิดแล้วไม่เปลี่ยนอีก (spec/07) ถ้า feed ส่งค่าใหม่มาสำหรับแท่งเดิม
       สิ่งที่เกิดคือ feed ขัดกับตัวเอง การเขียนทับจะทำให้ค่าที่ indicator เคย
       คำนวณไปแล้วต่างจากค่าที่บันทึกไว้ — ไล่ย้อนหลังไม่ได้เลย
    2. role `cane_engine` ไม่มีสิทธิ์ `UPDATE` `DO UPDATE` จึงถูก DB ปฏิเสธอยู่แล้ว
       ข้อบังคับกับตรรกะจึงพูดตรงกัน ไม่ใช่ตรรกะที่ใจดีกว่าที่ DB ยอม

    ผู้เรียกต้องกรองแท่งที่ยังวิ่งอยู่ออกก่อน (`closed_as_of()`) — ตารางนี้ไม่รู้
    เวลานาฬิกาของผู้เรียก จึงกรองซ้ำให้ไม่ได้ เหมือนที่ `BarCache` เคยเป็น

    `market` **ไม่มีค่าตั้งต้น** เพราะ `store_symbol()` ตัด `:USDT` ทิ้ง — `BTC/USDT`
    บน spot กับบน perp จึงมีชื่อเดียวกันในตาราง ตลาดที่เดาผิดไม่ได้ทำให้เขียนล้ม
    แต่ทำให้แท่งของสองตลาดปนกันเงียบๆ แล้ว indicator คำนวณบนข้อมูลที่ไม่มีอยู่จริง
    """
    if not bars:
        return 0
    stamp = now_ms() if created_ts is None else created_ts
    name = store_symbol(symbol)
    rows = [
        {
            "market": market,
            "symbol": name,
            "timeframe": timeframe,
            "open_ts": bar.open_ts,
            "close_ts": bar.close_ts,
            "open": price_to_db(bar.open),
            "high": price_to_db(bar.high),
            "low": price_to_db(bar.low),
            "close": price_to_db(bar.close),
            "volume": price_to_db(bar.volume),
            "created_ts": stamp,
        }
        for bar in bars
    ]
    # นับด้วย `RETURNING` ไม่ใช่ `rowcount` — การ insert หลายแถวครั้งเดียวคืน
    # `rowcount = -1` (driver ไม่รับประกันค่านี้ในโหมด executemany) ซึ่งถ้าเชื่อไป
    # จำนวนแท่งใหม่จะกลายเป็นตัวเลขที่ดูเหมือนข้อมูลแต่ไม่ใช่
    stmt = insert(bars_t).on_conflict_do_nothing().returning(bars_t.c.open_ts)
    return len(conn.execute(stmt, rows).all())


def _to_bar(row) -> Bar:  # noqa: ANN001 — Row ของ SQLAlchemy ไม่มีชนิดที่ระบุคอลัมน์ได้
    """`NUMERIC` → `float` **ที่นี่ที่เดียว** ก่อนออกไปเป็น `Bar`

    สูตร Action Zone ถูกเทียบกับ TradingView ด้วย golden test (#04) ซึ่งคำนวณบน
    เลขทศนิยมฐานสอง การส่ง `Decimal` ออกไปจะทำให้ผลต่างกันในหลักท้ายและเทียบไม่ผ่าน
    """
    return Bar(
        open_ts=row.open_ts,
        close_ts=row.close_ts,
        open=price_from_db(row.open),
        high=price_from_db(row.high),
        low=price_from_db(row.low),
        close=price_from_db(row.close),
        volume=price_from_db(row.volume),
    )


def closed_bars(
    conn: Connection,
    market: str,
    symbol: str,
    timeframe: str,
    as_of: int,
    *,
    limit: int | None = None,
) -> list[Bar]:
    """แท่งที่ปิดแล้ว ณ เวลา `as_of` เรียงเก่า → ใหม่

    เทียบ `close_ts < as_of` **แบบเดียวกับ `closed_as_of()`** ของ `data/ohlcv.py`
    ไม่ใช่ `<=` — กฎเดียวกันอยู่สองที่โดยจำเป็น (ที่นี่กรองใน SQL เพื่อไม่ต้องโหลด
    ประวัติทั้งก้อนขึ้นมา) จึงมีเทสต์ที่ยืนยันว่าสองทางให้ผลตรงกันที่ขอบพอดี
    ถ้าวันหนึ่งกฎเปลี่ยน เทสต์ตัวนั้นจะเป็นตัวที่ดังก่อน

    `limit` เอา **N แท่งท้าย** (ใหม่สุด) ไม่ใช่ N แท่งแรก เพราะผู้เรียกทุกรายต้องการ
    ของล่าสุด (#04 ใช้ 130 แท่งเพื่อ warm-up EMA, การตัดสินใจใช้ 85)
    """
    stmt = (
        select(bars_t)
        .where(
            bars_t.c.market == market,
            bars_t.c.symbol == store_symbol(symbol),
            bars_t.c.timeframe == timeframe,
            bars_t.c.close_ts < as_of,
        )
        .order_by(bars_t.c.open_ts.desc() if limit is not None else bars_t.c.open_ts)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = conn.execute(stmt).all()
    if limit is not None:
        rows.reverse()
    return [_to_bar(row) for row in rows]


def last_bar(conn: Connection, market: str, symbol: str, timeframe: str) -> Bar | None:
    """แท่งที่ใหม่ที่สุดในตาราง — ใช้ตั้ง `since` ของการดึงรอบถัดไป

    **ไม่กรอง `as_of`** โดยเจตนา: คำถามนี้คือ "เรามีของถึงไหนแล้ว" ซึ่งเป็นเรื่องของ
    การดึงข้อมูล ไม่ใช่เรื่องของการตัดสินใจ · replay ที่มี `as_of` อยู่ในอดีตขณะที่
    ตารางล้ำหน้าไปแล้ว ต้องไม่ทำให้ระบบดึงข้อมูลเก่าซ้ำทั้งชุด
    """
    stmt = (
        select(bars_t)
        .where(
            bars_t.c.market == market,
            bars_t.c.symbol == store_symbol(symbol),
            bars_t.c.timeframe == timeframe,
        )
        .order_by(bars_t.c.open_ts.desc())
        .limit(1)
    )
    row = conn.execute(stmt).first()
    return None if row is None else _to_bar(row)
