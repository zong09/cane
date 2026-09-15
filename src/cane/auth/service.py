"""การตัดสินของ auth — login สองขั้น การล็อก และ step-up (spec/09 §5. login สองขั้น)

ชั้นนี้ไม่รู้จัก HTTP · มันรับ `conn` กับข้อมูลที่คนกรอกมา แล้วคืนผลลัพธ์ที่ route
เอาไปแปลงเป็นหน้าจอ — เขียนแบบนี้เพื่อให้เกณฑ์ยืนยันความถูกต้องของ spec/09 ทดสอบได้โดยไม่ต้อง
มีเบราเซอร์ และเพื่อให้เส้นทางที่ตัดสินใจมีอยู่เส้นเดียว

## ทุกความล้มเหลวหน้าตาเหมือนกัน

รหัสผ่านผิด · อีเมลไม่มีในระบบ · บัญชีถูกระงับ · บัญชียังไม่ผูก TOTP — ทั้งสี่อย่าง
คืน `Failure` ตัวเดียวกัน ส่วนที่ต่างกันไปอยู่ใน audit log · การบอกว่า "ไม่มีบัญชีนี้"
คือการยืนยันรายชื่อผู้ใช้ให้คนที่เดาอีเมล

**อีเมลที่ไม่มีบัญชียังต้องเสียเวลาเท่ากับอีเมลที่มี** — `verify_password()` ถูกเรียก
กับ hash หลอกเมื่อไม่เจอบัญชี ไม่งั้นเวลาที่ตอบกลับจะบอกเองว่าอีเมลไหนมีอยู่จริง
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection

from cane.auth import secrets as auth_secrets
from cane.auth import totp
from cane.db.repo import audit, auth_tokens, backup_codes, login_attempts
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.repo.users import User

#: hash ของรหัสผ่านที่ไม่มีใครรู้ · มีไว้ให้เวลาที่ใช้ตอบเท่ากันไม่ว่าบัญชีจะมีอยู่ไหม
_DECOY_HASH = auth_secrets.hash_password(auth_secrets.new_token())


@dataclass(frozen=True, slots=True)
class Failure:
    """ล้มเหลว · `locked_until` มีค่าเมื่อบัญชีถูกล็อก (หน้าจอบอกเวลาที่เหลือได้)

    ไม่มีฟิลด์บอกว่าล้มเพราะอะไร **โดยเจตนา** — ถ้ามี วันหนึ่งจะมีคนเอาไปแสดง
    """

    locked_until: int | None = None


@dataclass(frozen=True, slots=True)
class Ticket:
    """ผ่านขั้นที่ 1 แล้ว · **ยังไม่มีสิทธิ์อะไรเลย** อายุ 5 นาที ใช้ได้ครั้งเดียว"""

    token: str


@dataclass(frozen=True, slots=True)
class SignedIn:
    token: str
    session_id: int
    user: User


def begin_login(
    conn: Connection,
    *,
    email: str,
    password: str,
    now: int,
    ip: str | None = None,
) -> Ticket | Failure:
    """ขั้นที่ 1 — อีเมล + รหัสผ่าน · ผ่านแล้วได้ **ตั๋ว ไม่ใช่ session**

    ความสำเร็จของขั้นนี้ **ไม่ล้างตัวนับการล็อก** เพราะสเปกบอกว่ามีแต่ login ที่
    สำเร็จครบสองขั้นเท่านั้นที่ล้าง · ถ้าล้างที่นี่ คนที่รู้รหัสผ่านแต่ไม่มีอุปกรณ์
    2FA จะยิงขั้นที่ 2 ได้ไม่จำกัดครั้ง
    """
    state = login_attempts.lock_state(conn, email)
    if state.is_locked(now):
        # ไม่บันทึกครั้งนี้เป็น "ผิด" — ไม่งั้นการยิงรัวจะยืดล็อกออกไปเรื่อยๆ
        # ซึ่งลงโทษเจ้าของบัญชีตัวจริง ไม่ใช่คนที่ยิง · แต่ยัง**ลง audit** เพราะ
        # spec/09 §8. audit log ของผู้ใช้ สั่งให้การล็อกอ่านย้อนหลังได้ และคนที่ถูก
        # ยิงรัวจนล็อกต้องเห็นได้ว่ามีคนพยายามต่อระหว่างที่ล็อกอยู่
        audit.record(
            conn,
            action="login.refused_locked",
            ts=now,
            target=users_repo.normalise_email(email),
            detail={"locked_until": state.locked_until},
            ip=ip,
        )
        return Failure(locked_until=state.locked_until)

    user = users_repo.by_email(conn, email)
    stored = user.password_hash if user and user.password_hash else _DECOY_HASH
    password_ok = auth_secrets.verify_password(stored, password)

    if user is None or not password_ok or not user.can_sign_in:
        login_attempts.record(
            conn,
            email=email,
            ok=False,
            kind="password",
            ts=now,
            user_id=user.id if user else None,
            ip=ip,
        )
        audit.record(
            conn,
            action="login.stage1_failed",
            ts=now,
            actor_user_id=user.id if user else None,
            target=users_repo.normalise_email(email),
            detail={"reason": _reason(user, password_ok)},
            ip=ip,
        )
        return Failure(locked_until=login_attempts.lock_state(conn, email).locked_until)

    token = auth_secrets.new_token()
    auth_tokens.issue(conn, user_id=user.id, kind="login", token=token, now=now)
    return Ticket(token=token)


def _reason(user: User | None, password_ok: bool) -> str:
    """เหตุผลลง audit log เท่านั้น · **ห้ามไปโผล่บนหน้าจอ**"""
    if user is None:
        return "ไม่มีบัญชีนี้"
    if not password_ok:
        return "รหัสผ่านผิด"
    return f"บัญชีอยู่ในสถานะ {user.status}"


def complete_login(
    conn: Connection,
    *,
    ticket: str,
    code: str,
    now: int,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SignedIn | Failure:
    """ขั้นที่ 2 — TOTP 6 หลัก หรือ backup code · ผ่านแล้วถึงจะมี session

    backup code ใช้ได้ที่นี่เท่านั้น **ไม่ใช้กับ step-up** (spec/09 §backup code) —
    step-up มีไว้ยืนยันว่าคนที่นั่งอยู่ถืออุปกรณ์ 2FA อยู่จริงในตอนนั้น รหัสที่พิมพ์
    เก็บไว้ในกระเป๋าตอบคำถามนั้นไม่ได้
    """
    user_id = auth_tokens.consume(conn, token=ticket, kind="login", now=now)
    if user_id is None:
        return Failure()

    user = users_repo.by_id(conn, user_id)
    if user is None or not user.can_sign_in or not user.totp_secret_enc:
        return Failure()

    state = login_attempts.lock_state(conn, user.email)
    if state.is_locked(now):
        return Failure(locked_until=state.locked_until)

    kind = _second_factor(conn, user, code, now=now)
    if kind is None:
        login_attempts.record(
            conn, email=user.email, ok=False, kind="totp", ts=now, user_id=user.id, ip=ip
        )
        audit.record(
            conn,
            action="login.stage2_failed",
            ts=now,
            actor_user_id=user.id,
            target=user.email,
            ip=ip,
        )
        return Failure(locked_until=login_attempts.lock_state(conn, user.email).locked_until)

    login_attempts.record(
        conn, email=user.email, ok=True, kind=kind, ts=now, user_id=user.id, ip=ip
    )
    users_repo.touch_login(conn, user.id, now)

    token = auth_secrets.new_token()
    session_id = sessions_repo.create(
        conn, user_id=user.id, token=token, now=now, ip=ip, user_agent=user_agent
    )
    audit.record(
        conn,
        action="login.ok",
        ts=now,
        actor_user_id=user.id,
        target=user.email,
        detail={"second_factor": kind},
        ip=ip,
    )
    return SignedIn(token=token, session_id=session_id, user=users_repo.by_id(conn, user.id))


def _second_factor(conn: Connection, user: User, code: str, *, now: int) -> str | None:
    """คืนชนิดของปัจจัยที่สองที่ผ่าน หรือ `None` · ลอง TOTP ก่อนเพราะเป็นทางปกติ"""
    secret = auth_secrets.decrypt_secret(user.totp_secret_enc)
    counter = totp.verify(secret, code, now=now, last_counter=user.totp_last_counter)
    if counter is not None:
        users_repo.set_totp_counter(conn, user.id, counter)
        return "totp"

    if backup_codes.consume(conn, user_id=user.id, code=code, now=now):
        return "backup_code"
    return None


def verify_step_up(conn: Connection, user: User, code: str, *, now: int) -> bool:
    """ยืนยันซ้ำก่อนลงมือ · **ขอทุกครั้ง ไม่มีช่วงผ่อนผัน** (spec/09 §step-up TOTP)

    TOTP เท่านั้น — backup code ใช้ที่นี่ไม่ได้ · และ counter ถูกเขียนทับเหมือนตอน
    login ซึ่งแปลว่ารหัสที่เพิ่งใช้ step-up ไปจะใช้ login ซ้ำในช่วงเดิมไม่ได้ด้วย
    """
    if not user.totp_secret_enc:
        return False
    secret = auth_secrets.decrypt_secret(user.totp_secret_enc)
    counter = totp.verify(secret, code, now=now, last_counter=user.totp_last_counter)
    if counter is None:
        return False
    users_repo.set_totp_counter(conn, user.id, counter)
    return True


def sign_out(conn: Connection, *, session_id: int, user_id: int, now: int) -> None:
    sessions_repo.revoke(conn, session_id, now)
    audit.record(conn, action="logout", ts=now, actor_user_id=user_id)


def enrol(
    conn: Connection, *, user: User, password: str | None, now: int
) -> tuple[str, list[str]]:
    """ผูก TOTP ครั้งแรก · คืน (secret ที่ยังไม่เข้ารหัส, backup code 10 ใบ)

    **ยังไม่เปิดใช้บัญชี** — ผู้เรียกต้องให้คนกรอกรหัสจากแอปกลับมาก่อนแล้วค่อยเรียก
    `confirm_enrolment()` · ถ้าเปิดใช้ตั้งแต่ตรงนี้ บัญชีจะ active ทั้งที่ยังไม่มี
    ใครพิสูจน์ว่าสแกน QR ติด ซึ่งเป็นบัญชีที่เจ้าของเข้าไม่ได้และปลดเองไม่ได้
    """
    if password is not None:
        users_repo.set_password(conn, user.id, auth_secrets.hash_password(password))
    return totp.new_secret(), totp.new_backup_codes()


def confirm_enrolment(
    conn: Connection, *, user: User, secret: str, codes: list[str], code: str, now: int
) -> bool:
    """รับได้ก็ต่อเมื่อรหัสจากแอปตรง — นี่คือหลักฐานว่าสแกน QR ติดจริง"""
    if totp.verify(secret, code, now=now) is None:
        return False
    users_repo.enrol_totp(
        conn,
        user.id,
        secret_enc=auth_secrets.encrypt_secret(secret),
        enrolled_ts=now,
    )
    backup_codes.replace_set(conn, user_id=user.id, codes=codes, now=now)
    audit.record(conn, action="totp.enrolled", ts=now, actor_user_id=user.id)
    return True
