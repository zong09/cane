"""ตาราง `decisions` ในฐานข้อมูล — ไป-กลับได้ของเดิม และ constraint ปฏิเสธจริง

สามเรื่องที่ไฟล์นี้เฝ้า:

1. **แท่งที่ระบบเลือกจะไม่ทำอะไรต้องเขียนลงได้** (spec/08:67) — NOT NULL ที่เกินจริง
   จะกลับหัวเจตนาทั้งใบ เทสต์แท่ง `no_signal` จึงเป็นเทสต์แรก ไม่ใช่เทสต์ขอบ
2. **ข้อบังคับที่ย้ายขึ้นมาเป็นของ schema ต้องมีผลจริง** ทุก CHECK ที่นี่ยิงให้ล้ม
   ไม่ใช่แค่ประกาศไว้ใน migration แล้วเชื่อว่าใช้ได้
3. **ลำดับการตัดสินใจต้องประกอบกลับได้ครบ** — เกณฑ์เสร็จของใบ 03 อยู่ที่
   `test_a_twenty_bar_run_reads_back_as_a_complete_sequence` ท้ายไฟล์
"""

from __future__ import annotations

import importlib.util

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from cane.config import load_profile
from cane.config.settings import SymbolConfig
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as repo
from cane.db.repo.decisions import (
    DecisionRecord,
    Flip,
    OrderAttempt,
    RiskCheck,
    Stop,
    Unmanaged,
    Verdict,
)
from cane.db.schema import (
    DECISION_TABLES,
    decision_flip,
    decision_orders,
    decision_unmanaged,
    decisions,
)

pytestmark = pytest.mark.db

DAY_MS = 86_400_000
#: **เที่ยงคืน UTC พอดี** (`T0 % DAY_MS == 0`) — แท่ง `1d` ปิดที่ขอบวันอยู่แล้ว และ
#: เทสต์ `utc_day` ต้องยืนหยัดบนขอบจริง ไม่ใช่เวลากลางวันที่บังเอิญเลือกมา
T0 = 1_787_961_600_000
PERP = "usdtm_perp"
SPOT = "spot"
BTC = "BTC/USDT"
ETH = "ETH/USDT"


@pytest.fixture(autouse=True)
def clean_decisions(db):
    """เริ่มทุกเทสต์จากตารางบันทึกที่ว่าง **ในทรานแซกชันของเทสต์เอง**

    เครื่อง dev ยังไม่มีแถวจริงในตารางพวกนี้ (ผู้เขียนคือใบ 12) แต่วันที่มี เทสต์ที่
    นับแถวจะเริ่มโกหกเงียบๆ ไม่ใช่ล้ม · การลบที่นี่ปลอดภัยเพราะ fixture `db`
    rollback ทุกอย่างท้ายเทสต์ แบบเดียวกับ `clean_config` ใน `test_config_db.py`
    """
    for table in reversed(DECISION_TABLES):
        db.execute(table.delete())


@pytest.fixture
def live_settings():
    """config ของ live ที่ **ยิงคำสั่งจริงได้ และมีทั้งเหรียญ perp และ spot**

    `config/live.toml` ให้ทั้งสองอย่างไม่ได้: มันตั้ง `dry_run = true` (ค่าตั้งต้นคือ
    true แม้ใน live ตาม spec/06:82) และมีเหรียญ perp ตัวเดียว · เหรียญ spot อยู่ใน
    `paper.toml` ซึ่ง `ck_config_settings_paper_dry_run` บังคับ `dry_run = true` ตายตัว
    → เส้นทางเข้าไม้/flip/order_error ของ spot จะบันทึกไม่ได้เลยถ้าโหลดไฟล์ดิบมาใช้
    """
    live = load_profile("config/live.toml")
    spot = SymbolConfig(
        symbol=ETH,
        market=SPOT,
        bucket_quote_long=80.0,
        leverage=1.0,
        allow_short=False,
    )
    return live.model_copy(
        update={"dry_run": False, "symbols": [*live.symbols, spot]}
    )


@pytest.fixture
def live_version(db, live_settings):
    return config_repo.insert_version(
        db, live_settings, source="migration", created_ts=T0
    ).id


@pytest.fixture
def paper_version(db):
    return config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="migration", created_ts=T0
    ).id


def head_values(version_id, **overrides):
    """ค่าของหัวหนึ่งแถวสำหรับ insert ดิบ — ใช้ตอนต้องใส่พิษเฉพาะคอลัมน์เดียว

    เส้นทางนี้ข้าม `validate_record()` โดยเจตนา เพราะสิ่งที่ทดสอบคือ **ฐาน** ปฏิเสธ
    ไม่ใช่ตัวตรวจของแอปปฏิเสธ
    """
    values = {
        "profile": "live",
        "market": PERP,
        "symbol": BTC,
        "timeframe": "1d",
        "bar_close_ts": T0,
        "decided_ts": T0 + 500,
        "config_version_id": version_id,
        "close_px": 77_500,
        "zone": "BLUE",
        "state": "BEARISH",
        "long_signal": False,
        "short_signal": False,
        "dry_run": False,
        "skip_reason": "no_signal",
        "created_ts": T0 + 600,
    }
    return {**values, **overrides}


