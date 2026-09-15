"""CDC ATR Trailing Stop เทียบกับไฟล์ export จริง — ตัวตัดสินการ seed ของ ATR

`wilder_atr_series()` นับ TR ของแท่งแรก (`high - low` เพราะไม่มีราคาปิดก่อนหน้า)
เข้า seed ด้วย ซึ่ง**เป็นข้อสันนิษฐาน** มาตลอด อ่านโค้ด Pine ชี้ขาดไม่ได้ว่า
`ta.atr` นับแท่งแรกเข้าหรือข้าม · ไฟล์ที่มีคอลัมน์ `Fast Trail` / `Slow Trail`
เป็นตัวชี้ขาด เพราะ ATR ที่ต่างกันแม้นิดเดียวจะทำให้เส้น trail เคลื่อนทันที

เทสต์ที่เหลือของ `cdc_trail()` อยู่ใน `test_late_entry.py` เพราะเขียนขึ้นมาเพื่อ
พิสูจน์เส้นทาง cold start ทางที่ 2 · ที่นี่มีแต่การเทียบกับของจริง
"""

from __future__ import annotations

import pytest

from cane.indicators.features import wilder_atr_series
from cane.indicators.trailing import (
    FAST_ATR_FACTOR,
    FAST_ATR_PERIOD,
    SLOW_ATR_FACTOR,
    SLOW_ATR_PERIOD,
    cdc_trail,
)
from golden import rows as golden_rows


@pytest.fixture(scope="module")
def golden():
    return golden_rows()


LINES = (
    ("Fast Trail", FAST_ATR_PERIOD, FAST_ATR_FACTOR, "fast_trail"),
    ("Slow Trail", SLOW_ATR_PERIOD, SLOW_ATR_FACTOR, "slow_trail"),
)


@pytest.mark.parametrize(("name", "period", "factor", "field"), LINES)
def test_the_trail_line_matches_tradingview_on_every_bar(
    golden, name, period, factor, field
):
    """ทุกแท่ง ไม่ตัด warm-up — เส้น trail ไม่มีช่วงอุ่นเครื่องให้ตัดอยู่แล้ว

    `Trail` ของแท่งหนึ่งขึ้นกับ `Trail[1]` ย้อนไปถึงแท่งแรกที่ ATR นิยามได้ ·
    ความคลาดเคลื่อนจากการ seed จึงไม่จางหายไปตามเวลาเหมือน EMA แต่ถูกพาไปทั้งเส้น
    ผ่าน `max`/`min` · การที่มันตรงตลอด 2564 แท่งคือหลักฐานว่า seed ถูก ไม่ใช่ว่า
    ผลของ seed เล็กเกินกว่าจะเห็น
    """
    theirs = [getattr(row, field) for row in golden]
    ours = cdc_trail([row.bar for row in golden], period=period, factor=factor)

    off = [
        index
        for index, (mine, their) in enumerate(zip(ours, theirs, strict=True))
        if their is not None and (mine is None or abs(mine - their) > 1e-6)
    ]

    assert not off, f"{name} ต่างจากไฟล์ {len(off)} แท่ง แท่งแรกคือ {off[:3]}"


@pytest.mark.parametrize(("name", "period", "factor", "field"), LINES)
def test_the_first_bar_of_true_range_counts_toward_the_atr_seed(
    golden, name, period, factor, field
):
    """**นี่คือข้อที่ชี้ขาดการ seed ของ ATR** ข้อบนยืนยันค่า ข้อนี้ยืนยันว่าทำไม

    ไฟล์เว้นค่าไว้ `period - 1` แท่ง แปลว่าค่าแรกอยู่ที่ดัชนี `period - 1` ซึ่ง
    ครอบ TR ของแท่งที่ 0..`period-1` = **`period` ตัว โดยนับแท่งแรกเข้าด้วย** ·
    ถ้า `ta.atr` ข้ามแท่งแรก (เพราะไม่มีราคาปิดก่อนหน้าให้ใช้) ค่าแรกจะต้องอยู่ที่
    ดัชนี `period` ไม่ใช่ `period - 1` · `wilder_atr_series()` ทำแบบแรกอยู่แล้ว

    ข้อนี้ปลดข้อสันนิษฐานที่ `features.py` แขวนไว้ตั้งแต่ตอนพอร์ต
    """
    theirs = [getattr(row, field) for row in golden]
    atr = wilder_atr_series([row.bar for row in golden], period)

    assert [i for i, v in enumerate(theirs) if v is None] == list(range(period - 1))
    assert [i for i, v in enumerate(atr) if v is None] == list(range(period - 1))


def test_the_two_lines_are_not_the_same_line(golden):
    """กันการผ่านแบบว่างเปล่า ถ้าวันหนึ่งสองคอลัมน์ชี้ไปที่ข้อมูลชุดเดียวกัน"""
    fast = [row.fast_trail for row in golden]
    slow = [row.slow_trail for row in golden]

    assert fast != slow
    assert sum(1 for f, s in zip(fast, slow) if f is not None and s is not None) > 500
