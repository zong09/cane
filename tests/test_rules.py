"""กฎไม้เรียว + โปรโตคอล flip — เกณฑ์ปิดใบสามข้อ บวกตารางเข้าไม้ทั้งตาราง

**ไม่แตะฐานข้อมูลและไม่ต่อเน็ต** ต้องรันได้ใต้ `-m "not db"` เสมอ

เกณฑ์ของใบ 08 ตรงๆ:
1. แท่งที่ไม่ใช่จุดสัญญาณถูกปฏิเสธทั้งสองฝั่ง
2. ขา 1 fill ไม่ครบ → ขา 2 **ไม่ถูกยิง** และมี `flip_aborted` ในบันทึก
3. `allow_short = false` ปิดอย่างเดียวไม่เปิด short

`RecordingBroker` **นับออเดอร์ที่ถูกยิงจริง** เพราะข้อ 2 พิสูจน์ไม่ได้ด้วยการดูค่าที่
คืนมา — `FlipResult.open is None` เป็นจริงได้ทั้งจาก "ไม่ยิง" และจาก "ยิงแล้วลบผลทิ้ง"
ตัวที่แยกสองอย่างนี้คือรายการออเดอร์ที่ปลายทางได้รับ ไม่ใช่ค่าที่ฟังก์ชันคืน
"""

from __future__ import annotations

import pytest

from cane.execution.broker import Order, OrderResult
from cane.rules import BarPlan, decide, execute_flip

STEP = 0.001


def plan(**overrides) -> BarPlan:
    base = {
        "long_signal": False,
        "short_signal": False,
        "state": "UNSET",
        "position_side": None,
        "market": "usdtm_perp",
        "allow_short": True,
    }
    return decide(**{**base, **overrides})


class RecordingBroker:
    """`Broker` ปลอมที่เก็บทุกออเดอร์ที่ถูกยิงตามลำดับ

    `fills` แมป `leg` → จำนวนที่ fill จริง · ใบที่ไม่ระบุถือว่า fill ครบ
    ตัวที่เทสต์สนใจคือ **`self.sent` ยาวเท่าไหร่และเรียงยังไง** ไม่ใช่ค่าที่คืน
    """

    def __init__(self, market: str = "usdtm_perp", fills: dict | None = None):
        self.market = market
        self.sent: list[Order] = []
        self._fills = fills or {}
        self._status = {}

    def status_for(self, leg: str, status: str) -> None:
        self._status[leg] = status

    def place(self, order: Order) -> OrderResult:
        self.sent.append(order)
        leg = "close" if order.reduce_only else "open"
        return OrderResult(
            client_order_id=order.client_order_id,
            venue_order_id=f"v-{len(self.sent)}",
            status=self._status.get(leg, "closed"),
            filled_qty=self._fills.get(leg, order.qty),
            avg_px=100.0,
        )


def orders(close_qty: float = 1.0, open_qty: float = 2.0, *, symbol: str = "BTC/USDT"):
    close = Order(
        symbol=symbol,
        side="buy",
        type="market",
        qty=close_qty,
        client_order_id="cane-BTC/USDT-1-buy-close",
        reduce_only=True,
    )
    opened = Order(
        symbol=symbol,
        side="buy",
        type="market",
        qty=open_qty,
        client_order_id="cane-BTC/USDT-1-buy-open",
    )
    return close, opened


# ── เกณฑ์ปิดใบข้อ 1 — แท่งที่ไม่ใช่จุดสัญญาณ ปฏิเสธทั้งสองฝั่ง ────────────────


@pytest.mark.parametrize("state", ["BULLISH", "BEARISH"])
@pytest.mark.parametrize("position_side", [None, "long", "short"])
def test_a_bar_with_no_signal_never_opens_whatever_the_trend_is_doing(
    state, position_side
):
    """เกณฑ์ข้อ 1 · **บังคับเท่ากันทั้งสองฝั่ง ไม่มี config ให้ปิด** (spec/03)

    เทรนด์ที่กำลังวิ่ง (`BULLISH`/`BEARISH`) คือจังหวะที่คนอยากเข้าที่สุด และเป็น
    จังหวะเดียวที่กฎนี้มีความหมาย — ถ้าไม่มีสัญญาณ ไม่เข้า จบ
    """
    got = plan(state=state, position_side=position_side)
    assert got.open_side is None
    assert got.needs_judge is False
    assert got.skip_reason == "cane_rule"


