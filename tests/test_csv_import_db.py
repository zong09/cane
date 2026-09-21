"""ไฟล์ export → ตาราง `bars` → `ReplayBarSource` — เส้นทางที่ `cane data import-bars` ใช้จริง

ต่างจาก `test_csv_import.py` ที่พิสูจน์แค่การแปลงไฟล์: ที่นี่พิสูจน์ว่าแท่งที่นำเข้าแล้วอ่านกลับมาเดินย้อนเวลาได้
โดย **ไม่แตะเครือข่าย** (`OfflineClient`) — เป็นข้อสันนิษฐานที่ replay ของใบ 12 ทั้งใบยืนอยู่บน

ใช้ symbol สมมติ ไม่ใช่ `BTC/USDT` เพื่อไม่ชนกับประวัติจริงที่อาจถูกนำเข้าไว้ใน dev DB แล้ว
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from cane.data import OfflineClient, ReplayBarSource, read_tradingview_csv
from cane.db.repo.bars import insert_bars
from cane.db.schema import bars as bars_t

pytestmark = pytest.mark.db

PERP = "usdtm_perp"
SYMBOL = "IMPORT/USDT"
DAY = 86_400_000
#: นาฬิกาที่อยู่หลังทุกแท่งในไฟล์ — `_load()` ใช้มันแยกแท่งที่ปิดแล้วตอนอ่านจากตาราง
FAR_FUTURE = 10**14

FIXTURE = (
    Path(__file__).parent / "fixtures" / "action_zone" / "BINANCE_BTCUSDT.P, 1D.csv"
)


def _stored(db) -> int:
    return db.execute(
        select(func.count()).select_from(bars_t).where(
            bars_t.c.market == PERP, bars_t.c.symbol == SYMBOL
        )
    ).scalar_one()


def test_importing_the_same_file_twice_adds_nothing_the_second_time(db):
    bars = read_tradingview_csv(FIXTURE, "1d")

    assert insert_bars(db, PERP, SYMBOL, "1d", bars) == len(bars) == 2564
    assert insert_bars(db, PERP, SYMBOL, "1d", bars) == 0
    assert _stored(db) == len(bars)


def test_replay_over_imported_bars_never_needs_the_network(db):
    """`OfflineClient` คืนว่างเสมอ — ถ้า `_load()` พึ่งค่าที่ดึงมา แท่งที่ได้จะเป็นศูนย์"""
    bars = read_tradingview_csv(FIXTURE, "1d")
    insert_bars(db, PERP, SYMBOL, "1d", bars)
    source = ReplayBarSource(
        db, OfflineClient(), PERP, as_of=bars[299].close_ts + 1, clock=lambda: FAR_FUTURE
    )

    seen = source.bars(SYMBOL, "1d")

    assert len(seen) == 300
    assert seen[-1].close_ts == bars[299].close_ts


def test_advancing_as_of_by_one_bar_reveals_exactly_one_more(db):
    """`as_of` เป็นแอตทริบิวต์ที่ไดรเวอร์ของ replay เลื่อนเอง — ต้องได้ทีละแท่งพอดี ไม่ข้าม ไม่ซ้ำ"""
    bars = read_tradingview_csv(FIXTURE, "1d")
    insert_bars(db, PERP, SYMBOL, "1d", bars)
    source = ReplayBarSource(
        db, OfflineClient(), PERP, as_of=bars[299].close_ts + 1, clock=lambda: FAR_FUTURE
    )
    before = len(source.bars(SYMBOL, "1d"))

    source.as_of += DAY

    after = source.bars(SYMBOL, "1d")
    assert len(after) == before + 1
    assert after[-1].close_ts == bars[300].close_ts


def test_a_bar_that_closes_exactly_at_as_of_is_not_yet_visible(db):
    """`closed_as_of()` ใช้ `close_ts < as_of` — แท่งที่เพิ่งปิดพอดีต้องรอ `as_of` ถัดไป

    ตัวนี้คือเหตุผลที่ไดรเวอร์ replay ต้องตั้ง `as_of = close_ts + 1` ไม่ใช่ `close_ts`
    """
    bars = read_tradingview_csv(FIXTURE, "1d")
    insert_bars(db, PERP, SYMBOL, "1d", bars)
    source = ReplayBarSource(
        db, OfflineClient(), PERP, as_of=bars[99].close_ts, clock=lambda: FAR_FUTURE
    )

    assert len(source.bars(SYMBOL, "1d")) == 99
