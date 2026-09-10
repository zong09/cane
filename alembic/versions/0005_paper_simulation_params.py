"""ค่าธรรมเนียมและ maintenance margin ของการจำลอง — `PaperBroker` คิด P&L ไม่ได้ถ้าไม่มี

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09

เกณฑ์ปิดใบของ `PaperBroker` คือ "คำนวณ P&L ได้ถูก" และ "จำลอง liquidation ได้" ซึ่งทั้งคู่
ต้องมีตัวเลขที่**ไม่มีอยู่ที่ไหนเลยในระบบ** — สเปกไม่ได้ให้ และ config มีแต่ `seed_quote`
ทางที่เลือกคือเก็บลง config ไม่ใช่ฝังเป็นค่าคงที่ในโค้ด ตามหลักของ spec/06 ที่ว่าเพดานและ
ค่าของความเสี่ยง "ไม่มีค่าตั้งต้นในโค้ด ต้องระบุใน config profile"

**ทั้งสองคอลัมน์เป็นพารามิเตอร์ของ*การจำลอง* ไม่ใช่ของการเทรดจริง** จึงคุมด้วย CHECK
`kind = 'paper'` แบบเดียวกับ `seed_quote` — ฝั่ง live ค่าจริงมาจากของที่ปลายทางส่งกลับมา
(fee ของแต่ละ fill และ `liquidationPrice` ของ position) การเก็บค่าที่เราเดาไว้ใน config
ของ live จะสร้างแหล่งความจริงที่สองที่ขัดกับใบแจ้งของ venue

**ไม่ backfill แถวเดิม และไม่บังคับว่า paper ต้องมีค่า** — สองข้อนี้ตั้งใจ:

- เครื่อง dev มี `paper` v1 อยู่แล้ว การเติมตัวเลขให้มันคือการเขียนค่าที่ไม่มีใครกรอกลงไป
  ในเวอร์ชันที่ประกาศตัวว่าแก้ไม่ได้ · ปล่อยเป็น `NULL` แล้ว seed ใหม่ให้ได้ v2 ที่ครบ
- ถ้าบังคับ `NOT NULL` หรือบังคับที่ชั้น `Settings` เวอร์ชัน `paper` v1 ที่เก็บไว้แล้วจะ
  **อ่านกลับไม่ได้** ซึ่งเป็นกับดักเดียวกับที่ใบ 03b เจอตอน `NUMERIC(9,4)` ปัดค่าเงียบๆ

ประตูที่ปิดจริงจึงอยู่ที่ `PaperBroker` เอง — ไม่มีค่าเมื่อไหร่ก็สร้าง broker ไม่ขึ้น
เป็น fail-closed ตรงจุดที่ค่านั้นถูกใช้ ที่เดียวกับที่ `seed_quote` ถูกบังคับอยู่แล้ว

**ไม่มี GRANT ในไฟล์นี้** — ไม่มีตารางใหม่ grant ระดับตารางของ `0002` ครอบคอลัมน์ใหม่ให้เอง
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: ทศนิยมของเปอร์เซ็นต์ — ตรงกับ `PCT` ใน schema.py และกับ `PCT_SCALE` ใน db/types.py
#: เขียนซ้ำที่นี่เพราะ migration เป็น snapshot ของวันที่เขียน ไม่ใช่ของ schema วันนี้
PCT = sa.Numeric(precision=9, scale=4)


def upgrade() -> None:
    op.add_column("config_broker", sa.Column("taker_fee_pct", PCT, nullable=True))
    op.add_column("config_broker", sa.Column("maintenance_margin_pct", PCT, nullable=True))

    # เหตุผลเดียวกับ `ck_config_broker_seed_quote` — ค่าของการจำลองไม่มีความหมายกับ
    # broker จริง และการปล่อยให้ตั้งได้จะทำให้คนอ่าน config ของ live เข้าใจว่าระบบ
    # คิด fee เองแทนที่จะใช้ค่าที่ venue แจ้งมา
    op.create_check_constraint(
        "ck_config_broker_paper_only_sim",
        "config_broker",
        "kind = 'paper' OR (taker_fee_pct IS NULL AND maintenance_margin_pct IS NULL)",
    )
    # fee ติดลบคือรายได้ต่อไม้ · MMR เป็นศูนย์แปลว่าไม่มีวัน liquidate ซึ่งเป็นการปิด
    # ชั้นป้องกันทั้งชั้นด้วยการกรอกเลข ไม่ใช่ด้วยการตัดสินใจ
    op.create_check_constraint(
        "ck_config_broker_taker_fee_range",
        "config_broker",
        "taker_fee_pct IS NULL OR (taker_fee_pct >= 0 AND taker_fee_pct < 100)",
    )
    op.create_check_constraint(
        "ck_config_broker_mmr_range",
        "config_broker",
        "maintenance_margin_pct IS NULL OR "
        "(maintenance_margin_pct > 0 AND maintenance_margin_pct < 100)",
    )


def downgrade() -> None:
    # ถอยได้เสมอ — ค่าที่หายไปคือค่าของการจำลองเท่านั้น ไม่มีไม้จริงไหนอ้างมันอยู่
    # (ledger อ้าง `fee_quote` ที่เป็นยอดเงินจริงต่อ fill ไม่ใช่อัตราใน config)
    for name in (
        "ck_config_broker_mmr_range",
        "ck_config_broker_taker_fee_range",
        "ck_config_broker_paper_only_sim",
    ):
        op.drop_constraint(name, "config_broker", type_="check")
    op.drop_column("config_broker", "maintenance_margin_pct")
    op.drop_column("config_broker", "taker_fee_pct")
