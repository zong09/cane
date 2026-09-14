"""kill_switch — สภาพปัจจุบันของสวิตช์หยุดฉุกเฉิน (spec/06, spec/10:130)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-14

ตารางนี้เป็นหนึ่งในสองตารางของทั้งระบบที่ **เขียนทับได้** (ADR 23 ยกเว้นให้) เพราะมัน
คือ *สภาพปัจจุบัน* ไม่ใช่ *สิ่งที่เกิดขึ้น* · ประวัติว่าใครกดเมื่อไหร่อยู่ที่
`user_audit_log` (ใบ 20) ซึ่งแก้ไม่ได้ — สภาพอ่านจากตารางเดียว ประวัติอ่านจากตารางที่
ไม่มีใครลบได้ **อย่าเอาสองหน้าที่นี้มาไว้ที่เดียวกัน**

ใบ 10 เขียนว่า state อยู่ที่ `var/state/killswitch.json` · **ล้าสมัยแล้ว** — ADR 22
ย้ายทุกอย่างลง PostgreSQL และใบ 14 เก็บกวาดคำว่า `var/state` ออกจากเอกสารไปแล้ว

## trigger คือหัวใจของไฟล์นี้ ไม่ใช่คอลัมน์

`consecutive_loss_breaker` แปลว่า **engine ต้อง latch ได้เอง** ส่วนการปลดเป็นการ*เพิ่ม*
ความเสี่ยง ซึ่ง spec/06 บอกว่าต้องพิมพ์ชื่อ profile ยืนยัน — เป็นการกระทำของคนผ่าน
คอนโซลเท่านั้น

GRANT ของ PostgreSQL แยก "ตั้งเป็น true" ออกจาก "ตั้งเป็น false" ไม่ได้ ให้ UPDATE
ก็ได้ทั้งสองทาง ไม่ให้ก็ไม่ได้ทั้งสองทาง · **trigger แยกได้** — `latched: true → false`
ถูกปฏิเสธเมื่อผู้กระทำไม่ใช่ `cane_console`

ผลคือ process ของ engine **ปลดสวิตช์ที่ตัวเองกดไม่ได้ ต่อให้โค้ดสั่งให้ทำ** ซึ่งเป็น
คุณสมบัติที่โค้ดฝั่ง Python รับประกันให้ไม่ได้เลย — เส้นทางไหนที่เผลอเรียก `unlatch()`
จะพังดังๆ ที่ฐาน แทนที่จะปลดเกราะเงียบๆ

## engine ยัง `INSERT` ได้ เพราะแถวอาจยังไม่เกิด

profile ที่ยังไม่เคยถูกกดสวิตช์เลยไม่มีแถว · ถ้า engine แทรกแถวแรกไม่ได้ การ latch
อัตโนมัติจาก `consecutive_loss_breaker` จะล้มในกรณีที่พบบ่อยที่สุด คือครั้งแรก ·
trigger ครอบ `INSERT` ด้วยไม่ได้และไม่จำเป็น — แถวใหม่ที่ `latched = false` ไม่ได้
ปลดอะไร มันคือสภาพตั้งต้นที่ถูกต้องอยู่แล้ว
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)

#: ปลดได้เฉพาะ role ของคอนโซล · เขียนเป็น trigger เพราะ GRANT แยกทิศของการเขียนไม่ได้
_GUARD = """
CREATE OR REPLACE FUNCTION kill_switch_unlatch_is_a_console_action()
RETURNS trigger AS $$
BEGIN
    IF OLD.latched AND NOT NEW.latched
       AND pg_has_role(current_user, 'cane_console', 'MEMBER') IS NOT TRUE THEN
        RAISE EXCEPTION
            'ปลด kill switch ได้เฉพาะคอนโซล — role % ทำไม่ได้ (spec/06)', current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "kill_switch",
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("latched", sa.Boolean(), nullable=False),
        # ใครกดกับกดเมื่อไหร่ — `None` ได้เมื่อยังไม่เคย latch · ประวัติเต็มอยู่ที่
        # `user_audit_log` ของใบ 20 ที่นี่เก็บแค่ครั้งล่าสุดเพื่อให้คอนโซลแสดงได้
        sa.Column("latched_by", sa.Text(), nullable=True),
        sa.Column("latched_ts", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("profile", name="pk_kill_switch"),
        # สวิตช์ที่ติดอยู่ต้องตอบได้ว่าติดเมื่อไหร่และเพราะอะไร — สวิตช์ที่ติดโดยไม่มี
        # ที่มาคือสิ่งที่คนอ่านคอนโซลแล้วไม่กล้าปลดและไม่กล้าปล่อยไว้
        sa.CheckConstraint(
            "latched = false OR (latched_ts IS NOT NULL AND reason IS NOT NULL)",
            name="ck_kill_switch_latched_has_a_story",
        ),
    )
    op.execute(_GUARD)
    op.execute(
        """
        CREATE TRIGGER kill_switch_guard
        BEFORE UPDATE ON kill_switch
        FOR EACH ROW EXECUTE FUNCTION kill_switch_unlatch_is_a_console_action()
        """
    )

    # engine latch เองได้ (consecutive_loss_breaker) แต่ปลดไม่ได้ — trigger กันไว้
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE kill_switch TO cane_engine"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE kill_switch TO cane_console"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS kill_switch_guard ON kill_switch")
    op.drop_table("kill_switch")
    op.execute("DROP FUNCTION IF EXISTS kill_switch_unlatch_is_a_console_action()")
