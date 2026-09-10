"""`PaperBroker` — เกณฑ์เสร็จของใบ 11

ใบสั่งไว้สี่ข้อ: เปิด/ปิดได้ทั้งสองฝั่ง, คิด P&L ถูก, จำลอง liquidation ได้, หัก
funding ได้ · ทั้งสี่อยู่ในไฟล์นี้พร้อมตัวเลขที่คำนวณด้วยมือ ไม่ใช่ตัวเลขที่ลอกมา
จากผลรันของโค้ดเอง

**เทสต์แตะ DB เพราะ broker เขียน ledger จริง** — นั่นคือสิ่งที่ทำให้มันต่างจาก mock
ตัวหนึ่ง fixture `db` rollback ทุกอย่างท้ายเทสต์ broker จึงเขียนในทรานแซกชันของเทสต์
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cane.data.ohlcv import Bar
from cane.db.repo import ledger as ledger_repo
from cane.execution import Order, PaperBroker, PaperError, client_order_id

pytestmark = pytest.mark.db

T0 = 1_787_961_600_000
DAY_MS = 86_400_000
SYMBOL = "BTC/USDT"

SEED = 10_000.0
FEE_PCT = 0.05
MMR_PCT = 0.4
LEVERAGE = 2.0


class FakeBars:
    """`BarSource` ที่เทสต์ควบคุมได้ — ผู้เรียกเติมแท่งเองทีละแท่ง"""

    def __init__(self):
        self._rows: dict[str, list[Bar]] = {}

    def add(self, symbol, close_ts, *, o, h, l, c):  # noqa: E741
        self._rows.setdefault(symbol, []).append(
            Bar(open_ts=close_ts - DAY_MS, close_ts=close_ts, open=o, high=h, low=l,
                close=c, volume=1.0)
        )

    def flat(self, symbol, close_ts, px):
        self.add(symbol, close_ts, o=px, h=px, l=px, c=px)

    def bars(self, symbol, timeframe):
        return list(self._rows.get(symbol, []))


def no_funding(symbol, after_ts, through_ts):
    return []


def perp(db, bars, funding_source=no_funding, **overrides):
    kwargs = dict(
        conn=db,
        market="usdtm_perp",
        profile="paper",
        bars=bars,
        timeframe="1d",
        seed_quote=SEED,
        taker_fee_pct=FEE_PCT,
        maintenance_margin_pct=MMR_PCT,
        funding_source=funding_source,
    )
    broker = PaperBroker(**{**kwargs, **overrides})
    if broker.market == "usdtm_perp":
        broker.set_leverage(SYMBOL, LEVERAGE)
    return broker


def buy(qty=2.0, ts=T0, leg="open", reduce_only=False):
    return Order(
        symbol=SYMBOL, side="buy", type="market", qty=qty,
        client_order_id=client_order_id(SYMBOL, ts, "buy", leg),
        reduce_only=reduce_only,
    )


def sell(qty=2.0, ts=T0, leg="close", reduce_only=True):
    return Order(
        symbol=SYMBOL, side="sell", type="market", qty=qty,
        client_order_id=client_order_id(SYMBOL, ts, "sell", leg),
        reduce_only=reduce_only,
    )


# ── ประตูตอนสร้าง ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "missing", ["seed_quote", "taker_fee_pct", "maintenance_margin_pct"]
)
def test_the_broker_will_not_construct_without_the_simulation_numbers(db, missing):
    """ประตูที่ migration 0005 ตั้งใจให้อยู่ตรงนี้ ไม่ใช่ที่ NOT NULL ของคอลัมน์

    เวอร์ชัน config ที่ seed ไว้ก่อนสองค่านี้จะมีอยู่ต้องอ่านกลับได้ ส่วนการ *ใช้*
    มันต้องมีค่าครบ — fail-closed ตรงจุดที่ค่าถูกใช้
    """
    with pytest.raises(PaperError, match=missing):
        perp(db, FakeBars(), **{missing: None})


def test_a_perp_broker_without_a_funding_source_will_not_construct(db):
    """เดารอบและอัตราเองจะทำให้ P&L ต่างจากของจริงด้วยตัวเลขที่ไม่มีใครกรอก"""
    with pytest.raises(PaperError, match="funding_source"):
        perp(db, FakeBars(), funding_source=None)


def test_a_spot_broker_needs_no_funding_source_because_spot_has_no_funding(db):
    """ไม่ใช่การผ่อนกฎ — spot ไม่มี funding อยู่จริง (spec/03:22, ADR 26)"""
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = PaperBroker(
        conn=db, market="spot", profile="paper", bars=bars, timeframe="1d",
        seed_quote=SEED, taker_fee_pct=FEE_PCT, maintenance_margin_pct=MMR_PCT,
    )

    assert broker.balance().free == Decimal("10000.00000000")


# ── เปิดและปิดทั้งสองฝั่ง พร้อม P&L ที่คำนวณด้วยมือ ──────────────────────────


def test_a_long_round_trip_moves_the_money_the_way_the_arithmetic_says(db):
    """ซื้อ 2 ที่ 100 ขาย 2 ที่ 110 · leverage 2 · fee 0.05% · seed 10000

    เปิด : notional 200 → margin 100 · fee 200 × 0.0005 = 0.10
           cash = 10000 − 100 − 0.10 = 9899.90 · used = 100
    ปิด  : pnl (110 − 100) × 2 = +20 · fee 220 × 0.0005 = 0.11
           cash = 9899.90 + 100 + 20 − 0.11 = 10019.79
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)

    broker.place(buy())

    assert broker.balance().free == Decimal("9899.90000000")
    assert broker.balance().used == Decimal("100.00000000")
    held = broker.positions()[0]
    assert (held.side, held.qty, held.entry_px) == ("long", 2.0, 100.0)

    bars.flat(SYMBOL, T0 + DAY_MS, 110.0)
    broker.place(sell(ts=T0 + DAY_MS))

    assert broker.balance().free == Decimal("10019.79000000")
    assert broker.positions() == []


