"""backup code (spec/09 §backup code)

10 รหัส สร้างพร้อมกันตอนผูก TOTP · แสดงครั้งเดียว · เก็บเป็น hash · ใช้ได้ครั้งละ
หนึ่งรหัสแล้วรหัสนั้นตายถาวร · **สร้างชุดใหม่ฆ่าชุดเก่าทั้งชุด** ไม่ใช่เติมของที่ใช้ไป

`consume()` ต้องลอง hash ทีละใบเพราะ argon2 มี salt ต่อแถว — เทียบตรงๆ ไม่ได้
ราคาคือ argon2 สิบครั้งต่อการใช้ backup code หนึ่งครั้ง ซึ่งเกิดขึ้นนานๆ ที
"""

from __future__ import annotations

from sqlalchemy import Connection, func, select, update

from cane.auth.secrets import hash_password, verify_password
from cane.auth.totp import normalise_backup_code
from cane.db.schema import backup_codes


def replace_set(conn: Connection, *, user_id: int, codes: list[str], now: int) -> None:
    """ฆ่าชุดเดิมทั้งชุดแล้วเขียนชุดใหม่ · เก็บเฉพาะ hash"""
    conn.execute(
        update(backup_codes)
        .where(
            backup_codes.c.user_id == user_id,
            backup_codes.c.used_ts.is_(None),
            backup_codes.c.retired_ts.is_(None),
        )
        .values(retired_ts=now)
    )
    conn.execute(
        backup_codes.insert(),
        [
            {
                "user_id": user_id,
                "code_hash": hash_password(normalise_backup_code(code)),
                "created_ts": now,
            }
            for code in codes
        ],
    )


def consume(conn: Connection, *, user_id: int, code: str, now: int) -> bool:
    """ใช้รหัสหนึ่งใบ · คืน `False` เมื่อไม่ตรงกับใบไหนที่ยังมีชีวิต

    เขียน `used_ts` แบบมีเงื่อนไข `used_ts IS NULL` ด้วยเหตุผลเดียวกับ `auth_tokens`
    — คำขอสองอันที่มาพร้อมกันด้วยรหัสเดียวกันต้องผ่านได้แค่อันเดียว
    """
    typed = normalise_backup_code(code)
    rows = conn.execute(
        select(backup_codes.c.id, backup_codes.c.code_hash).where(
            backup_codes.c.user_id == user_id,
            backup_codes.c.used_ts.is_(None),
            backup_codes.c.retired_ts.is_(None),
        )
    ).all()

    for row in rows:
        if verify_password(row.code_hash, typed):
            marked = conn.execute(
                update(backup_codes)
                .where(backup_codes.c.id == row.id, backup_codes.c.used_ts.is_(None))
                .values(used_ts=now)
            )
            return marked.rowcount == 1
    return False


def remaining(conn: Connection, user_id: int) -> int:
    """จำนวนที่เหลือ — หน้าจอแสดงให้เห็น (design: `เหลือ 2 ครั้ง`)"""
    return conn.execute(
        select(func.count()).where(
            backup_codes.c.user_id == user_id,
            backup_codes.c.used_ts.is_(None),
            backup_codes.c.retired_ts.is_(None),
        )
    ).scalar_one()
