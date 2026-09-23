"""replay_cursor — ความคืบหน้าของ replay ย้อนหลัง (ADR 29)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-20

ตารางที่เขียนทับได้อีกตัว (ADR 23 ยกเว้นให้) คู่กับ `kill_switch` ของ 0008 และ `engine_state`
ของ 0009 เพราะมันคือ *สภาพปัจจุบัน* ของ replay ที่กำลังเดิน ไม่ใช่ *สิ่งที่เกิดขึ้น* ·
ประวัติว่า replay ตัดสินอะไรไปบ้างอยู่ที่ `decisions` ซึ่งแก้ไม่ได้

## ตัวบอกความคืบหน้ากับตัวกันรันซ้ำ ไม่ใช่จุด resume

`as_of_ms` บอกว่า replay เดินเสร็จถึงแท่งไหน `end_ts` บอกว่ากำลังไปถึงไหน · แต่ **เงินสดของ
replay อยู่ในหน่วยความจำของ `PaperBroker`** ไม่ได้อยู่ในฐาน จึงเอาแถวนี้ไปพา process ใหม่
เดินต่อไม่ได้ — process ตายแล้วต้องเริ่มใหม่ใน scratch database ที่สร้างใหม่ · แถวนี้มีไว้กัน
การรันซ้ำใน scratch เดิม เพราะ `insert_decision` ไม่มี `ON CONFLICT` (ADR 27 §27.1) รันซ้ำแล้ว
ทุกแท่งจะมีสองแถวโดยไม่มีอะไรฟ้อง

## ไม่มี DELETE ให้ใคร

reset replay ด้วยการ **drop scratch database ทั้งใบ** ไม่ใช่ลบแถว · แถวที่ลบได้คือช่องโหว่:
ใครลบแล้ว replay ที่รันไปแล้วก็ดูเหมือนยังไม่เคยรัน
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)


def upgrade() -> None:
    op.create_table(
        "replay_cursor",
        sa.Column("profile", _PROFILE_T, nullable=False),
        # `as_of` ล่าสุดที่เดินเสร็จ · epoch ms เสมอ (`db/types.py`)
        sa.Column("as_of_ms", sa.BigInteger(), nullable=False),
        # `as_of` ปลายทางของรันนี้ · ไม่เปลี่ยนตลอดรัน (engine ไม่มีสิทธิ์ UPDATE คอลัมน์นี้)
        sa.Column("end_ts", sa.BigInteger(), nullable=False),
        # เวลานาฬิกาที่เขียนแถวล่าสุด · epoch ms
        sa.Column("updated_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("profile", name="pk_replay_cursor"),
        # ศูนย์หรือติดลบแปลว่ามีใครส่งวินาทีหรือ `None` ที่ถูกแปลงเป็นเลขมา (เหตุผลเดียวกับ 0009)
        sa.CheckConstraint(
            "as_of_ms > 0",
            name="ck_replay_cursor_as_of_is_epoch_ms",
        ),
        # ความคืบหน้าเลยปลายทางไม่ได้ — `advance()` ที่ข้าม `end_ts` ถูกฐานปฏิเสธเอง
        sa.CheckConstraint(
            "end_ts >= as_of_ms",
            name="ck_replay_cursor_end_not_before_as_of",
        ),
        sa.CheckConstraint(
            "updated_ts > 0",
            name="ck_replay_cursor_updated_is_epoch_ms",
        ),
    )

    op.execute("GRANT SELECT ON TABLE replay_cursor TO cane_engine")
    op.execute("GRANT SELECT ON TABLE replay_cursor TO cane_console")

    # engine เลื่อนความคืบหน้าได้อย่างเดียว · เปลี่ยน `end_ts` หรือ `profile` ไม่ได้
    op.execute(
        "GRANT INSERT (profile, as_of_ms, end_ts, updated_ts) "
        "ON TABLE replay_cursor TO cane_engine"
    )
    op.execute(
        "GRANT UPDATE (as_of_ms, updated_ts) "
        "ON TABLE replay_cursor TO cane_engine"
    )


def downgrade() -> None:
    # GRANT หายไปพร้อมตาราง ไม่ต้อง REVOKE แยก
    op.drop_table("replay_cursor")
