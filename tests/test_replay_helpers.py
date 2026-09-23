"""ตัวช่วยของ replay ออฟไลน์: ตัวกรอง lot กับรอบ funding

ไม่แตะฐานและไม่แตะเน็ต — ต้องรันได้ใน `-m "not db"`
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cane.engine.funding_cycles import FUNDING_INTERVAL_MS, funding_cycles
from cane.engine.lots import DEFAULT_LOTS, StaticLotSource, UnknownLot
from cane.sizing.matrix import LotFilter, plan_size

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS
# 2024-01-01 00:00:00 UTC — หารด้วย DAY_MS ลงตัว จึงอยู่บนขอบรอบ funding พอดี
T0 = 1_704_067_200_000


# ── lots ────────────────────────────────────────────────────────────────────


def test_default_table_has_the_two_paper_symbols():
    assert DEFAULT_LOTS[("usdtm_perp", "BTC/USDT")] == LotFilter(
        step=0.001, min_qty=0.001, min_notional=100.0
    )
    assert DEFAULT_LOTS[("spot", "ETH/USDT")] == LotFilter(
        step=0.0001, min_qty=0.0001, min_notional=5.0
    )


def test_default_source_returns_the_default_lots():
    src = StaticLotSource()
    assert src.lot("usdtm_perp", "BTC/USDT") == LotFilter(
        step=0.001, min_qty=0.001, min_notional=100.0
    )
    assert src.lot("spot", "ETH/USDT") == LotFilter(
        step=0.0001, min_qty=0.0001, min_notional=5.0
    )


def test_unknown_symbol_raises_unknown_lot():
    with pytest.raises(UnknownLot):
        StaticLotSource().lot("usdtm_perp", "DOGE/USDT")


def test_known_symbol_on_wrong_market_raises_unknown_lot():
    with pytest.raises(UnknownLot):
        StaticLotSource().lot("spot", "BTC/USDT")


def test_custom_table_is_honoured_and_does_not_mutate_defaults():
    custom = {("spot", "ETH/USDT"): LotFilter(step=0.01, min_qty=0.01)}
    src = StaticLotSource(custom)
    assert src.lot("spot", "ETH/USDT") == LotFilter(step=0.01, min_qty=0.01)
    with pytest.raises(UnknownLot):
        src.lot("usdtm_perp", "BTC/USDT")

    # แก้ตารางของผู้เรียกหลังสร้างแล้วต้องไม่รั่วเข้ามา
    custom.clear()
    assert src.lot("spot", "ETH/USDT") == LotFilter(step=0.01, min_qty=0.01)

    # ตัวสร้างตั้งต้นคัดลอกตาราง — `DEFAULT_LOTS` ต้องไม่ถูกแตะ
    assert len(DEFAULT_LOTS) == 2
    assert DEFAULT_LOTS[("usdtm_perp", "BTC/USDT")] == LotFilter(
        step=0.001, min_qty=0.001, min_notional=100.0
    )


def test_plan_size_accepts_the_btc_lot():
    # bucket_quote=1000.0 ปัดได้ qty=0.001 ซึ่ง notional (60) ต่ำกว่า min_notional=100 · ใช้ 10000.0
    # เพื่อให้ lot เดิมยังส่งได้
    dec = plan_size(
        base_pct=10,
        factors_present=0,
        bucket_quote=10000.0,
        max_position_pct=50.0,
        leverage=1.0,
        ref_px=60000.0,
        lot=StaticLotSource().lot("usdtm_perp", "BTC/USDT"),
    )
    assert dec.refused_reason is None
    # qty ต้องเป็นผลคูณของขั้น 0.001 พอดี (ตรวจด้วย Decimal ไม่ใช่ float เหตุผลเดียวกับ `floor_to_step`)
    qty = Decimal(str(dec.qty))
    steps = qty / Decimal("0.001")
    assert steps == steps.to_integral_value()


# ── funding_cycles ──────────────────────────────────────────────────────────


def test_aligned_day_yields_three_cycles():
    assert funding_cycles("BTC/USDT", T0, T0 + DAY_MS) == [
        (T0 + 8 * HOUR_MS, None),
        (T0 + 16 * HOUR_MS, None),
        (T0 + 24 * HOUR_MS, None),
    ]


def test_lower_bound_is_exclusive():
    # หน้าต่างที่เริ่มบนขอบพอดี: ขอบนั้นไม่นับ
    assert funding_cycles("BTC/USDT", T0 + 8 * HOUR_MS, T0 + 16 * HOUR_MS) == [
        (T0 + 16 * HOUR_MS, None)
    ]


def test_upper_bound_is_inclusive():
    # หน้าต่างที่จบบนขอบพอดี: ขอบนั้นนับ
    assert funding_cycles("BTC/USDT", T0, T0 + 8 * HOUR_MS) == [
        (T0 + 8 * HOUR_MS, None)
    ]


def test_empty_when_through_not_after():
    assert funding_cycles("BTC/USDT", T0, T0) == []
    assert funding_cycles("BTC/USDT", T0 + DAY_MS, T0) == []


def test_every_rate_is_none():
    cycles = funding_cycles("BTC/USDT", T0, T0 + 3 * DAY_MS)
    assert cycles
    assert all(rate is None for _, rate in cycles)


def test_splitting_into_daily_windows_matches_one_big_window():
    big = funding_cycles("BTC/USDT", T0, T0 + 3 * DAY_MS)
    split = []
    for i in range(3):
        split += funding_cycles("BTC/USDT", T0 + i * DAY_MS, T0 + (i + 1) * DAY_MS)
    assert split == big


def test_non_aligned_after_ts():
    # เลยขอบมาหนึ่งมิลลิวินาที: รอบถัดไปยังอยู่ที่ +8 ชม.
    assert funding_cycles("BTC/USDT", T0 + 1, T0 + DAY_MS) == [
        (T0 + 8 * HOUR_MS, None),
        (T0 + 16 * HOUR_MS, None),
        (T0 + 24 * HOUR_MS, None),
    ]


def test_interval_is_eight_hours():
    assert FUNDING_INTERVAL_MS == 8 * HOUR_MS
