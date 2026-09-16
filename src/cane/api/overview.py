"""หน้า ภาพรวม — ผูกกับโหมดที่ session กำลังดู (ใบ 22)

## ทำไมหลายช่องบนหน้านี้ขึ้นว่า "ยังไม่มีข้อมูล"

ใบ 22 ทำ **โครง** ของหน้า ตัวเลขที่ต้องมี engine เดินก่อนยังไม่มีต้นทางในฐาน:

| ช่อง | ต้นทาง | สถานะ |
| --- | --- | --- |
| โซน · state · สัญญาณ | ตาราง `decisions` | มีจริงทันทีที่ใบ 12 เขียนแถวแรก |
| kill switch | ตาราง `kill_switch` | **ใช้งานได้แล้ววันนี้** — ไม่มีแถว = ไม่ latched |
| เพดานทุกตัว | config เวอร์ชันที่ active | **ใช้งานได้แล้ววันนี้** |
| ถือสถานะอยู่ · มาร์จิ้นที่ใช้ | สถานะไม้ที่ venue | ยังไม่มีตารางเก็บ — ใบ 13 |
| ขาดทุนวันนี้ · แพ้ติดกัน | VIEW บน `fills` + ฟังก์ชันที่รับ mark price | ยังไม่มี VIEW · spec/10 §`daily_loss` และ `consecutive_losses` **ไม่ใช่ state** และไม่มีตารางของตัวเอง ห้ามเก็บเป็นตาราง |

**ค่าที่ยังไม่รู้ต้องขึ้นเป็น `—` ไม่ใช่ `0`** · ศูนย์เป็นคำตอบ ("วันนี้ไม่ขาดทุนเลย")
ส่วน `—` เป็นการบอกว่ายังตอบไม่ได้ · repo นี้ยึดเส้นนี้อยู่แล้วที่
`funding_unavailable_reason` และที่ `day_pnl_pct=None` ใน `risk/limits.py`

## เพดานมาร์จิ้นเป็น "ต่อฝั่ง" ไม่ใช่ก้อนเดียว

ข้อความในใบเขียนว่า cap คือผลรวม `bucket_quote_long` + `bucket_quote_short` ทุกบล็อก
· **สเปกไม่มีเพดานมาร์จิ้นรวมอยู่เลย** และ spec/05 เขียนไว้ตรงๆ ว่าเจตนาคือให้แต่ละ
เหรียญมีเงินของตัวเองแยกสองฝั่ง "ไม่ต้องมีกฎพิเศษกันสัดส่วนรวมเกิน 100%" ·
spec/10 §6. สัญญาของ API เขียนว่า `margin/notional ต่อฝั่ง` หน้านี้จึงแสดงเพดาน
**แยกสองฝั่ง** ตามสัญญานั้น ไม่ใช่ยุบเป็นตัวเลขเดียวที่ไม่มีสเปกรองรับ
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import current_mode, get_db, require_cap
from cane.api.templating import templates
from cane.config.settings import Settings, SymbolConfig
from cane.config.validate import ConfigError
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import killswitch as killswitch_repo
from cane.db.repo.decisions import DecisionRecord
from cane.db.repo.users import User
from cane.db.types import store_symbol

router = APIRouter()

#: ข้อความที่ใช้แทนค่าที่ยังคำนวณไม่ได้ · ห้ามใช้ `0` แทน
UNKNOWN = "—"


@dataclass(frozen=True, slots=True)
class Row:
    """หนึ่งบรรทัดของตาราง symbol

    `has_data` แยก "ยังไม่มีบันทึกของเหรียญนี้" ออกจาก "มีบันทึกแล้วแต่ยังไม่มีสัญญาณ"
    — สองอย่างนี้หน้าตาเหมือนกันถ้าดูแค่ `long_signal`/`short_signal`
    """

    pair: str
    market: str
    bucket_long: float
    bucket_short: float | None
    leverage: float
    allow_short: bool
    has_data: bool
    zone: str
    state: str
    close: str
    long_signal: bool
    short_signal: bool
    skip_reason: str | None


def _row(sym: SymbolConfig, found: DecisionRecord | None, *, allow_short: bool) -> Row:
    """`allow_short` ที่ส่งเข้ามาคือ **ผลรวมสองชั้น** แล้ว (spec/07 §`allow_short` มีสองชั้น)

    สวิตช์ระดับระบบปิดแล้ว เหรียญที่เปิดไว้เองก็ยังปิด — สามเหลี่ยม short จึงต้อง
    หายไปจากหน้าจอ ไม่ใช่แสดงแล้วกดไม่ได้
    """
    return Row(
        pair=sym.symbol,
        market=sym.market,
        bucket_long=sym.bucket_quote_long,
        bucket_short=sym.bucket_quote_short,
        leverage=sym.leverage,
        allow_short=allow_short,
        has_data=found is not None,
        zone=found.zone if found else "BLACK",
        state=found.state if found else UNKNOWN,
        close=f"{found.close_px:g}" if found else "รอข้อมูล",
        long_signal=bool(found and found.long_signal),
        short_signal=bool(found and found.short_signal and allow_short),
        skip_reason=found.skip_reason if found else None,
    )


def _rows(conn: Connection, settings: Settings, *, profile: str) -> tuple[Row, ...]:
    latest = decisions_repo.latest_per_symbol(conn, profile, settings.timeframe)
    return tuple(
        _row(
            sym,
            latest.get((sym.market, store_symbol(sym.symbol))),
            allow_short=settings.allow_short and sym.allow_short,
        )
        for sym in settings.symbols
        if sym.enabled
    )


def _pending(rows: tuple[Row, ...]) -> str:
    """เหรียญที่แท่งล่าสุดมีสัญญาณ · ไม่มีบันทึกสักแถว = ยังตอบไม่ได้ ไม่ใช่ศูนย์"""
    if not any(row.has_data for row in rows):
        return UNKNOWN
    return str(sum(1 for row in rows if row.long_signal or row.short_signal))


#: `skip_reason` ที่แปลว่า "เทรนด์เดินไปแล้วแต่แท่งนี้ไม่ใช่จุดสัญญาณ"
#:
#: `late_entry()` รับเฉพาะแท่งที่ `rules.cane.decide()` ปฏิเสธด้วยเหตุนี้ — มันคือ
#: ประตูเดียวที่เข้าเส้นทาง cold start ได้ (`rules/late_entry.py` ด่านที่ 1)
#: จึงใช้เป็นเงื่อนไขของแบนเนอร์ได้ตรงๆ โดยไม่ต้องมีธงใหม่ในฐาน — ซึ่ง spec/10
#: §5. state ที่อยู่ในตาราง ห้ามไว้อยู่แล้ว ("ไม่มี `cold_start_done`")
COLD_START_DOOR = "cane_rule"


def _cap(rows: tuple[Row, ...], *, side: str) -> float:
    if side == "long":
        return sum(row.bucket_long for row in rows)
    return sum(row.bucket_short or 0.0 for row in rows if row.allow_short)


def page_context(conn: Connection, *, profile: str) -> dict[str, object]:
    """ทุกอย่างที่ `partials/overview_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `ov_`

    เรียก `active_settings()` เองแทนที่จะใช้ของที่ `context.build()` เตรียมไว้
    เพราะหน้านี้ต้องแยก "config พัง" ออกจาก "ไม่มีเวอร์ชัน active" ให้คนอ่านเห็น —
    `context.build()` กลืน `ConfigError` ทิ้งโดยเจตนา
    """
    problems = 0
    try:
        settings = config_repo.active_settings(conn, profile)
    except ConfigError as exc:
        settings = None
        problems = len(exc.problems)

    kill = killswitch_repo.read(conn, profile)
    if settings is None:
        return {
            "ov_settings": None,
            "ov_problems": problems,
            "ov_rows": (),
            "ov_kill": kill,
        }

    rows = _rows(conn, settings, profile=profile)
    risk = settings.risk
    return {
        "ov_settings": settings,
        "ov_problems": 0,
        "ov_rows": rows,
        "ov_kill": kill,
        "ov_market_note": " · ".join(sorted({row.market for row in rows})) or UNKNOWN,
        # นับได้จากบันทึก
        "ov_pending": _pending(rows),
        "ov_cold": tuple(row for row in rows if row.skip_reason == COLD_START_DOOR),
        # ต้องรอสถานะไม้จาก venue (ใบ 13)
        "ov_holding": UNKNOWN,
        "ov_margin": UNKNOWN,
        "ov_notional": UNKNOWN,
        "ov_leverage": UNKNOWN,
        # ต้องรอ VIEW บน fills + mark price (spec/10 ห้ามเก็บเป็นตาราง)
        "ov_loss": UNKNOWN,
        "ov_breaker": UNKNOWN,
        # เพดานทั้งหมดมาจาก config เวอร์ชันที่ active — มีของจริงแล้ววันนี้
        "ov_loss_cap": risk.max_daily_loss_pct,
        "ov_breaker_cap": risk.consecutive_loss_breaker,
        "ov_cap_long": _cap(rows, side="long"),
        "ov_cap_short": _cap(rows, side="short"),
    }


@router.get("/partials/overview", response_class=HTMLResponse)
def body(
    request: Request,
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("view_overview")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้าภาพรวมของโหมดที่ดูอยู่

    มีเป็น partial แยกเพราะการสลับโหมดไม่ได้โหลดหน้าใหม่ — มัน swap เฉพาะการ์ด
    PROFILE กับ engine · ถ้าไม่มีเส้นทางนี้ ตัวเลขทั้งหน้าจะค้างอยู่ที่โหมดเดิม
    ทั้งที่หัวข้อด้านบนเปลี่ยนไปแล้ว ซึ่งเป็นภาพที่อ่านผิดได้ตรงๆ
    """
    with db.connect() as conn:
        ctx = page_context(conn, profile=mode)
    return templates.TemplateResponse(request, "partials/overview_body.html", ctx)
