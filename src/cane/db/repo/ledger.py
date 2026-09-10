"""ตาราง `fills` และ `funding_charges` — สิ่งที่เกิดขึ้นจริงที่ปลายทาง

`decisions` (ใบ 03) เก็บว่าระบบ**ตั้งใจ**ทำอะไร ไฟล์นี้เก็บว่าเกิดอะไรขึ้นจริง:
ราคาที่ได้ ค่าธรรมเนียมที่ถูกหัก funding ที่จ่ายไปแล้ว · รายงาน "ไม้ที่ปิดแล้ว"
derive จากบันทึกการตัดสินใจอย่างเดียวไม่ได้ (spec/07:186)

**นิยามของ `dedupe_key` อยู่ที่นี่ที่เดียว** — `UNIQUE (profile, dedupe_key)` ดีได้
เท่ากับนิยามของคีย์เท่านั้น ถ้าปล่อยให้แต่ละที่คิดเอง ตารางจะรับของซ้ำโดยที่
constraint ยังเขียวอยู่
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection, and_, select

from cane.db.schema import fills as fills_t
from cane.db.schema import funding_charges as funding_t
from cane.db.types import (
    now_ms,
    pct_from_db,
    pct_to_db,
    price_from_db,
    price_to_db,
    rate_from_db,
    rate_to_db,
    store_symbol,
)

#: ขาที่ปิดไม้ — ทั้งสองขาต้องมี `exit_reason` (CHECK ของ `fills`)
CLOSING_LEGS = ("close", "stop")


def trade_id_of(market: str, symbol: str, side: str, open_bar_close_ts: int) -> str:
    """`{market}:{SYMBOL}:{side}:{open_bar_close_ts}` — กุญแจของไม้หนึ่งไม้

    **ไม่มีตาราง `trades`** และไม่ต้องมี · บัญชีเป็น one-way (ADR 20 ของ config,
    spec/03) ไม้หนึ่งไม้จึงถูกระบุครบด้วย "เหรียญไหน ตลาดไหน ฝั่งไหน เปิดตอนแท่งไหน"
    อยู่แล้ว การตั้งตารางเพื่อออกเลขให้สิ่งที่ derive ได้คือแหล่งความจริงที่สอง
    ซึ่ง ADR 24 กันไว้

    `side` เป็นฝั่งของ**ไม้** (`long`/`short`) ไม่ใช่ฝั่งของออเดอร์ — ต่างจาก
    `client_order_id` ที่ใช้ฝั่งของออเดอร์ เพราะสองอย่างนี้ตอบคนละคำถาม: id ของ
    ออเดอร์ต้องแยกสองขาของ flip ที่เป็น `buy` เหมือนกัน ส่วนกุญแจของไม้ต้องบอกว่า
    ไม้นั้นถือฝั่งไหนอยู่

    ใช้รูปสั้นของ symbol เหมือนทุกตารางในระบบ (`store_symbol()`)
    """
    if side not in ("long", "short"):
        raise ValueError(f"side ของไม้ต้องเป็น long หรือ short ไม่ใช่ {side!r}")
    return f"{market}:{store_symbol(symbol)}:{side}:{open_bar_close_ts}"


def dedupe_key_of(client_order_id: str, seq: int = 0, venue_fill_id: str | None = None) -> str:
    """คีย์ที่ทำให้เขียน fill เดิมซ้ำไม่ได้ (spec/06 · spec/08 ขั้น 3)

    - **มี `venue_fill_id`** → ใช้ค่านั้นตรงๆ เพราะเป็นคีย์ที่ venue ออกให้เอง และเป็น
      ค่าเดียวกันทุกครั้งที่อ่านสถานะเดิมซ้ำ
    - **ไม่มี** (`PaperBroker` และ venue ที่ไม่คืน id) → `{client_order_id}#{seq}`
      โดย `seq` คือลำดับของ fill ภายในออเดอร์ใบเดียวกัน เริ่มที่ 0 · `client_order_id`
      เป็น deterministic อยู่แล้ว คีย์ที่ได้จึง**ซ้ำโดยตั้งใจ**เมื่อ process ตายกลางคัน
      แล้วกลับมาในแท่งเดิม ซึ่งคือทั้งหมดที่คีย์นี้มีหน้าที่ทำ

    ออเดอร์หนึ่งใบ fill เป็นหลายก้อนได้จริง `seq` จึงไม่ใช่ของประดับ — ถ้าไม่มี
    ก้อนที่สองของออเดอร์เดียวกันจะถูกฐานปฏิเสธในฐานะของซ้ำ ทั้งที่เป็นเงินคนละก้อน
    """
    if venue_fill_id is not None:
        return venue_fill_id
    if seq < 0:
        raise ValueError(f"seq ต้องไม่ติดลบ ไม่ใช่ {seq}")
    return f"{client_order_id}#{seq}"


@dataclass(frozen=True, slots=True)
class Fill:
    """หนึ่ง fill ที่เกิดขึ้นจริง — รูปเดียวกับแถวใน `fills`

    ราคาและปริมาณเป็น `float` (เข้าสูตรต่อ) ส่วน `fee_quote` เป็น `Decimal` เพราะมัน
    ถูกบวกสะสมจนต้องตรงกับใบแจ้งของ venue (`db/types.py`)
    """

    profile: str
    market: str
    symbol: str
    trade_id: str
    leg: str
    fill_ts: int
    px: float
    qty: float
    client_order_id: str
    order_type: str
    reduce_only: bool
    position_qty_after: float
    bar_close_ts: int
    dedupe_key: str
    ref_px: float | None = None
    fee_quote: Decimal | None = None
    fee_ccy: str | None = None
    fee_unavailable_reason: str | None = None
    venue_fill_id: str | None = None
    leverage: float | None = None
    exit_reason: str | None = None
    exit_detail: str | None = None

    def __post_init__(self) -> None:
        # CHECK ของตารางบังคับเรื่องนี้อยู่แล้ว ตรวจซ้ำที่นี่เพื่อให้พังตรงจุดที่
        # ประกอบค่าผิด ไม่ใช่ตอน insert ที่ traceback ชี้ไปที่ SQL
        closing = self.leg in CLOSING_LEGS
        if closing and self.exit_reason is None:
            raise ValueError(f"ขา {self.leg} ต้องมี exit_reason")
        if not closing and self.exit_reason is not None:
            raise ValueError("ขาเปิดต้องไม่มี exit_reason")


@dataclass(frozen=True, slots=True)
class FundingCharge:
    """funding หนึ่งรอบที่ถูกหักจากไม้หนึ่งไม้ — หรือเหตุผลที่ไม่รู้ว่าหักเท่าไร"""

    profile: str
    symbol: str
    trade_id: str
    cycle_ts: int
    position_qty: float
    market: str = "usdtm_perp"
    rate: float | None = None
    amount_quote: Decimal | None = None
    mark_px: float | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        known = self.rate is not None and self.amount_quote is not None
        if known == (self.unavailable_reason is not None):
            raise ValueError(
                "แถว funding ต้องบอกว่าหักไปเท่าไร หรือบอกว่าทำไมไม่รู้ อย่างใดอย่างหนึ่ง"
            )


def record_fill(conn: Connection, fill: Fill, *, created_ts: int | None = None) -> None:
    """เขียนหนึ่ง fill — **ของซ้ำต้องดัง ไม่ใช่ถูกกลืน**

    ไม่มี `ON CONFLICT DO NOTHING` ที่นี่โดยเจตนา ต่างจาก `bars` ที่แท่งเดิมซ้ำเป็น
    เรื่องปกติของการดึงข้อมูลทับช่วง · fill ที่ซ้ำแปลว่าชั้นบนกำลังจะคิดเงินก้อนเดิม
    สองครั้ง ผู้เรียกต้องเห็นและตัดสินใจเอง (reconcile ของ spec/08 ขั้น 3 รู้อยู่แล้ว
    ว่ากำลังอ่านของเดิมซ้ำ จึงเช็คด้วย `has_fill()` ก่อน ไม่ใช่เขียนแล้วกลืน error)
    """
    stamp = now_ms()
    conn.execute(
        fills_t.insert().values(
            profile=fill.profile,
            market=fill.market,
            symbol=store_symbol(fill.symbol),
            trade_id=fill.trade_id,
            leg=fill.leg,
            fill_ts=fill.fill_ts,
            px=price_to_db(fill.px),
            qty=price_to_db(fill.qty),
            ref_px=None if fill.ref_px is None else price_to_db(fill.ref_px),
            fee_quote=None if fill.fee_quote is None else price_to_db(fill.fee_quote),
            fee_ccy=fill.fee_ccy,
            fee_unavailable_reason=fill.fee_unavailable_reason,
            venue_fill_id=fill.venue_fill_id,
            client_order_id=fill.client_order_id,
            order_type=fill.order_type,
            reduce_only=fill.reduce_only,
            position_qty_after=price_to_db(fill.position_qty_after),
            leverage=None if fill.leverage is None else pct_to_db(fill.leverage),
            exit_reason=fill.exit_reason,
            exit_detail=fill.exit_detail,
            bar_close_ts=fill.bar_close_ts,
            dedupe_key=fill.dedupe_key,
            created_ts=stamp if created_ts is None else created_ts,
        )
    )


def has_fill(conn: Connection, profile: str, dedupe_key: str) -> bool:
    """เคยเขียน fill นี้ไปแล้วหรือยัง — ด่านที่ reconcile เรียกก่อน `record_fill()`"""
    stmt = select(fills_t.c.id).where(
        and_(fills_t.c.profile == profile, fills_t.c.dedupe_key == dedupe_key)
    )
    return conn.execute(stmt).first() is not None


def record_funding_charge(
    conn: Connection, charge: FundingCharge, *, created_ts: int | None = None
) -> None:
    """เขียน funding หนึ่งรอบ — `UNIQUE (profile, trade_id, cycle_ts)` กันหักซ้ำ"""
    stamp = now_ms()
    conn.execute(
        funding_t.insert().values(
            profile=charge.profile,
            market=charge.market,
            symbol=store_symbol(charge.symbol),
            trade_id=charge.trade_id,
            cycle_ts=charge.cycle_ts,
            rate=None if charge.rate is None else rate_to_db(charge.rate),
            amount_quote=(
                None if charge.amount_quote is None else price_to_db(charge.amount_quote)
            ),
            position_qty=price_to_db(charge.position_qty),
            mark_px=None if charge.mark_px is None else price_to_db(charge.mark_px),
            unavailable_reason=charge.unavailable_reason,
            created_ts=stamp if created_ts is None else created_ts,
        )
    )


def open_trade_id(
    conn: Connection, profile: str, market: str, symbol: str, side: str
) -> str | None:
    """กุญแจของไม้ที่ยัง**เปิดค้าง**อยู่ของเหรียญนี้ฝั่งนี้ · ไม่มี = `None`

    ขาปิดต้องเขียน `trade_id` เดียวกับขาเปิด แต่ผู้ที่กำลังปิดไม่จำเป็นต้องจำว่าไม้
    เปิดตอนแท่งไหน — `PaperBroker` จำได้เพราะมันถือสถานะจำลองอยู่ ส่วนฝั่ง live
    สถานะมาจาก `positions()` ของ venue ซึ่ง**ไม่ได้พาแท่งที่เปิดมาด้วย** ตัวที่ตอบได้
    คือ ledger เอง

    นิยาม "ยังเปิดค้าง" = fill ล่าสุดของไม้นั้นเหลือ `position_qty_after > 0` ·
    อ่านจากของที่เขียนไปแล้ว ไม่ใช่จากสถานะที่จำไว้ ตรงกับกฎของ spec/06 ที่ห้ามเชื่อ
    ความจำ

    **ใบ 13 เป็นเจ้าของคำถามว่า `positions()` ของ venue จับคู่กับผลนี้ได้แค่ไหน** —
    ไม้ที่คนไปเปิดเองที่หน้าเว็บจะไม่มีแถวใน ledger เลย ซึ่ง reconcile ต้องเห็นเป็น
    ของค้างที่ระบบไม่ได้ตั้งใจถือ (ADR 19) ไม่ใช่เอามาสวมกับไม้ของระบบ
    """
    prefix = f"{market}:{store_symbol(symbol)}:{side}:"
    stmt = (
        select(fills_t.c.trade_id, fills_t.c.position_qty_after)
        .where(
            and_(
                fills_t.c.profile == profile,
                fills_t.c.trade_id.startswith(prefix),
            )
        )
        .order_by(fills_t.c.fill_ts.desc(), fills_t.c.id.desc())
        .limit(1)
    )
    row = conn.execute(stmt).first()
    if row is None or price_from_db(row.position_qty_after) <= 0:
        return None
    return row.trade_id


def fills_of_trade(conn: Connection, profile: str, trade_id: str) -> list[Fill]:
    """ทุก fill ของไม้หนึ่งไม้ เรียงเก่า → ใหม่ — ตัวตั้งของรายงาน "ไม้ที่ปิดแล้ว\""""
    stmt = (
        select(fills_t)
        .where(and_(fills_t.c.profile == profile, fills_t.c.trade_id == trade_id))
        .order_by(fills_t.c.fill_ts, fills_t.c.id)
    )
    return [
        Fill(
            profile=row.profile,
            market=row.market,
            symbol=row.symbol,
            trade_id=row.trade_id,
            leg=row.leg,
            fill_ts=row.fill_ts,
            px=price_from_db(row.px),
            qty=price_from_db(row.qty),
            client_order_id=row.client_order_id,
            order_type=row.order_type,
            reduce_only=row.reduce_only,
            position_qty_after=price_from_db(row.position_qty_after),
            bar_close_ts=row.bar_close_ts,
            dedupe_key=row.dedupe_key,
            ref_px=None if row.ref_px is None else price_from_db(row.ref_px),
            fee_quote=row.fee_quote,
            fee_ccy=row.fee_ccy,
            fee_unavailable_reason=row.fee_unavailable_reason,
            venue_fill_id=row.venue_fill_id,
            leverage=None if row.leverage is None else pct_from_db(row.leverage),
            exit_reason=row.exit_reason,
            exit_detail=row.exit_detail,
        )
        for row in conn.execute(stmt)
    ]


def funding_charges_of_trade(
    conn: Connection, profile: str, trade_id: str
) -> list[FundingCharge]:
    """ทุกรอบ funding ของไม้หนึ่งไม้ เรียงตามรอบ"""
    stmt = (
        select(funding_t)
        .where(and_(funding_t.c.profile == profile, funding_t.c.trade_id == trade_id))
        .order_by(funding_t.c.cycle_ts)
    )
    return [
        FundingCharge(
            profile=row.profile,
            market=row.market,
            symbol=row.symbol,
            trade_id=row.trade_id,
            cycle_ts=row.cycle_ts,
            position_qty=price_from_db(row.position_qty),
            rate=None if row.rate is None else rate_from_db(row.rate),
            amount_quote=row.amount_quote,
            mark_px=None if row.mark_px is None else price_from_db(row.mark_px),
            unavailable_reason=row.unavailable_reason,
        )
        for row in conn.execute(stmt)
    ]
