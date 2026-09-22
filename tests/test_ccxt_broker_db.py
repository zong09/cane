"""`CcxtBroker` + `reconcile` — เกณฑ์เสร็จของใบ 13 ที่พิสูจน์ได้โดยไม่ต่อเน็ต

ใบสั่งไว้ว่า "จำลอง restart กลางคันในแท่งเดียวกันแล้วไม่เกิดออเดอร์ซ้ำและไม่กลับข้างซ้ำ"
ซึ่งตรวจได้ทั้งหมดด้วย venue ปลอม · ส่วนที่เหลือของใบ (รัน live `dry_run` หนึ่งสัปดาห์)
เป็นการสังเกตของจริง ไม่ใช่สิ่งที่เทสต์ยืนยันแทนได้

**venue ปลอมตัวนี้ต้องดื้อเหมือนของจริงสามข้อ** ไม่งั้นมันพิสูจน์แค่ว่าเราเรียกฟังก์ชัน
ถูกชื่อ:

1. `clientOrderId` ซ้ำ = ปฏิเสธ (นี่คือกลไกกันสั่งซ้ำทั้งหมดของ spec/06)
2. `fetch_my_trades` **ไม่คืน `clientOrderId`** มาด้วย — Binance ให้มาแต่เลขออเดอร์
   ถ้าปลอมให้มันคืนมา เราจะไม่มีวันเจอว่าโค้ดลืม join กับรายการออเดอร์
3. amend `STOP_MARKET` ไม่ได้ (`PUT /fapi/v1/order` ของ Binance รับแต่ LIMIT)
"""

from __future__ import annotations

import sys
from pathlib import Path

import ccxt
import pytest
from ccxt.base.decimal_to_precision import DECIMAL_PLACES, TICK_SIZE

sys.path.insert(0, str(Path(__file__).parent))

from golden import GOLDEN_DIR, load  # noqa: E402

from cane.config import load_profile  # noqa: E402
from cane.config.settings import SymbolConfig  # noqa: E402
from cane.db.repo import config as config_repo  # noqa: E402
from cane.db.repo import ledger as ledger_repo  # noqa: E402
from cane.db.schema import fills  # noqa: E402
from cane.engine.lots import CcxtLotSource, UnknownLot  # noqa: E402
from cane.engine.pipeline import RunContext, SymbolRuntime, run_bar  # noqa: E402
from cane.execution import Order, client_order_id  # noqa: E402
from cane.execution.ccxt_broker import BrokerError, CcxtBroker, replacement_id  # noqa: E402
from cane.execution.reconcile import bar_and_leg, spec_order_id, venue_order_id  # noqa: E402

PROFILE = "live"
SYMBOL = "BTC/USDT"
PERP = "usdtm_perp"
SPOT = "spot"
T0 = 1_787_961_600_000

#: เพดานของ `newClientOrderId` ที่ Binance บังคับ — ทั้ง fapi และ spot
VENUE_ID_LIMIT = 36


# ── กุญแจของออเดอร์ (ไม่แตะฐาน ไม่แตะเน็ต) ──────────────────────────────────


@pytest.mark.parametrize("leg", ["open", "close", "stop"])
@pytest.mark.parametrize("symbol", ["BTC/USDT", "1000PEPE/USDT"])
def test_every_leg_fits_the_venue_limit(leg, symbol):
    """กุญแจเต็มตาม spec/06 ยาวเกิน 36 อักษรแล้วตั้งแต่ขาปิด — รูปของ venue ต้องไม่เกิน

    นี่คือเหตุผลทั้งหมดที่ `venue_order_id()` มีอยู่ · ถ้าเทสต์นี้หายไป ระบบจะส่งขาเปิด
    ได้แต่ขาปิดถูกปฏิเสธ ซึ่งแปลว่า flip ค้างครึ่งทางบน perp ที่มี leverage
    """
    order_side = "sell" if leg == "close" else "buy"
    spec = client_order_id(symbol, T0, order_side, leg)
    venue = venue_order_id(T0, order_side, leg)

    assert len(spec) > VENUE_ID_LIMIT or symbol == "BTC/USDT"
    assert len(venue) <= VENUE_ID_LIMIT
    assert spec_order_id(venue, symbol) == spec


