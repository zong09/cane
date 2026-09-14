"""ขนาดหน้าตัก — ทุกคำตอบคำนวณด้วยมือ และตรวจกับ oracle อิสระอีกชุด

**ไม่แตะฐานข้อมูลและไม่ต่อเน็ต** ต้องรันได้ใต้ `-m "not db"` เสมอ

ตาราง `EXPECTED` ข้างล่างถูกคำนวณสามทางแยกกันแล้วตรงกันทั้งแปดแถว: คำนวณด้วยมือ,
ให้โมเดลอีกตัวคำนวณจากสูตรเปล่าโดยไม่เห็นโค้ดนี้เลย, และผลของโมดูลเอง · สองทางแรก
เป็นอิสระจากโค้ดที่กำลังทดสอบ ซึ่งเป็นเงื่อนไขที่ทำให้ตารางนี้เป็นเกณฑ์ได้จริง

## สองชุดพารามิเตอร์ตั้งใจให้ทุกช่องต่างกัน

ฝั่ง long ใช้ bucket 100 / เพดาน 50 / leverage 1 · ฝั่ง short ใช้ bucket 60 / เพดาน 40
/ leverage 2 (ตรงกับ `config/paper.toml` ของจริง) · ถ้าสองฝั่งใช้ตัวเลขเหมือนกัน เทสต์
จะผ่านทั้งที่โค้ดอ่าน `bucket_quote_long` มาใช้กับไม้ฝั่ง short — ซึ่งเป็นความผิดพลาด
ที่ spec/05:24 เตือนไว้ตรงๆ ("ทุกตัวในสูตรมาจากฝั่งเดียวกันหมด ห้ามผสม")
"""

from __future__ import annotations

import math

import pytest

from cane.sizing import (
    MAX_FACTORS,
    LotFilter,
    SizeDecision,
    floor_to_step,
    plan_size,
    size_pct,
)

BASE_PCT = 5.0
REF_PX = 100.0
LOT = LotFilter(step=0.001, min_qty=0.0)

#: `(n, side, bucket, cap, leverage)` → `(formula, final, capped, margin, notional, qty)`
EXPECTED = {
    (0, "long"): (5.0, 5.0, False, 5.0, 5.0, 0.05),
    (1, "long"): (25.0, 25.0, False, 25.0, 25.0, 0.25),
    (2, "long"): (45.0, 45.0, False, 45.0, 45.0, 0.45),
    (3, "long"): (65.0, 50.0, True, 50.0, 50.0, 0.5),
    (0, "short"): (5.0, 5.0, False, 3.0, 6.0, 0.06),
    (1, "short"): (25.0, 25.0, False, 15.0, 30.0, 0.3),
    (2, "short"): (45.0, 40.0, True, 24.0, 48.0, 0.48),
    (3, "short"): (65.0, 40.0, True, 24.0, 48.0, 0.48),
}

SIDE_CONFIG = {
    "long": {"bucket_quote": 100.0, "max_position_pct": 50.0, "leverage": 1.0},
    "short": {"bucket_quote": 60.0, "max_position_pct": 40.0, "leverage": 2.0},
}


def plan(n: int, side: str, **overrides) -> SizeDecision:
    kwargs = {
        "base_pct": BASE_PCT,
        "factors_present": n,
        "ref_px": REF_PX,
        "lot": LOT,
        **SIDE_CONFIG[side],
    }
    return plan_size(**{**kwargs, **overrides})


# ── เกณฑ์ปิดใบข้อ 1 — ทุก combination ของปัจจัย ทั้งสองฝั่ง ────────────────────


@pytest.mark.parametrize(("n", "side"), sorted(EXPECTED, key=lambda k: (k[1], k[0])))
def test_every_factor_count_on_both_sides_matches_the_hand_computed_table(n, side):
    """เกณฑ์ข้อ 1 · 0–3 ปัจจัย × สองฝั่ง = แปดแถว ครบตาราง `EXPECTED`"""
    formula, final, capped, margin, notional, qty = EXPECTED[(n, side)]
    got = plan(n, side)

    assert got.size_pct_formula == pytest.approx(formula)
    assert got.size_pct_final == pytest.approx(final)
    assert got.capped is capped
    assert got.margin == pytest.approx(margin)
    assert got.notional == pytest.approx(notional)
    assert got.qty == pytest.approx(qty)
    assert got.factors_present == n


