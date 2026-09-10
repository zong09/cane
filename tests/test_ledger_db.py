"""trade ledger — สิ่งที่ฐานต้องปฏิเสธ และกุญแจที่ทำให้ปฏิเสธได้

สองเรื่องที่ไฟล์นี้เฝ้า:

1. **เขียน fill เดิมซ้ำไม่ได้** นี่คือเหตุผลทั้งหมดที่ ledger ย้ายจากไฟล์มาเป็นตาราง
   — reconcile อ่านสถานะจริงทุกแท่ง (spec/08 ขั้น 3) แล้วเห็นของเดิมซ้ำเป็นเรื่องปกติ
   ตอนเป็นไฟล์ ความผิดพลาดคือคิดค่าธรรมเนียมซ้ำเงียบๆ
2. **แถวที่ตอบคำถามของตัวเองไม่ได้ต้องเขียนไม่ลง** — ขาปิดที่ไม่มีเหตุผลของการออก
   ค่าธรรมเนียมที่ไม่รู้สกุล funding ที่ไม่บอกทั้งยอดและเหตุผล
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from cane.db.repo import ledger as repo
from cane.db.repo.ledger import Fill, FundingCharge, dedupe_key_of, trade_id_of
from cane.db.schema import fills as fills_t
from cane.execution import client_order_id

pytestmark = pytest.mark.db

#: เที่ยงคืน UTC จริง — `1_788_000_000_000` ไม่ใช่ (ใบ 03 เคยพลาดตรงนี้)
T0 = 1_787_961_600_000
DAY_MS = 86_400_000

PERP = "usdtm_perp"
SYMBOL = "BTC/USDT"
TRADE = trade_id_of(PERP, SYMBOL, "long", T0)


def open_fill(**overrides) -> Fill:
    coid = client_order_id(SYMBOL, T0, "buy", "open")
    base = dict(
        profile="live",
        market=PERP,
        symbol=SYMBOL,
        trade_id=TRADE,
        leg="open",
        fill_ts=T0 + 500,
        px=100.0,
        qty=2.0,
        client_order_id=coid,
        order_type="market",
        reduce_only=False,
        position_qty_after=2.0,
        bar_close_ts=T0,
        dedupe_key=dedupe_key_of(coid),
        ref_px=100.0,
        fee_quote=Decimal("0.10"),
        fee_ccy="USDT",
        leverage=2.0,
    )
    return Fill(**{**base, **overrides})


def close_fill(**overrides) -> Fill:
    coid = client_order_id(SYMBOL, T0 + DAY_MS, "sell", "close")
    base = dict(
        profile="live",
        market=PERP,
        symbol=SYMBOL,
        trade_id=TRADE,
        leg="close",
        fill_ts=T0 + DAY_MS + 500,
        px=110.0,
        qty=2.0,
        client_order_id=coid,
        order_type="market",
        reduce_only=True,
        position_qty_after=0.0,
        bar_close_ts=T0 + DAY_MS,
        dedupe_key=dedupe_key_of(coid),
        ref_px=110.0,
        fee_quote=Decimal("0.11"),
        fee_ccy="USDT",
        leverage=2.0,
        exit_reason="signal",
    )
    return Fill(**{**base, **overrides})


def test_a_trade_reads_back_as_the_sequence_that_was_written(db):
    repo.record_fill(db, open_fill())
    repo.record_fill(db, close_fill())

    rows = repo.fills_of_trade(db, "live", TRADE)

    assert [row.leg for row in rows] == ["open", "close"]
    assert rows[0].px == 100.0 and rows[1].px == 110.0
    # ค่าธรรมเนียมกลับมาเป็น Decimal ไม่ใช่ float — มันถูกบวกสะสมจนต้องตรงกับใบแจ้ง
    assert rows[0].fee_quote == Decimal("0.10000000")
    assert isinstance(rows[1].fee_quote, Decimal)


def test_the_same_fill_cannot_be_written_twice(db):
    """หัวใจของใบนี้ — process ที่กลับมาในแท่งเดิมจะสร้าง `dedupe_key` เดิมเป๊ะ

    ตอน ledger เป็นไฟล์ ต้องเขียนโค้ด dedupe เอง และถ้าพลาดคือคิดค่าธรรมเนียมซ้ำ
    โดยไม่มีอะไรค้าน ตอนนี้ฐานปฏิเสธตั้งแต่ `INSERT`
    """
    repo.record_fill(db, open_fill())

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            repo.record_fill(db, open_fill())

    assert isinstance(caught.value.orig, psycopg.errors.UniqueViolation)


def test_the_same_key_in_the_other_profile_is_a_different_fill(db):
    """`UNIQUE` ผูก `profile` ไว้ด้วย — ไม้ paper กับ live ไม่ใช่ของซ้ำกันเอง"""
    repo.record_fill(db, open_fill())
    repo.record_fill(db, open_fill(profile="paper"))

    assert db.execute(select(func.count()).select_from(fills_t)).scalar_one() == 2


def test_two_partial_fills_of_one_order_are_two_rows_not_a_duplicate(db):
    """ออเดอร์ใบเดียว fill เป็นหลายก้อนได้จริง `seq` จึงไม่ใช่ของประดับ

    ถ้า `dedupe_key` เป็น `client_order_id` เปล่าๆ ก้อนที่สองจะถูกฐานปฏิเสธในฐานะ
    ของซ้ำ ทั้งที่เป็นเงินคนละก้อน แล้ว P&L จะขาดไปครึ่งไม้โดยไม่มีใครเห็น
    """
    coid = client_order_id(SYMBOL, T0, "buy", "open")
    repo.record_fill(db, open_fill(qty=1.0, position_qty_after=1.0,
                                   dedupe_key=dedupe_key_of(coid, 0)))
    repo.record_fill(db, open_fill(qty=1.0, position_qty_after=2.0, fill_ts=T0 + 600,
                                   dedupe_key=dedupe_key_of(coid, 1)))

    rows = repo.fills_of_trade(db, "live", TRADE)

    assert [row.qty for row in rows] == [1.0, 1.0]
    assert rows[-1].position_qty_after == 2.0


def test_a_venue_fill_id_wins_over_the_synthetic_key():
    """คีย์ที่ venue ออกให้เองดีกว่าคีย์ที่เราประกอบ — มันคงที่ข้ามการอ่านซ้ำแน่นอน"""
    coid = client_order_id(SYMBOL, T0, "buy", "open")

    assert dedupe_key_of(coid, 0) == f"{coid}#0"
    assert dedupe_key_of(coid, 0, venue_fill_id="88123") == "88123"


def test_has_fill_is_the_gate_reconcile_checks_before_writing(db):
    key = dedupe_key_of(client_order_id(SYMBOL, T0, "buy", "open"))

    assert repo.has_fill(db, "live", key) is False
    repo.record_fill(db, open_fill())
    assert repo.has_fill(db, "live", key) is True
    # profile อื่นยังไม่เคยเห็นคีย์นี้
    assert repo.has_fill(db, "paper", key) is False


def test_a_closing_leg_without_a_reason_does_not_assemble():
    """ดังตั้งแต่ประกอบค่า ไม่ต้องรอถึง CHECK ตอน insert ที่ traceback ชี้ไปที่ SQL"""
    with pytest.raises(ValueError, match="exit_reason"):
        close_fill(exit_reason=None)

    with pytest.raises(ValueError, match="exit_reason"):
        open_fill(exit_reason="signal")


@pytest.mark.parametrize(
    "column, value",
    [
        ("exit_reason", "'flip'"),
        ("exit_reason", "'timeout'"),
    ],
)
def test_an_exit_reason_outside_the_vocabulary_is_refused(db, column, value):
    """สี่ค่ามาจากสเปกทั้งสี่ · `flip` ไม่ใช่ค่าที่ห้าโดยเจตนา

    ขา 1 ของ flip คือการออกด้วยสัญญาณฝั่งตรงข้าม เหตุของการออกเป็นเหตุเดียวกันเป๊ะ
    ถ้าแยกเป็นค่าที่ห้า รายงาน "ออกเพราะสัญญาณ" จะนับขาดทุกครั้งที่มีการกลับข้าง
    """
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    INSERT INTO fills (profile, market, symbol, trade_id, leg, fill_ts,
                        px, qty, client_order_id, order_type, reduce_only,
                        position_qty_after, {column}, bar_close_ts, dedupe_key, created_ts)
                    VALUES ('live', 'usdtm_perp', 'BTC/USDT', :t, 'close', :ts,
                        110, 2, 'cane-x', 'market', true, 0, {value}, :bar, 'k1', :ts)
                    """
                ),
                {"t": TRADE, "ts": T0, "bar": T0},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