def quiet_bar(version_id, **overrides) -> DecisionRecord:
    """แท่งที่จบด้วย "ไม่ทำอะไร" — เส้นทางที่สั้นที่สุดที่ยังถูกกฎทุกข้อ"""
    base = {
        "profile": "live",
        "market": PERP,
        "symbol": BTC,
        "timeframe": "1d",
        "bar_close_ts": T0,
        "decided_ts": T0 + 500,
        "config_version_id": version_id,
        "close_px": 77_500.0,
        "zone": "BLUE",
        "state": "BEARISH",
        "long_signal": False,
        "short_signal": False,
        "dry_run": False,
        "skip_reason": "no_signal",
    }
    return DecisionRecord(**{**base, **overrides})


# ── ไป-กลับ ──────────────────────────────────────────────────────────────────


def test_a_bar_that_did_nothing_is_written_and_read_back(db, live_version):
    """เทสต์แรกของไฟล์คือเส้นทาง "ไม่ทำอะไร" เพราะมันคือเจตนาทั้งใบ (spec/08:67)"""
    decision_id = repo.insert_decision(db, quiet_bar(live_version), created_ts=T0 + 600)

    back = repo.decision_at(db, "live", PERP, BTC, "1d", T0)
    assert back is not None
    assert back.id == decision_id
    assert back.skip_reason == "no_signal"
    assert back.close_px == 77_500.0
    assert isinstance(back.close_px, float)
    assert back.side is None
    assert back.orders == ()
    assert back.flip is None
    assert back.stop is None


def test_a_full_bar_survives_the_round_trip_as_floats(db, live_version):
    """ทุกค่าที่เป็น `NUMERIC` ต้องกลับมาเป็น `float` — golden test ของใบ 04 พึ่งข้อนี้"""
    record = quiet_bar(
        live_version,
        zone="RED",
        state="BULLISH",
        short_signal=True,
        side="short",
        skip_reason=None,
        cold_start="trailing",
        leverage=2.0,
        margin_mode="isolated",
        judge_called=True,
        llm_fallback=False,
        prompt_hash="sha256:abc",
        factors_present=3,
        size_rule="confluence",
        size_pct_formula=65.0,
        size_pct_final=50.0,
        capped=True,
        margin=30.0,
        notional=60.0,
        qty=0.00077419,
        ref_px=77_500.25,
        funding_rate=0.0001234567,
        funding_next_ts=T0 + 3_600_000,
        verdicts=(
            Verdict(
                factor="CHANNEL_BREAKDOWN",
                side="short",
                present=True,
                cached=False,
                confidence=0.72,
                evidence_bars=(T0 - DAY_MS, T0),
                rationale="ทะลุขอบล่างของช่อง",
            ),
            Verdict(factor="LOWER_HIGH", side="short", present=True, cached=True),
        ),
        risk_checks=(
            RiskCheck(seq=1, layer="kill_switch", passed=True),
            RiskCheck(seq=2, layer="daily_loss", passed=True, value=1.5, limit_value=3.0),
            RiskCheck(seq=3, layer="liq_buffer", passed=True, value=40.0, limit_value=25.0),
        ),
        orders=(
            OrderAttempt(
                leg="close",
                order_side="sell",
                order_type="market",
                reduce_only=True,
                qty=0.0005,
                client_order_id="cane-btc-close",
                sent=True,
                accepted=True,
                venue_order_id="v-1",
            ),
            OrderAttempt(
                leg="open",
                order_side="sell",
                order_type="market",
                reduce_only=False,
                qty=0.00077419,
                client_order_id="cane-btc-open",
                sent=True,
                accepted=True,
                venue_order_id="v-2",
            ),
        ),
        flip=Flip(
            close_qty_intended=0.0005,
            close_qty_filled=0.0005,
            residual_qty=0.0,
            aborted=False,
        ),
        stop=Stop(action="placed", px=79_000.5, stop_order_id="v-3"),
    )
    repo.insert_decision(db, record, created_ts=T0 + 600)

    back = repo.decision_at(db, "live", PERP, BTC, "1d", T0)
    assert back is not None
    assert (back.size_pct_formula, back.size_pct_final) == (65.0, 50.0)
    assert back.qty == 0.00077419
    assert back.funding_rate == 0.0001234567
    assert back.leverage == 2.0
    assert [v.factor for v in back.verdicts] == ["CHANNEL_BREAKDOWN", "LOWER_HIGH"]
    assert back.verdicts[0].evidence_bars == (T0 - DAY_MS, T0)
    assert back.verdicts[0].confidence == 0.72
    assert back.verdicts[1].evidence_bars == ()
    assert [c.layer for c in back.risk_checks] == ["kill_switch", "daily_loss", "liq_buffer"]
    assert [o.leg for o in back.orders] == ["close", "open"]
    assert back.flip is not None and back.flip.residual_qty == 0.0
    assert back.stop is not None and back.stop.px == 79_000.5
    # อ่านกลับมาแล้วต้องยังเคารพ invariant เดิม ไม่ใช่แถวที่เขียนได้แต่อ่านกลับไม่ผ่าน
    repo.validate_record(back)