def test_each_factor_adds_exactly_twenty_points_before_any_cap():
    """ตารางของ spec/05:30-36 · ไม่มีกรณีพิเศษที่กระโดดไป 100 เมื่อครบสามปัจจัย

    ทางเลือกนั้นถูกปฏิเสธไว้ในสเปกเพราะมันทำให้สูตรมีข้อยกเว้นที่อธิบายด้วยหลักการ
    ไม่ได้ · เพดาน 100 ยังอยู่ในโค้ดแต่แตะไม่ถึงในรูปปัจจุบัน (base_pct สูงสุด 20
    ให้ 80) ซึ่งถูกแล้ว มันคือเพดานเชิงความหมาย ไม่ใช่ค่าที่คำนวณมาให้พอดี
    """
    steps = [
        size_pct(base_pct=BASE_PCT, factors_present=n, max_position_pct=100.0)[0]
        for n in range(MAX_FACTORS + 1)
    ]
    assert steps == [5.0, 25.0, 45.0, 65.0]
    assert steps[-1] == BASE_PCT + 60.0


def test_the_documented_sixty_five_to_eighty_range_is_what_the_formula_actually_gives():
    """spec/05:44 · เจ้าของตัดสินว่ายอมรับ 65–80 ไม่ใช่ 80–100 ของเอกสารต้นทาง

    ตรึงปลายทั้งสองข้างของช่วงไว้ เพราะข้อนี้เป็นการตัดสินใจที่ถูกบันทึกไว้ในสเปก
    ถ้าใครขยับ `base_pct` ออกนอก 5–20 หรือแก้ตัวคูณ ตัวเลขคู่นี้จะเปลี่ยนเงียบๆ
    """
    at_min = size_pct(base_pct=5.0, factors_present=3, max_position_pct=100.0)[0]
    at_max = size_pct(base_pct=20.0, factors_present=3, max_position_pct=100.0)[0]
    assert (at_min, at_max) == (65.0, 80.0)


# ── เกณฑ์ปิดใบข้อ 2 — เพดานแยกฝั่ง ────────────────────────────────────────────


def test_the_two_sides_read_their_own_bucket_and_their_own_cap():
    """เกณฑ์ข้อ 2 · ฝั่ง long ชนเพดานที่ 3 ปัจจัย ฝั่ง short ชนตั้งแต่ 2

    สองฝั่งชนคนละจุดเพราะเพดานคนละค่า — ถ้าโค้ดอ่าน config ของฝั่งเดียวมาใช้ทั้งคู่
    จุดที่ชนจะตรงกัน แล้วข้อนี้จะดัง
    """
    assert [plan(n, "long").capped for n in range(4)] == [False, False, False, True]
    assert [plan(n, "short").capped for n in range(4)] == [False, False, True, True]


def test_a_cap_that_merely_equals_the_formula_is_not_a_cap():
    """`capped` ต้องแปลว่า "ถูกกด" ไม่ใช่ "เท่ากันพอดี"

    ถ้าตั้งเพดาน 65 แล้วสูตรให้ 65 ไม้นั้นไม่ได้ถูกกดเลย · การนับว่า capped จะทำให้
    รายงาน "กี่ไม้ที่ชนเพดาน" นับเกินทุกครั้งที่เพดานถูกตั้งไว้พอดีกับสูตร
    """
    formula, final, capped = size_pct(
        base_pct=BASE_PCT, factors_present=3, max_position_pct=65.0
    )
    assert (formula, final) == (65.0, 65.0)
    assert capped is False


def test_the_record_keeps_both_the_formula_value_and_the_final_one():
    """spec/05:72 · ต้องแยกออกว่าไม้เล็กเพราะปัจจัยน้อย หรือเพราะชนเพดาน

    เก็บแค่ค่าสุดท้ายทำให้สองเหตุผลนี้หน้าตาเหมือนกันเป๊ะ: ไม้ 50% ที่มาจาก
    "สูตรให้ 65 แล้วถูกกด" กับที่มาจาก "สูตรให้ 50 พอดี" แยกไม่ออกอีกเลย
    """
    capped_trade = plan(3, "long")
    assert (capped_trade.size_pct_formula, capped_trade.size_pct_final) == (65.0, 50.0)
    assert capped_trade.capped is True


# ── เกณฑ์ปิดใบข้อ 3 — leverage ที่ 1x และ 2x ──────────────────────────────────