def test_a_short_round_trip_earns_when_the_price_falls(db):
    """ขาย 2 ที่ 100 ซื้อคืน 2 ที่ 90 · pnl (100 − 90) × 2 = +20

    เปิด : cash = 10000 − 100 − 0.10 = 9899.90
    ปิด  : fee 180 × 0.0005 = 0.09 → 9899.90 + 100 + 20 − 0.09 = 10019.81
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)

    broker.place(sell(leg="open", reduce_only=False))
    assert broker.positions()[0].side == "short"

    bars.flat(SYMBOL, T0 + DAY_MS, 90.0)
    broker.place(buy(ts=T0 + DAY_MS, leg="close", reduce_only=True))

    assert broker.balance().free == Decimal("10019.81000000")


def test_the_round_trip_reads_back_from_the_ledger_as_two_legs(db):
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())
    bars.flat(SYMBOL, T0 + DAY_MS, 110.0)
    broker.place(sell(ts=T0 + DAY_MS))

    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    rows = ledger_repo.fills_of_trade(db, "paper", trade)

    assert [row.leg for row in rows] == ["open", "close"]
    assert rows[0].exit_reason is None and rows[1].exit_reason == "signal"
    assert rows[1].position_qty_after == 0.0
    # ไม่มี slippage ให้อ้างอิงในโหมดนี้ — รายงานต้องเป็นศูนย์อย่างซื่อสัตย์
    assert [row.ref_px for row in rows] == [row.px for row in rows]


def test_opening_without_setting_leverage_first_is_refused(db):
    """spec/08 บังคับให้ตั้ง leverage ที่ปลายทางก่อนเปิดไม้ทุกครั้ง

    เพราะค่าที่ exchange ถูกเปลี่ยนจากนอกระบบได้ · จำลองก็ต้องบังคับเหมือนกัน
    ไม่งั้น paper จะผ่านเส้นทางที่ live ล้ม
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = PaperBroker(
        conn=db, market="usdtm_perp", profile="paper", bars=bars, timeframe="1d",
        seed_quote=SEED, taker_fee_pct=FEE_PCT, maintenance_margin_pct=MMR_PCT,
        funding_source=no_funding,
    )

    with pytest.raises(PaperError, match="set_leverage"):
        broker.place(buy())


