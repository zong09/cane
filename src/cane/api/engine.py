"""start / stop / status ของ engine

จังหวะของ `start` เป็นจุดที่พังเงียบได้ง่ายที่สุดในใบนี้ ดู `_start()` ประกอบ
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Engine

from cane.api import context
from cane.api.deps import (
    client_ip,
    current_mode,
    get_db,
    get_sup,
    require_cap,
    require_profile,
    require_step_up,
)
from cane.db.repo import audit
from cane.db.repo.users import User
from cane.db.types import now_ms
from cane.api.templating import templates
from cane.engine.supervisor import Supervisor

router = APIRouter()


def _log(conn, request: Request, user: User, *, action: str, target: str) -> None:
    """`step_up_verified=True` เสมอ เพราะ route นี้ผ่าน `require_step_up` มาแล้ว

    ถ้าวันหนึ่งมีใครถอด dependency นั้นออก บรรทัดนี้จะกลายเป็นคำโกหกในตารางที่
    ลบไม่ได้ · เขียนไว้ตรงนี้ไม่ใช่ที่ `require_step_up` เพราะ audit ต้องบันทึก
    **สิ่งที่ทำ** ไม่ใช่ **ด่านที่ผ่าน** — ด่านที่ผ่านแต่ทำไม่สำเร็จเป็นคนละเรื่อง
    """
    audit.record(
        conn,
        action=action,
        ts=now_ms(),
        actor_user_id=user.id,
        target=target,
        ip=client_ip(request),
        step_up_verified=True,
    )


def _card(
    request: Request,
    db: Engine,
    sup: Supervisor,
    *,
    mode: str,
    just_changed: bool = False,
    oob: bool = False,
) -> HTMLResponse:
    """`mode` ถูกส่งเข้ามา ไม่ใช่อ่านเองจาก request

    ใบ 19 อ่านจากคุกกี้ตรงนี้ได้เพราะคุกกี้อยู่บน request · ใบ 20 ย้ายโหมดไปอยู่บน
    แถวของ session ซึ่งอ่านได้ทาง dependency เท่านั้น
    """
    with db.connect() as conn:
        ctx = context.engine_fragment(
            conn, sup, mode=mode, just_changed=just_changed
        )
    ctx["oob"] = oob
    return templates.TemplateResponse(request, "partials/engine.html", ctx)


@router.get("/partials/engine", response_class=HTMLResponse)
def card(
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    mode: str = Depends(current_mode),
    _: User = Depends(require_cap("view_overview")),
) -> HTMLResponse:
    """การ์ด engine ตัวเดียวกับที่ HTMX poll ทุกรอบ heartbeat

    อยู่ใต้ `/partials/` ไม่ใช่ `/api/` โดยตั้งใจ — spec/10 §6. สัญญาของ API เป็น
    รายการ endpoint ที่ถือเป็นจริงและผูกกับตารางสิทธิ์ของ spec/09 การเพิ่ม endpoint
    ฝั่ง HTML เข้าไปในรายการนั้นจะทำให้สองเอกสารไม่ตรงกันโดยไม่ได้อะไรกลับมา
    """
    return _card(request, db, sup, mode=mode)


#: ข้อความในหัว modal ต่อ action · copy ของ design (handoff §9.8e)
_STEP_UP_COPY = {
    "start": ("สตาร์ท engine", "engine จะเริ่มวนรอบและตัดสินใจตามค่าที่ตั้งไว้"),
    "stop": ("หยุด engine", "ไม้ที่เปิดอยู่ยังอยู่ที่ venue ต่อไป และ stop loss ยังอยู่"),
}


@router.get("/partials/stepup/{profile}/{action}", response_class=HTMLResponse)
def step_up_modal(
    profile: str,
    action: str,
    request: Request,
    _: User = Depends(require_cap("engine_control")),
) -> HTMLResponse:
    """modal 436px ที่ขอรหัสก่อนลงมือ · ด่านจริงอยู่ที่ endpoint ไม่ใช่ที่นี่

    handoff เขียนไว้เองว่า "ของจริงต้องบังคับที่ endpoint ไม่ใช่ที่ UI" — modal นี้
    เป็นทางที่สะดวก ไม่ใช่ด่าน · `require_cap` ตรงนี้มีไว้ไม่ให้ modal โผล่ให้คนที่
    กดไปก็ได้ 403 อยู่ดี
    """
    target = require_profile(profile)
    if action not in _STEP_UP_COPY:
        raise HTTPException(status_code=404, detail=f"ไม่มี action {action!r}")
    title, detail = _STEP_UP_COPY[action]
    return templates.TemplateResponse(
        request,
        "partials/stepup_modal.html",
        {
            "title": f"{title} · {target}",
            "detail": detail,
            "action": f"/api/{target}/engine/{action}",
        },
    )


@router.get("/api/engine/status")
def status(
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    _: User = Depends(require_cap("view_overview")),
) -> dict[str, object]:
    """**ทั้งสอง profile ในคำขอเดียวเสมอ** (spec/10 §6. สัญญาของ API)

    ไม่แยกเป็นต่อ profile เพราะการ์ด PROFILE แสดงสถานะของอีกโหมดอยู่ด้วย สองคำขอ
    จะได้ภาพที่ต่างเวลากันเล็กน้อยทุกครั้ง
    """
    with db.connect() as conn:
        views = sup.status(conn)
    return {
        "engines": [
            {
                "profile": view.profile,
                "should_run": view.should_run,
                "last_heartbeat_ts": view.last_heartbeat_ts,
                "blocked_reason": view.blocked_reason,
                "status": view.status,
                "pid": view.pid,
            }
            for view in views
        ]
    }


@router.post("/api/{profile}/engine/start", response_class=HTMLResponse)
def start(
    profile: str,
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    mode: str = Depends(current_mode),
    user: User = Depends(require_cap("engine_control")),
    _: User = Depends(require_step_up),
) -> HTMLResponse:
    """สามจังหวะ: เขียนเจตนา → commit → spawn → **อ่านใหม่** ถึงจะ render

    `start()` คืนภาพ *ก่อน* คำสั่ง เพราะ `launch()` ต้องใช้ตัดสินว่ามีของเดิมอยู่ไหม
    ถ้าเอาภาพนั้นไปแสดงตรงๆ หน้าจอจะขึ้น `stopped` ทันทีหลังคนกดสตาร์ท

    `launch()` ต้องอยู่ **นอก** ทรานแซกชัน — process ลูกต่อ DB ด้วย connection ของ
    ตัวเอง มันมองไม่เห็นทรานแซกชันที่ยังไม่ commit แล้วจะอ่าน `should_run = false`
    แล้วออกทันทีโดยไม่มี error ที่ไหนเลย

    สตาร์ทซ้ำตอน heartbeat ยังสดคือ no-op คืน 200 ไม่ใช่ error — `launch()` ตัดสิน
    เรื่องนี้เองจากความสดของ heartbeat
    """
    target = require_profile(profile)
    with db.begin() as conn:
        before = sup.start(conn, target)
        _log(conn, request, user, action="engine.start", target=target)
    sup.launch(before)
    return _card(request, db, sup, mode=mode, just_changed=True, oob=True)


@router.post("/api/{profile}/engine/stop", response_class=HTMLResponse)
def stop(
    profile: str,
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    mode: str = Depends(current_mode),
    user: User = Depends(require_cap("engine_control")),
    _: User = Depends(require_step_up),
) -> HTMLResponse:
    """เจตนาในตารางคือกลไกหลัก · SIGTERM เป็นแค่ตัวเร่ง

    `signal_stop()` คืน `False` เมื่อไม่มี handle ซึ่ง **ไม่ใช่ความผิดพลาด** —
    คอนโซลที่เพิ่งรีสตาร์ทไม่มี handle แต่ `should_run` ที่เขียนไปแล้วทำงานแทนอยู่
    """
    target = require_profile(profile)
    with db.begin() as conn:
        sup.stop(conn, target)
        _log(conn, request, user, action="engine.stop", target=target)
    sup.signal_stop(target)
    return _card(request, db, sup, mode=mode, just_changed=True, oob=True)
