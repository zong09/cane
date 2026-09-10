"""สัญญาระหว่างชั้นตัดสินใจกับปลายทาง — ชั้นนี้ไม่รู้ว่าปลายทางเป็นอะไร (ADR 3)

ตรรกะการเทรดทั้งหมดอยู่**เหนือ**ไฟล์นี้ และต้องเขียนได้โดยไม่รู้ว่ากำลังคุยกับ
`PaperBroker` หรือ `CcxtBroker` อยู่ — นั่นคือสิ่งที่ทำให้ ADR 9 ("paper กับ live
ต่างที่ค่า ไม่ต่างที่ตรรกะ") เป็นจริงได้ ไม่ใช่แค่ประกาศไว้

**ทำไม interface มี order type** — cold start ทางที่ 2 บังคับให้วาง stop ที่เส้น
Slow Trail ทันทีที่เปิดไม้ (spec/03) ถ้า broker ทำได้แค่ market order ทางเดียวที่
เหลือคือประเมิน stop ฝั่ง engine ตอนแท่งรายวันปิด ซึ่งบน perp ที่มี leverage คือ
การป้องกันที่ตรวจวันละครั้ง ราคากระโดดข้ามคืนเดียวถึง liquidation ได้ก่อน engine
ตื่น · จึงวาง stop ไว้ที่ exchange จริง (ADR 17)

**ทำไมมี `replace`** — เส้น Slow Trail ขยับทุกแท่ง ถ้าไม่มี ต้อง cancel แล้ว place
ใหม่ ซึ่งเปิดหน้าต่างเวลาที่ไม้ไม่มี stop คุ้มอยู่

**ทำไมมี `open_orders`** — reconcile ต้องเห็น stop ที่ค้างอยู่ ไม่ใช่ดูแค่ position
ไม้ที่มี stop กับไม้ที่ stop หลุดไปแล้วมี position เหมือนกันทุกประการ (spec/06)

**หนึ่ง broker ต่อหนึ่งตลาด** — ท่าเดียวกับ `make_client(exchange, market)` ของชั้น
ข้อมูล เหตุผลตรงกัน: กระเป๋าเงินของ futures กับของ spot เป็นคนละใบ `balance()` จึง
เป็นค่าต่อตลาดอยู่แล้ว · และมันทำให้ `Order` ไม่ต้องพก `market` ไปทุกใบเพียงเพื่อให้
ปลายทางตัดสินใจซ้ำ
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from cane.db.types import store_symbol

#: ชนิดของออเดอร์ที่ระบบยิง — ตรงกับ `ck_decision_orders_order_type` ใน schema
ORDER_TYPES = ("market", "stop_market")
#: ฝั่งของ**ออเดอร์** ตรงกับ `order_side_t` — ไม่ใช่ฝั่งของไม้ (`side_t`)
ORDER_SIDES = ("buy", "sell")
#: ขาของออเดอร์ในแท่งหนึ่ง ตรงกับ `leg_t`
LEGS = ("open", "close", "stop")
#: ตลาดที่รองรับ ตรงกับ `ck_config_symbols_market`
MARKETS = ("usdtm_perp", "spot")

#: คำนำหน้าของ `clientOrderId` — ทำให้ออเดอร์ของระบบนี้แยกออกจากออเดอร์ที่คนไปกดเอง
#: ที่หน้าเว็บของ venue ซึ่ง reconcile ต้องมองเห็นแต่ต้องไม่นับว่าเป็นของตัวเอง
ID_PREFIX = "cane"


def client_order_id(symbol: str, bar_close_ts: int, order_side: str, leg: str) -> str:
    """`cane-{SYMBOL}-{bar_close_ts}-{side}-{leg}` แบบ deterministic (spec/06)

    คำนวณจากสิ่งที่รู้อยู่แล้วก่อนพยายามส่ง ไม่ใช่ค่าที่สุ่มตอนส่ง — process ที่ตาย
    กลางคันแล้วกลับมาในแท่งเดิมจะสร้าง id เดิมเป๊ะ แล้ว venue ปฏิเสธหรือคืนผลเดิม
    แทนที่จะเปิดสถานะซ้อน

    **`side` คือฝั่งของออเดอร์ ไม่ใช่ฝั่งของไม้** — ใบ 03 ตัดสินไว้แล้วที่
    `schema.py` (`decision_orders.order_side`) และเหตุผลของ spec/06 เองยืนยันซ้ำ:
    ที่ `leg` ขาดไม่ได้เพราะ "flip ยิงสองขาในแท่งเดียวและ**ฝั่งเดียวกัน**" จะจริงก็
    ต่อเมื่อ side หมายถึง buy/sell เท่านั้น — flip จาก short เป็น long คือปิด short
    (`buy`) แล้วเปิด long (`buy`) สองครั้งติดกัน ถ้า side เป็นฝั่งไม้ ขาสองขาจะได้
    `short` กับ `long` ที่ต่างกันอยู่แล้ว และ `leg` จะไม่จำเป็นด้วยเหตุผลนั้น

    **ไม่มี `market` ในกุญแจ** — CHECK บังคับหนึ่งเหรียญหนึ่งตลาดอยู่แล้ว (ADR 26)
    `SYMBOL` จึงชี้ตลาดได้ตัวเดียว การเติมเข้าไปเป็นข้อมูลซ้ำที่ยืด id โดยไม่กันอะไร

    รูปของ symbol เป็นรูปสั้นเดียวกับที่ config และตารางใช้ (`BTC/USDT`) ผ่าน
    `store_symbol()` ไม่ใช่ unified symbol ของ ccxt (`BTC/USDT:USDT`) — id ที่
    เปลี่ยนรูปตามตลาดจะทำให้ระบบสร้าง id คนละตัวสำหรับแท่งเดียวกัน ซึ่งเป็นสิ่ง
    เดียวที่ id นี้มีหน้าที่ป้องกัน
    """
    if order_side not in ORDER_SIDES:
        raise ValueError(f"order_side ต้องเป็นหนึ่งใน {ORDER_SIDES} ไม่ใช่ {order_side!r}")
    if leg not in LEGS:
        raise ValueError(f"leg ต้องเป็นหนึ่งใน {LEGS} ไม่ใช่ {leg!r}")
    return f"{ID_PREFIX}-{store_symbol(symbol)}-{bar_close_ts}-{order_side}-{leg}"


@dataclass(frozen=True, slots=True)
class Order:
    """ออเดอร์หนึ่งใบที่จะยิง — `client_order_id` คิดไว้ก่อนส่งเสมอ

    ไม่มีฟิลด์ `market` โดยเจตนา ตลาดเป็นของ broker ที่ถือออเดอร์ใบนี้อยู่
    """

    symbol: str
    side: str
    type: str
    qty: float
    client_order_id: str
    reduce_only: bool = False
    stop_px: float | None = None

    def __post_init__(self) -> None:
        if self.side not in ORDER_SIDES:
            raise ValueError(f"side ต้องเป็นหนึ่งใน {ORDER_SIDES} ไม่ใช่ {self.side!r}")
        if self.type not in ORDER_TYPES:
            raise ValueError(f"type ต้องเป็นหนึ่งใน {ORDER_TYPES} ไม่ใช่ {self.type!r}")
        if self.qty <= 0:
            raise ValueError(f"qty ต้องมากกว่าศูนย์ ไม่ใช่ {self.qty}")
        # ทั้งสองทางเป็น fail-closed ข้อเดียวกัน: ออเดอร์ที่ไม่ครบรูปห้ามหลุดไปถึง
        # ปลายทางแล้วให้ปลายทางตีความเอง — `stop_market` ที่ไม่มีราคาคือออเดอร์ที่
        # ไม่รู้ว่าจะทำงานเมื่อไร ส่วน `stop_px` บน market order คือค่าที่ไม่มีใครอ่าน
        # แล้วคนเขียนโค้ดเชื่อว่ามันมีผล
        if self.type == "stop_market" and self.stop_px is None:
            raise ValueError("stop_market ต้องมี stop_px")
        if self.type == "market" and self.stop_px is not None:
            raise ValueError("market order ไม่รับ stop_px")


@dataclass(frozen=True, slots=True)
class OrderResult:
    """ผลของการยิงหนึ่งครั้ง — เก็บสิ่งที่ ledger ต้องเขียนลง `fills` ครบ

    `fee_quote` กับ `fee_unavailable_reason` เป็นคู่ที่ห้ามมีพร้อมกัน (CHECK ของ
    `fills`) — venue ที่ไม่คืนค่าธรรมเนียมมาในผลของออเดอร์มีจริง การใส่ศูนย์แทน
    จะทำให้รายงานอ้างว่าไม้นั้นไม่มีค่าธรรมเนียม ซึ่งต่างจาก "ยังไม่รู้"
    """

    client_order_id: str
    venue_order_id: str | None
    status: str
    filled_qty: float
    avg_px: float | None = None
    fee_quote: Decimal | None = None
    fee_ccy: str | None = None
    fee_unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.fee_quote is not None and self.fee_unavailable_reason is not None:
            raise ValueError("fee_quote กับ fee_unavailable_reason มีพร้อมกันไม่ได้")
        # ยอดที่ไม่รู้สกุลบวกเข้ารายงานไม่ได้ — CHECK ของ `fills` ปฏิเสธเหมือนกัน
        if self.fee_quote is not None and self.fee_ccy is None:
            raise ValueError("fee_quote ต้องมาคู่กับ fee_ccy")


@dataclass(frozen=True, slots=True)
class OpenOrder:
    """ออเดอร์ที่ยังค้างอยู่ที่ปลายทาง — reconcile อ่านของจริงทุกแท่ง ไม่เชื่อความจำ"""

    venue_order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    type: str
    qty: float
    stop_px: float | None = None
    reduce_only: bool = False


@dataclass(frozen=True, slots=True)
class Position:
    """สถานะที่ถืออยู่จริงที่ปลายทาง

    `liquidation_px` เป็น `None` บน spot เพราะตลาดนั้น**ไม่มี** liquidation ซึ่ง
    คนละความหมายกับ "คำนวณไม่ได้" (ADR 26) · ชั้น risk ของ spot จึงไม่เรียก
    `min_liq_buffer_pct` เลย ไม่ใช่เรียกแล้วผ่านเสมอ (spec/06)
    """

    symbol: str
    side: str
    qty: float
    entry_px: float
    #: ราคาที่ใช้ตีมูลค่าไม้ ณ ตอนอ่าน — `max_daily_loss_pct` นับ mark-to-market
    mark_px: float
    unrealized_pnl: Decimal
    leverage: float
    liquidation_px: float | None = None

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError(f"side ของไม้ต้องเป็น long หรือ short ไม่ใช่ {self.side!r}")


@dataclass(frozen=True, slots=True)
class Balance:
    """ยอดเงินสกุลอ้างอิงของตลาดนี้ — กระเป๋า futures กับ spot เป็นคนละใบ

    เป็น `Decimal` ไม่ใช่ `float` เพราะมันถูกบวกกับ fee และ funding ที่ต้องรวมแล้ว
    ตรงกับใบแจ้งของ venue (`db/types.py`)
    """

    quote: str
    free: Decimal
    used: Decimal
    total: Decimal


def check_market_supports(order: Order, market: str) -> None:
    """สิ่งที่ตลาด `spot` **ไม่มี** ต้องถูกปฏิเสธ ไม่ใช่ส่งไปแล้วให้ปลายทางเมิน

    `reduce_only` บน spot ไม่มีความหมาย — การขายคือการขายของที่ถืออยู่ ไม่ใช่ธง
    ของออเดอร์ · ถ้าปล่อยผ่านเงียบๆ ขาปิดของ flip จะดูเหมือนถูกกันไว้แล้วทั้งที่
    ไม่มีอะไรกัน และคนอ่าน `decision_orders` ย้อนหลังจะเห็นธงที่ไม่เคยมีผล

    (flip ไม่เกิดบน spot อยู่แล้วตาม spec/03 ด่านนี้จึงเป็นตัวจับบั๊กของชั้นบน
    ไม่ใช่เส้นทางปกติ — ซึ่งเป็นเหตุผลที่มันต้องดัง ไม่ใช่เงียบ)
    """
    if market not in MARKETS:
        raise ValueError(f"market ต้องเป็นหนึ่งใน {MARKETS} ไม่ใช่ {market!r}")
    if market == "spot" and order.reduce_only:
        raise ValueError("spot ไม่มี reduce_only — การขายคือการขายของที่ถืออยู่")


class Broker(Protocol):
    """ปลายทางหนึ่งตัวต่อหนึ่งตลาด — `PaperBroker` (ใบนี้) และ `CcxtBroker` (ใบ 13)

    **ผู้เรียกต้องอ่านสถานะจริงก่อนตัดสินใจทุกครั้ง** ทั้ง `positions()` และ
    `open_orders()` (spec/06, spec/08 ขั้น 3) — สถานะจริงเปลี่ยนได้จากนอกระบบ
    ทั้งคนไปกดเอง order ถูก fill ทีหลัง stop ทำงานไปแล้ว หรือ venue มีปัญหา
    """

    #: ตลาดของ broker ตัวนี้ — ผู้เรียกใช้เลือกชั้น risk ที่ต้องเดิน (spec/06)
    market: str

    def place(self, order: Order) -> OrderResult: ...

    def cancel(self, order_id: str) -> None: ...

    def replace(self, order_id: str, stop_px: float) -> OrderResult: ...

    def open_orders(self, symbol: str) -> list[OpenOrder]: ...

    def positions(self) -> list[Position]: ...

    def balance(self) -> Balance: ...

    def set_leverage(self, symbol: str, leverage: float) -> None: ...

    def set_margin_mode(self, symbol: str, mode: str, position_mode: str) -> None: ...
