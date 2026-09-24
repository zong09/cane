"""action ของหน้าผู้ใช้ — เชิญ · เปลี่ยน role · ระงับ · ปลดระงับ · ปลดล็อก · reset 2FA (ใบ 20)

ทุก action เดินลำดับเดียวกัน และ**ลำดับมีผลจริง**:

1. ด่านที่ไม่ต้องใช้รหัส (`refusal`) — อีเมลซ้ำ ระงับตัวเอง OWNER คนสุดท้าย ADMIN แตะ OWNER
   · ตอบ 200 พร้อม modal ที่บอกเหตุ **ยังไม่แตะรหัส** เพราะ `verify_step_up()` เขียน counter
   ของ TOTP เมื่อผ่าน คนที่ถูกปฏิเสธด้วยเหตุอื่นจะเสียรหัสรอบนั้นไปฟรี (แบบเดียวกับ `risk.unlatch`)
2. step-up ในตัว handler ไม่ใช่ `require_step_up` — handler ของ `StepUpFailed` ใน `app.py`
   แกะ URL เป็นของ engine ตายตัว เส้นนี้จะได้ modal ที่ยิงกลับผิดที่
3. ลงมือ + `user_audit_log` ที่ `step_up_verified=True` ในทรานแซกชันเดียวกัน

คำตอบที่สำเร็จคือเนื้อหน้าผู้ใช้ที่ติด `hx-swap-oob` — htmx เอาไปวางที่ `#users-body`
แล้วเหลือความว่างใน `#modal-slot` ซึ่งคือการปิด modal (แบบเดียวกับการ์ด engine)

**ห้ามเหลือศูนย์ OWNER** ถูกตรวจสองชั้น: `refusal` ให้ข้อความที่คนอ่านได้ก่อนใช้รหัส ·
trigger `users_owner_floor` ที่ฐานเป็นด่านจริงถ้าสองคำขอแข่งกัน

## ที่ต่างจากไฟล์ design (handoff §9.8a · §9.8e)

| ในไฟล์ design | ที่นี่ | เพราะ |
| --- | --- | --- |
| reset 2FA: `…แล้วส่งลิงก์ตั้งค่าใหม่ไปที่ <email>…` | `…แล้วแสดงลิงก์ตั้งค่าใหม่ให้คุณส่งต่อเอง…` | ระบบไม่มีตัวส่งอีเมล ลิงก์ถูกแสดง ไม่ถูกส่ง (spec/09 §7. คำเชิญ · reset 2FA · รหัสผ่านที่ลืม) |
| ไม่มีปุ่ม/ข้อความของการปลดล็อก | ปุ่ม `ปลดล็อก` เฉพาะแถวที่ล็อกอยู่ · modal `ปลดล็อก <ชื่อ>` | spec/09 ให้ OWNER/ADMIN ปลดได้จากหน้าผู้ใช้ |
| ปลดระงับใช้ `/suspend` ตัวเดียว | `POST /api/users/{id}/unsuspend` แยก | สองทิศแยก endpoint เหมือน latch/unlatch — กดซ้ำไม่พลิกกลับ |
| แผงเชิญมีแค่อีเมลกับ role | ชื่อของบัญชีใหม่ = ส่วนหน้า `@` ของอีเมล | `users.name` ห้ามว่าง · ไม่เพิ่มช่องในแผง |
| ADMIN จัดการได้ทุกบัญชี | เฉพาะ OWNER แตะบัญชี OWNER หรือให้ role OWNER ได้ | กัน ADMIN เชิญอีเมลอีกอันของตัวเองเป็น OWNER |
| hint `รูปแบบอีเมลไม่ถูกต้อง` / `อีเมลนี้มีบัญชีอยู่แล้ว` ใต้แผงเชิญ | ข้อความเดียวกันใน modal ปฏิเสธ | ด่านเดียวกับ `refusal` ที่ endpoint ใช้ — ไม่มีตรรกะชุดที่สองฝั่งหน้าจอ |

เจ้าของยืนยันห้าแถวแรกแล้ว (handoff §15 ข้อ 9 · 2026-09-24) · แถวสุดท้ายย้ายแค่ตำแหน่ง ข้อความเดิมตาม design
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import client_ip, current_session, get_db, require_cap
from cane.api.templating import templates
from cane.api.users import is_locked, owner_only, page_context, roles_for
from cane.auth import service
from cane.auth.matrix import ROLES
from cane.auth.secrets import new_token
from cane.db.repo import audit, auth_tokens, login_attempts
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.db.types import now_ms

router = APIRouter()

#: action ในหน้าผู้ใช้ → ชื่อใน `user_audit_log` · cap อยู่ที่ route แต่ละตัว (spec/09 §4. endpoint → สิทธิ์ที่ต้องมี)
ACTIONS = {
    "invite": "user.invite",
    "role": "user.role",
    "suspend": "user.suspend",
    "unsuspend": "user.unsuspend",
    "unlock": "user.unlock",
    "reset-2fa": "user.reset_2fa",
}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

LAST_OWNER = "OWNER ที่ใช้งานอยู่คนสุดท้าย ระงับ ย้าย role หรือ reset 2FA ไม่ได้ — ระบบจะไม่มีใครแก้โปรไฟล์ได้อีก"
OWNER_ONLY = "เฉพาะ OWNER แตะบัญชี OWNER หรือให้ role OWNER ได้"


def _takes_away_an_active_owner(target: User, action: str, role: str) -> bool:
    if target.role != "OWNER" or target.status != "active":
        return False
    return action in ("suspend", "reset-2fa") or (action == "role" and role != "OWNER")


def refusal(
    conn: Connection, actor: User, action: str, *, target: User | None, email: str = "", role: str = ""
) -> str | None:
    """เหตุที่ปฏิเสธได้**โดยไม่ต้องใช้รหัส** · `None` = ไปขั้น step-up ต่อได้"""
    now = now_ms()
    if action == "invite":
        if not _EMAIL.match(email):
            return "รูปแบบอีเมลไม่ถูกต้อง"
        if role not in ROLES:
            return f"ไม่มี role {role!r}"
        if owner_only(actor, role):
            return OWNER_ONLY
        if users_repo.by_email(conn, email) is not None:
            return "อีเมลนี้มีบัญชีอยู่แล้ว"
        return None

    assert target is not None
    if owner_only(actor, target.role) or (action == "role" and owner_only(actor, role)):
        return OWNER_ONLY
    if action == "role":
        if role not in ROLES:
            return f"ไม่มี role {role!r}"
        if role == target.role:
            return f"{target.name} เป็น {role} อยู่แล้ว — เลือก role อื่นที่ไม่ใช่ role เดิม"
    if action == "suspend":
        if target.id == actor.id:
            return "ระงับตัวเองไม่ได้ — ออกจากระบบใช้ปุ่ม ออก"
        if target.status == "suspended":
            return f"{target.name} ถูกระงับอยู่แล้ว"
    if action == "unsuspend" and target.status != "suspended":
        return f"{target.name} ไม่ได้ถูกระงับ"
    if action == "unlock" and not is_locked(conn, target.email, now):
        return f"บัญชีของ {target.name} ไม่ได้ถูกล็อก"
    if action == "reset-2fa" and target.id == actor.id:
        return "reset 2FA ของตัวเองจากหน้านี้ไม่ได้ — ให้ OWNER หรือ ADMIN อีกคนทำให้"
    if _takes_away_an_active_owner(target, action, role) and users_repo.count_active_owners(conn) <= 1:
        return LAST_OWNER
    return None


def _copy(action: str, target: User | None, *, email: str, role: str) -> tuple[str, str]:
    """หัวกับเนื้อของ modal · handoff §9.8e (ข้อความที่เปลี่ยนดูตารางในหัวไฟล์)"""
    if action == "invite":
        return "ส่งคำเชิญผู้ใช้ใหม่", f"เชิญ {email} เป็น {role} · บันทึกในชื่อคุณ"
    assert target is not None
    return {
        "role": (f"เปลี่ยน role ของ {target.name}", "สิทธิ์เปลี่ยนตามตารางทันที มีผลกับ session ที่เปิดอยู่ด้วย"),
        "suspend": (
            f"ระงับ {target.name}",
            f"session ที่เปิดอยู่ของ {target.email} จะถูกตัดออกทันที และเข้าใช้ใหม่ไม่ได้จนกว่าจะปลดระงับ",
        ),
        "unsuspend": (f"ปลดระงับ {target.name}", f"ผู้ใช้จะเข้าคอนโซลได้อีกครั้งด้วยสิทธิ์ {target.role} เดิม"),
        "unlock": (f"ปลดล็อก {target.name}", "ล้างตัวนับการใส่รหัสผิด — เข้าสู่ระบบได้ทันทีโดยไม่ต้องรอหมดเวลาล็อก"),
        "reset-2fa": (
            f"reset 2FA ของ {target.name}",
            "ลบการผูก TOTP เดิม แล้วแสดงลิงก์ตั้งค่าใหม่ให้คุณส่งต่อเอง · ระหว่างนี้ผู้ใช้เข้าคอนโซลไม่ได้",
        ),
    }[action]


def _endpoint(action: str, target: User | None) -> str:
    return "/api/users" if action == "invite" else f"/api/users/{target.id}/{action}"


def _modal(
    request: Request,
    actor: User,
    action: str,
    target: User | None,
    *,
    email: str = "",
    role: str = "",
    error: str = "",
    blocked: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    title, detail = _copy(action, target, email=email, role=role)
    ctx: dict[str, object] = {
        "title": title,
        "detail": detail,
        "action": _endpoint(action, target),
        "hidden": (("email", email), ("role", role)) if action == "invite" else (("role", role),) if action == "role" else (),
        "error": error,
        "blocked": blocked,
    }
    if action == "role":
        assert target is not None
        base = f"/partials/users/stepup/role?user_id={target.id}&role="
        ctx["choices"] = tuple((r, r == role, base + r) for r in roles_for(actor))
        chosen = role in ROLES and role != target.role
        ctx["confirm_disabled"] = not chosen
        if chosen:
            extra = " · Owner แก้โปรไฟล์และสลับยิงจริงได้" if role == "OWNER" else ""
            ctx["hint"] = f"จาก {target.role} เป็น {role}{extra}"
    return templates.TemplateResponse(request, "partials/stepup_modal.html", ctx, status_code=status_code)


def _target(conn: Connection, user_id: int | None) -> User:
    target = users_repo.by_id(conn, user_id) if user_id is not None else None
    if target is None:
        raise HTTPException(status_code=404, detail="ไม่มีผู้ใช้นี้")
    return target


def _link(request: Request, token: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/enrol/{token}"


def _perform(
    conn: Connection, request: Request, actor: User, action: str, target: User | None, *, email: str, role: str
) -> tuple[str, dict[str, str]]:
    """ลงมือจริง · คืน (target ที่ลง audit, ข้อความแจ้งผลบนหน้า) — ลิงก์อยู่ใน notice เท่านั้น"""
    now = now_ms()
    if action == "invite":
        token = new_token()
        user_id = users_repo.create(
            conn, email=email, name=email.split("@")[0], role=role, created_ts=now
        )
        auth_tokens.issue(conn, user_id=user_id, kind="invite", token=token, now=now)
        clean = users_repo.normalise_email(email)
        return clean, {
            "text": f"เชิญ {clean} เป็น {role} แล้ว · ลิงก์คำเชิญหมดอายุใน 72 ชั่วโมง · ส่งใหม่ได้จากแถวของผู้ใช้ · ลิงก์นี้แสดงครั้งเดียว",
            "link": _link(request, token),
        }

    assert target is not None
    if action == "role":
        users_repo.set_role(conn, target.id, role)
        return target.email, {"text": f"{target.name}: {target.role} → {role} · มีผลที่ request ถัดไปของทุก session"}
    if action == "suspend":
        users_repo.set_status(conn, target.id, "suspended")
        cut = sessions_repo.revoke_all_for_user(conn, target.id, now)
        return target.email, {"text": f"ระงับ {target.name} แล้ว · ตัด session ที่เปิดอยู่ {cut} รายการ"}
    if action == "unsuspend":
        # CHECK ของฐานให้ `active` ได้เฉพาะบัญชีที่ตั้งรหัสผ่านและผูก TOTP ครบ · คนที่ถูกระงับ
        # ตอนยังรอรับเชิญกลับไปรอรับเชิญ ไม่ใช่ได้บัญชีที่ใช้งานได้ทั้งที่ยังไม่มี 2FA
        enrolled = target.password_hash is not None and target.totp_enrolled_ts is not None
        users_repo.set_status(conn, target.id, "active" if enrolled else "pending")
        return target.email, {"text": f"ปลดระงับ {target.name} แล้ว · กลับเป็น {target.role}"}
    if action == "unlock":
        login_attempts.clear(conn, email=target.email, ts=now, user_id=target.id)
        return target.email, {"text": f"ปลดล็อก {target.name} แล้ว"}

    # reset-2fa · คนที่ยังไม่เคยตั้งรหัสผ่านได้ลิงก์คำเชิญใหม่ (ตั้งรหัสผ่าน + TOTP) — นี่คือ
    # "ส่งใหม่ได้จากแถวของผู้ใช้" ของแผงเชิญ · การออกใหม่ฆ่าลิงก์เดิมที่ `issue()`
    kind = "invite" if target.password_hash is None else "reset_2fa"
    users_repo.clear_totp(conn, target.id)
    sessions_repo.revoke_all_for_user(conn, target.id, now)
    token = new_token()
    auth_tokens.issue(conn, user_id=target.id, kind=kind, token=token, now=now)
    return target.email, {
        "text": f"reset 2FA ของ {target.name} แล้ว · ส่งลิงก์นี้ให้เจ้าตัว หมดอายุใน 72 ชั่วโมง · ลิงก์นี้แสดงครั้งเดียว",
        "link": _link(request, token),
    }


def _run(
    request: Request,
    db: Engine,
    actor: User,
    session: Session,
    action: str,
    *,
    user_id: int | None = None,
    email: str = "",
    role: str = "",
    code: str = "",
) -> HTMLResponse:
    audit_action = ACTIONS[action]
    email = email.strip()
    with db.connect() as conn:
        target = None if action == "invite" else _target(conn, user_id)
        refused = refusal(conn, actor, action, target=target, email=email, role=role)
    if refused:
        return _modal(request, actor, action, target, email=email, role=role, error=refused, blocked=True)

    now = now_ms()
    with db.begin() as conn:
        ok = service.verify_step_up(conn, actor, code.strip(), now=now)
        if not ok:
            audit.record(
                conn,
                action=f"{audit_action}_refused",
                ts=now,
                actor_user_id=actor.id,
                target=target.email if target else users_repo.normalise_email(email),
                ip=client_ip(request),
            )
    if not ok:
        return _modal(
            request, actor, action, target, email=email, role=role, error="รหัส 6 หลักไม่ถูกต้อง", status_code=403
        )

    with db.begin() as conn:
        audit_target, notice = _perform(conn, request, actor, action, target, email=email, role=role)
        audit.record(
            conn,
            action=audit_action,
            ts=now,
            actor_user_id=actor.id,
            target=audit_target,
            detail={"from": target.role, "to": role} if action == "role" else {"role": role} if action == "invite" else None,
            ip=client_ip(request),
            step_up_verified=True,
        )

    # `actor` อ่านมาต้นคำขอ — ถ้าเพิ่งย้าย role ตัวเอง ปุ่มในคำตอบนี้ยังวาดด้วย role เดิม ·
    # คำขอถัดไปอ่าน role ใหม่จากตาราง (spec/09 §6. session) ด่านจริงอยู่ที่ endpoint อยู่แล้ว
    with db.connect() as conn:
        ctx = page_context(conn, session=session, actor=actor, tab="people", notice=notice)
    ctx["us_oob"] = True
    return templates.TemplateResponse(request, "partials/users_body.html", ctx)


# ── modal ─────────────────────────────────────────────────────────────────────


@router.get("/partials/users/stepup/{action}", response_class=HTMLResponse)
def step_up_modal(
    action: str,
    request: Request,
    user_id: int | None = Query(None),
    email: str = Query(""),
    role: str = Query(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("manage_users")),
) -> HTMLResponse:
    """modal ของทุก action ในหน้านี้ · **ด่านจริงอยู่ที่ POST** — ที่นี่แค่บอกเหตุล่วงหน้า

    modal เปลี่ยน role ยิงกลับมาที่นี่ทุกครั้งที่เลือก role เพื่ออัปเดตบรรทัด `จาก A เป็น B`
    และเปิดปุ่มยืนยันเมื่อ role ต่างจากเดิม — ไม่ต้องมี JS ของตัวเอง
    """
    if action not in ACTIONS:
        raise HTTPException(status_code=404, detail=f"ไม่มี action {action!r}")
    with db.connect() as conn:
        target = None if action == "invite" else _target(conn, user_id)
        # ยังไม่เลือก role ใหม่ใน modal เปลี่ยน role ไม่ใช่เหตุปฏิเสธ — ตัวเลือกยังไม่ครบ
        pending_choice = action == "role" and role in ("", target.role if target else "")
        refused = None if pending_choice else refusal(conn, actor, action, target=target, email=email.strip(), role=role)
    return _modal(request, actor, action, target, email=email.strip(), role=role, error=refused or "", blocked=bool(refused))


# ── endpoint · spec/10 §6. สัญญาของ API เป็นเจ้าของรายการ URL ────────────────


@router.post("/api/users", response_class=HTMLResponse)
def invite(
    request: Request,
    email: str = Form(""),
    role: str = Form(""),
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("manage_users")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    return _run(request, db, actor, session, "invite", email=email, role=role, code=step_up_code)


@router.post("/api/users/{user_id}/role", response_class=HTMLResponse)
def change_role(
    user_id: int,
    request: Request,
    role: str = Form(""),
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("manage_users")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    return _run(request, db, actor, session, "role", user_id=user_id, role=role, code=step_up_code)


@router.post("/api/users/{user_id}/suspend", response_class=HTMLResponse)
def suspend(
    user_id: int,
    request: Request,
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("manage_users")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    return _run(request, db, actor, session, "suspend", user_id=user_id, code=step_up_code)


@router.post("/api/users/{user_id}/unsuspend", response_class=HTMLResponse)
def unsuspend(
    user_id: int,
    request: Request,
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("manage_users")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    return _run(request, db, actor, session, "unsuspend", user_id=user_id, code=step_up_code)


@router.post("/api/users/{user_id}/unlock", response_class=HTMLResponse)
def unlock(
    user_id: int,
    request: Request,
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("manage_users")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    return _run(request, db, actor, session, "unlock", user_id=user_id, code=step_up_code)


@router.post("/api/users/{user_id}/reset-2fa", response_class=HTMLResponse)
def reset_2fa(
    user_id: int,
    request: Request,
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    actor: User = Depends(require_cap("reset_other_2fa")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    return _run(request, db, actor, session, "reset-2fa", user_id=user_id, code=step_up_code)
