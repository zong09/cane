"""Action Zone — สามจุดตายของใบ #04 ต้องพิสูจน์ได้โดยไม่ต้องมีไฟล์จาก TradingView

**ไฟล์นี้ไม่แตะฐานข้อมูลและไม่ต่อเน็ต** ต้องรันได้ใต้ `-m "not db"` เสมอ
(ธรรมเนียมเดียวกับ `test_ohlcv.py` และ `test_decision_record.py`)

ไฟล์นี้เคยเขียนว่า golden test ตรวจสองอย่างนี้ไม่ได้ — **ไฟล์จริงพิสูจน์ว่าผิดทั้งคู่**
(ดูบล็อก golden test ท้ายไฟล์) จดไว้เพราะข้อสันนิษฐานทั้งสองเคยเป็นเหตุผลที่ไม่ยอม
export ไฟล์มาตรวจ:

- **การ seed EMA** เดาไว้ว่าตัด warm-up 130 แท่งแล้วอิทธิพลของ seed เหลือ ≈ 4e-5
  มองไม่เห็น · จริงคือไฟล์ **เว้นช่องว่างไว้ 11 กับ 25 แท่ง** ตรงกับ `length - 1`
  พอดี ซึ่งชี้ขาดว่า `ta.ema` seed ด้วย SMA ไม่ใช่ค่าแรกของ source
- **`long_signal` / `short_signal`** เดาไว้ว่าสคริปต์ไม่ได้ `plot()` ค่าพวกนี้ · จริง
  คือมีคอลัมน์ `Buy Signal` กับ `Sell Signal` อยู่ในไฟล์ · ที่ไม่มีจริงคือ `state`
  กับสีแท่ง (`barcolor` ไม่ถูก export) ซึ่ง oracle ข้างล่างยังเป็นตัวตรวจอยู่

ตารางที่ `test_full_sequence_matches_oracle` ใช้ ถูกสร้างจาก oracle ที่เขียนขึ้นใหม่
จากไฟล์ Pine โดยตรงคนละรูปแบบกับ `action_zone.py` (series ทั้งเส้น + `barssince`
ที่ไล่ย้อนหลังจริง) ไม่ได้สร้างจากผลของโค้ดที่กำลังทดสอบ
"""

from __future__ import annotations

import pytest

from cane.data import Bar
from cane.indicators import action_zones, pine_ema, zone_of
from cane.indicators.action_zone import FAST_PERIOD, SLOW_PERIOD
from golden import WARMUP_BARS, rows as golden_rows

DAY = 86_400_000


def bars(closes, *, first_open: int = 1_600_000_000_000, span: int = DAY):
    """แท่งที่มีแต่ราคาปิดเป็นสาระ — ที่เหลือใส่ให้สอดคล้องกันไว้เท่านั้น

    สูตร Action Zone อ่านแค่ `close` (spec/02 §พารามิเตอร์ — `xsrc = close`) ถ้าเทสต์ไหน
    ในอนาคตต้องพึ่ง high/low แปลว่าโมดูลเปลี่ยนขอบเขตไปแล้ว ไม่ใช่เทสต์ผิด
    """
    return [
        Bar(
            open_ts=first_open + i * span,
            close_ts=first_open + (i + 1) * span,
            open=float(c),
            high=float(c) + 1.0,
            low=float(c) - 1.0,
            close=float(c),
            volume=1.0,
        )
        for i, c in enumerate(closes)
    ]


# ── pine_ema — จุดเดียวที่ต้องแก้ถ้า golden test ชี้ว่า seed ผิด ────────────────


def test_smooth_of_one_returns_the_source_untouched():
    """`smooth = 1` → `alpha = 1.0` พอดี · ต้องเท่ากันแบบไม่มีคลาดเคลื่อน ไม่ใช่ approx

    เอกสารของโมดูลอ้างข้อนี้ไว้เพื่อบอกว่า `xPrice = close` ที่ค่าตั้งต้น ถ้าข้อนี้พัง
    การเทียบ `close_px` กับ `fast_ma` ตรงๆ ของทั้งไปป์ไลน์จะเพี้ยนเงียบๆ
    """
    source = [100.0, 101.5, 99.25, 103.0]

    assert pine_ema(source, 1) == source


def test_ema_follows_the_recurrence_after_the_sma_seed():
    """คำนวณมือ: na · seed = (1+2)/2 = 1.5 · alpha = 2/3 → 2/3·3 + 1/3·1.5 = 2.5"""
    got = pine_ema([1.0, 2.0, 3.0], 2)

    assert got[0] is None
    assert got[1:] == pytest.approx([1.5, 2.5])


