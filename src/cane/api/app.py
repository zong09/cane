"""แอปของคอนโซล — โครงของใบ 19

supervisor อยู่ **ในกระบวนการของคอนโซล** ตาม spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile
และมันถือ handle ของลูกไว้ในหน่วยความจำเท่านั้น · จึงต้องเป็นตัวเดียวต่อหนึ่ง process
และ `cane serve` ต้องรัน worker เดียว — worker ที่สองจะได้ supervisor ที่ไม่มี handle
แล้ว `signal_stop()` ของมันจะคืน `False` ทุกครั้งโดยไม่มีอะไรบอกว่าทำไม
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import Engine

from cane.api import auth_routes
from cane.api import config as config_routes
from cane.api import engine as engine_routes
from cane.api import log as log_routes
from cane.api import overview as overview_routes
from cane.api import pages
from cane.api import report as report_routes
from cane.api import risk as risk_routes
from cane.api import session
from cane.api import symbol_detail as symbol_detail_routes
from cane.api import symbols as symbols_routes
from cane.api import users as users_routes
from cane.api.deps import LOGIN_PATH, StepUpFailed
from cane.api.templating import templates
from cane.api.templating import STATIC
from cane.db.engine import make_engine
from cane.engine.supervisor import Process, Supervisor, spawn_subprocess


def create_app(
    *,
    db: Engine | None = None,
    spawn: Callable[[str], Process] = spawn_subprocess,
) -> FastAPI:
    """`db` ฉีดเข้ามาได้เพื่อให้เทสต์ชุดที่ไม่มี Postgres สร้างแอปได้

    `make_engine()` อ่าน `CANE_DB_DSN` แล้ว raise เมื่อไม่มี และ lifespan เดินก่อน
    request แรกเสมอ — `dependency_overrides` จึงช่วยไม่ทัน ต้องกันตั้งแต่ตอนสร้างแอป
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = db is None
        app.state.db = make_engine(role="console") if owned else db
        app.state.sup = Supervisor(spawn=spawn)
        try:
            yield
        finally:
            # dispose เฉพาะตัวที่ตัวเองสร้าง · Engine ที่เทสต์ยืมมาให้เป็นของเทสต์
            if owned:
                app.state.db.dispose()

    app = FastAPI(title="cane console", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
    app.include_router(auth_routes.router)
    app.include_router(pages.router)
    app.include_router(engine_routes.router)
    app.include_router(config_routes.router)
    app.include_router(overview_routes.router)
    app.include_router(log_routes.router)
    app.include_router(risk_routes.router)
    app.include_router(report_routes.router)
    app.include_router(symbols_routes.router)
    app.include_router(symbol_detail_routes.router)
    app.include_router(users_routes.router)
    app.include_router(session.router)

    # `StepUpFailed` สืบทอดจาก `HTTPException` และ Starlette เลือก handler โดยไล่ตาม
    # `type(exc).__mro__` → ตัวที่เจาะจงกว่าต้องถูกลงทะเบียน**ก่อน** ตัวกว้าง ไม่งั้น
    # รหัส step-up ที่ผิดจะถูกพาไปหน้า login แทนที่จะได้ modal ใบเดิมคืนมา
    @app.exception_handler(StepUpFailed)
    async def _step_up_failed_reopens_the_modal(request: Request, exc: StepUpFailed):
        """รหัสผิดต้องได้ modal ใบเดิมพร้อมกล่องเตือน ไม่ใช่ JSON ที่ htmx ทิ้ง

        `hx-post` ของ modal ยิงไปที่ช่องของตัวเอง คำตอบนี้จึงเข้าไปแทนที่ modal
        ใบเดิมพอดี · ปุ่มยังอยู่ รหัสที่พิมพ์ผิดหายไป ซึ่งเป็นสิ่งที่ควรเกิด
        """
        parts = request.url.path.strip("/").split("/")
        profile, action = (parts[1], parts[3]) if len(parts) >= 4 else ("paper", "start")
        return templates.TemplateResponse(
            request,
            "partials/stepup_modal.html",
            {
                "title": f"ยืนยันอีกครั้ง · {profile}",
                "detail": "รหัสเปลี่ยนทุก 30 วินาที — ใช้รหัสล่าสุดจากแอป",
                "action": f"/api/{profile}/engine/{action}",
                "error": exc.detail,
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _unauthenticated_goes_to_login(
        request: Request, exc: StarletteHTTPException
    ):
        """คนที่พิมพ์ URL ตรงๆ แล้วยังไม่ได้ login ต้องเจอหน้า login ไม่ใช่ JSON 401

        คำขอของ HTMX ไม่ถูกแปลง — มันได้ `HX-Redirect` ที่ติดมากับ 401 อยู่แล้ว
        และการ redirect คำขอ `hx-post` จะ swap ทั้งหน้า login เข้าไปในการ์ดใบเล็ก
        """
        wants_page = (
            exc.status_code == 401
            and request.method == "GET"
            and "hx-request" not in request.headers
            # `/api/…` ตอบเป็นข้อมูลเสมอ · 303 ไปหน้า HTML คือคำตอบที่ client
            # ซึ่งอ่าน JSON เป็นจะ parse ไม่ออกและรายงานผิดเรื่อง
            and not request.url.path.startswith("/api/")
        )
        if wants_page:
            return RedirectResponse(LOGIN_PATH, status_code=303)
        return await http_exception_handler(request, exc)

    return app
