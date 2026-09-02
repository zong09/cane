"""OHLCV — แท่งที่คืนมาต้องปิดแล้วเสมอ และคู่ที่ข้อมูลไม่ถึง 85 แท่งต้องถูกข้าม

เกณฑ์ของใบ #02 มีสองข้อ เทสต์ในไฟล์นี้ยึดสองข้อนั้นเป็นหลัก:
  1. แท่งล่าสุดที่คืนมามี close timestamp < now **เสมอ**
  2. คู่ที่มีข้อมูลน้อยกว่า 85 แท่งถูกข้าม
"""

from __future__ import annotations

import pytest

from cane.data import (
    DEFAULT_LIMIT,
    Bar,
    BarCache,
    LiveBarSource,
    ReplayBarSource,
    bars_needed,
    timeframe_ms,
)

DAY = 86_400_000
HOUR = 3_600_000

SYMBOL = "BTC/USDT"


def rows(n: int, *, span: int = DAY, first_open: int = 1_600_000_000_000):
    """แถวดิบรูปเดียวกับที่ ccxt คืน — `[open_ts, o, h, l, c, v]`"""
    return [
        [first_open + i * span, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0 + i]
        for i in range(n)
    ]


class FakeClient:
    """เลียนแบบ ccxt เท่าที่ชั้นข้อมูลใช้

    `startTime` ของ Binance **รวมปลายซ้าย** (ยืนยันกับ endpoint จริงแล้ว) ตัวปลอม
    จึงต้องรวมปลายซ้ายด้วย ไม่งั้นเทสต์การดึงต่อจาก cache จะผ่านทั้งที่ของจริงพัง
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


def live(client, *, now, cache=None, limit=DEFAULT_LIMIT):
    return LiveBarSource(client, cache=cache, clock=lambda: now, limit=limit)


# ── เกณฑ์ข้อ 1: แท่งล่าสุดปิดแล้วเสมอ ──────────────────────────────────────


def test_latest_bar_is_always_already_closed():
    """กวาด as_of ทุกตำแหน่งรวมขอบเขตพอดี — ข้อรับประกันต้องไม่พลาดแม้จุดเดียว"""
    table = rows(40)
    client = FakeClient(table)

    for row in table:
        close = row[0] + DAY
        for now in (close - 1, close, close + 1):
            got = live(client, now=now).bars(SYMBOL, "1d")
            assert all(bar.close_ts < now for bar in got)
            if got:
                assert got[-1].close_ts < now


def test_bar_closing_exactly_now_is_not_returned_yet():
    """`<` ไม่ใช่ `<=` — ช้าไปหนึ่งแท่งปลอดภัยกว่าเร็วไปหนึ่งแท่ง"""
    table = rows(3)
    boundary = table[-1][0] + DAY

    got = live(FakeClient(table), now=boundary).bars(SYMBOL, "1d")

    assert [b.open_ts for b in got] == [r[0] for r in table[:-1]]


@pytest.mark.parametrize(("timeframe", "span"), [("1d", DAY), ("1h", HOUR)])
def test_drops_the_running_bar_on_every_timeframe(timeframe, span):
    """endpoint จริงคืนแท่งที่ยังวิ่งมาเป็นแถวสุดท้ายทั้ง 1d และ 1h (ยืนยันแล้ว)"""
    table = rows(5, span=span)
    now = table[-1][0] + span // 2

    got = live(FakeClient(table), now=now).bars(SYMBOL, timeframe)

    assert len(got) == 4
    assert got[-1].open_ts == table[-2][0]


def test_close_ts_is_open_plus_timeframe():
    table = rows(2)
    got = live(FakeClient(table), now=table[-1][0] + DAY * 5).bars(SYMBOL, "1d")
    assert [b.close_ts - b.open_ts for b in got] == [DAY, DAY]


# ── เกณฑ์ข้อ 2: ต่ำกว่า 85 แท่งถูกข้าม ─────────────────────────────────────


def test_fewer_than_85_closed_bars_is_skipped():
    table = rows(60)
    got = live(FakeClient(table), now=table[-1][0] + DAY * 2).bars(SYMBOL, "1d")

    assert len(got) == 60
    assert bars_needed(got) == 25


def test_85_raw_rows_with_one_still_running_is_still_skipped():
    """เคสคมสุด: ดิบครบ 85 แต่แท่งท้ายยังวิ่ง → ปิดจริง 84 → ยังไม่ตัดสินใจ"""
    table = rows(85)
    now = table[-1][0] + DAY // 2

    got = live(FakeClient(table), now=now).bars(SYMBOL, "1d")

    assert len(got) == 84
    assert bars_needed(got) == 1


def test_85_closed_bars_is_ready():
    table = rows(86)
    now = table[-1][0] + DAY // 2

    got = live(FakeClient(table), now=now).bars(SYMBOL, "1d")

    assert len(got) == 85
    assert bars_needed(got) == 0


# ── cache ───────────────────────────────────────────────────────────────────


def test_cache_never_stores_a_running_bar(tmp_path):
    """แท่งที่ยังวิ่งลง cache = repainting ที่เดินเข้ามาทางประตูหลัง"""
    table = rows(5)
    now = table[-1][0] + DAY // 2
    cache = BarCache(tmp_path)

    live(FakeClient(table), now=now, cache=cache).bars(SYMBOL, "1d")

    assert [b.open_ts for b in cache.load(SYMBOL, "1d")] == [r[0] for r in table[:-1]]


def test_incremental_fetch_refetches_the_last_bar_without_duplicating(tmp_path):
    table = rows(10)
    wall = table[-1][0] + DAY * 2
    cache = BarCache(tmp_path)

    live(FakeClient(table[:6]), now=wall, cache=cache).bars(SYMBOL, "1d")
    second = FakeClient(table)
    got = live(second, now=wall, cache=cache).bars(SYMBOL, "1d")

    assert [b.open_ts for b in got] == [r[0] for r in table]
    assert second.calls[0][2] == table[5][0], "since ต้องเป็นแท่งสุดท้ายที่ cache มี"


def test_corrupt_cache_is_treated_as_empty(tmp_path):
    cache = BarCache(tmp_path)
    cache.save(SYMBOL, "1d", [])
    for path in tmp_path.iterdir():
        path.write_text("{ไม่ใช่ json", encoding="utf-8")

    assert cache.load(SYMBOL, "1d") == []


def test_cache_survives_a_symbol_containing_a_slash(tmp_path):
    cache = BarCache(tmp_path)
    bar = Bar(1, 1 + DAY, 1.0, 2.0, 0.5, 1.5, 9.0)
    cache.save("BTC/USDT", "1d", [bar])

    assert cache.load("BTC/USDT", "1d") == [bar]


# ── replay เดินบนโค้ดเส้นเดียวกับ live ──────────────────────────────────────


def test_replay_reads_a_warm_cache_at_a_past_as_of(tmp_path):
    """cache ล้ำหน้า as_of ของ replay ได้ — การกรองจึงต้องเกิดตอนอ่าน ไม่ใช่ตอนเก็บ"""
    table = rows(100)
    wall = table[-1][0] + DAY * 2
    cache = BarCache(tmp_path)
    live(FakeClient(table), now=wall, cache=cache).bars(SYMBOL, "1d")
    assert len(cache.load(SYMBOL, "1d")) == 100

    replay = ReplayBarSource(
        FakeClient([]), as_of=table[50][0] + DAY, cache=cache, clock=lambda: wall
    )

    assert len(replay.bars(SYMBOL, "1d")) == 50


def test_live_and_replay_agree_at_the_same_instant():
    """ข้อรับประกันของ "โค้ดเส้นเดียวกัน" — ต่างกันได้แค่ที่ as_of มาจากไหน"""
    table = rows(30)
    now = table[-1][0] + DAY // 2

    from_live = live(FakeClient(table), now=now).bars(SYMBOL, "1d")
    from_replay = ReplayBarSource(
        FakeClient(table), as_of=now, clock=lambda: now
    ).bars(SYMBOL, "1d")

    assert from_live == from_replay


def test_replay_fetches_once_and_serves_every_step_from_memory():
    """replay หนึ่งปีต้องไม่ยิง request ต่อหนึ่งก้าว"""
    table = rows(100)
    client = FakeClient(table)
    wall = table[-1][0] + DAY * 2
    replay = ReplayBarSource(client, as_of=table[0][0], clock=lambda: wall)

    seen = []
    for row in table:
        replay.as_of = row[0] + DAY
        seen.append(len(replay.bars(SYMBOL, "1d")))

    assert len(client.calls) == 1
    assert seen == list(range(100))


# ── สัญญาเล็กๆ ที่พลาดแล้วข้อมูลผิดเงียบ ───────────────────────────────────


def test_client_is_asked_for_the_perp_not_the_spot_symbol():
    """`BTC/USDT` เฉยๆ ได้แท่ง spot มา — เทสต์ทุกตัวยังเขียวแต่ข้อมูลผิด"""
    client = FakeClient(rows(3))
    live(client, now=10**14).bars(SYMBOL, "1d")

    assert client.calls[0][0] == "BTC/USDT:USDT"


def test_unsupported_timeframe_is_refused_loudly():
    with pytest.raises(ValueError, match="4h"):
        timeframe_ms("4h")


def test_catches_up_when_the_cache_is_staler_than_one_page(tmp_path):
    """cache ค้างเกินหนึ่งหน้า — ขอหน้าเดียวจะได้ข้อมูลเก่าที่หน้าตาเหมือนข้อมูลสด

    เคสนี้อันตรายกว่าที่เห็น: แท่งที่ได้มา **ปิดแล้วจริงทุกแท่ง** ตัวกรอง `as_of` จึงไม่
    เห็นอะไรผิดเลย ถ้าไม่ไล่หน้าต่อ engine จะตัดสินใจบนแท่งของสามสัปดาห์ก่อน
    """
    limit = 50
    table = rows(700)
    wall = table[-1][0] + DAY * 2
    cache = BarCache(tmp_path)

    live(FakeClient(table[:10]), now=wall, cache=cache, limit=limit).bars(SYMBOL, "1d")

    catching_up = FakeClient(table)
    got = live(catching_up, now=wall, cache=cache, limit=limit).bars(SYMBOL, "1d")

    assert [b.open_ts for b in got] == [r[0] for r in table], "ต้องตามให้ทันถึงแท่งล่าสุด"
    assert len(catching_up.calls) > 1, "ต้องขอมากกว่าหนึ่งหน้า"


def test_an_empty_cache_asks_for_one_page_of_the_most_recent_bars():
    """cache ว่างไม่ต้องไล่หน้า — endpoint คืนแท่งล่าสุดให้อยู่แล้ว"""
    client = FakeClient(rows(700))

    live(client, now=10**14).bars(SYMBOL, "1d")

    assert len(client.calls) == 1
    assert client.calls[0][2] is None
    assert client.calls[0][3] == DEFAULT_LIMIT


def test_pagination_stops_when_the_cursor_stops_moving(tmp_path):
    """venue ที่คืนหน้าเต็มหน้าเดิมซ้ำๆ ต้องไม่ทำให้ลูปไม่จบ"""

    class StuckClient(FakeClient):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.calls.append((symbol, timeframe, since, limit))
            return [list(r) for r in self._table[:limit]]

    table = rows(20)
    cache = BarCache(tmp_path)
    cache.save(SYMBOL, "1d", [])
    live(FakeClient(table[:3]), now=table[-1][0] + DAY * 2, cache=cache, limit=3).bars(
        SYMBOL, "1d"
    )

    stuck = StuckClient(table)
    live(stuck, now=table[-1][0] + DAY * 2, cache=cache, limit=3).bars(SYMBOL, "1d")

    assert len(stuck.calls) < 10, "ต้องออกจากลูปเมื่อ cursor ไม่ขยับ"


def test_cache_holding_valid_json_of_the_wrong_shape_reads_as_empty(tmp_path):
    cache = BarCache(tmp_path)
    cache.save(SYMBOL, "1d", [])
    for path in tmp_path.iterdir():
        path.write_text("[1, 2, 3]", encoding="utf-8")

    assert cache.load(SYMBOL, "1d") == []
