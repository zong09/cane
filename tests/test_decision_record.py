"""กฎของ `validate_record()` — ตัวที่ CHECK ของ Postgres เขียนไม่ได้

ไฟล์นี้ **ไม่แตะ DB** โดยเจตนา (ไม่มี marker `db`) เพราะกฎที่มันเฝ้าเป็นกฎข้ามตาราง
และข้ามแถวที่บังคับได้แต่ในโค้ด — ถ้ามันพังต้องรู้ทันทีตอนรัน `pytest -m "not db"`
ไม่ใช่รู้ตอนที่มี Postgres อยู่ตรงหน้า

เทสต์ที่ยืนยันว่ามัน **ปฏิเสธจริง** สำคัญเท่าเทสต์ที่ยืนยันว่าของถูกผ่าน — ตัวตรวจที่
ปล่อยทุกอย่างผ่านคือตัวตรวจที่ไม่มีอยู่
"""

from __future__ import annotations

import pytest

from cane.db.repo.decisions import (
    DecisionRecord,
    Flip,
    OrderAttempt,
    RiskCheck,
    Stop,
    Unmanaged,
    Verdict,
    validate_record,
)

PERP = "usdtm_perp"
SPOT = "spot"
T0 = 1_788_000_000_000


def record(**overrides) -> DecisionRecord:
    """แท่งที่จบด้วย `no_signal` — เส้นทางที่สั้นที่สุดที่ยังถูกกฎทุกข้อ

    `**overrides` ให้เทสต์ทีละตัวใส่พิษเฉพาะฟิลด์ที่มันสนใจ แบบเดียวกับ `head_row()`
    ใน `test_config_db.py`
    """
    base = {
        "profile": "live",
        "market": PERP,
        "symbol": "BTC/USDT",
        "timeframe": "1d",
        "bar_close_ts": T0,
        "decided_ts": T0 + 500,
        "config_version_id": 1,
        "close_px": 77_500.0,
        "zone": "BLUE",
        "state": "BEARISH",
        "long_signal": False,
        "short_signal": False,
        "dry_run": False,
        "skip_reason": "no_signal",
    }
    return DecisionRecord(**{**base, **overrides})


def entry_order(**overrides) -> OrderAttempt:
    base = {
        "leg": "open",
        "order_side": "buy",
        "order_type": "market",
        "reduce_only": False,
        "qty": 0.0125,
        "client_order_id": "cane-btc-1788000000000-open",
        "sent": True,
        "accepted": True,
    }
    return OrderAttempt(**{**base, **overrides})


# ── skip_reason ⟺ ออเดอร์เปิดที่ venue รับแล้ว ────────────────────────────────


def test_a_bar_that_did_nothing_must_say_why():
    """แท่งที่ไม่เข้าไม้และไม่มีเหตุผล = บันทึกที่ตอบคำถาม "ทำไม" ไม่ได้ (spec/08:67)"""
    with pytest.raises(ValueError, match="skip_reason เป็น None"):
        validate_record(record(skip_reason=None))


def test_a_bar_that_entered_must_not_carry_a_skip_reason():
    with pytest.raises(ValueError, match="ไม้ที่เข้าได้ไม่มีเหตุผลที่ไม่เข้า"):
        validate_record(record(skip_reason="cane_rule", orders=(entry_order(),)))


def test_an_accepted_entry_order_stands_on_its_own():
    validate_record(record(skip_reason=None, orders=(entry_order(),)))


def test_the_criterion_is_accepted_not_sent():
    """ออเดอร์ที่ส่งแล้ว venue ปฏิเสธ **ไม่ใช่** ไม้ที่เข้าได้

    ถ้าเกณฑ์เป็น `sent` แถวนี้จะถูกบังคับให้ `skip_reason` เป็น `None` ทั้งที่เหตุผล
    จริงคือ `order_error` — ข้อขัดแย้งที่ทำให้ต้องเปลี่ยนเกณฑ์เป็น `accepted`
    """
    rejected = entry_order(sent=True, accepted=False, error="insufficient margin")
    validate_record(record(skip_reason="order_error", orders=(rejected,)))
    with pytest.raises(ValueError, match="skip_reason เป็น None"):
        validate_record(record(skip_reason=None, orders=(rejected,)))


def test_an_order_that_broke_before_sending_is_still_an_order_error():
    """`sent = False` แต่มี `error` — ช่องที่เกณฑ์ `sent AND NOT accepted` ทิ้งไว้"""
    broke = entry_order(sent=False, accepted=False, error="signer unavailable")
    validate_record(record(skip_reason="order_error", orders=(broke,)))


