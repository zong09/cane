"""Action Zone — สามจุดตายของใบ #04 ต้องพิสูจน์ได้โดยไม่ต้องมีไฟล์จาก TradingView

**ไฟล์นี้ไม่แตะฐานข้อมูลและไม่ต่อเน็ต** ต้องรันได้ใต้ `-m "not db"` เสมอ
(ธรรมเนียมเดียวกับ `test_ohlcv.py` และ `test_decision_record.py`)

golden test เทียบ TradingView คือเกณฑ์ปิดใบ แต่มันตรวจได้แค่ *ตรรกะของโซน* กับ
*การจัดแถวข้อมูล* — สองอย่างที่ใบเรียกว่าจุดตายมันตรวจไม่ได้เลย:

- **การ seed EMA** ค่าที่ TradingView export ออกมาถูก seed จากประวัติที่ยาวกว่าไฟล์
  มาก พอตัด warm-up 130 แท่งแล้วอิทธิพลของ seed เหลือราว `(25/27)**130` ≈ 4e-5
  มองไม่เห็นในระดับสี
- **`state` / `long_signal` / `short_signal`** สคริปต์ต้นทางไม่ได้ `plot()` ค่าพวกนี้
  (`bullish`/`bearish`/`buy`/`sell`) จึง **ไม่มีอยู่ในไฟล์ export** ต่อให้ได้ CSV มา
  ก็เทียบไม่ได้อยู่ดี

ตารางที่ `test_full_sequence_matches_oracle` ใช้ ถูกสร้างจาก oracle ที่เขียนขึ้นใหม่
จากไฟล์ Pine โดยตรงคนละรูปแบบกับ `action_zone.py` (series ทั้งเส้น + `barssince`
ที่ไล่ย้อนหลังจริง) ไม่ได้สร้างจากผลของโค้ดที่กำลังทดสอบ
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cane.data import Bar
from cane.indicators import action_zones, pine_ema, zone_of

DAY = 86_400_000


def bars(closes, *, first_open: int = 1_600_000_000_000, span: int = DAY):
    """แท่งที่มีแต่ราคาปิดเป็นสาระ — ที่เหลือใส่ให้สอดคล้องกันไว้เท่านั้น

    สูตร Action Zone อ่านแค่ `close` (spec/02:10 — `xsrc = close`) ถ้าเทสต์ไหน
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


def test_ema_follows_the_documented_recurrence():
    """คำนวณมือ: alpha = 2/3 · 1 → 5/3 → 23/9"""
    got = pine_ema([1.0, 2.0, 3.0], 2)

    assert got == pytest.approx([1.0, 5 / 3, 23 / 9])


def test_first_value_is_the_seed_and_is_never_na():
    """**นี่คือข้อตกลงเรื่อง seed ที่ยังไม่ถูกยืนยันกับ TradingView**

    คู่มือ Pine เขียน reference implementation ของ `ta.ema` ว่า seed ด้วยค่าแรกของ
    source ตรงๆ (เท่ากับ `pandas.ewm(adjust=False)`) แต่มีแหล่งที่บอกว่าฟังก์ชัน
    built-in seed ด้วย SMA และคืน `na` ก่อนครบ `length` แท่ง — อ่านโค้ดชี้ขาดไม่ได้

    เทสต์นี้ **ตรึงข้อตกลงที่เลือกไว้** ไม่ได้อ้างว่าถูก ถ้า golden test บอกว่าผิด
    เทสต์นี้คือตัวที่ต้องแก้พร้อมกับ `pine_ema()` และการที่มันแตกคือเจตนา
    """
    source = [42.0, 10.0, 10.0]

    assert pine_ema(source, 26)[0] == 42.0
    assert all(value is not None for value in pine_ema(source, 26))


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
    """หกโซนของ spec/02:32-38 · ระบบเทรดใช้แค่ GREEN/RED แต่ golden test เทียบครบ"""
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
EXPECTED = [
    ("BLACK", "UNSET", False, False, False, False),
    ("GREEN", "UNSET", True, False, False, False),
    ("GREEN", "UNSET", False, False, False, False),
    ("GREEN", "UNSET", False, False, False, False),
    ("ORANGE", "UNSET", False, False, False, False),
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
        None, 0, 1, 2, 3, 4, 5, 6, 7, 8, 0, 1, 2, 0, 1, 2, 3, 0
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
    """ผลตามมาของ `na` (spec/02:82) — ทั้งฝั่ง long และ short

    แท่ง 1 เป็น `longcond` ตัวแรกของชุด แท่ง 5 เป็น `shortcond` ตัวแรก ทั้งสองแท่ง
    แท่งก่อนหน้ายังเป็น `UNSET` จึงไม่มีสัญญาณ
    """
    got = computed()

    assert got[1].longcond and not got[1].long_signal
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


# ── golden test — ประตูปิดใบ ยังไม่มีไฟล์ ────────────────────────────────────

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "action_zone"


def test_golden_fixture_is_wired_up_once_it_exists():
    """ตัวสะดุด ไม่ใช่ golden test — golden test จริงยังเขียนไม่ได้

    เกณฑ์ปิดใบ #04 คือเทียบโซนทีละแท่งกับไฟล์ export จาก TradingView ≥500 แท่ง
    ตรง 100% หลังตัด warm-up 130 แท่ง (spec/02:93 กับ 103) ตอนนี้ **ยังไม่มีไฟล์**
    (ดู `fixtures/action_zone/README.md` ว่าต้อง export อะไรมา)

    loader ยังไม่เขียนโดยเจตนา — ชื่อคอลัมน์กับรูปแบบเวลาของไฟล์จริงต้องอ่านจากไฟล์
    ไม่ใช่เดา · เทสต์นี้ทำให้ "ไฟล์มาแล้วแต่ไม่มีใครต่อสาย" กลายเป็นเทสต์แดง
    ไม่ใช่ความเงียบ
    """
    found = sorted(GOLDEN_DIR.glob("*.csv")) if GOLDEN_DIR.is_dir() else []
    if not found:
        pytest.skip("ยังไม่มีไฟล์ export จาก TradingView — ใบ #04 ปิดไม่ได้")

    raise AssertionError(
        f"มีไฟล์ golden แล้ว ({', '.join(p.name for p in found)}) "
        "แต่ยังไม่มี loader — ต้องเขียน golden test ตัวจริงและตัด warm-up 130 แท่ง"
    )
