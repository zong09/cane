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
- enum ที่ปิดจริงเป็น Postgres ENUM · ชุดที่จะโตอีก (`skip_reason`, `exit_reason`)
  เป็น `TEXT` + `CHECK` เพราะเพิ่มค่าใหม่ทำได้ใน migration ธรรมดา ไม่ต้อง `ALTER TYPE`
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Identity,
    Index,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects import postgresql

metadata = MetaData()

#: `live` / `paper` — ชุดปิดจริง ไม่มีโหมดที่สาม (spec/07)
#: ประกาศไว้ที่นี่ให้ทุกโดเมนที่ผูกโหมดใช้ตัวเดียวกัน · `create_type=False` เพราะ
#: ตัว type ถูกสร้างใน migration แรกครั้งเดียว ไม่ใช่ตอนสร้างตารางที่อ้างถึงมัน
PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)

PRICE = Numeric(24, 8)
FUNDING_RATE = Numeric(12, 10)


#: แท่งราคาที่ปิดแล้ว — **ไม่ผูก profile**
#:
#: paper กับ live อ่าน public feed เดียวกัน ถ้าแยกตารางต่อโหมดจะเทียบผลสองโหมด
#: บนข้อมูลชุดเดียวกันไม่ได้ ซึ่งเป็นเหตุผลหลักที่มี paper อยู่ · แท่งที่ปิดแล้ว
#: ไม่เปลี่ยนอีก (spec/07) การแชร์ตารางจึงไม่มีทางให้โหมดหนึ่งไปทับข้อมูลของอีกโหมด
#: — ส่วน cursor ของ replay ที่ต้องแยกต่อ profile เป็นเรื่องของ #12 ไม่ใช่ของตารางนี้
bars = Table(
    "bars",
    metadata,
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
)


#: การสังเกต funding rate หนึ่งครั้ง — บันทึกทั้งตอนได้ค่าและตอนดึงไม่ได้
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