@pytest.mark.parametrize(
    "values, why",
    [
        # ยอดค่าธรรมเนียมที่ไม่รู้สกุลบวกเข้ารายงานไม่ได้
        ("fee_quote, fee_ccy", "0.1, NULL"),
        # "ยังไม่รู้" กับ "รู้แล้ว" พร้อมกันไม่ได้
        ("fee_quote, fee_ccy, fee_unavailable_reason", "0.1, 'USDT', 'venue เงียบ'"),
    ],
)
def test_a_fee_that_cannot_be_read_back_is_refused(db, values, why):
    columns, literals = values, why

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    INSERT INTO fills (profile, market, symbol, trade_id, leg, fill_ts,
                        px, qty, client_order_id, order_type, reduce_only,
                        position_qty_after, bar_close_ts, dedupe_key, created_ts,
                        {columns})
                    VALUES ('live', 'usdtm_perp', 'BTC/USDT', :t, 'open', :ts,
                        100, 2, 'cane-x', 'market', false, 2, :bar, 'k2', :ts,
                        {literals})
                    """
                ),
                {"t": TRADE, "ts": T0, "bar": T0},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


@pytest.mark.parametrize(
    "market, reduce_only, leverage",
    [
        # spot ไม่มีธง reduce_only อยู่จริง (spec/03:22)
        ("spot", "true", "1"),
        # spot ถูกบังคับ leverage = 1 ตั้งแต่ config (ADR 26)
        ("spot", "false", "3"),
    ],
)
def test_the_table_refuses_what_spot_does_not_have(db, market, reduce_only, leverage):
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    INSERT INTO fills (profile, market, symbol, trade_id, leg, fill_ts,
                        px, qty, client_order_id, order_type, reduce_only, leverage,
                        position_qty_after, bar_close_ts, dedupe_key, created_ts)
                    VALUES ('live', '{market}', 'ETH/USDT', :t, 'open', :ts,
                        100, 2, 'cane-x', 'market', {reduce_only}, {leverage},
                        2, :bar, 'k3', :ts)
                    """
                ),
                {"t": trade_id_of(market, "ETH/USDT", "long", T0), "ts": T0, "bar": T0},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_the_open_trade_is_found_from_the_ledger_not_from_memory(db):
    """ขาปิดต้องใช้ `trade_id` เดียวกับขาเปิด แต่ฝั่ง live ไม่มีใครจำแท่งที่เปิดไว้

    `positions()` ของ venue ไม่ได้พาแท่งที่เปิดมาด้วย ตัวที่ตอบได้คือ ledger เอง —
    ตรงกับกฎของ spec/06 ที่ห้ามเชื่อสถานะที่จำไว้ในหน่วยความจำ
    """
    assert repo.open_trade_id(db, "live", PERP, SYMBOL, "long") is None

    repo.record_fill(db, open_fill())
    assert repo.open_trade_id(db, "live", PERP, SYMBOL, "long") == TRADE
    # คนละฝั่งและคนละตลาดคือคนละไม้
    assert repo.open_trade_id(db, "live", PERP, SYMBOL, "short") is None
    assert repo.open_trade_id(db, "live", "spot", SYMBOL, "long") is None

    repo.record_fill(db, close_fill())
    assert repo.open_trade_id(db, "live", PERP, SYMBOL, "long") is None