def test_the_repository_normalises_the_symbol_like_every_other_table(db, live_version):
    """`BTC/USDT:USDT` ของ ccxt ต้องลงตารางเป็น `BTC/USDT` เหมือน `bars`

    ถ้าไม่ normalize `decisions.symbol` จะ join กับ `bars` และ `config_symbols` ไม่ติด
    """
    repo.insert_decision(db, quiet_bar(live_version, symbol="BTC/USDT:USDT"))

    stored = db.execute(select(decisions.c.symbol)).scalar_one()
    assert stored == BTC
    assert repo.decision_at(db, "live", PERP, "BTC/USDT:USDT", "1d", T0) is not None


def test_a_restart_inside_the_same_bar_keeps_both_rows(db, live_version):
    """กุญแจธรรมชาติ **ไม่ unique** — แถวที่สองคือหลักฐานของ restart (spec/06:127)

    ถ้าใส่ UNIQUE + upsert แถวแรกจะถูกทับ แล้วคนอ่านย้อนหลังจะสรุปว่า "ไม่มีออเดอร์
    ถูกส่ง" ซึ่งกลับหัวความจริง
    """
    first = repo.insert_decision(db, quiet_bar(live_version), created_ts=T0 + 600)
    second = repo.insert_decision(
        db, quiet_bar(live_version, skip_reason="cane_rule"), created_ts=T0 + 900
    )

    assert first != second
    rows = repo.decisions_for(db, "live", PERP, BTC, "1d")
    assert [r.id for r in rows] == [first, second]
    # ตัวอ่านของแท่งเดียวเลือกแถวล่าสุด และรู้ตัวว่าเลือก
    assert repo.decision_at(db, "live", PERP, BTC, "1d", T0).id == second


def test_insert_refuses_a_record_that_breaks_an_invariant(db, live_version):
    """`insert_decision()` เรียก `validate_record()` เอง ไม่ใช่ของที่ผู้เรียกเลือกเรียก"""
    with pytest.raises(ValueError, match="skip_reason เป็น None"):
        repo.insert_decision(db, quiet_bar(live_version, skip_reason=None))

    assert db.execute(select(func.count()).select_from(decisions)).scalar_one() == 0


# ── ขอบวัน UTC ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bar_close_ts", "expected_offset"),
    [
        (T0, 0),
        (T0 + DAY_MS - 1, 0),
        (T0 + DAY_MS, 1),
    ],
    ids=["midnight", "one-ms-before-midnight", "next-midnight"],
)
def test_utc_day_moves_exactly_at_midnight_utc(
    db, live_version, bar_close_ts, expected_offset
):
    """`max_daily_loss_pct` reset เที่ยงคืน **UTC** (spec/06:57)

    แท่งที่ปิด 00:00:00.000 ตกไปวันใหม่ ส่วนแท่งที่ปิด 23:59:59.999 ยังเป็นวันเดิม —
    คอลัมน์นี้เป็นทางเดียวที่ query ต่อวันเขียนได้โดยไม่เอา `datetime` เข้ามาใน `src/`
    """
    repo.insert_decision(db, quiet_bar(live_version, bar_close_ts=bar_close_ts))

    day = db.execute(select(decisions.c.utc_day)).scalar_one()
    assert day == T0 // DAY_MS + expected_offset


