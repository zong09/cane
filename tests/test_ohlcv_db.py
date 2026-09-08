"""ชั้นข้อมูลกับตาราง `bars` — ปลายทางที่แทน `data/cache.py` (ADR 22, ใบ 02b)

ต่างจาก `test_ohlcv.py` ที่ยิงตรรกะบริสุทธิ์: ไฟล์นี้ยิง **การต่อสาย** ระหว่าง
`LiveBarSource` / `ReplayBarSource` กับ repo — สิ่งที่พิสูจน์ได้เฉพาะตอนมีตารางจริง

ยืม `rows()` กับ `FakeClient` มาจาก `test_ohlcv.py` ไม่ทำสำเนา เพราะรายละเอียดที่
รับน้ำหนักไว้คือ "`startTime` ของ Binance รวมปลายซ้าย" — สองสำเนาจะเพี้ยนออกจากกัน
แล้วเทสต์ฝั่งหนึ่งจะผ่านบนสมมติฐานที่ของจริงไม่เป็นอย่างนั้น

**สิ่งที่ต่างจากยุค cache อย่างถอนคืนไม่ได้:** ไฟล์ JSON ลบแล้วสร้างใหม่ได้ แต่แถวใน
`bars` ลบไม่ได้ — `cane_engine` ไม่มี `UPDATE`/`DELETE` และ `insert_bars` เป็น
`ON CONFLICT DO NOTHING` แท่งที่ยังวิ่งซึ่งหลุดลงไปจะเป็นข้อมูลผิดถาวร
"""

from __future__ import annotations

import pytest

from cane.data import LiveBarSource, ReplayBarSource
from cane.db import closed_bars
from test_ohlcv import DAY, PERP, SPOT, SYMBOL, FakeClient, rows

pytestmark = pytest.mark.db

FAR_FUTURE = 10**14


def live(conn, client, *, now, market=PERP, limit=None):
    kwargs = {} if limit is None else {"limit": limit}
    return LiveBarSource(conn, client, market, clock=lambda: now, **kwargs)


def stored_opens(conn, *, market=PERP, timeframe="1d"):
    return [
        bar.open_ts
        for bar in closed_bars(conn, market, SYMBOL, timeframe, as_of=FAR_FUTURE)
    ]


# ── ด่านเดียวที่กันข้อมูลผิดถาวร ─────────────────────────────────────────────


def test_a_still_open_bar_never_reaches_the_table(db):
    """แท่งที่ยังวิ่งลงตาราง = repainting ที่เดินเข้ามาทางประตูหลัง แล้วลบออกไม่ได้

    อ่านกลับด้วย `as_of` ในอนาคตไกล เพื่อพิสูจน์ว่ามันไม่ได้ถูก **เขียน** ลงไป
    ไม่ใช่แค่ถูกกรองออกตอนอ่าน
    """
    table = rows(5)
    now = table[-1][0] + DAY // 2

    live(db, FakeClient(table), now=now).bars(SYMBOL, "1d")

    mine = {r[0] for r in table}
    assert set(stored_opens(db)) & mine == {r[0] for r in table[:-1]}


def test_the_data_layer_never_commits(db, db_engine):
    """ผู้เรียกเป็นเจ้าของทรานแซกชัน — commit ที่หลงอยู่ในชั้นข้อมูลจะยังทำให้เทสต์เขียว

    ถ้า `_load` commit เอง ข้อมูลของเทสต์จะอยู่ยืนกว่าตัวเทสต์ทั้งที่ fixture `db`
    rollback ทิ้งเสมอ · connection ตัวที่สองจึงต้องมองไม่เห็นอะไรเลย
    """
    table = rows(5)
    live(db, FakeClient(table), now=table[-1][0] + DAY * 2).bars(SYMBOL, "1d")
    mine = {r[0] for r in table}
    assert mine <= set(stored_opens(db)), "ต้องเขียนลง connection ของตัวเองแล้วจริง"

    with db_engine.connect() as outsider:
        assert mine.isdisjoint(stored_opens(outsider))


# ── ดึงต่อจากประวัติที่มีอยู่ ─────────────────────────────────────────────────


def test_incremental_fetch_refetches_the_last_bar_without_duplicating(db):
    table = rows(10)
    wall = table[-1][0] + DAY * 2

    live(db, FakeClient(table[:6]), now=wall).bars(SYMBOL, "1d")
    second = FakeClient(table)
    got = live(db, second, now=wall).bars(SYMBOL, "1d")

    assert [b.open_ts for b in got] == [r[0] for r in table]
    assert stored_opens(db) == [r[0] for r in table]
    assert second.calls[0][2] == table[5][0], "since ต้องเป็นแท่งสุดท้ายที่ตารางมี"


def test_catches_up_when_the_table_is_staler_than_one_page(db):
    """ประวัติค้างเกินหนึ่งหน้า — ขอหน้าเดียวจะได้ข้อมูลเก่าที่หน้าตาเหมือนข้อมูลสด

    เคสนี้อันตรายกว่าที่เห็น: แท่งที่ได้มา **ปิดแล้วจริงทุกแท่ง** ตัวกรอง `as_of` จึงไม่
    เห็นอะไรผิดเลย ถ้าไม่ไล่หน้าต่อ engine จะตัดสินใจบนแท่งของสามสัปดาห์ก่อน
    """
    limit = 50
    table = rows(700)
    wall = table[-1][0] + DAY * 2

    live(db, FakeClient(table[:10]), now=wall, limit=limit).bars(SYMBOL, "1d")

    catching_up = FakeClient(table)
    got = live(db, catching_up, now=wall, limit=limit).bars(SYMBOL, "1d")

    assert [b.open_ts for b in got] == [r[0] for r in table], "ต้องตามให้ทันถึงแท่งล่าสุด"
    assert len(catching_up.calls) > 1, "ต้องขอมากกว่าหนึ่งหน้า"


