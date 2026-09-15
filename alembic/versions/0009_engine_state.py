"""engine_state — เจตนาของคนกับ heartbeat ของ engine (spec/10 §5. state ที่อยู่ในตาราง)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-15

ตารางที่สองของระบบที่ **เขียนทับได้** (ADR 23 ยกเว้นให้) คู่กับ `kill_switch` ของ 0008
เพราะมันคือ *สภาพปัจจุบัน* ไม่ใช่ *สิ่งที่เกิดขึ้น* · ประวัติว่าใครกด start/stop เมื่อไหร่
อยู่ที่ `user_audit_log` (ใบ 20) ซึ่งแก้ไม่ได้

คำบรรยายใบ 18 เขียนว่า state อยู่ที่ `var/state/{profile}.json` · **ล้าสมัยแล้ว** ด้วย
เหตุผลเดียวกับที่ 0008 เขียนไว้ และ spec/10 ขึ้นต้นหน้าด้วย errata ของข้อนี้โดยตรง:
engine แต่ละ profile เป็นคนละ process กับคอนโซล state ที่สองฝ่ายเขียนต้องมีการล็อก
ซึ่ง DB ทำอยู่แล้วและไฟล์ JSON ไม่ทำ

## ไม่มีคอลัมน์ "สถานะ" และจะไม่มี

`running` / `crashed` / `stopping` **คิดใหม่ทุกครั้งที่ถาม** จาก `should_run` กับ
`last_heartbeat_ts` (spec/10 §2. สาม state ที่คนละเรื่องกัน) · เก็บลงตารางเมื่อไหร่จะได้
ค่าที่ค้างอยู่ตอน process ตายกลางทาง ซึ่งเป็นการโกหกชนิดเดียวกับที่ spec/10 ทั้งหน้ามีไว้กัน

ด้วยเหตุผลเดียวกัน **ไม่มี CHECK ที่ผูก `should_run` กับ `last_heartbeat_ts`** — `crashed`
คือ `should_run = true` คู่กับ heartbeat ที่เก่า ซึ่งเป็นคู่ที่ใบนี้มีไว้ตรวจจับพอดี
constraint ที่ห้ามคู่นั้นคือ constraint ที่ห้ามไม่ให้ตรวจเจอ

## ไม่มี `updated_ts` ทั้งที่ `kill_switch` มี

`kill_switch` มีผู้เขียนฝั่งเดียว `updated_ts` จึงไม่กำกวม · ตารางนี้มีผู้เขียนสองฝ่าย
คอลัมน์ที่ทั้งคู่ประทับจะเป็นกับดักสองชั้น: มันทำลายการแบ่งสิทธิ์ระดับคอลัมน์ข้างล่าง
(ทั้งสอง role ต้อง UPDATE ได้) และโค้ดที่หลังๆ อ่านมันเป็นตัวแทนความมีชีวิตจะอ่าน
"การกด stop" เป็น heartbeat แล้วค้างที่ `stopping` ตลอดไป

## GRANT ระดับคอลัมน์ ไม่ใช่ trigger

0008 ต้องใช้ trigger เพราะสิ่งที่ต้องแยกคือ **ทิศทางบนคอลัมน์เดียวกัน** (`latched`
true→false ต่างจาก false→true) ซึ่ง GRANT แยกไม่ได้จริงๆ

spec/10 §5. state ที่อยู่ในตาราง แยกคนละอย่าง — **แยกตามคอลัมน์** คอนโซลเขียน
`should_run` ส่วน engine เขียน `last_heartbeat_ts` กับ `blocked_reason` ·
`GRANT UPDATE (คอลัมน์)` คือเครื่องมือของงานนี้ตรงตัว และมีที่ใช้อยู่แล้วที่ 0002 (`GRANT UPDATE (is_active) ON config_versions`)
trigger ที่เพิ่มมาจะอ่อนกว่า (ทำงานหลังด่านสิทธิ์) และเพิ่มกลไกที่ต้องดูแลให้ตรงกันอีกตัว

**`server_default` ของ `should_run` จึงเป็นของจำเป็น ไม่ใช่ความสะดวก** — engine ต้อง
แทรกแถวแรกของตัวเองได้ (เหตุผลเดียวกับ 0008: profile ที่ยังไม่เคยเดินไม่มีแถว) แต่
`GRANT INSERT` ของมันไม่มีคอลัมน์ `should_run` อยู่ในรายการ มันจึง**เอ่ยถึงคอลัมน์นั้น
ไม่ได้เลย** และแถวที่ได้ต้องมาจาก DEFAULT เสมอ · การแทรกแถวจึงไม่ใช่การประกาศว่าจะรัน
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)


def upgrade() -> None:
    op.create_table(
        "engine_state",
        sa.Column("profile", _PROFILE_T, nullable=False),
        # เจตนาของคน — ดูหัวไฟล์ว่าทำไม DEFAULT ตัวนี้เป็นส่วนหนึ่งของด่านสิทธิ์
        sa.Column(
            "should_run",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # ความจริงของ process · `None` = ยังไม่เคยเดินเลย ไม่ใช่ "เดินเมื่อนานมาแล้ว"
        sa.Column("last_heartbeat_ts", sa.BigInteger(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("profile", name="pk_engine_state"),
        # `blocked` ที่ไม่มีเหตุผลกำกับคือสิ่งที่คนอ่านคอนโซลแล้วไม่รู้ว่าต้องไปแก้อะไร
        # — ช่องว่างล้วนคือค่าที่ดูเหมือนมีแต่ไม่มี ซึ่งแย่กว่า `NULL` ตรงๆ
        sa.CheckConstraint(
            "blocked_reason IS NULL OR length(btrim(blocked_reason)) > 0",
            name="ck_engine_state_blocked_reason_has_a_story",
        ),
        # epoch ms เสมอ (`db/types.py`) · ศูนย์หรือติดลบแปลว่ามีใครส่งวินาทีหรือ `None`
        # ที่ถูกแปลงเป็นเลขมา แล้ว heartbeat จะดูเก่าตลอดกาลโดยไม่มีอะไรส่งเสียง
        sa.CheckConstraint(
            "last_heartbeat_ts IS NULL OR last_heartbeat_ts > 0",
            name="ck_engine_state_heartbeat_is_epoch_ms",
        ),
    )

    op.execute("GRANT SELECT ON TABLE engine_state TO cane_engine")
    op.execute("GRANT SELECT ON TABLE engine_state TO cane_console")

    # คอนโซลประกาศเจตนาได้อย่างเดียว · ปลอม heartbeat ไม่ได้ และแตะเหตุที่ถูกบล็อกไม่ได้
    op.execute(
        "GRANT INSERT (profile, should_run) ON TABLE engine_state TO cane_console"
    )
    op.execute("GRANT UPDATE (should_run) ON TABLE engine_state TO cane_console")

    # engine รายงานความจริงของตัวเองได้อย่างเดียว · สั่งให้ตัวเองรันไม่ได้
    op.execute(
        "GRANT INSERT (profile, last_heartbeat_ts, blocked_reason) "
        "ON TABLE engine_state TO cane_engine"
    )
    op.execute(
        "GRANT UPDATE (last_heartbeat_ts, blocked_reason) "
        "ON TABLE engine_state TO cane_engine"
    )


def downgrade() -> None:
    # GRANT หายไปพร้อมตาราง ไม่ต้อง REVOKE แยก
    op.drop_table("engine_state")