# ── CHECK ต้องปฏิเสธจริง ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("market", "spot_margin"),
        ("timeframe", "4h"),
        ("cold_start", "hope"),
        ("factors_present", 4),
        ("size_rule", "base_only"),
        ("skip_reason", "kill_switch"),
    ],
)
def test_the_database_refuses_a_value_outside_a_closed_set(
    db, live_version, field, value
):
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(decisions.insert().values(head_values(live_version, **{field: value})))

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_a_bar_cannot_be_a_signal_on_both_sides(db, live_version):
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                decisions.insert().values(
                    head_values(live_version, long_signal=True, short_signal=True)
                )
            )

    assert "ck_decisions_signal_exclusive" in str(caught.value)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"leverage": 3}, "ck_decisions_spot_no_leverage"),
        ({"margin_mode": "isolated"}, "ck_decisions_spot_no_leverage"),
        ({"funding_rate": "0.0001"}, "ck_decisions_spot_no_funding"),
        ({"funding_next_ts": T0}, "ck_decisions_spot_no_funding"),
        ({"funding_unavailable_reason": "timeout"}, "ck_decisions_spot_no_funding"),
        ({"side": "short"}, "ck_decisions_spot_long_only"),
    ],
)
def test_the_database_refuses_what_spot_does_not_have(
    db, live_version, overrides, constraint
):
    """spot ไม่มี leverage / margin mode / funding / ฝั่ง short (decisions #26)

    `funding_rate = NULL` บน spot คือ "ตลาดนี้ไม่มี funding" ซึ่งคนละความหมายกับ
    "ดึงค่าไม่ได้" — ถ้าปล่อยให้เขียนค่าลงไปได้ ความต่างนั้นจะหายไปจากบันทึก
    """
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                decisions.insert().values(
                    head_values(live_version, market=SPOT, symbol=ETH, **overrides)
                )
            )

    assert constraint in str(caught.value)


def test_a_spot_bar_with_the_columns_left_empty_is_accepted(db, live_version):
    """ขาที่ **ผ่าน** ของกฎเดียวกัน — `leverage IS NULL` บน spot ไม่ถูกปฏิเสธ

    CHECK เขียนเป็น `leverage = 1` ซึ่ง `NULL = 1` ให้ NULL ไม่ใช่ false → ปล่อยผ่าน
    ทั้ง `NULL` และ `1` · เทสต์คู่นี้กันคนอ่านสรุปผิดว่า NULL ถูกปฏิเสธ
    """
    db.execute(decisions.insert().values(head_values(live_version, market=SPOT, symbol=ETH)))
    db.execute(
        decisions.insert().values(
            head_values(live_version, market=SPOT, symbol=ETH, leverage=1)
        )
    )

    assert db.execute(select(func.count()).select_from(decisions)).scalar_one() == 2


def test_an_order_the_venue_rejected_must_carry_the_error(db, live_version):
    """`sent AND NOT accepted` ที่ไม่มี `error` = แถวที่แยกไม่ออกจากบั๊กของโค้ดเอง"""
    decision_id = repo.insert_decision(db, quiet_bar(live_version))

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                decision_orders.insert().values(
                    decision_id=decision_id,
                    profile="live",
                    leg="open",
                    order_side="buy",
                    order_type="market",
                    reduce_only=False,
                    qty=1,
                    client_order_id="c-1",
                    sent=True,
                    accepted=False,
                    created_ts=T0,
                )
            )

    assert "ck_decision_orders_rejected_needs_error" in str(caught.value)


def test_a_residual_must_carry_its_side(db, live_version):
    """spec/03:79 บังคับให้บันทึกฝั่งของ residual — ของค้างที่ไม่รู้ฝั่งปิดด้วยมือไม่ได้"""
    decision_id = repo.insert_decision(db, quiet_bar(live_version))

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                decision_flip.insert().values(
                    decision_id=decision_id,
                    profile="live",
                    close_qty_intended=1,
                    close_qty_filled=0,
                    residual_qty=1,
                    residual_side=None,
                    aborted=True,
                    created_ts=T0,
                )
            )

    assert "ck_decision_flip_residual_needs_side" in str(caught.value)


# ── profile ของลูกโกหกไม่ได้ ─────────────────────────────────────────────────


def test_a_child_row_cannot_claim_a_different_profile_than_its_head(db, live_version):
    """composite FK `(decision_id, profile)` — ถ้าลูกโกหกได้ คอนโซลจะเอาไม้ paper
    ไปปนกับ live โดยไม่มีอะไรขวาง (แบบเดียวกับที่ config ทำในใบ 03b)
    """
    decision_id = repo.insert_decision(db, quiet_bar(live_version))

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                decision_unmanaged.insert().values(
                    decision_id=decision_id,
                    profile="paper",
                    side="long",
                    qty=1,
                    source="flip_aborted",
                    first_seen_bar_close_ts=T0,
                    created_ts=T0,
                )
            )

    assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


def test_a_live_decision_cannot_point_at_a_paper_config_version(db, paper_version):
    """`(config_version_id, profile)` เป็น composite FK ด้วยเหตุผลเดียวกัน

    ไม้ live ที่ชี้ config ของ paper คือบันทึกที่อ่านย้อนหลังแล้วได้ค่าที่ไม่ได้ใช้จริง
    """
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                decisions.insert().values(head_values(paper_version, profile="live"))
            )

    assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


# ── สิทธิ์: เขียนได้ครั้งเดียว แก้ไม่ได้ ลบไม่ได้ ────────────────────────────


def _as_role(conn, role: str) -> None:
    conn.execute(text(f'SET LOCAL ROLE "{role}"'))


