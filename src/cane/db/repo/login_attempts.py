"""ตัวนับของการล็อกบัญชี (spec/09 §การล็อกบัญชี)

**ล็อกที่ระดับบัญชี ไม่ใช่ที่อุปกรณ์หรือ IP** — ตารางนี้จึงนับตามอีเมล และ `ip`
เป็นข้อมูลประกอบสำหรับคนอ่านย้อนหลัง ไม่ใช่ส่วนหนึ่งของกุญแจที่ใช้นับ

## กฎที่เขียนผิดง่ายที่สุดของทั้งใบ

สเปกเขียนว่า "ครบ 15 นาทีแล้วลองใหม่ได้ **โดยตัวนับยังไม่ถูกล้าง** — ผิดอีกครั้งเดียว
ล็อกใหม่" · การนับแบบ "มีกี่ครั้งที่ผิดใน 15 นาทีที่ผ่านมา" **ผิดกฎข้อนี้** เพราะพอ
เวลาผ่านไป ของเก่าหลุดหน้าต่างแล้วตัวนับกลับเป็นศูนย์เอง ซึ่งคือการล้างตัวนับด้วย
การรอเฉยๆ ที่สเปกบอกว่าไม่ใช่

สิ่งที่ล้างตัวนับมีอย่างเดียวคือ **login ที่สำเร็จครบสองขั้น** · การนับจึงเป็น
"ผิดกี่ครั้งติดกันนับจากครั้งที่สำเร็จล่าสุด" แล้วถ้าถึงเพดาน ล็อกจะยืดจาก**ครั้งที่
ผิดล่าสุด** ไปอีก 15 นาที — ซึ่งทำให้ "ผิดอีกครั้งเดียวล็อกใหม่" เกิดขึ้นเอง
โดยไม่ต้องมีเงื่อนไขพิเศษ
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, func, select

from cane.db.repo.users import normalise_email
from cane.db.schema import login_attempts

#: ผิด 5 ครั้งติดกัน → ล็อก 15 นาที
MAX_FAILURES = 5
LOCK_MS = 15 * 60 * 1000


@dataclass(frozen=True, slots=True)
class LockState:
    consecutive_failures: int
    locked_until: int | None

    def is_locked(self, now: int) -> bool:
        return self.locked_until is not None and now < self.locked_until


#: ขั้นของ login ที่แถวนี้พูดถึง · `unlock` คือการล้างตัวนับด้วยมือ ไม่ใช่การ login
KINDS = ("password", "totp", "backup_code", "unlock")


def record(
    conn: Connection,
    *,
    email: str,
    ok: bool,
    kind: str,
    ts: int,
    user_id: int | None = None,
    ip: str | None = None,
) -> None:
    """บันทึกทุกครั้ง ทั้งสำเร็จและไม่สำเร็จ

    อีเมลที่ไม่มีบัญชีก็ถูกบันทึก (`user_id` เป็น `None`) — ถ้าไม่นับ หน้าจอที่
    "ไม่เคยล็อก" จะกลายเป็นคำตอบว่าอีเมลนี้ไม่มีอยู่ ซึ่งเป็นสิ่งเดียวกับที่
    spec/09 §5. login สองขั้น สั่งไม่ให้บอก
    """
    conn.execute(
        login_attempts.insert().values(
            email=normalise_email(email),
            user_id=user_id,
            ok=ok,
            kind=kind,
            ip=ip,
            ts=ts,
        )
    )


def lock_state(conn: Connection, email: str) -> LockState:
    """ผิดติดกันกี่ครั้งนับจากความสำเร็จล่าสุด และล็อกอยู่ถึงเมื่อไหร่"""
    target = normalise_email(email)

    last_ok = conn.execute(
        select(func.max(login_attempts.c.ts)).where(
            login_attempts.c.email == target, login_attempts.c.ok.is_(True)
        )
    ).scalar_one()
    since = last_ok if last_ok is not None else -1

    row = conn.execute(
        select(
            func.count().label("failures"),
            func.max(login_attempts.c.ts).label("latest"),
        ).where(
            login_attempts.c.email == target,
            login_attempts.c.ok.is_(False),
            login_attempts.c.ts > since,
        )
    ).one()

    failures = row.failures
    if failures < MAX_FAILURES:
        return LockState(consecutive_failures=failures, locked_until=None)
    return LockState(consecutive_failures=failures, locked_until=row.latest + LOCK_MS)


def clear(conn: Connection, *, email: str, ts: int, user_id: int | None = None) -> None:
    """ปลดล็อกด้วยมือ (`manage_users`) — เขียนแถว `unlock` ไม่ใช่ลบของเก่า

    ตารางนี้ไม่มี `DELETE` ให้ใครอยู่แล้ว (ADR 23) และการปลดล็อกคือเหตุการณ์ที่ควร
    อ่านย้อนหลังได้ · `kind='unlock'` แยกมันออกจากการ login สำเร็จ ทั้งที่ทั้งสอง
    อย่างล้างตัวนับเหมือนกัน — ประวัติที่อ่านว่าคนคนนั้นเข้ามาเองตอนนั้นคือประวัติที่ผิด
    """
    record(conn, email=email, ok=True, kind="unlock", ts=ts, user_id=user_id)
