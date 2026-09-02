"""config ในฐานข้อมูล — เวอร์ชันเพิ่มได้ ทับไม่ได้ และ CHECK ต้องปฏิเสธจริง

สองข้อที่ไฟล์นี้เฝ้า:

1. **เวอร์ชันเก่าอ่านได้เสมอ** นั่นคือเหตุผลทั้งหมดของการเก็บเป็นเวอร์ชัน —
   `decisions.config_version_id` (ใบ 03) ต้องพาไปอ่านค่าที่ใช้ตัดสินแท่งนั้นได้จริง
2. **ข้อบังคับที่ย้ายจากโค้ดขึ้นมาเป็นของ schema ต้องมีผลจริง** ทุกข้อที่นี่ยิงให้ล้ม
   ไม่ใช่แค่ประกาศไว้ใน migration แล้วเชื่อว่าใช้ได้
"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from cane.config import ConfigError, load_profile
from cane.db.repo import config as repo
from cane.db.schema import CONFIG_TABLES, config_settings, config_versions

pytestmark = pytest.mark.db

TS = 1_788_000_000_000


@pytest.fixture(autouse=True)
def clean_config(db):
    """เริ่มทุกเทสต์จากตาราง config ที่ว่าง **ในทรานแซกชันของเทสต์เอง**

    ต่างจากตารางข้อเท็จจริง ตาราง config ของเครื่อง dev **มีข้อมูลจริงค้างอยู่** —
    `cane db seed` เป็นขั้นตอนตั้งเครื่องที่ทุกคนรัน เวอร์ชัน v1 ของทั้งสอง profile
    จึง committed อยู่ก่อนเทสต์เริ่ม · เทสต์ที่เขียนแบบสมมติว่าตารางว่างจะพังด้วย
    `UNIQUE (profile, version)` แล้วอ่านเหมือนบั๊กของ schema ทั้งที่เป็นข้อสมมติที่ผิด

    การลบที่นี่ปลอดภัยเพราะ fixture `db` rollback ทุกอย่างท้ายเทสต์ — ข้อมูลที่
    dev seed ไว้ยังอยู่ครบหลังรันเทสต์เสร็จ
    """
    for table in reversed(CONFIG_TABLES):
        db.execute(table.delete())


@pytest.fixture
def paper():
    return load_profile("config/paper.toml")


@pytest.fixture
def live():
    return load_profile("config/live.toml")


def head_row(db, profile="live", **overrides):
    """หัวเวอร์ชันเปล่าหนึ่งแถว สำหรับเทสต์ที่สนใจแค่ตารางลูก"""
    values = {
        "profile": profile,
        "version": 1,
        "source": "toml_seed",
        "created_ts": TS,
        "is_active": False,
        **overrides,
    }
    return db.execute(
        config_versions.insert().values(**values).returning(config_versions.c.id)
    ).scalar_one()


def settings_row(db, version_id, profile="live", **overrides):
    values = {
        "config_version_id": version_id,
        "profile": profile,
        "timeframe": "1d",
        "market": "usdtm_perp",
        "base_pct": 10,
        "dry_run": True,
        "allow_short": True,
        "created_ts": TS,
        **overrides,
    }
    db.execute(config_settings.insert().values(**values))


# ── ไป-กลับ ─────────────────────────────────────────────────────────────────


def test_a_profile_survives_the_round_trip(db, paper):
    head = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)

    assert repo.settings_of(db, head.id) == paper


def test_the_head_records_where_the_version_came_from(db, paper):
    head = repo.insert_version(
        db, paper, source="toml_seed", note="seed แรก", created_ts=TS
    )

    assert (head.profile, head.version, head.source) == ("paper", 1, "toml_seed")
    assert head.note == "seed แรก"
    # ไม่ใช่คนกด — FK ไปตาราง users มาพร้อมใบ 20
    assert head.created_by_user_id is None
    # บันทึกแล้วยังไม่เปิดใช้ การเปิดใช้เป็นการตัดสินใจอีกครั้งหนึ่ง
    assert head.is_active is False


def test_versions_count_up_per_profile_independently(db, paper, live):
    first = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)
    second = repo.insert_version(db, paper, source="console", created_ts=TS)
    other = repo.insert_version(db, live, source="toml_seed", created_ts=TS)

    assert (first.version, second.version) == (1, 2)
    assert other.version == 1  # live เริ่มนับใหม่ ไม่ต่อจาก paper


def test_an_unknown_source_is_refused_before_it_reaches_the_database(db, paper):
    with pytest.raises(ValueError, match="source"):
        repo.insert_version(db, paper, source="mystery", created_ts=TS)


# ── เวอร์ชันเก่าต้องอ่านได้ต่อไป ─────────────────────────────────────────────


def test_activating_a_new_version_leaves_the_old_one_readable(db, paper):
    old = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)
    repo.activate(db, old.id)
    changed = paper.model_copy(update={"base_pct": 12.0})
    new = repo.insert_version(db, changed, source="console", created_ts=TS)
    repo.activate(db, new.id)

    assert repo.active_settings(db, "paper").base_pct == 12.0
    # ค่าที่ใช้ตัดสินแท่งเมื่อวานยังอ่านได้ — ไม่ถูกเวอร์ชันใหม่ทับ
    assert repo.settings_of(db, old.id).base_pct == 10.0
    assert [v.version for v in repo.versions(db, "paper")] == [2, 1]


def test_only_one_version_of_a_profile_is_active(db, paper):
    first = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)
    second = repo.insert_version(db, paper, source="console", created_ts=TS)

    repo.activate(db, first.id)
    repo.activate(db, second.id)

    active = db.execute(
        select(config_versions.c.version).where(
            config_versions.c.profile == "paper", config_versions.c.is_active
        )
    ).all()
    assert [row.version for row in active] == [2]


def test_two_active_versions_cannot_be_forced_in(db, paper):
    """ถ้าโค้ดพลาด partial unique index ต้องเป็นด่านที่ปฏิเสธ ไม่ใช่ความหวัง"""
    first = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)
    second = repo.insert_version(db, paper, source="console", created_ts=TS)
    repo.activate(db, first.id)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                config_versions.update()
                .where(config_versions.c.id == second.id)
                .values(is_active=True)
            )

    assert isinstance(caught.value.orig, psycopg.errors.UniqueViolation)


def test_activating_the_other_profile_does_not_touch_this_one(db, paper, live):
    paper_v = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)
    live_v = repo.insert_version(db, live, source="toml_seed", created_ts=TS)
    repo.activate(db, paper_v.id)
    repo.activate(db, live_v.id)

    assert repo.active_version(db, "paper").id == paper_v.id
    assert repo.active_version(db, "live").id == live_v.id


def test_nothing_active_means_nothing_to_trade_with(db):
    """ไม่มีเวอร์ชัน active = `None` ไม่ใช่ค่าตั้งต้น (fail-closed ตาม spec/06)"""
    assert repo.active_version(db, "live") is None
    assert repo.active_settings(db, "live") is None


def test_reading_a_version_that_does_not_exist_is_none(db):
    assert repo.settings_of(db, 999_999) is None


def test_activating_a_version_that_does_not_exist_raises(db):
    with pytest.raises(LookupError):
        repo.activate(db, 999_999)


# ── CHECK: ข้อบังคับที่ย้ายจากโค้ดขึ้นมาเป็นของ schema ────────────────────────


def test_paper_cannot_store_dry_run_false(db):
    """paper ต้องไม่มีทางส่งคำสั่งจริงได้เลย — ข้อนี้ต้องเป็นของ DB ไม่ใช่ของ validator

    validator เรียกได้ก็ลืมเรียกได้ CHECK ลืมไม่ได้
    """
    version_id = head_row(db, profile="paper")

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            settings_row(db, version_id, profile="paper", dry_run=False)

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_pct", 32),  # นอกช่วง 5–20
        ("base_pct", 1),
        ("market", "spot"),
        ("timeframe", "5m"),  # ไม่อยู่ใน TIMEFRAME_MS ของ data/ohlcv.py
        ("cold_start", "guess"),
    ],
)
def test_settings_outside_the_allowed_set_are_refused(db, field, value):
    version_id = head_row(db)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            settings_row(db, version_id, **{field: value})

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_a_risk_limit_left_out_is_refused(db):
    """risk limit ทุกคอลัมน์ `NOT NULL` — ตั้งไม่ครบ = บันทึกเวอร์ชันไม่ได้ = ไม่เทรด"""
    version_id = head_row(db)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO config_risk (config_version_id, profile,
                        max_position_pct_long, max_position_pct_short, max_leverage,
                        min_liq_buffer_pct, created_ts)
                    VALUES (:v, 'live', 50, 40, 3, 25, :ts)
                    """
                ),
                {"v": version_id, "ts": TS},
            )

    assert isinstance(caught.value.orig, psycopg.errors.NotNullViolation)


