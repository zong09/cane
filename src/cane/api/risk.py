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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import current_mode, get_db, require_cap
from cane.api.templating import templates
from cane.config.settings import Settings, SymbolConfig
from cane.config.validate import ConfigError
from cane.data.exchange import default_type
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import killswitch as killswitch_repo
from cane.db.repo.users import User
from cane.db.types import store_symbol

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


def page_context(conn: Connection, *, profile: str) -> dict[str, object]:
    """ทุกอย่างที่ `partials/risk_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `risk_`

    เรียก `active_settings()` เองแบบเดียวกับหน้าภาพรวม เพราะ `context.build()` กลืน
    `ConfigError` ทิ้ง และหน้านี้ต้องแยก "config พัง" ออกจาก "ไม่มีเวอร์ชัน active"
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
            "risk_profile": profile,
            "risk_settings": None,
            "risk_problems": problems,
            "risk_kill": kill,
            "risk_rows": (),
        }

    rows = _rows(settings)
    return {
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
    _: User = Depends(require_cap("view_overview")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้าความเสี่ยงของโหมดที่ดูอยู่

    สิทธิ์เป็น `view_overview` ตัวเดียวกับหน้าภาพรวม เพราะตารางสิทธิ์ของ spec/09
    ผูก `GET /api/{profile}/overview` กับ `/risk` ไว้ในแถวเดียวกัน

    มีเป็น partial แยกด้วยเหตุผลเดียวกับหน้าภาพรวม — การสลับโหมด swap แค่การ์ด
    PROFILE กับ engine ถ้าไม่มีเส้นทางนี้ เพดานทั้งหน้าจะค้างอยู่ที่โหมดเดิม
    """
    with db.connect() as conn:
        ctx = page_context(conn, profile=mode)
    return templates.TemplateResponse(request, "partials/risk_body.html", ctx)
