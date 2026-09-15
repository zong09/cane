"""ลิงก์และตั๋วที่ใช้ได้ครั้งเดียว (spec/09 §7. คำเชิญ · reset 2FA · รหัสผ่านที่ลืม)

สี่ชนิดเป็น **กลไกเดียวกัน** ต่างกันแค่ว่าปลดล็อกให้ทำอะไรและอยู่ได้นานแค่ไหน:

| `kind` | อายุ | ปลดล็อกให้ทำอะไร |
| --- | --- | --- |
| `login` | 5 นาที | ผ่านไปขั้นที่ 2 ของ login — **ไม่ใช่ session** |
| `invite` | 72 ชั่วโมง | ตั้งรหัสผ่าน + ผูก TOTP |
| `reset_2fa` | 72 ชั่วโมง | ผูก TOTP ใหม่ |
| `reset_password` | 72 ชั่วโมง | ตั้งรหัสผ่านใหม่ |

เก็บเป็น **hash ของ token** เหมือน session · **การออกใหม่ฆ่าของเดิม** ซึ่งเขียนไว้ที่
`issue()` ไม่ใช่ให้ผู้เรียกจำ — ลิงก์เก่าที่ยังใช้ได้หลังออกลิงก์ใหม่คือช่องที่คนส่ง
ลิงก์ผิดคนแล้วแก้ไม่ได้
"""

from __future__ import annotations

from sqlalchemy import Connection, select, update

from cane.auth.secrets import token_hash
from cane.db.schema import auth_tokens

MINUTE_MS = 60 * 1000
HOUR_MS = 60 * MINUTE_MS

#: ตั๋วขั้นที่ 1 สั้นมากโดยเจตนา — มันไม่ใช่สิ่งที่คนพกไปทำอย่างอื่น
LOGIN_TTL_MS = 5 * MINUTE_MS
LINK_TTL_MS = 72 * HOUR_MS

TTL = {
    "login": LOGIN_TTL_MS,
    "invite": LINK_TTL_MS,
    "reset_2fa": LINK_TTL_MS,
    "reset_password": LINK_TTL_MS,
}


def issue(conn: Connection, *, user_id: int, kind: str, token: str, now: int) -> None:
    """ฆ่าของเดิมชนิดเดียวกันของคนนี้ก่อน แล้วค่อยออกใบใหม่"""
    conn.execute(
        update(auth_tokens)
        .where(
            auth_tokens.c.user_id == user_id,
            auth_tokens.c.kind == kind,
            auth_tokens.c.used_ts.is_(None),
            auth_tokens.c.retired_ts.is_(None),
        )
        .values(retired_ts=now)
    )
    conn.execute(
        auth_tokens.insert().values(
            user_id=user_id,
            kind=kind,
            token_hash=token_hash(token),
            created_ts=now,
            expires_ts=now + TTL[kind],
        )
    )


def consume(conn: Connection, *, token: str, kind: str, now: int) -> int | None:
    """คืน `user_id` เมื่อใช้ได้ แล้ว**ทำเครื่องหมายว่าใช้แล้วในคำสั่งเดียวกัน**

    เขียน `used_ts` ด้วย `UPDATE ... WHERE used_ts IS NULL` แล้วดูว่ามีแถวโดนไหม
    ไม่ใช่ "อ่านก่อนแล้วค่อยเขียน" — สองคำขอที่มาพร้อมกันด้วยตั๋วใบเดียวกันจะผ่าน
    ทั้งคู่ถ้าอ่านก่อนเขียน ซึ่งทำให้คำว่า "ใช้ได้ครั้งเดียว" ไม่จริงตอนที่มีคนตั้งใจ
    """
    row = conn.execute(
        update(auth_tokens)
        .where(
            auth_tokens.c.token_hash == token_hash(token),
            auth_tokens.c.kind == kind,
            auth_tokens.c.used_ts.is_(None),
            auth_tokens.c.retired_ts.is_(None),
            auth_tokens.c.expires_ts > now,
        )
        .values(used_ts=now)
        .returning(auth_tokens.c.user_id)
    ).one_or_none()
    return None if row is None else row.user_id


def retire_all(conn: Connection, *, user_id: int, kind: str, now: int) -> None:
    conn.execute(
        update(auth_tokens)
        .where(
            auth_tokens.c.user_id == user_id,
            auth_tokens.c.kind == kind,
            auth_tokens.c.used_ts.is_(None),
            auth_tokens.c.retired_ts.is_(None),
        )
        .values(retired_ts=now)
    )


def live_kinds(conn: Connection, *, user_id: int, now: int) -> list[str]:
    """ลิงก์ที่ยังใช้ได้ของคนนี้ — หน้าจอผู้ใช้แสดงว่ามีคำเชิญค้างอยู่ไหม"""
    return list(
        conn.execute(
            select(auth_tokens.c.kind).where(
                auth_tokens.c.user_id == user_id,
                auth_tokens.c.used_ts.is_(None),
                auth_tokens.c.retired_ts.is_(None),
                auth_tokens.c.expires_ts > now,
            )
        ).scalars()
    )
