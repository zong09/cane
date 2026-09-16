"""สลับโหมดที่ session กำลังดู (spec/10 §3. สลับโหมดไม่ใช่การควบคุม)

"โหมดคือมุมมอง" และการสลับ **ต้องไม่แตะ engine ตัวใดเลย ไม่ start ไม่ stop ไม่ pause**
· โมดูลนี้จึงไม่มี `Supervisor.start`/`stop` อยู่เลยแม้แต่ตัวเดียว มันเรียกได้แค่
`status()` ซึ่งอ่านอย่างเดียว

โหมดถูกเขียนลง **แถวของ session** ไม่ใช่คุกกี้ · ค่าที่ฝั่งผู้ใช้ตั้งเองได้แปลว่า
ใครก็แก้เป็น `live` ได้โดยไม่ผ่าน step-up ซึ่งทำให้ด่านทั้งด่านไม่มีความหมาย

การสลับไป `live` **ไม่ใช่เรื่องของสิทธิ์** — spec/09 บอกว่าทุก role ที่ login ได้
สลับได้ สิ่งที่กั้นคือ step-up · ถ้าทำเป็นสิทธิ์ VIEWER กับ AUDITOR จะดู live ไม่ได้
ซึ่งขัดกับหน้าที่ของสองบทบาทนั้นทั้งบทบาท
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Engine

from cane.api import context
from cane.api.deps import (
    client_ip,
    current_session,
    current_user,
    get_db,
    get_sup,
    require_profile,
)
from cane.api.templating import templates
from cane.auth import service
from cane.db.repo import audit
from cane.db.repo import sessions as sessions_repo
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.db.types import now_ms
from cane.engine.supervisor import Supervisor

router = APIRouter()


@router.get("/partials/mode-modal", response_class=HTMLResponse)
def mode_modal(
    request: Request,
    db: Engine = Depends(get_db),
    _: User = Depends(current_user),
) -> HTMLResponse:
    with db.connect() as conn:
        warning = context.live_warning(conn)
    return templates.TemplateResponse(
        request, "partials/mode_modal.html", {"live_warning": warning}
    )


@router.get("/partials/mode-modal/close", response_class=HTMLResponse)
def close_mode_modal(_: User = Depends(current_user)) -> HTMLResponse:
    """ปิด modal = เขียนทับช่องของมันด้วยความว่าง · ไม่ต้องมี JS สำหรับเรื่องนี้"""
    return HTMLResponse("")


@router.post("/api/session/mode", response_class=HTMLResponse)
def switch_mode(
    request: Request,
    mode: str = Form(...),
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    session: Session = Depends(current_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    """live → paper ทันที · paper → live ต้องแนบรหัส 6 หลักมากับคำขอนี้เอง

    ไม่ใช้ dependency `require_step_up` ที่ปฏิเสธทั้ง request เพราะขา `paper`
    ไม่ต้องมีรหัส · เงื่อนไขขึ้นกับ **ค่าในฟอร์ม** ไม่ใช่กับตัว endpoint
    """
    target = require_profile(mode)
    now = now_ms()

    if target == "live":
        with db.begin() as conn:
            ok = service.verify_step_up(conn, user, step_up_code.strip(), now=now)
            if not ok:
                audit.record(
                    conn,
                    action="session.mode_live_refused",
                    ts=now,
                    actor_user_id=user.id,
                    ip=client_ip(request),
                )
        if not ok:
            with db.connect() as conn:
                warning = context.live_warning(conn)
            return templates.TemplateResponse(
                request,
                "partials/mode_modal.html",
                {"live_warning": warning, "error": "รหัส 6 หลักไม่ถูกต้อง"},
                status_code=403,
            )

    with db.begin() as conn:
        sessions_repo.set_mode(conn, session.id, target)
        if target == "live":
            # spec/09 ระบุว่าการสลับโหมดไป `live` เป็นสิ่งที่ต้องบันทึก
            audit.record(
                conn,
                action="session.mode_live",
                ts=now,
                actor_user_id=user.id,
                ip=client_ip(request),
                step_up_verified=True,
            )
        ctx = context.build(conn, sup, user=user, mode=target)

    # การ์ดกลับไปแบบ out-of-band แล้วเหลือความว่างมาแทน modal — ปิดหน้าต่างด้วย
    # การ swap เป้าเดียว ไม่ต้องมี JS มาช่วย
    ctx["oob"] = True
    response = templates.TemplateResponse(request, "partials/profile_state.html", ctx)
    # การสลับโหมดเปลี่ยน "ขอบเขต" ของทั้งหน้า ไม่ใช่แค่การ์ดสองใบนี้ · เนื้อหน้าที่
    # ผูกกับโหมด (ใบ 22 เป็นใบแรก) ฟังเหตุการณ์นี้แล้วดึงของตัวเองใหม่ · ที่นี่ไม่รู้
    # ว่าหน้าไหนเปิดอยู่และไม่ควรรู้ — การประกาศว่า "โหมดเปลี่ยนแล้ว" พอแล้ว
    response.headers["HX-Trigger"] = "cane:mode"
    return response
