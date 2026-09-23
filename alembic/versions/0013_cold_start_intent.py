"""cold_start_intent — เส้นทาง cold start ที่คนเลือกไว้ให้ run ถัดไป (ADR 35)

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-23

ตารางสภาพอีกตัว (ADR 23 ยกเว้นให้) คู่กับ `kill_switch` ของ 0008 · แถวหนึ่งคือ "ครั้งหน้าที่ engine
เริ่ม ให้เหรียญนี้เดินทางนี้" · ประวัติว่าใครเลือกอะไรเมื่อไหร่อยู่ที่ `user_audit_log` ซึ่งลบไม่ได้ ·
ทางที่ใช้จริงอยู่ที่ `decisions.cold_start`

## ใช้แล้วหายไป — สิทธิ์ของสอง role จึงไม่เหมือนตารางสภาพตัวอื่น

engine อ่านแถวที่แท่งแรกของ run ของเหรียญนั้น แล้ว **`DELETE`** ในทรานแซกชันเดียวกับแถว
`decisions` (ADR 35 §กลไก) · จึงได้ `SELECT, DELETE` และ **ไม่ได้** `INSERT`/`UPDATE` — engine
เลือกเส้นทางแทนคนไม่ได้ ต่อให้โค้ดสั่ง

คอนโซลได้ `SELECT, INSERT, UPDATE` และ **ไม่ได้** `DELETE` — ยกเลิกเจตนาคือการเลือก `skip`
ไม่ใช่การลบแถวทิ้งจนดูเหมือนไม่เคยมีใครเลือก

## `route` มีสองค่า ไม่ใช่สาม

`wait_1h` ยังไม่มีใน engine (ADR 35 §ตัดสินแล้ว) · endpoint ตอบ 422 และ CHECK ปฏิเสธซ้ำอีกชั้น
เพื่อให้เส้นทางไหนที่ข้าม endpoint มาก็เขียนไม่ลง · วันที่ engine สร้าง `wait_1h` คือวันที่แก้ CHECK นี้
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARKETS = "'usdtm_perp', 'spot'"
ROUTES = "'trailing', 'skip'"

_PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)


def upgrade() -> None:
    op.create_table(
        "cold_start_intent",
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        # ชื่อคน ไม่ใช่ FK ไป `users.id` — แบบเดียวกับ `kill_switch.latched_by` · FK จะทำให้ลบ/ระงับ
        # ผู้ใช้ที่มีเจตนาค้างอยู่ไม่ได้ ซึ่งไม่มีใครตัดสินว่าควรเป็นอย่างนั้น · id ของคนเลือกอยู่ที่
        # `user_audit_log` ซึ่งลบไม่ได้
        sa.Column("chosen_by", sa.Text(), nullable=False),
        sa.Column("chosen_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("profile", "market", "symbol", name="pk_cold_start_intent"),
        sa.CheckConstraint(f"market IN ({MARKETS})", name="ck_cold_start_intent_market"),
        sa.CheckConstraint(f"route IN ({ROUTES})", name="ck_cold_start_intent_route"),
        # ศูนย์หรือติดลบแปลว่ามีใครส่งวินาทีหรือ `None` ที่ถูกแปลงเป็นเลขมา (เหตุผลเดียวกับ 0009)
        sa.CheckConstraint("chosen_ts > 0", name="ck_cold_start_intent_chosen_is_epoch_ms"),
    )
    op.execute("GRANT SELECT, DELETE ON TABLE cold_start_intent TO cane_engine")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE cold_start_intent TO cane_console")


def downgrade() -> None:
    # GRANT หายไปเองพร้อม DROP TABLE
    op.drop_table("cold_start_intent")