def test_the_seed_is_the_sma_and_the_head_is_na():
    """**ข้อตกลงเรื่อง seed ที่ไฟล์จาก TradingView ยืนยันแล้ว** (เดิมตรงข้ามกับนี้)

    เทสต์ตัวก่อนหน้าที่นี่ตรึงข้อตกลงเก่าไว้ — seed ด้วยค่าแรกของ source ตามที่คู่มือ
    Pine เขียน — พร้อมโน้ตว่า "ถ้า golden test บอกว่าผิด การที่มันแตกคือเจตนา" ·
    ไฟล์ export บอกว่าผิดจริง: เส้น 12 คาบเว้นไว้ 11 แท่ง เส้น 26 คาบเว้นไว้ 25 แท่ง
    และค่าแรกตรงกับ SMA ของ `length` แท่งแรกถึงหลักที่ CSV พิมพ์ออกมา

    ข้อนี้ถือหลักฐานนั้นในรูปที่เล็กพอจะอ่านด้วยตา · golden test ข้างล่างถือตัวไฟล์
    """
    source = [42.0, 10.0, 10.0]

    assert pine_ema(source, 3) == [None, None, pytest.approx(62 / 3)]
    assert pine_ema(source, 26) == [None, None, None]


def test_ema_rejects_a_period_below_one():
    with pytest.raises(ValueError, match="ต้อง >= 1"):
        pine_ema([1.0, 2.0], 0)


def test_ema_of_nothing_is_nothing():
    assert pine_ema([], 12) == []


# ── zone_of — โซนทั้งเจ็ดครบทุกช่อง รวมช่องที่เกิดจากค่าเท่ากันพอดี ──────────────


@pytest.mark.parametrize(
    ("px", "fast_ma", "slow_ma", "expected"),
    [
        # bull (fast > slow)
        (110.0, 105.0, 100.0, "GREEN"),  # px > fast
        (102.0, 105.0, 100.0, "YELLOW"),  # fast > px > slow — Pre Sell 1
        (95.0, 105.0, 100.0, "ORANGE"),  # px < slow — Pre Sell 2
        # bear (fast < slow)
        (110.0, 100.0, 105.0, "BLUE"),  # px > slow — Pre Buy 2
        (102.0, 100.0, 105.0, "LBLUE"),  # fast < px < slow — Pre Buy 1
        (95.0, 100.0, 105.0, "RED"),  # px < fast
    ],
)
def test_every_zone_of_the_table(px, fast_ma, slow_ma, expected):
    """หกโซนของ spec/02 §นิยามโซนทั้ง 6 สี · ระบบเทรดใช้แค่ GREEN/RED แต่ golden test เทียบครบ"""
    assert zone_of(px, fast_ma, slow_ma) == expected


@pytest.mark.parametrize(
    ("px", "fast_ma", "slow_ma"),
    [
        (110.0, 100.0, 100.0),  # ไม่ bull ไม่ bear — EMA สองเส้นเท่ากันพอดี
        (100.0, 100.0, 95.0),  # bull แต่ px == fast พอดี
        (100.0, 100.0, 105.0),  # bear แต่ px == fast พอดี
        (105.0, 110.0, 105.0),  # bull, px < fast แต่ px == slow พอดี
    ],
)
def test_ties_fall_through_to_black(px, fast_ma, slow_ma):
    """เงื่อนไขทั้งหกใช้ `>` `<` ล้วน ช่องที่ค่าเท่ากันพอดีจึงเหลือเป็น `BLACK`

    `BLACK` เป็นสถานะจริงที่ต้องบันทึกได้ (schema.py — `zone_t`) ไม่ใช่ค่าที่หายไป
    ถ้าใครเปลี่ยนเป็น `>=` เพื่อ "กันช่องว่าง" โซนจะทับกันและลำดับของ if จะเริ่มมีผล
    """
    assert zone_of(px, fast_ma, slow_ma) == "BLACK"


# ── action_zones — ลำดับทั้งเส้น เทียบกับ oracle ที่เขียนแยกจากไฟล์ Pine ──────────

#: ราคาปิดที่ออกแบบให้ครอบทุกเคสที่ใบ #04 ระบุด้วยชุดเดียว ใช้ fast=2 slow=3
#: เพื่อให้ครอสเกิดเร็วพอที่จะไล่ด้วยตาได้ · ค่าที่คาดหวังมาจาก oracle อิสระ
CLOSES = [100, 101, 102, 103, 102, 101, 100, 99, 98, 98.6, 103, 104, 103, 105, 99, 95, 96, 101]