@pytest.mark.parametrize(
    ("columns", "values"),
    [
        # เปิด short แต่ไม่มีเงินของฝั่ง short เอง
        ("bucket_quote_long, leverage, allow_short, enabled", "100, 2, true, true"),
        # bucket ติดลบ
        ("bucket_quote_long, leverage, allow_short, enabled", "-1, 2, false, true"),
        ("bucket_quote_long, leverage, allow_short, enabled", "100, 0, false, true"),
    ],
)
def test_an_impossible_symbol_is_refused(db, columns, values):
    version_id = head_row(db)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    INSERT INTO config_symbols (config_version_id, profile, symbol,
                        {columns}, created_ts)
                    VALUES (:v, 'live', 'BTC/USDT', {values}, :ts)
                    """
                ),
                {"v": version_id, "ts": TS},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_position_pct_long", 150),  # เปอร์เซ็นต์เกิน 100
        ("max_position_pct_long", 0),
        ("max_position_pct_short", 150),
        ("max_leverage", 0),
        ("min_liq_buffer_pct", 100),  # บัฟเฟอร์ 100% = ไม่มีทางเปิดไม้ได้เลย
        ("min_liq_buffer_pct", 0),
        ("max_daily_loss_pct", 101),
        ("consecutive_loss_breaker", 0),  # ตัวตัดที่ 0 ไม้ = ตัดตลอดเวลา
    ],
)
def test_a_risk_limit_outside_its_range_is_refused(db, field, value):
    """ช่วงของ risk limit ทุกตัวเป็น CHECK — ไม่ใช่แค่กฎใน pydantic ที่ลืมเรียกได้"""
    version_id = head_row(db)
    values = {
        "max_position_pct_long": 50,
        "max_position_pct_short": 40,
        "max_leverage": 3,
        "min_liq_buffer_pct": 25,
        "max_daily_loss_pct": 3,
        "consecutive_loss_breaker": 2,
        field: value,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    INSERT INTO config_risk (config_version_id, profile,
                        {columns}, created_ts)
                    VALUES (:v, 'live', {placeholders}, :ts)
                    """
                ),
                {"v": version_id, "ts": TS, **values},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_a_zero_short_bucket_is_refused(db):
    """`0` ต่างจาก `NULL` — ไม่มีกระเป๋าฝั่ง short คือเว้นไว้ ไม่ใช่ตั้งเป็นศูนย์"""
    version_id = head_row(db)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    """
                    INSERT INTO config_symbols (config_version_id, profile, symbol,
                        bucket_quote_long, bucket_quote_short, leverage,
                        allow_short, enabled, created_ts)
                    VALUES (:v, 'live', 'BTC/USDT', 100, 0, 2, true, true, :ts)
                    """
                ),
                {"v": version_id, "ts": TS},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


