"""cold start — ข้อยกเว้นเดียวของกฎไม้เรียว และ stop ที่อยู่ที่ exchange จริง

**ไม่แตะฐานข้อมูลและไม่ต่อเน็ต** ต้องรันได้ใต้ `-m "not db"` เสมอ

เกณฑ์ของใบ 09 ตรงๆ:
1. เส้นทาง `wait_1h` ทำงาน
2. cold start ทั้งสองฝั่ง
3. **กด start engine ซ้ำๆ ระหว่างเทรนด์ขณะมีสถานะเปิด ต้องไม่เข้าไม้เพิ่มทุกครั้ง**
4. stop ถูกวางที่ exchange และเลื่อนตาม Trail2 จริง

ข้อ 3 คือข้อที่ไฟล์นี้ทดสอบด้วย **การเรียกซ้ำจริง** ไม่ใช่ด้วยการอ่านโค้ด — ตัวกัน
ที่สเปกกำหนดคือ "ไม่มีสถานะเปิด" ไม่ใช่ flag บนดิสก์ ซึ่งแปลว่าเรียกกี่ครั้งก็ต้อง
ได้ผลเดิม · ถ้าวันหนึ่งมีใครใส่ flag เข้ามา เทสต์นั้นจะยังเขียวแต่เทสต์ "กดซ้ำแล้ว
ต้องเข้าได้เมื่อไม่มีสถานะ" จะดัง
"""

from __future__ import annotations

import pytest

from cane.data import Bar
from cane.execution.broker import OpenOrder, Order, OrderResult
from cane.indicators import features
from cane.indicators.trailing import cdc_trail, cdc_trailing_stop
from cane.rules import BarPlan, ColdStartPlan, late_entry, maintain_stop

DAY = 86_400_000
CANE = BarPlan(skip_reason="cane_rule")