def test_a_quiet_bar_is_no_signal_while_a_running_trend_is_the_cane_rule():
    """แยกสองเหตุที่ไม่เข้าออกจากกัน — **นี่คือการตีความ ไม่ใช่ข้อที่สเปกเขียนตรงๆ**

    `SKIP_REASONS` ของใบ 03 มีทั้ง `no_signal` และ `cane_rule` แต่ไม่ได้บอกเส้นแบ่ง
    · ที่นี่อ่านว่า `no_signal` = ตลาดเงียบ ยังไม่มีเทรนด์ให้ตาม (แท่งส่วนใหญ่ของปี)
    ส่วน `cane_rule` = เทรนด์กำลังวิ่งแต่เราอยู่นอกเพราะตกรถ ซึ่งเป็นสถานะที่ใบ 09
    มีไว้จัดการโดยเฉพาะ · ยุบเป็นค่าเดียวแล้วจะมองไม่เห็นว่ามันเกิดบ่อยแค่ไหน
    """
    assert plan(state="UNSET").skip_reason == "no_signal"
    assert plan(state="BULLISH").skip_reason == "cane_rule"


# ── ตารางเข้าไม้ของ spec/03:34-42 ทั้งตาราง ───────────────────────────────────


@pytest.mark.parametrize(
    ("signal", "position_side", "close_side", "open_side", "skip_reason"),
    [
        ("long", None, None, "long", None),
        ("long", "short", "short", "long", None),
        ("long", "long", None, None, "already_positioned"),
        ("short", None, None, "short", None),
        ("short", "long", "long", "short", None),
        ("short", "short", None, None, "already_positioned"),
    ],
    ids=[
        "long_signal_while_flat_opens_long",
        "long_signal_while_short_flips",
        "long_signal_while_long_does_nothing",
        "short_signal_while_flat_opens_short",
        "short_signal_while_long_flips",
        "short_signal_while_short_does_nothing",
    ],
)
def test_the_entry_table_row_by_row(
    signal, position_side, close_side, open_side, skip_reason
):
    """หกแถวของ spec/03:34-42 · "ไม่ทำอะไร" เมื่อถือฝั่งเดียวกันคือ **ไม่มี pyramiding**"""
    got = plan(
        long_signal=signal == "long",
        short_signal=signal == "short",
        state="BEARISH" if signal == "long" else "BULLISH",
        position_side=position_side,
    )
    assert got.close_side == close_side
    assert got.open_side == open_side
    assert got.skip_reason == skip_reason
    assert got.needs_judge is (open_side is not None)


def test_closing_never_asks_the_judge():
    """spec/04:105 · "ขาปิดของ flip ไม่เรียก Judge เลย ปิดคือปิดทั้งหมดเสมอ"

    ความล้มเหลวของ LLM จึงไม่มีวันขวางการปิดสถานะ · ข้อนี้ต้องอ่านออกจาก `BarPlan`
    ตรงๆ ไม่ใช่ต้องไปไล่ดูว่าใครเรียก Judge บ้าง
    """
    close_only = plan(short_signal=True, state="BULLISH", position_side="long", allow_short=False)
    assert close_only.close_side == "long"
    assert close_only.needs_judge is False


# ── เกณฑ์ปิดใบข้อ 3 — allow_short = false ─────────────────────────────────────


def test_short_disabled_still_closes_the_long_but_opens_nothing():
    """เกณฑ์ข้อ 3 · spec/03:50 — "ยังปิด long ตามสัญญาณ short แต่ไม่เปิดไม้ใหม่"

    พฤติกรรมกลับไปเหมือนระบบ long-only เดิมทุกประการ · และ **ไม่เรียก Judge**
    ซึ่งแปลว่าไม่จ่ายเงินค่า LLM สำหรับไม้ที่จะไม่มีวันถูกเปิด
    """
    got = plan(short_signal=True, state="BULLISH", position_side="long", allow_short=False)

    assert got.close_side == "long"
    assert got.open_side is None
    assert got.needs_judge is False
    assert got.skip_reason == "short_disabled"


def test_short_disabled_while_flat_does_nothing_at_all():
    got = plan(short_signal=True, state="BULLISH", position_side=None, allow_short=False)
    assert (got.close_side, got.open_side) == (None, None)
    assert got.skip_reason == "short_disabled"


def test_spot_walks_the_same_path_because_short_is_impossible_there():
    """spot ไม่มีฝั่ง short จริงๆ ไม่ใช่ถูกปิดด้วย config (ADR 26)

    "สัญญาณแดงบนเหรียญ spot แปลว่าขายออกให้แบน จบ" — ผลลัพธ์เหมือน `allow_short`
    ที่ปิดไว้ทุกประการ ซึ่งเป็นเหตุผลที่ไม่ต้องมีทางแยกที่สองในโค้ด
    """
    got = decide(
        long_signal=False,
        short_signal=True,
        state="BULLISH",
        position_side="long",
        market="spot",
        allow_short=False,
    )
    assert (got.close_side, got.open_side) == ("long", None)
    assert got.skip_reason == "short_disabled"


