"""start / stop / status ของ engine

จังหวะของ `start` เป็นจุดที่พังเงียบได้ง่ายที่สุดในใบนี้ ดู `_start()` ประกอบ
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Engine

from cane.api import context
from cane.api.deps import current_mode, get_db, get_sup, require_profile
from cane.api.templating import templates
from cane.engine.supervisor import Supervisor

router = APIRouter()


def _card(
    request: Request, db: Engine, sup: Supervisor, *, just_changed: bool = False
) -> HTMLResponse:
    with db.connect() as conn:
        ctx = context.engine_fragment(
            conn, sup, mode=current_mode(request), just_changed=just_changed
        )
    return templates.TemplateResponse(request, "partials/engine.html", ctx)


@router.get("/partials/engine", response_class=HTMLResponse)
def card(
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
) -> HTMLResponse:
    """การ์ด engine ตัวเดียวกับที่ HTMX poll ทุกรอบ heartbeat

    อยู่ใต้ `/partials/` ไม่ใช่ `/api/` โดยตั้งใจ — spec/10 §6. สัญญาของ API เป็น
    รายการ endpoint ที่ถือเป็นจริงและผูกกับตารางสิทธิ์ของ spec/09 การเพิ่ม endpoint
    ฝั่ง HTML เข้าไปในรายการนั้นจะทำให้สองเอกสารไม่ตรงกันโดยไม่ได้อะไรกลับมา
    """
    return _card(request, db, sup)


@router.get("/api/engine/status")
def status(
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
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
    sup.launch(before)
    return _card(request, db, sup, just_changed=True)


@router.post("/api/{profile}/engine/stop", response_class=HTMLResponse)
def stop(
    profile: str,
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
) -> HTMLResponse:
    """เจตนาในตารางคือกลไกหลัก · SIGTERM เป็นแค่ตัวเร่ง

    `signal_stop()` คืน `False` เมื่อไม่มี handle ซึ่ง **ไม่ใช่ความผิดพลาด** —
    คอนโซลที่เพิ่งรีสตาร์ทไม่มี handle แต่ `should_run` ที่เขียนไปแล้วทำงานแทนอยู่
    """
    target = require_profile(profile)
    with db.begin() as conn:
        sup.stop(conn, target)
    sup.signal_stop(target)
    return _card(request, db, sup, just_changed=True)
