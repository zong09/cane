"""verdict_cache — คำตัดสินหนึ่งครั้งต่อแท่ง เรียกซ้ำได้คำตอบเดิมเสมอ (spec/04:69-77)

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-14

สเปกยืนอยู่บนหลัก "ระบบไม่เคยหลอกเรา" และบอกว่าระบบที่ตอบคนละอย่างสองครั้งบนข้อมูล
ชุดเดิมถือว่าหลอก · ตารางนี้คือกลไกที่ทำให้ข้อนั้นเป็นจริง ไม่ใช่แค่ประกาศไว้

## ข้อ 1 ของสเปกไม่ได้รับประกัน determinism — ข้อ 2 (ตารางนี้) จึงต้องแบกทั้งหมด

spec/04:73 สั่งให้ตั้งค่าการสุ่มต่ำสุดเท่าที่ API รองรับ **ซึ่งไม่ใช่หลักประกัน** ·
ฝั่ง Anthropic ส่ง `temperature` ไม่ได้แล้วตั้งแต่ Opus 4.7 / Sonnet 5 (400) ฝั่ง
OpenAI-compatible ส่ง `0` ได้แต่ endpoint ที่ทำ batching รวมผลบวกทศนิยมคนละลำดับตาม
องค์ประกอบของ batch พอ logit ขยับ greedy ก็เลือกคนละ token · ตัวที่รับประกันจริงคือ
"ตัดสินครั้งเดียวแล้วอ่านของเดิม" ซึ่งคือตารางนี้

*(ตอนเขียน migration นี้ สเปกข้อ 1 ยังสั่ง `temperature 0` ตรงๆ · แก้แล้ว)*

## คีย์มี `market` — spec/04:74 เขียนตรงกันแล้ว

ตอนสร้างตารางนี้สเปกยังเขียนคีย์เป็นหกช่องโดยไม่มี `market` เพราะเขียนไว้**ก่อน**
ADR 26 · `BTC/USDT` บน `spot` กับบน `usdtm_perp` เป็นคนละแท่งจริงๆ (ตาราง `bars` มี
`market` ใน PK) คำตัดสินที่คำนวณจากแท่งคนละชุดจึงทับกันไม่ได้ · ถ้าไม่มี `market`
ในคีย์ ไม้ spot จะได้คำตัดสินของ perp มาใช้เงียบๆ ซึ่งเป็นการหลอกแบบเดียวกับที่
สเปกตั้งใจกัน

## ไม่มี `profile` ในคีย์ และนั่นคือเจตนา

ตารางอื่นเกือบทั้งหมด scope ด้วย `profile` แต่ที่นี่ไม่ — เพราะคำตัดสินเป็นข้อเท็จจริงเกี่ยว
กับ**ข้อมูลตลาด** ไม่ใช่เกี่ยวกับ profile · `paper` กับ `live` ที่มองแท่ง BTC/USDT รายวัน
แท่งเดียวกันด้วย prompt ชุดเดียวกัน ต้องได้คำตอบเดียวกัน มิฉะนั้น ADR 9 ("paper กับ live
ต่างที่ค่า ไม่ต่างที่ตรรกะ") ไม่จริง · ผลพลอยได้: replay ของใบ 12 อุ่น cache ให้ live ไปด้วย
ท่าเดียวกับ `bars` ที่ไม่มี `profile` ใน PK เหมือนกัน

## `prompt_hash` รวม model id ไว้ด้วย (ดู `confluence/judge.py`)

สเปกบอกให้ `prompt_hash` อยู่ในคีย์เพื่อให้การแก้ prompt ทำให้ cache เก่าใช้ไม่ได้
โดยอัตโนมัติ · การเปลี่ยนโมเดลมีผลต่อคำตอบไม่น้อยกว่าการแก้ถ้อยคำ ถ้าไม่รวมไว้
การสลับโมเดลจะอ่านคำตัดสินของโมเดลเก่ามาใช้ต่อเงียบๆ ซึ่งเป็นการหลอกข้อเดียวกัน

## append-only เหมือนตารางข้อเท็จจริงอื่น (ADR 23)

engine เขียนได้ครั้งเดียว แก้ไม่ได้ ลบไม่ได้ · cache ตัวนี้ไม่ต้องมี invalidation เพราะ
ทุกอย่างที่ทำให้คำตอบเปลี่ยนได้อยู่ในคีย์อยู่แล้ว — แถวเก่าไม่ผิด มันแค่ไม่ถูกถามอีก
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKETS = "'usdtm_perp', 'spot'"
LONG_FACTORS = "'CHANNEL_BREAKOUT', 'RETAIL_CAPITULATION', 'HIGHER_LOW'"
SHORT_FACTORS = "'CHANNEL_BREAKDOWN', 'BUYING_EXHAUSTION', 'LOWER_HIGH'"

PCT = sa.Numeric(precision=9, scale=4)

_SIDE_T = postgresql.ENUM("long", "short", name="side_t", create_type=False)


def upgrade() -> None:
    op.create_table(
        "verdict_cache",
        # ── คีย์: หกช่องของสเปก บวก market ที่สเปกเขียนไว้ก่อน ADR 26 ────────────
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("bar_close_ts", sa.BigInteger(), nullable=False),
        sa.Column("side", _SIDE_T, nullable=False),
        sa.Column("factor", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        # ── ตัวคำตัดสิน ────────────────────────────────────────────────────────
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("confidence", PCT, nullable=True),
        sa.Column("evidence_bars", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "market",
            "symbol",
            "timeframe",
            "bar_close_ts",
            "side",
            "factor",
            "prompt_hash",
            name="pk_verdict_cache",
        ),
        sa.CheckConstraint(f"market IN ({MARKETS})", name="ck_verdict_cache_market"),
        sa.CheckConstraint(
            "timeframe IN ('1h', '1d')", name="ck_verdict_cache_timeframe"
        ),
        # factor ต้องเป็นของฝั่งที่ถาม — เขียนเป็น CHECK เดียวแทนสองข้อแยก เพราะ
        # ข้อผิดพลาดที่ตารางนี้ต้องกันคือ "verdict ฝั่ง long ไปนั่งในแถวของฝั่ง short"
        # ไม่ใช่ "ชื่อ factor สะกดผิด" · สองอย่างนี้กัน CHECK เดียวได้พร้อมกัน
        sa.CheckConstraint(
            f"(side = 'long' AND factor IN ({LONG_FACTORS}))"
            f" OR (side = 'short' AND factor IN ({SHORT_FACTORS}))",
            name="ck_verdict_cache_factor_matches_side",
        ),
        # คำตัดสินที่บอกว่ามีปัจจัยแต่ชี้แท่งไม่ได้คือความเห็น ไม่ใช่หลักฐาน
        # (spec/04:55) · บังคับที่ฐานด้วย ไม่ใช่เชื่อว่า `validate()` ถูกเรียกเสมอ
        sa.CheckConstraint(
            "present = false"
            " OR (evidence_bars IS NOT NULL AND array_length(evidence_bars, 1) > 0)",
            name="ck_verdict_cache_present_needs_evidence",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_verdict_cache_confidence_range",
        ),
    )
    # อ่านทีละแท่ง ทีละฝั่ง — Judge ถาม 3 factor พร้อมกันต่อหนึ่งแท่ง (spec/08:31)
    # index นี้ทำให้การดึงทั้งสามตัวเป็นการอ่านช่วงเดียว ไม่ใช่สามครั้งแยกกัน
    op.create_index(
        "ix_verdict_cache_bar",
        "verdict_cache",
        ["market", "symbol", "timeframe", "bar_close_ts", "side"],
    )

    # ตารางข้อเท็จจริง — เขียนได้ครั้งเดียว แก้ไม่ได้ ลบไม่ได้ (ADR 23)
    op.execute("GRANT SELECT, INSERT ON TABLE verdict_cache TO cane_engine")
    op.execute("GRANT SELECT ON TABLE verdict_cache TO cane_console")


def downgrade() -> None:
    # GRANT หายไปเองพร้อม DROP TABLE · ไม่แตะ ENUM ที่ 0004 เป็นเจ้าของ
    op.drop_index("ix_verdict_cache_bar", table_name="verdict_cache")
    op.drop_table("verdict_cache")