def test_a_replacement_stop_still_points_at_the_original_decision():
    """fill ของ stop ใบใหม่ต้องโยงกลับไปหา `decision_orders` แถวเดิม (ใบ 03 join ด้วยคีย์)"""
    base = venue_order_id(T0, "sell", "stop")
    replaced = replacement_id(base, 41_000.0)

    assert len(replaced) <= VENUE_ID_LIMIT
    assert replaced != base
    assert replacement_id(base, 41_000.0) == replaced  # deterministic — ลองซ้ำได้กุญแจเดิม
    assert replacement_id(base, 42_000.0) != replaced
    assert spec_order_id(replaced, SYMBOL) == client_order_id(SYMBOL, T0, "sell", "stop")


def test_an_order_a_human_placed_is_not_ours():
    """spec/06: reconcile ต้อง**เห็น**ของที่คนกดเอง แต่ต้องไม่**นับว่าเป็นของตัวเอง**"""
    assert spec_order_id("web_1234567890", SYMBOL) is None
    assert spec_order_id("", SYMBOL) is None
    assert spec_order_id("cane-นี่ไม่ใช่เลข-buy-open", SYMBOL) is None


def test_a_bar_that_is_not_on_a_second_boundary_is_refused():
    """ปัดเศษมิลลิวินาทีทิ้งเงียบๆ = สองแท่งได้กุญแจเดียวกัน ซึ่งคือสิ่งเดียวที่กุญแจนี้กัน"""
    with pytest.raises(ValueError, match="วินาที"):
        venue_order_id(T0 + 1, "buy", "open")


def test_a_hand_built_client_order_id_is_refused_before_it_reaches_the_venue():
    with pytest.raises(ValueError, match="spec/06"):
        bar_and_leg("cane-BTC/USDT-buy")


# ── venue ปลอม ──────────────────────────────────────────────────────────────


