"""รากฐาน: profile_t, bars, funding_observations, role append-only

Revision ID: 0001
Revises:
Create Date: 2026-09-02

**role ถูกสร้างใน migration ไม่ใช่ใน initdb script** — `CREATE ROLE` ไม่มี
`IF NOT EXISTS` และ role เป็นของระดับ cluster (ไม่ใช่ของฐานใดฐานหนึ่ง) ถ้าปล่อยให้
role เกิดจาก initdb อย่างเดียว การรัน `alembic upgrade head` บนฐานเปล่าที่ cluster
มี role อยู่แล้ว (หรือยังไม่มี) จะล้มที่ขั้น GRANT แบบสลับกันไปตามเครื่อง · `DO` block
ที่ guard ด้วย `pg_roles` ทำให้ idempotent จริงทั้งสองทาง

**GRANT อยู่ใน migration ที่เพิ่มตารางนั้นเสมอ** ไม่รวมไว้ที่เดียว — ตารางใหม่ที่ลืม
ให้สิทธิ์จะพังตอนเขียนครั้งแรก ซึ่งดีกว่าตารางที่ได้สิทธิ์เกินมาโดยไม่มีใครสังเกต
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: ตารางข้อเท็จจริง — `cane_engine` เขียนได้แต่ **ไม่มี UPDATE/DELETE เลย**
#: append-only จึงเป็นสิ่งที่ DB ปฏิเสธให้ ไม่ใช่ข้อตกลงที่โค้ดผิดพลาดทับได้
#: (ตอนเป็นไฟล์ JSONL มันเป็นแค่ข้อตกลง) · `cane_console` อ่านได้เท่านั้น
FACT_TABLES = ("bars", "funding_observations")


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cane_engine') THEN
                CREATE ROLE cane_engine NOLOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cane_console') THEN
                CREATE ROLE cane_console NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO cane_engine, cane_console")

    # `live` / `paper` — ตารางในไฟล์นี้ยังไม่มีตัวไหนผูกโหมด (แท่งราคาใช้ feed
    # เดียวกันทั้งสองโหมด) แต่สร้าง type ไว้ที่ migration แรกครั้งเดียว เพื่อไม่ให้
    # migration ของแต่ละโดเมนต้องมาเดาว่าตัวเองเป็นคนแรกที่ต้องสร้างมันหรือไม่
    op.execute("CREATE TYPE profile_t AS ENUM ('live', 'paper')")

    op.create_table(
        "bars",
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("open_ts", sa.BigInteger(), nullable=False),
        sa.Column("close_ts", sa.BigInteger(), nullable=False),
        sa.Column("open", sa.Numeric(24, 8), nullable=False),
        sa.Column("high", sa.Numeric(24, 8), nullable=False),
        sa.Column("low", sa.Numeric(24, 8), nullable=False),
        sa.Column("close", sa.Numeric(24, 8), nullable=False),
        sa.Column("volume", sa.Numeric(24, 8), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("symbol", "timeframe", "open_ts"),
        sa.CheckConstraint("close_ts > open_ts", name="ck_bars_close_after_open"),
        sa.CheckConstraint("high >= low", name="ck_bars_high_ge_low"),
        sa.CheckConstraint("high >= open AND high >= close", name="ck_bars_high_is_max"),
        sa.CheckConstraint("low <= open AND low <= close", name="ck_bars_low_is_min"),
        sa.CheckConstraint("volume >= 0", name="ck_bars_volume_nonneg"),
    )

    op.create_table(
        "funding_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("observed_ts", sa.BigInteger(), nullable=False),
        sa.Column("rate", sa.Numeric(12, 10), nullable=True),
        sa.Column("next_funding_ts", sa.BigInteger(), nullable=True),
        sa.Column("unavailable_reason", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # "ดึงไม่ได้ = บันทึกว่าไม่มีข้อมูล ห้ามเดาเป็น 0" ของ data/funding.py
        # กลายเป็นข้อบังคับของ schema ที่นี่ — เขียน rate=0 พร้อมเหตุผลไม่ได้อีก
        sa.CheckConstraint(
            "(rate IS NOT NULL) <> (unavailable_reason IS NOT NULL)",
            name="ck_funding_observations_rate_xor_reason",
        ),
    )
    op.create_index(
        "ix_funding_observations_symbol_ts",
        "funding_observations",
        ["symbol", "observed_ts"],
    )

    for table in FACT_TABLES:
        # คอลัมน์ id เป็น IDENTITY ซึ่ง sequence เป็นของตาราง — สิทธิ์ INSERT
        # บนตารางพอแล้ว ไม่ต้อง GRANT USAGE บน sequence แยก
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO cane_engine")
        op.execute(f"GRANT SELECT ON TABLE {table} TO cane_console")


def downgrade() -> None:
    op.drop_index("ix_funding_observations_symbol_ts", table_name="funding_observations")
    op.drop_table("funding_observations")
    op.drop_table("bars")
    op.execute("DROP TYPE profile_t")
    # **ตั้งใจไม่ DROP ROLE** — role เป็นของระดับ cluster ฐานอื่นใน cluster เดียวกัน
    # อาจใช้ชื่อเดียวกันอยู่ การลบทิ้งตอน downgrade ฐานเดียวจะดึงสิทธิ์ของฐานอื่นไปด้วย
    # การลบ role เป็นการตัดสินใจของ ops ไม่ใช่ผลข้างเคียงของการถอย migration
    # (GRANT บนตารางหายไปเองพร้อม DROP TABLE อยู่แล้ว)
