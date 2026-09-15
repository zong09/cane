"""สลับโหมดที่ session กำลังดู

spec/10 §3. สลับโหมดไม่ใช่การควบคุม — "โหมดคือมุมมอง" และการสลับ **ต้องไม่แตะ engine
ตัวใดเลย ไม่ start ไม่ stop ไม่ pause** · โมดูลนี้จึงไม่มี `Supervisor.start`/`stop`
อยู่เลยแม้แต่ตัวเดียว มันเรียกได้แค่ `status()` ซึ่งอ่านอย่างเดียว
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Engine

from cane.api import context
from cane.api.deps import (
    ConsoleUser,
    current_user,
    get_db,
    get_sup,
    require_profile,
    require_step_up,
    set_mode,
)
from cane.api.templating import templates
from cane.engine.supervisor import Supervisor

router = APIRouter()


@router.get("/partials/mode-modal", response_class=HTMLResponse)
def mode_modal(
    request: Request,
    db: Engine = Depends(get_db),
) -> HTMLResponse:
    with db.connect() as conn:
        warning = context.live_warning(conn)
    return templates.TemplateResponse(
        request, "partials/mode_modal.html", {"live_warning": warning}
    )


@router.get("/partials/mode-modal/close", response_class=HTMLResponse)
def close_mode_modal() -> HTMLResponse:
    """ปิด modal = เขียนทับช่องของมันด้วยความว่าง · ไม่ต้องมี JS สำหรับเรื่องนี้"""
    return HTMLResponse("")


@router.post("/api/session/mode", response_class=HTMLResponse)
def switch_mode(
    request: Request,
    mode: str = Form(...),
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    user: ConsoleUser = Depends(current_user),
) -> HTMLResponse:
    """live → paper ทันที · paper → live ต้อง step-up ซึ่งใบ 19 ยังไม่มี จึงได้ 403

    403 ตอบกลับเป็น **partial ของกล่องเตือนใน modal ไม่ใช่ JSON** เพราะ htmx เป็น
    คนรับ · และต้องเปิด `responseHandling` ให้ 403 swap ได้ที่ `base.html` ด้วย
    ไม่งั้นค่าปริยายของ htmx คือทิ้งคำตอบ 4xx แล้วกดปุ่มไปหน้าจอจะเงียบสนิท
    """
    target = require_profile(mode)

    if target == "live":
        try:
            require_step_up()
        except HTTPException as exc:
            return templates.TemplateResponse(
                request,
                "partials/mode_error.html",
                {"message": exc.detail},
                status_code=exc.status_code,
            )

    with db.connect() as conn:
        ctx = context.build(conn, sup, user=user, mode=target)
    response = templates.TemplateResponse(request, "partials/profile_state.html", ctx)
    set_mode(response, target)
    return response