def test_a_partly_closed_trade_is_still_open(db):
    """ของค้างจาก `flip_aborted` ยังเป็นไม้ที่เปิดอยู่ (spec/03:75, ADR 19)"""
    repo.record_fill(db, open_fill())
    repo.record_fill(db, close_fill(qty=1.5, position_qty_after=0.5))

    assert repo.open_trade_id(db, "live", PERP, SYMBOL, "long") == TRADE


def test_one_funding_cycle_is_charged_once(db):
    charge = FundingCharge(
        profile="live",
        symbol=SYMBOL,
        trade_id=TRADE,
        cycle_ts=T0 + 3_600_000,
        position_qty=2.0,
        rate=0.0001,
        amount_quote=Decimal("0.02"),
        mark_px=100.0,
    )
    repo.record_funding_charge(db, charge)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            repo.record_funding_charge(db, charge)

    assert isinstance(caught.value.orig, psycopg.errors.UniqueViolation)


def test_a_funding_row_must_say_the_amount_or_say_why_it_cannot():
    """แถวนี้มีอยู่เพราะมีการหักเงิน มันจึงเงียบทั้งสองทางไม่ได้

    ต่างจาก `decisions.funding_rate` ที่ใบ 03 ตั้งใจไม่ใส่ XOR ไว้ — ที่นั่นแถวมีอยู่
    เพราะแท่งปิด และแท่งที่จบด้วย `no_signal` อาจไม่เคยดึง funding เลยอย่างถูกต้อง
    """
    base = dict(profile="live", symbol=SYMBOL, trade_id=TRADE, cycle_ts=T0, position_qty=2.0)

    with pytest.raises(ValueError, match="funding"):
        FundingCharge(**base)

    with pytest.raises(ValueError, match="funding"):
        FundingCharge(**base, rate=0.0001, amount_quote=Decimal("0.02"),
                      unavailable_reason="venue เงียบ")


