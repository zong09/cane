"""หน้า HTML ของคอนโซล

ใบ 19 เป็น **โครง** — ทุกเมนูเปิดได้จริงและ render layout ครบ แต่ `<main>` ยังว่าง
ที่ทำอย่างนี้เพราะเกณฑ์เสร็จของใบคือ "เปิดคู่กับไฟล์ design แล้ว layout ตรงกัน"
ซึ่งตรวจได้ก็ต่อเมื่อกดดูได้ทุกเมนู ไม่ใช่แค่หน้าเดียว
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Engine

from cane.api import context
from cane.api.deps import ConsoleUser, current_mode, current_user, get_db, get_sup
from cane.api.templating import templates
from cane.engine.supervisor import Supervisor

router = APIRouter()

#: slug → (ป้าย, เลขใบที่จะมาเติมเนื้อหน้านี้)
PAGES: dict[str, tuple[str, int]] = {
    slug: (label, ticket) for slug, label, ticket in context.NAV + context.GLOBAL_NAV
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
    user: ConsoleUser = Depends(current_user),
) -> HTMLResponse:
    if slug not in PAGES:
        raise HTTPException(status_code=404, detail=f"ไม่มีหน้า {slug!r}")

    label, ticket = PAGES[slug]
    with db.connect() as conn:
        ctx = context.build(
            conn, sup, user=user, mode=current_mode(request), active=slug
        )
    ctx |= {"page_label": label, "page_ticket": ticket}
    return templates.TemplateResponse(request, "pages/placeholder.html", ctx)
