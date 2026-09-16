"""หน้า HTML ของคอนโซล

ใบ 19 เป็น **โครง** — ทุกเมนูเปิดได้จริงและ render layout ครบ แต่เนื้อยังว่าง
ที่ทำอย่างนี้เพราะเกณฑ์เสร็จของใบคือ "เปิดคู่กับไฟล์ design แล้ว layout ตรงกัน"
ซึ่งตรวจได้ก็ต่อเมื่อกดดูได้ทุกเมนู ไม่ใช่แค่หน้าเดียว

ใบ 21 เติมเนื้อของ `ตั้งค่า` เป็นหน้าแรก · หน้าที่มีเนื้อแล้วอยู่ใน `BODIES`
ที่เหลือยังไปที่ placeholder ตามเดิม
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Engine

from cane.api import config as config_routes
from cane.api import context
from cane.api import overview as overview_routes
from cane.api import risk as risk_routes
from cane.api.deps import current_mode, current_user, get_db, get_sup
from cane.api.templating import templates
from cane.db.repo import permissions as perms
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

#: slug → เทมเพลตของหน้าที่มีเนื้อแล้ว · ที่ไม่อยู่ในนี้ได้ placeholder ของใบ 19
BODIES = {
    "config": "pages/config.html",
    "overview": "pages/overview.html",
    "risk": "pages/risk.html",
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
        elif slug == "risk":
            ctx |= risk_routes.page_context(conn, profile=mode)
    ctx |= {"page_label": label, "page_ticket": ticket}
    return templates.TemplateResponse(request, BODIES.get(slug, "pages/placeholder.html"), ctx)