def test_leverage_one_makes_notional_equal_margin_exactly():
    """เส้นทาง spot · ADR 26 บังคับ leverage = 1 แล้วสูตรเดินเส้นเดียวกันเอง

    ไม่ต้องมี `if` ที่ไหนและโมดูลนี้ไม่รู้จักคำว่า spot เลย — ข้อนี้คือหลักฐานว่า
    การไม่มีทางแยกนั้นถูกต้อง ไม่ใช่แค่ลืมเขียน (spec/05:56)
    """
    spot = plan(2, "long", leverage=1.0)
    assert spot.notional == spot.margin
    assert spot.qty == pytest.approx(spot.margin / REF_PX)


def test_leverage_multiplies_notional_not_size_pct():
    """spec/05:60 · ถ้าคูณที่ `size_pct` เพดานของ risk จะไม่มีความหมายอีกต่อไป

    สองไม้นี้ต่างกันที่ leverage อย่างเดียว — `size_pct` กับ `margin` ต้องเท่ากันเป๊ะ
    ส่วน `notional` กับ `qty` ต้องเป็นสองเท่า · ถ้า leverage ไปโผล่ที่ `size_pct`
    ไม้ 2x จะถูกเพดานกดแล้ว `margin` จะไม่เท่ากัน
    """
    one = plan(1, "long", leverage=1.0)
    two = plan(1, "long", leverage=2.0)

    assert one.size_pct_final == two.size_pct_final == 25.0
    assert one.margin == two.margin == pytest.approx(25.0)
    assert two.notional == pytest.approx(one.notional * 2)
    assert two.qty == pytest.approx(one.qty * 2)
    assert two.capped is False, "leverage ต้องไม่ทำให้ชนเพดาน"


# ── ปัดลง — จุดที่ float พังเงียบๆ ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        (0.3, 0.1, 0.3),
        (0.7, 0.1, 0.7),
        (2.675, 0.001, 2.675),
        (0.29, 0.01, 0.29),
        (4.35, 0.05, 4.35),
    ],
)
def test_values_where_binary_floats_lose_a_whole_step(value, step, expected):
    """ห้าเคสที่ `floor(v/step)*step` บน float ปัดเกินไปหนึ่งขั้นเต็มๆ

    `0.3 / 0.1` เป็น `2.9999999999999996` บน float แล้ว `floor` ได้ 2 → คำตอบ 0.2
    · เงินหายไปหนึ่งในสามของไม้เพราะการแทนเลขฐานสอง ซึ่งไม่มีเทสต์ระดับบนจับได้เลย

    เคสเหล่านี้ยังแยก `Decimal(str(x))` ออกจาก `Decimal(x)` ด้วย — ตัวหลังรับค่าฐานสอง
    ที่แท้จริงเข้ามาทั้งดุ้นแล้วให้คำตอบเดียวกับ float ทุกเคสในรายการนี้
    """
    assert floor_to_step(value, step) == pytest.approx(expected)
    assert math.floor(value / step) * step != pytest.approx(expected), (
        "เคสนี้ต้องเป็นเคสที่ float พังจริง ไม่งั้นมันไม่ได้ทดสอบอะไร"
    )


def test_it_floors_and_never_rounds_to_nearest():
    """spec/05:26 · "ปัดลงเสมอ — ปัดขึ้นแปลว่าเกินเพดานที่ตั้งไว้"

    `33.3337 / 0.001` เป็น `33333.7` · ปัดลงได้ `33.333` ส่วนปัดใกล้สุดได้ `33.334`
    ซึ่งทำให้ไม้ใหญ่กว่าที่เพดานอนุญาตหนึ่งขั้น
    """
    assert floor_to_step(33.3337, 0.001) == pytest.approx(33.333)
    assert floor_to_step(0.0009, 0.001) == 0.0
    assert floor_to_step(1.9999, 1.0) == 1.0


def test_a_quantity_that_does_not_divide_evenly_is_trimmed_not_grown():
    """notional 100 ที่ราคา 3 → 33.3333… → `33.333` ไม่ใช่ `33.334`"""
    got = plan_size(
        base_pct=100.0,
        factors_present=0,
        bucket_quote=100.0,
        max_position_pct=100.0,
        leverage=1.0,
        ref_px=3.0,
        lot=LotFilter(step=0.001, min_qty=0.0),
    )
    assert got.notional == pytest.approx(100.0)
    assert got.qty == pytest.approx(33.333)
    assert got.qty * 3.0 < got.notional, "ปัดลงแล้วมูลค่าต้องไม่เกิน notional"


# ── ตัวกรองของ venue — ไม้ที่คำนวณได้แต่ส่งไม่ได้ ─────────────────────────────


