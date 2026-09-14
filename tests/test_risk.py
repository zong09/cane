"""ชั้น risk + kill switch — เกณฑ์ปิดใบสามข้อ และด่านที่ฐานข้อมูลบังคับเอง

เกณฑ์ของใบ 10 ตรงๆ:
1. test แต่ละ limit แยกกัน
2. kill switch ที่ latch แล้ว **ปลดเองไม่ได้แม้ restart**
3. stop/start engine ไม่แตะสถานะ kill switch

ข้อ 2 ครึ่งหลัง ("แม้ restart") ทดสอบด้วย **connection ใหม่** ไม่ใช่การอ่านตัวแปรเดิม —
สถานะอยู่ในตาราง ไม่ใช่ในหน่วยความจำของ process ซึ่งเป็นทั้งหมดที่ข้อนั้นหมายถึง

ข้อ 3 ยังไม่มี `engine_state` ให้ทดสอบจริง (ใบ 18) · สิ่งที่ทดสอบได้ตอนนี้คือด่านที่
**แข็งกว่าและอยู่ต่ำกว่าโค้ด**: trigger ของ migration 0008 ที่ปฏิเสธการปลดสวิตช์เมื่อ
ผู้กระทำไม่ใช่ `cane_console` · เส้นทางไหนก็ตามของ engine — รวมถึง start/stop ในอนาคต —
ปลดสวิตช์ไม่ได้แม้โค้ดจะสั่ง ซึ่งเป็นการรับประกันที่แรงกว่า "ไม่มีโค้ดเรียก"
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from cane.db.repo import killswitch as ks
from cane.risk import SKIPPED_LAYERS, check_all

PROFILE = "paper"
OK = {
    "day_pnl_pct": -1.0,
    "max_daily_loss_pct": 5.0,
    "entry_px": 100.0,
    "liquidation_px": 60.0,
    "min_liq_buffer_pct": 25.0,
}


def run(db, **overrides):
    base = {"profile": PROFILE, "market": "usdtm_perp", **OK}
    return check_all(db, **{**base, **overrides})


def as_role(conn, role: str) -> None:
    """สวม role ใน savepoint ของตัวเอง · `LOCAL` ถูกยกเลิกเมื่อจบทรานแซกชัน"""
    conn.execute(text(f'SET LOCAL ROLE "{role}"'))


# ── เกณฑ์ข้อ 1 — แต่ละ limit แยกกัน ───────────────────────────────────────────


@pytest.mark.db
def test_a_trade_that_clears_every_layer_records_all_three(db):
    got = run(db)
    assert got.passed is True
    assert [c.layer for c in got.checks] == ["kill_switch", "daily_loss", "liq_buffer"]
    assert [c.seq for c in got.checks] == [1, 2, 3]
    assert all(c.passed for c in got.checks)


@pytest.mark.db
def test_a_latched_kill_switch_stops_the_trade_at_the_first_layer(db):
    """ชั้นที่ไม่มีในตารางคือ**หลักฐานว่าลำดับถูกเคารพ** ไม่ใช่ข้อมูลที่หายไป

    ใบ 03 ตั้งใจให้อ่านย้อนหลังได้ว่าไม้ถูกปฏิเสธที่ด่านไหน — ถ้าเดินทุกชั้นเสมอแล้ว
    ค่อยดูว่าอันไหนไม่ผ่าน ข้อมูลนั้นจะหายไป และเราจะเสียเวลาคำนวณ liquidation ของ
    ไม้ที่ถูกห้ามไปตั้งแต่ด่านแรก
    """
    ks.latch(db, PROFILE, reason="เทสต์")
    got = run(db)

    assert got.passed is False
    assert got.blocked_by == "kill_switch"
    assert len(got.checks) == 1, "ต้องไม่เดินชั้นที่เหลือเลย"
    assert got.checks[0].detail == "latched"


@pytest.mark.db
def test_the_daily_loss_limit_counts_the_size_of_the_loss_not_the_signed_pnl(db):
    """`day_pnl_pct` ติดลบคือขาดทุน · เพดานเป็นบวก เทียบขนาดกัน

    spec/06 · นับ **mark-to-market** ไม่ใช่แค่ realized เพราะไม้ที่ยังเปิดค้างขาดทุน
    อยู่คือความเสี่ยงจริงที่ยังถืออยู่ · ที่นี่ไม่คำนวณให้ ผู้เรียกส่งมา
    """
    assert run(db, day_pnl_pct=-4.9).passed is True
    blocked = run(db, day_pnl_pct=-5.0)
    assert blocked.passed is False
    assert blocked.blocked_by == "daily_loss"
    assert blocked.checks[-1].value == pytest.approx(5.0)
    assert blocked.checks[-1].limit_value == pytest.approx(5.0)


@pytest.mark.db
def test_a_profitable_day_never_trips_the_loss_limit(db):
    assert run(db, day_pnl_pct=12.0).passed is True


@pytest.mark.db
def test_a_day_pnl_that_cannot_be_computed_is_fail_closed(db):
    """spec/06 · fail-closed — ทุกกรณีที่ระบบไม่มั่นใจ ให้ไม่ทำ ไม่ใช่ทำแบบเดา"""
    got = run(db, day_pnl_pct=None)
    assert got.passed is False
    assert got.blocked_by == "daily_loss"
    assert got.checks[-1].value is None


@pytest.mark.db
def test_the_liquidation_buffer_is_measured_as_a_percentage_of_the_entry(db):
    """คำนวณด้วยมือ · entry 100, liq 60 → ระยะ 40% · เพดาน 25% → ผ่าน

    ขยับ liq มาที่ 80 → ระยะ 20% ซึ่งต่ำกว่าเพดาน → ไม่ผ่าน
    """
    assert run(db, liquidation_px=60.0).checks[-1].value == pytest.approx(40.0)
    tight = run(db, liquidation_px=80.0)
    assert tight.passed is False
    assert tight.blocked_by == "liq_buffer"
    assert tight.checks[-1].value == pytest.approx(20.0)


@pytest.mark.db
def test_a_short_position_has_its_liquidation_above_the_entry(db):
    """ใช้ค่าสัมบูรณ์ · ชั้นนี้ถามว่า "ห่างแค่ไหน" ไม่ใช่ "ไปทางไหน"

    ถ้าลบตรงๆ ระยะของไม้ฝั่ง short จะติดลบแล้วไม่ผ่านทุกครั้ง ทั้งที่ห่างพอ
    """
    got = run(db, liquidation_px=140.0)
    assert got.checks[-1].value == pytest.approx(40.0)
    assert got.passed is True


@pytest.mark.db
def test_exactly_at_the_buffer_floor_is_accepted(db):
    """เพดานเขียนว่า "ต้อง ≥ ค่านี้" — พอดีจึงผ่าน"""
    assert run(db, liquidation_px=75.0).passed is True


@pytest.mark.db
def test_a_missing_liquidation_price_on_perp_refuses_the_trade(db):
    """spec/06 · "คำนวณระยะไม่ได้ = ไม่เปิด" · `min_liq_buffer_pct` คือสิ่งเดียวที่
    กัน liquidation ได้ ระบบไม่มีทางกันเรื่องนี้ด้วยตรรกะสัญญาณ
    """
    got = run(db, liquidation_px=None)
    assert got.passed is False
    assert got.blocked_by == "liq_buffer"
    assert "liquidation" in (got.checks[-1].detail or "")


# ── spot มีสองชั้น ไม่ใช่สามชั้นที่ผ่านฟรี ────────────────────────────────────


@pytest.mark.db
def test_a_spot_trade_never_walks_the_liquidation_layer_at_all(db):
    """ADR 26 · spot ไม่มี liquidation **อยู่จริง** ไม่ใช่มีแล้วคำนวณไม่ได้

    เรียกแล้วให้ผ่านเสมอจะทำให้รายงาน "ไม้ที่ผ่านด่าน liquidation" นับไม้ spot รวมเข้า
    ไปด้วย ซึ่งเป็นตัวเลขที่ไม่มีความหมาย · `liquidation_px = None` บน spot จึงไม่ใช่
    การ fail-closed แต่คือสภาพปกติ
    """
    got = run(db, market="spot", liquidation_px=None)

    assert got.passed is True
    assert [c.layer for c in got.checks] == ["kill_switch", "daily_loss"]
    assert SKIPPED_LAYERS["spot"] == ("liq_buffer",)


@pytest.mark.db
def test_an_unknown_market_is_refused_rather_than_silently_given_every_layer(db):
    with pytest.raises(ValueError, match="market"):
        run(db, market="options")


# ── เกณฑ์ข้อ 2 — latched แล้วปลดเองไม่ได้ แม้ restart ────────────────────────


@pytest.mark.db
def test_a_profile_that_was_never_touched_is_not_latched(db):
    """ไม่มีแถว = ไม่ latched · สวิตช์ที่ไม่เคยถูกแตะคือสวิตช์ที่ไม่ได้กด

    คืนคำตอบ ไม่ใช่โยน exception — นี่เป็นสภาพที่ถูกต้อง ไม่ใช่ข้อมูลที่หายไป
    """
    assert ks.is_latched(db, PROFILE) is False
    assert ks.read(db, PROFILE).reason is None


@pytest.mark.db
def test_the_switch_stays_latched_when_read_through_a_brand_new_connection(db_engine):
    """เกณฑ์ข้อ 2 · "แม้ restart" = สถานะอยู่ในตาราง ไม่ใช่ในหน่วยความจำของ process

    ทดสอบด้วย connection คนละตัวที่ commit แล้ว ซึ่งเป็นสิ่งที่ restart หมายถึงจริงๆ
    """
    with db_engine.begin() as first:
        first.execute(text("DELETE FROM kill_switch WHERE profile = 'paper'"))
        ks.latch(first, PROFILE, reason="แพ้ติดกันครบเกณฑ์", by="engine")

    try:
        with db_engine.connect() as second:
            state = ks.read(second, PROFILE)
            assert state.latched is True
            assert state.reason == "แพ้ติดกันครบเกณฑ์"
            assert state.latched_ts is not None
    finally:
        with db_engine.begin() as cleanup:
            cleanup.execute(text("DELETE FROM kill_switch WHERE profile = 'paper'"))


@pytest.mark.db
def test_pressing_the_button_again_never_fails_and_keeps_the_first_reason(db):
    """spec/10 §เขียน · การกดหยุดฉุกเฉินซ้ำต้องไม่เคยล้มเหลว

    **ครั้งแรกชนะ** — สาเหตุแรกคือสาเหตุที่อธิบายเหตุการณ์ · ถ้าครั้งที่สองเขียนทับ
    เหตุผลจะกลายเป็น "คนกดซ้ำ" ซึ่งไม่ได้บอกอะไรเลยว่าเกิดอะไรขึ้นตอนแรก
    """
    ks.latch(db, PROFILE, reason="เหตุแรก", by="engine")
    again = ks.latch(db, PROFILE, reason="คนกดซ้ำ", by="someone")

    assert again.latched is True
    assert again.reason == "เหตุแรก"
    assert again.latched_by == "engine"


@pytest.mark.db
def test_latching_without_a_reason_is_refused(db):
    """CHECK ของตารางบังคับด้วย · สวิตช์ที่ติดโดยไม่มีที่มาคือสิ่งที่คนไม่กล้าปลด
    และไม่กล้าปล่อยไว้
    """
    with pytest.raises(ValueError, match="เหตุผล"):
        ks.latch(db, PROFILE, reason="")


@pytest.mark.db
def test_the_table_itself_refuses_a_latched_row_with_no_story(db):
    """เขียน SQL ตรงๆ ข้าม repo — ฐานต้องยังปฏิเสธ"""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="latched_has_a_story"):
        db.execute(
            text(
                "INSERT INTO kill_switch (profile, latched, updated_ts) "
                "VALUES ('live', true, 1)"
            )
        )


# ── เกณฑ์ข้อ 3 — engine ปลดสวิตช์ไม่ได้ แม้โค้ดจะสั่ง ───────────────────────


@pytest.mark.db
def test_the_engine_role_cannot_unlatch_even_when_the_code_asks_it_to(db):
    """เกณฑ์ข้อ 3 · **ด่านนี้อยู่ต่ำกว่าโค้ด** — trigger ของ migration 0008

    ใบสั่งว่า "กด stop engine ไม่ปลด kill switch และกด start engine ก็ไม่ปลด" ·
    `engine_state` ยังไม่มี (ใบ 18) แต่สิ่งที่ทดสอบได้ตอนนี้แรงกว่าการไล่ดูว่ามีโค้ด
    เรียก `unlatch()` ตรงไหนบ้าง: **เส้นทางไหนก็ตามที่รันด้วยสิทธิ์ของ engine
    ปลดไม่ได้เลย** ต่อให้เขียนโค้ดเรียกตรงๆ

    GRANT ของ PostgreSQL แยก "ตั้งเป็น true" ออกจาก "ตั้งเป็น false" ไม่ได้ จึงต้อง
    เป็น trigger ไม่ใช่สิทธิ์
    """
    from sqlalchemy.exc import ProgrammingError

    ks.latch(db, PROFILE, reason="engine latch เอง", by="engine")

    savepoint = db.begin_nested()
    as_role(db, "cane_engine")
    with pytest.raises(ProgrammingError, match="คอนโซล"):
        ks.unlatch(db, PROFILE)
    savepoint.rollback()

    assert ks.is_latched(db, PROFILE) is True, "ต้องยัง latched อยู่"


@pytest.mark.db
def test_the_engine_role_can_still_latch_because_that_lowers_risk(db):
    """`consecutive_loss_breaker` แปลว่า engine ต้อง latch ได้เอง

    trigger กันแค่ทิศเดียว — การ**เพิ่ม**ความปลอดภัยไม่ถูกกั้น
    """
    savepoint = db.begin_nested()
    as_role(db, "cane_engine")
    state = ks.latch(db, PROFILE, reason="แพ้ติดกัน 4 ไม้", by="engine")
    assert state.latched is True
    savepoint.rollback()


@pytest.mark.db
def test_the_console_role_is_the_one_that_can_unlatch(db):
    """คู่ของข้อข้างบน · ถ้าข้อนี้ล้ม แปลว่า trigger กันกว้างเกินไปจนคนปลดไม่ได้เลย"""
    ks.latch(db, PROFILE, reason="เทสต์")

    savepoint = db.begin_nested()
    as_role(db, "cane_console")
    state = ks.unlatch(db, PROFILE)
    assert state.latched is False
    assert state.reason is None
    savepoint.rollback()


@pytest.mark.db
def test_unlatching_a_switch_that_is_not_latched_is_a_no_op_not_an_error(db):
    """เหตุผลเดียวกับการกด latch ซ้ำ — ปุ่มความปลอดภัยต้องไม่ทำให้คนสับสน

    ยิงตอนที่ยังไม่ latched ไม่มีแถวให้ update ด้วยซ้ำ trigger จึงไม่ทำงาน
    """
    assert ks.unlatch(db, PROFILE).latched is False