def test_allow_short_true_on_spot_is_refused_rather_than_read_as_false():
    """สถานะที่ไม่ควรมีอยู่ต้องดัง ไม่ใช่ถูกตีความให้ถูกต้องเงียบๆ

    spec/07 กันไว้ตั้งแต่โหลด config แล้ว ด่านนี้จับบั๊กของชั้นบน — ถ้ามันเงียบ
    การที่ config หลุดมาผิดจะมองไม่เห็นเลยเพราะพฤติกรรมยังดูถูกต้อง
    """
    with pytest.raises(ValueError, match="spot"):
        decide(
            long_signal=False,
            short_signal=True,
            state="BULLISH",
            position_side="long",
            market="spot",
            allow_short=True,
        )


# ── invariant ของ BarPlan ─────────────────────────────────────────────────────


def test_a_plan_without_an_open_leg_must_say_why():
    """invariant ของใบ 03 (`skip_reason IS NULL ⟺ มีออเดอร์เปิด`) เขียนเป็นโค้ด"""
    with pytest.raises(ValueError, match="skip_reason"):
        BarPlan(open_side=None, skip_reason=None)
    with pytest.raises(ValueError, match="skip_reason"):
        BarPlan(open_side="long", skip_reason="no_signal")


def test_a_flip_that_reopens_the_same_side_is_refused():
    with pytest.raises(ValueError, match="กลับข้าง"):
        BarPlan(close_side="long", open_side="long")


def test_one_bar_cannot_be_both_signals():
    """`action_zones()` ให้สองค่านี้จากโซน GREEN กับ RED ซึ่งเกิดพร้อมกันไม่ได้

    มาถึงที่นี่แปลว่าผู้เรียกประกอบข้อมูลผิด ไม่ใช่ตลาดทำอะไรแปลก
    """
    with pytest.raises(ValueError, match="ทั้ง long_signal และ short_signal"):
        plan(long_signal=True, short_signal=True, state="BULLISH")


# ── เกณฑ์ปิดใบข้อ 2 — ขา 1 fill ไม่ครบ ────────────────────────────────────────


def test_a_complete_flip_fires_close_first_then_open():
    """ลำดับคือทั้งหมดของโปรโตคอล · สลับแล้วมีจังหวะที่ถือสถานะเกินเพดาน (spec/03)"""
    broker = RecordingBroker()
    close, opened = orders()
    got = execute_flip(broker, close_order=close, open_order=opened, step=STEP)

    assert [o.client_order_id for o in broker.sent] == [
        "cane-BTC/USDT-1-buy-close",
        "cane-BTC/USDT-1-buy-open",
    ]
    assert broker.sent[0].reduce_only is True
    assert broker.sent[1].reduce_only is False
    assert got.aborted is False
    assert got.skip_reason is None
    assert got.residual_qty == 0.0


def test_a_partial_close_never_sends_the_second_leg(capsys):
    """เกณฑ์ข้อ 2 · ตัวชี้ขาดคือ **ปลายทางได้รับออเดอร์กี่ใบ** ไม่ใช่ค่าที่คืนมา

    `FlipResult.open is None` เป็นจริงได้ทั้งจาก "ไม่ยิง" และจาก "ยิงแล้วลบผลทิ้ง"
    · ถ้าขา 2 ถูกยิงจริง ระบบจะเปิดสถานะสวนกับ 0.4 ที่ยังค้างอยู่ ซึ่งละเมิด one-way
    โดยตรง และบน perp คือการถือความเสี่ยงสองทางพร้อมกันโดยไม่มีชั้นไหนตั้งใจ
    """
    broker = RecordingBroker(fills={"close": 0.6})
    close, opened = orders(close_qty=1.0)
    got = execute_flip(broker, close_order=close, open_order=opened, step=STEP)

    assert len(broker.sent) == 1, "ขา 2 ต้องไม่ถูกยิงเลย ไม่ใช่ยิงแล้วยกเลิก"
    assert broker.sent[0].reduce_only is True
    assert got.open is None
    assert got.aborted is True
    assert got.skip_reason == "flip_aborted"


def test_the_abort_reports_exactly_what_is_left_hanging():
    """ADR 19 · ระบบไม่ปิดของค้างให้เอง ข้อแลกเปลี่ยนคือมันต้อง **มองเห็นได้ทันที**

    `residual_qty` ลง `decisions.residual_qty` แล้วคอนโซลแสดงเป็นสถานะที่ต้องคน
    จัดการ · ถ้าตัวเลขนี้ผิดหรือหาย "ให้คนไปปิดเอง" แปลว่าไม่มีใครรู้ว่าต้องปิด
    """
    broker = RecordingBroker(fills={"close": 0.6})
    close, opened = orders(close_qty=1.0)
    got = execute_flip(broker, close_order=close, open_order=opened, step=STEP)

    assert got.close_qty_intended == 1.0
    assert got.close_qty_filled == pytest.approx(0.6)
    assert got.residual_qty == pytest.approx(0.4)


