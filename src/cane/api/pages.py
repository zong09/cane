"""หน้า HTML ของคอนโซล

ใบ 19 เป็น **โครง** — ทุกเมนูเปิดได้จริงและ render layout ครบ แต่เนื้อยังว่าง
ที่ทำอย่างนี้เพราะเกณฑ์เสร็จของใบคือ "เปิดคู่กับไฟล์ design แล้ว layout ตรงกัน"
ซึ่งตรวจได้ก็ต่อเมื่อกดดูได้ทุกเมนู ไม่ใช่แค่หน้าเดียว

ใบ 21 เติมเนื้อของ `ตั้งค่า` เป็นหน้าแรก · หน้าที่มีเนื้อแล้วอยู่ใน `BODIES`
ใบ 20 เติม `ผู้ใช้` เป็นหน้าสุดท้าย — ตอนนี้ทุกเมนูมีเนื้อแล้ว
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Engine

from cane.api import config as config_routes
from cane.api import context
from cane.api import log as log_routes
from cane.api import overview as overview_routes
from cane.api import report as report_routes
from cane.api import risk as risk_routes
from cane.api import symbols as symbols_routes
from cane.api import users as users_routes
from cane.api.deps import current_mode, current_session, current_user, get_db, get_sup
from cane.api.templating import templates
from cane.db.repo import permissions as perms
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.engine.supervisor import Supervisor

router = APIRouter()

#: slug → (ป้าย, เลขใบที่จะมาเติมเนื้อหน้านี้)
PAGES: dict[str, tuple[str, int]] = {
    slug: (label, ticket) for slug, label, ticket in context.NAV + context.GLOBAL_NAV
}

#: สิทธิ์ขั้นต่ำที่ต้องมีเพื่อ "เปิดหน้า" · spec/09 ผูกสิทธิ์ให้ endpoint ที่คืนข้อมูล
#: ส่วนหน้า HTML เป็นเปลือก — คนที่เปิดหน้าภาพรวมได้ต้องมี `view_overview` เป็นอย่างน้อย
#: และหน้าผู้ใช้เป็นของ `manage_users` เท่านั้น
PAGE_CAP = {slug: "view_overview" for slug in PAGES}
PAGE_CAP["users"] = "manage_users"
#: spec/09 ผูก `GET /api/{profile}/records` ไว้กับ `read_decisions` — เปลือกของหน้า
#: จึงต้องขอสิทธิ์เดียวกับ partial ที่มันจะยิงต่อ ไม่งั้นหน้าเปิดได้แล้วเนื้อ 403
PAGE_CAP["log"] = "read_decisions"

#: slug → เทมเพลตของหน้า · ครบทุกเมนูแล้ว (fallback ไป placeholder ของใบ 19 ไม่ถูกใช้อีก)
BODIES = {
    "config": "pages/config.html",
    "log": "pages/log.html",
    "overview": "pages/overview.html",
    "report": "pages/report.html",
    "risk": "pages/risk.html",
    "symbols": "pages/symbols.html",
    "users": "pages/users.html",
}


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/overview")


@router.get("/{slug}", response_class=HTMLResponse)
def page(
    slug: str,
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    user: User = Depends(current_user),
    mode: str = Depends(current_mode),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    if slug not in PAGES:
        raise HTTPException(status_code=404, detail=f"ไม่มีหน้า {slug!r}")

    label, ticket = PAGES[slug]
    with db.connect() as conn:
        if not perms.allowed(conn, role=user.role, cap=PAGE_CAP[slug]):
            raise HTTPException(status_code=403, detail=f"ต้องมีสิทธิ์ {PAGE_CAP[slug]}")
        ctx = context.build(conn, sup, user=user, mode=mode, active=slug)
        if slug == "config":
            # หน้าตั้งค่าเปิดที่โปรไฟล์ของโหมดที่ดูอยู่ · แท็บอีกใบสลับเองทาง partial
            ctx |= config_routes.page_context(
                conn, sup, profile=mode, user=user, mode=mode
            )
        elif slug == "overview":
            ctx |= overview_routes.page_context(conn, profile=mode)
        elif slug == "log":
            ctx |= log_routes.page_context(conn, profile=mode)
        elif slug == "risk":
            ctx |= risk_routes.page_context(conn, profile=mode, user=user)
        elif slug == "symbols":
            ctx |= symbols_routes.page_context(conn, profile=mode, user=user)
        elif slug == "report":
            ctx |= report_routes.page_context(conn, profile=mode, user=user)
        elif slug == "users":
            ctx |= users_routes.page_context(
                conn, session=session, actor=user, tab=request.query_params.get("tab", "")
            )
    ctx |= {"page_label": label, "page_ticket": ticket}
    return templates.TemplateResponse(request, BODIES.get(slug, "pages/placeholder.html"), ctx)