class FakeVenue:
    """ccxt ปลอมหนึ่งตลาด · ดูหัวไฟล์ว่าทำไมมันต้องดื้อสามข้อ"""

    precisionMode = TICK_SIZE

    def __init__(self, market: str = PERP, *, px: float = 40_000.0, quote: float = 100_000.0,
                 trade_ids: bool = True):
        self.market = market
        self.px = px
        self.quote = quote
        #: venue บางแห่งไม่ให้เลข fill มา — กุญแจกันซ้ำต้องพึ่ง `seq` แทน
        self.trade_ids = trade_ids
        self.orders: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.side: str | None = None
        self.qty = 0.0
        self.entry = 0.0
        self.leverage = 1.0
        self.calls: list[str] = []
        self._next = 0

    # -- สิ่งที่ผู้เรียกทำได้

    def create_order(self, symbol, type, side, amount, price=None, params=None):  # noqa: A002
        params = params or {}
        coid = params.get("clientOrderId", "")
        self.calls.append(f"create:{coid}")
        if any(o["clientOrderId"] == coid for o in self.orders.values()):
            raise ccxt.InvalidOrder(f"duplicate clientOrderId {coid}")
        if len(coid) > VENUE_ID_LIMIT:
            raise ccxt.InvalidOrder(f"clientOrderId ยาว {len(coid)} เกิน {VENUE_ID_LIMIT}")
        self._next += 1
        oid = str(self._next)
        trigger = params.get("triggerPrice")
        row = {
            "id": oid, "clientOrderId": coid, "symbol": symbol, "side": side,
            "amount": amount, "status": "open", "filled": 0.0, "average": None,
            "triggerPrice": trigger, "reduceOnly": bool(params.get("reduceOnly")),
        }
        self.orders[oid] = row
        if trigger is None:
            self._fill(row, self.px)
        return dict(row)

    def cancel_order(self, id, symbol=None):  # noqa: A002
        self.calls.append(f"cancel:{id}")
        row = self.orders.get(id)
        if row is None or row["status"] != "open":
            raise ccxt.OrderNotFound(id)
        row["status"] = "canceled"
        return dict(row)

    def edit_order(self, id, symbol, type, side, amount=None, price=None, params=None):  # noqa: A002
        self.calls.append(f"edit:{id}")
        # Binance amend ได้เฉพาะ LIMIT · stop ของระบบเป็น STOP_MARKET
        raise ccxt.NotSupported("amend รองรับเฉพาะ LIMIT order")

    # -- การอ่าน

    def fetch_orders(self, symbol=None, since=None):
        return [dict(o) for o in self.orders.values()]

    def fetch_my_trades(self, symbol=None, since=None):
        # ไม่มีคีย์ `clientOrderId` โดยเจตนา — Binance ไม่ให้มา
        return [dict(t) for t in self.trades]

    def fetch_open_orders(self, symbol=None):
        return [dict(o) for o in self.orders.values() if o["status"] == "open"]

    def fetch_positions(self, symbols=None):
        if self.qty <= 0:
            return []
        return [{
            "symbol": f"{SYMBOL}:USDT", "side": self.side, "contracts": self.qty,
            "entryPrice": self.entry, "markPrice": self.px, "unrealizedPnl": 0.0,
            "leverage": self.leverage, "liquidationPrice": self.entry * 0.5,
        }]

    def fetch_balance(self):
        return {"USDT": {"free": self.quote, "used": 0.0, "total": self.quote}}

    def fetch_ticker(self, symbol):
        return {"last": self.px}

    def fetch_funding_history(self, symbol=None, since=None):
        return []

    def fetch_funding_rate_history(self, symbol=None, since=None):
        return []

    def set_leverage(self, leverage, symbol=None):
        self.leverage = leverage

    def set_margin_mode(self, marginMode, symbol=None):  # noqa: N803
        return None

    def fetch_market_leverage_tiers(self, symbol):
        # โตตามขนาดไม้เหมือนตารางจริงของ Binance — ชั้นเดียวจะไม่จับบั๊ก "หยิบชั้นแรกเสมอ"
        return [
            {"tier": 1, "maxNotional": 50_000, "maintenanceMarginRate": 0.004},
            {"tier": 2, "maxNotional": 250_000, "maintenanceMarginRate": 0.005},
        ]

    def load_markets(self, reload=False):
        return {
            f"{SYMBOL}:USDT": {
                "precision": {"amount": 0.001},
                "limits": {"amount": {"min": 0.001}, "cost": {"min": 100.0}},
            },
        }

    # -- ข้างใน

    def _fill(self, row: dict, px: float, *, qty: float | None = None) -> None:
        qty = row["amount"] if qty is None else qty
        row.update(status="closed", filled=qty, average=px)
        self._next += 1
        self.trades.append({
            "id": f"t{self._next}" if self.trade_ids else None,
            "order": row["id"], "symbol": row["symbol"],
            "side": row["side"], "price": px, "amount": qty,
            "timestamp": T0 + 60_000, "fee": {"cost": px * qty * 0.0004, "currency": "USDT"},
        })
        signed = qty if row["side"] == "buy" else -qty
        held = (self.qty if self.side == "long" else -self.qty) + signed
        self.entry = px if self.qty == 0 else self.entry
        self.qty = abs(held)
        self.side = "long" if held > 0 else "short" if held < 0 else None
        if self.qty == 0:
            self.entry = 0.0

    def split_last_fill(self, px: float) -> None:
        """ออเดอร์ใบล่าสุด fill เป็นก้อนที่สองคนละราคา — เกิดจริงบนออเดอร์ขนาดใหญ่"""
        last = self.trades[-1]
        half = last["amount"] / 2
        last["amount"] = half
        self._next += 1
        self.trades.append({**last, "id": f"t{self._next}" if self.trade_ids else None,
                            "price": px, "amount": half})

    def trigger_stop(self, px: float) -> None:
        """stop ทำงานที่ venue ตอนที่ process ของเราไม่อยู่"""
        for row in self.orders.values():
            if row["status"] == "open" and row["triggerPrice"] is not None:
                self._fill(row, px)
                return
        raise AssertionError("ไม่มี stop ค้างให้ทำงาน")


# ── ตัวช่วยของเทสต์ที่แตะฐาน ────────────────────────────────────────────────

ROWS = load(GOLDEN_DIR / "BINANCE_BTCUSDT.P, 1D.csv")
BARS = [row.bar for row in ROWS]
BUY_1 = 213


class ReplayBars:
    def __init__(self, bars=BARS):
        self._bars = bars
        self.n = 0

    def at(self, index: int) -> None:
        self.n = index + 1

    def bars(self, symbol, timeframe):
        return list(self._bars[: self.n])