def test_the_engine_role_can_write_a_decision(db, live_version):
    with db.begin_nested():
        _as_role(db, "cane_engine")
        repo.insert_decision(db, quiet_bar(live_version))

    assert db.execute(select(func.count()).select_from(decisions)).scalar_one() == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decisions SET skip_reason = 'cane_rule'",
        "DELETE FROM decisions",
    ],
)
def test_the_engine_role_cannot_rewrite_history(db, live_version, statement):
    """ตารางข้อเท็จจริงเป็น append-only ที่บังคับด้วย grant จริง (decisions #23)

    นี่คือสิ่งที่ JSONL ทำไม่ได้ และเป็นเหตุผลหนึ่งที่ย้ายบันทึกลงฐาน
    """
    repo.insert_decision(db, quiet_bar(live_version))

    with pytest.raises(ProgrammingError) as caught:
        with db.begin_nested():
            _as_role(db, "cane_engine")
            db.execute(text(statement))

    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)


def test_the_console_role_can_only_read_decisions(db, live_version):
    repo.insert_decision(db, quiet_bar(live_version))

    with db.begin_nested():
        _as_role(db, "cane_console")
        count = db.execute(text("SELECT count(*) FROM decisions")).scalar_one()
    assert count == 1

    with pytest.raises(ProgrammingError) as caught:
        with db.begin_nested():
            _as_role(db, "cane_console")
            repo.insert_decision(db, quiet_bar(live_version))

    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)


@pytest.mark.parametrize("table", [t.name for t in DECISION_TABLES])
def test_no_role_may_delete_from_any_decision_table(db, table):
    """ไล่ทุกตารางในชุด ไม่ใช่แค่หัว — grant ที่ลืมตารางหนึ่งจะเงียบมาก"""
    for role in ("cane_engine", "cane_console"):
        with pytest.raises(ProgrammingError) as caught:
            with db.begin_nested():
                _as_role(db, role)
                db.execute(text(f"DELETE FROM {table}"))
        assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)


# ── migration ถอยแล้วเดินหน้าได้ ─────────────────────────────────────────────


