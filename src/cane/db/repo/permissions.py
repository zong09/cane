"""ตารางสิทธิ์ที่มีเวอร์ชัน (spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role)

ตารางนี้เป็น **ข้อมูล** ไม่ใช่ค่าคงที่ในโค้ด · คอนโซลแก้ได้ด้วยการสร้างเวอร์ชันใหม่
แล้วเลื่อนตัวชี้ (ADR 18) ไม่ใช่ `UPDATE` ทับ — ฐานไม่มี `UPDATE` บนเนื้อให้อยู่แล้ว

## สองข้อที่ `allowed()` ตัดสินก่อนแตะตาราง

1. **OWNER ได้ทุกข้อเสมอ** — สเปกระบุว่าข้อบังคับนี้อยู่ในโค้ด · trigger ที่ฐาน
   กันการเขียนข้อมูลที่ขัดกันไว้อีกชั้น แต่ตัวที่ตอบคำถาม "ทำได้ไหม" คือบรรทัดนี้
2. **ไม่มีเวอร์ชัน active = ไม่ได้สักข้อ** — fail closed · spec/09 ระบุว่าค่าเริ่มต้น
   ของ endpoint ที่ยังไม่มีในตารางสิทธิ์คือ 403 ไม่ใช่ "ผ่านเพราะยังไม่ได้ผูกสิทธิ์"
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select, update

from cane.db.schema import permission_versions, permissions, role_permissions, roles

OWNER = "OWNER"


@dataclass(frozen=True, slots=True)
class PermissionVersion:
    id: int
    created_ts: int
    created_by_user_id: int | None
    is_active: bool


def active_version(conn: Connection) -> PermissionVersion | None:
    row = conn.execute(
        select(
            permission_versions.c.id,
            permission_versions.c.created_ts,
            permission_versions.c.created_by_user_id,
            permission_versions.c.is_active,
        ).where(permission_versions.c.is_active.is_(True))
    ).one_or_none()
    return None if row is None else PermissionVersion(**row._mapping)


def lock_active(conn: Connection) -> PermissionVersion | None:
    """เวอร์ชันที่ active พร้อม `FOR UPDATE` — สองคำขอที่บันทึกตารางพร้อมกันต้องต่อคิว
    ไม่งั้นทั้งคู่เห็นฐานเดียวกันแล้วตัวหลังทับของตัวแรกโดยไม่รู้ตัว"""
    row = conn.execute(
        select(
            permission_versions.c.id,
            permission_versions.c.created_ts,
            permission_versions.c.created_by_user_id,
            permission_versions.c.is_active,
        )
        .where(permission_versions.c.is_active.is_(True))
        .with_for_update()
    ).one_or_none()
    return None if row is None else PermissionVersion(**row._mapping)


def allowed(conn: Connection, *, role: str, cap: str) -> bool:
    """สิทธิ์ถูกคิดใหม่ทุกครั้งที่ถาม — นั่นคือเหตุผลที่แก้ role แล้วมีผลทันที"""
    if role == OWNER:
        return True

    version = active_version(conn)
    if version is None:
        return False

    row = conn.execute(
        select(role_permissions.c.allowed)
        .select_from(
            role_permissions.join(roles, role_permissions.c.role_id == roles.c.id).join(
                permissions, role_permissions.c.permission_id == permissions.c.id
            )
        )
        .where(
            role_permissions.c.version_id == version.id,
            roles.c.name == role,
            permissions.c.cap == cap,
        )
    ).one_or_none()
    # ไม่มีแถว = ไม่ได้ · สิทธิ์ใหม่ที่ยังไม่ได้ใส่ในเวอร์ชันที่ใช้อยู่ต้องตกไป
    # ฝั่งปลอดภัยเอง ไม่ใช่ตกไปฝั่งที่เปิดให้ทุก role
    return bool(row is not None and row.allowed)


def insert_version(
    conn: Connection,
    matrix: dict[str, dict[str, bool]],
    *,
    created_ts: int,
    created_by_user_id: int | None = None,
) -> int:
    """`matrix` คือ `{role: {cap: allowed}}` · คืน id ของเวอร์ชันใหม่ (**ยังไม่ active**)

    แยกการบันทึกออกจากการเปิดใช้ด้วยเหตุผลเดียวกับ `repo/config.py` — สองอย่างนี้
    เป็นการกระทำของคนคนละครั้ง และเวอร์ชันที่บันทึกไว้แต่ยังไม่เปิดใช้เป็นสภาพที่มีจริง
    """
    version_id = conn.execute(
        permission_versions.insert()
        .values(created_ts=created_ts, created_by_user_id=created_by_user_id)
        .returning(permission_versions.c.id)
    ).scalar_one()

    role_ids = dict(conn.execute(select(roles.c.name, roles.c.id)).all())
    cap_ids = dict(conn.execute(select(permissions.c.cap, permissions.c.id)).all())

    rows = [
        {
            "version_id": version_id,
            "role_id": role_ids[role],
            "permission_id": cap_ids[cap],
            "allowed": value,
        }
        for role, caps in matrix.items()
        for cap, value in caps.items()
    ]
    if rows:
        conn.execute(role_permissions.insert(), rows)
    return version_id


def activate(conn: Connection, version_id: int) -> None:
    """ปิดของเดิมก่อนเปิดของใหม่ — index `uq_permission_versions_one_active` บังคับ

    ทำสองคำสั่งในทรานแซกชันเดียวของผู้เรียก · สลับลำดับเมื่อไหร่จะชน unique index
    ซึ่งเป็นการล้มที่ถูกต้อง ไม่ใช่สภาพที่มีสองเวอร์ชัน active พร้อมกัน
    """
    conn.execute(
        update(permission_versions)
        .where(permission_versions.c.is_active.is_(True))
        .values(is_active=False)
    )
    conn.execute(
        update(permission_versions)
        .where(permission_versions.c.id == version_id)
        .values(is_active=True)
    )


def matrix_of(conn: Connection, version_id: int) -> dict[str, dict[str, bool]]:
    rows = conn.execute(
        select(roles.c.name, permissions.c.cap, role_permissions.c.allowed)
        .select_from(
            role_permissions.join(roles, role_permissions.c.role_id == roles.c.id).join(
                permissions, role_permissions.c.permission_id == permissions.c.id
            )
        )
        .where(role_permissions.c.version_id == version_id)
    )
    out: dict[str, dict[str, bool]] = {}
    for role, cap, value in rows:
        out.setdefault(role, {})[cap] = value
    return out


def all_caps(conn: Connection) -> list[str]:
    return list(conn.execute(select(permissions.c.cap).order_by(permissions.c.id)).scalars())
