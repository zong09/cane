"""feature ป้อน Judge — ทุกคำตอบในไฟล์นี้คำนวณด้วยมือ ไม่ได้มาจากโค้ดที่กำลังทดสอบ

**ไม่แตะฐานข้อมูลและไม่ต่อเน็ต** ต้องรันได้ใต้ `-m "not db"` เสมอ (ธรรมเนียมเดียวกับ
`test_action_zone.py`)

`bars()` ของที่นี่เป็นคนละตัวกับของ `test_action_zone.py` โดยเจตนา — ตัวนั้นสร้างแท่ง
ที่มีแต่ราคาปิดเป็นสาระ และ docstring ของมันบอกไว้ว่าถ้าเทสต์ไหนต้องพึ่ง high/low
แปลว่าโมดูลเปลี่ยนขอบเขตไปแล้ว · features.py พึ่ง high/low/open ครบทุกช่อง จึงต้องมี
ตัวสร้างของตัวเอง ไม่ใช่ไปขยายตัวเก่าจนเทสต์ของใบ 04 เปลี่ยนความหมาย

## เทสต์กระจกเงา — ข้อที่พิสูจน์ว่าฝั่ง short เป็นภาพสะท้อนจริง

spec/04:24 เขียนว่าฝั่ง short "คือภาพสะท้อนของมันทีละข้อ ไม่ใช่ชุดกฎใหม่" ข้อความนั้น
ตรวจได้ก็ต่อเมื่อมีเทสต์ที่พลิกชุดข้อมูลทั้งชุดแล้วยืนยันว่าทุก feature พลิกตาม —
`test_every_feature_mirrors_when_the_series_flips` คือข้อนั้น ถ้าวันหนึ่งมีใครเพิ่ม
feature ฝั่งเดียวเข้ามา เทสต์นี้จะเป็นตัวที่ดัง ไม่ใช่ code review
"""

from __future__ import annotations

import dataclasses

import pytest

from cane.data import Bar
from cane.indicators import (
    Features,
    SwingPoint,
    features,
    fit_line,
    min_bars,
    pivot_highs,
    pivot_lows,
    true_range,
    wilder_atr,
)

DAY = 86_400_000


