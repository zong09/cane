"""บัญชีผู้ใช้ (spec/09 §2. บัญชี role และสถานะ)

`role` ถูกอ่านมาเป็น **ชื่อ** ไม่ใช่ `role_id` ตั้งแต่ชั้นนี้ เพราะทุกคนที่อยู่เหนือ
ขึ้นไปสนใจคำว่า `OWNER` ไม่ใช่เลข 1 · เลขอยู่ในฐานเพื่อ FK เท่านั้น

`email` ถูกทำเป็นตัวพิมพ์เล็กที่นี่ที่เดียว ก่อนแตะฐาน — ฐานมี CHECK กันไว้อีกชั้น
ถ้าเส้นทางไหนลืม มันจะล้มดังตอนเขียน ไม่ใช่กลายเป็นบัญชีคู่แฝดที่ login สลับกันได้
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, func, select, update

from cane.db.schema import roles, users


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    name: str
    role: str
    status: str
    password_hash: str | None
    totp_secret_enc: str | None
    totp_enrolled_ts: int | None
    totp_last_counter: int | None
    last_login_ts: int | None

    @property
    def can_sign_in(self) -> bool:
        """`active` เท่านั้น · `pending` กับ `suspended` เข้าไม่ได้เลย

        ฐานมี CHECK ที่ทำให้ `active` แปลว่าตั้งรหัสผ่านและผูก TOTP ครบแล้วเสมอ
        ตรงนี้จึงไม่ต้องตรวจซ้ำ — ถ้าตรวจซ้ำแล้ววันหนึ่งสองที่ไม่ตรงกัน จะไม่มีใคร
        รู้ว่าอันไหนคือกฎจริง
        """
        return self.status == "active"


def normalise_email(email: str) -> str:
    return email.strip().lower()


_COLUMNS = (
    users.c.id,
    users.c.email,
    users.c.name,
    roles.c.name.label("role"),
    users.c.status,
    users.c.password_hash,
    users.c.totp_secret_enc,
    users.c.totp_enrolled_ts,
    users.c.totp_last_counter,
    users.c.last_login_ts,
)

_JOINED = select(*_COLUMNS).select_from(users.join(roles, users.c.role_id == roles.c.id))


def from_mapping(mapping: Mapping[str, object]) -> User:
    """ประกอบ `User` จาก mapping ของคอลัมน์ตาม `_COLUMNS`

    เปิดให้โมดูลอื่นเรียกได้เพราะ `repo/sessions.py` อ่าน user มาใน query เดียวกับ
    session (spec/09 §6. session สั่งให้ตรวจสถานะทุก request — สอง query ต่อ request
    คือสองภาพที่ต่างเวลากัน)
    """
    return User(**{key: mapping[key] for key in User.__slots__})


def by_email(conn: Connection, email: str) -> User | None:
    row = conn.execute(
        _JOINED.where(users.c.email == normalise_email(email))
    ).one_or_none()
    return None if row is None else from_mapping(row._mapping)


def by_id(conn: Connection, user_id: int) -> User | None:
    row = conn.execute(_JOINED.where(users.c.id == user_id)).one_or_none()
    return None if row is None else from_mapping(row._mapping)


def lock_by_ids(conn: Connection, *user_ids: int) -> dict[int, User]:
    """อ่านใหม่พร้อม `FOR UPDATE` ในทรานแซกชันของผู้เรียก — ด่านที่ต้องตรวจซ้ำตอนเขียน

    ล็อกเรียงตาม `id` เสมอ · สองคำขอที่ล็อกคนละลำดับคือ deadlock
    """
    rows = conn.execute(
        _JOINED.where(users.c.id.in_(user_ids)).order_by(users.c.id).with_for_update(of=users)
    )
    return {row.id: from_mapping(row._mapping) for row in rows}


def create(
    conn: Connection,
    *,
    email: str,
    name: str,
    role: str,
    created_ts: int,
    password_hash: str | None = None,
) -> int:
    """สร้างบัญชี `pending` เสมอ — ไม่มีเส้นทางไหนสร้างบัญชีที่เข้าได้เลยทันที

    spec/09 §2. บัญชี role และสถานะ บอกว่า OWNER คนแรกก็ยังต้องผูก TOTP ผ่านคอนโซล
    เหมือนคนอื่น · ถ้าที่นี่รับ `status` เป็นอาร์กิวเมนต์ได้ CLI จะกลายเป็นทางลัด
    ที่ข้าม 2FA ซึ่งเป็นช่องเดียวที่ทั้งหน้าสเปกมีไว้ปิด
    """
    role_id = conn.execute(select(roles.c.id).where(roles.c.name == role)).scalar_one()
    return conn.execute(
        users.insert()
        .values(
            email=normalise_email(email),
            name=name,
            role_id=role_id,
            status="pending",
            password_hash=password_hash,
            created_ts=created_ts,
        )
        .returning(users.c.id)
    ).scalar_one()


def set_password(conn: Connection, user_id: int, password_hash: str) -> None:
    conn.execute(
        update(users).where(users.c.id == user_id).values(password_hash=password_hash)
    )


def enrol_totp(
    conn: Connection, user_id: int, *, secret_enc: str, enrolled_ts: int
) -> None:
    """ผูก TOTP แล้วเปิดใช้บัญชี — สองอย่างนี้เป็นการเขียนครั้งเดียวโดยเจตนา

    ฐานปฏิเสธ `active` ที่ยังไม่มี `totp_enrolled_ts` อยู่แล้ว การแยกเป็นสองคำสั่ง
    จึงได้แค่สภาพกลางที่เขียนไม่ลงอยู่ดี
    """
    conn.execute(
        update(users)
        .where(users.c.id == user_id)
        .values(
            totp_secret_enc=secret_enc,
            totp_enrolled_ts=enrolled_ts,
            totp_last_counter=None,
            status="active",
        )
    )


def clear_totp(conn: Connection, user_id: int) -> None:
    """reset 2FA — บัญชีกลับเป็น `pending` (spec/09)

    ไม่มีสถานะพิเศษสำหรับ "รอผูก 2FA ใหม่" เพราะ `pending` แปลว่าแบบนั้นอยู่แล้ว
    """
    conn.execute(
        update(users)
        .where(users.c.id == user_id)
        .values(
            status="pending",
            totp_secret_enc=None,
            totp_enrolled_ts=None,
            totp_last_counter=None,
        )
    )


def set_totp_counter(conn: Connection, user_id: int, counter: int) -> None:
    """**ต้องเรียกทุกครั้งที่ TOTP ผ่าน** ไม่งั้นรหัสเดิมใช้ซ้ำได้ (spec/09 §TOTP)"""
    conn.execute(
        update(users).where(users.c.id == user_id).values(totp_last_counter=counter)
    )


def set_status(conn: Connection, user_id: int, status: str) -> None:
    """trigger `users_owner_floor` ปฏิเสธการระงับ OWNER ที่ใช้งานอยู่คนสุดท้าย

    ตรงนี้จึงไม่ตรวจซ้ำ — ข้อบังคับอยู่ที่ฐาน เส้นทางไหนที่ลืมตรวจจะล้มดัง
    """
    conn.execute(update(users).where(users.c.id == user_id).values(status=status))


def set_role(conn: Connection, user_id: int, role: str) -> None:
    role_id = conn.execute(select(roles.c.id).where(roles.c.name == role)).scalar_one()
    conn.execute(update(users).where(users.c.id == user_id).values(role_id=role_id))


def touch_login(conn: Connection, user_id: int, ts: int) -> None:
    conn.execute(update(users).where(users.c.id == user_id).values(last_login_ts=ts))


def count_active_owners(conn: Connection) -> int:
    """สำหรับหน้าจอที่อยากเตือนก่อนกด — **ไม่ใช่** ด่าน ด่านคือ trigger ที่ฐาน"""
    return conn.execute(
        select(func.count())
        .select_from(users.join(roles, users.c.role_id == roles.c.id))
        .where(roles.c.name == "OWNER", users.c.status == "active")
    ).scalar_one()


def everyone(conn: Connection) -> list[User]:
    return [from_mapping(row._mapping) for row in conn.execute(_JOINED.order_by(users.c.id))]
