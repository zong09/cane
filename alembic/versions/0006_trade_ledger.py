"""trade ledger — fill จริงและ funding ที่ถูกหักไปแล้ว (spec/07:186)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-09

รายงาน "ไม้ที่ปิดแล้ว" derive จาก `decisions` อย่างเดียวไม่ได้ — บันทึกการตัดสินใจเก็บว่า
ระบบ*ตั้งใจ*ทำอะไร ส่วนราคาที่ได้จริง ค่าธรรมเนียมจริง และ funding ที่ถูกหักไปแล้วเป็นของที่
เกิดที่ปลายทาง ตารางสองตัวนี้คือที่เก็บของฝั่งนั้น

**หัวใจอยู่ที่ UNIQUE สองตัว ไม่ใช่ที่คอลัมน์** — reconcile ของ spec/08 ขั้น 3 อ่านสถานะจริง
ทุกแท่ง แล้วเห็น fill เดิมซ้ำเมื่อ process กลับมาในแท่งเดิม ตอนที่ ledger เป็นไฟล์ ต้องเขียน
โค้ด dedupe เอง และถ้าพลาดคือคิดค่าธรรมเนียมซ้ำเงียบๆ ตอนนี้ฐานปฏิเสธให้ตั้งแต่ `INSERT`

## `stop_events` ที่ใบสั่งไว้ **ไม่ได้สร้าง** — มันซ้ำกับของที่ใบ 03 ทำไปแล้ว

`decision_stop` (migration 0004) เก็บ `action IN ('placed','replaced','unchanged','missing')`
พร้อม `px` และ `stop_order_id` ต่อแท่งอยู่แล้ว ซึ่งคือสิ่งเดียวกับที่ `stop_events` จะเก็บ ·
คอมเมนต์ในใบเขียนไว้ตอนที่ ledger ยังอยู่ในใบ 03 และยังไม่รู้ว่าใบ 03 จะสร้างตารางนี้เอง

สองคอลัมน์ที่รายการในใบมีเกินมาไม่ต้องเก็บ: `prev_stop_px` **derive ได้** จากแถวของแท่งก่อน
หน้า ซึ่ง ADR 24 บอกตรงๆ ว่าให้เป็น VIEW ไม่ใช่คอลัมน์ · ส่วน `reason` ของการขยับ stop มี
ค่าเดียวเสมอคือ "Slow Trail ขยับ" (spec/08) การเก็บช่องว่างให้กรอกจะเชิญให้มีค่าที่สอง

**stop ที่ทำงานจริงไม่ใช่ event มันคือ fill** — แถวใน `fills` ที่ `leg = 'stop'` พร้อม
`exit_reason = 'stop'` ไม่ใช่แถวในตารางที่สาม การมีสองที่ให้บันทึกเรื่องเดียวกันคือแหล่ง
ความจริงที่ซ้อนกัน ซึ่งเป็นสิ่งที่ ADR 24 ตั้งใจกัน

## `exit_reason` มีสี่ค่า และมาจากสเปกทั้งสี่

- `signal` — สัญญาณฝั่งตรงข้าม ทางออกปกติทางเดียวของระบบ (spec/03 "ออกจากไม้")
  **ขา 1 ของ flip ใช้ค่านี้** ไม่มีค่าแยก — flip คือการออกด้วยสัญญาณฝั่งตรงข้ามแล้วเข้า
  ฝั่งใหม่ต่อในแท่งเดียว เหตุของการออกจึงเป็นเหตุเดียวกันเป๊ะ การแยกค่าจะทำให้รายงาน
  "ออกเพราะสัญญาณ" นับขาดไปทุกครั้งที่มีการกลับข้าง
- `stop` — stop order ที่วางไว้ที่ exchange ทำงาน (ADR 17, spec/03:156)
- `liquidation` — exchange ปิดให้เองที่ราคา liquidation **แม้ยังไม่มีสัญญาณฝั่งตรงข้าม**
  (spec/06:63) เป็นทางออกที่ระบบไม่ได้สั่ง จึงต้องแยกออกจาก `stop` ให้เห็น
- `manual` — คนกด "ปิดไม้ฉุกเฉิน" (spec/06:19) ซึ่งเป็นทางออกทางเดียวของของค้างจาก
  `flip_aborted` ด้วย (spec/03:83)

บังคับด้วย CHECK คู่กับ `leg` — ขาเปิดห้ามมีเหตุผลของการออก ขาปิดและขา stop ห้ามไม่มี
ท่าเดียวกับ `ck_decision_orders_order_type` ของใบ 03 คือ `Text` + CHECK ไม่ใช่ ENUM ใหม่

## `dedupe_key` นิยามที่นี่ ไม่ปล่อยให้ผู้เรียกคิดเอง

UNIQUE ดีได้เท่ากับนิยามของคีย์เท่านั้น ถ้าแต่ละที่คิดคนละแบบ ตารางจะรับของซ้ำโดยที่
constraint ยังเขียว:

- มี `venue_fill_id` → ใช้ค่านั้นตรงๆ เป็นคีย์ของ fill ที่ venue ออกให้เอง
- ไม่มี (PaperBroker และ venue ที่ไม่คืน id) → `{client_order_id}#{n}` โดย `n` คือลำดับ
  ของ fill ภายในออเดอร์ใบเดียวกัน เริ่มที่ 0 · `client_order_id` เป็น deterministic
  อยู่แล้ว (spec/06) คีย์ที่ได้จึงซ้ำได้เมื่อ process กลับมาในแท่งเดิม ซึ่งคือเจตนา

## `market` เก็บต่อแถว และ `symbol` ด้วย

`market` มาจากคอมเมนต์ในใบเอง — net% ของ perp กับ spot เทียบกันไม่ได้ถ้าไม่แยก (ค่าธรรมเนียม
คนละแบบ ฝั่งหนึ่งมี funding อีกฝั่งไม่มี) · **`symbol` ไม่มีในรายการของใบ ซึ่งเป็นข้อบกพร่อง
ของรายการ ไม่ใช่การออกแบบ** — fill ที่ไม่รู้ว่าเป็นของเหรียญไหน query ไม่ได้เลย

`funding_charges` บังคับ `market = 'usdtm_perp'` เพราะ **spot ไม่มี funding อยู่จริง**
ไม่ใช่มีแล้วเป็นศูนย์ (spec/03:22, ADR 26) — ให้ฐานปฏิเสธ แทนที่จะให้โค้ดชั้นบนคอยจำ

**ตารางทั้งสองเป็นตารางข้อเท็จจริง** (ADR 23) → `cane_engine` ได้ `SELECT, INSERT`
`cane_console` ได้ `SELECT` อย่างเดียว ไม่มี `UPDATE`/`DELETE` ให้ใคร

**ไม่มี ENUM ใหม่** — `profile_t` และ `leg_t` มีอยู่แล้วจาก 0001/0004 ชื่อตารางและค่าคงที่
เขียนไว้ในไฟล์นี้เอง ไม่ import จาก `schema.py` เพราะ migration เป็น snapshot ของวันที่เขียน
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEDGER_TABLES = ("fills", "funding_charges")

MARKETS = "'usdtm_perp', 'spot'"
ORDER_TYPES = "'market', 'stop_market'"
EXIT_REASONS = "'signal', 'stop', 'liquidation', 'manual'"

PRICE = sa.Numeric(precision=24, scale=8)
PCT = sa.Numeric(precision=9, scale=4)
RATE = sa.Numeric(precision=12, scale=10)

_PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)
_LEG_T = postgresql.ENUM("open", "close", "stop", name="leg_t", create_type=False)


def upgrade() -> None:
    op.create_table(
        "fills",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("trade_id", sa.Text(), nullable=False),
        sa.Column("leg", _LEG_T, nullable=False),
        sa.Column("fill_ts", sa.BigInteger(), nullable=False),
        sa.Column("px", PRICE, nullable=False),
        sa.Column("qty", PRICE, nullable=False),
        # ราคาที่ชั้นตัดสินใจเห็นตอนสั่ง — slippage คือ px เทียบกับค่านี้ จึงเก็บตัวตั้ง
        # ไม่ใช่เก็บผลต่างที่ derive ได้ (ADR 24)
        sa.Column("ref_px", PRICE, nullable=True),
        sa.Column("fee_quote", PRICE, nullable=True),
        sa.Column("fee_ccy", sa.Text(), nullable=True),
        sa.Column("fee_unavailable_reason", sa.Text(), nullable=True),
        sa.Column("venue_fill_id", sa.Text(), nullable=True),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        # ขนาดที่เหลือ *หลัง* fill ใบนี้ — ทำให้ไล่ลำดับได้โดยไม่ต้องรวมทุกแถวก่อนหน้า
        # และเป็นตัวที่บอกว่าขาปิดปิดครบหรือเหลือของค้าง (spec/03 flip_aborted)
        sa.Column("position_qty_after", PRICE, nullable=False),
        sa.Column("leverage", PCT, nullable=True),
        sa.Column("exit_reason", sa.Text(), nullable=True),
        sa.Column("exit_detail", sa.Text(), nullable=True),
        sa.Column("bar_close_ts", sa.BigInteger(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # หัวใจของไฟล์นี้ — process ที่กลับมาในแท่งเดิมเขียนซ้ำไม่ได้
        sa.UniqueConstraint("profile", "dedupe_key", name="uq_fills_dedupe"),
        sa.CheckConstraint(f"market IN ({MARKETS})", name="ck_fills_market"),
        sa.CheckConstraint(f"order_type IN ({ORDER_TYPES})", name="ck_fills_order_type"),
        sa.CheckConstraint("px > 0", name="ck_fills_px_positive"),
        sa.CheckConstraint("qty > 0", name="ck_fills_qty_positive"),
        sa.CheckConstraint(
            "position_qty_after >= 0", name="ck_fills_position_qty_after_not_negative"
        ),
        # "ยังไม่รู้ค่าธรรมเนียม" ต่างจาก "ไม่มีค่าธรรมเนียม" — สองคอลัมน์นี้จึงห้าม
        # มีพร้อมกัน และศูนย์ไม่ใช่คำตอบของกรณีแรก
        sa.CheckConstraint(
            "fee_quote IS NULL OR fee_unavailable_reason IS NULL", name="ck_fills_fee_xor"
        ),
        # ยอดค่าธรรมเนียมที่ไม่รู้สกุลคือยอดที่บวกเข้ารายงานไม่ได้
        sa.CheckConstraint(
            "fee_quote IS NULL OR fee_ccy IS NOT NULL", name="ck_fills_fee_needs_ccy"
        ),
        sa.CheckConstraint(
            f"exit_reason IS NULL OR exit_reason IN ({EXIT_REASONS})",
            name="ck_fills_exit_reason",
        ),
        # ขาเปิดไม่มีเหตุผลของการออก · ขาปิดกับขา stop ต้องมี ไม่งั้นรายงาน
        # "ออกเพราะอะไร" จะมีแถวที่ตอบไม่ได้ปนอยู่โดยไม่มีใครเห็น
        sa.CheckConstraint(
            "(leg = 'open') = (exit_reason IS NULL)", name="ck_fills_exit_reason_by_leg"
        ),
        # spot ไม่มีธง reduce_only อยู่จริง (spec/03:22) — ให้ฐานปฏิเสธ ไม่ใช่ให้โค้ดจำ
        sa.CheckConstraint(
            "market <> 'spot' OR NOT reduce_only", name="ck_fills_spot_no_reduce_only"
        ),
        # spot ถูกบังคับ leverage = 1 ตั้งแต่ config (ADR 26) ค่าอื่นบน spot คือบั๊ก
        sa.CheckConstraint(
            "market <> 'spot' OR leverage IS NULL OR leverage = 1",
            name="ck_fills_spot_no_leverage",
        ),
    )
    # รายงานอ่านตามไม้ และ reconcile อ่านตามเหรียญของแท่งที่กำลังทำ
    op.create_index("ix_fills_trade", "fills", ["profile", "trade_id"])
    op.create_index(
        "ix_fills_symbol_bar", "fills", ["profile", "market", "symbol", "bar_close_ts"]
    )

    op.create_table(
        "funding_charges",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("trade_id", sa.Text(), nullable=False),
        sa.Column("cycle_ts", sa.BigInteger(), nullable=False),
        sa.Column("rate", RATE, nullable=True),
        sa.Column("amount_quote", PRICE, nullable=True),
        sa.Column("position_qty", PRICE, nullable=False),
        sa.Column("mark_px", PRICE, nullable=True),
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # หนึ่งรอบ funding ต่อหนึ่งไม้ หักได้ครั้งเดียว
        sa.UniqueConstraint(
            "profile", "trade_id", "cycle_ts", name="uq_funding_charges_cycle"
        ),
        # spot ไม่มี funding อยู่จริง ไม่ใช่มีแล้วเป็นศูนย์ (spec/03:22, ADR 26)
        sa.CheckConstraint("market = 'usdtm_perp'", name="ck_funding_charges_market"),
        sa.CheckConstraint("position_qty > 0", name="ck_funding_charges_qty_positive"),
        # แถวนี้มีอยู่เพราะรอบ funding มาถึงตอนที่ยังถือไม้อยู่ — มันจึงต้องตอบให้ได้
        # ว่าหักไปเท่าไร หรือทำไมถึงไม่รู้ · ไม่มีทางที่แถวจะเงียบทั้งสองทาง
        #
        # ต่างจาก `decisions.funding_rate` ที่ใบ 03 **ตั้งใจไม่ใส่ XOR** ไว้ เพราะแท่ง
        # ที่จบด้วย no_signal อาจไม่เคยดึง funding เลยอย่างถูกต้อง — ที่นั่นแถวมีอยู่
        # เพราะแท่งปิด ที่นี่แถวมีอยู่เพราะมีการหักเงิน
        sa.CheckConstraint(
            "(rate IS NOT NULL AND amount_quote IS NOT NULL AND unavailable_reason IS NULL)"
            " OR (rate IS NULL AND amount_quote IS NULL AND unavailable_reason IS NOT NULL)",
            name="ck_funding_charges_known_or_explained",
        ),
    )
    op.create_index("ix_funding_charges_trade", "funding_charges", ["profile", "trade_id"])

    for table in LEDGER_TABLES:
        # ตารางข้อเท็จจริง — เขียนได้ครั้งเดียว แก้ไม่ได้ ลบไม่ได้ (ADR 23)
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO cane_engine")
        op.execute(f"GRANT SELECT ON TABLE {table} TO cane_console")


def downgrade() -> None:
    # ลบแต่ตารางที่ไฟล์นี้สร้าง ไม่แตะ ENUM ที่ 0001/0004 เป็นเจ้าของ
    # GRANT หายไปเองพร้อม DROP TABLE
    op.drop_index("ix_funding_charges_trade", table_name="funding_charges")
    op.drop_table("funding_charges")
    op.drop_index("ix_fills_symbol_bar", table_name="fills")
    op.drop_index("ix_fills_trade", table_name="fills")
    op.drop_table("fills")
