"""ตาราง Postgres ทั้งหมด ประกาศด้วย SQLAlchemy Core (ไม่ใช้ ORM)

**ไม่ใช้ ORM โดยเจตนา** โปรเจกต์นี้ส่งข้อมูลไปมาด้วย frozen dataclass และฉีด
dependency เข้าทุกชั้น · session/identity-map ของ ORM ชนกับสไตล์นั้นตรงๆ และซ่อน
SQL ที่รันจริงไว้หลัง lazy-load ซึ่งเป็นสิ่งที่ระบบเทรดไม่ควรมี

ข้อตกลงร่วมของ schema (ใช้กับทุกโดเมนที่จะเพิ่มเข้ามาทีหลัง):

- ชื่อตารางเป็น `snake_case` พหูพจน์ · คอลัมน์เวลาลงท้าย `_ts` เป็น `BIGINT` epoch ms
- เงินและราคาเป็น `NUMERIC(24,8)` · เปอร์เซ็นต์ `NUMERIC(9,4)` · funding `NUMERIC(12,10)`
- ทุกตารางมี `created_ts` (เวลานาฬิกาตอน insert) **แยกจาก** `_ts` ที่เป็นเวลาของเหตุการณ์
  เพราะเวลาที่เหตุการณ์เกิดกับเวลาที่เราบันทึกมันไม่เท่ากัน และตอนไล่ปัญหาต้องใช้ทั้งคู่
- `profile` อยู่ใน unique key ของทุกตารางที่ผูกโหมด — query ที่ลืมกรอง profile จะชน
  กันเองตอนเขียน ไม่ใช่ไปโป๊ะตอนคอนโซลเอาไม้ paper ไปแสดงปนกับ live
- enum ที่ปิดจริงเป็น Postgres ENUM · ชุดที่จะโตอีก (`market`, `skip_reason`, `exit_reason`)
  เป็น `TEXT` + `CHECK` เพราะเพิ่มค่าใหม่ทำได้ใน migration ธรรมดา ไม่ต้อง `ALTER TYPE`
- `market` (`usdtm_perp` / `spot`) เป็น **ค่าต่อเหรียญ ไม่ใช่ค่าของทั้งระบบ** (decisions #26)
  และอยู่ในกุญแจของทุกตารางที่เก็บของต่อเหรียญ — เหรียญชื่อเดียวกันบนสองตลาดเป็นคนละของ
  · ค่าเขียนซ้ำใน `CHECK` ของแต่ละตารางโดยเจตนา ไม่ตั้งเป็นค่าคงที่ร่วมให้ `config/`
  import มาใช้ เพราะทิศ `config` → `db` ทำให้เกิด import cycle (ดู `repo/config.py`)
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql

metadata = MetaData()

#: `live` / `paper` — ชุดปิดจริง ไม่มีโหมดที่สาม (spec/07)
#: ประกาศไว้ที่นี่ให้ทุกโดเมนที่ผูกโหมดใช้ตัวเดียวกัน · `create_type=False` เพราะ
#: ตัว type ถูกสร้างใน migration แรกครั้งเดียว ไม่ใช่ตอนสร้างตารางที่อ้างถึงมัน
PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)

#: `isolated` / `cross` — ชุดปิดจริงของ margin mode บน USDT-M perp
MARGIN_MODE_T = postgresql.ENUM("isolated", "cross", name="margin_mode_t", create_type=False)

PRICE = Numeric(24, 8)
FUNDING_RATE = Numeric(12, 10)
#: เปอร์เซ็นต์และตัวคูณ — 4 ทศนิยมพอสำหรับค่าที่คนกรอกในฟอร์ม
PCT = Numeric(9, 4)


#: แท่งราคาที่ปิดแล้ว — **ไม่ผูก profile**
#:
#: paper กับ live อ่าน public feed เดียวกัน ถ้าแยกตารางต่อโหมดจะเทียบผลสองโหมด
#: บนข้อมูลชุดเดียวกันไม่ได้ ซึ่งเป็นเหตุผลหลักที่มี paper อยู่ · แท่งที่ปิดแล้ว
#: ไม่เปลี่ยนอีก (spec/07) การแชร์ตารางจึงไม่มีทางให้โหมดหนึ่งไปทับข้อมูลของอีกโหมด
#: — ส่วน cursor ของ replay ที่ต้องแยกต่อ profile เป็นเรื่องของ #12 ไม่ใช่ของตารางนี้
bars = Table(
    "bars",
    metadata,
    # `market` อยู่ในกุญแจเพราะ `BTC/USDT` บน spot กับบน perp เป็นคนละแท่งราคา —
    # `store_symbol()` ตัด `:USDT` ทิ้งเพื่อให้ตารางเก็บรูปเดียวกับที่ config เขียน
    # (spec/07) ถ้าไม่มีคอลัมน์นี้ สองตลาดจะยุบเป็นแถวเดียวกันแล้ว indicator จะคำนวณ
    # บนแท่งที่ปนกันสองตลาดโดยไม่มีอะไรส่งเสียง · เก็บเป็นคอลัมน์ไม่ใช่เข้ารหัสไว้ใน
    # สตริง symbol เพราะ `WHERE market = 'spot'` ต้องเขียนได้ ไม่ใช่ต้อง `LIKE '%:USDT'`
    Column("market", Text, primary_key=True),
    Column("symbol", Text, primary_key=True),
    Column("timeframe", Text, primary_key=True),
    Column("open_ts", BigInteger, primary_key=True),
    # เก็บ close_ts ลงตารางแทนการคำนวณจาก timeframe ตอนอ่าน ให้ตรงกับ `Bar` ที่
    # ถือ close_ts ไว้บนตัวแท่งเอง — แท่งอธิบายตัวเองได้โดยไม่ต้องพก timeframe ไปด้วย
    Column("close_ts", BigInteger, nullable=False),
    Column("open", PRICE, nullable=False),
    Column("high", PRICE, nullable=False),
    Column("low", PRICE, nullable=False),
    Column("close", PRICE, nullable=False),
    Column("volume", PRICE, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    CheckConstraint("close_ts > open_ts", name="ck_bars_close_after_open"),
    # สามข้อล่างนี้เป็นความจริงของนิยาม "แท่งราคา" ไม่ใช่การเดาพฤติกรรม venue
    # ถ้า feed ส่งของที่ขัดข้อนี้มา เราต้องการให้มันล้มดังตอน insert ไม่ใช่ให้
    # indicator ไปคำนวณต่อบนแท่งที่เป็นไปไม่ได้แล้วได้สัญญาณที่ดูปกติ
    CheckConstraint("high >= low", name="ck_bars_high_ge_low"),
    CheckConstraint("high >= open AND high >= close", name="ck_bars_high_is_max"),
    CheckConstraint("low <= open AND low <= close", name="ck_bars_low_is_min"),
    CheckConstraint("volume >= 0", name="ck_bars_volume_nonneg"),
    CheckConstraint("market IN ('usdtm_perp', 'spot')", name="ck_bars_market"),
)


#: การสังเกต funding rate หนึ่งครั้ง — บันทึกทั้งตอนได้ค่าและตอนดึงไม่ได้
#:
#: **ตารางนี้เป็นของ `usdtm_perp` เท่านั้น จึงไม่มีคอลัมน์ `market`** — spot ไม่มี funding
#: อยู่จริง ไม่ใช่มีแล้วเป็นศูนย์ แถวในตารางนี้จึงไม่กำกวมแม้ไม่ระบุตลาด · ถ้าวันหนึ่ง
#: มีตลาดที่สามที่มี funding ด้วย ตอนนั้นค่อยเพิ่มคอลัมน์ อย่าเดาไว้ก่อน
#:
#: `CHECK` ตัวนั้นคือกฎของ `data/funding.py` ("ดึงไม่ได้ = บันทึกว่าไม่มีข้อมูล
#: **ห้ามเดาเป็น 0**") ที่ยกจาก docstring ขึ้นมาเป็นข้อบังคับของ schema — เขียน
#: `rate = 0` ตอนดึงไม่ได้ทำไม่ได้อีกแล้ว ต้นทุนที่หายต้องหายเสียงดัง
#:
#: ตั้งใจ **ไม่ใส่ UNIQUE** บน `(symbol, observed_ts)` — สองแถวของเวลาเดียวกันคือ
#: หลักฐานว่ามีการดึงซ้ำ (โปรเซสรีสตาร์ทกลางแท่ง) ไม่ใช่ข้อเท็จจริงเดียวกันสองใบ
#: ถ้าใส่ UNIQUE แล้ว upsert แถวแรกจะถูกทับและร่องรอยการรีสตาร์ทจะหายไป
funding_observations = Table(
    "funding_observations",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("symbol", Text, nullable=False),
    Column("observed_ts", BigInteger, nullable=False),
    Column("rate", FUNDING_RATE),
    Column("next_funding_ts", BigInteger),
    Column("unavailable_reason", Text),
    Column("created_ts", BigInteger, nullable=False),
    CheckConstraint(
        "(rate IS NOT NULL) <> (unavailable_reason IS NOT NULL)",
        name="ck_funding_observations_rate_xor_reason",
    ),
    Index("ix_funding_observations_symbol_ts", "symbol", "observed_ts"),
)


# ── config — versioned, immutable ───────────────────────────────────────────
#
# รากของการออกแบบส่วนนี้: **ทุกการแก้ config สร้างเวอร์ชันใหม่ ไม่ทับของเก่า**
#
# ของที่ได้กลับมาและเป็นเหตุผลหลักที่คุ้ม: `decisions.config_version_id` (ใบ 03) จะเป็น FK
# ตรงไปที่เวอร์ชันที่ใช้ตัดสินแท่งนั้น ทำให้ไม่ต้อง snapshot `base_pct` / `bucket_quote` /
# `max_position_pct` ลงทุกแถวบันทึกอีก · ปัญหาที่ ADR 18 สร้างไว้โดยไม่ได้ตั้งใจ (คอนโซล
# แก้ไฟล์แล้วโหลดใหม่ → เวลาของ git history ไม่ตรงกับ `bar_close_ts` จึงตรวจเลขย้อนหลัง
# ไม่ได้) หายไปโดยโครงสร้าง ไม่ใช่หายไปเพราะมีคนคอยระวัง
#
# **ราคาที่จ่ายและต้องรู้ให้ชัด:** การรีวิว config ผ่าน `git diff` หายไป (`decisions.md`
# ข้อ 18 ตั้งใจใช้มันเป็นร่องรอย) `config_versions` ที่ immutable + `created_by_user_id`
# แทนได้และผูกกับเวลาของแท่งได้ดีกว่า แต่ไม่มีหน้า diff ให้คนรีวิวก่อน merge อีกแล้ว

#: หัวของทุกเวอร์ชัน · เนื้อของแถวนี้ไม่ถูกแก้ ยกเว้น `is_active` ที่เป็น **ตัวชี้**
#: ว่าเวอร์ชันไหนกำลังใช้อยู่ — คอนโซลจึงได้สิทธิ์ `UPDATE` เฉพาะคอลัมน์นั้นคอลัมน์เดียว
#: (grant ระดับคอลัมน์ใน migration) ส่วนคอลัมน์อื่นแก้ไม่ได้เลยแม้แต่คอนโซล
config_versions = Table(
    "config_versions",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("profile", PROFILE_T, nullable=False),
    Column("version", Integer, nullable=False),
    Column("source", Text, nullable=False),
    Column("note", Text),
    Column("created_ts", BigInteger, nullable=False),
    # FK ไปตาราง `users` มาพร้อมใบ 20 ที่เป็นคนสร้างตารางนั้น · ตอนนี้เป็นเลขเปล่า
    # และ `NULL` = ไม่ใช่คนกด (seed จาก TOML หรือ migration)
    Column("created_by_user_id", BigInteger),
    Column("is_active", Boolean, nullable=False, server_default=text("false")),
    UniqueConstraint("profile", "version", name="uq_config_versions_profile_version"),
    # ให้ตารางลูกอ้างกลับได้แบบ composite เพื่อกันคอลัมน์ profile ของลูกเพี้ยนจากหัว
    UniqueConstraint("id", "profile", name="uq_config_versions_id_profile"),
    CheckConstraint("version > 0", name="ck_config_versions_version_positive"),
    CheckConstraint(
        "source IN ('toml_seed', 'console', 'migration')",
        name="ck_config_versions_source",
    ),
    # active ได้ profile ละหนึ่งเวอร์ชันเท่านั้น — partial unique index ทำให้
    # "เผลอเปิดสองเวอร์ชันพร้อมกัน" เป็นไปไม่ได้ ไม่ใช่เรื่องที่ต้องคอยตรวจ
    Index(
        "uq_config_versions_active",
        "profile",
        unique=True,
        postgresql_where=text("is_active"),
    ),
)


def _version_fk() -> tuple[Column, Column]:
    """คอลัมน์ที่ตารางลูกทุกตัวใช้ผูกกลับไปที่หัวเวอร์ชัน

    `profile` ซ้ำอยู่บนลูกทุกตัวโดยเจตนา — CHECK อย่าง "paper บังคับ dry_run" ต้องเห็น
    profile ในแถวเดียวกันจึงเขียนได้ · ความซ้ำนั้นเพี้ยนจากหัวไม่ได้เพราะ FK เป็นแบบ
    composite `(config_version_id, profile)` ชี้ไปที่ `UNIQUE (id, profile)` ของหัว
    """
    return (
        Column("config_version_id", BigInteger, primary_key=True),
        Column("profile", PROFILE_T, nullable=False),
    )


_CHILD_FK = "config_versions.id"


config_settings = Table(
    "config_settings",
    metadata,
    *_version_fk(),
    Column("timeframe", Text, nullable=False),
    # NULL = ไม่เข้าเส้นทาง cold start เลย (fail-closed ตาม spec/03)
    Column("cold_start", Text),
    Column("base_pct", PCT, nullable=False),
    Column("dry_run", Boolean, nullable=False),
    Column("allow_short", Boolean, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["config_version_id", "profile"],
        [_CHILD_FK, "config_versions.profile"],
        name="fk_config_settings_version",
    ),
    CheckConstraint("base_pct BETWEEN 5 AND 20", name="ck_config_settings_base_pct"),
    # paper บังคับ dry_run — profile ที่ไม่มีทางส่งคำสั่งจริงได้เลย ต้องเป็นข้อบังคับ
    # ของ DB ไม่ใช่ของ validator ที่ลืมเรียกได้
    CheckConstraint("profile <> 'paper' OR dry_run", name="ck_config_settings_paper_dry_run"),
    CheckConstraint(
        "cold_start IS NULL OR cold_start IN ('wait_1h', 'trailing', 'skip')",
        name="ck_config_settings_cold_start",
    ),
    # timeframe ที่ไม่รู้จักต้องดังตอนบันทึก ไม่ใช่ตอน `timeframe_ms()` ยก ValueError
    # กลางรอบการตัดสินใจ — ชุดนี้ตรงกับ `TIMEFRAME_MS` ใน data/ohlcv.py
    CheckConstraint("timeframe IN ('1h', '1d')", name="ck_config_settings_timeframe"),
)


config_symbols = Table(
    "config_symbols",
    metadata,
    Column("config_version_id", BigInteger, primary_key=True),
    Column("symbol", Text, primary_key=True),
    Column("profile", PROFILE_T, nullable=False),
    #: `usdtm_perp` / `spot` — **ค่าต่อเหรียญ ไม่ใช่ค่าของทั้ง profile** (decisions #26)
    #: เหรียญหนึ่งเลือกได้ตลาดเดียว แต่ profile เดียวถือ BTC บน perp และ ETH บน spot ได้
    Column("market", Text, nullable=False),
    Column("bucket_quote_long", PRICE, nullable=False),
    # เว้นได้ = เทรดฝั่ง long อย่างเดียว
    Column("bucket_quote_short", PRICE),
    Column("leverage", PCT, nullable=False),
    Column("allow_short", Boolean, nullable=False),
    # false = พักไว้ คงบล็อกไว้แต่ข้ามในรอบคำนวณ
    Column("enabled", Boolean, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["config_version_id", "profile"],
        [_CHILD_FK, "config_versions.profile"],
        name="fk_config_symbols_version",
    ),
    CheckConstraint("bucket_quote_long > 0", name="ck_config_symbols_bucket_long"),
    CheckConstraint(
        "bucket_quote_short IS NULL OR bucket_quote_short > 0",
        name="ck_config_symbols_bucket_short",
    ),
    CheckConstraint("leverage > 0", name="ck_config_symbols_leverage"),
    # ฝั่ง short ต้องมีเงินของตัวเอง ไม่ยืมจากฝั่ง long (spec/05 แยกกระเป๋าต่อฝั่ง)
    CheckConstraint(
        "NOT allow_short OR bucket_quote_short IS NOT NULL",
        name="ck_config_symbols_short_needs_bucket",
    ),
    CheckConstraint("market IN ('usdtm_perp', 'spot')", name="ck_config_symbols_market"),
    # สามข้อล่างนี้ไม่ใช่การตั้งกฎ แต่เป็นการเขียนสิ่งที่ตลาด spot **ไม่มี** ลงไปให้ฐาน
    # ปฏิเสธแทนที่จะให้โค้ดชั้นบนคอยจำ — บน spot ขายชอร์ตไม่ได้ ไม่มี leverage และ
    # ไม่มีกระเป๋าฝั่ง short · ค่าที่ขัดข้อพวกนี้ไม่ได้ "ตั้งผิด" แต่ **เป็นไปไม่ได้**
    CheckConstraint(
        "market <> 'spot' OR NOT allow_short", name="ck_config_symbols_spot_no_short"
    ),
    # `leverage = 1` ไม่ใช่ NULL เพราะสูตร `notional = margin × leverage` (spec/05:16)
    # ต้องเดินเส้นทางเดียวทั้งสองตลาด — ตัวคูณที่เป็นหนึ่งทำให้ notional = margin เอง
    CheckConstraint(
        "market <> 'spot' OR leverage = 1", name="ck_config_symbols_spot_no_leverage"
    ),
    CheckConstraint(
        "market <> 'spot' OR bucket_quote_short IS NULL",
        name="ck_config_symbols_spot_no_short_bucket",
    ),
)


#: risk limit **ทุกคอลัมน์ NOT NULL** — fail-closed ของ spec/06 กลายเป็นข้อบังคับของ
#: schema ไม่ใช่แค่การไม่ใส่ default ใน pydantic · ตั้งไม่ครบ = บันทึกเวอร์ชันไม่ได้
#: = ไม่มีเวอร์ชันให้ activate = ไม่เทรด ซึ่งเป็นลำดับที่ต้องการ
config_risk = Table(
    "config_risk",
    metadata,
    *_version_fk(),
    Column("max_position_pct_long", PCT, nullable=False),
    Column("max_position_pct_short", PCT, nullable=False),
    Column("max_leverage", PCT, nullable=False),
    Column("min_liq_buffer_pct", PCT, nullable=False),
    Column("max_daily_loss_pct", PCT, nullable=False),
    Column("consecutive_loss_breaker", Integer, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["config_version_id", "profile"],
        [_CHILD_FK, "config_versions.profile"],
        name="fk_config_risk_version",
    ),
    CheckConstraint(
        "max_position_pct_long > 0 AND max_position_pct_long <= 100",
        name="ck_config_risk_pct_long",
    ),
    CheckConstraint(
        "max_position_pct_short > 0 AND max_position_pct_short <= 100",
        name="ck_config_risk_pct_short",
    ),
    CheckConstraint("max_leverage > 0", name="ck_config_risk_max_leverage"),
    CheckConstraint(
        "min_liq_buffer_pct > 0 AND min_liq_buffer_pct < 100",
        name="ck_config_risk_liq_buffer",
    ),
    CheckConstraint(
        "max_daily_loss_pct > 0 AND max_daily_loss_pct <= 100",
        name="ck_config_risk_daily_loss",
    ),
    CheckConstraint(
        "consecutive_loss_breaker > 0", name="ck_config_risk_loss_breaker"
    ),
)


config_broker = Table(
    "config_broker",
    metadata,
    *_version_fk(),
    Column("kind", Text, nullable=False),
    Column("exchange", Text),
    Column("margin_mode", MARGIN_MODE_T, nullable=False),
    Column("position_mode", Text, nullable=False),
    Column("seed_quote", PRICE),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["config_version_id", "profile"],
        [_CHILD_FK, "config_versions.profile"],
        name="fk_config_broker_version",
    ),
    CheckConstraint("kind IN ('ccxt', 'paper')", name="ck_config_broker_kind"),
    # ต้องรู้ venue ก่อนจึงจะต่อผ่าน ccxt ได้
    CheckConstraint("kind <> 'ccxt' OR exchange IS NOT NULL", name="ck_config_broker_exchange"),
    # เงินตั้งต้นจำลองไม่มีความหมายกับ broker จริง
    CheckConstraint("kind = 'paper' OR seed_quote IS NULL", name="ck_config_broker_seed_quote"),
    CheckConstraint("seed_quote IS NULL OR seed_quote > 0", name="ck_config_broker_seed_positive"),
    # one-way เป็นข้อบังคับของระบบ ไม่ใช่ตัวเลือก — hedge ทำให้ถือสวนกันได้
    # ซึ่งละเมิดหลัก "หนึ่งฝั่งต่อเหรียญเสมอ" (spec/03)
    CheckConstraint("position_mode = 'one_way'", name="ck_config_broker_position_mode"),
)


config_data = Table(
    "config_data",
    metadata,
    *_version_fk(),
    Column("exchange", Text, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["config_version_id", "profile"],
        [_CHILD_FK, "config_versions.profile"],
        name="fk_config_data_version",
    ),
)

#: ตาราง config ทั้งชุด เรียงตามลำดับที่ต้องเขียน (หัวก่อนลูก)
CONFIG_TABLES = (
    config_versions,
    config_settings,
    config_symbols,
    config_risk,
    config_broker,
    config_data,
)


# ── บันทึกการตัดสินใจ (ใบ 03) ────────────────────────────────────────────────
#
# หนึ่งแถวต่อหนึ่งแท่งต่อหนึ่ง symbol **เขียนทุกกรณี** รวมถึงตอนที่ผลคือ "ไม่ทำอะไร"
# (decisions #11, spec/08:67) — บันทึกที่เขียนเฉพาะตอนมีออเดอร์คือบันทึกที่เข้าข้างตัวเอง
# เห็นแต่ตอนที่ระบบทำอะไร ไม่เห็นตอนที่มันเลือกจะไม่ทำ

#: สีของ Action Zone — เจ็ดค่า (spec/02:32-38) · หัวข้อในสเปกเขียน "6 สี" ซึ่งนับเฉพาะ
#: สีจริง `BLACK` คือแท่งที่ไม่เข้าเงื่อนไขใดเลย (เช่น `FastMA == SlowMA`) ซึ่งเกิดได้
#: และต้องบันทึกได้ · ระบบเทรดจริงใช้แค่ `GREEN`/`RED` แต่ต้องคำนวณครบเพราะ golden
#: test เทียบสีทีละแท่งกับ TradingView (spec/02:40)
ZONE_T = postgresql.ENUM(
    "GREEN",
    "BLUE",
    "LBLUE",
    "RED",
    "ORANGE",
    "YELLOW",
    "BLACK",
    name="zone_t",
    create_type=False,
)

#: สถานะของสัญญาณ (spec/02:64) · `UNSET` = ยังไม่เคยเกิด `longcond`/`shortcond` เลย
#: ซึ่งไม่ใช่ค่าที่หายไป แต่เป็นสถานะจริงของช่วงต้นชุดข้อมูล (spec/02:76-82)
STATE_T = postgresql.ENUM("BULLISH", "BEARISH", "UNSET", name="state_t", create_type=False)

#: ฝั่งของ **ไม้** · `order_side_t` ด้านล่างคือฝั่งของ **ออเดอร์** — สองชุดนี้คนละเรื่อง
#: การ flip จาก long ไป short ในโหมด one-way คือ `sell` สองครั้ง (spec/06:94) ถ้าใช้ชุด
#: เดียวกันทั้งสองความหมาย บันทึกจะอ่านเหมือนเปิด short ซ้ำสองไม้
SIDE_T = postgresql.ENUM("long", "short", name="side_t", create_type=False)

#: ฝั่งของออเดอร์ที่ยิงเข้า venue
ORDER_SIDE_T = postgresql.ENUM("buy", "sell", name="order_side_t", create_type=False)

#: ขาของออเดอร์ในหนึ่งแท่ง (spec/06:124) · `stop` อยู่ในชุดด้วยเพราะ cold start ทางที่ 2
#: วาง `stop_market` ไว้ที่ venue ในแท่งเดียวกับที่เปิดไม้ (spec/06:129, decisions #17)
LEG_T = postgresql.ENUM("open", "close", "stop", name="leg_t", create_type=False)


#: หัวของบันทึกหนึ่งแท่ง
#:
#: **ไม่มี UNIQUE บน `(profile, market, symbol, timeframe, bar_close_ts)` โดยเจตนา** —
#: process ที่ตายกลางแท่งแล้วกลับมาในแท่งเดิมจะส่ง `clientOrderId` เดิมซ้ำแล้ว venue
#: ปฏิเสธ (spec/06:127) · สองแถวของกุญแจเดียวกันจึง**ไม่ใช่**ข้อเท็จจริงเดียวกัน แถวที่สอง
#: คือหลักฐานของ restart · ถ้าใส่ UNIQUE แล้ว upsert แถวแรกจะถูกทับ แล้วคนอ่านย้อนหลัง
#: จะสรุปว่า "ไม่มีออเดอร์ถูกส่ง" ซึ่งกลับหัวความจริง · คอนโซลเลือกแถวล่าสุดไปแสดง
#: และรู้ตัวว่าเลือก
#:
#: คอลัมน์เกือบทั้งหมด **nullable** เพราะแท่งที่จบด้วย `no_signal` มีแค่หัวกับ
#: `zone`/`state`/`close_px` — NOT NULL ที่เกินจริงจะทำให้แท่งที่ระบบเลือกจะไม่ทำอะไร
#: เขียนไม่ลง ซึ่งกลับหัวเจตนาทั้งใบ (spec/08:67)
decisions = Table(
    "decisions",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("profile", PROFILE_T, nullable=False),
    #: `usdtm_perp` / `spot` — ค่าต่อเหรียญ (decisions #26) · อยู่ในกุญแจธรรมชาติเพราะ
    #: เหรียญชื่อเดียวกันบนสองตลาดเป็นคนละของ
    Column("market", Text, nullable=False),
    Column("symbol", Text, nullable=False),
    Column("timeframe", Text, nullable=False),
    Column("bar_close_ts", BigInteger, nullable=False),
    #: ขอบวัน UTC ของ risk (spec/06:57) — `max_daily_loss_pct` reset เที่ยงคืน UTC
    #: ไม่ใช่เวลาท้องถิ่น · คอลัมน์นี้ทำให้ query ต่อวันเขียนได้โดยไม่ต้องเอา
    #: `datetime` เข้ามาใน `src/` (decisions #22)
    Column("utc_day", Integer, Computed("bar_close_ts / 86400000", persisted=True)),
    #: เวลาที่ตัดสิน — แยกจาก `bar_close_ts` (เวลาของเหตุการณ์) และจาก `created_ts`
    #: (เวลาที่บันทึกลงฐาน) · ตอนไล่ปัญหาต้องใช้ทั้งสาม
    Column("decided_ts", BigInteger, nullable=False),
    #: FK ไปเวอร์ชัน config ที่ใช้จริง → ไม่ต้อง snapshot `base_pct`/`bucket_quote`/
    #: `max_position_pct` ลงทุกแถว (decisions #18, #22) · `repo.config.settings_of()`
    #: คืนค่าทั้งชุดของเวอร์ชันนั้นให้
    Column("config_version_id", BigInteger, nullable=False),
    Column("close_px", PRICE, nullable=False),
    Column("zone", ZONE_T, nullable=False),
    Column("state", STATE_T, nullable=False),
    #: **ไม่มีคอลัมน์ `signal`** — spec/02:64 ให้ชื่อจริงในโค้ดเป็นบูลีนสองตัว
    #: ส่วน spec/07:142 เขียน `signal` เฉยๆ ไม่เคยระบุค่าที่มันเก็บ
    Column("long_signal", Boolean, nullable=False),
    Column("short_signal", Boolean, nullable=False),
    Column("side", SIDE_T),
    #: โหมด cold start ที่แท่งนี้เดิน (ชุดเดียวกับ `config_settings.cold_start`)
    #: NULL = ไม่ได้เข้าเส้นทาง cold start
    Column("cold_start", Text),
    Column("dry_run", Boolean, nullable=False),
    #: NULL บน spot — ตลาดนั้นไม่มีทั้งคู่ (decisions #26)
    Column("leverage", PCT),
    Column("margin_mode", MARGIN_MODE_T),
    Column("judge_called", Boolean),
    #: LLM ล้มเหลว = **ยังลงไม้ ที่ `base_pct`** (decisions #6, spec/04:87) ไม่ใช่ข้ามสัญญาณ
    #: จึงเป็นคอลัมน์ ไม่ใช่ค่าของ `skip_reason` · ถ้าไม่มีธงนี้ ตอนอ่านย้อนหลังจะแยกไม่ออก
    #: ระหว่าง "LLM บอกว่าไม่มีปัจจัย" กับ "LLM ตอบไม่ได้"
    Column("llm_fallback", Boolean),
    Column("llm_fallback_reason", Text),
    Column("prompt_hash", Text),
    Column("factors_present", Integer),
    #: `confluence` / `cold_start` / `none` — ตอบว่า "ใช้สูตรไหน" ส่วน `llm_fallback`
    #: ตอบว่า "input จริงหรือเปล่า" · ไม่มีค่า `base_only` เพราะเส้นทาง fallback รันสูตร
    #: confluence ตัวเดิมด้วย `factors_present = 0`
    Column("size_rule", Text),
    #: ก่อนเพดาน / หลังเพดาน (spec/05:16-17) · `capped` แยก "ไม้เล็กเพราะปัจจัยน้อย"
    #: ออกจาก "ไม้เล็กเพราะชนเพดาน" (spec/05:68)
    Column("size_pct_formula", PCT),
    Column("size_pct_final", PCT),
    Column("capped", Boolean),
    Column("margin", PRICE),
    Column("notional", PRICE),
    Column("qty", PRICE),
    Column("ref_px", PRICE),
    #: ตอบคำถามเดียว: "ทำไมไม่มีออเดอร์เปิด (`leg = open`) ถูกส่ง" — หนึ่งค่าต่อแถว
    #: เลือกจากประตูแรกที่ปิด · invariant ที่ผูกค่านี้กับ `decision_orders` เขียนเป็น
    #: CHECK ไม่ได้ (ข้ามตาราง) จึงอยู่ที่ `repo.decisions.validate_record()`
    Column("skip_reason", Text),
    #: NULL ทั้งชุดบน spot — ตลาดนั้น**ไม่มี** funding ซึ่งคนละความหมายกับ "ดึงไม่ได้"
    #: (decisions #26, spec/07:19)
    Column("funding_rate", FUNDING_RATE),
    Column("funding_next_ts", BigInteger),
    Column("funding_unavailable_reason", Text),
    Column("created_ts", BigInteger, nullable=False),
    # ให้ตารางลูกอ้างกลับได้แบบ composite เพื่อกันคอลัมน์ profile ของลูกเพี้ยนจากหัว
    UniqueConstraint("id", "profile", name="uq_decisions_id_profile"),
    # ไม้ของ live ชี้ config version ของ paper ไม่ได้ — ต้องเป็น composite ไม่ใช่ FK
    # คอลัมน์เดียว ไม่งั้นบันทึกจะอ้างการตั้งค่าของอีกโหมดได้โดยไม่มีอะไรขวาง
    ForeignKeyConstraint(
        ["config_version_id", "profile"],
        ["config_versions.id", "config_versions.profile"],
        name="fk_decisions_config_version",
    ),
    CheckConstraint("market IN ('usdtm_perp', 'spot')", name="ck_decisions_market"),
    # ชุดนี้ตรงกับ `TIMEFRAME_MS` ใน data/ohlcv.py และกับ `ck_config_settings_timeframe`
    CheckConstraint("timeframe IN ('1h', '1d')", name="ck_decisions_timeframe"),
    # แท่งเดียวเป็นสัญญาณสองฝั่งพร้อมกันไม่ได้ (spec/02:44-46 นิยามสัญญาณจาก state ก่อนหน้า)
    CheckConstraint(
        "NOT (long_signal AND short_signal)", name="ck_decisions_signal_exclusive"
    ),
    CheckConstraint(
        "cold_start IS NULL OR cold_start IN ('wait_1h', 'trailing', 'skip')",
        name="ck_decisions_cold_start",
    ),
    # factor มีสามตัวต่อฝั่ง (spec/04:19-24) — ค่าที่เกินสามคือบั๊กของชั้น judge
    CheckConstraint(
        "factors_present IS NULL OR factors_present BETWEEN 0 AND 3",
        name="ck_decisions_factors_present",
    ),
    CheckConstraint(
        "size_rule IS NULL OR size_rule IN ('confluence', 'cold_start', 'none')",
        name="ck_decisions_size_rule",
    ),
    # ชุดปิดที่ตกลงกันไว้ในใบ · เป็น TEXT + CHECK ไม่ใช่ ENUM เพราะชุดนี้จะโตอีก
    # (ขั้น 1 ของ spec/08 ที่ข้ามเหรียญไปเลยยังไม่มีค่าของตัวเอง — ใบ 12 ตัดสิน)
    CheckConstraint(
        "skip_reason IS NULL OR skip_reason IN ("
        "'flip_aborted', 'no_signal', 'already_positioned', 'short_disabled', "
        "'cane_rule', 'rr_too_low', 'risk_rejected', 'order_error', 'dry_run')",
        name="ck_decisions_skip_reason",
    ),
    # สามข้อล่างเขียนสิ่งที่ตลาด spot **ไม่มี** ลงไปให้ฐานปฏิเสธ แบบเดียวกับที่
    # `config_symbols` ทำ — ค่าที่ขัดข้อพวกนี้ไม่ได้ "ตั้งผิด" แต่เป็นไปไม่ได้
    CheckConstraint(
        "market <> 'spot' OR (leverage = 1 AND margin_mode IS NULL)",
        name="ck_decisions_spot_no_leverage",
    ),
    CheckConstraint(
        "market <> 'spot' OR (funding_rate IS NULL AND funding_next_ts IS NULL "
        "AND funding_unavailable_reason IS NULL)",
        name="ck_decisions_spot_no_funding",
    ),
    # spot เป็น long-only — แดงบน spot คือ "ขายออกให้แบน" ไม่ใช่การเปิดไม้ short
    # (decisions #26, spec/03:20)
    CheckConstraint(
        "market <> 'spot' OR side IS NULL OR side = 'long'",
        name="ck_decisions_spot_long_only",
    ),
    # กุญแจธรรมชาติ **ไม่ unique** ตามเหตุผลใน docstring ของตาราง
    Index(
        "ix_decisions_natural",
        "profile",
        "market",
        "symbol",
        "timeframe",
        "bar_close_ts",
    ),
)


def _decision_fk() -> tuple[Column, Column]:
    """คอลัมน์ที่ตารางลูกของ `decisions` ใช้ผูกกลับไปที่หัว

    เหตุผลเดียวกับ `_version_fk()` — `profile` ซ้ำอยู่บนลูกทุกตัวเพื่อให้กฎที่ผูกโหมด
    เขียนเป็น CHECK ได้ และความซ้ำนั้นเพี้ยนจากหัวไม่ได้เพราะ FK เป็นแบบ composite
    `(decision_id, profile)` ชี้ไปที่ `UNIQUE (id, profile)` ของหัว · ลูกที่โกหก
    `profile` ได้ทำให้คอนโซลเอาไม้ paper ไปปนกับ live โดยไม่มีอะไรขวาง

    `market` **ไม่** ซ้ำลงมาด้วย แม้จะมีกฎของ spot ที่อยากเขียนเป็น CHECK บนลูก
    (`reduce_only` เป็นของ perp) — การพา `market` ลงมาบังคับให้ FK ขยายเป็น
    `(decision_id, profile, market)` ทั้งหกตาราง ซึ่งไม่คุ้มกับกฎข้อเดียว
    กฎนั้นจึงอยู่ที่ `validate_record()` แทน
    """
    return (
        Column("decision_id", BigInteger, primary_key=True),
        Column("profile", PROFILE_T, nullable=False),
    )


_DECISION_FK = "decisions.id"


#: คำตัดสินของ Judge ต่อ factor — เขียนลงทุกตัว **ไม่ว่าผลจะเป็นอะไร** (spec/04:77)
#: เป็นแถวจริงไม่ใช่ JSON ก้อน เพราะคอนโซลต้องกรองตาม factor และนับ compliance ต่อแท่ง
decision_verdicts = Table(
    "decision_verdicts",
    metadata,
    *_decision_fk(),
    Column("factor", Text, primary_key=True),
    #: ตัดสินเฉพาะ factor ของฝั่งที่กำลังจะเข้า ไม่มีการหักลบข้ามฝั่ง (spec/04:26)
    Column("side", SIDE_T, nullable=False),
    Column("present", Boolean, nullable=False),
    #: ใช้สำหรับให้คนอ่านย้อนหลังเท่านั้น **ห้ามผูกกับขนาดไม้** (decisions #12, spec/05:52)
    Column("confidence", PCT),
    Column("evidence_bars", postgresql.ARRAY(BigInteger)),
    Column("rationale", Text),
    #: มาจาก cache หรือเรียกจริง (spec/07:145) — ต่างกันตอนไล่ค่าใช้จ่ายและตอนไล่บั๊ก
    Column("cached", Boolean, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "profile"],
        [_DECISION_FK, "decisions.profile"],
        name="fk_decision_verdicts_decision",
    ),
    CheckConstraint(
        "factor IN ('CHANNEL_BREAKOUT', 'RETAIL_CAPITULATION', 'HIGHER_LOW', "
        "'CHANNEL_BREAKDOWN', 'BUYING_EXHAUSTION', 'LOWER_HIGH')",
        name="ck_decision_verdicts_factor",
    ),
)


#: ผลการตรวจ risk **ทีละชั้น** (spec/07:149) เรียงตาม `seq`
#:
#: spec/08:39 ตรวจเรียง `kill_switch` → `daily_loss` → `liq_buffer` และชั้นแรกที่ไม่ผ่าน
#: ปฏิเสธทั้งไม้ → **ชั้นที่ไม่มีในตารางคือหลักฐานว่าลำดับถูกเคารพ** ไม่ใช่ข้อมูลที่หายไป
#: ไม้บน spot มีสองแถว เพราะไม่มี liquidation จึง**ไม่เรียก** ชั้น `liq_buffer` เลย
#: ไม่ใช่เรียกแล้วผ่านเสมอ (decisions #26, spec/06:50)
#:
#: invariant "มีได้ไม่เกินหนึ่งแถวที่ `passed = false` และต้องเป็น `seq` สูงสุด" กับ
#: "`seq` เรียง 1..n ไม่มีช่อง" เป็นกฎข้ามแถว CHECK เขียนไม่ได้ → `validate_record()`
decision_risk_checks = Table(
    "decision_risk_checks",
    metadata,
    *_decision_fk(),
    Column("seq", Integer, primary_key=True),
    Column("layer", Text, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("value", PRICE),
    Column("limit_value", PRICE),
    Column("detail", Text),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "profile"],
        [_DECISION_FK, "decisions.profile"],
        name="fk_decision_risk_checks_decision",
    ),
    CheckConstraint(
        "layer IN ('kill_switch', 'daily_loss', 'liq_buffer')",
        name="ck_decision_risk_checks_layer",
    ),
    CheckConstraint("seq > 0", name="ck_decision_risk_checks_seq"),
)


#: ออเดอร์ที่พยายามส่งในแท่งนี้ — **หลายแถวต่อหนึ่ง decision**
#:
#: flip ยิงสองขาในแท่งเดียว (ปิดแล้วเปิด, spec/06:129) และ cold start ทางที่ 2 เพิ่มขา
#: `stop` เข้ามาอีก · คีย์เป็น surrogate เพราะขาเดียวกันซ้ำได้ตอน retry จึงไม่มีชุด
#: คอลัมน์ธรรมชาติที่เป็นกุญแจได้
decision_orders = Table(
    "decision_orders",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("decision_id", BigInteger, nullable=False),
    Column("profile", PROFILE_T, nullable=False),
    Column("leg", LEG_T, nullable=False),
    #: ฝั่งของออเดอร์ ไม่ใช่ฝั่งของไม้ — flip long→short ในโหมด one-way คือ `sell`
    #: สองครั้ง (spec/06:94)
    Column("order_side", ORDER_SIDE_T, nullable=False),
    Column("order_type", Text, nullable=False),
    #: ของ perp เท่านั้น (decisions #26) — บังคับที่ `validate_record()` ไม่ใช่ CHECK
    #: เพราะ CHECK ต้องเห็น `market` ในแถวเดียวกัน ซึ่งพา composite FK ไปทั้งหกตาราง
    Column("reduce_only", Boolean, nullable=False),
    Column("qty", PRICE, nullable=False),
    Column("stop_px", PRICE),
    #: กำหนดจาก (symbol, แท่ง, ขา) แบบ deterministic ก่อนพยายามส่ง (spec/06:127) จึงมี
    #: ค่าอยู่แม้แถวที่พังก่อนส่ง — เป็นตัวที่ทำให้ restart ในแท่งเดิมถูก venue ปฏิเสธ
    Column("client_order_id", Text, nullable=False),
    Column("sent", Boolean, nullable=False),
    Column("accepted", Boolean, nullable=False),
    Column("venue_order_id", Text),
    Column("error", Text),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "profile"],
        [_DECISION_FK, "decisions.profile"],
        name="fk_decision_orders_decision",
    ),
    CheckConstraint(
        "order_type IN ('market', 'stop_market')", name="ck_decision_orders_order_type"
    ),
    # ออเดอร์ที่ส่งแล้วไม่ถูกรับ ต้องบอกได้ว่าเพราะอะไร — ถ้าไม่บังคับ แถวที่ venue
    # ปฏิเสธจะแยกไม่ออกจากแถวที่โค้ดลืมเซ็ต `accepted` และ `skip_reason = order_error`
    # จะไม่มีหลักฐานรองรับ
    CheckConstraint(
        "NOT (sent AND NOT accepted) OR error IS NOT NULL",
        name="ck_decision_orders_rejected_needs_error",
    ),
    Index("ix_decision_orders_decision", "decision_id"),
)


#: ผลของ flip สองขาในแท่งนี้ (spec/07:143) — หนึ่งแถวต่อ decision
#:
#: `residual_side` ไม่ได้อยู่ในบล็อก `flip{}` ของ spec/07:143 แต่ spec/03:79 สั่งให้บันทึก
#: **ฝั่งของ residual** ด้วย · ของค้างที่ไม่รู้ฝั่งคือของค้างที่คนปิดด้วยมือไม่ได้
#: **ตารางนี้ไม่มีแถวของไม้ spot** — ไม่มี flip บน spot (decisions #26, spec/03:20)
decision_flip = Table(
    "decision_flip",
    metadata,
    *_decision_fk(),
    Column("close_qty_intended", PRICE, nullable=False),
    Column("close_qty_filled", PRICE, nullable=False),
    Column("residual_qty", PRICE, nullable=False),
    Column("residual_side", SIDE_T),
    Column("aborted", Boolean, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "profile"],
        [_DECISION_FK, "decisions.profile"],
        name="fk_decision_flip_decision",
    ),
    # ฝั่งเว้นได้เฉพาะตอนไม่มีของค้าง — spec/03:79 บังคับให้ของค้างมีฝั่งกำกับ
    CheckConstraint(
        "residual_qty = 0 OR residual_side IS NOT NULL",
        name="ck_decision_flip_residual_needs_side",
    ),
)


#: stop order ของแท่งนี้ (spec/07:148) — cold start ทางที่ 2 และการขยับ Slow Trail
#:
#: `missing` คือการทำตาม spec/08:80 ที่ห้ามวาง stop ใหม่เงียบๆ ทับตอนหา stop เดิมไม่เจอ
#: ต้องเทียบกับ `positions()` แล้วบันทึกให้ชัดว่ามันหายไป
decision_stop = Table(
    "decision_stop",
    metadata,
    *_decision_fk(),
    Column("action", Text, nullable=False),
    Column("px", PRICE),
    Column("stop_order_id", Text),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "profile"],
        [_DECISION_FK, "decisions.profile"],
        name="fk_decision_stop_decision",
    ),
    CheckConstraint(
        "action IN ('placed', 'replaced', 'unchanged', 'missing')",
        name="ck_decision_stop_action",
    ),
)


#: สถานะที่เปิดค้างที่ปลายทางแต่ระบบไม่ได้ตั้งใจถือ — **เขียนซ้ำทุกแท่งจนกว่าคนจะปิด**
#: (decisions #19) ไม่ใช่ event ครั้งเดียวตอนเกิด
#:
#: เพราะกฎนั้น "ค้างมากี่แท่งแล้ว" จึงเป็น `count(*)` แทนการไล่อ่านย้อนหาแท่งที่เกิดเหตุ
#: คีย์เป็น `(decision_id, side)` เพราะหนึ่งแท่งมีของค้างได้ฝั่งละไม่เกินหนึ่ง — surrogate
#: key จะปล่อยให้แท่งเดียวมีสองแถวฝั่งเดียวกัน ซึ่งทำให้ `count(*)` เพี้ยนเงียบๆ
decision_unmanaged = Table(
    "decision_unmanaged",
    metadata,
    *_decision_fk(),
    Column("side", SIDE_T, primary_key=True),
    Column("qty", PRICE, nullable=False),
    Column("source", Text, nullable=False),
    #: แท่งที่เห็นของค้างนี้ครั้งแรก — ยกมาซ้ำทุกแท่งที่เขียนต่อจากนั้น
    Column("first_seen_bar_close_ts", BigInteger, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    ForeignKeyConstraint(
        ["decision_id", "profile"],
        [_DECISION_FK, "decisions.profile"],
        name="fk_decision_unmanaged_decision",
    ),
)

#: ตารางบันทึกทั้งชุด เรียงตามลำดับที่ต้องเขียน (หัวก่อนลูก)
DECISION_TABLES = (
    decisions,
    decision_verdicts,
    decision_risk_checks,
    decision_orders,
    decision_flip,
    decision_stop,
    decision_unmanaged,
)
