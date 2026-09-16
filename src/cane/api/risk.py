"""หน้า ความเสี่ยง — ผูกกับโหมดที่ session กำลังดู (ใบ 23)

## ช่องไหนมีของจริง ช่องไหนยังไม่มี

| ส่วน | ต้นทาง | สถานะ |
| --- | --- | --- |
| kill switch | ตาราง `kill_switch` | **ใช้งานได้แล้ววันนี้** — ไม่มีแถว = ไม่ latched |
| เพดานทั้งหก | config เวอร์ชันที่ active | **ใช้งานได้แล้ววันนี้** |
| BROKER · ตารางเลเวอเรจกับ bucket | config เวอร์ชันที่ active | **ใช้งานได้แล้ววันนี้** |
| ไม้ต่อฝั่ง · มาร์จิ้น · notional · ไม้ที่รอส่ง | สถานะไม้ที่ venue | ยังไม่มีตารางเก็บ — ใบ 13 |
| ห่าง liquidation ของไม้ปัจจุบัน | ราคาเข้ากับราคา liquidation ของไม้จริง | ใบ 13 · สูตรอยู่ที่ `risk/limits.py` แล้ว |
| ขาดทุนวันนี้ · แพ้ติดกัน | VIEW บน `fills` | ยังไม่มี VIEW · spec/10 §`daily_loss` และ `consecutive_losses` **ไม่ใช่ state** และไม่มีตารางของตัวเอง |
| ไม้ที่ระบบไม่ได้ตั้งใจถือ | `decision_unmanaged` ผ่าน `latest_per_symbol()` | อ่านได้แล้ว · ฐาน dev ยังไม่มีบันทึกสักแถว |

**ค่าที่ยังไม่รู้ขึ้นเป็น `—` ไม่ใช่ `0`** เส้นเดียวกับหน้าภาพรวม · ตัวนับ breaker จึงเป็น
**ช่องว่างเท่าจำนวนเพดาน** ไม่ใช่ศูนย์ช่องที่เต็ม — ศูนย์แปลว่า "ยังไม่แพ้เลย" ซึ่งเป็น
คำตอบที่เรายังให้ไม่ได้

## สามอย่างที่ไฟล์ design เขียนไว้ผิด และห้ามลอกเข้ามา

1. การ์ด kill switch เขียนว่าสถานะเก็บที่ `state/killswitch.json` — ของจริงคือตาราง
   `kill_switch` ตั้งแต่ migration 0008 (errata ที่หัว spec/10)
2. `defaultType = future` — `future` ในคำศัพท์ ccxt คือสัญญาที่มีวันหมดอายุ perp เป็น
   `swap` · ค่านี้ไม่ได้เก็บในฐานเลย `data/exchange.py` คิดจาก `market` ของแต่ละเหรียญ
   และหนึ่งโปรไฟล์มีสอง market พร้อมกันได้
3. สูตร `clientOrderId` ในภาพเขียน `ฝั่งสถานะ (long/short)` — ของจริงคือฝั่งของ
   **ออเดอร์** (`buy`/`sell`) ตาม spec/06 §กันสั่งซ้ำ (reconciliation) ซึ่งเขียน errata
   ข้อนี้ไว้เอง

## ชื่อโมดูลชนกับแพ็กเกจ `cane.risk` ไหม

ไม่ชน — ทั้ง repo ใช้ absolute import (`from cane.risk import limits`) และไฟล์นี้อยู่ใน
`cane.api` · ที่สำคัญกว่าคือ **หนึ่งโมดูลต่อหนึ่งหน้า** ซึ่ง `api/overview.py` กับ
`api/config.py` วางไว้แล้ว การตั้งชื่อเลี่ยงจะทำให้กฎนั้นพังเพื่อแก้ปัญหาที่ไม่มีอยู่
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import (
    client_ip,
    current_mode,
    get_db,
    require_cap,
    require_profile,
)
from cane.api import config as config_routes
from cane.api.templating import templates
from cane.auth import service
from cane.config.settings import Settings, SymbolConfig
from cane.config.validate import ConfigError, validate_settings
from cane.data.exchange import default_type
from cane.db.repo import audit
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import killswitch as killswitch_repo
from cane.db.repo import permissions as perms
from cane.db.repo.users import User
from cane.db.types import now_ms, store_symbol

router = APIRouter()

#: ข้อความที่ใช้แทนค่าที่ยังคำนวณไม่ได้ · ห้ามใช้ `0` แทน (เหมือน `api/overview.py`)
UNKNOWN = "—"


@dataclass(frozen=True, slots=True)
class SymbolRow:
    """หนึ่งบรรทัดของตารางเลเวอเรจและ bucket ต่อเหรียญ

    `liq` เป็นข้อความไม่ใช่ตัวเลข เพราะสองความหมายที่ต่างกันต้องอ่านออกจากกัน:
    เหรียญ spot **ไม่มี** ราคา liquidation — ชั้นนั้นไม่ถูกเรียกเลย ไม่ใช่เรียกแล้ว
    ผ่านเสมอ (spec/06 §`min_liq_buffer_pct` คือสิ่งเดียวที่กัน liquidation) ·
    ส่วน perp มีแต่ยังคำนวณไม่ได้เพราะไม่มีไม้จริงให้วัด
    """

    pair: str
    market: str
    leverage: float
    bucket_long: float
    bucket_short: float | None
    allow_short: bool
    liq: str

    @property
    def sides(self) -> str:
        return "long + short" if self.allow_short else "long เท่านั้น"


@dataclass(frozen=True, slots=True)
class Held:
    """ไม้ที่ระบบไม่ได้ตั้งใจถือ — เศษจาก flip ที่ปิดขาเก่าไม่หมด (decisions #19)

    แยกจากไม้ปกติบนหน้าจอโดยเจตนา: มันมี leverage และ **ไม่มี stop** จนกว่าคนจะเห็น
    และระบบจะไม่ปิดให้เอง · ถ้าคอนโซลกลืนมันไปกับไม้ปกติ การตัดสินนั้นกลายเป็น
    การเงียบใส่ความเสี่ยง
    """

    pair: str
    side: str
    qty: float
    source: str


@dataclass(frozen=True, slots=True)
class Ceiling:
    """หนึ่งแถวของการ์ด "เพดานความเสี่ยง · จาก config profile"

    `pips` > 0 เฉพาะแถว breaker ซึ่ง design วาดเป็นช่องเรียงกัน · ทุกช่องว่างเสมอ
    เพราะตัวนับต้องมาจาก VIEW ที่ยังไม่มี (ดูหัวไฟล์)
    """

    label: str
    value: str
    note: str
    pips: int = 0


def _rows(settings: Settings) -> tuple[SymbolRow, ...]:
    """`allow_short` ของแต่ละแถวเป็นผลรวมสองชั้น (spec/07 §`allow_short` มีสองชั้น)"""
    return tuple(
        SymbolRow(
            pair=sym.symbol,
            market=sym.market,
            leverage=sym.leverage,
            bucket_long=sym.bucket_quote_long,
            bucket_short=sym.bucket_quote_short,
            allow_short=settings.allow_short and sym.allow_short,
            liq="ไม่มี (spot)" if sym.market == "spot" else UNKNOWN,
        )
        for sym in settings.symbols
        if sym.enabled
    )


def _held(conn: Connection, settings: Settings, *, profile: str) -> tuple[Held, ...]:
    """ของค้างจากบันทึกล่าสุดของแต่ละเหรียญ

    อ่านจาก `latest_per_symbol()` ตัวเดียวกับหน้าภาพรวม — `unmanaged` ถูกเขียนซ้ำ
    ทุกแท่งจนกว่าจะหมด แท่งล่าสุดจึงเป็นภาพปัจจุบันอยู่แล้ว ไม่ต้องไล่ย้อนหลัง
    """
    latest = decisions_repo.latest_per_symbol(conn, profile, settings.timeframe)
    held: list[Held] = []
    for sym in settings.symbols:
        if not sym.enabled:
            continue
        found = latest.get((sym.market, store_symbol(sym.symbol)))
        if found is None:
            continue
        held.extend(
            Held(pair=sym.symbol, side=item.side, qty=item.qty, source=item.source)
            for item in found.unmanaged
        )
    return tuple(held)


def _ceilings(settings: Settings, rows: tuple[SymbolRow, ...]) -> tuple[Ceiling, ...]:
    risk = settings.risk
    return (
        Ceiling(
            "เพดานต่อไม้ฝั่ง long · % ของ bucket long",
            f"{risk.max_position_pct_long:g}",
            "เกินเพดานแล้วย่อขนาดไม้ลง ไม่ใช่ปฏิเสธทั้งไม้ · ไม้ที่รออยู่ตอนนี้ยังอ่านไม่ได้",
        ),
        Ceiling(
            "เพดานต่อไม้ฝั่ง short · % ของ bucket short",
            f"{risk.max_position_pct_short:g}",
            "bucket ของสองฝั่งแยกกัน — เปิด short ไม่กินโควตาของไม้ long ที่รออยู่",
        ),
        Ceiling(
            "เพดานขาดทุนต่อวัน",
            f"{risk.max_daily_loss_pct:g}",
            f"วันนี้ {UNKNOWN} · นับกำไรขาดทุนที่ยังไม่ปิดด้วย · ขอบวันคือเที่ยงคืน UTC",
        ),
        Ceiling(
            "ตัดวงจรเมื่อแพ้ติดกัน",
            f"{risk.consecutive_loss_breaker}",
            f"แพ้ติดกัน {UNKNOWN} ไม้ · ครบเมื่อไหร่สั่ง latch kill switch · นับรวมสองฝั่ง",
            pips=risk.consecutive_loss_breaker,
        ),
        Ceiling(
            "เพดานเลเวอเรจต่อเหรียญ",
            f"{risk.max_leverage:g}x",
            _leverage_note(rows),
        ),
        Ceiling(
            "บัฟเฟอร์ห่างราคา liquidation ขั้นต่ำ",
            f"{risk.min_liq_buffer_pct:g}%",
            "ไม้ที่คำนวณแล้วห่างน้อยกว่านี้ถูกปฏิเสธก่อนยิง ไม่ใช่ย่อลงให้พอดี · "
            "เฉพาะเหรียญ perp — เหรียญ spot ไม่ผ่านชั้นนี้เลย",
        ),
    )


def _leverage_note(rows: tuple[SymbolRow, ...]) -> str:
    """ตั้งไว้สูงสุดเท่าไร ที่เหรียญไหน — อ่านจาก config ไม่ใช่จากไม้"""
    if not rows:
        return "ยังไม่มีเหรียญที่เปิดใช้ในโปรไฟล์นี้"
    top = max(row.leverage for row in rows)
    where = " ".join(row.pair for row in rows if row.leverage == top)
    return f"ตั้งไว้สูงสุดในโปรไฟล์ตอนนี้ {top:g}x ที่ {where}"


def _broker(settings: Settings, rows: tuple[SymbolRow, ...]) -> tuple[tuple[str, str], ...]:
    """panel BROKER · `defaultType` คิดจาก market ไม่ได้อ่านจากฐาน (ดูหัวไฟล์)"""
    broker = settings.broker
    markets = sorted({row.market for row in rows})
    kinds = " · ".join(default_type(market) for market in markets) or UNKNOWN
    return (
        ("kind", broker.kind),
        ("exchange", broker.exchange or UNKNOWN),
        ("defaultType", kinds),
        ("marginMode", broker.margin_mode),
        ("positionMode", broker.position_mode.replace("_", "-")),
    )


def _bucket(rows: tuple[SymbolRow, ...], *, side: str) -> float:
    if side == "long":
        return sum(row.bucket_long for row in rows)
    return sum(row.bucket_short or 0.0 for row in rows if row.allow_short)


def page_context(
    conn: Connection, *, profile: str, user: User, notice: str = ""
) -> dict[str, object]:
    """ทุกอย่างที่ `partials/risk_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `risk_`

    เรียก `active_settings()` เองแบบเดียวกับหน้าภาพรวม เพราะ `context.build()` กลืน
    `ConfigError` ทิ้ง และหน้านี้ต้องแยก "config พัง" ออกจาก "ไม่มีเวอร์ชัน active"

    รับ `user` ด้วย (ต่างจากหน้าภาพรวม) เพราะปุ่มบนหน้านี้เปลี่ยนของจริง · ปุ่มที่คนนี้
    กดไม่ได้ต้อง **ไม่ถูก render** ไม่ใช่ render แล้วได้ 403 ตอนกด ซึ่ง htmx จะเอา
    JSON ของ FastAPI มา swap ลงหน้าจอ (แนวเดียวกับ `cfg_can_edit` ของใบ 21)
    """
    problems = 0
    try:
        settings = config_repo.active_settings(conn, profile)
    except ConfigError as exc:
        settings = None
        problems = len(exc.problems)

    kill = killswitch_repo.read(conn, profile)
    gates = {
        "risk_oob": False,
        "risk_notice": notice,
        "risk_can_latch": perms.allowed(conn, role=user.role, cap="killswitch_latch"),
        "risk_can_unlatch": perms.allowed(conn, role=user.role, cap="killswitch_unlatch"),
        "risk_can_dry_run": perms.allowed(conn, role=user.role, cap="toggle_dry_run"),
        "risk_can_allow_short": perms.allowed(conn, role=user.role, cap="edit_profile"),
    }
    if settings is None:
        return {
            "risk_profile": profile,
            "risk_settings": None,
            "risk_problems": problems,
            "risk_kill": kill,
            "risk_rows": (),
            **gates,
        }

    rows = _rows(settings)
    return {
        **gates,
        "risk_profile": profile,
        "risk_settings": settings,
        "risk_problems": 0,
        "risk_kill": kill,
        "risk_rows": rows,
        "risk_held": _held(conn, settings, profile=profile),
        "risk_ceilings": _ceilings(settings, rows),
        "risk_broker": _broker(settings, rows),
        # เพดานของทั้งสองฝั่งมาจาก config — ตัวหารมีจริง ตัวตั้งยังไม่มี
        "risk_bucket_long": _bucket(rows, side="long"),
        "risk_bucket_short": _bucket(rows, side="short"),
        # ต้องรอสถานะไม้จาก venue (ใบ 13)
        "risk_long_count": UNKNOWN,
        "risk_long_margin": UNKNOWN,
        "risk_long_notional": UNKNOWN,
        "risk_short_count": UNKNOWN,
        "risk_short_margin": UNKNOWN,
        "risk_short_notional": UNKNOWN,
    }


@router.get("/partials/risk", response_class=HTMLResponse)
def body(
    request: Request,
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("view_overview")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้าความเสี่ยงของโหมดที่ดูอยู่

    สิทธิ์เป็น `view_overview` ตัวเดียวกับหน้าภาพรวม เพราะตารางสิทธิ์ของ spec/09
    ผูก `GET /api/{profile}/overview` กับ `/risk` ไว้ในแถวเดียวกัน

    มีเป็น partial แยกด้วยเหตุผลเดียวกับหน้าภาพรวม — การสลับโหมด swap แค่การ์ด
    PROFILE กับ engine ถ้าไม่มีเส้นทางนี้ เพดานทั้งหน้าจะค้างอยู่ที่โหมดเดิม
    """
    with db.connect() as conn:
        ctx = page_context(conn, profile=mode, user=user)
    return templates.TemplateResponse(request, "partials/risk_body.html", ctx)


# ── kill switch ───────────────────────────────────────────────────────────────


def _card(
    request: Request,
    db: Engine,
    *,
    mode: str,
    user: User,
    oob: bool,
    notice: str = "",
) -> HTMLResponse:
    """คืนเนื้อหน้าใหม่หลังจากสวิตช์เปลี่ยนสถานะ

    `oob=True` เฉพาะคำตอบที่ออกมาจากฟอร์มใน `#modal-slot` — htmx เอาก้อนนี้ไปวางที่
    `#risk-body` เองแล้วเหลือความว่างมาแทน modal ซึ่งคือการปิด modal (รูปเดียวกับ
    `config.activate`) · ปุ่ม latch ยิงตรงไปที่ `#risk-body` อยู่แล้ว ถ้าติดธง oob
    ด้วยจะได้ความว่างทับหน้าจอทั้งหน้า
    """
    with db.connect() as conn:
        ctx = page_context(conn, profile=mode, user=user, notice=notice)
    ctx["risk_oob"] = oob
    return templates.TemplateResponse(request, "partials/risk_body.html", ctx)


def _unlatch_modal(
    request: Request, *, target: str, error: str = "", status_code: int = 200
) -> HTMLResponse:
    """modal ของการปลด · มีช่องพิมพ์ชื่อโปรไฟล์ **เพิ่ม** จากช่องรหัส 6 หลัก

    สามด่านตอบคำถามคนละข้อจึงแทนกันไม่ได้ (spec/10 §2. สาม state ที่คนละเรื่องกัน):
    ชื่อโปรไฟล์กันการกดผิดโหมด · OWNER กับ step-up กันการกดผิดคน
    """
    return templates.TemplateResponse(
        request,
        "partials/stepup_modal.html",
        {
            "title": f"ปลด kill switch · {target}",
            "detail": "ปลดแล้วระบบกลับไปเปิดไม้ใหม่ได้ตามสัญญาณ · การหยุดกดซ้ำได้เสมอ การปลดไม่ใช่",
            "action": f"/api/{target}/killswitch/unlatch",
            "fields": (("profile_name", f"พิมพ์ {target} เพื่อยืนยัน", target),),
            "error": error,
        },
        status_code=status_code,
    )


@router.post("/api/{profile}/killswitch/latch", response_class=HTMLResponse)
def latch(
    profile: str,
    request: Request,
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("killswitch_latch")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """หยุดยิงออเดอร์เปิดใหม่ทันที · **ไม่มี step-up และกดซ้ำต้องไม่เคยล้มเหลว**

    spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role ให้สิทธิ์นี้กว้างถึง TRADER เพราะ
    ความช้าตอนฉุกเฉินแพงกว่าการกดเกิน · คนที่กด latch แล้วเห็น error เพราะมันถูก
    latch อยู่แล้ว จะไม่รู้ว่าตัวเองหยุดสำเร็จหรือยัง แล้วจะไปกดอย่างอื่น

    เหตุผลคิดให้ ไม่ได้ถามในฟอร์ม — ตารางบังคับว่า latched ต้องมีที่มา
    (`ck_kill_switch_latched_has_a_story`) แต่กล่องข้อความคั่นระหว่างคนกับปุ่มหยุด
    ฉุกเฉินคือความช้าที่ไม่ควรมี · **ใครกดอยู่ที่ `latched_by` ช่องเดียว** ไม่ปนเข้าไป
    ในเหตุผลด้วย ไม่งั้นการ์ดขึ้นชื่อคนสองที่ในประโยคเดียว · ชื่อที่คนอ่านคอนโซลรู้จัก
    คือชื่อในระบบ ส่วน id ของคนกดอยู่ที่ `user_audit_log` ซึ่งลบไม่ได้

    **ไม่แตะ engine เลย** — latch กับ `should_run` เป็นคนละ state คนละ lifecycle
    (spec/10 §`engine.should_run` ≠ `kill_switch.latched`) · engine ที่เดินอยู่จะเดินต่อ
    และยังบันทึกการตัดสินใจที่ลงท้ายว่าถูกกั้น
    """
    target = require_profile(profile)
    now = now_ms()
    with db.begin() as conn:
        killswitch_repo.latch(conn, target, reason="กดจากคอนโซล", by=user.name)
        audit.record(
            conn,
            action="killswitch.latch",
            ts=now,
            actor_user_id=user.id,
            target=target,
            ip=client_ip(request),
        )
    return _card(request, db, mode=mode, user=user, oob=False)


@router.get("/partials/risk/{profile}/unlatch", response_class=HTMLResponse)
def unlatch_modal(
    profile: str,
    request: Request,
    _: User = Depends(require_cap("killswitch_unlatch")),
) -> HTMLResponse:
    return _unlatch_modal(request, target=require_profile(profile))


@router.post("/api/{profile}/killswitch/unlatch", response_class=HTMLResponse)
def unlatch(
    profile: str,
    request: Request,
    profile_name: str = Form(""),
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("killswitch_unlatch")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """ปลดสวิตช์ — ทางออกทางเดียวจากสถานะ latched

    **ลำดับด่านมีผลจริง**: ตรวจชื่อโปรไฟล์ก่อน แล้วค่อยตรวจรหัส · `verify_step_up()`
    เขียน counter ของ TOTP เมื่อผ่าน และ counter ใช้ร่วมกับ login — ตรวจกลับด้าน
    เมื่อไหร่ คนที่พิมพ์ชื่อผิดจะเสียรหัสรอบนั้นไปโดยที่ยังไม่ได้ปลดอะไรเลย

    ชื่อไม่ตรงคืน **200** ไม่ใช่ 4xx เพราะ htmx ทิ้งคำตอบ 4xx ทุกตัวยกเว้นที่อยู่ใน
    `responseHandling` ของ `base.html` (มีแค่ 403 ของ step-up) — modal ที่เงียบหาย
    แปลว่าปุ่มเสียในสายตาคนกด

    ยืนยัน step-up **ในตัว handler** ไม่ใช่ผ่าน `require_step_up` ด้วยเหตุผลเดียวกับ
    `config.activate`: handler ของ `StepUpFailed` แกะ URL เป็น
    `/api/{profile}/engine/{action}` ตายตัว เส้นนี้จะได้ modal ที่ยิงกลับผิด router
    """
    target = require_profile(profile)
    now = now_ms()

    if profile_name.strip() != target:
        return _unlatch_modal(
            request,
            target=target,
            error=f"ชื่อโปรไฟล์ไม่ตรง — ต้องพิมพ์ {target} · ยังไม่ได้ใช้รหัสรอบนี้",
        )

    with db.begin() as conn:
        ok = service.verify_step_up(conn, user, step_up_code.strip(), now=now)
        if not ok:
            audit.record(
                conn,
                action="killswitch.unlatch_refused",
                ts=now,
                actor_user_id=user.id,
                target=target,
                ip=client_ip(request),
            )
    if not ok:
        return _unlatch_modal(
            request, target=target, error="รหัส 6 หลักไม่ถูกต้อง", status_code=403
        )

    with db.begin() as conn:
        killswitch_repo.unlatch(conn, target)
        audit.record(
            conn,
            action="killswitch.unlatch",
            ts=now,
            actor_user_id=user.id,
            target=target,
            ip=client_ip(request),
            step_up_verified=True,
        )
    return _card(request, db, mode=mode, user=user, oob=True)


# ── สวิตช์ dry_run / allow_short ──────────────────────────────────────────────

#: ป้ายของสวิตช์แต่ละตัวบน modal · คีย์ตรงกับชื่อฟิลด์ใน `Settings`
_SWITCH_COPY = {
    "dry_run": (
        "โหมดทดลอง",
        "เปิด = คำนวณครบทุกขั้นและบันทึกครบ แต่ไม่ส่งคำสั่งจริง · "
        "ปิด = ออเดอร์จริงถูกส่งไปที่ venue ตั้งแต่รอบถัดไป",
    ),
    "allow_short": (
        "ฝั่ง short ทั้งระบบ",
        "ปิด = ระบบยังปิด long ตามสัญญาณ short แต่ไม่เปิดไม้ใหม่ฝั่งลง · "
        "เหรียญ spot ไม่ได้รับผลอยู่แล้วเพราะฐานบังคับ long-only",
    ),
}


def _switch_modal(
    request: Request,
    *,
    target: str,
    field: str,
    value: str,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    """modal ของสวิตช์ · ส่ง **ค่าที่ต้องการ** ไปกับฟอร์ม ไม่ใช่คำสั่ง "กลับด้านให้ฉัน"

    กดปุ่มเดิมซ้ำจึงลงที่เดิมเสมอ · ถ้าให้เซิร์ฟเวอร์กลับด้านเอง สองคำขอที่ซ้อนกัน
    จะสลับกันไปมาแล้วผลสุดท้ายขึ้นกับว่าใครถึงก่อน
    """
    label, detail = _SWITCH_COPY[field]
    want = "เปิด" if value == "true" else "ปิด"
    return templates.TemplateResponse(
        request,
        "partials/stepup_modal.html",
        {
            "title": f"{want}{label} · {target}",
            "detail": f"{detail} · บันทึกเป็นเวอร์ชันใหม่แล้วเปิดใช้ทันที",
            "action": f"/api/{target}/config/{field}",
            "hidden": (("value", value),),
            "error": error,
        },
        status_code=status_code,
    )


def _toggled(
    request: Request,
    db: Engine,
    *,
    target: str,
    field: str,
    value: str,
    code: str,
    user: User,
    mode: str,
) -> HTMLResponse:
    """สลับสวิตช์ = **สร้างเวอร์ชันใหม่แล้วเลื่อนตัวชี้ในคำขอเดียว** (spec/10 §เขียน)

    ตัวเขียนเป็นคู่เดิมของใบ 21 (`insert_version` + `activate`) ไม่ใช่ทางเขียน config
    เส้นที่สอง · ที่ต่างคือ **ช่องที่รับ** ซึ่งส่งเป็น `editable` เข้าไปที่ `patched()`
    แทนที่จะไปเพิ่มใน `EDITABLE` ที่ฟอร์มหน้าตั้งค่าใช้อยู่ — ใบ 21 ไม่ต้อง step-up
    ตอนบันทึกร่าง ถ้าสองช่องนี้ไปอยู่ในนั้นด่านที่สเปกแยกไว้ก็หายไปเงียบๆ

    **ทุกด่านที่ไม่ต้องใช้รหัสอยู่ก่อนด่านรหัส** ด้วยเหตุผลเดียวกับการปลด kill switch:
    `verify_step_up()` กิน counter ของ TOTP ที่ใช้ร่วมกับ login · คนที่กดสวิตช์ที่ฐาน
    ปฏิเสธอยู่แล้วต้องไม่เสียรหัสไปด้วย
    """
    now = now_ms()
    if value not in ("true", "false"):
        return _card(request, db, mode=mode, user=user, oob=False,
                     notice=f"ค่าของสวิตช์ต้องเป็น true หรือ false ไม่ใช่ {value!r}")

    with db.connect() as conn:
        try:
            base = config_repo.active_settings(conn, target)
        except ConfigError:
            base = None
    if base is None:
        return _card(request, db, mode=mode, user=user, oob=False,
                     notice="ยังไม่มีเวอร์ชันที่เปิดใช้ให้ลอก — แก้ที่หน้า ตั้งค่า ก่อน")

    if field == "dry_run" and target == "paper" and value == "false":
        # ฐานปฏิเสธด้วย CHECK `ck_config_settings_paper_dry_run` อยู่แล้ว · บอกเหตุ
        # ก่อนจะกินรหัสของคนกด ดีกว่าปล่อยให้ไปตายที่ INSERT (spec/06 §dry_run)
        return _card(request, db, mode=mode, user=user, oob=False,
                     notice="paper บังคับ dry_run = true ที่ฐาน — โปรไฟล์จำลองยิงจริงไม่ได้")

    with db.begin() as conn:
        ok = service.verify_step_up(conn, user, code.strip(), now=now)
        if not ok:
            audit.record(
                conn,
                action=f"config.{field}_refused",
                ts=now,
                actor_user_id=user.id,
                target=f"{target} {field}={value}",
                ip=client_ip(request),
            )
    if not ok:
        return _switch_modal(
            request, target=target, field=field, value=value,
            error="รหัส 6 หลักไม่ถูกต้อง", status_code=403,
        )

    raw, problems = config_routes.patched(base, {field: value}, editable={field: "bool"})
    try:
        settings = validate_settings(raw, source="console")
    except ConfigError as exc:
        problems.extend(exc.problems)
    if problems:
        # วันนี้ไม่มีกฎไหนพามาถึงตรงนี้ได้ — `paper` + `dry_run=false` ถูกกันไว้ข้างบน
        # แล้ว และ `allow_short` ระดับระบบไม่มีกฎของตัวเองเลย · ด่านนี้อยู่เพื่อให้กฎที่
        # เพิ่มมาทีหลังตกฝั่งปลอดภัย (ไม่เขียน) แทนที่จะไปตายที่ INSERT
        #
        # **ต้องมี audit แถวนี้** เพราะมาถึงตรงนี้ได้แปลว่ารหัส TOTP ถูกใช้ไปแล้วหนึ่งรอบ ·
        # spec/10 §เขียน เขียนไว้ที่แถว activate ว่าจังหวะที่กินรหัสแล้วไม่ได้ผลลัพธ์
        # "ไม่ใช่ no-op เงียบ"
        message = " · ".join(problem.message for problem in problems)
        with db.begin() as conn:
            audit.record(
                conn,
                action=f"config.{field}_rejected",
                ts=now,
                actor_user_id=user.id,
                target=f"{target} {field}={value}",
                detail={"problems": message},
                ip=client_ip(request),
                step_up_verified=True,
            )
        return _card(request, db, mode=mode, user=user, oob=True, notice=message)

    with db.begin() as conn:
        head = config_repo.insert_version(
            conn,
            settings,
            source="console",
            note=f"สลับ {field} = {value} จากหน้าความเสี่ยง",
            created_by_user_id=user.id,
            created_ts=now,
        )
        config_repo.activate(conn, head.id)
        audit.record(
            conn,
            action=f"config.{field}",
            ts=now,
            actor_user_id=user.id,
            target=f"{target} v{head.version} {field}={value}",
            ip=client_ip(request),
            step_up_verified=True,
        )
    return _card(request, db, mode=mode, user=user, oob=True,
                 notice=f"{field} = {value} แล้ว · เปิดใช้เป็นเวอร์ชัน v{head.version}")


@router.get("/partials/risk/{profile}/dry-run", response_class=HTMLResponse)
def dry_run_modal(
    profile: str,
    value: str,
    request: Request,
    _: User = Depends(require_cap("toggle_dry_run")),
) -> HTMLResponse:
    return _switch_modal(
        request, target=require_profile(profile), field="dry_run", value=value
    )


@router.post("/api/{profile}/config/dry_run", response_class=HTMLResponse)
def toggle_dry_run(
    profile: str,
    request: Request,
    value: str = Form(""),
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("toggle_dry_run")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """สิทธิ์ `toggle_dry_run` แยกจาก `edit_profile` เพราะการปิดโหมดทดลองคือการเริ่ม
    ส่งเงินจริง ไม่ใช่การแก้ค่าอีกช่องหนึ่ง (spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role)
    """
    return _toggled(
        request, db,
        target=require_profile(profile), field="dry_run", value=value,
        code=step_up_code, user=user, mode=mode,
    )


@router.get("/partials/risk/{profile}/allow-short", response_class=HTMLResponse)
def allow_short_modal(
    profile: str,
    value: str,
    request: Request,
    _: User = Depends(require_cap("edit_profile")),
) -> HTMLResponse:
    return _switch_modal(
        request, target=require_profile(profile), field="allow_short", value=value
    )


@router.post("/api/{profile}/config/allow_short", response_class=HTMLResponse)
def toggle_allow_short(
    profile: str,
    request: Request,
    value: str = Form(""),
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("edit_profile")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """`allow_short` เป็นค่าในโปรไฟล์ สิทธิ์จึงเป็น `edit_profile` ตัวเดิม · ที่ต้อง
    step-up เพราะมันเลื่อนตัวชี้ให้ในคำขอเดียว ไม่ใช่เพราะเป็นการกระทำชนิดใหม่
    """
    return _toggled(
        request, db,
        target=require_profile(profile), field="allow_short", value=value,
        code=step_up_code, user=user, mode=mode,
    )
