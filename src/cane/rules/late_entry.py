"""cold start — ข้อยกเว้นเดียวของกฎไม้เรียวทั้งระบบ (spec/03)

บอทที่รันต่อเนื่องเห็นทุกสัญญาณตอนแท่งปิด **มันตกรถไม่ได้** · ไฟล์นี้จึงมีไว้สำหรับ
สถานการณ์เดียว: engine เพิ่งเริ่มทำงานขณะที่เทรนด์เดินไปแล้ว

## ทางเข้ามีทางเดียว และมันคือ `cane_rule`

`late_entry()` รับ `BarPlan` ของ `rules/cane.py` เข้ามา แล้ว **ทำงานเฉพาะเมื่อ
`skip_reason == "cane_rule"`** เท่านั้น · เขียนแบบนี้แทนที่จะรับสัญญาณดิบเพราะมันทำ
ให้ "cold start เป็นข้อยกเว้นของกฎไม้เรียว" เป็นจริงตามรูปของโค้ด — ผู้เรียกจะแอบ
เข้าไม้ที่แท่งซึ่งกฎอื่นปฏิเสธไปแล้วไม่ได้ ต้องผ่าน `decide()` ก่อนเสมอ

## ไม่มี flag `cold_start_done` และห้ามมี

คอนโซลมีปุ่ม start engine กดกี่ครั้งก็ได้ **ทุกครั้งที่ engine เริ่มคือ cold start
ครั้งใหม่** · ถ้าเก็บ flag ลงดิสก์ เส้นทางนี้จะถูกปิดถาวรหลัง boot ครั้งแรก ทั้งที่
มันควรทำงานได้อีกในรอบการรันถัดไป

**ตัวกันของจริงคือ "ต้องไม่มีสถานะเปิดทั้งสองฝั่ง"** ไม่ใช่ flag — ไฟล์นี้จึงไม่มี
สถานะภายในเลยสักตัว มันเป็นฟังก์ชันบริสุทธิ์ที่ตัดสินจากสิ่งที่อ่านมาจากปลายทาง
ทุกครั้ง ซึ่งแปลว่ากดปุ่มรัวๆ ระหว่างเทรนด์ก็ได้ผลเดิมทุกครั้งโดยอัตโนมัติ

## RR ≥ 2:1 — สเปกบังคับไว้แต่ไม่เคยนิยาม "เป้ากำไร"

spec/03:135 กับ spec/00:24 บังคับ RR ≥ 2:1 · ความเสี่ยงชัดเจน (`entry − Trail2`)
แต่เป้ากำไรไม่มีนิยามอยู่ที่ไหน และ ADR 13 ตัดเป้าราคาออกจากระบบไปแล้ว ส่วนเส้นทาง
ปกติก็ออกที่สัญญาณฝั่งตรงข้ามซึ่งไม่รู้ราคาล่วงหน้า

**เจ้าของตัดสินแล้ว (2026-09-14): เป้ากำไรคือจุดเหวี่ยงล่าสุดที่ยืนยันแล้ว** —
`swing_highs[-1]` สำหรับฝั่ง long, `swing_lows[-1]` สำหรับฝั่ง short ซึ่ง `features()`
ของใบ 05 คำนวณไว้อยู่แล้ว · ไม่ขัด ADR 13 เพราะมันเป็นโครงสร้างที่คำนวณจากข้อมูล
ไม่ใช่ตัวเลขที่ hardcode มาจากบทวิเคราะห์ในเอกสารต้นทาง

ผลที่ตามมาโดยตั้งใจ: **ตลาดที่เพิ่งทำจุดสูงสุดใหม่จะไม่มีเป้าอยู่ข้างหน้า** (reward
ติดลบ) แล้ว cold start ไม่เข้า — ซึ่งตรงกับสิ่งที่กฎไม้เรียวห้ามอยู่แล้วพอดี
คือการไล่ราคาที่ยอด

## ขนาดไม้ใช้ `base_pct` ตรงๆ ไม่เรียก Judge

ไม่ใช่แท่งสัญญาณ จึงไม่มีปัจจัยสนับสนุนให้ตัดสิน (spec/03, spec/05:76) · ผู้เรียกส่ง
`factors_present = 0` เข้า `sizing.plan_size()` ซึ่งให้ `base_pct` พอดีโดยไม่ต้องมี
สูตรที่สองให้ดูแลว่าตรงกัน
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cane.execution.broker import Order, client_order_id

if TYPE_CHECKING:
    from cane.execution.broker import Broker, OpenOrder, OrderResult
    from cane.indicators.features import Features
    from cane.rules.cane import BarPlan

#: เส้นทางที่ config เลือกได้ · `None` หรือ `"skip"` = ไม่เข้าเส้นทางนี้ (fail-closed)
ROUTES = ("wait_1h", "trailing")

#: RR ขั้นต่ำของเส้นทาง `trailing` (spec/03:135, spec/00:24)
MIN_REWARD_TO_RISK = 2.0

#: `decision_stop.action` ของใบ 03 — ชุดปิด ไม่ใช่ข้อความอิสระ
STOP_ACTIONS = ("placed", "replaced", "unchanged", "missing")

#: ฝั่งของ**ออเดอร์** ที่ปิดไม้ฝั่งนั้น — stop ของ long คือคำสั่งขาย
_CLOSING_ORDER_SIDE = {"long": "sell", "short": "buy"}


@dataclass(frozen=True, slots=True)
class ColdStartPlan:
    """ผลของการพิจารณา cold start · `side is None` แปลว่าไม่เข้า

    `skip_reason` เลือกจาก **ประตูแรกที่ปิด** ตามธรรมเนียมของ `SKIP_REASONS` ใบ 03
    — ลำดับของประตูจึงเป็นส่วนหนึ่งของสัญญา ไม่ใช่รายละเอียดการทำงาน

    `stop_px` มีเฉพาะเส้นทาง `trailing` · เส้นทาง `wait_1h` ไม่เข้าไม้ที่แท่งนี้เลย
    มันบอกให้ผู้เรียกไปดูกราฟ 1 ชั่วโมงแล้วเดินตรรกะ Action Zone ตัวเดิมที่นั่น —
    **ไม่ใช่สูตรใหม่** แค่เปลี่ยน timeframe (spec/03:129-132)
    """

    side: str | None = None
    route: str | None = None
    stop_px: float | None = None
    risk: float | None = None
    reward: float | None = None
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        if (self.side is None) != (self.skip_reason is not None):
            raise ValueError(
                "ต้องมี skip_reason เมื่อไม่เข้าไม้ และต้องไม่มีเมื่อเข้า — "
                f"ได้ side={self.side!r} skip_reason={self.skip_reason!r}"
            )
        if self.route == "trailing" and self.side is not None and self.stop_px is None:
            raise ValueError("เส้นทาง trailing ต้องมี stop เสมอ (spec/03:135)")


def late_entry(
    plan: BarPlan,
    *,
    route: str | None,
    state: str,
    position_side: str | None,
    allow_short: bool,
    feat: Features,
    trail_slow: float | None,
) -> ColdStartPlan:
    """พิจารณา cold start ของแท่งนี้ · ประตูเรียงตามลำดับที่ปิดก่อนได้ก่อน

    `position_side` ต้องมาจากการ **อ่านสถานะจริงที่ปลายทาง** ไม่ใช่ความจำของระบบ
    (spec/08 ขั้น 3) — นี่คือตัวกันเพียงตัวเดียวที่ทำให้การกด start engine ซ้ำๆ
    ระหว่างเทรนด์ไม่กลายเป็นการเพิ่มสถานะซ้อนทุกครั้ง

    `trail_slow` คือ `Trail2` ของแท่งนี้จาก `indicators/trailing.py` · เป็น `None`
    ได้เมื่อข้อมูลยังไม่พอ (ช่วงอุ่นเครื่องของ ATR) ซึ่งแปลว่าตั้ง stop ไม่ได้
    """
    if route is not None and route != "skip" and route not in ROUTES:
        raise ValueError(f"cold_start ต้องเป็นหนึ่งใน {ROUTES}, 'skip' หรือไม่ระบุ — ได้ {route!r}")

    # ── ประตู 1 · เส้นทางนี้เปิดเฉพาะตอนที่กฎไม้เรียวเป็นตัวปฏิเสธ ────────────
    # แท่งที่ `decide()` ปฏิเสธด้วยเหตุอื่น (ถือฝั่งนั้นอยู่, short ปิดไว้) ไม่ใช่
    # การตกรถ · แท่งที่มีสัญญาณก็ไม่ใช่ — เส้นทางปกติจัดการไปแล้ว
    if plan.skip_reason != "cane_rule":
        return ColdStartPlan(skip_reason=plan.skip_reason or "no_signal")

    # ── ประตู 2 · config ไม่ได้เปิดเส้นทางนี้ไว้ → กฎไม้เรียวยืนตามเดิม ────────
    if route is None or route == "skip":
        return ColdStartPlan(skip_reason="cane_rule")

    # ── ประตู 3 · มีสถานะเปิดอยู่แล้ว ฝั่งไหนก็ตาม (spec/03 "เงื่อนไขบังคับ") ──
    if position_side is not None:
        return ColdStartPlan(skip_reason="already_positioned")

    # ── ประตู 4 · ไม่มีเทรนด์ให้ตกรถ ────────────────────────────────────────
    side = {"BULLISH": "long", "BEARISH": "short"}.get(state)
    if side is None:
        return ColdStartPlan(skip_reason="no_signal")

    # ── ประตู 5 · ฝั่ง short ที่เปิดไม่ได้ ──────────────────────────────────
    if side == "short" and not allow_short:
        return ColdStartPlan(skip_reason="short_disabled")

    # ── ทางที่ 1 · ลง timeframe เล็ก ไม่เข้าไม้ที่แท่งนี้ ────────────────────
    if route == "wait_1h":
        return ColdStartPlan(side=side, route="wait_1h")

    # ── ทางที่ 2 · เข้าที่ราคาปัจจุบัน แต่ต้องมี stop และ RR ถึงเกณฑ์ ────────
    return _trailing_plan(side=side, feat=feat, trail_slow=trail_slow)


def _trailing_plan(
    *, side: str, feat: Features, trail_slow: float | None
) -> ColdStartPlan:
    """เส้นทาง `trailing` · stop ที่ Trail2 และ RR ≥ 2:1 (ดูหัวไฟล์ว่า reward คืออะไร)

    `rr_too_low` ครอบสามกรณีที่ต่างกันในรายละเอียดแต่จบเหมือนกัน: ไม่มีจุดเหวี่ยงให้
    เป็นเป้า, เป้าอยู่หลังราคาไปแล้ว (reward ติดลบ) และ RR ไม่ถึงเกณฑ์ · ยุบเป็น
    เหตุเดียวเพราะ `SKIP_REASONS` ของใบ 03 เป็นชุดปิดและทั้งสามคือ "อัตราส่วนไม่คุ้ม"
    เหมือนกัน — รายละเอียดที่ต่างกันอยู่ใน `risk`/`reward` ที่คืนมาด้วยเสมอ
    """
    entry = feat.close_px
    if trail_slow is None:
        return ColdStartPlan(skip_reason="rr_too_low")

    if side == "long":
        risk = entry - trail_slow
        target = feat.swing_highs[-1].price if feat.swing_highs else None
        reward = None if target is None else target - entry
    else:
        risk = trail_slow - entry
        target = feat.swing_lows[-1].price if feat.swing_lows else None
        reward = None if target is None else entry - target

    # `risk <= 0` แปลว่าเส้น Trail อยู่ผิดข้างของราคา — วาง stop ตรงนั้นคือวาง stop
    # ที่ทำงานทันทีที่ส่ง ซึ่งไม่ใช่ stop แต่เป็นคำสั่งปิดที่เขียนผิดรูป
    if risk <= 0 or reward is None or reward < MIN_REWARD_TO_RISK * risk:
        return ColdStartPlan(
            risk=risk, reward=reward, skip_reason="rr_too_low"
        )

    return ColdStartPlan(
        side=side, route="trailing", stop_px=trail_slow, risk=risk, reward=reward
    )


@dataclass(frozen=True, slots=True)
class StopAction:
    """สิ่งที่เกิดกับ stop ที่ปลายทางในแท่งนี้ · `action` ตรงกับ `decision_stop.action`

    `order_id` คือ id ของออเดอร์ที่ **กำลังคุ้มไม้อยู่หลังจบรอบนี้** ไม่ใช่ id ของ
    ออเดอร์ที่ถูกแทน — ใบ 11 ตัดสินไว้แล้วว่า stop ที่ทำงานต้องเก็บ id ของออเดอร์
    ที่ arm มันไว้ ไม่ใช่ id ที่ตายไปแล้ว
    """

    action: str
    stop_px: float
    order_id: str | None
    result: OrderResult | None = None

    def __post_init__(self) -> None:
        if self.action not in STOP_ACTIONS:
            raise ValueError(f"action ต้องเป็นหนึ่งใน {STOP_ACTIONS} ไม่ใช่ {self.action!r}")


def maintain_stop(
    broker: Broker,
    *,
    symbol: str,
    side: str,
    qty: float,
    stop_px: float,
    bar_close_ts: int,
    existing: OpenOrder | None,
) -> StopAction:
    """วาง stop ที่ exchange หรือเลื่อนของเดิม · **ไม่ใช่ cancel แล้ว place ใหม่**

    ADR 17 · `replace()` มีอยู่เพราะ cancel+place เปิดหน้าต่างเวลาที่ไม้ไม่มี stop
    คุ้มอยู่ · บน perp ที่มี leverage หน้าต่างนั้นยาวพอให้ราคากระโดดถึง liquidation ได้

    `existing` มาจาก `broker.open_orders()` ของรอบนี้ — **อ่านของจริงทุกแท่ง** ไม่ใช่
    จำว่าเคยวางไว้ (spec/06, spec/08 ขั้น 3) · `None` แปลว่าปลายทางไม่มี stop ค้างอยู่
    ซึ่งเป็นได้ทั้ง "เพิ่งเปิดไม้" และ "stop หายไปโดยที่เราไม่รู้" — ทั้งสองกรณีจบ
    เหมือนกันคือวางใหม่ การแยกสองอย่างนี้เป็นงานของ reconcile ไม่ใช่ของที่นี่

    `stop_px` ที่เท่าเดิมเป๊ะ → `unchanged` **ไม่ยิงอะไรเลย** · Slow Trail นิ่งได้
    หลายแท่งติดกัน (สาขา `max(prev, ...)` ของสูตร) การยิง replace ทุกแท่งจะเป็น
    การรบกวนปลายทางโดยไม่ได้อะไรและทำให้ rate limit หมดเร็วขึ้นเปล่าๆ
    """
    if qty <= 0:
        raise ValueError(f"qty ของ stop ต้องมากกว่าศูนย์ ไม่ใช่ {qty}")
    if side not in _CLOSING_ORDER_SIDE:
        raise ValueError(f"side ของไม้ต้องเป็น long/short ไม่ใช่ {side!r}")

    if existing is not None and existing.stop_px == stop_px:
        return StopAction(
            action="unchanged", stop_px=stop_px, order_id=existing.venue_order_id
        )

    if existing is not None:
        result = broker.replace(existing.venue_order_id, stop_px)
        return StopAction(
            action="replaced",
            stop_px=stop_px,
            order_id=result.venue_order_id or existing.venue_order_id,
            result=result,
        )

    order = Order(
        symbol=symbol,
        side=_CLOSING_ORDER_SIDE[side],
        type="stop_market",
        qty=qty,
        client_order_id=client_order_id(
            symbol, bar_close_ts, _CLOSING_ORDER_SIDE[side], "stop"
        ),
        reduce_only=True,
        stop_px=stop_px,
    )
    result = broker.place(order)
    return StopAction(
        action="placed",
        stop_px=stop_px,
        order_id=result.venue_order_id,
        result=result,
    )