@pytest.mark.parametrize(
    "values",
    [
        # ccxt ต้องรู้ venue
        "'ccxt', NULL, 'isolated', 'one_way', NULL",
        # เงินตั้งต้นจำลองไม่มีความหมายกับ broker จริง
        "'ccxt', 'binance', 'isolated', 'one_way', 10000",
        # hedge ทำให้ถือสวนกันได้ ซึ่งละเมิด "หนึ่งฝั่งต่อเหรียญเสมอ"
        "'paper', NULL, 'isolated', 'hedge', 10000",
        "'mystery', NULL, 'isolated', 'one_way', NULL",
        # เงินตั้งต้นติดลบไม่ใช่เงินตั้งต้น
        "'paper', NULL, 'isolated', 'one_way', -1",
    ],
)
def test_an_impossible_broker_is_refused(db, values):
    version_id = head_row(db)

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            db.execute(
                text(
                    f"""
                    INSERT INTO config_broker (config_version_id, profile, kind,
                        exchange, margin_mode, position_mode, seed_quote, created_ts)
                    VALUES (:v, 'live', {values}, :ts)
                    """
                ),
                {"v": version_id, "ts": TS},
            )

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_a_child_row_cannot_claim_a_different_profile_than_its_head(db):
    """`profile` ซ้ำอยู่บนลูกเพื่อให้เขียน CHECK ได้ — composite FK กันมันเพี้ยนจากหัว

    ถ้าลูกโกหก profile ได้ CHECK อย่าง "paper บังคับ dry_run" จะถูกหลบด้วยการ
    เขียนลูกว่าเป็น live ทั้งที่หัวเป็น paper
    """
    version_id = head_row(db, profile="paper")

    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            settings_row(db, version_id, profile="live", dry_run=False)

    assert isinstance(caught.value.orig, psycopg.errors.ForeignKeyViolation)