#: (zone, state, longcond, shortcond, long_signal, short_signal) ต่อแท่ง
#:
#: สองแท่งแรกเป็น `BLACK` เพราะ SlowMA (3 คาบ) ยัง seed ไม่ได้ · แท่ง 4 เป็น `BLACK`
#: เพราะ `px == slow_ma` พอดี (102.0 ทั้งคู่) ซึ่งไม่เข้าทั้ง `>` และ `<` — เป็น
#: ช่องที่ `test_ties_fall_through_to_black` ตรึงไว้ โผล่มาเองในชุดจริง
EXPECTED = [
    ("BLACK", "UNSET", False, False, False, False),
    ("BLACK", "UNSET", False, False, False, False),
    ("GREEN", "UNSET", True, False, False, False),
    ("GREEN", "UNSET", False, False, False, False),
    ("BLACK", "UNSET", False, False, False, False),
    ("RED", "BEARISH", False, True, False, False),
    ("RED", "BEARISH", False, False, False, False),
    ("RED", "BEARISH", False, False, False, False),
    ("RED", "BEARISH", False, False, False, False),
    ("LBLUE", "BEARISH", False, False, False, False),
    ("GREEN", "BULLISH", True, False, True, False),
    ("GREEN", "BULLISH", False, False, False, False),
    ("YELLOW", "BULLISH", False, False, False, False),
    ("GREEN", "BULLISH", True, False, False, False),
    ("RED", "BEARISH", False, True, False, True),
    ("RED", "BEARISH", False, False, False, False),
    ("RED", "BEARISH", False, False, False, False),
    ("GREEN", "BULLISH", True, False, True, False),
]

def computed():
    return action_zones(bars(CLOSES), fast=2, slow=3, smooth=1)


def test_full_sequence_matches_oracle():
    got = [
        (z.zone, z.state, z.longcond, z.shortcond, z.long_signal, z.short_signal)
        for z in computed()
    ]

    assert got == EXPECTED


def test_barssince_is_none_until_it_has_happened_once():
    """จุดตายข้อ 2 — `na` ไม่ใช่อนันต์ และไม่ใช่ศูนย์

    `bars_since_short` เป็น `None` ตลอดห้าแท่งแรกเพราะยังไม่มี `shortcond` เลย
    ถ้าใครแทน `None` ด้วย `+inf` ค่าพวกนี้จะกลายเป็นเลขที่เทียบได้ทันที แล้ว
    `state` จะเป็น `BEARISH` ตั้งแต่แท่งแรก → เกิดสัญญาณผีที่แท่ง 1
    """
    got = computed()

    assert [z.bars_since_long for z in got] == [
        None, None, 0, 1, 2, 3, 4, 5, 6, 7, 0, 1, 2, 0, 1, 2, 3, 0
    ]
    assert [z.bars_since_short for z in got] == [
        None, None, None, None, None, 0, 1, 2, 3, 4, 5, 6, 7, 8, 0, 1, 2, 3
    ]


def test_state_is_unset_until_both_conditions_have_happened():
    """และเมื่อเกิดครบทั้งคู่แล้ว จะไม่กลับไป `UNSET` อีกเลย"""
    got = computed()
    first_both = next(
        i
        for i, z in enumerate(got)
        if z.bars_since_long is not None and z.bars_since_short is not None
    )

    assert all(z.state == "UNSET" for z in got[:first_both])
    assert all(z.state != "UNSET" for z in got[first_both:])


def test_the_first_condition_of_the_series_is_never_a_signal():
    """ผลตามมาของ `na` (spec/02 §สามจุดที่พลาดง่ายตอน port) — ทั้งฝั่ง long และ short

    แท่ง 2 เป็น `longcond` ตัวแรกของชุด (แท่ง 0-1 ยังไม่มี SlowMA) แท่ง 5 เป็น
    `shortcond` ตัวแรก · ทั้งสองแท่ง แท่งก่อนหน้ายังเป็น `UNSET` จึงไม่มีสัญญาณ
    """
    got = computed()

    assert got[2].longcond and not got[2].long_signal
    assert got[5].shortcond and not got[5].short_signal


