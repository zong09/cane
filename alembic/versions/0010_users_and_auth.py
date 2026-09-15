"""ผู้ใช้และการยืนยันตัวตน (spec/09 §9. ที่เก็บ)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-15

## ไฟล์นี้ยาวเพราะข้อบังคับของ spec/09 ส่วนใหญ่เขียนลงฐานได้ ไม่ใช่เพราะตารางเยอะ

สามข้อที่ **ไม่ได้อยู่ในโค้ด Python เลย** และนั่นคือประเด็น:

1. `user_audit_log` กับ `login_attempts` ไม่มี `UPDATE`/`DELETE` ให้ใคร — append-only
   ด้วยสิทธิ์ ไม่ใช่ด้วยข้อตกลง (ADR 23) · ตารางที่บอกว่า "ใครทำอะไร" ซึ่งคนที่ทำ
   แก้ได้เอง ไม่ได้บอกอะไรเลย
2. `users.email` ไม่อยู่ใน `GRANT UPDATE` — คอนโซลเปลี่ยนอีเมลของบัญชีไม่ได้
   ต่อให้เขียนโค้ดสั่งให้ทำ · อีเมลคือตัวตนที่ audit log อ้างถึง
3. เนื้อของ `role_permissions` ไม่มี `UPDATE` — แก้ตารางสิทธิ์คือ **สร้างเวอร์ชันใหม่
   แล้วเลื่อนตัวชี้** (ADR 18) คอนโซลได้ `UPDATE` เฉพาะ `permission_versions.is_active`
   เหมือนที่ `config_versions` ได้มาตั้งแต่ 0002

## trigger สองตัว — ที่ GRANT แยกไม่ได้

`GRANT` แยก "ทิศ" ของการเขียนไม่ได้ (เหตุผลเดียวกับ 0008 kill switch):

- **`users_owner_never_reaches_zero`** — spec/09 บอกว่าระบบที่ไม่มี OWNER แก้ profile
  และสลับ `dry_run` ไม่ได้อีกเลย และ **ไม่มีทางกู้จากในคอนโซล** · ข้อบังคับที่การพลาด
  ครั้งเดียวทำให้ระบบกู้ไม่ได้ ต้องอยู่ต่ำกว่าชั้นที่พลาดได้
- **`role_permissions_owner_column_is_locked`** — spec/09 บอกว่าคอลัมน์ OWNER ล็อก
  และย้ำว่า "ปุ่มที่ปิดไว้เป็นความสุภาพ ไม่ใช่ข้อบังคับ" · ตัวตรวจสิทธิ์ฝั่ง Python
  ก็คืน True ให้ OWNER ก่อนแตะตารางอยู่แล้ว trigger นี้กันเส้นทางที่เขียน **ข้อมูล**
  ที่ขัดกับโค้ดนั้นลงไปได้ตั้งแต่แรก

## ที่เก็บความลับ — บังคับที่ชนิดของคอลัมน์ไม่ได้ จึงบังคับที่ชื่อ

`password_hash` `token_hash` `code_hash` `totp_secret_enc` — ชื่อคอลัมน์บอกว่าข้างใน
ต้องเป็นอะไร (ADR 25) · `totp_secret_enc` เป็น **ciphertext** ไม่ใช่ hash เพราะ TOTP
ต้องถอดกลับมาคำนวณได้ · เกณฑ์ตัดสินของทั้งชุดคือ **dump ของ DB อย่างเดียวต้องใช้
เข้าระบบไม่ได้**

## roles กับ permissions ถูก seed ที่นี่ แต่ matrix ไม่ใช่

`roles` (5 แถว) กับ `permissions` (13 แถว) คือ **คำศัพท์** ที่โค้ดอ้างชื่อตรงๆ ไม่ใช่
ค่าที่คนแก้ · ส่วน **ว่า role ไหนได้ cap ไหน** เป็นข้อมูลที่คอนโซลแก้ได้ (spec/09
"ตารางนี้เป็น 'ข้อมูล' ไม่ใช่ค่าคงที่ในโค้ด") จึงมาเป็นเวอร์ชันแรกจาก
`cane auth seed-permissions` ไม่ใช่จาก migration

ผลที่ตั้งใจ: ฐานที่ migrate แล้วแต่ยังไม่ seed **ปฏิเสธทุก action** เพราะไม่มีเวอร์ชัน
active ให้เทียบ — ตรงกับ spec/09 ที่ว่าค่าเริ่มต้นของสิ่งที่ยังไม่มีในตารางคือ 403
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUS_T = postgresql.ENUM(
    "pending", "active", "suspended", name="user_status_t", create_type=False
)
_TOKEN_KIND_T = postgresql.ENUM(
    "login", "invite", "reset_2fa", "reset_password",
    name="auth_token_kind_t", create_type=False,
)

#: 5 role ของ spec/09 · ลำดับคือลำดับที่หน้าจอแสดง ไม่ใช่ลำดับของสิทธิ์
ROLES = ("OWNER", "ADMIN", "TRADER", "VIEWER", "AUDITOR")

#: 13 สิทธิ์ของ spec/09 · `cap` คือชื่อจริงในโค้ดและใน DB ไม่ใช่คำบรรยาย
PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("view_overview", "ดูภาพรวม กราฟ โซน และสถานะ engine"),
    ("read_decisions", "อ่านบันทึกการตัดสินใจ"),
    ("choose_cold_start_route", "เลือกเส้นทาง cold start"),
    ("close_position_manual", "ปิดไม้ด้วยมือ (ฉุกเฉิน)"),
    ("killswitch_latch", "กด kill switch หยุดฉุกเฉิน"),
    ("killswitch_unlatch", "ปลด kill switch"),
    ("engine_control", "start / stop engine"),
    ("toggle_dry_run", "สลับ dry_run เป็นยิงจริง"),
    ("edit_profile", "แก้โปรไฟล์ risk limit allow_short และการตั้งค่าแจ้งเตือน"),
    ("edit_symbols", "เพิ่ม–ลบคู่เหรียญ"),
    ("manage_users", "เชิญ ย้าย role ระงับ ปลดล็อกบัญชี ตัด session แก้ตารางสิทธิ์"),
    ("reset_other_2fa", "reset 2FA ของคนอื่น"),
    ("export_records", "ส่งออกบันทึกทั้งหมด"),
)

_OWNER_NEVER_ZERO = """
CREATE OR REPLACE FUNCTION users_owner_never_reaches_zero()
RETURNS trigger AS $$
DECLARE
    owner_id integer;
    remaining integer;