def test_order_error_needs_an_order_that_actually_errored():
    with pytest.raises(ValueError, match="เหตุผลไม่มีหลักฐานรองรับ"):
        validate_record(record(skip_reason="order_error"))


def test_an_errored_entry_cannot_be_filed_under_another_reason():
    broke = entry_order(sent=False, accepted=False, error="signer unavailable")
    with pytest.raises(ValueError, match="ไม่ใช่ 'order_error'"):
        validate_record(record(skip_reason="cane_rule", orders=(broke,)))


def test_a_retry_that_succeeded_in_the_same_bar_is_writable():
    """ครั้งแรกพัง ครั้งที่สองผ่าน ในแท่งเดียว — ไม่มีประตูไหนปิด จึงไม่มี `skip_reason`

    `decision_orders` ใช้ surrogate key เพราะขาเดียวกันซ้ำได้ตอน retry · ถ้าเทียบ
    `order_error` แบบ biconditional ล้วน แถวนี้จะถูกบังคับให้เป็นทั้ง `None` และ
    `'order_error'` พร้อมกันแล้วเขียนไม่ลงเลย ซึ่งกลับหัวเจตนาของกุญแจนั้นเอง
    """
    validate_record(
        record(
            skip_reason=None,
            orders=(
                entry_order(
                    client_order_id="cane-btc-open-1",
                    accepted=False,
                    error="timeout",
                ),
                entry_order(client_order_id="cane-btc-open-2"),
            ),
        )
    )


def test_an_unknown_skip_reason_is_refused_before_the_database_sees_it():
    with pytest.raises(ValueError, match="ไม่รู้จัก"):
        validate_record(record(skip_reason="kill_switch"))


def test_a_closing_leg_alone_does_not_count_as_an_entry():
    """ขาปิดล้วน (spot เจอสัญญาณแดง) ยังต้องมี `skip_reason`"""
    close_only = entry_order(leg="close", order_side="sell", reduce_only=False)
    validate_record(
        record(market=SPOT, leverage=1.0, skip_reason="short_disabled", orders=(close_only,))
    )
    with pytest.raises(ValueError, match="skip_reason เป็น None"):
        validate_record(
            record(market=SPOT, leverage=1.0, skip_reason=None, orders=(close_only,))
        )


# ── ลำดับของ risk check ──────────────────────────────────────────────────────


def test_risk_layers_are_recorded_in_the_order_they_ran():
    validate_record(
        record(
            skip_reason="risk_rejected",
            risk_checks=(
                RiskCheck(seq=1, layer="kill_switch", passed=True),
                RiskCheck(seq=2, layer="daily_loss", passed=True),
                RiskCheck(seq=3, layer="liq_buffer", passed=False, value=12.5, limit_value=25.0),
            ),
        )
    )


def test_two_spot_layers_are_a_complete_sequence():
    """spot ไม่มี liquidation จึงไม่เรียกชั้นที่สามเลย — สองแถวคือชุดที่ครบ

    ไม่ใช่ลำดับที่มีช่อง เพราะ `seq` นับตามชั้นที่ **ถูกเรียกจริง** (decisions #26)
    """
    validate_record(
        record(
            market=SPOT,
            leverage=1.0,
            skip_reason="risk_rejected",
            risk_checks=(
                RiskCheck(seq=1, layer="kill_switch", passed=True),
                RiskCheck(seq=2, layer="daily_loss", passed=False),
            ),
        )
    )


def test_a_gap_in_the_sequence_is_refused():
    with pytest.raises(ValueError, match="ไม่มีช่อง"):
        validate_record(
            record(
                skip_reason="risk_rejected",
                risk_checks=(
                    RiskCheck(seq=1, layer="kill_switch", passed=True),
                    RiskCheck(seq=3, layer="liq_buffer", passed=False),
                ),
            )
        )


def test_only_one_layer_can_fail():
    with pytest.raises(ValueError, match="ไม่เกินหนึ่งชั้น"):
        validate_record(
            record(
                skip_reason="risk_rejected",
                risk_checks=(
                    RiskCheck(seq=1, layer="kill_switch", passed=False),
                    RiskCheck(seq=2, layer="daily_loss", passed=False),
                ),
            )
        )


def test_the_failing_layer_must_be_the_last_one_recorded():
    """ชั้นแรกที่ไม่ผ่านปฏิเสธทั้งไม้ → ชั้นที่ตามหลังไม่ถูกเรียก (spec/08:39)"""
    with pytest.raises(ValueError, match="ชั้นสุดท้าย"):
        validate_record(
            record(
                skip_reason="risk_rejected",
                risk_checks=(
                    RiskCheck(seq=1, layer="kill_switch", passed=False),
                    RiskCheck(seq=2, layer="daily_loss", passed=True),
                ),
            )
        )