def test_green_again_while_still_bullish_is_not_a_buy():
    """จุดตายข้อ 1 — `long_signal` ไม่เท่ากับ "แท่งเขียวแรก"

    แท่ง 12 ย่อลงไป `YELLOW` โดยสถานะยังเป็น `BULLISH` แล้วแท่ง 13 กลับมา `GREEN`
    นั่นคือ `longcond` ตามนิยาม แต่ **ไม่ใช่สัญญาณ** เพราะแท่งก่อนหน้าไม่ได้เป็น
    `BEARISH` · ถ้า implement ตามคำว่า "เขียวแรก" ตรงตัวจะได้สัญญาณเกินจริงที่นี่
    """
    got = computed()

    assert got[12].zone == "YELLOW"
    assert got[12].state == "BULLISH"
    assert got[13].zone == "GREEN"
    assert got[13].longcond
    assert not got[13].long_signal


def test_long_and_short_signals_never_fire_on_the_same_bar():
    """GREEN กับ RED ทับกันไม่ได้ · ถ้าข้อนี้พัง กฎ flip ของ spec/03 จะมีทางเข้าที่ผิด"""
    assert not any(z.long_signal and z.short_signal for z in computed())


def test_zone_is_reproducible_from_the_two_ema_lines_alone():
    """นี่คือรูปเดียวกับที่ golden test จะใช้เทียบไฟล์ export

    ไฟล์จาก TradingView มีคอลัมน์ `close`, `Fast EMA`, `Slow EMA` (ค่าที่ `plot()`)
    แต่ไม่มีสีของแท่ง (`barcolor` ไม่ถูก export) — เทียบได้ก็เพราะโซนเป็นฟังก์ชัน
    บริสุทธิ์ของสามค่านั้นเมื่อ `smooth = 1` ข้อนี้พังแปลว่าเทียบด้วยวิธีนั้นไม่ได้แล้ว
    """
    for zone in computed():
        assert zone.zone == zone_of(zone.close_px, zone.fast_ma, zone.slow_ma)


def test_result_lines_up_with_the_input_bars():
    source = bars(CLOSES)
    got = action_zones(source, fast=2, slow=3)

    assert [z.bar_close_ts for z in got] == [b.close_ts for b in source]
    assert [z.close_px for z in got] == [b.close for b in source]


def test_no_bars_gives_no_rows():
    assert action_zones([]) == []


def test_default_periods_run_on_a_realistic_length():
    """ค่าตั้งต้น 12/26 กับชุด 200 แท่ง — กันการพังที่โผล่เฉพาะคาบจริง"""
    closes = [100.0 + (i % 17) - (i % 5) * 2 for i in range(200)]

    got = action_zones(bars(closes))

    assert len(got) == 200
    assert {z.zone for z in got} <= {
        "GREEN", "BLUE", "LBLUE", "RED", "ORANGE", "YELLOW", "BLACK"
    }
    assert {z.state for z in got} <= {"BULLISH", "BEARISH", "UNSET"}


# ── golden test — เทียบกับไฟล์ export จริงจาก TradingView ─────────────────────


@pytest.fixture(scope="module")
def golden():
    return golden_rows()


def test_golden_file_is_long_enough_to_close_the_ticket(golden):
    """เกณฑ์ปิดใบคือ ≥500 แท่ง *หลังตัด warm-up* — ต้องวัดก่อนจะเชื่อข้อถัดไป

    ถ้าวันหนึ่งมีคน export ไฟล์สั้นมาทับ เทสต์โซนข้างล่างจะยัง "ผ่าน" ทั้งที่
    พิสูจน์อะไรไม่ได้แล้ว ข้อนี้คือตัวที่ดังแทน
    """
    assert len(golden) - WARMUP_BARS >= 500


def test_every_zone_matches_tradingview_after_warmup(golden):
    """โซนของเราต้องตรงกับ TradingView **ทุกแท่ง** หลังตัด warm-up 130 แท่ง

    เทียบกับ `zone_of(close, Fast EMA, Slow EMA)` ของ *ค่าจากไฟล์* ไม่ใช่ของเรา ·
    `barcolor()` ไม่ถูก export ออกมา แต่โซนเป็นฟังก์ชันบริสุทธิ์ของสามค่านั้นเมื่อ
    `smooth = 1` (ตรึงไว้ที่ `test_zone_is_reproducible_from_the_two_ema_lines_alone`)
    สิ่งที่ข้อนี้ตรวจจริงๆ จึงคือ **EMA ของเราพาโซนไปทางเดียวกับของเขาไหม**
    """
    ours = action_zones([row.bar for row in golden])

    mismatched = [
        (index, zone_of(row.bar.close, row.fast_ema, row.slow_ema), ours[index].zone)
        for index, row in enumerate(golden)
        if index >= WARMUP_BARS and row.fast_ema is not None and row.slow_ema is not None
    ]
    mismatched = [item for item in mismatched if item[1] != item[2]]

    assert not mismatched, f"โซนไม่ตรง {len(mismatched)} แท่ง: {mismatched[:5]}"