class StubJudge:
    def ask(self, *, system, user, schema, factor, side, bar_indices):
        return {
            "factor": factor, "side": side, "present": True, "confidence": 0.75,
            "evidence_bars": [bar_indices[-1]], "rationale": "stub",
        }


def broker_of(db, venue, feed, *, market=PERP, timeframe="1d"):
    return CcxtBroker(
        conn=db, client=venue, market=market, profile=PROFILE,
        bars=feed, timeframe=timeframe, symbols=(SYMBOL,),
    )


def fills_rows(db, leg: str | None = None):
    stmt = fills.select().where(fills.c.profile == PROFILE)
    rows = list(db.execute(stmt))
    return [r for r in rows if leg is None or r.leg == leg]


@pytest.fixture
def clean(db):
    """ตารางที่ engine เขียนใน profile live ต้องว่างก่อนเริ่ม — dev DB อาจมีของค้างจากที่อื่น"""
    for table in ("decision_flip", "decision_orders", "decision_stop", "decision_unmanaged",
                  "decision_verdicts", "decision_risk_checks", "decisions", "verdict_cache"):
        db.exec_driver_sql(f"DELETE FROM {table}")
    db.exec_driver_sql(f"DELETE FROM fills WHERE profile = '{PROFILE}'")
    db.exec_driver_sql(f"DELETE FROM funding_charges WHERE profile = '{PROFILE}'")
    db.exec_driver_sql(f"DELETE FROM kill_switch WHERE profile = '{PROFILE}'")
    return db


# ── การอ่านความจริงกลับ ─────────────────────────────────────────────────────


@pytest.mark.db
def test_the_ledger_row_comes_from_the_venues_record_not_from_our_request(clean):
    """ราคาและค่าธรรมเนียมที่ลง ledger ต้องเป็นของ venue · คำตอบของ `create_order` ยังไม่ใช่ความจริง"""
    venue = FakeVenue(px=40_000.0)
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed)

    result = broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))

    rows = fills_rows(clean)
    assert len(rows) == 1
    row = rows[0]
    assert row.client_order_id == client_order_id(SYMBOL, T0, "buy", "open")
    assert float(row.px) == 40_000.0
    assert float(row.fee_quote) == pytest.approx(40_000.0 * 0.5 * 0.0004)
    # เวลาของ venue กับแท่งที่เรามารู้เป็นคนละค่า — คอลัมน์แยกกันตั้งแต่ schema
    assert row.fill_ts == T0 + 60_000
    assert row.bar_close_ts == BARS[100].close_ts
    assert row.leg == "open" and row.exit_reason is None
    assert result.venue_order_id == "1"


@pytest.mark.db
def test_the_same_order_sent_twice_in_one_bar_does_not_open_twice(clean):
    """restart กลางแท่ง: กุญแจ deterministic ทำให้ venue ปฏิเสธใบที่สอง (spec/06)"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    order = Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    )
    broker_of(clean, venue, feed).place(order)

    # process ใหม่ ความจำว่าง แต่กุญแจเหมือนเดิมเป๊ะ
    with pytest.raises(BrokerError, match="ปฏิเสธ"):
        broker_of(clean, venue, feed).place(order)

    assert len(fills_rows(clean)) == 1
    assert venue.qty == 0.5


@pytest.mark.db
def test_a_stop_that_fired_while_the_process_was_down_is_recorded_on_the_next_read(clean):
    """ขั้น 3 อ่านของจริงทุกแท่ง — fill ที่เกิดตอนเราไม่อยู่ต้องเข้า ledger ตอนกลับมา"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed)
    broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))
    broker.place(Order(
        symbol=SYMBOL, side="sell", type="stop_market", qty=0.5, stop_px=38_000.0,
        reduce_only=True, client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"),
    ))

    venue.trigger_stop(37_500.0)  # ราคากระโดดข้ามตอน process ดับ
    feed.at(101)
    fresh = broker_of(clean, venue, feed)
    assert fresh.open_orders(SYMBOL) == []

    stop_fills = fills_rows(clean, leg="stop")
    assert len(stop_fills) == 1
    row = stop_fills[0]
    assert row.exit_reason == "stop"
    assert float(row.position_qty_after) == 0.0
    assert row.client_order_id == client_order_id(SYMBOL, T0, "sell", "stop")
    # แท่งที่เรามารู้คือแท่งที่กำลังอ่าน ไม่ใช่แท่งที่ venue บอกว่า fill เกิด
    assert row.bar_close_ts == BARS[101].close_ts
    assert row.fill_ts != row.bar_close_ts


