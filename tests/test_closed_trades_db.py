"""VIEW `closed_trades` — ไม้ที่ปิดแล้วต้องคิดจาก ledger ได้ถูก และต้นทุนที่หายต้องหายเสียงดัง

ตัวเลขทุกตัวในไฟล์นี้คิดด้วยมือไว้ในคอมเมนต์ข้างเคส · VIEW ที่คำนวณผิดแต่เทสต์เทียบกับ
ผลของ VIEW เองจะเขียวตลอดไป จึงต้องเทียบกับเลขที่ได้จากนอก SQL
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from cane.db.repo import ledger as repo
from cane.db.repo.ledger import Fill, FundingCharge, dedupe_key_of, trade_id_of
from cane.execution import client_order_id

pytestmark = pytest.mark.db

#: เที่ยงคืน UTC จริง จึงตรงกับกริด funding 8 ชม. พอดี
T0 = 1_787_961_600_000
DAY_MS = 86_400_000
EIGHT_H = 8 * 60 * 60 * 1000

PERP = "usdtm_perp"
SYMBOL = "BTC/USDT"


def _fill(side: str, leg: str, *, market: str = PERP, seq: int = 0, **overrides) -> Fill:
    opening = leg == "open"
    bar = T0 if opening else T0 + DAY_MS
    order_side = "buy" if opening == (side == "long") else "sell"
    coid = client_order_id(SYMBOL, bar, order_side, leg)
    base = dict(
        profile="live",
        market=market,
        symbol=SYMBOL,
        trade_id=trade_id_of(market, SYMBOL, side, T0),
        leg=leg,
        fill_ts=bar + 500,
        px=100.0 if opening else 110.0,
        qty=2.0,
        client_order_id=coid,
        order_type="market",
        reduce_only=not opening and market == PERP,
        position_qty_after=2.0 if opening else 0.0,
        bar_close_ts=bar,
        dedupe_key=dedupe_key_of(coid, seq),
        ref_px=100.0 if opening else 110.0,
        fee_quote=Decimal("0.10"),
        fee_ccy="USDT",
        leverage=None if market != PERP else 2.0,
        exit_reason=None if opening else "signal",
    )
    return Fill(**{**base, **overrides})


def _funding(side: str, cycle_ts: int, amount: str | None = "0.05", **overrides) -> FundingCharge:
    base = dict(
        profile="live",
        symbol=SYMBOL,
        trade_id=trade_id_of(PERP, SYMBOL, side, T0),
        cycle_ts=cycle_ts,
        position_qty=2.0,
        rate=None if amount is None else 0.0001,
        amount_quote=None if amount is None else Decimal(amount),
        unavailable_reason=None if amount is not None else "venue ไม่ได้ให้อัตราของรอบนี้",
    )
    return FundingCharge(**{**base, **overrides})


#: เข้า T0+500 ออก T0+1d+500 → รอบที่ T0+8h, T0+16h, T0+24h = 3 รอบ
CYCLES = (T0 + EIGHT_H, T0 + 2 * EIGHT_H, T0 + 3 * EIGHT_H)


def _only(db, profile: str = "live") -> repo.ClosedTrade:
    trades = repo.closed_trades(db, profile)
    assert len(trades) == 1
    return trades[0]


def test_a_long_round_trip_nets_price_minus_fee_minus_funding(db):
    # ซื้อ 2 @100 อ้างอิง 99.5 → slippage 2 × 0.5 = 1.0
    # ขาย 2 @110 อ้างอิง 110.2 → slippage 2 × 0.2 = 0.4
    repo.record_fill(db, _fill("long", "open", ref_px=99.5))
    repo.record_fill(db, _fill("long", "close", ref_px=110.2, fee_quote=Decimal("0.11")))
    for ts in CYCLES:
        repo.record_funding_charge(db, _funding("long", ts))

    trade = _only(db)

    assert trade.side == "long"
    assert (trade.entry_px, trade.exit_px, trade.qty) == (100.0, 110.0, 2.0)
    assert trade.pnl_px_quote == Decimal("20")
    assert trade.slippage_quote == Decimal("1.4")
    assert trade.fee_quote == Decimal("0.21")
    assert trade.funding_quote == Decimal("0.15")
    # gross = 20 + 1.4 · net = 20 - 0.21 - 0.15
    assert trade.gross_quote == Decimal("21.4")
    assert trade.net_quote == Decimal("19.64")
    # % บน notional ขาเข้า 200 ไม่ใช่มาร์จิ้น 100
    assert trade.net_pct == pytest.approx(9.82)
    assert trade.gross_pct == pytest.approx(10.7)
    assert trade.funding_cycles_expected == 3
    assert trade.funding_cycles_missing == 0
    assert trade.cost_complete is True
    assert trade.exit_reason == "signal"
    assert trade.open_bar_close_ts == T0 and trade.close_bar_close_ts == T0 + DAY_MS


def test_a_short_gains_when_it_buys_back_lower(db):
    # ขาย 2 @100 ซื้อคืน 2 @90 → +20 · ขาปิดซื้อแพงกว่าอ้างอิง 0.1 → slippage 0.2
    repo.record_fill(db, _fill("short", "open"))
    repo.record_fill(db, _fill("short", "close", px=90.0, ref_px=89.9))
    for ts in CYCLES:
        repo.record_funding_charge(db, _funding("short", ts, amount="-0.05"))

    trade = _only(db)

    assert trade.side == "short"
    assert trade.pnl_px_quote == Decimal("20")
    assert trade.slippage_quote == Decimal("0.2")
    # short ได้รับ funding (ยอดติดลบ = เข้ากระเป๋า) จึงบวกเข้า net
    assert trade.net_quote == Decimal("20") - Decimal("0.2") + Decimal("0.15")
    assert trade.cost_complete is True


def test_a_stop_leg_closes_a_trade_the_same_as_a_close_leg(db):
    repo.record_fill(db, _fill("long", "open"))
    repo.record_fill(
        db,
        _fill("long", "stop", px=95.0, ref_px=96.0, order_type="stop_market", exit_reason="stop"),
    )

    trade = _only(db)

    assert trade.exit_reason == "stop"
    assert trade.pnl_px_quote == Decimal("-10")


def test_partial_fills_are_priced_by_their_weighted_average(db):
    # ขาเข้าสองก้อน 1 @100 + 1 @104 → เฉลี่ย 102
    repo.record_fill(db, _fill("long", "open", qty=1.0, position_qty_after=1.0))
    repo.record_fill(
        db,
        _fill("long", "open", seq=1, qty=1.0, px=104.0, position_qty_after=2.0, fill_ts=T0 + 600),
    )
    repo.record_fill(db, _fill("long", "close"))

    trade = _only(db)

    assert trade.entry_px == pytest.approx(102.0)
    assert trade.entry_notional == Decimal("204")
    assert trade.pnl_px_quote == Decimal("16")


def test_a_trade_still_open_is_not_a_closed_trade(db):
    repo.record_fill(db, _fill("long", "open"))

    assert repo.closed_trades(db, "live") == []


def test_a_trade_closed_only_partly_is_not_a_closed_trade(db):
    """ของค้างจาก `flip_aborted` (ADR 19) ยังไม่ใช่ไม้ที่ปิดแล้ว"""
    repo.record_fill(db, _fill("long", "open"))
    repo.record_fill(db, _fill("long", "close", qty=1.5, position_qty_after=0.5))

    assert repo.closed_trades(db, "live") == []


def test_a_funding_cycle_that_was_never_recorded_marks_the_cost_incomplete(db):
    repo.record_fill(db, _fill("long", "open"))
    repo.record_fill(db, _fill("long", "close"))
    for ts in CYCLES[:2]:
        repo.record_funding_charge(db, _funding("long", ts))

    trade = _only(db)

    assert trade.funding_cycles_expected == 3
    assert trade.funding_cycles_recorded == 2
    assert trade.funding_cycles_missing == 1
    assert trade.cost_complete is False


def test_a_funding_cycle_recorded_without_an_amount_marks_the_cost_incomplete(db):
    repo.record_fill(db, _fill("long", "open"))
    repo.record_fill(db, _fill("long", "close"))
    for ts in CYCLES[:2]:
        repo.record_funding_charge(db, _funding("long", ts))
    repo.record_funding_charge(db, _funding("long", CYCLES[2], amount=None))

    trade = _only(db)

    assert trade.funding_cycles_unavailable == 1
    assert trade.funding_cycles_missing == 0
    assert trade.cost_complete is False


@pytest.mark.parametrize(
    "fee",
    [
        dict(fee_quote=None, fee_ccy=None, fee_unavailable_reason="venue ไม่คืนค่าธรรมเนียม"),
        # ค่าธรรมเนียมเป็น BNB บวกเข้ายอด USDT ไม่ได้ — ไม่รู้ ไม่ใช่ศูนย์
        dict(fee_quote=Decimal("0.0002"), fee_ccy="BNB"),
    ],
    ids=["unknown", "foreign-currency"],
)
def test_a_fee_that_cannot_be_counted_in_quote_marks_the_cost_incomplete(db, fee):
    repo.record_fill(db, _fill("long", "open", **fee))
    repo.record_fill(db, _fill("long", "close"))
    for ts in CYCLES:
        repo.record_funding_charge(db, _funding("long", ts))

    trade = _only(db)

    assert trade.fee_missing_fills == 1
    assert trade.fee_quote == Decimal("0.1")
    assert trade.cost_complete is False


def test_a_missing_reference_price_flags_slippage_but_not_the_net(db):
    """net ไม่ได้พึ่ง `ref_px` — ราคาที่ fill จริงรวม slippage ไว้แล้ว"""
    repo.record_fill(db, _fill("long", "open", ref_px=None))
    repo.record_fill(db, _fill("long", "close"))
    for ts in CYCLES:
        repo.record_funding_charge(db, _funding("long", ts))

    trade = _only(db)

    assert trade.slippage_missing_fills == 1
    assert trade.cost_complete is True


def test_spot_expects_no_funding_at_all(db):
    repo.record_fill(db, _fill("long", "open", market="spot"))
    repo.record_fill(db, _fill("long", "close", market="spot"))

    trade = _only(db)

    assert trade.market == "spot"
    assert trade.funding_cycles_expected == 0
    assert trade.cost_complete is True


def test_trades_are_kept_to_their_own_profile(db):
    repo.record_fill(db, _fill("long", "open", profile="paper"))
    repo.record_fill(db, _fill("long", "close", profile="paper"))

    assert repo.closed_trades(db, "live") == []
    assert len(repo.closed_trades(db, "paper")) == 1


@pytest.mark.parametrize("role", ["cane_console", "cane_engine"])
def test_both_roles_can_read_the_view(db, role):
    with db.begin_nested():
        db.execute(text(f'SET LOCAL ROLE "{role}"'))
        db.execute(text("SELECT count(*) FROM closed_trades")).scalar_one()


def test_the_view_cannot_be_written_through(db):
    """VIEW ไม่ได้เปิดทางอ้อมให้แก้ ledger

    ฐานปฏิเสธสองชั้น: ไม่มีใครได้ `DELETE` บน VIEW และ VIEW ที่มี `WITH` ก็ไม่
    auto-updatable ตั้งแต่แรก · ชั้นไหนปฏิเสธก่อนไม่สำคัญ ขอให้ปฏิเสธ
    """
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            db.execute(text('SET LOCAL ROLE "cane_console"'))
            db.execute(text("DELETE FROM closed_trades"))
