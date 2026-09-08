"""OHLCV — แท่งที่คืนมาต้องปิดแล้วเสมอ และคู่ที่ข้อมูลไม่ถึง 85 แท่งต้องถูกข้าม

เกณฑ์ของใบ #02 มีสองข้อ เทสต์ในไฟล์นี้ยึดสองข้อนั้นเป็นหลัก:
  1. แท่งล่าสุดที่คืนมามี close timestamp < now **เสมอ**
  2. คู่ที่มีข้อมูลน้อยกว่า 85 แท่งถูกข้าม

**ไฟล์นี้ไม่แตะฐานข้อมูล** และต้องรันได้ใต้ `-m "not db"` เสมอ — กฎที่บังคับด้วย
Python ไม่ใช่ด้วย SQL ต้องพิสูจน์ได้บนเครื่องที่ไม่มี Postgres (ธรรมเนียมเดียวกับ
`test_decision_record.py`) ทั้งสองเกณฑ์จึงถูกยิงที่ตัวฟังก์ชัน (`closed_as_of`,
`bars_needed`, `fetch_forward`) ส่วนการต่อสายเข้ากับตาราง `bars` อยู่ใน
`test_ohlcv_db.py` ซึ่งยืม `rows()` กับ `FakeClient` ของไฟล์นี้ไปใช้
"""

from __future__ import annotations

import pytest

from cane.data import (
    DEFAULT_LIMIT,
    bars_needed,
    closed_as_of,
    fetch_forward,
    timeframe_ms,
    to_bars,
)

DAY = 86_400_000
HOUR = 3_600_000

SYMBOL = "BTC/USDT"
PERP = "usdtm_perp"
SPOT = "spot"


def rows(n: int, *, span: int = DAY, first_open: int = 1_600_000_000_000):
    """แถวดิบรูปเดียวกับที่ ccxt คืน — `[open_ts, o, h, l, c, v]`

    ค่าต้องสอดคล้องกับ CHECK ของตาราง `bars` (high สูงสุด · low ต่ำสุด · volume ≥ 0)
    เพราะปลายทางเป็นตารางแล้ว ไม่ใช่ไฟล์ JSON ที่รับอะไรก็ได้
    """
    return [
        [first_open + i * span, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0 + i]
        for i in range(n)
    ]


class FakeClient:
    """เลียนแบบ ccxt เท่าที่ชั้นข้อมูลใช้

    `startTime` ของ Binance **รวมปลายซ้าย** (ยืนยันกับ endpoint จริงแล้ว) ตัวปลอม
    จึงต้องรวมปลายซ้ายด้วย ไม่งั้นเทสต์การดึงต่อจากประวัติที่มีอยู่จะผ่านทั้งที่ของจริงพัง
    """

    def __init__(self, table=None):
        self._table = list(table or [])
        self.calls: list[tuple] = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        out = self._table
        if since is not None:
            out = [r for r in out if r[0] >= since]
        if limit is not None:
            out = out[:limit]
        return list(out)

    def fetch_funding_rate(self, symbol):  # pragma: no cover - ไม่ใช้ในไฟล์นี้
        raise AssertionError("ไม่ควรถูกเรียกจากเส้นทาง OHLCV")


# ── เกณฑ์ข้อ 1: แท่งล่าสุดปิดแล้วเสมอ ──────────────────────────────────────


def test_latest_bar_is_always_already_closed():
    """กวาด as_of ทุกตำแหน่งรวมขอบเขตพอดี — ข้อรับประกันต้องไม่พลาดแม้จุดเดียว"""
    history = to_bars(rows(40), "1d")

    for bar in history:
        for now in (bar.close_ts - 1, bar.close_ts, bar.close_ts + 1):
            got = closed_as_of(history, now)
            assert all(b.close_ts < now for b in got)
            if got:
                assert got[-1].close_ts < now


def test_bar_closing_exactly_now_is_not_returned_yet():
    """`<` ไม่ใช่ `<=` — ช้าไปหนึ่งแท่งปลอดภัยกว่าเร็วไปหนึ่งแท่ง"""
    history = to_bars(rows(3), "1d")
    boundary = history[-1].close_ts

    got = closed_as_of(history, boundary)

    assert [b.open_ts for b in got] == [b.open_ts for b in history[:-1]]


@pytest.mark.parametrize(("timeframe", "span"), [("1d", DAY), ("1h", HOUR)])
def test_drops_the_running_bar_on_every_timeframe(timeframe, span):
    """endpoint จริงคืนแท่งที่ยังวิ่งมาเป็นแถวสุดท้ายทั้ง 1d และ 1h (ยืนยันแล้ว)"""
    history = to_bars(rows(5, span=span), timeframe)
    now = history[-1].open_ts + span // 2

    got = closed_as_of(history, now)

    assert len(got) == 4
    assert got[-1].open_ts == history[-2].open_ts


