"""`CcxtBroker` — ปลายทางจริงผ่าน ccxt · หนึ่งตัวต่อหนึ่งตลาด (ADR 3, ADR 28)

ไฟล์นี้เป็น**การแปลคำศัพท์** ไม่ใช่ที่อยู่ของตรรกะ: `Order` ของเราเป็นอาร์กิวเมนต์ของ
`create_order` ส่วนคำตอบของ venue เป็น `OrderResult`/`Position`/`OpenOrder` ตรรกะการเทรด
ทั้งหมดอยู่เหนือ `Broker` และต้องไม่รู้ว่าปลายทางเป็นอะไร

## `dry_run` ไม่มีที่นี่

ADR 31 บอกว่าธงนั้นกั้นที่ **engine** (`RunContext.dry_run_blocks`) และมีหลักฐานบังคับ
ที่ `repo/decisions.py:_check_dry_run()` · ถ้าใส่ด่านที่สองไว้ในนี้ ตรรกะของ paper กับ
live จะแยกออกจากกันอีกครั้ง ซึ่งเป็นสิ่งเดียวที่ ADR 9 ห้าม

## การเขียน ledger เป็นของ `reconcile.py`

`PaperBroker` เขียน `fills` เองได้เพราะมันคือคนที่ทำให้ fill เกิด · ที่นี่ venue เป็น
คนทำ และคำตอบของ `create_order` ก็ยัง**ไม่ใช่ความจริงสุดท้าย** (fill แบ่งหลายก้อน ค่า
ธรรมเนียมมาทีหลัง stop ทำงานตอน process ดับ) · ความจริงจึงถูกอ่านกลับจาก venue ที่
`execution/reconcile.py` แล้วเขียนลง ledger ที่นั่นที่เดียว

**จุดที่เรียก reconcile คือตะเข็บเดียวกับ `PaperBroker._settle()`** — `open_orders()`
(ขั้น 3 ของทุกแท่ง) และหลัง `place()` · ทำแบบนี้ไปป์ไลน์ไม่ต้องมี `if` แยก paper กับ
live แม้แต่บรรทัดเดียว ซึ่งเป็นเหตุผลเดียวกับที่ `paper.py` เลือกท่านี้แทน `settle(bar)`

## clientOrderId ที่ venue รับได้ ≠ กุญแจของ spec/06

Binance จำกัด `newClientOrderId` ไว้ที่ 36 อักษร และกุญแจเต็มของขาปิดยาวเกินไปแล้ว ·
การแปลงสองทิศอยู่ที่ `reconcile.venue_order_id()` / `reconcile.spec_order_id()` ที่เดียว
พร้อมเหตุผลเต็ม — ที่นี่แค่เรียกใช้
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import ccxt
from sqlalchemy import Connection

from cane.data.exchange import default_type, unified_symbol
from cane.data.ohlcv import BarSource
from cane.db.types import price_to_db, store_symbol
from cane.execution import reconcile
from cane.execution.broker import (
    Balance,
    OpenOrder,
    Order,
    OrderResult,
    Position,
    check_market_supports,
)
from cane.execution.reconcile import bar_and_leg, spec_order_id, venue_order_id

log = logging.getLogger(__name__)

PERP = "usdtm_perp"
SPOT = "spot"

#: error ทั้งหมดของ ccxt สืบจากตัวนี้ — จับให้แคบเหมือน `data/exchange.py`
#: `except Exception` จะกลบ `TypeError`/`KeyError` ของเราเองให้กลายเป็น "venue มีปัญหา"
TRADING_ERRORS: tuple[type[BaseException], ...] = (ccxt.BaseError,)


class BrokerError(RuntimeError):
    """ปลายทางปฏิเสธ หรือคำตอบของมันไม่ครบพอจะเชื่อ — ผู้เรียกเห็นแล้วตัดสินใจเอง"""


def api_key_env(exchange: str) -> tuple[str, str]:
    """ชื่อตัวแปรของ `(key, secret)` ของ venue หนึ่ง — `binance` → `CANE_BINANCE_API_*`

    ผูกกับชื่อ venue แทนที่จะเป็นชื่อเดียวตายตัว เพราะเครื่องเดียวอาจถือคีย์ของหลาย
    venue พร้อมกัน (Decision #3 ยังเปิดทางไว้) การใช้ชื่อเดียวแปลว่าสลับ venue ใน
    config แล้วได้คีย์ของที่เก่าไปยิงที่ใหม่ ซึ่งล้มแบบที่หาสาเหตุยาก
    """
    return f"CANE_{exchange.upper()}_API_KEY", f"CANE_{exchange.upper()}_API_SECRET"


class TradingClient(Protocol):
    """สิ่งที่ broker ต้องการจาก ccxt — เทสต์ปลอมได้ทั้งก้อน เหมือน `ExchangeClient`"""

    def create_order(
        self,
        symbol: str,
        type: str,  # noqa: A002 — ชื่อพารามิเตอร์ของ ccxt
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def cancel_order(self, id: str, symbol: str | None = None) -> dict[str, Any]: ...  # noqa: A002

    def edit_order(
        self,
        id: str,  # noqa: A002
        symbol: str,
        type: str,  # noqa: A002
        side: str,
        amount: float | None = None,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def fetch_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]: ...

    def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]: ...

    def fetch_balance(self) -> dict[str, Any]: ...

    def fetch_ticker(self, symbol: str) -> dict[str, Any]: ...

    def set_leverage(self, leverage: float, symbol: str | None = None) -> Any: ...

    def set_margin_mode(self, marginMode: str, symbol: str | None = None) -> Any: ...  # noqa: N803

    def load_markets(self, reload: bool = False) -> dict[str, Any]: ...

    def fetch_market_leverage_tiers(self, symbol: str) -> list[dict[str, Any]]: ...


def make_trading_client(
    exchange: str, market: str, *, env: dict[str, str] | None = None
) -> TradingClient:
    """ccxt client ที่ **มีคีย์** ของตลาดหนึ่งตลาด — คนละตัวกับของชั้นข้อมูล

    **ไม่เอาไปรวมกับ `data/exchange.py:make_client()` โดยเจตนา** · ที่นั่นเขียนไว้ว่า
    ชั้นข้อมูลไม่รับ credential เข้ามาเลย จึงไม่มีอะไรให้หลุด — เป็นหลักประกันเชิง
    โครงสร้าง ไม่ใช่ความระมัดระวังของคนเขียน · การเติมพารามิเตอร์คีย์เข้าไปในตัวนั้น
    ทำลายหลักประกันนั้นทิ้งทั้งอัน เพื่อประหยัดโค้ดหกบรรทัด

    ขาดคีย์ = `RuntimeError` ตอนสร้าง ไม่ใช่ตอนยิงออเดอร์ใบแรก (fail-closed, spec/06)
    """
    source = os.environ if env is None else env
    key_name, secret_name = api_key_env(exchange)
    key, secret = source.get(key_name, ""), source.get(secret_name, "")
    if not key or not secret:
        missing = ", ".join(n for n, v in ((key_name, key), (secret_name, secret)) if not v)
        raise RuntimeError(
            f"ไม่มี {missing} — กรอกใน .env แล้วสั่งด้วย `uv run --env-file .env ...` · "
            "คีย์ไม่ commit และไม่ผ่านแชท (spec/06 §ความปลอดภัยของ credential)"
        )
    factory = getattr(ccxt, exchange, None)
    if factory is None:
        raise ValueError(f"ccxt ไม่รู้จัก exchange {exchange!r}")
    return factory({
        "apiKey": key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": default_type(market)},
    })


# ── กุญแจของ stop ที่ถูกวางใหม่ ──────────────────────────────────────────────


def replacement_id(base_venue_id: str, stop_px: float) -> str:
    """กุญแจของ stop ใบใหม่ตอนที่ venue amend ของเดิมไม่ได้ — ดู `CcxtBroker.replace`

    ผูกกับ **ราคาเป้าหมาย** ไม่ใช่ตัวนับ · ตัวนับจะเริ่มใหม่ทุกครั้งที่ process รีสตาร์ท
    แล้วกุญแจจะไม่ deterministic อีกต่อไป ซึ่งทำลายการกันสั่งซ้ำทั้งหมด · การลองวางซ้ำ
    ที่ราคาเดิมได้กุญแจเดิมเป๊ะ แล้ว venue ปฏิเสธในฐานะของซ้ำ ซึ่งคือสิ่งที่ต้องการ

    ย่อเป็น digest เพราะความยาวของราคาไม่มีเพดาน (`0.00000123` กับ `104250.5`) และ
    งบของกุญแจมีแค่ 36 อักษร
    """
    digest = hashlib.sha256(f"{stop_px!r}".encode()).hexdigest()[:6]
    return f"{base_venue_id}-r{digest}"


# ── ตัวแปลงคำตอบของ venue ───────────────────────────────────────────────────


def _need(row: dict[str, Any], *names: str) -> Any:
    """ค่าแรกที่ไม่ใช่ `None` จากชื่อที่ให้มา · ไม่มีเลย = `BrokerError`

    ccxt ไม่ได้ทำให้ทุก venue ตอบครบทุกคีย์ · ค่าที่หายไปแล้วถูกแทนด้วยศูนย์คือ
    ตัวเลขที่ไม่มีใครกรอกแต่รายงานอ้างว่าเป็นของจริง — ต้องดัง ไม่ใช่เดา
    """
    for name in names:
        value = row.get(name)
        if value is not None:
            return value
    raise BrokerError(f"คำตอบของ venue ไม่มี {' หรือ '.join(names)}: {sorted(row)}")


def _trigger_px(row: dict[str, Any]) -> float | None:
    """ราคา trigger ของ stop — ccxt เรียก `triggerPrice` ส่วนรุ่นเก่าเรียก `stopPrice`"""
    value = row.get("triggerPrice")
    if value is None:
        value = row.get("stopPrice")
    return None if value is None else float(value)


def _result(row: dict[str, Any], client_order_id: str) -> OrderResult:
    """คำตอบของ `create_order`/`edit_order` → `OrderResult`

    `client_order_id` ส่งเข้ามาเป็นกุญแจตามสเปก ไม่ได้อ่านจากคำตอบ — venue คืนรูปของ
    ตัวเองกลับมา (รูปย่อ) และสิ่งที่ผู้เรียกกับ `decision_orders` ต้องเห็นคือรูปเต็ม

    **ค่าธรรมเนียมที่ venue ไม่คืนมาในคำตอบของออเดอร์ = "ยังไม่รู้" ไม่ใช่ศูนย์** ·
    Binance futures ไม่ใส่ fee มากับคำตอบของ market order เลย ตัวจริงมาทาง
    `fetch_my_trades` ที่ `reconcile.py` อ่านทีหลัง
    """
    fee = row.get("fee") or {}
    cost, currency = fee.get("cost"), fee.get("currency")
    known = cost is not None and currency is not None
    return OrderResult(
        client_order_id=client_order_id,
        venue_order_id=None if row.get("id") is None else str(row["id"]),
        status=str(row.get("status") or "open"),
        filled_qty=float(row.get("filled") or 0.0),
        avg_px=None if row.get("average") is None else float(row["average"]),
        fee_quote=price_to_db(cost) if known else None,
        fee_ccy=currency if known else None,
        fee_unavailable_reason=None if known else "venue ไม่คืนค่าธรรมเนียมมากับคำตอบของออเดอร์",
    )


@dataclass
class CcxtBroker:
    """ปลายทางจริงหนึ่งตลาด · `symbols` คือคู่เหรียญของตลาดนี้ที่ config เปิดไว้

    `conn` เป็นของผู้เรียก (ไม่ commit เอง) เหมือน `PaperBroker` และ `repo/` ทั้งชุด —
    การ*อ่าน*สถานะเขียน ledger ได้ (fill ที่เกิดตอน process ดับ) จึงต้องอยู่ใน
    ทรานแซกชันเดียวกับแท่งนั้น

    `symbols` จำเป็นเพราะ `positions()` ไม่รับพารามิเตอร์ แต่ **spot ไม่มี
    `fetch_positions`** ให้เรียก (ดู `positions()`) ตัวที่บอกได้ว่าต้องดูเหรียญไหนบ้าง
    จึงมีแต่ config

    `bars` มีด้วยเหตุผลเดียวกับที่ `PaperBroker` มี: `fills.bar_close_ts` คือ**แท่งที่
    เรามารู้** ซึ่งต่างจาก `fill_ts` ของ venue · ตอน `open_orders()` ไม่มีใครบอกแท่ง
    ปัจจุบันมาให้ broker ตัวที่ตอบได้จึงมีแต่แหล่งแท่งเอง
    """

    conn: Connection
    client: TradingClient
    market: str
    profile: str
    bars: BarSource
    timeframe: str
    symbols: tuple[str, ...] = ()
    quote: str = "USDT"

    #: venue order id → เหรียญ · `cancel`/`replace` ของ ccxt ต้องมี symbol แต่ `Broker`
    #: ไม่ส่งมาให้ · ผู้เรียกได้ id มาจาก `open_orders(symbol)` ของแท่งเดียวกันเสมอ
    #: (spec/08 ขั้น 3 → `maintain_stop`) การจำคู่นี้ไว้จึงเป็นการจำสิ่งที่เพิ่งอ่านมา
    #: ไม่ใช่การเชื่อความจำแทนการอ่านสถานะจริง
    _symbol_of: dict[str, str] = field(init=False, default_factory=dict)
    #: ชั้น maintenance margin ต่อเหรียญ — ตารางของ venue ไม่เปลี่ยนรายวัน อ่านครั้งเดียวพอ
    _tiers: dict[str, list[dict[str, Any]]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        if self.market not in (PERP, SPOT):
            raise ValueError(f"market ต้องเป็น {PERP} หรือ {SPOT} ไม่ใช่ {self.market!r}")
        self.symbols = tuple(store_symbol(s) for s in self.symbols)

    # ── ส่งคำสั่ง ────────────────────────────────────────────────────────────

    def place(self, order: Order) -> OrderResult:
        check_market_supports(order, self.market)
        symbol = store_symbol(order.symbol)
        usym = unified_symbol(symbol, self.market)
        bar_close_ts, leg = bar_and_leg(order.client_order_id)
        venue_id = venue_order_id(bar_close_ts, order.side, leg)

        params: dict[str, Any] = {"clientOrderId": venue_id}
        if order.reduce_only:
            params["reduceOnly"] = True
        if order.type == "stop_market":
            # ชื่อ unified ของ ccxt · การแปลเป็น `STOP_MARKET`/`stopPrice` ของแต่ละ
            # venue เป็นงานของ ccxt ไม่ใช่ของเรา — เราไม่รู้คำศัพท์ของ venue ที่ยังไม่เจอ
            params["triggerPrice"] = order.stop_px

        try:
            row = self.client.create_order(usym, "market", order.side, order.qty, None, params)
        except TRADING_ERRORS as error:
            raise BrokerError(f"venue ปฏิเสธออเดอร์ {venue_id}: {error}") from error

        if row.get("id") is not None:
            self._symbol_of[str(row["id"])] = symbol
        # ความจริงเรื่องเงินมาจาก venue ไม่ใช่จากคำตอบใบนี้ — ดูหัวไฟล์
        self._sync(symbol)
        return _result(row, order.client_order_id)

    def cancel(self, order_id: str) -> None:
        symbol = self._symbol_for(order_id)
        try:
            self.client.cancel_order(order_id, unified_symbol(symbol, self.market))
        except TRADING_ERRORS as error:
            raise BrokerError(f"ยกเลิกออเดอร์ {order_id} ไม่สำเร็จ: {error}") from error

    def replace(self, order_id: str, stop_px: float) -> OrderResult:
        """ขยับจุดป้องกันโดย**ไม่เปิดหน้าต่างที่ไม้ไม่มี stop คุ้ม** (ADR 17, spec/06)

        **Binance amend ได้เฉพาะ LIMIT order** (`PUT /fapi/v1/order` เขียนไว้ตรงๆ) ส่วน
        stop ของระบบนี้เป็น `STOP_MARKET` · `has['editOrder']` ของ ccxt เป็นจริงอยู่ดี
        เพราะมันตอบระดับ venue ไม่ใช่ระดับชนิดออเดอร์ จึงเชื่อไม่ได้ ต้องลองแล้วดู

        เมื่อ amend ไม่ได้ ทางที่เหลือมีสองทางและมันไม่เท่ากัน:

        - **cancel แล้ว place** — คือสิ่งที่ ADR 17 สร้าง `replace` ขึ้นมาเพื่อไม่ให้ทำ
          ระหว่างสองคำสั่งไม้ไม่มีอะไรคุ้ม และบน perp ที่มี leverage หน้าต่างนั้นยาว
          พอให้ราคากระโดดถึง liquidation
        - **place แล้ว cancel** — ระหว่างสองคำสั่งไม้มี stop **สองใบ** คุ้มอยู่ ซึ่ง
          บนออเดอร์ `reduceOnly` ไม่มีผลเสีย: ใบที่ทำงานทีหลังเจอ position ศูนย์แล้ว
          และ venue ยกเลิก reduce-only ที่ค้างให้เองเมื่อไม้ปิด

        จึงเลือกทางหลัง · ถ้า cancel ใบเก่าล้ม จะเหลือ stop ค้างสองใบ — **ดังไว้ใน log**
        แล้วปล่อยให้ขั้น 3 ของแท่งถัดไปเห็นเอง ใบเก่าของ trailing stop อยู่ห่างราคา
        มากกว่าเสมอ (เส้นขยับเข้าหาราคาทางเดียว) การค้างจึงไม่เพิ่มความเสี่ยง
        """
        symbol = self._symbol_for(order_id)
        usym = unified_symbol(symbol, self.market)
        existing = next((o for o in self.open_orders(symbol) if o.venue_order_id == order_id), None)
        if existing is None:
            raise BrokerError(f"ไม่มีออเดอร์ค้างชื่อ {order_id!r} ที่ {symbol} ให้ขยับ")

        try:
            row = self.client.edit_order(
                order_id, usym, "market", existing.side, existing.qty, None,
                {"triggerPrice": stop_px, "reduceOnly": existing.reduce_only},
            )
        except TRADING_ERRORS as error:
            log.info("venue amend stop %s ไม่ได้ (%s) — วางใบใหม่ก่อนแล้วค่อยยกเลิกใบเก่า", order_id, error)
            return self._replace_by_placing(existing, symbol, usym, stop_px)

        if row.get("id") is not None:
            self._symbol_of[str(row["id"])] = symbol
        return _result(row, existing.client_order_id or "")

    def _replace_by_placing(
        self, existing: OpenOrder, symbol: str, usym: str, stop_px: float
    ) -> OrderResult:
        if existing.client_order_id is None:
            raise BrokerError(
                f"stop {existing.venue_order_id} ที่ {symbol} ไม่มีกุญแจของระบบ — "
                "เป็นออเดอร์ที่คนไปวางเอง ระบบไม่ขยับของที่ไม่ใช่ของตัวเอง (ADR 19)"
            )
        bar_close_ts, leg = bar_and_leg(existing.client_order_id)
        venue_id = replacement_id(venue_order_id(bar_close_ts, existing.side, leg), stop_px)
        params: dict[str, Any] = {"clientOrderId": venue_id, "triggerPrice": stop_px}
        if existing.reduce_only:
            params["reduceOnly"] = True
        try:
            row = self.client.create_order(usym, "market", existing.side, existing.qty, None, params)
        except TRADING_ERRORS as error:
            raise BrokerError(
                f"วาง stop ใบใหม่ที่ {stop_px} ไม่สำเร็จ ({error}) — ใบเก่า {existing.venue_order_id} "
                "ยังคุ้มไม้อยู่ ไม่ได้ถูกยกเลิก"
            ) from error

        if row.get("id") is not None:
            self._symbol_of[str(row["id"])] = symbol
        try:
            self.client.cancel_order(existing.venue_order_id, usym)
        except TRADING_ERRORS as error:
            log.warning(
                "วาง stop ใบใหม่ที่ %s แล้ว แต่ยกเลิกใบเก่า %s ไม่สำเร็จ: %s — "
                "เหลือ stop ค้างสองใบที่ %s ใบเก่าอยู่ไกลราคากว่า",
                stop_px, existing.venue_order_id, error, symbol,
            )
        # กุญแจตามสเปกยังเป็นของใบเดิม — ดู `spec_order_id()` ว่าทำไม
        return _result(row, existing.client_order_id)

    # ── อ่านสถานะจริง ────────────────────────────────────────────────────────

    def open_orders(self, symbol: str) -> list[OpenOrder]:
        """ออเดอร์ค้างของเหรียญนี้ **รวมของที่คนไปกดเองที่หน้าเว็บ**

        ไม่กรองด้วยคำนำหน้า `cane-` ที่นี่โดยเจตนา — spec/06 บอกว่า reconcile ต้อง
        *มองเห็น* ของที่คนกดเอง แต่ต้องไม่*นับว่าเป็นของตัวเอง* การกรองทิ้งตั้งแต่ชั้น
        นี้คือการทำให้ชั้นบนมองไม่เห็นมันเลย

        เป็นตะเข็บที่ reconcile ทำงาน (ดูหัวไฟล์) — ขั้น 3 ของทุกแท่งผ่านทางนี้
        """
        key = store_symbol(symbol)
        self._sync(key)
        try:
            rows = self.client.fetch_open_orders(unified_symbol(key, self.market))
        except TRADING_ERRORS as error:
            raise BrokerError(f"อ่านออเดอร์ค้างของ {key} ไม่สำเร็จ: {error}") from error

        out: list[OpenOrder] = []
        for row in rows:
            venue_id = str(_need(row, "id"))
            self._symbol_of[venue_id] = key
            trigger = _trigger_px(row)
            out.append(
                OpenOrder(
                    venue_order_id=venue_id,
                    client_order_id=spec_order_id(str(row.get("clientOrderId") or ""), key),
                    symbol=key,
                    side=str(_need(row, "side")),
                    type="stop_market" if trigger is not None else "market",
                    qty=float(_need(row, "amount", "remaining")),
                    stop_px=trigger,
                    reduce_only=bool(row.get("reduceOnly")),
                )
            )
        return out

    def positions(self) -> list[Position]:
        """ไม้ที่ถืออยู่จริง · **spot ไม่มี position ให้ถาม** จึงอ่านจาก ledger

        บน perp `fetch_positions()` เป็นความจริงทั้งหมด รวมไม้ที่คนไปเปิดเอง ซึ่งขั้น 3
        ต้องเห็น (ADR 19)

        บน spot สิ่งที่ venue มีคือ**ยอดคงเหลือ** ไม่ใช่ไม้ — เหรียญที่คนโอนเข้ามาเอง
        กับเหรียญที่ระบบซื้อไว้อยู่ในยอดเดียวกันแยกไม่ออก · การตีความยอดทั้งก้อนว่าเป็น
        ไม้ของระบบคือการอ้างสิทธิ์ในของที่ไม่ใช่ของเรา แล้วสัญญาณขายครั้งถัดไปจะขาย
        เหรียญของคนไปด้วย · จึงนับเฉพาะสิ่งที่ **ledger บอกว่าเราเปิดเอง** (`open_trade_id`
        + `position_qty_after` ของ fill ล่าสุด) ส่วนที่เกินมาไม่ใช่ของระบบ
        """
        if self.market == PERP:
            return self._perp_positions()
        # ขั้น 3 เรียก `positions()` **ก่อน** `open_orders()` · บน spot คำตอบมาจาก ledger
        # ไม่ใช่จาก venue ถ้าไม่อ่านกลับก่อน ไม้ที่ถูกขายไปตอน process ดับจะยังโผล่เป็น
        # ของที่ถืออยู่ตลอดทั้งแท่งนี้ — perp ไม่มีปัญหานี้เพราะมันถาม venue ตรงๆ
        for symbol in self.symbols:
            self._sync(symbol)
        return [p for symbol in self.symbols if (p := self._spot_position(symbol)) is not None]

    def _perp_positions(self) -> list[Position]:
        try:
            rows = self.client.fetch_positions()
        except TRADING_ERRORS as error:
            raise BrokerError(f"อ่านสถานะที่ปลายทางไม่สำเร็จ: {error}") from error

        out: list[Position] = []
        for row in rows:
            qty = float(row.get("contracts") or 0.0)
            if qty <= 0:
                continue
            out.append(
                Position(
                    # รูปสั้นเสมอ — ขั้น 3 จับคู่กับ `symbol` ของ config ตรงตัว
                    # (`p.symbol == symbol`) ถ้าคืน `BTC/USDT:USDT` การจับคู่จะพลาด
                    # เงียบๆ แล้วไปป์ไลน์จะเชื่อว่าไม่มีไม้ แล้วเปิดทับของที่ถืออยู่
                    symbol=store_symbol(str(_need(row, "symbol"))),
                    side=str(_need(row, "side")),
                    qty=qty,
                    entry_px=float(_need(row, "entryPrice")),
                    mark_px=float(_need(row, "markPrice")),
                    unrealized_pnl=price_to_db(float(row.get("unrealizedPnl") or 0.0)),
                    leverage=float(_need(row, "leverage")),
                    liquidation_px=(
                        None if row.get("liquidationPrice") is None
                        else float(row["liquidationPrice"])
                    ),
                )
            )
        return out

    def _spot_position(self, symbol: str) -> Position | None:
        held = reconcile.ledger_position(self.conn, self.profile, self.market, symbol)
        if held is None:
            return None
        side, qty, entry_px = held
        try:
            mark = float(_need(self.client.fetch_ticker(unified_symbol(symbol, self.market)), "last", "close"))
        except TRADING_ERRORS as error:
            raise BrokerError(f"อ่านราคาล่าสุดของ {symbol} ไม่สำเร็จ: {error}") from error
        return Position(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_px=entry_px,
            mark_px=mark,
            unrealized_pnl=price_to_db((mark - entry_px) * qty),
            leverage=1.0,
            # spot ไม่มี liquidation อยู่จริง ไม่ใช่คำนวณไม่ได้ (ADR 26)
            liquidation_px=None,
        )

    def balance(self) -> Balance:
        try:
            row = self.client.fetch_balance()
        except TRADING_ERRORS as error:
            raise BrokerError(f"อ่านยอดเงินไม่สำเร็จ: {error}") from error
        wallet = row.get(self.quote) or {}
        if not wallet:
            raise BrokerError(
                f"กระเป๋าของ {self.market} ไม่มีสกุล {self.quote} — "
                "ทุกเพดานความเสี่ยงคิดเป็น % ของยอดนี้ ค่าที่เดาไม่ได้ต้องไม่ถูกเดา"
            )
        return Balance(
            quote=self.quote,
            free=price_to_db(float(wallet.get("free") or 0.0)),
            used=price_to_db(float(wallet.get("used") or 0.0)),
            total=price_to_db(float(_need(wallet, "total"))),
        )

    # ── ตั้งค่าที่ปลายทาง ────────────────────────────────────────────────────

    def set_leverage(self, symbol: str, leverage: float) -> None:
        """ตั้งทุกครั้งก่อนเปิดไม้ — ค่าที่ exchange ถูกเปลี่ยนจากนอกระบบได้ (spec/06)"""
        if self.market == SPOT:
            raise BrokerError("spot ไม่มี leverage — บังคับเป็น 1 ตั้งแต่ config (ADR 26)")
        usym = unified_symbol(store_symbol(symbol), self.market)
        try:
            self.client.set_leverage(leverage, usym)
        except TRADING_ERRORS as error:
            raise BrokerError(f"ตั้ง leverage {leverage} ที่ {symbol} ไม่สำเร็จ: {error}") from error

    def set_margin_mode(self, symbol: str, mode: str, position_mode: str) -> None:
        """`isolated` + `one_way` เท่านั้น — ทั้งคู่เป็นข้อบังคับของระบบ ไม่ใช่ตัวเลือก

        venue ที่ตั้งค่าเดิมอยู่แล้วตอบกลับเป็น error ("No need to change margin type",
        code `-4046`) ซึ่ง**ไม่ใช่ความล้มเหลว** · ถ้ายกขึ้นไป แท่งที่สองของทุกวันจะล้ม
        ทั้งที่ค่าที่ปลายทางถูกต้องแล้ว

        **กลืนเฉพาะ `MarginModeAlreadySet` ไม่ใช่ error ทุกชนิดของ ccxt** — binance ตั้ง
        `options.setMarginMode.throwMarginModeAlreadySet = True` ไว้เอง กรณีนี้จึงมาถึงเรา
        เป็นคลาสของตัวเองแยกจากอย่างอื่นอยู่แล้ว · การดักกว้างกว่านี้จะกลืน auth ที่หมดอายุ
        สิทธิ์ที่ไม่พอ และเน็ตที่ล่ม ไปพร้อมกัน แล้วระบบจะเปิดไม้ต่อด้วย margin mode ที่
        อาจเป็น cross ทั้งที่สูตร liquidation ทั้งไฟล์คิดบน isolated (`paper.py` §สูตร liquidation)
        """
        if self.market == SPOT:
            raise BrokerError("spot ไม่มี margin mode")
        if mode != "isolated":
            raise BrokerError(f"ระบบรองรับเฉพาะ isolated ไม่ใช่ {mode!r} (spec/07)")
        if position_mode != "one_way":
            raise BrokerError("hedge ไม่ใช่ตัวเลือกที่ปิดไว้ แต่เป็นค่าที่ระบบไม่รองรับ (spec/07)")
        usym = unified_symbol(store_symbol(symbol), self.market)
        try:
            self.client.set_margin_mode(mode, usym)
        except ccxt.MarginModeAlreadySet:
            log.info("margin mode ของ %s เป็น %s อยู่แล้ว ไม่ต้องเปลี่ยน", symbol, mode)
        except TRADING_ERRORS as error:
            raise BrokerError(f"ตั้ง margin mode {mode} ที่ {symbol} ไม่สำเร็จ: {error}") from error

    def maintenance_margin(self, symbol: str, notional: float) -> float | None:
        """อัตรา maintenance margin ของ **ชั้นที่ notional นี้ตกอยู่** เป็นเปอร์เซ็นต์

        ขั้น 12 ตรวจ `min_liq_buffer_pct` กับไม้ที่ยังไม่เปิด จึงถามราคา liquidation จาก
        `positions()` ไม่ได้ ต้องคำนวณเอง · config ของ live ห้ามถืออัตรานี้ (`cross_checks()`
        เขียนว่า "ราคา liquidation มาจาก exchange") ตัวที่ตอบได้จึงมีแต่ที่นี่

        **ต้องเลือกตามชั้น ไม่ใช่หยิบชั้นแรกเสมอ** — อัตราของ Binance โตตามขนาดไม้ ชั้นแรก
        ต่ำที่สุด การใช้มันกับไม้ใหญ่คือการรายงานว่าไม้ห่าง liquidation มากกว่าความจริง
        ซึ่งทำให้ด่านที่มีไว้กัน liquidation ปล่อยผ่านไม้ที่ควรถูกปฏิเสธ

        venue ที่ไม่บอกชั้นมาเลย = `None` แล้วด่านปฏิเสธไม้นั้น (fail-closed, spec/06) —
        ไม่ใช่เดาอัตรามาตรฐานให้
        """
        if self.market == SPOT:
            return None
        key = store_symbol(symbol)
        if key not in self._tiers:
            try:
                self._tiers[key] = self.client.fetch_market_leverage_tiers(
                    unified_symbol(key, self.market)
                )
            except TRADING_ERRORS as error:
                raise BrokerError(f"อ่านชั้น maintenance margin ของ {key} ไม่สำเร็จ: {error}") from error
        tiers = [t for t in self._tiers[key] if t.get("maintenanceMarginRate") is not None]
        if not tiers:
            log.warning("venue ไม่บอกชั้น maintenance margin ของ %s — ด่าน liq_buffer จะปฏิเสธไม้", key)
            return None
        tiers.sort(key=lambda t: float(t.get("maxNotional") or float("inf")))
        for tier in tiers:
            ceiling = tier.get("maxNotional")
            if ceiling is None or notional <= float(ceiling):
                return float(tier["maintenanceMarginRate"]) * 100
        # เกินชั้นสูงสุดที่ venue ประกาศ — ใช้ชั้นที่แพงที่สุด ไม่ใช่ปล่อยผ่าน
        return float(tiers[-1]["maintenanceMarginRate"]) * 100

    # ── ตัวช่วย ──────────────────────────────────────────────────────────────

    def _sync(self, symbol: str) -> None:
        """อ่านความจริงกลับแล้วเขียน ledger — ตะเข็บเดียวกับ `PaperBroker._settle()`

        ไม่มีแท่งที่ปิดแล้วเลย = ยังไม่มี "แท่งที่เรามารู้" ให้อ้าง · ข้ามไปเงียบๆ ไม่ได้
        เพราะจะแปลว่า fill เงียบหาย จึงดังไว้ใน log แล้วรอแท่งถัดไป (ขั้น 1 ของไปป์ไลน์
        ข้ามเหรียญนั้นอยู่แล้วด้วยเหตุผลเดียวกัน)
        """
        closed = self.bars.bars(symbol, self.timeframe)
        if not closed:
            log.warning("ยังไม่มีแท่งที่ปิดแล้วของ %s — ยังอ่าน fill กลับไม่ได้", symbol)
            return
        reconcile.sync(
            self.conn,
            self.client,
            profile=self.profile,
            market=self.market,
            symbol=symbol,
            bar_close_ts=closed[-1].close_ts,
        )

    def _symbol_for(self, order_id: str) -> str:
        symbol = self._symbol_of.get(order_id)
        if symbol is None:
            raise BrokerError(
                f"ไม่รู้ว่าออเดอร์ {order_id!r} อยู่เหรียญไหน — ccxt ต้องมี symbol ทุกครั้งที่ "
                "ยกเลิกหรือแก้ออเดอร์ · id ต้องมาจาก `open_orders()` ของแท่งเดียวกัน"
            )
        return symbol
