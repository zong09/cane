"""ไปป์ไลน์ต่อแท่ง (`engine/pipeline.py`) — หนึ่งเทสต์ต่อหนึ่งเส้นทางของ spec/08 §สิบสี่ขั้นของหนึ่งรอบ

ใช้ **แท่งจริง** จาก golden fixture ของ TradingView (`BINANCE_BTCUSDT.P, 1D.csv`) ไม่ใช่แท่งที่ประกอบขึ้นเอง:
สัญญาณ Buy/Sell ที่ใช้ตัดสินมาจากสูตรที่พิสูจน์แล้วว่าตรงกับ TradingView (`test_action_zone.py`) จึงไม่ต้องเชื่อว่า
แท่งสังเคราะห์ที่ผมเขียนไปโดนสัญญาณตามที่ตั้งใจ · ดัชนีสัญญาณที่ใช้ (นับจากแท่งแรกของไฟล์):

    68 Sell · 120 Buy · 172 Sell · 213 Buy · 220 Sell · 221 Buy · 292 Sell · 318 Buy

รันเริ่มที่ดัชนี 173 (`QUIET_FROM`) คือหลังสัญญาณเก่าสุดที่เกี่ยวข้อง ทุกเทสต์จึงเริ่มจากสถานะแบน · 213→220 คือ
long แล้วกลับเป็น short (flip) ส่วน 220→221 คือกลับกลับ

`PaperBroker` ตัวจริง (ไม่ใช่ mock) — สิ่งที่พิสูจน์คือสายที่ต่อกันจนถึงตารางจริง ไม่ใช่ว่าเรียกฟังก์ชันถูกชื่อ
Judge เป็นตัวปลอมเพราะไม่ต้องการเน็ต (`FakeJudge` ของ `test_confluence.py` เป็นต้นแบบ)

**เทสต์ที่เขียนคำสั่งไม่ผ่านต้องเห็น "ไม่มีการเปิดไม้" ไม่ใช่แค่ `skip_reason`** — เส้นทางที่บอกว่าข้ามแล้วยังส่งคำสั่ง
ไปคือบั๊กที่ร้ายที่สุดของไฟล์นี้ จึงเช็ก `broker.positions()` ด้วยทุกครั้ง
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from golden import GOLDEN_DIR, load  # noqa: E402

from cane.config import load_profile  # noqa: E402
from cane.config.settings import Settings, SymbolConfig  # noqa: E402
from cane.data.ohlcv import Bar  # noqa: E402
from cane.db.repo import config as config_repo  # noqa: E402
from cane.db.repo import decisions as decisions_repo  # noqa: E402
from cane.db.repo import killswitch  # noqa: E402
from cane.db.schema import decisions, verdict_cache  # noqa: E402
from cane.engine.lots import StaticLotSource  # noqa: E402
from cane.engine.pipeline import DayPnl, RunContext, SymbolRuntime, run_bar  # noqa: E402
from cane.execution.broker import OrderResult  # noqa: E402
from cane.execution.paper import PaperBroker  # noqa: E402
from cane.sizing.matrix import LotFilter  # noqa: E402

pytestmark = pytest.mark.db

PROFILE = "paper"
SYMBOL = "BTC/USDT"
PERP = "usdtm_perp"
SPOT = "spot"

ROWS = load(GOLDEN_DIR / "BINANCE_BTCUSDT.P, 1D.csv")
BARS: list[Bar] = [row.bar for row in ROWS]

BUY_1, SELL_1, BUY_2 = 213, 220, 221
QUIET_FROM = 173  # หลัง Sell ที่ 172 — ก่อน Buy ที่ 213 ไม่มีสัญญาณเลย
DAY_MS = 86_400_000
T0 = 1_787_961_600_000

SEED = 10_000.0


class ReplayBars:
    """`BarSource` เหนือแท่งของ fixture — `at(i)` คือ "แท่งที่ดัชนี i เพิ่งปิด" """

    def __init__(self, bars: list[Bar] = BARS):
        self._bars = bars
        self.n = 0

    def at(self, index: int) -> None:
        self.n = index + 1

    def bars(self, symbol: str, timeframe: str) -> list[Bar]:
        return list(self._bars[: self.n])


class StubJudge:
    """`LlmClient` ปลอม — ตอบว่ามีทุก factor โดยอ้างแท่งสุดท้ายที่ตารางแสดง · `fail` ทำให้ยกข้อผิดพลาด"""

    def __init__(self, *, present: bool = True, fail: bool = False):
        self.present = present
        self.fail = fail
        self.calls = 0

    def ask(self, *, system, user, schema, factor, side, bar_indices):
        self.calls += 1
        if self.fail:
            raise TimeoutError("gateway ไม่ตอบ")
        return {
            "factor": factor,
            "side": side,
            "present": self.present,
            "confidence": 0.75,
            "evidence_bars": [bar_indices[-1]] if self.present else [],
            "rationale": "stub",
        }


@pytest.fixture(autouse=True)
def _clean(db):
    """ตารางที่ engine เขียนใน profile paper ต้องว่างก่อนเริ่ม — dev DB อาจมีของจากเทสต์อื่นที่ commit ไว้"""
    for table in ("decision_flip", "decision_orders", "decision_stop", "decision_unmanaged",
                  "decision_verdicts", "decision_risk_checks", "decisions", "verdict_cache"):
        db.exec_driver_sql(f"DELETE FROM {table}")
    db.exec_driver_sql("DELETE FROM kill_switch WHERE profile = 'paper'")
    db.exec_driver_sql("DELETE FROM fills WHERE profile = 'paper'")


@pytest.fixture
def settings() -> Settings:
    return load_profile("config/paper.toml")


@pytest.fixture
def version_id(db, settings) -> int:
    return config_repo.insert_version(db, settings, source="migration", created_ts=T0).id


#: bucket ของ `config/paper.toml` (100) เล็กกว่า `min_notional` ของ BTC (100) จนไม้ทุกไม้ถูกสูตรปฏิเสธ —
#: ค่านั้นมีไว้ให้ seed ผ่าน ไม่ใช่ให้เทรด · เทสต์นี้พิสูจน์ไปป์ไลน์ จึงใช้ bucket ที่ไม้ส่งได้จริง
PERP_CFG = SymbolConfig(
    symbol=SYMBOL, market=PERP, bucket_quote_long=10_000.0, bucket_quote_short=6_000.0,
    leverage=2.0, allow_short=True,
)
SPOT_CFG = SymbolConfig(
    symbol=SYMBOL, market=SPOT, bucket_quote_long=10_000.0, leverage=1.0, allow_short=False,
)


def build(db, settings, version_id, *, cfg=None, judge=None, lots=None, market=PERP, bars=None):
    """ประกอบของต่อรัน — คืน `(ctx, sym, bars, broker)`"""
    cfg = cfg or PERP_CFG
    feed = bars or ReplayBars()
    broker = PaperBroker(
        conn=db,
        market=cfg.market,
        profile=PROFILE,
        bars=feed,
        timeframe=settings.timeframe,
        seed_quote=SEED,
        taker_fee_pct=settings.broker.taker_fee_pct,
        maintenance_margin_pct=settings.broker.maintenance_margin_pct,
        funding_source=(lambda symbol, after_ts, through_ts: []) if cfg.market == PERP else None,
    )
    ctx = RunContext(
        profile=PROFILE,
        timeframe=settings.timeframe,
        settings=settings,
        config_version_id=version_id,
        judge=judge or StubJudge(),
        model_id="stub|model",
        lots=lots or StaticLotSource({(cfg.market, cfg.symbol): LotFilter(0.001, 0.001, 100.0)}),
        now=lambda: T0,
    )
    sym = SymbolRuntime(cfg=cfg, bars=feed, broker=broker)
    return ctx, sym, feed, broker


def step(db, ctx, sym, feed, index):
    feed.at(index)
    return run_bar(db, ctx, sym)


def rows_written(db) -> int:
    return db.execute(decisions.select().with_only_columns(decisions.c.id)).all().__len__()


# ── ขั้น 1 · ข้ามเมื่อแท่งไม่พอ ────────────────────────────────────────────


def test_a_symbol_with_too_few_closed_bars_is_skipped_without_a_row(db, settings, version_id, caplog):
    """แท่งไม่ถึง 85 → ไม่มี zone/state/close_px ให้แถวบังคับมี · ข้ามพร้อม log ไม่ใช่แถวเปล่าที่เติมค่าแทน"""
    ctx, sym, feed, broker = build(db, settings, version_id)

    with caplog.at_level("WARNING"):
        record = step(db, ctx, sym, feed, 83)

    assert record is None
    assert rows_written(db) == 0
    assert broker.positions() == []
    assert any("ข้าม" in message for message in caplog.messages)


# ── ขั้น 6 · ไม่มีสัญญาณ → ต้อง "เขียนว่าไม่ทำอะไร" ─────────────────────────


def test_a_bar_with_no_signal_still_writes_a_row_that_says_so(db, settings, version_id):
    """ขั้น 14 เกิดทุกเส้นทาง — บันทึกที่เห็นแต่ตอนระบบลงมือคือบันทึกที่เข้าข้างตัวเอง"""
    ctx, sym, feed, broker = build(db, settings, version_id)

    record = step(db, ctx, sym, feed, 150)

    assert record is not None and record.id is not None
    assert (record.long_signal, record.short_signal) == (False, False)
    assert record.skip_reason in ("no_signal", "cane_rule")
    assert record.orders == () and record.flip is None
    assert rows_written(db) == 1
    assert broker.positions() == []


# ── ขั้น 8–13 · เข้าไม้ (long) ─────────────────────────────────────────────


def test_a_buy_signal_enters_a_long_and_the_row_proves_every_step(db, settings, version_id):
    judge = StubJudge()
    ctx, sym, feed, broker = build(db, settings, version_id, judge=judge)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.long_signal and record.side == "long"
    assert record.skip_reason is None
    # Judge ถูกเรียกครบสามปัจจัยของฝั่ง long และผลถูกบันทึก
    assert record.judge_called is True and judge.calls == 3
    assert [v.side for v in record.verdicts] == ["long"] * 3
    assert record.factors_present == 3 and record.size_rule == "confluence"
    # ขนาดไม้: ค่าที่คำนวณ ไม่ใช่ศูนย์ และเคารพเพดานฝั่ง long
    assert record.qty and record.qty > 0 and record.notional and record.margin
    # ด่าน risk เดินครบสามชั้นตามลำดับบน perp
    assert [c.layer for c in record.risk_checks] == ["kill_switch", "daily_loss", "liq_buffer"]
    assert all(c.passed for c in record.risk_checks)
    # ออเดอร์เปิดถูกรับ และไม้อยู่ที่ broker จริง
    assert [(o.leg, o.accepted) for o in record.orders] == [("open", True)]
    (position,) = broker.positions()
    assert position.side == "long" and position.qty == pytest.approx(record.qty)
    # paper ที่ dry_run = true ก็เข้าไม้ (ADR 31) และบันทึกค่าตามจริง
    assert record.dry_run is True
    # อ่านกลับจากตารางแล้วยังเป็นแถวที่เขียนได้
    decisions_repo.validate_record(decisions_repo.decision_at(db, PROFILE, PERP, SYMBOL, "1d", record.bar_close_ts))


# ── ขั้น 4–5 · flip ─────────────────────────────────────────────────────────


def test_a_sell_signal_while_long_closes_first_then_opens_the_short(db, settings, version_id):
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, SELL_1):
        step(db, ctx, sym, feed, index)
    assert [p.side for p in broker.positions()] == ["long"]

    record = step(db, ctx, sym, feed, SELL_1)

    assert record.short_signal and record.side == "short" and record.skip_reason is None
    # ขา 1 (ปิด) มาก่อนขา 2 (เปิด) และเป็น reduce_only
    assert [o.leg for o in record.orders] == ["close", "open"]
    assert record.orders[0].reduce_only is True and record.orders[0].order_side == "sell"
    assert record.orders[1].order_side == "sell" and record.orders[1].reduce_only is False
    assert record.flip is not None and record.flip.aborted is False and record.flip.residual_qty == 0.0
    # หลัง flip ถือฝั่งเดียว และเป็นฝั่ง short ไม่ใช่สองฝั่งพร้อมกัน
    assert [p.side for p in broker.positions()] == ["short"]


# ── ขั้น 5 · flip ที่ขาปิดไม่ fill ────────────────────────────────────────────


class StuckCloseBroker:
    """ห่อ `PaperBroker` แต่ขาปิด (reduce_only market) ไม่ fill — สถานการณ์ที่ flip ต้อง abort"""

    def __init__(self, inner: PaperBroker):
        self._inner = inner
        self.stuck = True

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def place(self, order):
        if self.stuck and order.reduce_only and order.type == "market":
            return OrderResult(client_order_id=order.client_order_id, venue_order_id="stuck-1",
                               status="open", filled_qty=0.0)
        return self._inner.place(order)


def test_a_flip_whose_close_leg_does_not_fill_aborts_and_keeps_writing_the_leftover(db, settings, version_id):
    """ขา 1 ไม่ fill = ยกเลิกขา 2 ทั้งหมด · ของค้างต้อง **ซ้ำทุกแท่ง** ไม่ใช่บันทึกครั้งเดียวแล้วเงียบ (ADR 19)"""
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, SELL_1):
        step(db, ctx, sym, feed, index)
    sym.broker = StuckCloseBroker(broker)

    aborted = step(db, ctx, sym, feed, SELL_1)

    assert aborted.skip_reason == "flip_aborted"
    assert aborted.flip.aborted is True and aborted.flip.residual_qty > 0
    assert aborted.flip.residual_side == "long"
    # ขาเปิดไม่เคยถูกส่ง — ไม่มีสองฝั่งพร้อมกัน
    open_leg = next(o for o in aborted.orders if o.leg == "open")
    assert open_leg.sent is False and open_leg.accepted is False
    assert [p.side for p in broker.positions()] == ["long"]
    (held,) = aborted.unmanaged
    assert (held.side, held.source, held.first_seen_bar_close_ts) == ("long", "flip_aborted", aborted.bar_close_ts)

    # แท่งถัดไปไม่มีสัญญาณ แต่ของค้างยังต้องถูกเขียน · first_seen ไม่เลื่อน
    later = step(db, ctx, sym, feed, SELL_1 + 1)
    (still,) = later.unmanaged
    assert still.first_seen_bar_close_ts == aborted.bar_close_ts
    assert still.qty == pytest.approx(aborted.flip.residual_qty)


# ── ขั้น 12 · risk ปฏิเสธ ────────────────────────────────────────────────────


def test_a_latched_kill_switch_rejects_the_entry_and_nothing_is_sent(db, settings, version_id):
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)
    killswitch.latch(db, PROFILE, reason="ทดสอบ", by="test")

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.skip_reason == "risk_rejected"
    assert [(c.layer, c.passed) for c in record.risk_checks] == [("kill_switch", False)]
    assert record.orders == ()
    assert broker.positions() == []


def test_a_latched_kill_switch_still_lets_the_opposite_signal_close_the_position(db, settings, version_id):
    """kill switch หยุดการ **เปิด** ความเสี่ยงใหม่ ไม่ใช่การปิด — ไม้ที่ถือค้างต้องออกได้ (spec/06 §Kill switch)"""
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, SELL_1):
        step(db, ctx, sym, feed, index)
    assert [p.side for p in broker.positions()] == ["long"]
    killswitch.latch(db, PROFILE, reason="ทดสอบ", by="test")

    record = step(db, ctx, sym, feed, SELL_1)

    assert record.skip_reason == "risk_rejected"
    assert [o.leg for o in record.orders] == ["close"] and record.orders[0].accepted is True
    assert record.flip is None  # ปิดอย่างเดียวไม่ใช่ flip
    assert broker.positions() == []


def test_a_daily_loss_beyond_the_limit_rejects_and_stops_before_the_liquidation_layer(db, settings, version_id):
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)
    # วันเดียวกับแท่งที่จะตัดสิน ฐาน 2 เท่าของ equity จริง → ขาดทุน 50% เกินเพดาน 5%
    sym.day = DayPnl(day=(BARS[BUY_1].close_ts - 1) // DAY_MS, baseline=Decimal(2 * SEED), _last=Decimal(SEED))

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.skip_reason == "risk_rejected"
    assert [(c.layer, c.passed) for c in record.risk_checks] == [("kill_switch", True), ("daily_loss", False)]
    assert broker.positions() == []


# ── short ปิดไว้ ────────────────────────────────────────────────────────────


def test_a_sell_signal_with_short_disabled_still_closes_the_long(db, settings, version_id):
    """spec/03 §`allow_short = false` — สัญญาณแดงยังต้องปิด long ให้แบน แต่ไม่เปิด short และไม่เรียก Judge"""
    off = settings.model_copy(update={"allow_short": False})
    judge = StubJudge()
    ctx, sym, feed, broker = build(db, off, version_id, judge=judge)
    for index in range(QUIET_FROM, SELL_1):
        step(db, ctx, sym, feed, index)
    calls_before = judge.calls

    record = step(db, ctx, sym, feed, SELL_1)

    assert record.skip_reason == "short_disabled"
    assert [o.leg for o in record.orders] == ["close"]
    assert judge.calls == calls_before  # ไม่เรียก Judge เพิ่ม
    assert record.judge_called is None and record.side is None
    assert broker.positions() == []


# ── ขั้น 13 · dry_run บน broker จริง ─────────────────────────────────────────


def test_dry_run_on_a_real_broker_computes_everything_and_sends_nothing(db, settings, version_id):
    """ADR 31 — `dry_run` กั้นเฉพาะ `broker.kind = ccxt` · ที่นี่จำลองด้วยการเปลี่ยน kind แต่ยังใช้ `PaperBroker`
    เป็นตัวรับ เพื่อพิสูจน์ว่า **ไม่มีคำสั่งไปถึง broker เลย** ไม่ใช่แค่ `skip_reason` บอกว่าข้าม
    """
    live_like = settings.model_copy(update={"broker": settings.broker.model_copy(update={"kind": "ccxt"})})
    ctx, sym, feed, broker = build(db, live_like, version_id)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.skip_reason == "dry_run"
    assert [(o.leg, o.sent, o.accepted) for o in record.orders] == [("open", False, False)]
    assert record.qty and record.qty > 0  # คำนวณครบ
    assert all(c.passed for c in record.risk_checks)
    assert broker.positions() == []


# ── ขั้น 9 · Judge ล้มเหลว ───────────────────────────────────────────────────


def test_a_failed_judge_falls_back_to_the_base_size_and_still_enters(db, settings, version_id):
    """ADR 6 — LLM ใช้การไม่ได้ = ไม่มี factor + ติดธง แล้วเข้าไม้ที่ `base_pct` ไม่ใช่ข้ามสัญญาณ"""
    ctx, sym, feed, broker = build(db, settings, version_id, judge=StubJudge(fail=True))
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.skip_reason is None
    assert (record.llm_fallback, record.llm_fallback_reason) == (True, "transport")
    assert record.factors_present == 0
    assert record.size_pct_final == record.size_pct_formula == settings.base_pct
    assert [p.side for p in broker.positions()] == ["long"]


# ── ขั้น 10 · ขนาดไม้ส่งไม่ได้ ────────────────────────────────────────────────


def test_a_sized_trade_the_venue_would_refuse_is_recorded_as_an_order_error(db, settings, version_id):
    """ไม้ที่คำนวณได้แต่ส่งไม่ได้ต้องมีเหตุผลของสูตรติดมากับขาเปิดที่ไม่ได้ส่ง — `validate_record` บังคับ"""
    lots = StaticLotSource({(PERP, SYMBOL): LotFilter(0.001, 0.001, 10_000_000.0)})
    ctx, sym, feed, broker = build(db, settings, version_id, lots=lots)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.skip_reason == "order_error"
    (attempt,) = record.orders
    assert (attempt.leg, attempt.sent, attempt.accepted) == ("open", False, False)
    assert attempt.error == "below_min_notional"
    assert record.risk_checks == ()  # ไม่มีไม้ให้เช็ก risk
    assert broker.positions() == []


# ── cold start ทางที่ 2 ─────────────────────────────────────────────────────

#: แท่งจริงที่ `state` เป็น BULLISH ไม่มีสัญญาณ ไม่มีสถานะ และ RR ของ Trail2 ≥ 2 (สแกนจาก fixture แล้ว) —
#: เข้าไม้ตอน engine เพิ่งเริ่มได้เส้นทาง `trailing` พร้อม stop ที่ 8257.9449817
COLD_AT = 137

#: แท่งที่เข้าไม้ cold start แล้วไม้ **รอดอย่างน้อย 15 แท่ง** และ Trail2 ขยับหลายครั้ง (สแกนแล้ว) — ที่ 137 ไม้ถูก stop
#: ออกในแท่งถัดไปทันที จึงใช้ดูการเลื่อน stop ไม่ได้
COLD_HELD = 138


def cold_settings(settings) -> Settings:
    return settings.model_copy(update={"cold_start": "trailing"})


def test_cold_start_trailing_enters_at_base_pct_with_a_stop_and_never_calls_the_judge(db, settings, version_id):
    judge = StubJudge()
    ctx, sym, feed, broker = build(db, cold_settings(settings), version_id, judge=judge)

    record = step(db, ctx, sym, feed, COLD_AT)

    assert (record.cold_start, record.side, record.size_rule) == ("trailing", "long", "cold_start")
    assert record.skip_reason is None and record.judge_called is False and judge.calls == 0
    assert record.size_pct_final == cold_settings(settings).base_pct
    # ขาเปิดแล้วตามด้วย stop ที่ exchange ทันที (ADR 17) เป็น reduce_only ที่ Trail2
    assert [o.leg for o in record.orders] == ["open", "stop"]
    stop_leg = record.orders[1]
    assert stop_leg.order_type == "stop_market" and stop_leg.reduce_only and stop_leg.accepted
    assert record.stop.action == "placed" and record.stop.px == stop_leg.stop_px == 8257.9449817
    assert [o.type for o in broker.open_orders(SYMBOL)] == ["stop_market"]


def test_cold_start_is_evaluated_once_per_run_not_on_every_bar(db, settings, version_id):
    """spec/08 §cold start — ถ้าประเมินทุกแท่ง ระบบจะเข้าไม้ใหม่ทันทีทุกครั้งที่ถูก stop ทั้งที่ไม่มีสัญญาณ"""
    ctx, sym, feed, broker = build(db, cold_settings(settings), version_id)
    assert sym.cold_start_pending is True

    step(db, ctx, sym, feed, COLD_AT - 1)  # แท่งที่ไม่เข้าเส้นทาง — แต่ก็นับว่าเป็นรอบแรกแล้ว
    assert sym.cold_start_pending is False

    record = step(db, ctx, sym, feed, COLD_AT)

    assert record.cold_start is None and record.side is None
    assert broker.positions() == []


def test_the_stop_follows_slow_trail_even_while_the_kill_switch_is_latched(db, settings, version_id):
    """spec/06 §Kill switch — การเลื่อน stop คือขยับจุดป้องกัน ไม่ใช่เปิดความเสี่ยงใหม่"""
    ctx, sym, feed, broker = build(db, cold_settings(settings), version_id)
    step(db, ctx, sym, feed, COLD_HELD)
    killswitch.latch(db, PROFILE, reason="ทดสอบ", by="test")

    actions = []
    for index in range(COLD_HELD + 1, COLD_HELD + 16):
        record = step(db, ctx, sym, feed, index)
        assert broker.positions(), "ไม้ต้องรอดตลอดช่วงนี้ ไม่งั้นข้อนี้ไม่ได้พิสูจน์อะไร"
        actions.append(record.stop.action)
        assert record.skip_reason in ("no_signal", "cane_rule", "already_positioned")  # ไม่มีการเปิดใหม่
    assert "replaced" in actions, actions
    assert set(actions) <= {"replaced", "unchanged"}


def test_a_stop_that_should_exist_but_does_not_is_recorded_missing_and_not_silently_replaced(db, settings, version_id):
    """spec/08 §การเลื่อน stop ตาม Slow Trail — หายไปแปลว่าทำงานไปแล้วหรือหลุดไป ต้องเทียบกับ `positions()` ให้ชัด"""
    ctx, sym, feed, broker = build(db, cold_settings(settings), version_id)
    entered = step(db, ctx, sym, feed, COLD_AT)
    broker.cancel(entered.stop.stop_order_id)
    assert broker.open_orders(SYMBOL) == []

    record = step(db, ctx, sym, feed, COLD_AT + 1)

    assert record.stop.action == "missing"
    assert broker.open_orders(SYMBOL) == []  # ไม่วางทับเงียบๆ
    assert [p.side for p in broker.positions()] == ["long"]


# ── spot ─────────────────────────────────────────────────────────────────────


def test_spot_buys_on_green_and_sells_to_flat_on_red_with_no_flip_and_no_reduce_only(db, settings, version_id):
    """spec/03 §`spot` — long-only: แดงคือขายออกให้แบน ไม่มี flip และ spot ไม่มี reduceOnly"""
    spot_settings = settings.model_copy(update={"allow_short": False})
    ctx, sym, feed, broker = build(db, spot_settings, version_id, cfg=SPOT_CFG)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)

    bought = step(db, ctx, sym, feed, BUY_1)
    assert bought.skip_reason is None and bought.margin_mode is None and bought.leverage == 1.0
    assert [(o.leg, o.order_side, o.reduce_only) for o in bought.orders] == [("open", "buy", False)]
    assert [c.layer for c in bought.risk_checks] == ["kill_switch", "daily_loss"]  # spot ไม่มีชั้น liquidation

    for index in range(BUY_1 + 1, SELL_1):
        step(db, ctx, sym, feed, index)
    sold = step(db, ctx, sym, feed, SELL_1)

    assert sold.skip_reason == "short_disabled" and sold.flip is None
    assert [(o.leg, o.order_side, o.reduce_only) for o in sold.orders] == [("close", "sell", False)]
    assert broker.positions() == []


# ── broker ล้มตอนส่งคำสั่ง ────────────────────────────────────────────────────


class RaisingBroker:
    """ห่อ `PaperBroker` แต่ `place()` ที่เข้าเงื่อนไข `when` ยกข้อผิดพลาด — ปลายทางล่มกลางแท่ง"""

    def __init__(self, inner: PaperBroker, when):
        self._inner = inner
        self._when = when

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def place(self, order):
        if self._when(order):
            raise ConnectionError("exchange ล่ม")
        return self._inner.place(order)


def test_a_broker_that_fails_on_the_open_leg_ends_the_bar_as_an_order_error_without_crashing(db, settings, version_id):
    """`run_bar` ต้องไม่ล้มทั้งรันเพราะปลายทางล่มหนึ่งครั้ง — บันทึกแล้วให้ขั้น 3 ของแท่งหน้าอ่านของจริงเอง"""
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, BUY_1):
        step(db, ctx, sym, feed, index)
    sym.broker = RaisingBroker(broker, lambda order: not order.reduce_only)

    record = step(db, ctx, sym, feed, BUY_1)

    assert record.skip_reason == "order_error"
    (attempt,) = record.orders
    assert (attempt.leg, attempt.sent, attempt.accepted) == ("open", True, False)
    assert "exchange ล่ม" in attempt.error
    assert broker.positions() == []


def test_a_broker_that_fails_in_the_middle_of_a_flip_records_it_and_leaves_the_truth_to_the_next_read(db, settings, version_id):
    ctx, sym, feed, broker = build(db, settings, version_id)
    for index in range(QUIET_FROM, SELL_1):
        step(db, ctx, sym, feed, index)
    sym.broker = RaisingBroker(broker, lambda order: order.reduce_only)  # ขาปิดล่มเลย

    record = step(db, ctx, sym, feed, SELL_1)

    assert record.skip_reason == "order_error"
    close_leg, open_leg = record.orders
    assert close_leg.leg == "close" and "exchange ล่ม" in close_leg.error
    assert open_leg.leg == "open" and open_leg.sent is False and "flip" in open_leg.error
    assert [p.side for p in broker.positions()] == ["long"]  # ไม่มีสองฝั่งพร้อมกัน
