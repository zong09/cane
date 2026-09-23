"""`report.summarize()` — ตัวเลขรวมของหน้ารายงาน เทียบกับเลขที่คิดด้วยมือ"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cane.db.repo.ledger import ClosedTrade
from cane.db.repo.report import Origin
from cane.report import summarize

DAY_MS = 86_400_000
T0 = 1_787_961_600_000


def trade(n: int, net: str, *, side: str = "long", symbol: str = "BTC/USDT",
          gross: str | None = None, fee: str = "0", slip: str = "0", funding: str = "0",
          notional: str = "100", complete: bool = True) -> ClosedTrade:
    net_d = Decimal(net)
    return ClosedTrade(
        profile="live", market="usdtm_perp", symbol=symbol,
        trade_id=f"usdtm_perp:{symbol}:{side}:{T0 + n * DAY_MS}",
        side=side, open_bar_close_ts=T0 + n * DAY_MS,
        close_bar_close_ts=T0 + (n + 1) * DAY_MS,
        entry_ts=T0 + n * DAY_MS, exit_ts=T0 + (n + 1) * DAY_MS,
        qty=1.0, entry_px=100.0, exit_px=100.0, entry_notional=Decimal(notional),
        leverage=2.0, exit_reason="signal", exit_detail=None,
        pnl_px_quote=net_d, slippage_quote=Decimal(slip), slippage_missing_fills=0,
        fee_quote=Decimal(fee), fee_missing_fills=0, funding_quote=Decimal(funding),
        funding_cycles_expected=3, funding_cycles_recorded=3,
        funding_cycles_unavailable=0, funding_cycles_missing=0,
        gross_quote=Decimal(gross if gross is not None else net), net_quote=net_d,
        gross_pct=0.0, net_pct=float(net_d / Decimal(notional) * 100),
        cost_complete=complete,
    )


def origin(version: int = 1) -> Origin:
    return Origin(config_version_id=version, size_pct=25.0, on_signal=True, cold_start=None)


def test_the_total_is_divided_by_the_capital_not_summed_from_per_trade_percents():
    # +10 บน notional 20 (= +50%) กับ -5 บน notional 1000 (= -0.5%) · ทุน 500
    # ผลรวมที่ถูก: +5 / 500 = +1% · ถ้าบวก % ต่อไม้จะได้ +49.5% ซึ่งผิดทั้งหมด
    trades = [trade(0, "10", notional="20"), trade(1, "-5", notional="1000")]
    origins = {t.trade_id: origin() for t in trades}

    s = summarize(trades, origins, {1: Decimal("500")})

    assert s.capital == Decimal("500")
    assert s.net_quote == Decimal("5")
    assert s.net_pct == pytest.approx(1.0)


def test_sides_split_the_total_into_points_that_add_back_up():
    trades = [trade(0, "30"), trade(1, "-6", side="short")]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("600")})

    assert s.long_pts == pytest.approx(5.0)
    assert s.short_pts == pytest.approx(-1.0)
    assert s.long_pts + s.short_pts == pytest.approx(s.net_pct)
    assert (s.longs, s.shorts) == (1, 1)


def test_wins_and_average_results_use_net_not_gross():
    # ไม้ที่สองกำไรก่อนหักต้นทุน แต่ขาดทุนหลังหัก — นับเป็นไม้แพ้
    trades = [trade(0, "10", notional="100"), trade(1, "-1", gross="2", notional="100")]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("100")})

    assert s.wins == 1
    assert s.avg_win_pct == pytest.approx(10.0)
    assert s.avg_loss_pct == pytest.approx(-1.0)


def test_drawdown_is_the_deepest_fall_from_the_running_peak():
    # สะสม: +10 → +4 → +7 → -1 · ยอดสูงสุด +10 · ลึกสุด -1 - 10 = -11 ที่ไม้ที่สี่
    trades = [trade(0, "10"), trade(1, "-6"), trade(2, "3"), trade(3, "-8")]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("100")})

    assert s.drawdown_pct == pytest.approx(-11.0)
    assert s.drawdown_ts == trades[3].exit_ts


def test_costs_are_counted_in_points_of_the_capital():
    trades = [trade(0, "10", fee="0.5", slip="0.3", funding="0.2")]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("200")})

    assert s.fee_quote + s.slippage_quote + s.funding_quote == Decimal("1.0")
    assert s.cost_pts == pytest.approx(0.5)


def test_the_equity_curve_accumulates_in_exit_order():
    trades = [trade(1, "-2", gross="-1"), trade(0, "5", gross="6")]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("100")})

    assert [(p.net_pct, p.gross_pct) for p in s.equity] == [
        pytest.approx((5.0, 6.0)), pytest.approx((3.0, 5.0))
    ]
    # ตารางไม้เรียงใหม่ → เก่า
    assert s.trades[0].exit_ts > s.trades[1].exit_ts


def test_two_capitals_in_the_range_give_no_single_percent_but_one_per_segment():
    """ทุนเปลี่ยนกลางช่วง → ไม่มีตัวหารตัวเดียวที่ถูก · ยอด USDT ยังตอบได้"""
    trades = [trade(0, "10"), trade(1, "6")]
    origins = {trades[0].trade_id: origin(1), trades[1].trade_id: origin(2)}

    s = summarize(trades, origins, {1: Decimal("100"), 2: Decimal("300")})

    assert s.capital is None
    assert s.net_pct is None and s.long_pts is None and s.drawdown_pct is None
    assert s.equity == ()
    assert s.net_quote == Decimal("16")
    assert [(seg.capital, seg.net_pct) for seg in s.segments] == [
        (Decimal("100"), pytest.approx(10.0)),
        (Decimal("300"), pytest.approx(2.0)),
    ]


def test_two_versions_with_the_same_capital_are_one_segment():
    trades = [trade(0, "10"), trade(1, "6")]
    origins = {trades[0].trade_id: origin(1), trades[1].trade_id: origin(2)}

    s = summarize(trades, origins, {1: Decimal("200"), 2: Decimal("200")})

    assert s.capital == Decimal("200")
    assert len(s.segments) == 1


def test_a_trade_with_no_origin_leaves_the_percent_unanswered():
    """หาแถวตัดสินไม่เจอ = ไม่รู้ทุน · ไม่หารด้วยทุนของไม้อื่นแทน"""
    trades = [trade(0, "10"), trade(1, "6")]
    s = summarize(trades, {trades[0].trade_id: origin()}, {1: Decimal("100")})

    assert s.capital is None
    assert s.segments[-1].capital is None and s.segments[-1].net_pct is None


def test_per_symbol_rows_add_up_to_the_total():
    trades = [trade(0, "10"), trade(1, "-4", symbol="ETH/USDT", side="short"),
              trade(2, "2", symbol="ETH/USDT")]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("200")})

    assert [r.symbol for r in s.per_symbol] == ["BTC/USDT", "ETH/USDT"]
    eth = s.per_symbol[1]
    assert (eth.trades, eth.wins, eth.longs, eth.shorts) == (2, 1, 1, 1)
    assert sum(r.net_pct for r in s.per_symbol) == pytest.approx(s.net_pct)


def test_trades_with_incomplete_costs_are_counted():
    trades = [trade(0, "10"), trade(1, "6", complete=False)]
    s = summarize(trades, {t.trade_id: origin() for t in trades}, {1: Decimal("100")})

    assert s.incomplete == 1


def test_no_trades_is_an_empty_summary_not_an_error():
    s = summarize([], {}, {})

    assert s.trades == () and s.segments == ()
    assert s.net_quote == 0 and s.net_pct is None
    assert s.avg_win_pct is None and s.drawdown_ts is None