@pytest.mark.db
def test_reading_the_same_window_again_does_not_write_the_fill_twice(clean):
    """ทุกแท่งอ่านหน้าต่างเดิมซ้ำโดยตั้งใจ — `UNIQUE (profile, dedupe_key)` คือตัวกัน"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed)
    broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))

    for index in (101, 102, 103):
        feed.at(index)
        broker.open_orders(SYMBOL)

    assert len(fills_rows(clean)) == 1


@pytest.mark.db
def test_a_trade_from_an_order_a_human_placed_never_reaches_the_ledger(clean):
    """ADR 19: ของที่คนกดเองเป็นสถานะที่ระบบไม่ได้ตั้งใจถือ ไม่ใช่ไม้ของระบบ"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    venue.create_order(f"{SYMBOL}:USDT", "market", "buy", 0.2, None, {"clientOrderId": "web_999"})

    broker_of(clean, venue, feed).open_orders(SYMBOL)

    assert fills_rows(clean) == []


@pytest.mark.db
def test_positions_come_back_in_the_form_step_three_compares_against(clean):
    """ขั้น 3 จับคู่ `p.symbol == symbol` ตรงตัว — รูปยาวทำให้ไปป์ไลน์เชื่อว่าไม่มีไม้"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed)
    broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))

    held = broker.positions()

    assert [p.symbol for p in held] == [SYMBOL]
    assert held[0].side == "long" and held[0].liquidation_px is not None


@pytest.mark.db
def test_a_spot_position_counts_only_what_the_ledger_says_we_bought(clean):
    """ยอดคงเหลือของ spot รวมเหรียญที่คนโอนเข้ามาเอง — อ้างสิทธิ์ทั้งก้อนไม่ได้"""
    venue = FakeVenue(market=SPOT)
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed, market=SPOT)

    assert broker.positions() == []

    broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))
    held = broker.positions()

    assert len(held) == 1
    assert held[0].qty == 0.5 and held[0].entry_px == 40_000.0
    # spot ไม่มี liquidation อยู่จริง ไม่ใช่คำนวณไม่ได้ (ADR 26)
    assert held[0].liquidation_px is None


@pytest.mark.db
def test_a_stop_is_replaced_by_placing_the_new_one_before_cancelling_the_old(clean):
    """ADR 17: ห้ามมีช่วงเวลาที่ไม้ไม่มี stop คุ้ม · Binance amend STOP_MARKET ไม่ได้"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed)
    broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))
    placed = broker.place(Order(
        symbol=SYMBOL, side="sell", type="stop_market", qty=0.5, stop_px=38_000.0,
        reduce_only=True, client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"),
    ))
    venue.calls.clear()

    moved = broker.replace(placed.venue_order_id, 39_000.0)

    create_at = next(i for i, c in enumerate(venue.calls) if c.startswith("create:"))
    cancel_at = next(i for i, c in enumerate(venue.calls) if c.startswith("cancel:"))
    assert create_at < cancel_at, "วางใบใหม่ต้องมาก่อนยกเลิกใบเก่า ไม่งั้นมีช่วงที่ไม้เปลือย"
    # กุญแจตามสเปกยังเป็นของใบเดิม — fill ของมันต้องโยงกลับไปหาแถวเดิมของ `decision_orders`
    assert moved.client_order_id == client_order_id(SYMBOL, T0, "sell", "stop")
    remaining = broker.open_orders(SYMBOL)
    assert [o.stop_px for o in remaining] == [39_000.0]


# ── ทั้งไปป์ไลน์: restart กลางแท่งเดียวกัน ───────────────────────────────────


PERP_CFG = SymbolConfig(
    symbol=SYMBOL, market=PERP, bucket_quote_long=10_000.0, bucket_quote_short=6_000.0,
    leverage=2.0, allow_short=True,
)


