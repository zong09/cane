"""หน้า login และการผูก 2FA (spec/09 §5. login สองขั้น, §7. คำเชิญ · reset 2FA · รหัสผ่านที่ลืม)

หน้าเหล่านี้เป็นหน้าเดียวของคอนโซลที่เปิดได้โดยไม่มี session · ทุกอย่างที่เหลือ
ผ่าน `signed_in` หมด

**ข้อความบนหน้าจอเหมือนกันทุกความล้มเหลว** — `service.Failure` ไม่มีฟิลด์บอกเหตุผล
มาให้อยู่แล้ว ที่นี่จึงเขียนข้อความไว้ที่เดียวและไม่มีทางเผลอแยกเคส
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine

from cane.api.deps import (
    LOGIN_PATH,
    clear_session_cookie,
    client_ip,
    current_session,
    current_user,
    get_db,
    set_session_cookie,
)
from cane.api.templating import templates
from cane.auth import service, totp
from cane.db.repo import auth_tokens
from cane.db.repo import users as users_repo
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.db.types import now_ms

router = APIRouter()

#: ข้อความเดียวสำหรับทุกความล้มเหลวของ login — รหัสผ่านผิด อีเมลไม่มีในระบบ
#: บัญชีถูกระงับ และบัญชียังไม่ผูก 2FA ให้ผลหน้าจอเดียวกันหมด (spec/09)
SAME_FOR_EVERY_FAILURE = "อีเมลหรือรหัสผ่านไม่ถูกต้อง หรือบัญชียังใช้งานไม่ได้"
SECOND_FACTOR_FAILED = "รหัสยืนยันไม่ถูกต้อง หรือหมดเวลาแล้ว — เริ่มใหม่อีกครั้ง"


def _minutes_left(locked_until: int | None, now: int) -> int | None:
    if locked_until is None or locked_until <= now:
        return None
    return max(1, (locked_until - now + 59_999) // 60_000)


@router.get(LOGIN_PATH, response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/login.html", {})


@router.post(LOGIN_PATH, response_class=HTMLResponse)
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Engine = Depends(get_db),
) -> HTMLResponse:
    """ขั้นที่ 1 · ผ่านแล้วได้ฟอร์มขั้นที่ 2 พร้อมตั๋วในช่องซ่อน — **ยังไม่มี session**"""
    now = now_ms()
    with db.begin() as conn:
        result = service.begin_login(
            conn, email=email, password=password, now=now, ip=client_ip(request)
        )

    if isinstance(result, service.Failure):
        return templates.TemplateResponse(
            request,
            "pages/login.html",
            {
                "error": SAME_FOR_EVERY_FAILURE,
                "locked_minutes": _minutes_left(result.locked_until, now),
                "email": email,
            },
            status_code=401,
        )

    return templates.TemplateResponse(
        request, "pages/login_2fa.html", {"ticket": result.token}
    )


@router.post("/login/verify", response_class=HTMLResponse)
def login_verify(
    request: Request,
    ticket: str = Form(...),
    code: str = Form(...),
    db: Engine = Depends(get_db),
) -> Response:
    """ขั้นที่ 2 · TOTP หรือ backup code แล้วถึงจะมี session"""
    now = now_ms()
    with db.begin() as conn:
        result = service.complete_login(
            conn,
            ticket=ticket,
            code=code.strip(),
            now=now,
            ip=client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    if isinstance(result, service.Failure):
        return templates.TemplateResponse(
            request,
            "pages/login.html",
            {
                "error": SECOND_FACTOR_FAILED,
                "locked_minutes": _minutes_left(result.locked_until, now),
            },
            status_code=401,
        )

    response = Response(status_code=204, headers={"HX-Redirect": "/overview"})
    set_session_cookie(request, response, result.token)
    return response


@router.post("/logout")
def logout(
    request: Request,
    db: Engine = Depends(get_db),
    session: Session = Depends(current_session),
    user: User = Depends(current_user),
) -> Response:
    with db.begin() as conn:
        service.sign_out(conn, session_id=session.id, user_id=user.id, now=now_ms())

    response = Response(status_code=204, headers={"HX-Redirect": LOGIN_PATH})
    clear_session_cookie(response)
    return response


# ── ผูก 2FA ผ่านลิงก์ที่ถูก "แสดง" ไม่ใช่ "ส่ง" (spec/09) ────────────────────


@router.get("/enrol/{token}", response_class=HTMLResponse)
def enrol_form(token: str, request: Request, db: Engine = Depends(get_db)) -> Response:
    """แสดง secret ให้สแกน · **ยังไม่เปิดใช้บัญชี** จนกว่ารหัสจากแอปจะกลับมา

    ตั๋วยังไม่ถูก consume ที่นี่ เพราะการเปิดหน้าทิ้งไว้แล้วสแกนไม่ทันต้องไม่ทำให้
    ลิงก์ตาย · ตัวที่ใช้ตั๋วจริงคือ `POST` ข้างล่าง
    """
    now = now_ms()
    with db.connect() as conn:
        user = _user_for_invite(conn, token, now)
    if user is None:
        return templates.TemplateResponse(
            request, "pages/enrol_dead.html", {}, status_code=404
        )

    secret = totp.new_secret()
    return templates.TemplateResponse(
        request,
        "pages/enrol.html",
        {
            "token": token,
            "secret": secret,
            "uri": totp.provisioning_uri(secret, user.email),
            "email": user.email,
            "needs_password": user.password_hash is None,
        },
    )


@router.post("/enrol/{token}", response_class=HTMLResponse)
def enrol_submit(
    token: str,
    request: Request,
    secret: str = Form(...),
    code: str = Form(...),
    password: str = Form(""),
    db: Engine = Depends(get_db),
) -> Response:
    now = now_ms()
    with db.begin() as conn:
        # **ยังไม่ใช้ตั๋วตรงนี้** — รหัสจากแอปที่พิมพ์ผิดไม่ควรเผาลิงก์ทิ้ง · ตั๋วถูกใช้
        # เมื่อผูกสำเร็จเท่านั้น ซึ่งทำให้ "ลองใหม่" เป็นการยิง `POST` เดิมซ้ำได้เฉยๆ
        user = _user_for_invite(conn, token, now)
        if user is None:
            return templates.TemplateResponse(
                request, "pages/enrol_dead.html", {}, status_code=404
            )

        # รหัสผ่านต้องลงก่อน `enrol_totp()` เพราะตัวนั้นเปลี่ยนสถานะเป็น `active`
        # และ `ck_users_active_means_fully_enrolled` ปฏิเสธ active ที่ยังไม่มีรหัสผ่าน
        fresh_password = password.strip() or None
        if fresh_password is not None:
            service.enrol(conn, user=user, password=fresh_password, now=now)

        codes = totp.new_backup_codes()
        ok = service.confirm_enrolment(
            conn, user=user, secret=secret, codes=codes, code=code.strip(), now=now
        )
        if ok:
            auth_tokens.consume(conn, token=token, kind="invite", now=now)

    if not ok:
        return templates.TemplateResponse(
            request,
            "pages/enrol.html",
            {
                "token": token,
                "secret": secret,
                "uri": totp.provisioning_uri(secret, user.email),
                "email": user.email,
                "needs_password": user.password_hash is None and fresh_password is None,
                "error": "รหัสจากแอปไม่ตรง — ลองใหม่อีกครั้ง",
            },
            status_code=400,
        )

    return templates.TemplateResponse(
        request, "pages/enrol_done.html", {"codes": codes}
    )


def _user_for_invite(conn, token: str, now: int) -> User | None:
    """อ่านว่าใครถือลิงก์นี้อยู่ โดย **ไม่ใช้** ตั๋ว"""
    from sqlalchemy import select

    from cane.auth.secrets import token_hash
    from cane.db.schema import auth_tokens as table

    row = conn.execute(
        select(table.c.user_id).where(
            table.c.token_hash == token_hash(token),
            table.c.kind == "invite",
            table.c.used_ts.is_(None),
            table.c.retired_ts.is_(None),
            table.c.expires_ts > now,
        )
    ).one_or_none()
    return None if row is None else users_repo.by_id(conn, row.user_id)
