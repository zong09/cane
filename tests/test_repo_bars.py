"""repository ของ `bars` และ `funding_observations` — ไป-กลับต้องได้ของเดิม

สองเรื่องที่ไฟล์นี้เฝ้าเป็นพิเศษ:

1. **`NUMERIC` ใน DB ↔ `float` ในสูตร** จุดแปลงอยู่ที่ repository ที่เดียว ถ้ามัน
   คืน `Decimal` ออกไป golden test ที่เทียบกับ TradingView (#04) จะเพี้ยนหลักท้าย
2. **แท่งที่ปิดแล้วเขียนทับไม่ได้** และตัวกรอง `close_ts < as_of` ของ SQL ต้องให้
   ผลตรงกับ `closed_as_of()` ของ `data/ohlcv.py` ที่ขอบพอดี
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cane.data.funding import FundingRate
from cane.data.ohlcv import Bar, closed_as_of
from cane.db.repo.bars import closed_bars, insert_bars, last_bar
from cane.db.repo.funding import latest_observation, record_observation

pytestmark = pytest.mark.db

SYMBOL = "BTC/USDT"
TIMEFRAME = "1d"
DAY_MS = 86_400_000
T0 = 1_788_000_000_000


def bar(index: int, close: float = 77_500.0) -> Bar:
    open_ts = T0 + index * DAY_MS
    return Bar(
        open_ts=open_ts,
        close_ts=open_ts + DAY_MS,
        open=77_000.25,
        high=78_000.5,
        low=76_500.125,
        close=close,
        volume=1234.5,
    )


# ── ไป-กลับ ─────────────────────────────────────────────────────────────────


def test_a_bar_survives_the_round_trip_as_floats(db):
    insert_bars(db, SYMBOL, TIMEFRAME, [bar(0)])

    got = closed_bars(db, SYMBOL, TIMEFRAME, as_of=T0 + 10 * DAY_MS)

    assert got == [bar(0)]
    assert all(isinstance(value, float) for value in (got[0].close, got[0].volume))


def test_prices_keep_eight_decimals(db):
    """`NUMERIC(24,8)` — ราคาเหรียญเล็กใช้ทศนิยมลึกกว่าที่ BTC ใช้"""
    tiny = Bar(
        open_ts=T0,
        close_ts=T0 + DAY_MS,
        open=0.00001234,
        high=0.00001299,
        low=0.00001201,
        close=0.00001250,
        volume=98_765_432.5,
    )

    insert_bars(db, SYMBOL, TIMEFRAME, [tiny])
    got = closed_bars(db, SYMBOL, TIMEFRAME, as_of=T0 + DAY_MS + 1)

    assert got == [tiny]


def test_bars_come_back_oldest_first(db):
    insert_bars(db, SYMBOL, TIMEFRAME, [bar(2), bar(0), bar(1)])

    got = closed_bars(db, SYMBOL, TIMEFRAME, as_of=T0 + 10 * DAY_MS)

    assert [b.open_ts for b in got] == [T0, T0 + DAY_MS, T0 + 2 * DAY_MS]


def test_the_limit_takes_the_newest_bars_still_oldest_first(db):
    """ผู้เรียกทุกรายต้องการของล่าสุด (#04 ใช้ 130 แท่ง การตัดสินใจใช้ 85)"""
    insert_bars(db, SYMBOL, TIMEFRAME, [bar(i) for i in range(5)])

    got = closed_bars(db, SYMBOL, TIMEFRAME, as_of=T0 + 10 * DAY_MS, limit=2)

    assert [b.open_ts for b in got] == [T0 + 3 * DAY_MS, T0 + 4 * DAY_MS]


# ── เขียนแล้วไม่ทับ ─────────────────────────────────────────────────────────


def test_reinserting_a_bar_changes_nothing(db):
    """feed ที่ส่งค่าใหม่ให้แท่งที่ปิดแล้วคือ feed ที่ขัดกับตัวเอง — ของเดิมอยู่"""
    insert_bars(db, SYMBOL, TIMEFRAME, [bar(0, close=77_500.0)])

    written = insert_bars(db, SYMBOL, TIMEFRAME, [bar(0, close=77_600.0)])

    assert written == 0
    got = closed_bars(db, SYMBOL, TIMEFRAME, as_of=T0 + 10 * DAY_MS)
    assert got[0].close == 77_500.0


def test_inserting_reports_how_many_rows_were_new(db):
    insert_bars(db, SYMBOL, TIMEFRAME, [bar(0)])

    written = insert_bars(db, SYMBOL, TIMEFRAME, [bar(0), bar(1), bar(2)])

    assert written == 2


def test_inserting_nothing_touches_nothing(db):
    assert insert_bars(db, SYMBOL, TIMEFRAME, []) == 0


# ── `as_of` — กฎเดียวกับ closed_as_of() ─────────────────────────────────────


def test_the_as_of_boundary_matches_closed_as_of_exactly(db):
    """แท่งที่ปิด **ตรง** `as_of` ต้องยังไม่ถูกนับ ทั้งใน SQL และใน Python

    กฎ `<` (ไม่ใช่ `<=`) อยู่สองที่โดยจำเป็น — ที่นี่กรองใน SQL เพื่อไม่ต้องโหลด
    ประวัติทั้งก้อน เทสต์ตัวนี้คือสิ่งที่จะดังถ้าวันหนึ่งสองที่นั้นเลิกตรงกัน
    """
    history = [bar(0), bar(1), bar(2)]
    insert_bars(db, SYMBOL, TIMEFRAME, history)
    boundary = history[1].close_ts

    from_sql = closed_bars(db, SYMBOL, TIMEFRAME, as_of=boundary)

    assert from_sql == closed_as_of(history, boundary)
    assert [b.close_ts for b in from_sql] == [history[0].close_ts]


def test_last_bar_ignores_as_of_because_it_answers_a_different_question(db):
    """"เรามีของถึงไหน" เป็นเรื่องของการดึงข้อมูล ไม่ใช่ของการตัดสินใจ"""
    insert_bars(db, SYMBOL, TIMEFRAME, [bar(0), bar(1), bar(2)])

    assert last_bar(db, SYMBOL, TIMEFRAME) == bar(2)


def test_last_bar_of_an_unknown_pair_is_none(db):
    assert last_bar(db, "ETH/USDT", TIMEFRAME) is None


# ── ชื่อคู่เหรียญเหลือรูปเดียวในตาราง ────────────────────────────────────────


def test_the_perp_and_the_config_spelling_land_on_one_row(db):
    """`BTC/USDT:USDT` กับ `BTC/USDT` คือเหรียญเดียวกัน ไม่ใช่สองประวัติ"""
    insert_bars(db, "BTC/USDT:USDT", TIMEFRAME, [bar(0)])

    written = insert_bars(db, "BTC/USDT", TIMEFRAME, [bar(0)])

    assert written == 0
    assert closed_bars(db, "BTC/USDT:USDT", TIMEFRAME, as_of=T0 + DAY_MS + 1) == [bar(0)]


def test_timeframes_of_one_pair_do_not_mix(db):
    """cold start ทางที่ 1 อ่าน 1h ขณะที่การตัดสินใจอ่าน 1d (spec/07)"""
    insert_bars(db, SYMBOL, "1d", [bar(0)])
    insert_bars(db, SYMBOL, "1h", [bar(1)])

    got = closed_bars(db, SYMBOL, "1d", as_of=T0 + 10 * DAY_MS)

    assert [b.open_ts for b in got] == [T0]


# ── funding: ไม่มีข้อมูล ≠ ศูนย์ ────────────────────────────────────────────


def test_a_recorded_rate_comes_back_as_a_float(db):
    record_observation(db, FundingRate(SYMBOL, 0.00007542, 1_788_336_000_000), observed_ts=T0)

    got = latest_observation(db, SYMBOL)

    assert got == FundingRate(SYMBOL, 0.00007542, 1_788_336_000_000)
    assert isinstance(got.rate, float)


def test_a_missing_rate_stays_missing_and_keeps_its_reason(db):
    """`rate is None` ต้องไม่กลายเป็น 0 ตอนไป-กลับผ่านตาราง"""
    missing = FundingRate(SYMBOL, None, unavailable_reason="RequestTimeout")

    record_observation(db, missing, observed_ts=T0)
    got = latest_observation(db, SYMBOL)

    assert got == missing
    assert got.rate is None
    assert got.available is False


def test_a_real_zero_rate_is_not_read_as_missing(db):
    """venue ส่ง 0 มาจริงได้ และนั่นเป็นข้อมูล ไม่ใช่การขาดข้อมูล"""
    record_observation(db, FundingRate(SYMBOL, 0.0), observed_ts=T0)

    got = latest_observation(db, SYMBOL)

    assert got.rate == 0.0
    assert got.available is True
    assert got.unavailable_reason is None


def test_the_newest_observation_wins_and_ties_break_on_insert_order(db):
    """สองแถวที่เวลาเดียวกันเกิดได้ตอนโปรเซสรีสตาร์ทกลางแท่ง — เอาตัวที่เขียนหลัง"""
    record_observation(db, FundingRate(SYMBOL, 0.0001), observed_ts=T0)
    record_observation(db, FundingRate(SYMBOL, 0.0002), observed_ts=T0)

    assert latest_observation(db, SYMBOL).rate == pytest.approx(0.0002)


def test_observations_of_other_pairs_are_not_returned(db):
    record_observation(db, FundingRate("ETH/USDT", 0.0009), observed_ts=T0)

    assert latest_observation(db, SYMBOL) is None


def test_a_funding_rate_keeps_ten_decimals(db):
    """`NUMERIC(12,10)` — funding เป็นเลขระดับ 0.0001 การปัดที่ 8 ตำแหน่งกินนัยสำคัญ"""
    record_observation(db, FundingRate(SYMBOL, 0.0000123456), observed_ts=T0)

    assert latest_observation(db, SYMBOL).rate == pytest.approx(0.0000123456)


# ── การแปลงชนิดที่ขอบ ───────────────────────────────────────────────────────


def test_float_to_numeric_does_not_carry_binary_noise(db):
    """`Decimal(0.1)` ให้ `0.1000000000000000055…` — ต้องผ่าน `str` เท่านั้น"""
    from cane.db.types import price_to_db

    assert price_to_db(0.1) == Decimal("0.10000000")