def test_buy_and_sell_signals_match_tradingview(golden):
    """`Buy Signal` / `Sell Signal` ในไฟล์คือ `long_signal` / `short_signal` ของเรา

    หัวไฟล์นี้เคยเขียนว่าสัญญาณ "ไม่มีอยู่ในไฟล์ export" — **ผิด** · สคริปต์ต้นทาง
    `plot()` มันไว้จริง ไฟล์จึงตรวจได้ทั้งเงื่อนไขโซนและกฎ `bearish[1]` ที่เป็น
    จุดตายข้อที่ 1 ของใบนี้ ซึ่งเป็นส่วนที่ oracle ในไฟล์นี้ตรวจให้ไม่ได้

    เทียบเป็น **เซ็ตของดัชนีทั้งไฟล์ ไม่ตัด warm-up** โดยเจตนา — `barssince` ที่ยัง
    เป็น `na` มีผลเฉพาะช่วงหัว การตัดหัวทิ้งจะทำให้ข้อที่อยากตรวจที่สุดหลุดไป
    """
    ours = action_zones([row.bar for row in golden])

    assert {i for i, z in enumerate(ours) if z.long_signal} == {
        i for i, row in enumerate(golden) if row.buy
    }
    assert {i for i, z in enumerate(ours) if z.short_signal} == {
        i for i, row in enumerate(golden) if row.sell
    }


def test_signals_are_a_strict_subset_of_the_zone_conditions(golden):
    """กันการ "ตรงเพราะบังเอิญ" ของข้อบน — ถ้า `long_signal` กลายเป็น `longcond`

    ในไฟล์นี้ `longcond` เกิด 117 ครั้งแต่ `Buy Signal` เกิด 38 ครั้ง · ถ้าใครทำกฎ
    `bearish[1]` หายไป ข้อบนจะดังอยู่แล้ว ข้อนี้บอกว่ามันดัง**เพราะอะไร**
    """
    ours = action_zones([row.bar for row in golden])

    assert sum(z.long_signal for z in ours) < sum(z.longcond for z in ours)
    assert sum(z.short_signal for z in ours) < sum(z.shortcond for z in ours)
    assert all(z.longcond for z in ours if z.long_signal)
    assert all(z.shortcond for z in ours if z.short_signal)


def test_both_ema_lines_match_tradingview(golden):
    """เส้น EMA ของเราต้องตรงกับไฟล์ **ทุกแท่งที่ไฟล์มีค่า** ไม่ใช่แค่หลัง warm-up

    นี่คือข้อที่ชี้ขาดเรื่องการ seed ซึ่งหัวไฟล์ของ `action_zone.py` ค้างไว้ ·
    เทียบตรงๆ กับ `pine_ema()` ไม่ผ่าน `action_zones()` เพื่อให้ที่เกิดเหตุชัด

    เกณฑ์ `1e-6` เทียบกับราคา BTC หลักหมื่น = ราว 1e-10 เชิงสัดส่วน — แน่นพอที่
    การ seed ต่างกันจะหลุด แต่ไม่แน่นจนไปดังเพราะการปัดเศษของ CSV
    """
    closes = [row.bar.close for row in golden]

    for name, period, theirs in (
        ("Fast EMA", FAST_PERIOD, [row.fast_ema for row in golden]),
        ("Slow EMA", SLOW_PERIOD, [row.slow_ema for row in golden]),
    ):
        ours = pine_ema(closes, period)
        off = [
            i
            for i, (mine, their) in enumerate(zip(ours, theirs, strict=True))
            if their is not None and (mine is None or abs(mine - their) > 1e-6)
        ]
        assert not off, f"{name} ต่างจากไฟล์ {len(off)} แท่ง แท่งแรกคือ {off[:3]}"

        blank = [i for i, their in enumerate(theirs) if their is None]
        assert blank == list(range(period - 1)), (
            f"{name} ในไฟล์ว่าง {len(blank)} แท่ง ไม่ใช่ {period - 1} "
            "— ข้อสันนิษฐานเรื่องความยาวช่วงอุ่นเครื่องผิด"
        )
        assert [i for i, mine in enumerate(ours) if mine is None] == blank, (
            f"{name}: ช่วงที่เราคืน `None` ไม่ตรงกับช่องว่างในไฟล์"
        )