def test_a_leftover_smaller_than_one_lot_step_is_not_a_leftover():
    """เศษที่เล็กกว่าหนึ่งขั้นปิดไม่ได้ด้วยออเดอร์ใดๆ มันจึงไม่ใช่ของค้างที่คนต้องปิด

    เกณฑ์นี้มาจาก venue ไม่ใช่ epsilon ที่ตั้งเอง — การเทียบ `filled == ordered`
    ตรงๆ บน float จะสะดุด noise ระดับ 1e-17 แล้ว abort ทุกครั้งโดยไม่มีอะไรผิดจริง
    """
    broker = RecordingBroker(fills={"close": 0.9995})
    close, opened = orders(close_qty=1.0)
    got = execute_flip(broker, close_order=close, open_order=opened, step=STEP)

    assert len(broker.sent) == 2
    assert got.aborted is False
    assert got.residual_qty == 0.0


def test_a_close_order_that_is_still_open_aborts_even_when_the_numbers_look_complete():
    """`filled_qty` ของออเดอร์ที่ยังวิ่งอยู่คือค่าที่เปลี่ยนได้หลังเราอ่าน

    การเปิดฝั่งใหม่ทับขาปิดที่ยังไม่นิ่งคือการเดิมพันว่ามันจะ fill ครบ ซึ่งเป็น
    การเดิมพันที่แพ้แล้วได้สถานะสวนกันพอดี
    """
    broker = RecordingBroker()
    broker.status_for("close", "open")
    close, opened = orders(close_qty=1.0)
    got = execute_flip(broker, close_order=close, open_order=opened, step=STEP)

    assert len(broker.sent) == 1
    assert got.aborted is True
    assert got.residual_qty == 0.0, "ตัวเลขบอกว่าไม่เหลือ แต่ยังเชื่อไม่ได้"


# ── ออเดอร์ที่ประกอบผิดต้องดังก่อนถึงปลายทาง ────────────────────────────────


def test_a_close_leg_without_reduce_only_is_refused_before_anything_is_sent():
    """ขาปิดที่ไม่มีธงนี้ปิดเกินขนาดที่ถืออยู่ได้ แล้วกลายเป็นการเปิดสวนทันที

    ซึ่งคือความล้มเหลวข้อเดียวกับที่ทั้งไฟล์นี้มีไว้กัน
    """
    broker = RecordingBroker()
    close, opened = orders()
    naked = Order(
        symbol=close.symbol,
        side=close.side,
        type=close.type,
        qty=close.qty,
        client_order_id=close.client_order_id,
    )
    with pytest.raises(ValueError, match="reduce_only"):
        execute_flip(broker, close_order=naked, open_order=opened, step=STEP)
    assert broker.sent == []


def test_an_open_leg_marked_reduce_only_is_refused():
    broker = RecordingBroker()
    close, opened = orders()
    wrong = Order(
        symbol=opened.symbol,
        side=opened.side,
        type=opened.type,
        qty=opened.qty,
        client_order_id=opened.client_order_id,
        reduce_only=True,
    )
    with pytest.raises(ValueError, match="reduce_only"):
        execute_flip(broker, close_order=close, open_order=wrong, step=STEP)
    assert broker.sent == []


def test_two_legs_on_different_symbols_are_refused():
    broker = RecordingBroker()
    close, _ = orders()
    _, other = orders(symbol="ETH/USDT")
    with pytest.raises(ValueError, match="เหรียญเดียวกัน"):
        execute_flip(broker, close_order=close, open_order=other, step=STEP)
    assert broker.sent == []


def test_there_is_no_flip_on_spot_and_the_existing_guard_is_what_stops_it():
    """spec/03 · ไม่มี flip บน spot · `check_market_supports()` ปฏิเสธ `reduce_only`
    บน spot อยู่แล้ว ด่านนั้นจึงกันเส้นทางนี้ให้โดยอัตโนมัติ

    ไม่มี `if market` ตัวที่สองในไฟล์ flip — ข้อนี้คือหลักฐานว่านั่นเพียงพอ ไม่ใช่
    ว่าลืมเขียน
    """
    broker = RecordingBroker(market="spot")
    close, opened = orders()
    with pytest.raises(ValueError, match="spot"):
        execute_flip(broker, close_order=close, open_order=opened, step=STEP)
    assert broker.sent == []


def test_a_step_of_zero_is_refused_before_any_order_goes_out():
    broker = RecordingBroker()
    close, opened = orders()
    with pytest.raises(ValueError, match="step"):
        execute_flip(broker, close_order=close, open_order=opened, step=0.0)
    assert broker.sent == []