def test_there_is_no_pyramiding_and_no_opening_against_the_position(db):
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())

    with pytest.raises(PaperError, match="pyramiding"):
        broker.place(buy(ts=T0 + DAY_MS))


def test_closing_more_than_is_held_is_refused(db):
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy(qty=2.0))

    with pytest.raises(PaperError, match="ปิดเกิน"):
        broker.place(sell(qty=3.0, ts=T0 + DAY_MS))


# ── stop ที่วางไว้ที่ปลายทาง ────────────────────────────────────────────────


def test_a_stop_that_fires_is_a_fill_not_an_event(db):
    """เส้นทาง cold start ทางที่ 2 ต้องทดสอบใน paper ได้ ไม่งั้นขัด ADR 9

    stop ที่ 95 · แท่งถัดมาเปิด 99 ต่ำสุด 94 → ราคาไหลผ่าน 95 ระหว่างแท่ง
    pnl (95 − 100) × 2 = −10 · fee 190 × 0.0005 = 0.095
    cash = 9899.90 + 100 − 10 − 0.095 = 9989.805
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())
    broker.place(
        Order(symbol=SYMBOL, side="sell", type="stop_market", qty=2.0, stop_px=95.0,
              reduce_only=True,
              client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"))
    )
    assert len(broker.open_orders(SYMBOL)) == 1

    bars.add(SYMBOL, T0 + DAY_MS, o=99.0, h=99.0, l=94.0, c=96.0)

    # ไม่มีใครบอก broker ว่าแท่งใหม่มาแล้ว — การอ่านสถานะเป็นตัวเดินเวลาให้
    assert broker.positions() == []
    assert broker.open_orders(SYMBOL) == []
    assert broker.balance().free == Decimal("9989.80500000")

    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    stop_fill = ledger_repo.fills_of_trade(db, "paper", trade)[-1]
    assert (stop_fill.leg, stop_fill.exit_reason) == ("stop", "stop")
    assert stop_fill.px == 95.0


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop_price(db):
    """แท่งที่เปิดมาต่ำกว่า stop แล้วแปลว่าไม่มีใครได้ราคานั้น

    การ fill ที่ `stop_px` จะทำให้ paper รายงานผลดีกว่าความจริงตรงจุดที่เจ็บที่สุด
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())
    broker.place(
        Order(symbol=SYMBOL, side="sell", type="stop_market", qty=2.0, stop_px=95.0,
              reduce_only=True,
              client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"))
    )

    bars.add(SYMBOL, T0 + DAY_MS, o=90.0, h=91.0, l=88.0, c=89.0)
    broker.positions()

    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    assert ledger_repo.fills_of_trade(db, "paper", trade)[-1].px == 90.0


def test_replace_moves_the_stop_without_a_window_where_nothing_guards_the_trade(db):
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())
    armed = broker.place(
        Order(symbol=SYMBOL, side="sell", type="stop_market", qty=2.0, stop_px=95.0,
              reduce_only=True,
              client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"))
    )

    broker.replace(armed.venue_order_id, 97.0)

    still_armed = broker.open_orders(SYMBOL)
    assert len(still_armed) == 1 and still_armed[0].stop_px == 97.0


# ── liquidation ─────────────────────────────────────────────────────────────


def test_the_liquidation_price_follows_the_isolated_margin_formula(db):
    """long · entry 100 · leverage 2 · mmr 0.4% → 100 × (1 − (0.5 − 0.004)) = 50.4"""
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())

    assert broker.positions()[0].liquidation_px == pytest.approx(50.4)