def test_close_ts_is_open_plus_timeframe():
    got = to_bars(rows(2), "1d")
    assert [b.close_ts - b.open_ts for b in got] == [DAY, DAY]


# ── เกณฑ์ข้อ 2: ต่ำกว่า 85 แท่งถูกข้าม ─────────────────────────────────────


def test_fewer_than_85_closed_bars_is_skipped():
    history = to_bars(rows(60), "1d")
    got = closed_as_of(history, history[-1].open_ts + DAY * 2)

    assert len(got) == 60
    assert bars_needed(got) == 25


def test_85_raw_rows_with_one_still_running_is_still_skipped():
    """เคสคมสุด: ดิบครบ 85 แต่แท่งท้ายยังวิ่ง → ปิดจริง 84 → ยังไม่ตัดสินใจ"""
    history = to_bars(rows(85), "1d")

    got = closed_as_of(history, history[-1].open_ts + DAY // 2)

    assert len(got) == 84
    assert bars_needed(got) == 1


def test_85_closed_bars_is_ready():
    history = to_bars(rows(86), "1d")

    got = closed_as_of(history, history[-1].open_ts + DAY // 2)

    assert len(got) == 85
    assert bars_needed(got) == 0


# ── สัญญาเล็กๆ ที่พลาดแล้วข้อมูลผิดเงียบ ───────────────────────────────────


def test_unsupported_timeframe_is_refused_loudly():
    with pytest.raises(ValueError, match="4h"):
        timeframe_ms("4h")


def test_a_perp_pair_is_asked_for_with_the_quote_suffix():
    """`BTC/USDT` เฉยๆ ได้แท่ง spot มา — เทสต์ทุกตัวยังเขียวแต่ข้อมูลผิด"""
    client = FakeClient(rows(3))

    fetch_forward(client, PERP, SYMBOL, "1d", None, DEFAULT_LIMIT)

    assert client.calls[0][0] == "BTC/USDT:USDT"


def test_a_spot_pair_is_asked_for_with_the_config_spelling():
    """ตลาดเดียวกันชื่อคนละรูป — ถ้าต่อ `:USDT` ให้ spot ด้วย venue จะไม่รู้จักชื่อนั้น"""
    client = FakeClient(rows(3))

    fetch_forward(client, SPOT, SYMBOL, "1d", None, DEFAULT_LIMIT)

    assert client.calls[0][0] == "BTC/USDT"


# ── การไล่หน้า: เส้นทางที่ตัดสินได้โดยไม่ต้องมีปลายทางที่เก็บ ────────────────


def test_no_since_asks_for_one_page_of_the_most_recent_bars():
    """ยังไม่มีแท่งในตาราง ไม่ต้องไล่หน้า — endpoint คืนแท่งล่าสุดให้อยู่แล้ว"""
    client = FakeClient(rows(700))

    fetch_forward(client, PERP, SYMBOL, "1d", None, DEFAULT_LIMIT)

    assert len(client.calls) == 1
    assert client.calls[0][2] is None
    assert client.calls[0][3] == DEFAULT_LIMIT


def test_walks_pages_until_a_short_page_ends_the_walk():
    """หน้าเดียวไม่พอเมื่อประวัติค้างเกินหนึ่งหน้า — ข้อมูลเก่าที่หน้าตาเหมือนข้อมูลสด"""
    table = rows(700)
    client = FakeClient(table)

    got = fetch_forward(client, PERP, SYMBOL, "1d", table[9][0], 50)

    assert len(client.calls) > 1, "ต้องขอมากกว่าหนึ่งหน้า"
    assert got[-1][0] == table[-1][0], "ต้องตามให้ทันถึงแท่งล่าสุด"


def test_pagination_stops_when_the_cursor_stops_moving():
    """venue ที่คืนหน้าเต็มหน้าเดิมซ้ำๆ ต้องไม่ทำให้ลูปไม่จบ"""

    class StuckClient(FakeClient):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.calls.append((symbol, timeframe, since, limit))
            return [list(r) for r in self._table[:limit]]

    table = rows(20)
    stuck = StuckClient(table)

    fetch_forward(stuck, PERP, SYMBOL, "1d", table[0][0], 3)

    assert len(stuck.calls) < 10, "ต้องออกจากลูปเมื่อ cursor ไม่ขยับ"


def test_a_venue_that_returns_nothing_ends_the_walk():
    """หน้าว่างคือ "หมดแล้ว" ไม่ใช่เหตุให้ขอต่อไปเรื่อยๆ"""
    client = FakeClient([])

    got = fetch_forward(client, PERP, SYMBOL, "1d", 1_600_000_000_000, 50)

    assert got == []
    assert len(client.calls) == 1
