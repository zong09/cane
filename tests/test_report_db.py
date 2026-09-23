"""ตัวอ่านของหน้ารายงาน (`db/repo/report.py`) — ไม้ต้องหาแถวตัดสินและทุนของเวอร์ชันตัวเองเจอ"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cane.config import load_profile
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import ledger
from cane.db.repo import report as repo
from cane.db.repo.decisions import DecisionRecord, Flip, OrderAttempt
from cane.db.repo.ledger import Fill, dedupe_key_of, trade_id_of
from cane.db.schema import DECISION_TABLES, fills, funding_charges

pytestmark = pytest.mark.db

DAY_MS = 86_400_000
T0 = 1_787_961_600_000
PERP = "usdtm_perp"
BTC = "BTC/USDT"


@pytest.fixture(autouse=True)
def clean(db):
    """ตารางว่างในทรานแซกชันของเทสต์ — fixture `db` rollback ให้ท้ายเทสต์"""
    for table in (funding_charges, fills, *reversed(DECISION_TABLES)):
        db.execute(table.delete())


def version(db, *, long: float = 100.0, short: float = 60.0) -> int:
    settings = load_profile("config/live.toml")
    symbols = [s.model_copy(update={"bucket_quote_long": long, "bucket_quote_short": short})
               for s in settings.symbols]
    return config_repo.insert_version(
        db, settings.model_copy(update={"symbols": symbols}), source="migration", created_ts=T0
    ).id


def entered(version_id: int, bar: int, *, side: str = "long", long_signal: bool = True,
            **overrides) -> DecisionRecord:
    base = dict(
        profile="live", market=PERP, symbol=BTC, timeframe="1d",
        bar_close_ts=bar, decided_ts=bar + 500, config_version_id=version_id,
        close_px=100.0, zone="GREEN", state="BULLISH",
        long_signal=long_signal, short_signal=side == "short", dry_run=True,
        side=side, skip_reason="dry_run", size_pct_final=25.0,
        orders=(
            OrderAttempt(leg="open", order_side="buy" if side == "long" else "sell",
                         order_type="market", reduce_only=False, qty=1.0,
                         client_order_id=f"open-{bar}", sent=False, accepted=False),
        ),
    )
    return DecisionRecord(**{**base, **overrides})


def round_trip(db, bar: int, *, side: str = "long", close: bool = True) -> str:
    trade = trade_id_of(PERP, BTC, side, bar)
    common = dict(profile="live", market=PERP, symbol=BTC, trade_id=trade,
                  order_type="market", fee_quote=Decimal("0.1"), fee_ccy="USDT", leverage=2.0)
    ledger.record_fill(db, Fill(
        **common, leg="open", fill_ts=bar, px=100.0, qty=1.0, client_order_id=f"o-{bar}",
        reduce_only=False, position_qty_after=1.0, bar_close_ts=bar,
        dedupe_key=dedupe_key_of(f"o-{bar}"), ref_px=100.0,
    ))
    if close:
        ledger.record_fill(db, Fill(
            **common, leg="close", fill_ts=bar + DAY_MS, px=110.0, qty=1.0,
            client_order_id=f"c-{bar}", reduce_only=True, position_qty_after=0.0,
            bar_close_ts=bar + DAY_MS, dedupe_key=dedupe_key_of(f"c-{bar}"), ref_px=110.0,
            exit_reason="signal",
        ))
    return trade


def test_a_trade_finds_the_decision_that_opened_it(db):
    v = version(db)
    decisions_repo.insert_decision(db, entered(v, T0))
    trade = round_trip(db, T0)

    found = repo.origins(db, "live")

    assert found[trade].config_version_id == v
    assert found[trade].size_pct == 25.0
    assert found[trade].on_signal is True


def test_an_entry_on_a_bar_that_was_not_a_signal_is_reported_as_such(db):
    v = version(db)
    decisions_repo.insert_decision(
        db, entered(v, T0, long_signal=False, cold_start="trailing", skip_reason="dry_run")
    )
    trade = round_trip(db, T0)

    origin = repo.origins(db, "live")[trade]

    assert origin.on_signal is False
    assert origin.cold_start == "trailing"


def test_the_latest_decision_of_the_bar_wins_when_there_are_two(db):
    old, new = version(db), version(db, long=200.0)
    decisions_repo.insert_decision(db, entered(old, T0))
    decisions_repo.insert_decision(db, entered(new, T0))
    trade = round_trip(db, T0)

    assert repo.origins(db, "live")[trade].config_version_id == new


def test_a_trade_with_no_decision_has_no_origin(db):
    trade = round_trip(db, T0)

    assert trade not in repo.origins(db, "live")


def test_capital_is_both_buckets_of_every_enabled_symbol(db):
    small, big = version(db), version(db, long=300.0, short=200.0)

    caps = repo.capital_of(db, {small, big})

    assert caps == {small: Decimal("160"), big: Decimal("500")}
    assert repo.capital_of(db, set()) == {}


def test_a_trade_still_held_is_an_open_trade_with_its_margin(db):
    round_trip(db, T0, close=False)
    round_trip(db, T0 + 5 * DAY_MS)

    held = repo.open_trades(db, "live")

    # 1 × 100 ÷ 2x = 50
    assert [(t.symbol, t.side, t.qty, t.margin) for t in held] == [(BTC, "long", 1.0, Decimal("50"))]


def test_flips_are_counted_in_the_range_with_the_completed_ones(db):
    v = version(db)
    flip = dict(side="short", long_signal=False, zone="RED", state="BEARISH")
    decisions_repo.insert_decision(db, entered(
        v, T0, **flip,
        flip=Flip(close_qty_intended=1.0, close_qty_filled=1.0, residual_qty=0.0, aborted=False),
    ))
    decisions_repo.insert_decision(db, entered(
        v, T0 + DAY_MS, **flip, skip_reason="flip_aborted", orders=(),
        flip=Flip(close_qty_intended=1.0, close_qty_filled=0.4, residual_qty=0.6,
                  residual_side="long", aborted=True),
    ))

    assert repo.flips(db, "live", since_ts=None, until_ts=None) == repo.FlipCount(2, 1)
    assert repo.flips(db, "live", since_ts=T0 + DAY_MS, until_ts=None) == repo.FlipCount(1, 0)


def test_journal_counts_can_be_limited_to_a_range(db):
    v = version(db)
    for n in range(3):
        decisions_repo.insert_decision(db, entered(v, T0 + n * DAY_MS))

    everything = decisions_repo.journal_counts(db, "live")
    middle = decisions_repo.journal_counts(
        db, "live", since_ts=T0 + DAY_MS, until_ts=T0 + DAY_MS
    )

    assert everything["all"] == 3
    assert middle["all"] == 1