def test_a_position_that_reaches_liquidation_is_closed_by_the_exchange(db):
    """ทางออกที่ระบบไม่ได้สั่ง — ต้องแยกจาก `stop` ให้เห็น (spec/06:63)"""
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())

    bars.add(SYMBOL, T0 + DAY_MS, o=60.0, h=61.0, l=50.0, c=52.0)

    assert broker.positions() == []
    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    last = ledger_repo.fills_of_trade(db, "paper", trade)[-1]
    assert last.exit_reason == "liquidation"
    assert last.px == pytest.approx(50.4)


def test_when_both_levels_are_inside_one_bar_the_stop_fires_first(db):
    """ไม่ใช่การเดา แต่มาจากลำดับของราคาเอง — ของ long นั้น stop สูงกว่า liq เสมอ

    เมื่อ `min_liq_buffer_pct` ทำงานถูกต้อง ราคาที่ไหลลงจึงผ่าน stop ก่อน
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())
    broker.place(
        Order(symbol=SYMBOL, side="sell", type="stop_market", qty=2.0, stop_px=95.0,
              reduce_only=True,
              client_order_id=client_order_id(SYMBOL, T0, "sell", "stop"))
    )

    bars.add(SYMBOL, T0 + DAY_MS, o=99.0, h=99.0, l=40.0, c=45.0)
    broker.positions()

    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    last = ledger_repo.fills_of_trade(db, "paper", trade)[-1]
    assert (last.exit_reason, last.px) == ("stop", 95.0)


# ── funding ─────────────────────────────────────────────────────────────────


def test_funding_is_charged_once_per_cycle_and_leaves_the_wallet(db):
    """long จ่ายเมื่ออัตราเป็นบวก · 0.0001 × 110 × 2 = 0.022"""
    cycles = {SYMBOL: [(T0 + DAY_MS, 0.0001)]}

    def source(symbol, after_ts, through_ts):
        return [(ts, rate) for ts, rate in cycles[symbol] if after_ts < ts <= through_ts]

    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars, funding_source=source)
    broker.place(buy())
    before = broker.balance().free

    bars.flat(SYMBOL, T0 + DAY_MS, 110.0)
    broker.positions()

    assert broker.balance().free == before - Decimal("0.02200000")

    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    charges = ledger_repo.funding_charges_of_trade(db, "paper", trade)
    assert len(charges) == 1
    assert charges[0].amount_quote == Decimal("0.02200000")

    # อ่านสถานะซ้ำในแท่งเดิมต้องไม่หักซ้ำ — `settled_through_ts` กันรอบเดิม
    broker.positions()
    assert len(ledger_repo.funding_charges_of_trade(db, "paper", trade)) == 1


def test_a_short_receives_funding_when_the_rate_is_positive(db):
    def source(symbol, after_ts, through_ts):
        return [(T0 + DAY_MS, 0.0001)] if after_ts < T0 + DAY_MS <= through_ts else []

    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars, funding_source=source)
    broker.place(sell(leg="open", reduce_only=False))
    before = broker.balance().free

    bars.flat(SYMBOL, T0 + DAY_MS, 110.0)
    broker.positions()

    assert broker.balance().free == before + Decimal("0.02200000")


def test_a_cycle_with_no_rate_is_written_down_rather_than_skipped(db):
    """"ไม่รู้อัตรา" ต้องเหลือร่องรอย ไม่ใช่หายไปเงียบๆ เหมือนไม่มีรอบนั้น"""
    def source(symbol, after_ts, through_ts):
        return [(T0 + DAY_MS, None)] if after_ts < T0 + DAY_MS <= through_ts else []

    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars, funding_source=source)
    broker.place(buy())
    before = broker.balance().free

    bars.flat(SYMBOL, T0 + DAY_MS, 110.0)
    broker.positions()

    assert broker.balance().free == before
    trade = ledger_repo.trade_id_of("usdtm_perp", SYMBOL, "long", T0)
    charge = ledger_repo.funding_charges_of_trade(db, "paper", trade)[0]
    assert charge.amount_quote is None and charge.unavailable_reason is not None


# ── spot ────────────────────────────────────────────────────────────────────


def spot_broker(db, bars):
    return PaperBroker(
        conn=db, market="spot", profile="paper", bars=bars, timeframe="1d",
        seed_quote=SEED, taker_fee_pct=FEE_PCT, maintenance_margin_pct=MMR_PCT,
    )


def test_spot_pays_the_whole_notional_and_has_no_liquidation(db):
    """ซื้อ 2 ที่ 100 บน spot — ไม่มี leverage เงินออกเต็มจำนวน

    cash = 10000 − 200 − 0.10 = 9799.90 · `liquidation_px` เป็น `None` เพราะ
    ตลาดนี้**ไม่มี** liquidation ไม่ใช่มีแล้วไกลมาก (ADR 26)
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = spot_broker(db, bars)

    broker.place(buy())

    assert broker.balance().free == Decimal("9799.90000000")
    held = broker.positions()[0]
    assert held.liquidation_px is None and held.leverage == 1.0


