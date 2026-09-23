"""ไดรเวอร์ replay (`engine/replay.py`) — รันจริงบน scratch database ที่สร้างขึ้นเองในเทสต์ (ADR 29)

ไม่ใช้ fixture `db` (ทรานแซกชันที่ rollback) เพราะสิ่งที่พิสูจน์คือ **ไดรเวอร์ commit ต่อ `as_of` จริง**: verdict cache
ต้องรอดแม้แท่งนั้นล้ม, cursor ต้องกันรันซ้ำข้ามทรานแซกชัน — ทั้งคู่มองไม่เห็นจากในทรานแซกชันเดียว · จึงสร้าง database
ชื่อ `cane_replay_pytest` ทั้งใบ migrate seed นำเข้าแท่ง แล้ว drop ทิ้งตอนจบ

ช้ากว่าเทสต์อื่น (migrate ทั้งชุด) จึงติด `slow` — `pytest -m "not slow"` ข้ามได้
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from cane.config import load_profile
from cane.data import read_tradingview_csv
from cane.db.engine import dsn_from_env, make_engine
from cane.db.repo import config as config_repo
from cane.db.repo import replay_cursor
from cane.db.repo.bars import insert_bars
from cane.db.schema import decisions, verdict_cache
from cane.engine.replay import NoJudge, ReplayError, run_replay

pytestmark = [pytest.mark.db, pytest.mark.slow]

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "action_zone" / "BINANCE_BTCUSDT.P, 1D.csv"
SCRATCH = "cane_replay_pytest"
DAY_MS = 86_400_000

#: ช่วงที่ replay: แท่งที่ปิดวันที่ดัชนี 200–259 ของไฟล์ (60 แท่ง) — ครอบสัญญาณ Buy 213, Sell 220, Buy 221
#: ซึ่งเป็น flip สองครั้งติดกัน ทำให้มีทั้ง Judge ที่ถูกเรียกและการปิดสองขา
FIRST, LAST = 200, 259


@pytest.fixture(scope="module")
def scratch_dsn():
    """สร้าง database ใหม่ทั้งใบ migrate แล้ว seed config + แท่ง · drop ทิ้งตอนจบโมดูล"""
    admin_dsn = dsn_from_env()
    url = make_url(admin_dsn)
    dsn = url.set(database=SCRATCH).render_as_string(hide_password=False)

    admin = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{SCRATCH}"'))

    # `alembic/env.py` อ่าน DSN จาก environment · ตั้งชั่วคราวให้ชี้ scratch แล้วคืนค่า
    import os

    previous = os.environ.get("CANE_DB_DSN")
    os.environ["CANE_DB_DSN"] = dsn
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(REPO / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        os.environ["CANE_DB_DSN"] = previous

    owner = make_engine(dsn)
    bars = read_tradingview_csv(FIXTURE, "1d")
    settings = load_profile(str(REPO / "config" / "replay.toml"))
    with owner.begin() as conn:
        insert_bars(conn, "usdtm_perp", "BTC/USDT", "1d", bars)
        version = config_repo.insert_version(conn, settings, source="migration", created_ts=1)
        config_repo.activate(conn, version.id)
    owner.dispose()

    yield dsn

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture(scope="module")
def bars_all():
    return read_tradingview_csv(FIXTURE, "1d")


class ScriptedJudge:
    """ตอบว่ามีทุก factor · นับครั้งที่ถูกเรียก — พิสูจน์ว่า cache ทำงาน (แท่งเดียวกันไม่ถูกถามซ้ำ)"""

    model_id = "scripted|test"

    def __init__(self):
        self.calls = 0

    def ask(self, *, system, user, schema, factor, side, bar_indices):
        self.calls += 1
        return {"factor": factor, "side": side, "present": True, "confidence": 0.8,
                "evidence_bars": [bar_indices[-1]], "rationale": "scripted"}


def _run(dsn, bars_all, judge, model_id):
    engine = make_engine(dsn, role="engine")
    try:
        return run_replay(
            db=engine,
            profile="paper",
            from_close_ts=bars_all[FIRST].close_ts,
            to_close_ts=bars_all[LAST].close_ts,
            judge=judge,
            model_id=model_id,
        )
    finally:
        engine.dispose()


def test_a_replay_writes_exactly_one_row_per_bar_and_commits_the_verdict_cache(scratch_dsn, bars_all):
    judge = ScriptedJudge()

    summary = _run(scratch_dsn, bars_all, judge, judge.model_id)

    assert summary.bars == LAST - FIRST + 1 == 60
    assert summary.decisions == 60
    engine = make_engine(scratch_dsn)
    with engine.connect() as conn:
        rows = conn.execute(
            select(decisions.c.bar_close_ts, decisions.c.decided_ts, decisions.c.utc_day, decisions.c.long_signal,
                   decisions.c.short_signal, decisions.c.skip_reason)
            .order_by(decisions.c.bar_close_ts)
        ).all()
        cached = conn.execute(select(func.count()).select_from(verdict_cache)).scalar_one()
        cursor = replay_cursor.read(conn, "paper")
    engine.dispose()

    # หนึ่งแถวต่อแท่ง ไม่ขาด ไม่ซ้ำ ตรงกับแท่งของ fixture
    assert [r.bar_close_ts for r in rows] == [bars_all[i].close_ts for i in range(FIRST, LAST + 1)]
    # เวลาตัดสินคือเวลาที่แท่งปิด (+1ms) ไม่ใช่นาฬิกาของวันที่รัน · utc_day มาจากแท่ง
    assert all(r.decided_ts == r.bar_close_ts + 1 for r in rows)
    assert all(r.utc_day == r.bar_close_ts // DAY_MS for r in rows)
    # สัญญาณตรง golden ของ TradingView ในช่วงเดียวกัน: Buy 213, Sell 220, Buy 221
    signals = [(r.bar_close_ts, "B" if r.long_signal else "S") for r in rows if r.long_signal or r.short_signal]
    assert signals == [(bars_all[213].close_ts, "B"), (bars_all[220].close_ts, "S"), (bars_all[221].close_ts, "B")]
    # verdict cache ถูก commit (connection ที่สอง) ไม่ใช่เขียนแล้วหายไปกับทรานแซกชันที่ไม่เคยถูก commit
    assert cached > 0 and judge.calls == cached
    # cursor ไปถึงปลายทางพอดี — `end_ts` ต้องเท่า as_of สุดท้าย ไม่งั้นแท่งสุดท้ายถูกฐานปฏิเสธ
    assert cursor.as_of_ms == cursor.end_ts == bars_all[LAST].close_ts + 1


def test_running_again_in_the_same_scratch_database_is_refused(scratch_dsn, bars_all):
    """`insert_decision` ไม่มี ON CONFLICT — รันซ้ำแล้วทุกแท่งจะมีสองแถวโดยไม่มีอะไรฟ้อง (ADR 27 §27.1)"""
    with pytest.raises(ReplayError, match="เคยรัน replay"):
        _run(scratch_dsn, bars_all, NoJudge(), NoJudge.model_id)

    engine = make_engine(scratch_dsn)
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(decisions)).scalar_one() == 60
    engine.dispose()


def test_replay_refuses_a_database_that_is_not_a_scratch(bars_all):
    """ที่ห้ามพลาดที่สุดของไฟล์นี้ — replay บน database จริงทิ้งการตัดสินใจปลอมไว้ในตารางที่ลบไม่ได้"""
    engine = make_engine(role="engine")  # DSN จาก environment = database ปกติของ dev
    try:
        with pytest.raises(ReplayError, match="ไม่ใช่ scratch"):
            run_replay(db=engine, profile="paper", from_close_ts=1, to_close_ts=2,
                       judge=NoJudge(), model_id=NoJudge.model_id)
    finally:
        engine.dispose()