@pytest.mark.db
def test_deciding_the_same_bar_twice_opens_one_position_and_does_not_flip_back(clean):
    """เกณฑ์เสร็จของใบ 13 · restart กลางคันในแท่งเดียวกัน = ไม่มีออเดอร์ซ้ำ ไม่กลับข้างซ้ำ

    รอบที่สองใช้ broker ตัวใหม่ (ความจำว่างเหมือน process ที่เพิ่งกลับมา) แต่ **สถานะจริง
    ที่ venue ยังอยู่** — ขั้น 3 อ่านเจอแล้วไม่เปิดซ้ำ ซึ่งคือเหตุผลที่ spec/08 สั่งให้อ่าน
    สถานะจริงก่อนตัดสินใจทุกครั้ง แทนที่จะเชื่อความจำ
    """
    settings = load_profile("config/live.toml").model_copy(
        update={"dry_run": False, "symbols": [PERP_CFG]}
    )
    # config ของ live **ห้าม**ถืออัตรา maintenance margin (`cross_checks()`) — ด่าน
    # min_liq_buffer_pct จึงต้องได้ค่าจาก venue ผ่าน `broker.maintenance_margin()`
    # ไม่งั้นมันปฏิเสธไม้ perp ทุกไม้ตลอดไปและ live จะเทรดไม่ได้เลย
    assert settings.broker.maintenance_margin_pct is None
    version = config_repo.insert_version(clean, settings, source="migration", created_ts=T0)
    venue = FakeVenue(px=float(BARS[BUY_1].close))
    feed = ReplayBars()
    feed.at(BUY_1)
    ctx = RunContext(
        profile=PROFILE, timeframe=settings.timeframe, settings=settings,
        config_version_id=version.id, judge=StubJudge(), model_id="stub|model",
        lots=CcxtLotSource({PERP: venue}), now=lambda: T0,
    )

    first = run_bar(clean, ctx, SymbolRuntime(
        cfg=PERP_CFG, bars=feed, broker=broker_of(clean, venue, feed)))
    opened = venue.qty
    assert opened > 0, "แท่งนี้ต้องเปิดไม้ ไม่งั้นเทสต์ไม่ได้พิสูจน์อะไร"

    second = run_bar(clean, ctx, SymbolRuntime(
        cfg=PERP_CFG, bars=feed, broker=broker_of(clean, venue, feed)))

    assert venue.qty == opened and venue.side == "long"
    assert len([c for c in venue.calls if c.startswith("create:")]) == 1
    assert len(fills_rows(clean, leg="open")) == 1
    assert first is not None and second is not None
    assert ledger_repo.open_trade_id(clean, PROFILE, PERP, SYMBOL, "long") is not None


# ── ตัวกรอง lot ของ venue ───────────────────────────────────────────────────


def test_a_tick_size_venue_reports_the_step_as_is():
    assert CcxtLotSource({PERP: FakeVenue()}).lot(PERP, SYMBOL).step == 0.001


def test_a_decimal_places_venue_reports_a_digit_count_not_a_step():
    """อ่านผิดแบบหนึ่งหลักคือ qty ที่ผิดพันเท่า — จึงถาม `precisionMode` ไม่ใช่เดาจากขนาดตัวเลข"""
    venue = FakeVenue()
    venue.precisionMode = DECIMAL_PLACES
    venue.load_markets = lambda reload=False: {  # type: ignore[method-assign]
        f"{SYMBOL}:USDT": {"precision": {"amount": 3}, "limits": {"amount": {"min": 0.001}, "cost": {}}}
    }

    lot = CcxtLotSource({PERP: venue}).lot(PERP, SYMBOL)

    assert lot.step == pytest.approx(0.001)
    assert lot.min_notional is None  # venue ไม่มีเกณฑ์นี้ ≠ เกณฑ์เป็นศูนย์


def test_a_symbol_the_venue_does_not_list_fails_closed():
    with pytest.raises(UnknownLot):
        CcxtLotSource({PERP: FakeVenue()}).lot(PERP, "DOGE/USDT")


# ── สิ่งที่ venue ดื้อกว่าที่คิด ──────────────────────────────────────────────