# ── สิ่งที่ตลาด spot ไม่มี ────────────────────────────────────────────────────


def test_a_spot_decision_cannot_carry_a_flip():
    with pytest.raises(ValueError, match="ไม่มี flip บน spot"):
        validate_record(
            record(
                market=SPOT,
                leverage=1.0,
                flip=Flip(
                    close_qty_intended=0.5,
                    close_qty_filled=0.5,
                    residual_qty=0.0,
                    aborted=False,
                ),
            )
        )


def test_a_spot_order_cannot_ask_for_reduce_only():
    with pytest.raises(ValueError, match="reduce_only"):
        validate_record(
            record(
                market=SPOT,
                leverage=1.0,
                skip_reason="short_disabled",
                orders=(entry_order(leg="close", order_side="sell", reduce_only=True),),
            )
        )


def test_a_spot_decision_cannot_open_a_short():
    with pytest.raises(ValueError, match="จบที่ leg=close"):
        validate_record(
            record(
                market=SPOT,
                leverage=1.0,
                skip_reason=None,
                orders=(entry_order(order_side="sell"),),
            )
        )


def test_a_perp_decision_may_close_with_reduce_only():
    """ขาปิดของ flip บน perp ใช้ `reduceOnly` เป็นเรื่องปกติ (spec/06:129)"""
    validate_record(
        record(
            skip_reason="flip_aborted",
            orders=(entry_order(leg="close", order_side="sell", reduce_only=True),),
            flip=Flip(
                close_qty_intended=0.5,
                close_qty_filled=0.2,
                residual_qty=0.3,
                residual_side="long",
                aborted=True,
            ),
        )
    )


# ── dry_run ──────────────────────────────────────────────────────────────────


def test_a_dry_run_bar_computes_everything_but_sends_nothing():
    """spec/06:80-84 — บันทึกครบ แต่ขั้น 13 ข้ามการยิง (spec/08:43)"""
    validate_record(
        record(
            dry_run=True,
            skip_reason="dry_run",
            size_rule="confluence",
            size_pct_formula=65.0,
            size_pct_final=50.0,
            capped=True,
            orders=(entry_order(sent=False, accepted=False),),
        )
    )


def test_a_dry_run_bar_that_sent_an_entry_is_refused():
    with pytest.raises(ValueError, match="ห้ามส่งคำสั่งจริง"):
        validate_record(
            record(dry_run=True, skip_reason=None, orders=(entry_order(),))
        )


# ── ทศนิยม: ปฏิเสธ ไม่ปัด ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("close_px", 77_500.123_456_789),
        ("qty", 0.012_345_678_9),
        ("margin", 100.000_000_001),
        ("notional", 200.000_000_001),
        ("ref_px", 77_500.123_456_789),
        ("leverage", 2.000_01),
        ("size_pct_formula", 65.000_01),
        ("size_pct_final", 50.000_01),
        ("funding_rate", 0.000_1_000_000_001),
    ],
)
def test_a_value_finer_than_its_column_is_refused_not_rounded(field, value):
    """การปัดที่ไม่มีใครเห็นทำให้บันทึกไม่ตรงกับที่คำนวณจริง (กฎที่ใบ 03b ตั้งไว้)"""
    with pytest.raises(ValueError, match="ทศนิยม"):
        validate_record(record(**{field: value}))


def test_the_field_that_is_too_fine_is_named_in_the_error():
    """`require_scale()` ไม่มี location ของ pydantic ให้พึ่ง จึงต้องบอกชื่อเอง"""
    with pytest.raises(ValueError, match="orders"):
        validate_record(
            record(skip_reason=None, orders=(entry_order(qty=0.012_345_678_9),))
        )


@pytest.mark.parametrize(
    "child",
    [
        {"verdicts": (Verdict(factor="HIGHER_LOW", side="long", present=True, cached=False, confidence=0.700_01),)},
        {"risk_checks": (RiskCheck(seq=1, layer="kill_switch", passed=True, value=1.000_000_001),)},
        {"stop": Stop(action="placed", px=70_000.000_000_001)},
        {"unmanaged": (Unmanaged(side="long", qty=0.100_000_000_1, source="flip_aborted", first_seen_bar_close_ts=T0),)},
    ],
)
def test_children_are_checked_for_scale_too(child):
    with pytest.raises(ValueError, match="ทศนิยม"):
        validate_record(record(**child))
