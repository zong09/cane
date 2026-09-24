"""session ของคอนโซล (spec/09 §6. session)

**ตารางนี้คือเหตุผลที่ข้อบังคับสามข้อของสเปกเป็นจริงได้** — ตัด session แล้วมีผลที่
request ถัดไป · ระงับผู้ใช้แล้วตัดทันที · เปลี่ยน role แล้วมีผลกับ session ที่เปิดอยู่
ทั้งสามข้อเป็นจริงเพราะ `lookup()` อ่านสถานะจากฐาน **ทุก request** ไม่ใช่เพราะมีใคร
ไปไล่ลบคุกกี้

`lookup()` จึงคืน `User` มาด้วย ไม่ใช่แค่แถวของ session — ผู้เรียกที่ได้แต่ session
จะต้องไปอ่าน user เองอีกที ซึ่งเป็นจังหวะที่เส้นทางใดเส้นทางหนึ่งจะลืม
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, select, update

from cane.auth.secrets import token_hash
from cane.db.repo.users import User, from_mapping
from cane.db.schema import roles, sessions, users

#: 12 ชั่วโมงตาม spec/09 §6. session
LIFETIME_MS = 12 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    user_id: int
    ip: str | None
    user_agent: str | None
    mode: str
    created_ts: int
    last_seen_ts: int
    expires_ts: int
    revoked_ts: int | None


def create(
    conn: Connection,
    *,
    user_id: int,
    token: str,
    now: int,
    ip: str | None = None,
    user_agent: str | None = None,
    mode: str = "paper",
) -> int:
    """เก็บ **hash ของ token** · ตัว token อยู่ในคุกกี้ของคนคนนั้นเท่านั้น"""
    return conn.execute(
        sessions.insert()
        .values(
            user_id=user_id,
            token_hash=token_hash(token),
            ip=ip,
            user_agent=user_agent,
            mode=mode,
            created_ts=now,
            last_seen_ts=now,
            expires_ts=now + LIFETIME_MS,
        )
        .returning(sessions.c.id)
    ).scalar_one()


_SESSION_COLUMNS = (
    sessions.c.id,
    sessions.c.user_id,
    sessions.c.ip,
    sessions.c.user_agent,
    sessions.c.mode,
    sessions.c.created_ts,
    sessions.c.last_seen_ts,
    sessions.c.expires_ts,
    sessions.c.revoked_ts,
)


def lookup(conn: Connection, token: str, *, now: int) -> tuple[Session, User] | None:
    """คืน `None` เมื่อ**อะไรก็ตาม**ไม่เข้าเงื่อนไข — หมดอายุ ถูกตัด หรือเจ้าของถูกระงับ

    รวมสามเหตุไว้เป็นคำตอบเดียวโดยเจตนา · ผู้เรียกทำอย่างเดียวกันหมดคือเด้งออก
    และการแยกเหตุให้หน้าจอเห็นคือการบอกคนที่ถือ token เก่าว่าบัญชีนั้นยังมีอยู่ไหม
    """
    joined = (
        select(*_SESSION_COLUMNS, *_user_columns())
        .select_from(
            sessions.join(users, sessions.c.user_id == users.c.id).join(
                roles, users.c.role_id == roles.c.id
            )
        )
        .where(sessions.c.token_hash == token_hash(token))
    )
    row = conn.execute(joined).one_or_none()
    if row is None:
        return None

    mapping = dict(row._mapping)
    session = Session(**{key: mapping[key] for key in Session.__slots__})
    if session.revoked_ts is not None or session.expires_ts <= now:
        return None

    user = from_mapping({key: mapping[f"u_{key}"] for key in User.__slots__})
    if not user.can_sign_in:
        return None
    return session, user


def _user_columns():
    """คอลัมน์ของ user ใน query เดียวกัน · เติม `u_` กันชนกับคอลัมน์ของ session

    `sessions.id` กับ `users.id` ชื่อเดียวกัน — ไม่เติมคำนำหน้าแล้วอันหนึ่งจะทับอีกอัน
    ใน `_mapping` โดยไม่มี error และจะได้ `Session.id` ที่เป็น id ของ user
    """
    return (
        users.c.id.label("u_id"),
        users.c.email.label("u_email"),
        users.c.name.label("u_name"),
        roles.c.name.label("u_role"),
        users.c.status.label("u_status"),
        users.c.password_hash.label("u_password_hash"),
        users.c.totp_secret_enc.label("u_totp_secret_enc"),
        users.c.totp_enrolled_ts.label("u_totp_enrolled_ts"),
        users.c.totp_last_counter.label("u_totp_last_counter"),
        users.c.last_login_ts.label("u_last_login_ts"),
    )


def touch(conn: Connection, session_id: int, now: int) -> None:
    conn.execute(
        update(sessions).where(sessions.c.id == session_id).values(last_seen_ts=now)
    )


def set_mode(conn: Connection, session_id: int, mode: str) -> None:
    """โหมดคือมุมมองของ session (spec/10 §3. สลับโหมดไม่ใช่การควบคุม)

    อยู่ในตารางนี้ไม่ใช่ในคุกกี้ เพราะ spec/09 §6. session ห้ามเชื่อค่าที่ฝั่งผู้ใช้
    ตั้งเองได้ · ค่าที่ตั้งเองได้แปลว่าใครก็แก้คุกกี้เป็น `live` ได้โดยไม่ผ่าน step-up
    """
    conn.execute(update(sessions).where(sessions.c.id == session_id).values(mode=mode))


def revoke(conn: Connection, session_id: int, now: int) -> None:
    conn.execute(
        update(sessions)
        .where(sessions.c.id == session_id, sessions.c.revoked_ts.is_(None))
        .values(revoked_ts=now)
    )


def revoke_all_for_user(conn: Connection, user_id: int, now: int) -> int:
    """ระงับผู้ใช้ = ตัด session ทันที (spec/09 §6. session) · คืนจำนวนที่ตัดไป"""
    result = conn.execute(
        update(sessions)
        .where(sessions.c.user_id == user_id, sessions.c.revoked_ts.is_(None))
        .values(revoked_ts=now)
    )
    return result.rowcount


def live_all(conn: Connection, *, now: int) -> list[Session]:
    """session ที่ยังใช้ได้ของทุกคน — แท็บ `session ที่เปิดอยู่` ของหน้าผู้ใช้ · ใหม่ก่อนเก่า"""
    rows = conn.execute(
        select(*_SESSION_COLUMNS)
        .where(sessions.c.revoked_ts.is_(None), sessions.c.expires_ts > now)
        .order_by(sessions.c.created_ts.desc(), sessions.c.id.desc())
    )
    return [Session(**row._mapping) for row in rows]


def live_for_user(conn: Connection, user_id: int, *, now: int) -> list[Session]:
    rows = conn.execute(
        select(*_SESSION_COLUMNS)
        .where(
            sessions.c.user_id == user_id,
            sessions.c.revoked_ts.is_(None),
            sessions.c.expires_ts > now,
        )
        .order_by(sessions.c.last_seen_ts.desc())
    )
    return [Session(**row._mapping) for row in rows]