@pytest.mark.parametrize(("field", "value"), [("version", 0), ("source", "somewhere")])
def test_a_bad_version_head_is_refused(db, field, value):
    with pytest.raises(IntegrityError) as caught:
        with db.begin_nested():
            head_row(db, **{field: value})

    assert isinstance(caught.value.orig, psycopg.errors.CheckViolation)


def test_a_version_that_breaks_a_cross_table_rule_is_never_stored(db, paper):
    """`leverage` เกิน `max_leverage` เป็นกฎที่ DB เขียนเป็น CHECK ไม่ได้

    ถ้า repository เชื่อผู้เรียก เวอร์ชันแบบนี้จะเขียนลงได้แต่ `settings_of()`
    ปฏิเสธตอนอ่าน — เวอร์ชันที่เก็บแล้วอ่านไม่ได้ แย่กว่าการเขียนไม่สำเร็จ
    """
    over = paper.model_copy(
        update={"symbols": [paper.symbols[0].model_copy(update={"leverage": 9.0})]}
    )

    with pytest.raises(ConfigError):
        repo.insert_version(db, over, source="console", created_ts=TS)

    assert repo.versions(db, "paper") == []


# ── สิทธิ์: engine อ่าน config · console แก้ได้แต่ทับของเก่าไม่ได้ ───────────


def _as_role(conn, role: str) -> None:
    conn.execute(text(f'SET LOCAL ROLE "{role}"'))


def test_the_engine_role_can_read_config(db, paper):
    repo.insert_version(db, paper, source="toml_seed", created_ts=TS)

    with db.begin_nested():
        _as_role(db, "cane_engine")
        count = db.execute(text("SELECT count(*) FROM config_versions")).scalar_one()

    assert count == 1


def test_the_engine_role_cannot_write_config(db, paper):
    """engine ไม่ใช่คนแก้ config ของตัวเอง"""
    with pytest.raises(ProgrammingError) as caught:
        with db.begin_nested():
            _as_role(db, "cane_engine")
            repo.insert_version(db, paper, source="console", created_ts=TS)

    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)


def test_the_console_role_can_add_a_version_and_activate_it(db, paper):
    with db.begin_nested():
        _as_role(db, "cane_console")
        head = repo.insert_version(db, paper, source="console", created_ts=TS)
        repo.activate(db, head.id)
        active = repo.active_version(db, "paper")

    assert active.id == head.id


def test_the_console_role_cannot_rewrite_a_version_it_already_saved(db, paper):
    """`is_active` เป็นตัวชี้ที่ขยับได้ · เนื้อของเวอร์ชันขยับไม่ได้แม้แต่คอนโซล

    grant เป็นระดับคอลัมน์ — ถ้าให้ `UPDATE` ทั้งตาราง คำว่า immutable จะกลับไป
    เป็นข้อตกลงที่โค้ดผิดพลาดทับได้อีก
    """
    head = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)

    with pytest.raises(ProgrammingError) as caught:
        with db.begin_nested():
            _as_role(db, "cane_console")
            db.execute(
                config_versions.update()
                .where(config_versions.c.id == head.id)
                .values(note="เขียนทับ")
            )

    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)


def test_the_console_role_cannot_delete_a_version(db, paper):
    head = repo.insert_version(db, paper, source="toml_seed", created_ts=TS)

    with pytest.raises(ProgrammingError) as caught:
        with db.begin_nested():
            _as_role(db, "cane_console")
            db.execute(
                config_versions.delete().where(config_versions.c.id == head.id)
            )

    assert isinstance(caught.value.orig, psycopg.errors.InsufficientPrivilege)
