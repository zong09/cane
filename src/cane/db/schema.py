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