BEGIN
    SELECT id INTO owner_id FROM roles WHERE name = 'OWNER';

    IF OLD.role_id = owner_id AND OLD.status = 'active'
       AND (NEW.role_id <> owner_id OR NEW.status <> 'active') THEN
        SELECT count(*) INTO remaining
        FROM users
        WHERE role_id = owner_id AND status = 'active' AND id <> OLD.id;

        IF remaining = 0 THEN
            RAISE EXCEPTION
                'OWNER ที่ใช้งานอยู่คนสุดท้าย ระงับหรือย้าย role ไม่ได้ (spec/09)'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_OWNER_COLUMN_LOCKED = """
CREATE OR REPLACE FUNCTION role_permissions_owner_column_is_locked()
RETURNS trigger AS $$
BEGIN
    IF NOT NEW.allowed
       AND NEW.role_id = (SELECT id FROM roles WHERE name = 'OWNER') THEN
        RAISE EXCEPTION
            'คอลัมน์ OWNER ของตารางสิทธิ์ล็อก — ถอนสิทธิ์ของ OWNER ไม่ได้ (spec/09)'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute("CREATE TYPE user_status_t AS ENUM ('pending', 'active', 'suspended')")
    # `login` คือตั๋วของขั้นที่ 1 (spec/09 §5. login สองขั้น) — อายุ 5 นาที ใช้ได้
    # ครั้งเดียว ผูกกับอีเมลนั้น และ **ไม่ใช่ session** · อยู่ในตารางเดียวกับอีกสามชนิด
    # เพราะเป็นกลไกเดียวกันทุกประการ ต่างกันแค่ว่าปลดล็อกให้ทำอะไรและอยู่ได้นานแค่ไหน
    op.execute(
        "CREATE TYPE auth_token_kind_t AS ENUM "
        "('login', 'invite', 'reset_2fa', 'reset_password')"
    )

    # ── คำศัพท์: role กับ cap ────────────────────────────────────────────────

    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("cap", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permissions"),
        sa.UniqueConstraint("cap", name="uq_permissions_cap"),
    )

    roles = sa.table("roles", sa.column("name", sa.Text))
    op.bulk_insert(roles, [{"name": name} for name in ROLES])
    permissions = sa.table(
        "permissions", sa.column("cap", sa.Text), sa.column("note", sa.Text)
    )
    op.bulk_insert(
        permissions, [{"cap": cap, "note": note} for cap, note in PERMISSIONS]
    )

    # ── ผู้ใช้ ───────────────────────────────────────────────────────────────

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("status", _STATUS_T, nullable=False, server_default="pending"),
        sa.Column("password_hash", sa.Text(), nullable=True),
        # ciphertext ไม่ใช่ hash — TOTP ต้องถอดกลับมาคำนวณได้ (ADR 25)
        sa.Column("totp_secret_enc", sa.Text(), nullable=True),
        sa.Column("totp_enrolled_ts", sa.BigInteger(), nullable=True),
        # ช่วงเวลาล่าสุดที่ TOTP ผ่าน · spec/09 §TOTP — ปฏิเสธ counter ที่ ≤ ค่านี้
        # ไม่งั้นรหัสที่หลุดตายังยิงซ้ำได้อีก 90 วินาที
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column("last_login_ts", sa.BigInteger(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_users_role"),
        # บัญชี `active` ต้องตั้งรหัสผ่านและผูก TOTP ครบแล้ว — spec/09 ·
        # เขียนเป็น CHECK เพราะ "active ที่ยังไม่ผูก TOTP" คือสภาพที่ทำให้ข้อบังคับ
        # "ยังไม่ผูก 2FA เข้าไม่ได้เลย" กลายเป็นเรื่องของโค้ดที่อาจลืมตรวจ
        sa.CheckConstraint(
            "status <> 'active' OR "
            "(password_hash IS NOT NULL AND totp_enrolled_ts IS NOT NULL)",
            name="ck_users_active_means_fully_enrolled",
        ),
        sa.CheckConstraint("email = lower(email)", name="ck_users_email_is_lowercase"),
    )

    # ── เวอร์ชันของตารางสิทธิ์ (ADR 18) ──────────────────────────────────────

    op.create_table(
        "permission_versions",
        sa.Column("id", sa.Integer(), sa.Identity(always=True), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id", name="pk_permission_versions"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_permission_versions_author"
        ),
    )
    # active ได้ทีละเวอร์ชันเดียว — แบบเดียวกับ `config_versions` ของ 0002
    op.create_index(
        "uq_permission_versions_one_active",
        "permission_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint(
            "version_id", "role_id", "permission_id", name="pk_role_permissions"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"], ["permission_versions.id"], name="fk_role_permissions_version"
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_role_permissions_role"),
        sa.ForeignKeyConstraint(
            ["permission_id"], ["permissions.id"], name="fk_role_permissions_permission"
        ),
    )

    # ── 2FA ──────────────────────────────────────────────────────────────────

    op.create_table(
        "backup_codes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.Column("used_ts", sa.BigInteger(), nullable=True),
        # ชุดใหม่ฆ่าชุดเก่าทั้งชุด (spec/09 §backup code) · แยกจาก `used_ts` เพราะ
        # "ถูกใช้ไปแล้ว" กับ "ถูกแทนที่" เป็นคนละเรื่องตอนอ่านย้อนหลัง
        sa.Column("retired_ts", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_backup_codes"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_backup_codes_user"),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_backup_codes_one_per_user"),
    )

    # ── session ──────────────────────────────────────────────────────────────

    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # hash ของ token ไม่ใช่ token (ADR 25) · dump ของตารางนี้สวมสิทธิ์ใครไม่ได้
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        # โหมดที่ session นี้กำลังดู (spec/10 §3. สลับโหมดไม่ใช่การควบคุม) — อยู่ที่นี่
        # ไม่ใช่ในคุกกี้ เพราะ spec/09 ห้ามเชื่ออะไรที่ฝั่งผู้ใช้ตั้งเองได้
        sa.Column("mode", postgresql.ENUM(name="profile_t", create_type=False),
                  nullable=False, server_default="paper"),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_ts", sa.BigInteger(), nullable=False),
        sa.Column("expires_ts", sa.BigInteger(), nullable=False),
        sa.Column("revoked_ts", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user"),
        sa.CheckConstraint("expires_ts > created_ts", name="ck_sessions_expiry_is_ahead"),
    )
    op.create_index("ix_sessions_user", "sessions", ["user_id"])

    # ── ลิงก์ใช้ครั้งเดียว: คำเชิญ / reset 2FA / ตั้งรหัสผ่านใหม่ ─────────────

    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", _TOKEN_KIND_T, nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.Column("expires_ts", sa.BigInteger(), nullable=False),
        sa.Column("used_ts", sa.BigInteger(), nullable=True),
        # การออกใหม่ฆ่าลิงก์เดิม (spec/09) — เหตุผลเดียวกับ `backup_codes.retired_ts`
        sa.Column("retired_ts", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_auth_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_tokens_user"),
        sa.CheckConstraint(
            "expires_ts > created_ts", name="ck_auth_tokens_expiry_is_ahead"
        ),
    )

    # ── สองตารางที่แก้ไม่ได้ ─────────────────────────────────────────────────

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        # อีเมลที่ไม่มีบัญชีก็ต้องถูกนับ (spec/09 §การล็อกบัญชี) `user_id` จึง nullable
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        # ขั้นไหนของ login · `unlock` คือการที่ OWNER/ADMIN ล้างตัวนับให้ด้วยมือ
        # ซึ่ง **ไม่ใช่การ login สำเร็จ** ถึงจะล้างตัวนับเหมือนกัน · ถ้าใช้ `ok`
        # ตัวเดียวแยกสองเรื่องนี้ ประวัติจะอ่านว่าคนคนนั้น login เข้ามาเองตอนนั้น
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_login_attempts"),
        sa.CheckConstraint(
            "kind IN ('password', 'totp', 'backup_code', 'unlock')",
            name="ck_login_attempts_kind",
        ),
        # `unlock` เป็นการล้างตัวนับเสมอ ไม่มี `unlock` ที่ล้มเหลว
        sa.CheckConstraint(
            "kind <> 'unlock' OR ok", name="ck_login_attempts_unlock_always_succeeds"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_login_attempts_user"),
        # `users.email` มี CHECK เดียวกันอยู่แล้ว · ถ้าตารางนี้ไม่มี อีเมลที่ไม่มีบัญชี
        # จะถูกนับแยกกันตามตัวพิมพ์ แล้วการล็อกจะเลี่ยงได้ด้วยการพิมพ์ใหญ่สลับเล็ก
        sa.CheckConstraint(
            "email = lower(email)", name="ck_login_attempts_email_is_lowercase"
        ),
    )
    op.create_index("ix_login_attempts_email_ts", "login_attempts", ["email", "ts"])

    op.create_table(
        "user_audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        # `None` ได้ เมื่อเป็นการกระทำที่ยังไม่รู้ว่าใคร (login ที่ไม่สำเร็จ)
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        # ผ่าน `cane.log.redact()` มาแล้วตั้งแต่ก่อนถึงที่นี่ (spec/09 §redaction เกิดตอนเขียน ไม่ใช่ตอนอ่าน
        # เกิดตอนเขียน ไม่ใช่ตอนอ่าน) — ตารางนี้ลบไม่ได้ ของที่หลุดลงมาแล้วอยู่ถาวร
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("step_up_verified", sa.Boolean(), nullable=False),
        sa.Column("ts", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_audit_log"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_user_audit_log_actor"
        ),
    )
    op.create_index("ix_user_audit_log_ts", "user_audit_log", ["ts"])

    # ── trigger ที่ GRANT แทนไม่ได้ ──────────────────────────────────────────

    op.execute(_OWNER_NEVER_ZERO)
    op.execute(
        """
        CREATE TRIGGER users_owner_floor
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION users_owner_never_reaches_zero()
        """
    )
    op.execute(_OWNER_COLUMN_LOCKED)
    op.execute(
        """
        CREATE TRIGGER role_permissions_owner_lock
        BEFORE INSERT ON role_permissions
        FOR EACH ROW EXECUTE FUNCTION role_permissions_owner_column_is_locked()
        """
    )

    # ── สิทธิ์ ───────────────────────────────────────────────────────────────
    #
    # engine ไม่ได้อะไรเลยจากไฟล์นี้ · บอทไม่มีบัญชีผู้ใช้ ไม่อ่านสิทธิ์ของคน และ
    # ไม่เขียน audit log ของคน — ของที่ engine ทำเองอยู่ในตารางข้อเท็จจริงของมัน

    for table in ("roles", "permissions"):
        op.execute(f"GRANT SELECT ON TABLE {table} TO cane_console")
        op.execute(f"GRANT SELECT ON TABLE {table} TO cane_engine")

    op.execute("GRANT SELECT, INSERT ON TABLE users TO cane_console")
    # `email`, `id`, `created_ts` ไม่อยู่ในรายการ — เปลี่ยนตัวตนของบัญชีไม่ได้
    op.execute(
        "GRANT UPDATE (name, role_id, status, password_hash, totp_secret_enc, "
        "totp_enrolled_ts, totp_last_counter, last_login_ts) "
        "ON TABLE users TO cane_console"
    )

    op.execute("GRANT SELECT, INSERT ON TABLE permission_versions TO cane_console")
    op.execute(
        "GRANT UPDATE (is_active) ON TABLE permission_versions TO cane_console"
    )
    # เนื้อของเวอร์ชันแก้ไม่ได้แม้แต่คอนโซล (spec/09)
    op.execute("GRANT SELECT, INSERT ON TABLE role_permissions TO cane_console")

    op.execute("GRANT SELECT, INSERT ON TABLE backup_codes TO cane_console")
    op.execute(
        "GRANT UPDATE (used_ts, retired_ts) ON TABLE backup_codes TO cane_console"
    )

    op.execute("GRANT SELECT, INSERT ON TABLE sessions TO cane_console")
    op.execute(
        "GRANT UPDATE (last_seen_ts, revoked_ts, mode) ON TABLE sessions TO cane_console"
    )

    op.execute("GRANT SELECT, INSERT ON TABLE auth_tokens TO cane_console")
    op.execute(
        "GRANT UPDATE (used_ts, retired_ts) ON TABLE auth_tokens TO cane_console"
    )

    # append-only — ไม่มี UPDATE ไม่มี DELETE ให้ใครทั้งสิ้น (ADR 23)
    op.execute("GRANT SELECT, INSERT ON TABLE login_attempts TO cane_console")
    op.execute("GRANT SELECT, INSERT ON TABLE user_audit_log TO cane_console")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS role_permissions_owner_lock ON role_permissions")
    op.execute("DROP TRIGGER IF EXISTS users_owner_floor ON users")
    for table in (
        "user_audit_log",
        "login_attempts",
        "auth_tokens",
        "sessions",
        "backup_codes",
        "role_permissions",
        "permission_versions",
        "users",
        "permissions",
        "roles",
    ):
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS role_permissions_owner_column_is_locked()")
    op.execute("DROP FUNCTION IF EXISTS users_owner_never_reaches_zero()")
    op.execute("DROP TYPE IF EXISTS auth_token_kind_t")
    op.execute("DROP TYPE IF EXISTS user_status_t")