def test_a_funding_row_that_does_not_know_the_amount_is_allowed_to_say_so(db):
    repo.record_funding_charge(
        db,
        FundingCharge(
            profile="live",
            symbol=SYMBOL,
            trade_id=TRADE,
            cycle_ts=T0,
            position_qty=2.0,
            unavailable_reason="venue ไม่คืนอัตรา",
        ),
    )

    rows = repo.funding_charges_of_trade(db, "live", TRADE)

    assert len(rows) == 1
    assert rows[0].amount_quote is None
    assert rows[0].unavailable_reason == "venue ไม่คืนอัตรา"


def test_spot_can_never_get_a_funding_row(db):
    """spot ไม่มี funding อยู่จริง ไม่ใช่มีแล้วเป็นศูนย์ (spec/03:22, ADR 26)"""
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO funding_charges (profile, market, symbol, trade_id,
                        cycle_ts, rate, amount_quote, position_qty, mark_px, created_ts)
                    VALUES ('live', 'spot', 'ETH/USDT', :t, :ts, 0.0001, 0.02, 2, 100, :ts)
                    """
                ),
                {"t": trade_id_of("spot", "ETH/USDT", "long", T0), "ts": T0},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_the_trade_key_names_the_position_side_not_the_order_side():
    """กุญแจของไม้กับ id ของออเดอร์ตอบคนละคำถาม จึงใช้คนละคำศัพท์ของ `side`"""
    assert trade_id_of(PERP, "BTC/USDT:USDT", "short", T0) == (
        "usdtm_perp:BTC/USDT:short:1787961600000"
    )

    with pytest.raises(ValueError, match="long หรือ short"):
        trade_id_of(PERP, SYMBOL, "sell", T0)


def test_the_ledger_is_append_only_for_the_engine_role(db):
    """ADR 23 — `cane_engine` ไม่มี `UPDATE`/`DELETE` บน ledger ไม่ใช่แค่ไม่เรียกใช้"""
    repo.record_fill(db, open_fill())
    db.execute(text("SET LOCAL ROLE cane_engine"))

    for statement in (
        "UPDATE fills SET px = 1",
        "DELETE FROM fills",
        "UPDATE funding_charges SET position_qty = 1",
        "DELETE FROM funding_charges",
    ):
        with pytest.raises(Exception) as caught:
            with db.begin_nested():
                db.execute(text(statement))
        assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)