def test_spot_cannot_sell_more_than_it_holds(db):
    """ขายคือขายของที่มี ไม่ใช่การเปิดฝั่งใหม่"""
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = spot_broker(db, bars)
    broker.place(buy(qty=2.0))

    with pytest.raises(PaperError, match="ปิดเกิน"):
        broker.place(sell(qty=5.0, ts=T0 + DAY_MS, reduce_only=False))


def test_on_spot_a_sell_is_never_a_new_short_it_is_selling_what_is_held(db):
    """ไม่มี `if` ไหนปฏิเสธ "spot short" — **เส้นทาง**เป็นตัวปฏิเสธ

    คำสั่ง `sell` บน spot ถูกส่งเข้าทางปิดเสมอ ขายตอนไม่มีของจึงล้มด้วย "ไม่มีไม้
    ให้ปิด" ไม่ใช่ "ห้าม short" · เขียนเป็นเทสต์ไว้เพราะมันคือเหตุผลที่กิ่ง
    `side == "short"` ใน `_open()` ไปไม่ถึงบน spot และไม่ควรมีใครไปเติมกลับ
    """
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = spot_broker(db, bars)

    with pytest.raises(PaperError, match="ไม่มีไม้ให้ปิด"):
        broker.place(sell(reduce_only=False))

    with pytest.raises(PaperError, match="leverage"):
        broker.set_leverage(SYMBOL, 2.0)


def test_a_spot_fill_never_carries_a_perp_only_field(db):
    """CHECK ของ `fills` ปฏิเสธอยู่แล้ว — เทสต์นี้ยืนยันว่า broker ไม่ยิงไปให้ปฏิเสธ"""
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = spot_broker(db, bars)
    broker.place(buy())

    trade = ledger_repo.trade_id_of("spot", SYMBOL, "long", T0)
    row = ledger_repo.fills_of_trade(db, "paper", trade)[0]

    assert row.reduce_only is False
    assert row.leverage is None


# ── การอ่านสถานะเป็นตัวเดินเวลา ─────────────────────────────────────────────


def test_the_mark_price_follows_the_latest_closed_bar(db):
    """`max_daily_loss_pct` นับ mark-to-market จึงต้องได้ราคาล่าสุดเสมอ (spec/06)"""
    bars = FakeBars()
    bars.flat(SYMBOL, T0, 100.0)
    broker = perp(db, bars)
    broker.place(buy())

    bars.flat(SYMBOL, T0 + DAY_MS, 105.0)
    held = broker.positions()[0]

    assert held.mark_px == 105.0
    assert held.unrealized_pnl == Decimal("10.00000000")


def test_placing_an_order_with_no_closed_bar_to_price_it_is_refused(db):
    broker = perp(db, FakeBars())

    with pytest.raises(PaperError, match="ไม่มีแท่ง"):
        broker.place(buy())
