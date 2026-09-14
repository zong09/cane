"""โปรโตคอล flip — สองขาในแท่งเดียว จุดที่พังแล้วเปิดสถานะสวนกัน (spec/03)

```
ขา 1  ปิดฝั่งเดิม (reduceOnly)
        │
        ├─ fill ครบ ──▶ ขา 2  เปิดฝั่งใหม่
        │
        └─ ไม่ fill ครบ ──▶ ยกเลิกขา 2 ทั้งหมด
                            skip_reason = flip_aborted
                            จบรอบ ไม่แตะ symbol นี้อีกจนแท่งถัดไป
```

**abort ไม่ใช่ retry** — ถ้าขา 1 ปิดได้ไม่หมดแล้วขา 2 เดินต่อ ระบบจะเปิดสถานะสวนกับ
ของเดิมที่ยังค้างอยู่ ซึ่งละเมิด one-way โดยตรง และบน perp มันคือการถือความเสี่ยงสองทาง
พร้อมกันโดยที่ไม่มีชั้นไหนตั้งใจให้เกิด

## ทำไมฟังก์ชันนี้เป็นที่เดียวที่ยิงสองขา

ลำดับ "ปิดก่อนเปิด" จะเป็นจริงก็ต่อเมื่อไม่มีใครยิงสองขานั้นได้จากที่อื่น · ถ้าปล่อยให้
ชั้นบนถือ `BarPlan` แล้วยิงเอง การสลับลำดับจะเป็นบั๊กที่เขียนได้ง่ายมากและตรวจจาก
diff ไม่เห็น — ที่นี่รับออเดอร์ทั้งสองใบพร้อมกันและเป็นผู้ตัดสินใจเองว่าจะยิงใบที่สอง
ไหม ผู้เรียกไม่มีทางยิงขาเปิดก่อนได้แม้จะเขียนผิด

## "fill ครบ" วัดด้วยขั้นของ lot ไม่ใช่ด้วย epsilon ที่ตั้งเอง

`qty` เป็น `float` การเทียบ `filled == ordered` ตรงๆ จะสะดุด noise ระดับ 1e-17 แล้ว
abort ทุกครั้งโดยไม่มีอะไรผิดจริง · แต่ตัวเลข epsilon ที่ตั้งขึ้นเองก็เป็นเกณฑ์ที่
ไม่มีใครอธิบายได้ · เกณฑ์ที่มีความหมายจริงคือ **ขั้นของ lot ที่ venue รับ**: เศษที่
เล็กกว่าหนึ่งขั้นปิดไม่ได้ด้วยออเดอร์ใดๆ ทั้งสิ้น มันจึงไม่ใช่ของค้างที่คนต้องไปปิด
· ผู้เรียกส่ง `step` เข้ามาเพราะมันเป็นค่าของ venue ไม่ใช่ของเรา (ใบ 07 `LotFilter`)

## ของที่ค้างหลัง abort — ระบบไม่จัดการเอง

ADR 19 · ส่วนที่ปิดไม่ลงยังเป็นสถานะเปิดที่ exchange และระบบ **จะไม่พยายามปิดมันเอง
ในรอบถัดไป** ทางแก้อัตโนมัติทุกทางทำให้ระบบมีเส้นทางที่ยิงคำสั่งนอกจังหวะสัญญาณ
ซึ่งเปิดพื้นที่ให้บั๊กในโหมดที่ทดสอบยากที่สุด

ข้อแลกเปลี่ยนคือมันต้อง **มองเห็นได้ทันที** — `FlipResult.residual_qty` คือค่าที่ลง
`decisions.residual_qty` และคอนโซลต้องแสดงเป็นสถานะที่ต้องคนจัดการ · ที่นี่มีหน้าที่
รายงานให้ครบ ไม่ใช่ซ่อนว่ามีเศษเหลือ
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cane.execution.broker import Order, check_market_supports

if TYPE_CHECKING:
    from cane.execution.broker import Broker, OrderResult

#: สถานะของออเดอร์ที่แปลว่า "จบแล้ว" — ออเดอร์ที่ยังค้าง (`open`) ไม่นับว่า fill ครบ
#: แม้ `filled_qty` จะเท่ากับที่สั่ง เพราะมันยังเปลี่ยนได้หลังจากนี้
FILLED_STATUS = "closed"


@dataclass(frozen=True, slots=True)
class FlipResult:
    """ผลของทั้งรอบ · ชื่อฟิลด์ตรงกับคอลัมน์ของ `DecisionRecord` (ใบ 03)

    `open` เป็น `None` เมื่อ abort — **ไม่ใช่ `OrderResult` ที่ status ว่าง** เพราะ
    ขาที่ไม่เคยถูกส่งกับขาที่ส่งแล้วไม่สำเร็จเป็นคนละเรื่องตอนอ่านย้อนหลัง

    `residual_qty` คือส่วนที่ปิดไม่ลง (ADR 19) · เป็น 0.0 เมื่อปิดครบ และค่าที่เล็ก
    กว่าหนึ่งขั้นของ lot ถูกปัดเป็น 0.0 เพราะมันปิดไม่ได้ด้วยออเดอร์ใดๆ อยู่แล้ว
    """

    close: OrderResult
    open: OrderResult | None
    close_qty_intended: float
    close_qty_filled: float
    residual_qty: float
    skip_reason: str | None

    @property
    def aborted(self) -> bool:
        return self.skip_reason == "flip_aborted"


def execute_flip(
    broker: Broker,
    *,
    close_order: Order,
    open_order: Order,
    step: float,
) -> FlipResult:
    """ยิงขาปิดก่อน แล้วยิงขาเปิด**ก็ต่อเมื่อ**ขาปิด fill ครบ

    `close_order` ต้องเป็น `reduce_only` — ขาปิดที่ไม่มีธงนี้ปิดเกินขนาดที่ถืออยู่ได้
    แล้วกลายเป็นการเปิดสวนทันที ซึ่งคือความล้มเหลวข้อเดียวกับที่ทั้งไฟล์นี้มีไว้กัน
    · ตรวจที่นี่แม้ `check_market_supports()` จะตรวจเรื่อง spot ให้แล้ว เพราะสองข้อ
    นี้กันคนละอย่าง

    **ไม่มี flip บน spot** (spec/03) — `check_market_supports()` ปฏิเสธ `reduce_only`
    บน spot อยู่แล้ว ด่านนั้นจึงกันเส้นทางนี้ให้โดยอัตโนมัติ ไม่ต้องมี `if market`
    ที่สองให้ดูแลว่าตรงกันไหม
    """
    if step <= 0:
        raise ValueError(f"step ต้องมากกว่าศูนย์ ไม่ใช่ {step}")
    if not close_order.reduce_only:
        raise ValueError("ขาปิดของ flip ต้องเป็น reduce_only ไม่งั้นมันเปิดสวนได้เอง")
    if open_order.reduce_only:
        raise ValueError("ขาเปิดของ flip ต้องไม่เป็น reduce_only")
    if close_order.symbol != open_order.symbol:
        raise ValueError(
            f"สองขาต้องเป็นเหรียญเดียวกัน ไม่ใช่ {close_order.symbol!r} กับ "
            f"{open_order.symbol!r}"
        )
    check_market_supports(close_order, broker.market)
    check_market_supports(open_order, broker.market)

    closed = broker.place(close_order)
    residual = _residual(close_order.qty, closed, step)

    if _unresolved(closed, residual):
        # ยกเลิกขา 2 ทั้งหมด · **ไม่ยิงแล้วค่อยยกเลิก** — ไม่ยิงเลย
        return FlipResult(
            close=closed,
            open=None,
            close_qty_intended=close_order.qty,
            close_qty_filled=closed.filled_qty,
            residual_qty=residual,
            skip_reason="flip_aborted",
        )

    return FlipResult(
        close=closed,
        open=broker.place(open_order),
        close_qty_intended=close_order.qty,
        close_qty_filled=closed.filled_qty,
        residual_qty=0.0,
        skip_reason=None,
    )


def _residual(intended: float, result: OrderResult, step: float) -> float:
    """**เหลือเท่าไหร่** · เศษที่เล็กกว่าหนึ่งขั้นของ lot นับเป็นศูนย์ (ดูหัวไฟล์)

    ตอบคำถามเดียวและไม่ตัดสินใจอะไร — `_unresolved()` เป็นตัวตัดสินว่าจะ abort ไหม
    · สองคำถามนี้เคยถูกยุบเป็นฟังก์ชันเดียวแล้วอ่านไม่ออกว่าค่าที่คืนมาแปลว่า
    "เหลือเท่านี้" หรือ "เชื่อไม่ได้"
    """
    gap = max(0.0, intended - result.filled_qty)
    return gap if gap >= step else 0.0


def _unresolved(result: OrderResult, residual: float) -> bool:
    """**ต้อง abort ไหม** · มีสองเหตุ และเหตุที่สองไม่ได้อยู่ในตัวเลข

    เหตุแรกชัดเจน: ยังปิดไม่หมด · เหตุที่สองคือออเดอร์ที่ยังไม่จบ (`status != closed`)
    ซึ่ง **abort แม้ `residual` จะเป็นศูนย์** — ค่าที่อ่านจากออเดอร์ที่ยังวิ่งอยู่คือ
    ค่าที่เปลี่ยนได้หลังเราอ่าน การเปิดฝั่งใหม่ทับขาปิดที่ยังไม่นิ่งคือการเดิมพันว่า
    มันจะ fill ครบ ซึ่งเป็นการเดิมพันที่แพ้แล้วได้สถานะสวนกันพอดี
    """
    return residual > 0.0 or result.status != FILLED_STATUS
