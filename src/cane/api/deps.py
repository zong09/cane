"""ตะเข็บของคอนโซล — session สิทธิ์ และ step-up (spec/09)

ใบ 19 ทิ้งสามจุดนี้ไว้เป็น stub · ใบ 20 เติมของจริงลงไปโดยที่เทมเพลตไม่ต้องแก้สักไฟล์
ซึ่งเป็นเหตุผลที่มันถูกมัดไว้เป็นฟังก์ชันเดียวต่อหนึ่งเรื่องตั้งแต่แรก

`get_db()` คืน `Engine` **ไม่ใช่ `Connection`** โดยเจตนา · dependency ที่ yield จาก
`engine.begin()` จะห่อ body ของ handler ไว้ในทรานแซกชันทั้งก้อน ซึ่งพา `launch()`
เข้าไปอยู่ข้างในด้วย — กับดักที่ `engine/supervisor.py` เขียนเตือนไว้

## ทุก request อ่านสถานะใหม่ ไม่มีอะไรถูกแคชไว้ในคุกกี้

คุกกี้มีแค่ token ที่ไม่มีความหมายในตัว · role สถานะบัญชี และโหมดที่กำลังดู
อ่านจากฐานทุกครั้ง (spec/09 §6. session) — นั่นคือเหตุผลที่ตัด session แล้วมีผลที่
request ถัดไป ระงับผู้ใช้แล้วตัดทันที และเปลี่ยน role แล้วมีผลกับ session ที่เปิดอยู่
ถ้าเอา role ใส่คุกกี้แล้วเชื่อ ทั้งสามข้อกลายเป็น "มีผลใน 12 ชั่วโมง" เงียบๆ
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Form, HTTPException, Request, Response
from sqlalchemy import Engine

from cane.auth import service
from cane.db.repo import audit
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.db.types import now_ms
from cane.engine.state import PROFILES
from cane.engine.supervisor import Supervisor

SESSION_COOKIE = "cane_session"
LOGIN_PATH = "/login"


def get_db(request: Request) -> Engine:
    return request.app.state.db


def get_sup(request: Request) -> Supervisor:
    return request.app.state.sup


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    """`Secure` เฉพาะตอนที่มาทาง https จริง

    spec/09 §6. session สั่ง `Secure` ไว้ และนั่นถูกสำหรับของที่ deploy จริง · แต่
    เบราเซอร์ทิ้งคุกกี้ `Secure` ที่มาทาง http ธรรมดา ซึ่งแปลว่า `cane serve` บน
    เครื่อง dev จะ login ผ่านแล้วเด้งออกทุกครั้งโดยไม่มีอะไรบอกว่าทำไม ·
    ผูกกับ scheme ของคำขอแทนการปิดตาย — deploy ที่อยู่หลัง https ได้ `Secure` เสมอ
    """
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=sessions_repo.LIFETIME_MS // 1000,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict")


def signed_in(request: Request, db: Engine = Depends(get_db)) -> tuple[Session, User]:
    """ด่านแรกของทุกอย่าง · 401 เมื่อไม่มี session ที่ใช้ได้

    `HX-Redirect` ติดมากับ 401 เพื่อให้ HTMX พาไปหน้า login แทนที่จะ swap ความว่าง ·
    คำขอที่ไม่ใช่ HTMX ถูกแปลงเป็น redirect ที่ `app.py` อีกที
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthenticated()

    now = now_ms()
    with db.connect() as conn:
        found = sessions_repo.lookup(conn, token, now=now)
    if found is None:
        raise _unauthenticated()

    session, user = found
    with db.begin() as conn:
        sessions_repo.touch(conn, session.id, now)
    return session, user


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="ต้องเข้าสู่ระบบก่อน",
        headers={"HX-Redirect": LOGIN_PATH},
    )


def current_user(context: tuple[Session, User] = Depends(signed_in)) -> User:
    return context[1]


def current_session(context: tuple[Session, User] = Depends(signed_in)) -> Session:
    return context[0]


def current_mode(context: tuple[Session, User] = Depends(signed_in)) -> str:
    """โหมดอยู่บนแถวของ session ไม่ใช่ในคุกกี้

    ค่าที่ฝั่งผู้ใช้ตั้งเองได้แปลว่าใครก็แก้เป็น `live` ได้โดยไม่ผ่าน step-up
    """
    return context[0].mode


def require_profile(profile: str) -> str:
    """profile ที่ไม่มีอยู่คือ **404 ไม่ใช่ 400** (spec/10 §6. สัญญาของ API)"""
    if profile not in PROFILES:
        raise HTTPException(status_code=404, detail=f"ไม่มี profile {profile!r}")
    return profile


def require_cap(cap: str) -> Callable[..., User]:
    """dependency ที่ผูกหนึ่ง endpoint เข้ากับหนึ่งสิทธิ์ (spec/09)

    ประกอบทับ `signed_in` เสมอ — 401 จึงมาก่อน 403 · คนที่ยังไม่ได้ login ต้องไม่ได้
    คำตอบที่บอกว่า endpoint นี้ต้องใช้สิทธิ์อะไร
    """

    def dependency(
        db: Engine = Depends(get_db), user: User = Depends(current_user)
    ) -> User:
        with db.connect() as conn:
            ok = perms.allowed(conn, role=user.role, cap=cap)
        if not ok:
            raise HTTPException(status_code=403, detail=f"ต้องมีสิทธิ์ {cap}")
        return user

    return dependency


def require_step_up(
    request: Request,
    code: str = Form("", alias="step_up_code"),
    db: Engine = Depends(get_db),
    user: User = Depends(current_user),
) -> User:
    """**ขอทุกครั้งที่ลงมือ ไม่มีช่วงผ่อนผัน** (spec/09 §step-up TOTP)

    รหัสมากับ request นั้นเอง ไม่ใช่กับ session ที่ยืนยันไว้เมื่อกี้ · ผลถูกบันทึกลง
    `user_audit_log.step_up_verified` ที่ผู้เรียก — ตรงนี้ทำหน้าที่ปฏิเสธอย่างเดียว
    """
    now = now_ms()
    with db.begin() as conn:
        ok = service.verify_step_up(conn, user, code, now=now)
        if not ok:
            audit.record(
                conn,
                action="stepup.failed",
                ts=now,
                actor_user_id=user.id,
                target=str(request.url.path),
                ip=client_ip(request),
            )
    if not ok:
        raise StepUpFailed()
    return user


class StepUpFailed(HTTPException):
    """403 ที่ `app.py` แปลงเป็น modal ใบเดิมพร้อมกล่องเตือน

    เป็นคลาสของตัวเองเพราะ handler ต้องแยกมันออกจาก 403 อื่นๆ ที่ควรเป็น JSON ·
    ตัวที่ปฏิเสธยังเป็น dependency เหมือนเดิม การแปลงเป็นหน้าจอเป็นคนละเรื่อง
    """

    def __init__(self) -> None:
        super().__init__(status_code=403, detail="รหัส 6 หลักไม่ถูกต้อง")
