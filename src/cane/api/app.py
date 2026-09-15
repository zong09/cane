"""แอปของคอนโซล — โครงของใบ 19

supervisor อยู่ **ในกระบวนการของคอนโซล** ตาม spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile
และมันถือ handle ของลูกไว้ในหน่วยความจำเท่านั้น · จึงต้องเป็นตัวเดียวต่อหนึ่ง process
และ `cane serve` ต้องรัน worker เดียว — worker ที่สองจะได้ supervisor ที่ไม่มี handle
แล้ว `signal_stop()` ของมันจะคืน `False` ทุกครั้งโดยไม่มีอะไรบอกว่าทำไม
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from cane.api import engine as engine_routes
from cane.api import pages, session
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
    app.include_router(pages.router)
    app.include_router(engine_routes.router)
    app.include_router(session.router)
    return app