def bars(rows, *, first_open: int = 1_600_000_000_000, span: int = DAY):
    """สร้างแท่งจาก `(open, high, low, close)` ตรงๆ — ไม่มีการเดาค่าช่องไหนให้

    features.py อ่านครบทั้งสี่ช่อง การให้เทสต์ระบุเองทุกช่องคือสิ่งที่ทำให้คำตอบที่
    คำนวณด้วยมือตรวจย้อนได้จากตัวเลขในไฟล์นี้เพียงอย่างเดียว
    """
    return [
        Bar(
            open_ts=first_open + i * span,
            close_ts=first_open + (i + 1) * span,
            open=float(o),
            high=float(h),
            low=float(lo),
            close=float(c),
            volume=1.0,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def calm(n: int, *, base: float = 100.0):
    """`n` แท่ง doji ช่วงกว้าง 2.0 ไม่มี gap → ATR เป็น 2.0 เป๊ะไม่ว่าคาบเท่าไหร่

    ใช้เป็นหัวชุดข้อมูลเวลาต้องการให้ยาวถึง `min_bars()` โดยไม่ให้หัวชุดไปกวนค่าที่
    กำลังตรวจ · เป็น doji ทั้งหมดแปลว่า `red_run` และ `green_run` ของมันเป็น 0
    """
    return [(base, base + 1.0, base - 1.0, base) for _ in range(n)]


def one_big_bar(*, span: float = 1.0):
    """19 แท่ง doji แล้วปิดท้ายด้วยแท่งเดียวที่ body กว้าง 2.0

    `span` คือครึ่งความกว้างของทุกแท่ง → ATR เป็น `2·span` พอดี (TR ของทุกแท่งเท่ากัน
    หมด รวมแท่งท้ายที่ high/low ยังเท่าเดิม) · **body เท่าเดิมทั้งสองชุด เปลี่ยนแค่ ATR**
    คู่นี้จึงเป็นตัวที่แยกได้ว่า `body_atr` กับ `body_z` เป็นคนละตัวเลขจริง

    z-score คำนวณจาก body ดิบ = [0.0 × 19, 2.0] · mean = 0.1, var = 3.8/20 = 0.19
    → z = 1.9 ÷ √0.19 = √19 **ไม่ขึ้นกับ `span`** เพราะ z-score ไม่สนใจสเกล
    """
    flatline = [(100.0, 100.0 + span, 100.0 - span, 100.0)] * 19
    return bars([*flatline, (99.0, 100.0 + span, 100.0 - span, 101.0)])


def mirror(seq):
    """พลิกชุดข้อมูลรอบศูนย์ — สูงสุดกลายเป็นต่ำสุด แท่งเขียวกลายเป็นแท่งแดง

    `high` กับ `low` **สลับที่กัน** ไม่ใช่แค่ติดลบอยู่กับที่ ไม่งั้นจะได้แท่งที่
    `high < low` ซึ่งไม่ใช่ภาพสะท้อนแต่เป็นแท่งพัง
    """
    return [
        Bar(
            open_ts=b.open_ts,
            close_ts=b.close_ts,
            open=-b.open,
            high=-b.low,
            low=-b.high,
            close=-b.close,
            volume=b.volume,
        )
        for b in seq
    ]


# ── ชุดข้อมูลกลาง — ใช้ร่วมกันในเทสต์เส้นแนวโน้มและเทสต์กระจกเงา ─────────────────

#: กึ่งกลางของแต่ละแท่ง 24 ตัว · ยอดที่ยืนยันได้อยู่ที่ index 4, 10, 16 (สูง 120, 116,
#: 112 — เพดานที่กดลงมาเรื่อยๆ) แล้วราคาแหกขึ้นไปปิดที่ 129 ในสี่แท่งสุดท้าย
MIDS = [
    100, 105, 110, 115, 119, 114, 109, 104,
    100, 105, 115, 110, 105, 100, 104, 108,
    111, 106, 101, 99, 105, 115, 125, 129,
]


def zigzag():
    """แท่ง 24 ตัวจาก `MIDS` — `high = mid+1`, `low = mid-1`, body ±0.4 ตามทิศของ mid

    body อยู่ในกรอบ high/low เสมอ การใส่ body จึงไม่ขยับจุด pivot เลย (pivot อ่าน
    เฉพาะ high/low) — ตัวเลขยอดและก้นที่คำนวณด้วยมือไว้จึงยังใช้ได้ทั้งชุด
    """
    rows = []
    for i, mid in enumerate(MIDS):
        rising = i == 0 or mid > MIDS[i - 1]
        body = 0.4 if rising else -0.4
        rows.append((mid - body, mid + 1.0, mid - 1.0, mid + body))
    return bars(rows)


# ── true_range ────────────────────────────────────────────────────────────────


def test_the_first_bar_has_no_previous_close_so_true_range_is_just_its_span():
    """`None` คือ "ไม่มีแท่งก่อนหน้า" ไม่ใช่ "ปิดที่ศูนย์"

    ถ้าแทน `None` ด้วย 0.0 แท่งแรกจะได้ TR เท่ากับราคาทั้งก้อน แล้ว ATR ของทุกชุด
    ข้อมูลจะพองตามระดับราคาของเหรียญ ซึ่งจะทำให้ทุกค่าที่หารด้วย ATR ผิดพร้อมกันหมด
    """
    bar = bars([(100, 110, 108, 109)])[0]
    assert true_range(bar, None) == 2.0


def test_a_gap_beyond_the_bar_span_is_what_true_range_exists_to_catch():
    """ช่วงของแท่งเอง 2.0 แต่ราคาปิดก่อนหน้าอยู่ห่างออกไป — TR ต้องเป็นระยะที่ไกลกว่า"""
    bar = bars([(100, 110, 108, 109)])[0]
    assert true_range(bar, 112.0) == 4.0  # |108 - 112|
    assert true_range(bar, 105.0) == 5.0  # |110 - 105|


# ── wilder_atr ────────────────────────────────────────────────────────────────


def test_a_series_of_identical_ranges_gives_that_range_back_exactly():
    """ทุกแท่งช่วง 2.0 และไม่มี gap → ทั้ง seed และการ smooth ให้ 2.0 · ต้องเท่าเป๊ะ"""
    assert wilder_atr(bars(calm(15))) == 2.0


def test_the_smoothing_is_wilder_not_a_plain_average():
    """คำนวณด้วยมือ คาบ 2 · TR = [10, 4, 2, 10] → seed (10+4)/2 = 7 → 4.5 → 7.25

    ตัวเลขนี้แยกได้สามทางพร้อมกัน: SMA ของทั้งสี่ตัวคือ 6.5, RMA ที่ตัด TR ตัวแรกทิ้ง
    ก็ได้ 6.5 เหมือนกัน (บังเอิญ) ส่วน RMA ที่นับ TR ตัวแรกได้ 7.25 — ชุดตัวเลขก่อนหน้า
    นี้ให้ 6.5 ทั้งสองนโยบาย seed จึงแยกไม่ออกว่าโค้ดทำอะไรอยู่
    """
    series = bars(
        [
            (10, 16, 6, 10),
            (10, 14, 10, 12),
            (12, 13, 11, 12),
            (12, 22, 12, 20),
        ]
    )
    assert wilder_atr(series, 2) == 7.25


def test_the_first_true_range_goes_into_the_seed_the_way_pine_does_it():
    """`ta.atr` = `ta.rma(ta.tr(true), length)` และ `ta.tr(true)` คืน high−low ที่แท่งแรก

    คำนวณด้วยมือ คาบ 14 · TR = [100, แล้ว 2.0 อีก 14 ตัว] → seed = (100 + 13·2)/14 = 9.0
    → smooth ครั้งเดียวด้วย 2.0 = (9·13 + 2)/14 = 8.5

    ถ้าใครตัด TR ตัวแรกทิ้ง (ซึ่ง "สะอาด" กว่าในเชิงสถิติ) ค่าจะเป็น 2.0 พอดี — ห่างกัน
    มากพอที่เทสต์นี้จะเป็นตัวชี้ขาดว่าโค้ดใช้นโยบายไหน ซึ่งเป็นข้อที่ golden test ของ
    ใบ 09 จะมาตัดสินอีกที
    """
    series = bars([(100, 150, 50, 100), *calm(14)])
    assert wilder_atr(series) == 8.5


def test_too_few_bars_for_the_period_is_refused_not_averaged_over_what_there_is():
    with pytest.raises(ValueError, match="14"):
        wilder_atr(bars(calm(13)))


def test_a_period_below_one_is_refused():
    with pytest.raises(ValueError, match="คาบของ ATR"):
        wilder_atr(bars(calm(20)), 0)


# ── pivot ─────────────────────────────────────────────────────────────────────


def _from_lows(lows):
    return bars([(lo + 1.0, lo + 2.0, lo, lo + 1.0) for lo in lows])


def _from_highs(highs):
    return bars([(h - 1.0, h, h - 2.0, h - 1.0) for h in highs])


def test_a_confirmed_swing_low_is_the_only_one_the_window_admits():
    """low = [10, 9, 5, 9, 10, 11, 12] · หน้าต่าง 2/2 → มีก้นเดียวที่ index 2

    index 3 กับ 4 ต่ำกว่าเพื่อนบ้านบางตัวแต่หน้าต่างของมันยังมองเห็น 5 อยู่ จึงไม่ผ่าน
    """
    assert pivot_lows(_from_lows([10, 9, 5, 9, 10, 11, 12])) == [
        SwingPoint(index=2, price=5.0)
    ]


def test_the_newest_bars_can_never_be_pivots_however_extreme_they_are():
    """ก้นที่ต่ำที่สุดของชุดอยู่ท้ายสุด แต่ยังไม่มีแท่งขวามายืนยัน → ไม่อยู่ในรายการ

    นี่คือผลโดยตรงของ ADR 10 (ตัดสินบนแท่งปิดแล้ว) ไม่ใช่ข้อจำกัดที่ควรแก้ — ก้นที่
    เพิ่งเกิดยังกลับไปทำ low ใหม่ได้ การนับมันตอนนี้คือการมองอนาคต
    """
    found = pivot_lows(_from_lows([10, 9, 5, 9, 10, 11, 1]))
    assert [p.index for p in found] == [2]


def test_a_flat_bottom_is_not_two_swing_lows_at_the_same_price():
    """low เท่ากันสองแท่งติดกัน → ไม่นับเป็นจุดเหวี่ยงเลย ไม่ใช่นับเป็นสองจุด

    ถ้านับเป็นสองจุด `HIGHER_LOW` จะได้ข้อมูลเข้าว่า "ก้นใหม่สูงกว่าก้นเดิม 0.0"
    ซึ่งเป็นประโยคที่ไม่มีความหมายสำหรับคนตัดสิน
    """
    assert pivot_lows(_from_lows([10, 9, 5, 5, 9, 10, 11])) == []


def test_swing_highs_are_the_same_rule_read_upside_down():
    assert pivot_highs(_from_highs([10, 11, 15, 11, 10, 9, 8])) == [
        SwingPoint(index=2, price=15.0)
    ]


def test_a_pivot_window_below_one_is_refused():
    with pytest.raises(ValueError, match="หน้าต่าง pivot"):
        pivot_lows(_from_lows([1, 2, 3, 4, 5]), left=0)


# ── fit_line ──────────────────────────────────────────────────────────────────


def test_two_points_give_the_line_through_both_of_them():
    """(0, 10) และ (4, 2) → slope −2 intercept 10 · residual เป็นศูนย์ ต้องเท่าเป๊ะ"""
    fit = fit_line([SwingPoint(0, 10.0), SwingPoint(4, 2.0)])
    assert fit == (-2.0, 10.0)


def test_three_collinear_points_give_the_same_line_as_the_two_ends():
    fit = fit_line([SwingPoint(0, 10.0), SwingPoint(2, 6.0), SwingPoint(4, 2.0)])
    assert fit == pytest.approx((-2.0, 10.0))


def test_fewer_than_two_points_is_none_because_no_line_can_be_drawn():
    """`None` แปลว่าลากเส้นไม่ได้ ไม่ใช่ "เส้นแบน" — ตลาดที่ยังไม่เคยเหวี่ยงไม่มีกรอบให้เบรค"""
    assert fit_line([]) is None
    assert fit_line([SwingPoint(3, 7.0)]) is None


# ── features — ขอบและสภาวะที่ค่าหายไปอย่างถูกต้อง ───────────────────────────────


def test_below_the_minimum_the_call_is_refused_with_the_number_it_wanted():
    need = min_bars()
    with pytest.raises(ValueError, match=str(need)):
        features(bars(calm(need - 1)))


def test_the_minimum_is_the_formulas_own_need_not_the_data_layers_eighty_five():
    """`min_bars()` ตอบว่า "สูตรมีข้อมูลพอไหม" ส่วน `MIN_CLOSED_BARS` ตอบว่า "คู่นี้พร้อมเทรดไหม"

    สองเลขนี้ไม่ควรผูกกัน — ถ้าวันหนึ่งมีใครทำให้ `min_bars()` เท่ากับ 85 แปลว่า
    เอาเกณฑ์ของชั้นข้อมูลมาปนกับข้อจำกัดทางคณิตศาสตร์ของสูตร
    """
    from cane.data import MIN_CLOSED_BARS

    assert min_bars() == 20  # body_lookback เป็นตัวที่กว้างที่สุด ไม่ใช่ ATR
    assert min_bars() < MIN_CLOSED_BARS


def test_a_series_that_never_moves_is_refused_loudly_not_served_as_inf():
    """ราคาไม่ขยับเลย → ATR เป็นศูนย์ → ทุก feature ที่หารด้วยมันเป็น inf/nan

    เลือกให้ดังตรงนี้แทนที่จะส่ง inf ไปให้ LLM ตีความ — ธรรมเนียมเดียวกับที่
    `timeframe_ms()` ดังตั้งแต่ตอนเรียกแทนที่จะคำนวณผิดเงียบๆ
    """
    with pytest.raises(ValueError, match="ATR เป็นศูนย์"):
        features(bars([(100, 100, 100, 100)] * 25))


def test_a_calm_doji_series_reports_absence_not_zero():
    """แท่ง doji ล้วนที่ช่วงเท่ากันหมด — ทุกช่องที่ "ไม่มี" ต้องเป็น `None`/ว่าง ไม่ใช่ 0.0

    body ทุกแท่งเท่ากันเป๊ะ → std เป็นศูนย์ → z-score ไม่นิยาม การคืน 0.0 จะโกหกว่า
    "ขนาดปกติ" ทั้งที่ความจริงคือ "เทียบไม่ได้" · low เท่ากันหมด → ไม่มีจุดเหวี่ยง
    → ลากเส้นกรอบไม่ได้ทั้งสองเส้น
    """
    feat = features(bars(calm(24)))

    assert feat.atr == 2.0
    assert feat.body_atr == 0.0
    assert feat.body_z is None
    assert feat.swing_lows == ()
    assert feat.swing_highs == ()
    assert feat.resistance is None
    assert feat.support is None
    assert feat.red_run == 0
    assert feat.green_run == 0
    assert feat.gap_atr == 0.0


def test_a_doji_ends_both_runs_rather_than_only_one_of_them():
    """แท่งที่ปิดเท่าราคาเปิดไม่ใช่ทั้งแดงและเขียว — ต้องตัดทั้งสองชุด

    ถ้าตัดชุดเดียว ชุดที่เหลือจะวิ่งข้าม doji ไปนับรวมกับแท่งก่อนหน้า แล้ว
    `RETAIL_CAPITULATION` จะเห็น "แดงติดกัน 5 แท่ง" ที่มี doji คั่นกลางอยู่
    """
    reds = [(100, 100.5, 97, 97.5), (97.5, 98, 94, 95), (95, 95.5, 92, 93)]
    doji = [(93, 94, 92, 93)]
    assert features(bars([*calm(20), *reds])).red_run == 3
    ended = features(bars([*calm(20), *reds, *doji]))
    assert ended.red_run == 0
    assert ended.green_run == 0


def test_the_run_counts_the_streak_that_ends_at_the_last_bar():
    """สามแท่งแดงติดกันปิดท้ายชุด → `red_run` 3 และ `green_run` 0 · ไม่หักลบข้ามฝั่ง"""
    reds = [(100, 100.5, 97, 97.5), (97.5, 98, 94, 95), (95, 95.5, 92, 93)]
    feat = features(bars([*calm(20), *reds]))
    assert (feat.red_run, feat.green_run) == (3, 0)


def test_the_gap_is_signed_and_measured_in_atr():
    """คำนวณด้วยมือ: 20 แท่งสงบ (TR 2.0) แล้วแท่งที่ 21 เปิดต่ำลงไป 6.0

    TR ของแท่งสุดท้าย = |93 − 100| = 7.0 · ทิ้ง TR ตัวแรก เหลือ 2.0 อีก 19 ตัวกับ 7.0
    → seed = 2.0, smooth ด้วย 2.0 อีกห้าครั้งยังเป็น 2.0, ครั้งสุดท้าย (2·13 + 7)/14
    = 33/14 · gap = (94 − 100) ÷ 33/14 = −84/33 ติดลบเพราะเปิดต่ำกว่าปิดก่อนหน้า
    """
    feat = features(bars([*calm(20), (94, 95, 93, 94.5)]))
    assert feat.atr == pytest.approx(33.0 / 14.0)
    assert feat.gap_atr == pytest.approx(-6.0 / (33.0 / 14.0))


def test_a_bar_that_dwarfs_its_neighbours_scores_exactly_root_nineteen():
    """ค่าจริงของ `body_z` ที่คำนวณด้วยมือ — ที่มาอยู่ใน docstring ของ `one_big_bar()`

    ตรึงสองอย่าง: หน้าต่างรวมแท่งสุดท้ายเอง และ std เป็น population ไม่ใช่ sample
    """
    feat = features(one_big_bar())

    assert feat.atr == 2.0
    assert feat.body_z == pytest.approx(19.0**0.5)
    assert (feat.green_run, feat.red_run) == (1, 0)
    assert feat.gap_atr == pytest.approx(-0.5)


def test_the_z_score_cannot_do_the_job_of_the_ratio_because_it_ignores_scale():
    """สองชุดที่ body เท่ากันแต่ ATR ต่างกันเท่าตัว → `body_atr` ต่างกัน `body_z` เท่าเดิม

    นี่คือข้อที่บอกว่าทำไมสองฟิลด์นี้แยกกัน · ก่อนหน้านี้โค้ดหาร body ด้วย ATR **ก่อน**
    หา z-score แล้วเรียกผลว่าเป็นการอ่านสเปกรวมทั้งประโยค — เทสต์นี้แสดงว่าการหารนั้น
    หักล้างตัวเองทิ้ง ตัวเลขที่ได้คือ z-score ของ body ดิบทุกประการ ไม่มีอะไรของ ATR
    เหลืออยู่เลย ถ้ารวมสองแนวคิดเป็นฟิลด์เดียว คำถาม "ใหญ่เทียบความผันผวนไหม" จะหายไป
    """
    narrow, wide = features(one_big_bar()), features(one_big_bar(span=2.0))

    assert (narrow.atr, wide.atr) == (2.0, 4.0)
    assert narrow.body_atr == pytest.approx(1.0)
    assert wide.body_atr == pytest.approx(0.5)
    assert wide.body_z == pytest.approx(narrow.body_z)


def test_the_body_score_ignores_direction_but_the_run_and_the_gap_do_not():
    """กระจกเงาของชุดข้างบน — แท่งใหญ่ตัวเดิมกลายเป็นแท่งแดง แต่ "ใหญ่" เท่าเดิม

    `RETAIL_CAPITULATION` กับ `BUYING_EXHAUSTION` ใช้ตัวเลขขนาดชุดเดียวกัน ต่างกันที่
    ทิศซึ่งอ่านจาก `red_run`/`green_run` — ถ้า `body_atr`/`body_z` ดันมีทิศติดมาด้วย
    สองปัจจัยนี้จะนับทิศซ้ำสองครั้ง
    """
    up, down = features(one_big_bar()), features(mirror(one_big_bar()))

    assert down.body_z == pytest.approx(up.body_z)
    assert down.body_atr == pytest.approx(up.body_atr)
    assert (down.red_run, down.green_run) == (up.green_run, up.red_run)
    assert down.gap_atr == pytest.approx(-up.gap_atr)


def test_max_swings_below_one_is_refused_rather_than_quietly_returning_all_of_them():
    """`points[-0:]` คือทั้งรายการ ไม่ใช่รายการว่าง — ความผิดพลาดแบบที่ Python ไม่ฟ้อง"""
    with pytest.raises(ValueError, match="max_swings"):
        features(zigzag(), max_swings=0)


# ── features — เส้นกรอบแนวโน้ม ────────────────────────────────────────────────


def test_the_ceiling_is_fitted_through_the_confirmed_highs_only():
    """ยอดที่ยืนยันแล้วของ `MIDS` คือ index 4, 10, 16 ที่ราคา 120, 116, 112

    least squares ด้วยมือ: mean_x = 10, mean_y = 116, Sxx = 72, Sxy = −48
    → slope = −2/3, intercept = 116 + 20/3 = 368/3
    ตัวเลขชุดนี้ต้องมาจากยอดสามจุด **ไม่ใช่** จาก high ของทั้ง 24 แท่ง — ถ้าใครเปลี่ยน
    ไปฟิตบน high ดิบ slope จะกลายเป็นบวก (ราคาทั้งชุดยกตัวขึ้น) แล้วเทสต์นี้จะดัง
    """
    feat = features(zigzag())

    assert feat.resistance is not None
    assert feat.resistance.points == (4, 10, 16)
    assert feat.resistance.slope == pytest.approx(-2.0 / 3.0)
    assert feat.resistance.intercept == pytest.approx(368.0 / 3.0)


def test_price_above_the_falling_ceiling_shows_up_as_a_positive_distance():
    """เส้นที่ index 23 อยู่ที่ (−2/3)(23) + 368/3 = 322/3 ≈ 107.33 ราคาปิดอยู่ที่ 129.4

    ระยะห่างเป็นบวกคือสิ่งที่ `CHANNEL_BREAKOUT` มองหา — แต่ **เท่าไหร่ถึงเรียกว่า
    เบรคสำเร็จเป็นคำตัดสินของ LLM** เทสต์นี้ตรวจแค่ว่าเครื่องหมายกับมาตรวัดถูก
    """
    feat = features(zigzag())
    line = feat.resistance
    assert line is not None

    expected = (129.4 - 322.0 / 3.0) / feat.atr
    assert line.distance_atr == pytest.approx(expected)
    assert line.distance_atr > 0


def test_the_floor_is_fitted_through_the_confirmed_lows_and_is_a_separate_line():
    """ก้นที่ยืนยันแล้วอยู่ที่ index 8, 13, 19 — คนละชุดกับยอด จึงต้องได้คนละเส้น"""
    feat = features(zigzag())
    assert feat.support is not None
    assert feat.support.points == (8, 13, 19)
    assert feat.resistance is not None
    assert feat.support.slope != feat.resistance.slope


# ── ข้อบังคับของไฟล์ ──────────────────────────────────────────────────────────


def test_no_feature_is_a_boolean_because_the_verdict_belongs_to_the_llm():
    """spec/04:5-11 ยกคำว่า "สำเร็จ / ขนาดใหญ่ / ชัดเจน" ให้ LLM ตัดสิน

    `bool` ที่โผล่มาใน `Features` คือ threshold ที่ถูกย้ายกลับมาอยู่ในโค้ด — เทสต์นี้
    คือด่านที่จับมัน ไม่ใช่การอ่านโค้ดตอน review
    """
    kinds = [f.type for f in dataclasses.fields(Features)]
    assert not any("bool" in kind for kind in kinds), kinds


def test_every_feature_mirrors_when_the_series_flips():
    """พลิกชุดข้อมูลทั้งชุดรอบศูนย์ → ทุก feature ต้องพลิกตาม (spec/04:24)

    ATR กับขนาด body ไม่ขึ้นกับทิศ จึงต้อง **เท่าเดิม** ส่วนยอด/ก้น, สีของแท่ง และ
    ทิศของ gap ต้องสลับข้างกันหมด · ข้อนี้คือหลักฐานว่าฝั่ง short เป็นภาพสะท้อนจริง
    ไม่ใช่ชุดกฎที่สองที่บังเอิญเขียนคล้ายกัน
    """
    up, down = zigzag(), mirror(zigzag())
    a, b = features(up), features(down)

    assert b.atr == pytest.approx(a.atr)
    assert b.body_atr == pytest.approx(a.body_atr)
    # ชุดนี้ body เท่ากันทุกแท่ง z-score จึงไม่นิยามทั้งสองฝั่ง — เขียนเป็น `is None`
    # ตรงๆ ไม่ใช่ `approx` เทียบกัน เพราะ `None == approx(None)` ผ่านโดยไม่ได้ตรวจอะไร
    # ค่าจริงของมันมีเทสต์แยกอยู่ที่ `..._scores_exactly_root_nineteen`
    assert a.body_z is None and b.body_z is None

    assert [p.index for p in b.swing_lows] == [p.index for p in a.swing_highs]
    assert [p.price for p in b.swing_lows] == pytest.approx(
        [-p.price for p in a.swing_highs]
    )
    assert [p.index for p in b.swing_highs] == [p.index for p in a.swing_lows]

    assert (b.red_run, b.green_run) == (a.green_run, a.red_run)
    assert b.gap_atr == pytest.approx(-a.gap_atr)

    assert a.resistance is not None and b.support is not None
    assert b.support.points == a.resistance.points
    assert b.support.slope == pytest.approx(-a.resistance.slope)
    assert b.support.distance_atr == pytest.approx(-a.resistance.distance_atr)


def test_the_streak_at_the_end_of_the_shared_series_is_four_green_bars():
    """`MIDS` ยกตัวขึ้นตั้งแต่ index 20 ถึง 23 → เขียวติดกันสี่แท่ง · นับด้วยมือจากตาราง

    เทสต์กระจกเงาพึ่งตัวเลขนี้ (มันเทียบ `red_run` ของภาพสะท้อนกับ `green_run` ของ
    ต้นฉบับ) ถ้าไม่ตรึงไว้ ชุดข้อมูลที่ทุกแท่งเป็น doji ก็ผ่านเทสต์นั้นได้เหมือนกัน
    """
    feat = features(zigzag())
    assert (feat.green_run, feat.red_run) == (4, 0)