def test_the_stored_bar_wins_over_a_refetched_value(db):
    """`ON CONFLICT DO NOTHING` เก็บแถวเดิมไว้ ค่าที่คืนขึ้นไปจึงต้องเป็นแถวเดิมด้วย

    แท่งที่ทับกันหนึ่งแท่งเกิดทุกรอบโดยเจตนา (`since` = แท่งสุดท้ายที่มี) ถ้า venue
    ส่งค่าที่ต่างออกมาแล้วเราคืนค่าใหม่ขึ้นไป indicator จะคำนวณบนตัวเลขที่ตารางไม่ได้
    ถืออยู่ — ความต่างที่ไม่มีใครตามรอยได้ภายหลัง
    """
    table = rows(6)
    wall = table[-1][0] + DAY * 2
    live(db, FakeClient(table), now=wall).bars(SYMBOL, "1d")

    tampered = [list(r) for r in table]
    # **แท่งสุดท้าย** คือแท่งเดียวที่ถูกดึงทับ (`since` = แท่งสุดท้ายที่ตารางมี)
    # ถ้าไปแก้แท่งอื่นเทสต์จะเขียวโดยไม่ได้วัดอะไรเลย เพราะแท่งนั้นไม่ถูกดึงซ้ำ
    # และ close ใหม่ต้องยังอยู่ในกรอบ high/low ไม่งั้น CHECK ของตารางจะล้มก่อนที่
    # `ON CONFLICT DO NOTHING` จะได้ทำงาน — คนละเรื่องกับที่เทสต์นี้จะวัด
    tampered[-1][4] = tampered[-1][3]
    assert tampered[-1][4] != table[-1][4]

    second = FakeClient(tampered)
    got = live(db, second, now=wall).bars(SYMBOL, "1d")

    assert second.calls[0][2] == table[-1][0], "ต้องดึงทับแท่งสุดท้ายจริง"
    assert got[-1].close == table[-1][4]
    by_open = {
        bar.open_ts: bar
        for bar in closed_bars(db, PERP, SYMBOL, "1d", as_of=FAR_FUTURE)
    }
    assert by_open[table[-1][0]].close == table[-1][4]


# ── replay เดินบนโค้ดเส้นเดียวกับ live ──────────────────────────────────────


def test_replay_reads_a_warm_table_at_a_past_as_of(db):
    """ตารางล้ำหน้า as_of ของ replay ได้ — การกรองจึงต้องเกิดตอนอ่าน ไม่ใช่ตอนเก็บ"""
    table = rows(100)
    wall = table[-1][0] + DAY * 2
    live(db, FakeClient(table), now=wall).bars(SYMBOL, "1d")
    assert len(stored_opens(db)) == 100

    replay = ReplayBarSource(
        db, FakeClient([]), PERP, as_of=table[50][0] + DAY, clock=lambda: wall
    )

    assert len(replay.bars(SYMBOL, "1d")) == 50


def test_live_and_replay_agree_at_the_same_instant(db):
    """ข้อรับประกันของ "โค้ดเส้นเดียวกัน" — ต่างกันได้แค่ที่ as_of มาจากไหน"""
    table = rows(30)
    now = table[-1][0] + DAY // 2

    from_live = live(db, FakeClient(table), now=now).bars(SYMBOL, "1d")
    from_replay = ReplayBarSource(
        db, FakeClient(table), PERP, as_of=now, clock=lambda: now
    ).bars(SYMBOL, "1d")

    assert from_live == from_replay


def test_replay_fetches_once_and_serves_every_step_from_memory(db):
    """replay หนึ่งปีต้องไม่ยิง request ต่อหนึ่งก้าว"""
    table = rows(100)
    client = FakeClient(table)
    wall = table[-1][0] + DAY * 2
    replay = ReplayBarSource(db, client, PERP, as_of=table[0][0], clock=lambda: wall)

    seen = []
    for row in table:
        replay.as_of = row[0] + DAY
        seen.append(len(replay.bars(SYMBOL, "1d")))

    assert len(client.calls) == 1
    assert seen == list(range(100))


# ── สองตลาดของเหรียญเดียว ────────────────────────────────────────────────────


def test_spot_and_perp_bars_of_one_pair_do_not_mix(db):
    """`store_symbol()` ตัด `:USDT` ทิ้ง — ถ้า market ไม่เข้า PK สองตลาดจะทับกันเงียบๆ

    นี่คือบั๊กข้อมูลที่ ADR 26 มาปิด และเป็นเหตุผลที่ `market` ผูกกับ source ตั้งแต่
    ตอนสร้าง ไม่ใช่ส่งเข้ามาต่อการเรียก
    """
    perp_table = rows(5)
    spot_table = [list(r) for r in perp_table]
    for row in spot_table:
        row[4] = row[4] + 500.0  # spot ซื้อขายคนละราคากับ perp
        row[2] = row[2] + 500.0  # high ต้องยังสูงสุด (CHECK ของตาราง)
    wall = perp_table[-1][0] + DAY * 2

    live(db, FakeClient(perp_table), now=wall).bars(SYMBOL, "1d")
    live(db, FakeClient(spot_table), now=wall, market=SPOT).bars(SYMBOL, "1d")

    from_perp = closed_bars(db, PERP, SYMBOL, "1d", as_of=FAR_FUTURE)
    from_spot = closed_bars(db, SPOT, SYMBOL, "1d", as_of=FAR_FUTURE)

    assert [b.open_ts for b in from_perp] == [b.open_ts for b in from_spot]
    assert [b.close for b in from_perp] == [r[4] for r in perp_table]
    assert [b.close for b in from_spot] == [r[4] for r in spot_table]