def bars(closes, *, span: float = 2.0):
    return [
        Bar(
            open_ts=i * DAY,
            close_ts=(i + 1) * DAY,
            open=float(c),
            high=float(c) + span,
            low=float(c) - span,
            close=float(c),
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


#: ความลึกของการย่อที่ทำให้ RR ผ่าน 2:1 พอดีอย่างมีระยะเผื่อ (RR ≈ 2.57)
#: **ตัวเลขนี้ไม่ใช่ค่าที่เลือกมาลอยๆ** — ด่าน RR เข้มกว่าที่ดูจากสูตรมาก ดู
#: `test_a_steady_uptrend_alone_is_never_enough_to_pass_the_gate` ว่าทำไม
PULLBACK = 4.0


def ran_up_then_paused(pull: float = PULLBACK):
    """ไต่ขึ้น 22 แท่งถึงยอด แล้วย่อลง 3 แท่งแบบไม่สร้างยอดใหม่

    รูปนี้คือรูปเดียวที่ทำให้ cold start ฝั่ง long ผ่านด่าน RR ได้จริง: ราคาต้องอยู่
    **ใต้จุดเหวี่ยงล่าสุด** (ถึงจะมีเป้าอยู่ข้างหน้า) แต่ยัง **เหนือ Trail2** (ถึงจะ
    ตั้ง stop ได้) · ช่วงที่เข้าเงื่อนไขทั้งสองพร้อมกันแคบมาก

    ย่อแบบ monotonic เพื่อไม่ให้เกิด pivot high ใหม่มาแทนที่ยอดเดิม — ถ้าเกิด เป้า
    จะเลื่อนมาอยู่ใกล้ราคาแล้ว reward หดจนไม่ผ่าน
    """
    rise = [100.0 + i * 2 for i in range(22)]
    peak = rise[-1]
    # `span = 1.0` ทำให้ TR = 2.0 ทุกแท่ง → ATR = 2.0 → SL2 = 4.0 เป๊ะ
    # ตัวเลขที่คำนวณด้วยมือในเทสต์ข้างล่างพึ่งค่านี้ ห้ามเปลี่ยนโดยไม่คิดเลขใหม่
    return bars(rise + [peak - pull * (i + 1) / 3 for i in range(3)], span=1.0)


def fell_then_paused(pull: float = PULLBACK):
    """ภาพสะท้อนทีละข้อของ `ran_up_then_paused()` — ลง 22 แท่ง แล้วเด้ง 3 แท่ง"""
    fall = [142.0 - i * 2 for i in range(22)]
    trough = fall[-1]
    return bars(fall + [trough + pull * (i + 1) / 3 for i in range(3)], span=1.0)


def still_climbing(n: int = 25):
    """ขาขึ้นล้วนไม่เคยย่อเลย — **ไม่มีจุดเหวี่ยงสักจุด**

    ราคาทำ high ใหม่ทุกแท่ง ไม่มีแท่งไหนเป็นยอดเฉพาะที่ `swing_highs` จึงว่างเปล่า
    ซึ่งเป็นสภาวะตลาดจริง ไม่ใช่ข้อมูลที่หายไป — และแปลว่า **ไม่มีเป้าให้วัด RR**
    """
    return bars([100.0 + i * 2 for i in range(n)], span=1.0)


def broke_past_the_old_high():
    """ขึ้น → ย่อ (เกิดยอดที่ 123) → ขึ้นทะลุยอดเดิมไปไกล ปิดที่ 140.5

    ต่างจาก `still_climbing()` ตรงที่ **มี**จุดเหวี่ยง แต่มันอยู่หลังราคาไปแล้ว
    reward จึงติดลบ ไม่ใช่ `None` — สองสภาวะนี้จบเหมือนกันแต่มาคนละทาง
    """
    closes = (
        [100.0 + i * 2 for i in range(12)]
        + [118.0, 115.0, 113.0]
        + [113.0 + i * 2.5 for i in range(1, 12)]
    )
    return bars(closes, span=1.0)


def call(**overrides) -> ColdStartPlan:
    series = overrides.pop("series", ran_up_then_paused())
    feat = overrides.pop("feat", features(series))
    slow = overrides.pop("trail_slow", cdc_trail(series, period=10, factor=2.0)[-1])
    base = {
        "plan": CANE,
        "route": "trailing",
        "state": "BULLISH",
        "position_side": None,
        "allow_short": True,
        "feat": feat,
        "trail_slow": slow,
    }
    return late_entry(**{**base, **overrides})


# ── เกณฑ์ข้อ 3 — ตัวกันคือ "ไม่มีสถานะเปิด" ไม่ใช่ flag บนดิสก์ ─────────────────


@pytest.mark.parametrize("held", ["long", "short"])
def test_pressing_start_engine_over_and_over_never_stacks_a_position(held):
    """เกณฑ์ข้อ 3 · เรียกสิบครั้งติดขณะถือของอยู่ ต้องปฏิเสธทุกครั้งเหมือนกันเป๊ะ

    spec/03 · "ทุกครั้งที่ engine เริ่มคือ cold start ครั้งใหม่" และ **ห้ามเก็บ flag
    `cold_start_done` ลงดิสก์** เพราะจะปิดเส้นทางนี้ถาวรหลัง boot ครั้งแรก ·
    ตัวกันของจริงคือเงื่อนไข "ไม่มีสถานะเปิดทั้งสองฝั่ง" ซึ่งเป็นฟังก์ชันบริสุทธิ์
    ของสิ่งที่อ่านจากปลายทาง ไม่มีสถานะภายในให้จำ

    **ถือฝั่งไหนก็ปฏิเสธเหมือนกัน** — ฝั่งเดียวกันคือการเข้าไม้ซ้ำ ฝั่งตรงข้ามคือ
    การเปิดสวนสถานะเดิม ซึ่งละเมิด one-way
    """
    got = [call(position_side=held) for _ in range(10)]

    assert all(p.side is None for p in got)
    assert {p.skip_reason for p in got} == {"already_positioned"}
    assert got[0] == got[-1], "เรียกซ้ำต้องได้ผลเดิมเป๊ะ ไม่มีสถานะภายในให้ค่อยๆ เปลี่ยน"


def test_the_same_call_enters_once_the_position_is_gone():
    """คู่ของข้อข้างบน · ถ้ามี flag บนดิสก์ ข้อนี้จะดัง — ข้อข้างบนจะยังเขียว

    สองข้อนี้ต้องอยู่ด้วยกันถึงจะพิสูจน์ว่าตัวกันคือสถานะจริง ไม่ใช่การจำว่าเคยกด
    """
    assert call(position_side="long").skip_reason == "already_positioned"
    assert call(position_side=None).side == "long"


# ── เกณฑ์ข้อ 2 — cold start ทั้งสองฝั่ง ───────────────────────────────────────


def test_a_running_uptrend_is_a_missed_long():
    """เกณฑ์ข้อ 2 ฝั่งแรก · คำนวณด้วยมือ: risk ≈ 1.944, reward = 5.0 → RR ≈ 2.57"""
    series = ran_up_then_paused()
    got = call(series=series, state="BULLISH")

    assert got.side == "long"
    assert got.route == "trailing"
    assert got.stop_px is not None and got.stop_px < features(series).close_px
    assert got.reward >= 2 * got.risk


def test_a_running_downtrend_is_a_missed_short():
    """เกณฑ์ข้อ 2 ฝั่งที่สอง · ภาพสะท้อนทีละข้อ — stop อยู่**เหนือ**ราคา

    RR ออกมาเท่ากันเป๊ะกับฝั่ง long (≈ 2.57) ซึ่งเป็นหลักฐานว่าสองฝั่งเดินสูตร
    เดียวกันจริง ไม่ใช่ชุดกฎที่สองที่บังเอิญเขียนคล้ายกัน
    """
    series = fell_then_paused()
    slow = cdc_trail(series, period=10, factor=2.0)[-1]
    got = call(series=series, feat=features(series), state="BEARISH", trail_slow=slow)

    assert got.side == "short"
    assert got.stop_px is not None and got.stop_px > features(series).close_px
    assert got.risk is not None and got.risk > 0
    assert got.reward / got.risk == pytest.approx(
        _rr(ran_up_then_paused(), "long"), rel=1e-9
    )


def _rr(series, side: str) -> float:
    """RR ของชุดข้อมูลหนึ่ง คำนวณตรงๆ ไม่ผ่าน `late_entry()` — ใช้เทียบสองฝั่ง"""
    feat = features(series)
    slow = cdc_trail(series, period=10, factor=2.0)[-1]
    if side == "long":
        return (feat.swing_highs[-1].price - feat.close_px) / (feat.close_px - slow)
    return (feat.close_px - feat.swing_lows[-1].price) / (slow - feat.close_px)


def test_a_trend_that_never_set_is_not_a_missed_ride():
    """`UNSET` แปลว่ายังไม่เคยเกิดสัญญาณครบทั้งสองฝั่ง — ไม่มีรถให้ตก"""
    assert call(state="UNSET").skip_reason == "no_signal"


def test_short_disabled_blocks_the_missed_short_too():
    got = call(series=fell_then_paused(), state="BEARISH", allow_short=False)
    assert got.side is None
    assert got.skip_reason == "short_disabled"


# ── ทางเข้ามีทางเดียวและมันคือ cane_rule ──────────────────────────────────────


@pytest.mark.parametrize(
    "reason", ["no_signal", "already_positioned", "short_disabled", None]
)
def test_a_bar_the_normal_path_did_not_refuse_with_the_cane_rule_is_not_a_cold_start(
    reason,
):
    """cold start เป็น**ข้อยกเว้นของกฎไม้เรียว** ไม่ใช่ประตูหลังของทั้งระบบ

    แท่งที่ `decide()` ปฏิเสธด้วยเหตุอื่นไม่ใช่การตกรถ และแท่งที่มีสัญญาณ
    (`skip_reason is None`) เส้นทางปกติจัดการไปแล้ว · เขียนให้ `late_entry()` รับ
    `BarPlan` เข้ามาแทนสัญญาณดิบก็เพื่อให้ข้อนี้เป็นจริงตามรูปของโค้ด ผู้เรียกจะ
    แอบเข้าไม้ที่แท่งซึ่งกฎอื่นปฏิเสธไปแล้วไม่ได้
    """
    plan = (
        BarPlan(open_side="long", needs_judge=True)
        if reason is None
        else BarPlan(skip_reason=reason)
    )
    got = call(plan=plan)
    assert got.side is None
    assert got.skip_reason == (reason or "no_signal")


def test_config_that_does_not_open_the_route_leaves_the_cane_rule_standing():
    """fail-closed · ไม่ระบุ = ไม่เข้าเส้นทางนี้ (spec/03:120)

    เหตุผลที่คืนคือ `cane_rule` ไม่ใช่ค่าใหม่ — กฎไม้เรียวยังเป็นตัวที่ปฏิเสธอยู่
    เส้นทางที่จะยกเว้นมันแค่ปิดอยู่
    """
    for route in (None, "skip"):
        assert call(route=route).skip_reason == "cane_rule"


def test_an_unknown_route_is_refused_at_the_call():
    with pytest.raises(ValueError, match="cold_start"):
        call(route="yolo")


# ── เกณฑ์ข้อ 1 — เส้นทาง wait_1h ──────────────────────────────────────────────


def test_wait_one_hour_picks_a_side_but_does_not_enter_on_this_bar():
    """ทางที่ 1 ไม่เข้าไม้ที่แท่งรายวันนี้ · มันบอกให้ไปดูกราฟ 1 ชั่วโมงต่อ

    **ไม่ใช่สูตรใหม่** — ตรรกะ Action Zone ตัวเดิมทั้งหมด เปลี่ยนแค่ timeframe
    (spec/03:129-132) · ดังนั้นมันจึงไม่มี `stop_px` และไม่ต้องผ่านด่าน RR
    """
    got = call(route="wait_1h")
    assert got.side == "long"
    assert got.route == "wait_1h"
    assert got.stop_px is None
    assert got.skip_reason is None


def test_wait_one_hour_does_not_need_a_trail_at_all():
    """ทางที่ 1 ไม่ใช้ Trail2 เลย ข้อมูลที่ยังไม่พอตั้ง stop จึงไม่ควรขวางมัน"""
    assert call(route="wait_1h", trail_slow=None).side == "long"


# ── RR ≥ 2:1 — เป้ากำไรคือจุดเหวี่ยงล่าสุด (เจ้าของตัดสิน 2026-09-14) ──────────


def test_the_reward_is_measured_to_the_last_confirmed_swing():
    """สเปกบังคับ RR ≥ 2:1 แต่ไม่เคยนิยาม "เป้ากำไร" และ ADR 13 ตัดเป้าราคาออกไปแล้ว

    เจ้าของตัดสินว่าเป้าคือจุดเหวี่ยงล่าสุดที่ยืนยันแล้วจาก `features()` — เป็น
    โครงสร้างที่คำนวณจากข้อมูล ไม่ใช่ตัวเลขที่ hardcode จากบทวิเคราะห์
    """
    series = ran_up_then_paused()
    feat = features(series)
    slow = cdc_trail(series, period=10, factor=2.0)[-1]
    got = call(series=series, feat=feat, trail_slow=slow)

    assert feat.swing_highs, "ชุดข้อมูลของเทสต์ต้องมีจุดเหวี่ยง"
    assert got.reward == pytest.approx(feat.swing_highs[-1].price - feat.close_px)
    assert got.risk == pytest.approx(feat.close_px - slow)


def test_a_market_that_just_made_a_new_high_has_no_target_left_and_is_refused():
    """ผลที่ตามมาโดยตั้งใจ — ไล่ราคาที่ยอดคือสิ่งที่กฎไม้เรียวห้ามอยู่แล้วพอดี

    ราคาปิดอยู่เหนือจุดเหวี่ยงล่าสุด → reward ติดลบ → ไม่มีเป้าอยู่ข้างหน้าให้วิ่งไป
    """
    series = broke_past_the_old_high()
    feat = features(series)
    assert feat.close_px > feat.swing_highs[-1].price, "ชุดนี้ต้องเพิ่งทำ new high"

    got = call(series=series, feat=feat, trail_slow=feat.close_px - 1.0)
    assert got.side is None
    assert got.skip_reason == "rr_too_low"
    assert got.reward is not None and got.reward < 0


def test_a_market_that_never_swung_has_no_target_to_measure_against():
    """ขาขึ้นล้วนไม่มีจุดเหวี่ยงเลย — `reward` เป็น `None` ไม่ใช่ติดลบ

    สองสภาวะนี้ (ไม่มีเป้า กับ เป้าอยู่หลังราคา) จบเหมือนกันคือ `rr_too_low` แต่มา
    คนละทาง · `risk` ยังถูกคำนวณและคืนมาเสมอ เพื่อให้คนอ่านย้อนหลังแยกออกว่าเกิดอะไร
    """
    series = still_climbing()
    feat = features(series)
    assert feat.swing_highs == (), "ชุดนี้ต้องไม่มีจุดเหวี่ยงเลย"

    got = call(series=series, feat=feat, trail_slow=feat.close_px - 1.0)
    assert got.skip_reason == "rr_too_low"
    assert got.reward is None
    assert got.risk == pytest.approx(1.0)


def test_a_steady_uptrend_alone_is_never_enough_to_pass_the_gate():
    """ด่าน RR เข้มกว่าที่ดูจากสูตรมาก — **นี่คือข้อค้นพบ ไม่ใช่แค่เทสต์**

    `Trail2` อยู่ที่ 2×ATR ใต้ราคา ดังนั้น `reward ≥ 2×risk` แปลว่าเป้าต้องอยู่สูงกว่า
    ราคาปัจจุบันถึง **4×ATR** · ในขาขึ้นที่เดินเรียบๆ จุดเหวี่ยงล่าสุดอยู่ใต้ราคาเสมอ
    (เพราะราคาทำ high ใหม่ทุกแท่ง) เส้นทาง `trailing` จึงไม่ผ่านเลย

    มันผ่านได้เฉพาะตอนที่ราคาย่อลงมาใต้ยอดเดิมพอสมควรแต่ยังไม่หลุด Trail2 —
    ช่วงที่แคบมาก · ผลคือเส้นทางนี้จะยิงจริงไม่บ่อยเท่าที่ใบสั่งชวนให้คิด
    """
    for depth in (0.0, 1.0, 2.0, 3.0):
        series = broke_past_the_old_high() if depth == 0.0 else ran_up_then_paused(depth)
        assert call(series=series, feat=features(series),
                    trail_slow=cdc_trail(series, period=10, factor=2.0)[-1]
                    ).skip_reason == "rr_too_low", f"ย่อ {depth} ไม่ควรผ่าน"
    deep = ran_up_then_paused(PULLBACK)
    assert call(series=deep, feat=features(deep),
                trail_slow=cdc_trail(deep, period=10, factor=2.0)[-1]).side == "long"


def test_a_reward_that_does_not_reach_twice_the_risk_is_refused():
    """คำนวณด้วยมือ · reward = 10, risk = 6 → RR = 1.67 ไม่ถึง 2 → ไม่เข้า

    ตัวเลขเลือกให้อยู่ใต้เกณฑ์พอประมาณ ไม่ใช่เฉียดฉิว เพื่อให้ข้อนี้ไม่ไวต่อการ
    ปัดเศษ · เคสที่พอดี 2.0 เป๊ะมีเทสต์แยกข้างล่าง
    """
    series = ran_up_then_paused()
    feat = features(series)
    target = feat.swing_highs[-1].price
    entry = feat.close_px

    got = call(series=series, feat=feat, trail_slow=entry - (target - entry) / 1.67)
    assert got.skip_reason == "rr_too_low"


def test_exactly_two_to_one_is_accepted_because_the_spec_says_at_least():
    """spec/03:135 เขียน "อย่างน้อย 2 เท่า" — พอดีสองเท่าจึงผ่าน ไม่ใช่ตก

    บังคับ RR ให้เป็น 2.0 เป๊ะด้วยการวาง `trail_slow` ที่ระยะครึ่งหนึ่งของ reward
    """
    series = ran_up_then_paused()
    feat = features(series)
    reward = feat.swing_highs[-1].price - feat.close_px
    assert reward > 0

    got = call(series=series, feat=feat, trail_slow=feat.close_px - reward / 2.0)
    assert got.side == "long", "RR พอดี 2.0 ต้องผ่าน"
    assert got.reward / got.risk == pytest.approx(2.0)


def test_a_trail_on_the_wrong_side_of_price_is_refused_not_used_as_a_stop():
    """`risk <= 0` แปลว่าเส้น Trail อยู่ผิดข้างของราคา

    วาง stop ตรงนั้นคือวาง stop ที่ทำงานทันทีที่ส่ง ซึ่งไม่ใช่ stop แต่เป็นคำสั่งปิด
    ที่เขียนผิดรูป
    """
    series = ran_up_then_paused()
    feat = features(series)
    got = call(series=series, feat=feat, trail_slow=feat.close_px + 5.0)
    assert got.skip_reason == "rr_too_low"
    assert got.risk is not None and got.risk < 0


def test_no_trail_yet_means_no_stop_can_be_set_so_no_entry():
    """ช่วงอุ่นเครื่องของ ATR · ไม่มี Trail2 = ตั้ง stop ไม่ได้ = ไม่เข้า (fail-closed)"""
    assert call(trail_slow=None).skip_reason == "rr_too_low"


def test_a_trailing_plan_without_a_stop_cannot_be_constructed():
    """spec/03:135 บังคับ stop เสมอบนเส้นทางนี้ · invariant เขียนเป็นโค้ด"""
    with pytest.raises(ValueError, match="stop"):
        ColdStartPlan(side="long", route="trailing", stop_px=None)


# ── เกณฑ์ข้อ 4 — stop อยู่ที่ exchange และเลื่อนตาม Trail2 ────────────────────


class StopBroker:
    """`Broker` ปลอมที่เก็บทุกการกระทำตามลำดับ · `placed` / `replaced` แยกกันชัด"""

    def __init__(self, market: str = "usdtm_perp"):
        self.market = market
        self.placed: list[Order] = []
        self.replaced: list[tuple[str, float]] = []

    def place(self, order: Order) -> OrderResult:
        self.placed.append(order)
        return OrderResult(
            client_order_id=order.client_order_id,
            venue_order_id=f"v{len(self.placed)}",
            status="open",
            filled_qty=0.0,
        )

    def replace(self, order_id: str, stop_px: float) -> OrderResult:
        self.replaced.append((order_id, stop_px))
        return OrderResult(
            client_order_id="c",
            venue_order_id=order_id,
            status="open",
            filled_qty=0.0,
        )


def resting(stop_px: float, order_id: str = "v1") -> OpenOrder:
    return OpenOrder(
        venue_order_id=order_id,
        client_order_id=None,
        symbol="BTC/USDT",
        side="sell",
        type="stop_market",
        qty=1.0,
        stop_px=stop_px,
        reduce_only=True,
    )


def stop(broker, *, stop_px, existing=None, side="long"):
    return maintain_stop(
        broker,
        symbol="BTC/USDT",
        side=side,
        qty=1.0,
        stop_px=stop_px,
        bar_close_ts=DAY,
        existing=existing,
    )


def test_the_stop_goes_to_the_exchange_as_a_real_order_not_kept_in_memory():
    """เกณฑ์ข้อ 4 · ADR 17 — ประเมินฝั่ง engine ตอนแท่งรายวันปิดบางเกินไปสำหรับ perp

    ราคากระโดดข้ามคืนเดียวถึง liquidation ได้ก่อน engine ตื่น
    """
    broker = StopBroker()
    got = stop(broker, stop_px=95.0)

    assert len(broker.placed) == 1
    order = broker.placed[0]
    assert order.type == "stop_market"
    assert order.stop_px == 95.0
    assert order.reduce_only is True
    assert order.side == "sell", "stop ของไม้ฝั่ง long คือคำสั่งขาย"
    assert got.action == "placed"
    assert got.order_id == "v1"


def test_a_short_position_is_protected_by_a_buy_stop():
    broker = StopBroker()
    stop(broker, stop_px=105.0, side="short")
    assert broker.placed[0].side == "buy"


def test_the_stop_is_moved_with_replace_not_cancel_then_place():
    """ADR 17 · cancel+place เปิดหน้าต่างเวลาที่ไม้ไม่มี stop คุ้มอยู่

    บน perp ที่มี leverage หน้าต่างนั้นยาวพอให้ราคากระโดดถึง liquidation ได้
    """
    broker = StopBroker()
    got = stop(broker, stop_px=97.0, existing=resting(95.0))

    assert broker.replaced == [("v1", 97.0)]
    assert broker.placed == [], "ห้ามมีการวางใบใหม่เลย"
    assert got.action == "replaced"


def test_a_stop_already_at_the_right_price_is_left_alone():
    """Slow Trail นิ่งได้หลายแท่งติดกัน (สาขา `max(prev, ...)` ของสูตร)

    ยิง replace ทุกแท่งคือการรบกวนปลายทางโดยไม่ได้อะไร และทำให้ rate limit หมดเร็ว
    """
    broker = StopBroker()
    got = stop(broker, stop_px=95.0, existing=resting(95.0))

    assert (broker.placed, broker.replaced) == ([], [])
    assert got.action == "unchanged"
    assert got.order_id == "v1"


def test_the_stop_follows_slow_trail_bar_by_bar_on_a_real_series():
    """เกณฑ์ข้อ 4 ครึ่งหลัง · เดินทั้งเส้นแล้วยืนยันว่า stop ตามไปจริงทุกแท่ง

    ชุดนี้ราคาขึ้นเรื่อยๆ `Trail2` จึงไต่ขึ้นตาม แล้ว stop ต้องถูก `replace` ไปที่ค่า
    ใหม่ทุกครั้งที่เส้นขยับ — ไม่ใช่วางครั้งเดียวแล้วทิ้งไว้
    """
    series = bars([100.0 + i * 2 for i in range(20)])
    trail = cdc_trail(series, period=10, factor=2.0)
    broker = StopBroker()

    resting_order: OpenOrder | None = None
    seen: list[tuple[str, float]] = []
    for px in trail:
        if px is None:
            continue
        action = stop(broker, stop_px=px, existing=resting_order)
        seen.append((action.action, action.stop_px))
        resting_order = resting(action.stop_px, action.order_id or "v1")

    assert seen[0][0] == "placed"
    assert all(a == "replaced" for a, _ in seen[1:])
    moved = [px for _, px in seen]
    assert moved == sorted(moved), "เส้นขาขึ้น stop ต้องไม่เคยถอยลง"
    assert len(broker.replaced) == len(seen) - 1


def test_a_stop_for_nothing_is_refused():
    """stop ที่คุ้มศูนย์หน่วยคือออเดอร์ที่ปลายทางจะปฏิเสธ — ดังตั้งแต่ที่นี่"""
    with pytest.raises(ValueError, match="qty"):
        maintain_stop(
            StopBroker(),
            symbol="BTC/USDT",
            side="long",
            qty=0.0,
            stop_px=95.0,
            bar_close_ts=DAY,
            existing=None,
        )


# ── อินดิเคเตอร์ — สิ่งที่ตรวจได้โดยไม่มีไฟล์จาก TradingView ─────────────────


def test_a_steady_uptrend_puts_the_slow_trail_exactly_two_atr_below_price():
    """คำนวณด้วยมือ · ราคา +2 ทุกแท่ง ช่วงกว้าง 4 คงที่ → TR = 4 ทุกแท่ง → ATR = 4

    `SL2 = 2.0 × 4 = 8` และเทรนด์ขาขึ้นทำให้สาขา `max(prev, SC − SL2)` เลือก
    `SC − SL2` เสมอ → Trail2 = close − 8 เป๊ะ
    """
    series = bars([100.0 + i * 2 for i in range(20)])
    trail = cdc_trail(series, period=10, factor=2.0)

    for bar, px in zip(series, trail, strict=True):
        if px is not None:
            assert px == pytest.approx(bar.close - 8.0)


def test_the_warm_up_is_na_not_zero():
    """`length - 1` แท่งแรกเป็น `None` เพราะ RMA ยังไม่มี TR ครบ `length` ตัว

    Pine คืน `na` ตรงนั้น แล้ว `SL = factor × na` เป็น `na` ทำให้ Trail เป็น `na`
    ด้วย · การเติมศูนย์ให้เต็มเส้นจะทำให้ stop ของแท่งแรกๆ ถูกตั้งที่ราคาปิดพอดี
    """
    series = bars([100.0 + i * 2 for i in range(20)])
    trail = cdc_trail(series, period=10, factor=2.0)

    assert trail[:9] == [None] * 9
    assert trail[9] is not None


def whipsaw():
    """ขึ้น 16 แท่ง → ทิ่มลงหลุดเส้น 1 แท่ง → เด้งกลับขึ้นเหนือเส้น

    รูปเดียวที่ทำให้ `SC[1]` กับ `Trail[1]` **ไม่ตรงกัน** · ชุดข้อมูลที่วิ่งทางเดียว
    ทุกชุดจะมีสองค่านี้อยู่ข้างเดียวกันเสมอ เงื่อนไขที่ต่างกันจึงมองไม่เห็นเลย
    """
    return bars([100.0 + i * 2 for i in range(16)] + [118.0, 126.0, 134.0], span=1.0)


def test_the_trail_resets_instead_of_locking_when_only_this_bar_crossed_back():
    """**จุดที่พอร์ต Pine ผิดง่ายที่สุด** — `SC[1]` กับ `Trail[1]` เป็นคนละอย่าง

    สองเงื่อนไขแรกของ `iff` ซ้อนต้องการ **ทั้ง** ราคาปิดแท่งนี้และแท่งก่อนหน้าอยู่
    ข้างเดียวกันของเส้น · ที่แท่ง 17 ราคาปิด 126 อยู่**เหนือ**เส้นเดิม (125.904)
    แต่ราคาปิดแท่งก่อน (118) อยู่**ใต้**เส้น → สาขาแรกเป็นเท็จ → ตกลงมาที่สาขาสาม
    ซึ่งคือ `SC − SL2` **ไม่มี `max` มาล็อก** → เส้นรีเซ็ตลงมาที่ 117.086

    ถ้าเผลอตัดเงื่อนไข `SC[1]` ทิ้ง สาขาแรกจะทำงานแล้วได้ `max(125.904, 117.086)`
    = 125.904 คือเส้นค้างอยู่ที่เดิม — **ต่างกันเกือบ 9 หน่วย** และ stop จะถูกวางผิด
    ข้างของราคาในแท่งถัดไป · ชุดข้อมูลที่วิ่งทางเดียวจับข้อนี้ไม่ได้เลยเพราะสองค่า
    อยู่ข้างเดียวกันตลอด
    """
    trail = cdc_trail(whipsaw(), period=10, factor=2.0)

    assert trail[15] == pytest.approx(124.106, abs=0.01), "ยอดก่อนทิ่มลง"
    # แท่ง 16 · ราคาหลุดลงใต้เส้น แต่แท่งก่อนยังอยู่เหนือ → สาขาสอง (min) ไม่ทำงาน
    # เช่นกัน → ตกมาที่ `else` ซึ่งคือ `SC + SL2` (เส้นพลิกไปอยู่เหนือราคา)
    assert trail[16] == pytest.approx(125.904, abs=0.01)
    assert trail[16] > whipsaw()[16].close
    # แท่ง 17 · ข้อที่กลายพันธุ์จับได้ตัวเดียว
    assert trail[17] == pytest.approx(117.086, abs=0.01)
    assert trail[17] < trail[16], "ต้องรีเซ็ตลง ไม่ใช่ค้างอยู่ที่ 125.904"


def test_the_two_lines_use_their_own_period_and_factor():
    """เส้นเดียวกันสองค่า · fast = 0.5×atr(5) ชิดราคากว่า slow = 2×atr(10) เสมอ

    ในเทรนด์ขาขึ้น ทั้งสองเส้นอยู่ใต้ราคา เส้นที่ระยะสั้นกว่าจึง**สูงกว่า**
    """
    series = bars([100.0 + i * 2 for i in range(25)])
    points = cdc_trailing_stop(series)
    tail = [p for p in points if p.fast is not None and p.slow is not None]

    assert tail, "ต้องมีช่วงที่ทั้งสองเส้นนิยามได้"
    for point in tail:
        assert point.fast > point.slow
        assert point.hst == pytest.approx(point.fast - point.slow)