@pytest.mark.db
def test_two_chunks_of_one_order_both_reach_the_ledger_when_the_venue_gives_no_fill_id(clean):
    """ออเดอร์ใบเดียว fill หลายก้อนคนละราคาเกิดจริง — ทุกก้อนคือเงินคนละก้อน

    `ledger.dedupe_key_of` เขียนเตือนไว้ตรงๆ ว่า `seq` ไม่ใช่ของประดับ · ถ้าทุกก้อนได้
    กุญแจ `{coid}#0` เหมือนกัน ก้อนที่สองจะถูกมองว่าซ้ำแล้วหายไปเงียบๆ
    """
    venue = FakeVenue(trade_ids=False)
    feed = ReplayBars()
    feed.at(100)
    broker = broker_of(clean, venue, feed)
    broker.place(Order(
        symbol=SYMBOL, side="buy", type="market", qty=0.5,
        client_order_id=client_order_id(SYMBOL, T0, "buy", "open"),
    ))
    venue.split_last_fill(40_100.0)

    feed.at(101)
    broker.open_orders(SYMBOL)

    rows = sorted(fills_rows(clean), key=lambda r: float(r.px))
    assert [float(r.px) for r in rows] == [40_000.0, 40_100.0]
    assert {r.dedupe_key for r in rows} == {
        f"{client_order_id(SYMBOL, T0, 'buy', 'open')}#0",
        f"{client_order_id(SYMBOL, T0, 'buy', 'open')}#1",
    }
    # อ่านหน้าต่างเดิมซ้ำต้องไม่เพิ่มแถว — `seq` นับจากลำดับในรายการ ไม่ใช่จากแถวที่มีอยู่
    feed.at(102)
    broker.open_orders(SYMBOL)
    assert len(fills_rows(clean)) == 2


@pytest.mark.db
def test_a_trade_whose_order_fell_outside_the_window_is_loud_not_silent(clean, caplog):
    """"ไม่รู้จัก" กับ "ไม่ใช่ของเรา" เป็นคนละเรื่อง — อันแรกอาจเป็นขาปิดที่ ledger จะไม่มีวันรู้"""
    venue = FakeVenue()
    feed = ReplayBars()
    feed.at(100)
    venue.trades.append({
        "id": "t99", "order": "9999", "symbol": f"{SYMBOL}:USDT", "side": "sell",
        "price": 40_000.0, "amount": 0.5, "timestamp": T0, "fee": None,
    })

    with caplog.at_level("WARNING"):
        broker_of(clean, venue, feed).open_orders(SYMBOL)

    assert fills_rows(clean) == []
    assert any("9999" in message for message in caplog.messages)


def test_a_venue_that_fails_while_moving_a_stop_does_not_take_the_whole_bar_down():
    """ขั้นนี้อยู่นอก `_execute` — ข้อผิดพลาดที่ทะลุขึ้นไปทำให้ทั้งแท่งไม่มีแถวบันทึกเลย

    ผลที่ตามมาไม่ใช่แค่ "ไม่มีบันทึก": `loop._run_bar_cycle` นับว่าแท่งนั้นทำแล้ว ขาปิด
    ตามสัญญาณของแท่งนั้นจึงไม่ได้ยิงและไม่ถูกลองใหม่ · กับ ccxt นี่คือวันเน็ตไม่ดีธรรมดา
    """
    from cane.engine.pipeline import _slow_trail
    from cane.execution.broker import OpenOrder, Position

    class Refusing:
        market = PERP

        def replace(self, order_id, stop_px):
            raise BrokerError("venue ไม่ตอบ")

    existing = OpenOrder(
        venue_order_id="7", client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"),
        symbol=SYMBOL, side="sell", type="stop_market", qty=0.5, stop_px=38_000.0,
        reduce_only=True,
    )
    position = Position(
        symbol=SYMBOL, side="long", qty=0.5, entry_px=40_000.0, mark_px=40_000.0,
        unrealized_pnl=0, leverage=2.0, liquidation_px=20_000.0,
    )
    sym = SymbolRuntime(cfg=PERP_CFG, bars=ReplayBars(), broker=Refusing())

    stop, failed = _slow_trail(None, None, sym, position, [existing], 39_000.0, T0)

    # stop ใบเดิมยังคุ้มไม้อยู่ที่ราคาเดิม — นั่นคือความจริงเรื่องสถานะที่ปลายทาง
    assert stop.action == "unchanged" and stop.px == 38_000.0
    # ส่วนเหตุผลที่มันไม่ขยับอยู่ในแถวออเดอร์ที่ล้ม ไม่ใช่ในคำว่า `unchanged`
    assert failed is not None and failed.leg == "stop" and failed.accepted is False
    assert failed.stop_px == 39_000.0 and "venue ไม่ตอบ" in failed.error
