"""อ่านความจริงกลับจาก venue แล้วเขียนลง ledger — สิ่งที่ `PaperBroker` ทำเองไม่ได้ที่นี่

`PaperBroker` เขียน `fills` ตอน `place()` ได้เพราะมันคือคนที่ทำให้ fill เกิด · บน venue
จริงคำตอบของ `create_order` **ยังไม่ใช่ความจริงสุดท้าย**: ออเดอร์ใบเดียว fill เป็นหลาย
ก้อน ค่าธรรมเนียมมาทีหลัง และ stop ทำงานตอนที่ process ของเราดับอยู่ · ความจริงจึงมา
จากการอ่านกลับ ไม่ใช่จากการจำว่าเราสั่งอะไรไป (spec/06 · spec/08 ขั้น 3)

## ของซ้ำเป็นเรื่องปกติ ไม่ใช่ความผิดพลาด

ทุกแท่งอ่านหน้าต่างเดิมซ้ำโดยตั้งใจ — `UNIQUE (profile, dedupe_key)` คือสิ่งที่ทำให้
การอ่านซ้ำไม่กลายเป็นการคิดเงินซ้ำ · เช็คด้วย `has_fill()` ก่อนเขียน ไม่ใช่เขียนแล้ว
กลืน error เพราะ error ที่ถูกกลืนจะกลบของซ้ำ*จริง*ไปด้วย (`ledger.py:record_fill`)

**หน้าต่างย้อนหลังเป็นแค่ "ขี้เกียจอ่านไกลกว่านี้" ไม่ใช่กลไกความถูกต้อง** — ตัวที่
กันของซ้ำคือกุญแจ ไม่ใช่ขอบของหน้าต่าง จึงไม่ต้องมี watermark ให้ลืมซิงก์

## ของที่ไม่ใช่ของระบบ ต้องไม่ถูกรับเป็นของระบบ

trade ที่ `clientOrderId` ไม่ขึ้นต้นด้วย `cane-` คือของที่คนไปกดเองที่หน้าเว็บ ·
**ไม่เขียนลง ledger** (ADR 19) ขั้น 3 ของไปป์ไลน์เห็นมันจาก `positions()` แล้วบันทึก
เป็นสถานะที่ระบบไม่ได้ตั้งใจถือทุกแท่งจนกว่าคนจะปิด · การเอามาสวมกับไม้ของระบบจะทำให้
รายงาน P&L ของระบบรวมเงินของคนอื่นเข้ามาด้วย

## `fill_ts` กับ `bar_close_ts` เป็นคนละเวลา

`fill_ts` คือเวลาที่ venue บอกว่า fill เกิด · `bar_close_ts` คือแท่งที่**เรามารู้**
stop ที่ทำงานตอน process ดับจะมีสองค่านี้ห่างกันเป็นวัน · `PaperBroker` ยุบสองค่านี้
เป็นค่าเดียวได้เพราะในโลกจำลองมันเท่ากันจริง ที่นี่ไม่ใช่
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from sqlalchemy import Connection

from cane.data.exchange import unified_symbol
from cane.db.repo import ledger as ledger_repo
from cane.db.repo.ledger import Fill, FundingCharge, dedupe_key_of, trade_id_of
from cane.db.types import now_ms, price_to_db, store_symbol
from cane.execution.broker import ID_PREFIX, LEGS, ORDER_SIDES

log = logging.getLogger(__name__)

PERP = "usdtm_perp"

#: อ่านย้อนหลังเท่านี้ทุกครั้ง — ยาวพอจะครอบ process ที่ดับข้ามสุดสัปดาห์
#: สั้นพอที่จะไม่ขอทั้งประวัติทุกแท่ง · ดูหัวไฟล์ว่าทำไมค่านี้ไม่ใช่กลไกความถูกต้อง
LOOKBACK_MS = 7 * 86_400_000

#: `leg` → `exit_reason` ของขาที่ปิดไม้ · ชุดค่าเป็นของ `ck_fills_exit_reason`
_EXIT_REASON = {"close": "signal", "stop": "stop"}


class VenueHistory(Protocol):
    """ส่วนของ ccxt ที่การอ่านกลับต้องใช้ — แยกจาก `TradingClient` เพื่อให้เทสต์ปลอมได้แคบ"""

    def fetch_orders(
        self, symbol: str | None = None, since: int | None = None
    ) -> list[dict[str, Any]]: ...

    def fetch_my_trades(
        self, symbol: str | None = None, since: int | None = None
    ) -> list[dict[str, Any]]: ...

    def fetch_funding_history(
        self, symbol: str | None = None, since: int | None = None
    ) -> list[dict[str, Any]]: ...

    def fetch_funding_rate_history(
        self, symbol: str | None = None, since: int | None = None
    ) -> list[dict[str, Any]]: ...


# ── กุญแจของออเดอร์: รูปของ venue ↔ รูปของ spec/06 ───────────────────────────
#
# อยู่ที่นี่เพราะไฟล์นี้คือเจ้าของ "ความสอดคล้องระหว่างของที่ venue บันทึกกับของที่
# ledger บันทึก" ทั้งสองทิศ · แยกไปอยู่กับ broker แล้วฝั่งอ่านกลับจะต้อง import
# ย้อนกลับมาหา broker ซึ่งเป็นวงกลม


def venue_order_id(bar_close_ts: int, order_side: str, leg: str) -> str:
    """`cane-{วินาที}-{side}-{leg}` — รูปที่ **ส่งให้ venue** (26 อักษรคงที่)

    กุญแจตาม spec/06 คือ `cane-{SYMBOL}-{bar_close_ts}-{side}-{leg}` และมันยังเป็นสิ่ง
    ที่ลง `fills.client_order_id` กับ `decision_orders.client_order_id` ไม่เปลี่ยน · แต่
    Binance จำกัด `newClientOrderId` ไว้ที่ 36 อักษร (`^[\\.A-Z\\:/a-z0-9_-]{1,36}$`
    ทั้ง fapi และ spot) และกุญแจเต็มของขาปิดยาวเกินไปแล้ว:

        cane-BTC/USDT-1787961600000-sell-close  → 38 อักษร · venue ปฏิเสธทุกใบ

    **ตัดชื่อเหรียญออกได้เพราะ venue แยกออเดอร์ตามเหรียญอยู่แล้ว** — ทุก endpoint ที่
    เราเรียกส่ง `symbol` ไปด้วยเสมอ ชื่อในกุญแจจึงเป็นข้อมูลซ้ำที่กินงบความยาว · และ
    วินาทีแทนมิลลิวินาทีไม่เสียข้อมูลเพราะแท่ง 1h/1d ปิดที่ขอบวินาทีพอดี (เป็นรูปที่
    ตัวอย่างใน spec/06 เขียนไว้เองด้วย)

    คุณสมบัติที่กุญแจนี้มีหน้าที่รักษายังครบทุกข้อ: deterministic จากสิ่งที่รู้ก่อนส่ง ·
    `leg` ยังอยู่ จึงยังแยกสองขาของ flip ที่เป็น `buy` เหมือนกัน · คำนำหน้า `cane-` ยัง
    อยู่ จึงยังแยกออเดอร์ของระบบออกจากที่คนไปกดเอง
    """
    if order_side not in ORDER_SIDES:
        raise ValueError(f"order_side ต้องเป็นหนึ่งใน {ORDER_SIDES} ไม่ใช่ {order_side!r}")
    if leg not in LEGS:
        raise ValueError(f"leg ต้องเป็นหนึ่งใน {LEGS} ไม่ใช่ {leg!r}")
    if bar_close_ts % 1000:
        # แท่ง 1h/1d ปิดที่ขอบวินาทีพอดี · เศษมิลลิวินาทีแปลว่าผู้เรียกส่งเวลาอื่นมา
        # ถ้าปัดทิ้งเงียบๆ สองเวลาที่ต่างกันจะได้กุญแจเดียวกัน ซึ่งเป็นสิ่งเดียวที่
        # กุญแจนี้มีหน้าที่กัน
        raise ValueError(f"bar_close_ts ต้องลงตัวเป็นวินาที ไม่ใช่ {bar_close_ts}")
    return f"{ID_PREFIX}-{bar_close_ts // 1000}-{order_side}-{leg}"


def spec_order_id(venue_id: str, symbol: str) -> str | None:
    """รูปของ venue → กุญแจตาม spec/06 · ไม่ใช่ของระบบนี้ = `None`

    `symbol` มาจากบริบทของผู้เรียก (ทุก endpoint ถามทีละเหรียญอยู่แล้ว) ไม่ใช่จากกุญแจ

    ส่วนต่อท้าย `-r{digest}` ของ stop ที่ถูกวางใหม่ถูกตัดทิ้ง เพื่อให้ fill ของมันยัง
    โยงกลับไปหา `decision_orders` แถวเดิมได้ — ทางเชื่อมเดียวระหว่าง `fills` กับบันทึก
    การตัดสินใจคือกุญแจนี้ (ใบ 03 เลือก join ด้วยคีย์ ไม่มี FK)
    """
    parts = venue_id.split("-")
    if len(parts) not in (4, 5) or parts[0] != ID_PREFIX:
        return None
    seconds, order_side, leg = parts[1], parts[2], parts[3]
    if not seconds.isdigit() or order_side not in ORDER_SIDES or leg not in LEGS:
        return None
    return f"{ID_PREFIX}-{store_symbol(symbol)}-{int(seconds) * 1000}-{order_side}-{leg}"


def bar_and_leg(client_order_id: str) -> tuple[int, str]:
    """แกะ `(bar_close_ts, leg)` ออกจากกุญแจตาม spec/06 — ผิดรูป = ไม่ส่ง

    ชั้นบนสร้างกุญแจด้วย `broker.client_order_id()` เสมอ · กุญแจที่แกะไม่ออกแปลว่ามีใคร
    ประกอบเองด้วยมือ ซึ่งต้องดัง**ก่อน**ออเดอร์ถึงปลายทาง ไม่ใช่หลังจากนั้น
    """
    parts = client_order_id.split("-")
    if len(parts) != 5 or parts[0] != ID_PREFIX or not parts[2].isdigit():
        raise ValueError(
            f"client_order_id {client_order_id!r} ไม่ใช่รูปของ spec/06 "
            f"({ID_PREFIX}-SYMBOL-bar_close_ts-side-leg)"
        )
    return int(parts[2]), parts[4]


# ── สถานะของไม้ตามที่ ledger จำได้ ───────────────────────────────────────────


def ledger_position(
    conn: Connection, profile: str, market: str, symbol: str
) -> tuple[str, float, float] | None:
    """`(side, qty, entry_px)` ของไม้ที่ระบบเปิดเองและยังค้างอยู่ · ไม่มี = `None`

    ใช้บน **spot** ที่ venue ไม่มี `positions()` ให้ถาม — สิ่งที่มีคือยอดคงเหลือ ซึ่ง
    รวมเหรียญที่คนโอนเข้ามาเองไว้ในก้อนเดียวกันแยกไม่ออก · คำตอบที่ปลอดภัยคือนับเฉพาะ
    สิ่งที่เราบันทึกไว้เองว่าซื้อมา ส่วนที่เกินไม่ใช่ของระบบ (ADR 19)

    `entry_px` เป็นค่าเฉลี่ยถ่วงน้ำหนักของ**ขาเปิด**ทั้งหมดของไม้นั้น ไม่ใช่ราคาของ fill
    ก้อนแรก — ออเดอร์ใบเดียว fill เป็นหลายก้อนคนละราคาได้จริง
    """
    for side in ("long", "short"):
        trade_id = ledger_repo.open_trade_id(conn, profile, market, symbol, side)
        if trade_id is None:
            continue
        fills = ledger_repo.fills_of_trade(conn, profile, trade_id)
        if not fills:
            continue
        opened = [f for f in fills if f.leg == "open"]
        qty_in = sum(f.qty for f in opened)
        if qty_in <= 0:
            continue
        entry_px = sum(f.px * f.qty for f in opened) / qty_in
        return side, fills[-1].position_qty_after, entry_px
    return None


# ── การอ่านกลับ ──────────────────────────────────────────────────────────────


def sync(
    conn: Connection,
    client: VenueHistory,
    *,
    profile: str,
    market: str,
    symbol: str,
    bar_close_ts: int,
    now: int | None = None,
) -> int:
    """เขียน fill และ funding ที่ venue บันทึกไว้แต่ ledger ยังไม่มี · คืนจำนวนแถวที่เขียน

    `conn` เป็นของผู้เรียก ไม่ commit — อยู่ในทรานแซกชันของแท่งนั้น เหมือน `repo/` ทั้งชุด
    """
    symbol = store_symbol(symbol)
    since = (now_ms() if now is None else now) - LOOKBACK_MS
    written = _sync_fills(conn, client, profile, market, symbol, bar_close_ts, since)
    if market == PERP:
        written += _sync_funding(conn, client, profile, market, symbol, since)
    return written


def _sync_fills(
    conn: Connection,
    client: VenueHistory,
    profile: str,
    market: str,
    symbol: str,
    bar_close_ts: int,
    since: int,
) -> int:
    usym = unified_symbol(symbol, market)
    # `fetch_my_trades` ของ Binance ไม่คืน `clientOrderId` มาด้วย (ทั้ง fapi และ spot)
    # มีแต่เลขออเดอร์ · ตัวที่ผูกสองอย่างเข้าด้วยกันคือรายการออเดอร์ จึงต้องอ่านทั้งคู่
    coid_of = {
        str(row["id"]): str(row.get("clientOrderId") or "")
        for row in client.fetch_orders(usym, since)
        if row.get("id") is not None
    }
    trades = sorted(
        client.fetch_my_trades(usym, since),
        key=lambda t: (t.get("timestamp") or 0, str(t.get("id") or "")),
    )

    state = ledger_position(conn, profile, market, symbol)
    open_trade_id = (
        ledger_repo.open_trade_id(conn, profile, market, symbol, state[0]) if state else None
    )
    qty_held = state[1] if state else 0.0

    written = 0
    for trade in trades:
        venue_fill_id = str(trade.get("id") or "")
        spec_id = spec_order_id(coid_of.get(str(trade.get("order") or ""), ""), symbol)
        if spec_id is None:
            # ของที่คนไปกดเอง หรือออเดอร์ที่เก่ากว่าหน้าต่างของ `fetch_orders` — ไม่ใช่
            # ของระบบจนกว่าจะพิสูจน์ได้ว่าใช่ (ADR 19 · ดูหัวไฟล์)
            continue
        key = dedupe_key_of(spec_id, venue_fill_id=venue_fill_id or None)
        qty = float(trade.get("amount") or 0.0)
        if qty <= 0:
            continue

        _, leg = bar_and_leg(spec_id)
        if ledger_repo.has_fill(conn, profile, key):
            # **ไม่เดินยอดต่อ** — `ledger_position()` อ่านมาจากแถวที่เขียนไปแล้ว ยอดตั้งต้น
            # จึงนับก้อนนี้ไปเรียบร้อย · การเดินซ้ำคือการนับสองครั้ง แล้ว `position_qty_after`
            # ของก้อนถัดไปจะโตกว่าไม้จริง
            continue

        side = str(trade.get("side") or "")
        if leg == "open":
            if open_trade_id is None:
                open_bar, _ = bar_and_leg(spec_id)
                open_trade_id = trade_id_of(
                    market, symbol, "long" if side == "buy" else "short", open_bar
                )
            trade_id = open_trade_id
        elif open_trade_id is None:
            # ปิดไม้ที่ ledger ไม่เคยรู้ว่าเปิด — เขียนไม่ได้โดยไม่แต่ง `trade_id` ขึ้นมา
            # ซึ่งจะทำให้รายงาน "ไม้ที่ปิดแล้ว" มีไม้ที่ไม่มีขาเปิด · ดังไว้ ไม่เขียน
            log.warning(
                "ข้าม fill ของขา %s ที่ %s (%s): ledger ไม่มีไม้ที่เปิดค้างให้ผูก",
                leg, symbol, spec_id,
            )
            continue
        else:
            trade_id = open_trade_id

        qty_held, open_trade_id = _advance(
            qty_held, open_trade_id, leg, qty, market, symbol, trade, spec_id
        )
        fee = trade.get("fee") or {}
        cost, currency = fee.get("cost"), fee.get("currency")
        known_fee = cost is not None and currency is not None
        ledger_repo.record_fill(
            conn,
            Fill(
                profile=profile,
                market=market,
                symbol=symbol,
                trade_id=trade_id,
                leg=leg,
                # เวลาของ venue · คนละค่ากับแท่งที่เรามารู้ ดูหัวไฟล์
                fill_ts=int(trade.get("timestamp") or bar_close_ts),
                px=float(trade["price"]),
                qty=qty,
                client_order_id=spec_id,
                order_type="stop_market" if leg == "stop" else "market",
                reduce_only=market != "spot" and leg in ("close", "stop"),
                position_qty_after=qty_held,
                bar_close_ts=bar_close_ts,
                dedupe_key=key,
                venue_fill_id=venue_fill_id or None,
                fee_quote=price_to_db(cost) if known_fee else None,
                fee_ccy=currency if known_fee else None,
                fee_unavailable_reason=None if known_fee else "venue ไม่คืนค่าธรรมเนียมของ fill นี้",
                exit_reason=_EXIT_REASON.get(leg),
            ),
        )
        written += 1
    return written


def _advance(
    qty_held: float,
    open_trade_id: str | None,
    leg: str,
    qty: float,
    market: str,
    symbol: str,
    trade: dict[str, Any],
    spec_id: str,
) -> tuple[float, str | None]:
    """ยอดคงค้างหลัง fill ก้อนนี้ · ปิดหมดแล้วไม้นั้นจบ กุญแจของไม้จึงหมดอายุไปด้วย"""
    if leg == "open":
        if open_trade_id is None:
            open_bar, _ = bar_and_leg(spec_id)
            side = "long" if str(trade.get("side") or "") == "buy" else "short"
            open_trade_id = trade_id_of(market, symbol, side, open_bar)
        return qty_held + qty, open_trade_id
    remaining = max(qty_held - qty, 0.0)
    return remaining, open_trade_id if remaining > 0 else None


def _sync_funding(
    conn: Connection,
    client: VenueHistory,
    profile: str,
    market: str,
    symbol: str,
    since: int,
) -> int:
    """funding ที่ถูกหักไปแล้ว → `funding_charges` · รอบละแถว ผูกกับไม้ที่ถืออยู่

    รอบ 8 ชม. เป็นกริดตายตัว `(trade_id, cycle_ts)` จึง idempotent อยู่แล้ว — กันซ้ำ
    ด้วย `UNIQUE (profile, trade_id, cycle_ts)` โดยอ่านรอบที่มีอยู่แล้วมาก่อน (ไม่มี
    `has_funding()` ให้เรียกแบบ `has_fill()` เพราะกุญแจของมันเป็นคอลัมน์จริงอยู่แล้ว)

    **อัตราไม่ได้มากับยอดที่ถูกหัก** — Binance คืนยอดที่ income history ส่วนอัตราอยู่อีก
    endpoint · จับคู่ด้วยเวลาของรอบ · รอบที่หาอัตราไม่เจอ เขียนว่า "ไม่รู้อัตรา" ไม่ใช่
    เติมศูนย์ ซึ่งจะอ้างว่าไม้นั้นไม่เสีย funding (`FundingCharge` บังคับอย่างใดอย่างหนึ่ง)
    """
    state = ledger_position(conn, profile, market, symbol)
    if state is None:
        return 0
    trade_id = ledger_repo.open_trade_id(conn, profile, market, symbol, state[0])
    if trade_id is None:
        return 0

    usym = unified_symbol(symbol, market)
    done = {c.cycle_ts for c in ledger_repo.funding_charges_of_trade(conn, profile, trade_id)}
    rates = {
        int(row["timestamp"]): float(row["fundingRate"])
        for row in client.fetch_funding_rate_history(usym, since)
        if row.get("timestamp") is not None and row.get("fundingRate") is not None
    }

    written = 0
    for row in client.fetch_funding_history(usym, since):
        cycle_ts = int(row.get("timestamp") or 0)
        if cycle_ts == 0 or cycle_ts in done:
            continue
        amount = row.get("amount")
        rate = rates.get(cycle_ts)
        known = amount is not None and rate is not None
        ledger_repo.record_funding_charge(
            conn,
            FundingCharge(
                profile=profile,
                market=market,
                symbol=symbol,
                trade_id=trade_id,
                cycle_ts=cycle_ts,
                position_qty=state[1],
                # venue คิดเป็น "ได้รับ" (บวก = เข้ากระเป๋า) ส่วน ledger เก็บเป็น
                # "ถูกหัก" ตามที่ `PaperBroker` เขียนไว้ — เครื่องหมายจึงกลับกัน
                rate=rate if known else None,
                amount_quote=price_to_db(-float(amount)) if known else None,
                unavailable_reason=None if known else "venue ไม่ได้ให้อัตราของรอบนี้",
            ),
        )
        done.add(cycle_ts)
        written += 1
    return written