def test_a_trade_too_small_for_the_venue_is_refused_with_a_reason_not_a_zero():
    """`qty = 0` ที่ถูกส่งต่อไปจะไประเบิดที่ `Order.__post_init__` อีกสามชั้นถัดไป

    โดยที่ข้อความไม่ได้บอกว่าเพราะอะไร · ที่นี่บอกตั้งแต่ต้นทางว่า venue จะปฏิเสธ
    เพราะอะไร และตัวเลข `size_pct`/`margin` ยังเป็นของจริงที่ควรถูกบันทึก เพราะมัน
    ตอบว่า "ระบบตั้งใจจะลงเท่าไหร่" ซึ่งเป็นคนละคำถามกับ "ลงไปเท่าไหร่"
    """
    tiny = plan(0, "long", lot=LotFilter(step=0.001, min_qty=0.1))

    assert tiny.qty == pytest.approx(0.05)
    assert tiny.refused_reason == "below_min_qty"
    assert tiny.sendable is False
    assert tiny.margin == pytest.approx(5.0), "ตัวเลขที่ตั้งใจยังต้องอยู่ครบ"
    assert tiny.size_pct_final == 5.0


def test_min_notional_refuses_a_trade_that_passed_both_step_and_min_qty():
    """ตัวกรองสามตัวเป็นอิสระจากกัน — ผ่านสองข้อแล้วยังตกข้อที่สามได้"""
    got = plan(0, "long", lot=LotFilter(step=0.001, min_qty=0.01, min_notional=10.0))

    assert got.qty == pytest.approx(0.05)
    assert got.qty >= 0.01
    assert got.refused_reason == "below_min_notional"


def test_a_quantity_floored_all_the_way_to_zero_says_min_qty_not_min_notional():
    """`qty = 0` เข้าข่ายทั้งสองข้อพร้อมกัน · เหตุผลที่ตรงกว่าคือขนาด ไม่ใช่มูลค่า"""
    got = plan_size(
        base_pct=5.0,
        factors_present=0,
        bucket_quote=1.0,
        max_position_pct=100.0,
        leverage=1.0,
        ref_px=100_000.0,
        lot=LotFilter(step=0.001, min_qty=0.001, min_notional=5.0),
    )
    assert got.qty == 0.0
    assert got.refused_reason == "below_min_qty"


def test_no_min_notional_means_no_rule_not_a_rule_of_zero():
    """`None` คือ "venue นี้ไม่มีเกณฑ์" ไม่ใช่ "เกณฑ์เป็นศูนย์"

    สองอย่างให้ผลเดียวกันโดยบังเอิญ แต่ตัวหลังคือการอ้างว่ารู้ค่าที่จริงๆ แล้วไม่รู้
    """
    got = plan(0, "long", lot=LotFilter(step=0.001, min_qty=0.0, min_notional=None))
    assert got.sendable is True


# ── ค่าที่รับไม่ได้ต้องดังตั้งแต่ตอนเรียก ─────────────────────────────────────


def test_four_factors_is_refused_rather_than_clamped_to_three():
    """`ck_decisions_factors_present` บังคับ `BETWEEN 0 AND 3` ไว้ที่ฐานแล้ว

    `n = 4` แปลว่าผู้เรียกนับผิด · การหนีบให้เป็น 3 จะซ่อนการนับผิดนั้นไว้ใต้ตัวเลข
    ที่ดูสมเหตุสมผล แล้วไม้จะถูกลงด้วยขนาดที่ไม่มีใครสั่ง
    """
    with pytest.raises(ValueError, match="factors_present"):
        size_pct(base_pct=BASE_PCT, factors_present=4, max_position_pct=50.0)
    with pytest.raises(ValueError, match="factors_present"):
        size_pct(base_pct=BASE_PCT, factors_present=-1, max_position_pct=50.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bucket_quote", 0.0),
        ("bucket_quote", -1.0),
        ("leverage", 0.0),
        ("ref_px", 0.0),
        ("max_position_pct", 0.0),
    ],
)
def test_values_that_cannot_produce_a_trade_are_refused_at_the_call(field, value):
    with pytest.raises(ValueError, match=field):
        plan(1, "long", **{field: value})


def test_a_lot_filter_with_a_zero_step_is_refused():
    """`step = 0` ทำให้การปัดหารด้วยศูนย์ · ดังตอนสร้างตัวกรอง ไม่ใช่ตอนคำนวณไม้"""
    with pytest.raises(ValueError, match="step"):
        LotFilter(step=0.0, min_qty=0.0)