def test_the_migration_can_be_rolled_back_and_reapplied(db, live_version):
    """`downgrade()` แล้ว `upgrade()` ของ `0004` ในทรานแซกชันของเทสต์เอง

    ท่าเดียวกับที่ `test_config_db.py` ใช้ยิง guard ของ `0003` — `MigrationContext`
    ผูก `op` เข้ากับ connection ของเทสต์ จึงเห็นแถวที่ยัง uncommitted และ fixture `db`
    rollback ทั้ง DDL ทิ้งท้ายเทสต์ · แทนการยืนยันด้วยมือใน terminal
    """
    spec = importlib.util.spec_from_file_location(
        "_m0004", "alembic/versions/0004_decisions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    repo.insert_decision(db, quiet_bar(live_version))

    with Operations.context(MigrationContext.configure(db)):
        module.downgrade()
        assert db.execute(text("SELECT to_regclass('public.decisions')")).scalar_one() is None
        module.upgrade()

    assert db.execute(select(func.count()).select_from(decisions)).scalar_one() == 0


# ── เกณฑ์เสร็จของใบ: 20 แท่ง 1 perp + 1 spot ─────────────────────────────────


def _perp_run(version_id) -> list[DecisionRecord]:
    """สิบสองแท่งของ BTC บน perp — ไล่ทุกเส้นทางที่ใบสั่งไว้"""

    def bar(index, **overrides):
        return quiet_bar(
            version_id,
            bar_close_ts=T0 + index * DAY_MS,
            decided_ts=T0 + index * DAY_MS + 500,
            leverage=2.0,
            margin_mode="isolated",
            funding_rate=0.0001,
            funding_next_ts=T0 + index * DAY_MS + 3_600_000,
            **overrides,
        )

    def order(**overrides):
        base = {
            "leg": "open",
            "order_side": "buy",
            "order_type": "market",
            "reduce_only": False,
            "qty": 0.0005,
            "client_order_id": "cane-btc",
            "sent": True,
            "accepted": True,
        }
        return OrderAttempt(**{**base, **overrides})

    passing_risk = (
        RiskCheck(seq=1, layer="kill_switch", passed=True),
        RiskCheck(seq=2, layer="daily_loss", passed=True),
        RiskCheck(seq=3, layer="liq_buffer", passed=True),
    )
    held = Unmanaged(
        side="long", qty=0.0003, source="flip_aborted", first_seen_bar_close_ts=T0 + 5 * DAY_MS
    )

    return [
        # 1 · ไม่ทำอะไร
        bar(1),
        # 2 · cold start ทางที่ 2 — เข้าไม้พร้อมวาง stop ที่ venue (decisions #17)
        bar(
            2,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            side="long",
            skip_reason=None,
            cold_start="trailing",
            judge_called=False,
            size_rule="cold_start",
            size_pct_formula=5.0,
            size_pct_final=5.0,
            capped=False,
            margin=5.0,
            notional=10.0,
            qty=0.00012903,
            ref_px=77_500.0,
            risk_checks=passing_risk,
            orders=(
                order(client_order_id="cane-btc-2-open", qty=0.00012903),
                order(
                    leg="stop",
                    order_side="sell",
                    order_type="stop_market",
                    reduce_only=True,
                    qty=0.00012903,
                    stop_px=74_000.0,
                    client_order_id="cane-btc-2-stop",
                    venue_order_id="v-stop-2",
                ),
            ),
            stop=Stop(action="placed", px=74_000.0, stop_order_id="v-stop-2"),
        ),
        # 3 · สัญญาณเดิมซ้ำขณะถือไม้อยู่แล้ว — ไม่เปิดทับ (spec/03:28-35)
        bar(3, zone="GREEN", state="BULLISH", long_signal=True, skip_reason="already_positioned"),
        # 4 · flip ครบสองขา · ทั้งสองขาเป็น `sell` เพราะโหมด one-way (spec/06:94)
        bar(
            4,
            zone="RED",
            state="BULLISH",
            short_signal=True,
            side="short",
            skip_reason=None,
            judge_called=True,
            factors_present=2,
            size_rule="confluence",
            size_pct_formula=45.0,
            size_pct_final=45.0,
            capped=False,
            margin=27.0,
            notional=54.0,
            qty=0.00069677,
            ref_px=77_500.0,
            verdicts=(
                Verdict(factor="CHANNEL_BREAKDOWN", side="short", present=True, cached=False),
                Verdict(factor="LOWER_HIGH", side="short", present=True, cached=False),
                Verdict(factor="BUYING_EXHAUSTION", side="short", present=False, cached=False),
            ),
            risk_checks=passing_risk,
            orders=(
                order(
                    leg="close",
                    order_side="sell",
                    reduce_only=True,
                    qty=0.00012903,
                    client_order_id="cane-btc-4-close",
                ),
                order(
                    order_side="sell",
                    qty=0.00069677,
                    client_order_id="cane-btc-4-open",
                ),
            ),
            flip=Flip(
                close_qty_intended=0.00012903,
                close_qty_filled=0.00012903,
                residual_qty=0.0,
                aborted=False,
            ),
            stop=Stop(action="unchanged"),
        ),
        # 5 · ขา 1 ไม่ fill ครบ → ยกเลิกขา 2 · ของค้างเกิดที่นี่ (spec/03:64, decisions #19)
        bar(
            5,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            skip_reason="flip_aborted",
            orders=(
                order(
                    leg="close",
                    order_side="buy",
                    reduce_only=True,
                    qty=0.00069677,
                    client_order_id="cane-btc-5-close",
                ),
            ),
            flip=Flip(
                close_qty_intended=0.00069677,
                close_qty_filled=0.00039677,
                residual_qty=0.0003,
                residual_side="long",
                aborted=True,
            ),
            unmanaged=(held,),
        ),
        # 6-8 · ของค้างถูกเขียนซ้ำทุกแท่งจนกว่าคนจะปิด — ไม่ใช่ event ครั้งเดียว
        bar(6, unmanaged=(held,)),
        bar(7, unmanaged=(held,)),
        bar(8, unmanaged=(held,)),
        # 9 · risk ปฏิเสธที่ชั้นสาม — ชั้นที่สี่ไม่มีเพราะไม่ถูกเรียก
        bar(
            9,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            skip_reason="risk_rejected",
            judge_called=True,
            factors_present=1,
            size_rule="confluence",
            size_pct_formula=25.0,
            size_pct_final=25.0,
            capped=False,
            margin=15.0,
            notional=30.0,
            qty=0.00038709,
            ref_px=77_500.0,
            risk_checks=(
                RiskCheck(seq=1, layer="kill_switch", passed=True),
                RiskCheck(seq=2, layer="daily_loss", passed=True, value=1.2, limit_value=3.0),
                RiskCheck(seq=3, layer="liq_buffer", passed=False, value=12.0, limit_value=25.0),
            ),
            unmanaged=(held,),
        ),
        # 10 · กฎลุงโฉลก — ไม่ใช่แท่งสัญญาณ ปฏิเสธก่อนถึงชั้น risk (spec/08:37)
        bar(
            10,
            zone="GREEN",
            state="BULLISH",
            skip_reason="cane_rule",
            size_rule="none",
            unmanaged=(held,),
        ),
        # 11 · venue ปฏิเสธออเดอร์เปิด
        bar(
            11,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            skip_reason="order_error",
            judge_called=True,
            factors_present=0,
            llm_fallback=True,
            llm_fallback_reason="timeout",
            size_rule="confluence",
            size_pct_formula=5.0,
            size_pct_final=5.0,
            capped=False,
            risk_checks=passing_risk,
            orders=(
                order(
                    client_order_id="cane-btc-11-open",
                    accepted=False,
                    error="insufficient margin",
                ),
            ),
            unmanaged=(held,),
        ),
        # 12 · ครบสาม factor → สูตรให้ 65 แต่ชนเพดาน 50 · **ย่อ ไม่ใช่ปฏิเสธ** (spec/08:58-60)
        bar(
            12,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            side="long",
            skip_reason=None,
            judge_called=True,
            factors_present=3,
            size_rule="confluence",
            size_pct_formula=65.0,
            size_pct_final=50.0,
            capped=True,
            margin=50.0,
            notional=100.0,
            qty=0.00129032,
            ref_px=77_500.0,
            verdicts=(
                Verdict(factor="CHANNEL_BREAKOUT", side="long", present=True, cached=False),
                Verdict(factor="RETAIL_CAPITULATION", side="long", present=True, cached=True),
                Verdict(factor="HIGHER_LOW", side="long", present=True, cached=False),
            ),
            risk_checks=passing_risk,
            orders=(order(client_order_id="cane-btc-12-open", qty=0.00129032),),
            unmanaged=(held,),
        ),
    ]


def _spot_run(version_id) -> list[DecisionRecord]:
    """เจ็ดแท่งของ ETH บน spot — long-only ไม่มี flip ไม่มี funding ไม่มี leverage"""

    def bar(index, **overrides):
        return quiet_bar(
            version_id,
            market=SPOT,
            symbol=ETH,
            bar_close_ts=T0 + index * DAY_MS,
            decided_ts=T0 + index * DAY_MS + 500,
            close_px=3_100.0,
            **overrides,
        )

    two_layers = (
        RiskCheck(seq=1, layer="kill_switch", passed=True),
        RiskCheck(seq=2, layer="daily_loss", passed=True),
    )

    return [
        bar(1),
        # 2 · เข้าไม้ฝั่งเดียวที่ spot มี
        bar(
            2,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            side="long",
            skip_reason=None,
            judge_called=True,
            factors_present=2,
            size_rule="confluence",
            size_pct_formula=45.0,
            size_pct_final=45.0,
            capped=False,
            margin=36.0,
            notional=36.0,
            qty=0.01161290,
            ref_px=3_100.0,
            verdicts=(
                Verdict(factor="CHANNEL_BREAKOUT", side="long", present=True, cached=False),
                Verdict(factor="HIGHER_LOW", side="long", present=True, cached=False),
                Verdict(factor="RETAIL_CAPITULATION", side="long", present=False, cached=False),
            ),
            risk_checks=two_layers,
            orders=(
                OrderAttempt(
                    leg="open",
                    order_side="buy",
                    order_type="market",
                    reduce_only=False,
                    qty=0.01161290,
                    client_order_id="cane-eth-2-open",
                    sent=True,
                    accepted=True,
                ),
            ),
        ),
        bar(3, zone="GREEN", state="BULLISH", long_signal=True, skip_reason="already_positioned"),
        # 4 · สัญญาณแดงบน spot = **ขายออกให้แบน จบ** ไม่มีขาเปิดตาม (spec/03:20)
        bar(
            4,
            zone="RED",
            state="BULLISH",
            short_signal=True,
            skip_reason="short_disabled",
            orders=(
                OrderAttempt(
                    leg="close",
                    order_side="sell",
                    order_type="market",
                    reduce_only=False,
                    qty=0.01161290,
                    client_order_id="cane-eth-4-close",
                    sent=True,
                    accepted=True,
                ),
            ),
        ),
        bar(5),
        # 6 · risk ปฏิเสธ — สองชั้นคือชุดที่ครบของ spot ไม่ใช่ลำดับที่มีช่อง
        bar(
            6,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            skip_reason="risk_rejected",
            judge_called=True,
            factors_present=1,
            size_rule="confluence",
            size_pct_formula=25.0,
            size_pct_final=25.0,
            capped=False,
            risk_checks=(
                RiskCheck(seq=1, layer="kill_switch", passed=True),
                RiskCheck(seq=2, layer="daily_loss", passed=False, value=3.4, limit_value=3.0),
            ),
        ),
        # 7 · cold start ทางที่ 1 แล้ว RR ไม่ถึง 2:1 (spec/03:135)
        bar(
            7,
            zone="GREEN",
            state="BEARISH",
            long_signal=True,
            skip_reason="rr_too_low",
            cold_start="wait_1h",
            size_rule="cold_start",
        ),
    ]


def test_a_twenty_bar_run_reads_back_as_a_complete_sequence(
    db, live_version, paper_version
):
    """เกณฑ์เสร็จของใบ 03 — เขียนยี่สิบแท่งสองเหรียญแล้วประกอบลำดับกลับมาให้ครบ

    ครอบทุกเส้นทางที่ใบสั่ง: ไม่ทำอะไร · เข้าไม้ · cold start พร้อม stop · flip ครบ ·
    flip abort แล้วมีของค้างตามหลังสามแท่ง · risk ปฏิเสธ · cane rule · order error ·
    ชนเพดาน · ขายออกให้แบนบน spot · dry run

    เส้นทาง `dry_run` ผูก config version ของ `paper` เพราะ `ck_config_settings_paper_dry_run`
    บังคับ `dry_run = true` ที่นั่นตายตัว ส่วนสิบเก้าแท่งที่เหลือผูกเวอร์ชันของ `live`
    ที่ประกอบเองให้ `dry_run = false` (ไฟล์ดิบตั้งเป็น true)
    """
    dry = DecisionRecord(
        profile="paper",
        market=SPOT,
        symbol=ETH,
        timeframe="1d",
        bar_close_ts=T0 + 8 * DAY_MS,
        decided_ts=T0 + 8 * DAY_MS + 500,
        config_version_id=paper_version,
        close_px=3_100.0,
        zone="GREEN",
        state="BEARISH",
        long_signal=True,
        short_signal=False,
        dry_run=True,
        skip_reason="dry_run",
        judge_called=True,
        factors_present=3,
        size_rule="confluence",
        size_pct_formula=65.0,
        size_pct_final=50.0,
        capped=True,
        margin=40.0,
        notional=40.0,
        qty=0.01290322,
        ref_px=3_100.0,
        risk_checks=(
            RiskCheck(seq=1, layer="kill_switch", passed=True),
            RiskCheck(seq=2, layer="daily_loss", passed=True),
        ),
        orders=(
            OrderAttempt(
                leg="open",
                order_side="buy",
                order_type="market",
                reduce_only=False,
                qty=0.01290322,
                client_order_id="cane-eth-8-open",
                sent=False,
                accepted=False,
            ),
        ),
    )

    script = [*_perp_run(live_version), *_spot_run(live_version), dry]
    assert len(script) == 20
    for record in script:
        repo.insert_decision(db, record)

    perp = repo.decisions_for(db, "live", PERP, BTC, "1d")
    spot = repo.decisions_for(db, "live", SPOT, ETH, "1d")
    paper = repo.decisions_for(db, "paper", SPOT, ETH, "1d")
    assert len(perp) + len(spot) + len(paper) == 20

    # ไม่มีแท่งหาย — แท่งที่ 1..12 ของ perp และ 1..7 ของ spot อยู่ครบและเรียงถูก
    assert [r.bar_close_ts for r in perp] == [T0 + i * DAY_MS for i in range(1, 13)]
    assert [r.bar_close_ts for r in spot] == [T0 + i * DAY_MS for i in range(1, 8)]

    # ทุกแถวที่อ่านกลับมาต้องยังเคารพ invariant ของ `skip_reason` (และกฎอื่นทั้งหมด)
    for record in [*perp, *spot, *paper]:
        repo.validate_record(record)

    # ลำดับบอกสถานะไม้ได้ตรงกับสคริปต์: เข้า long ที่ 2 → flip เป็น short ที่ 4 →
    # ปิดไม่ครบที่ 5 (ของค้าง) → เข้า long อีกครั้งที่ 12
    entered = [r.bar_close_ts for r in perp if r.skip_reason is None]
    assert entered == [T0 + 2 * DAY_MS, T0 + 4 * DAY_MS, T0 + 12 * DAY_MS]
    assert [r.side for r in perp if r.skip_reason is None] == ["long", "short", "long"]

    # ของค้างเขียนซ้ำทุกแท่งตั้งแต่แท่งที่ 5 → "ค้างมากี่แท่งแล้ว" เป็น count(*)
    stale = db.execute(
        select(func.count())
        .select_from(decision_unmanaged)
        .where(decision_unmanaged.c.first_seen_bar_close_ts == T0 + 5 * DAY_MS)
    ).scalar_one()
    assert stale == 8
    assert perp[4].flip is not None and perp[4].flip.residual_side == "long"

    # spot ไม่มี flip ไม่มี funding และไม่มีขาเปิดฝั่ง sell เลยทั้งชุด
    assert all(r.flip is None for r in spot)
    assert all(r.funding_rate is None and r.funding_next_ts is None for r in spot)
    assert all(
        o.order_side == "buy" for r in spot for o in r.orders if o.leg == "open"
    )
    assert [len(r.risk_checks) for r in spot if r.risk_checks] == [2, 2]

    # ชนเพดานแยกจากปัจจัยน้อยได้ (spec/05:68)
    capped = [r for r in perp if r.capped]
    assert len(capped) == 1
    assert (capped[0].size_pct_formula, capped[0].size_pct_final) == (65.0, 50.0)

    # dry run คำนวณครบทุกขั้นแต่ไม่มีคำสั่งหลุดออกไป (spec/06:80-84)
    assert paper[0].size_pct_final == 50.0
    assert paper[0].orders[0].sent is False
