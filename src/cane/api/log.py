"""หน้า บันทึก — ตาราง `decisions` ทั้งโปรไฟล์ พร้อมชิปกรองที่มีตัวนับ (ใบ 24)

## ขอบเขตของหน้านี้คือ `profile` ไม่ใช่ `profile` + `timeframe`

ต่างจากหน้าภาพรวมกับ rail ที่กรองด้วย `timeframe` ของ config เวอร์ชันที่ active ·
สองที่นั้นแสดง **สถานะปัจจุบัน** ส่วนหน้านี้เป็น **บันทึกย้อนหลัง** · ถ้าผูกกับ
timeframe ของเวอร์ชันที่ active วันที่มีใครสลับ `1d` เป็น `1h` บันทึกเก่าทั้งกอง
จะหายจากหน้าจอโดยไม่มีคำอธิบาย และหน้าที่มีไว้ตรวจย้อนหลังจะเชื่อไม่ได้ตั้งแต่นั้น

ผลพลอยได้: หน้านี้เปิดได้แม้ config ของโหมดนั้นโหลดไม่ผ่านหรือไม่มีเวอร์ชัน active
ซึ่งเป็นตอนที่คนอยากอ่านบันทึกที่สุด

## ช่องที่ยังตอบไม่ได้ และเพราะอะไร

| ช่องในไฟล์ design | ต้นทาง | สถานะ |
| --- | --- | --- |
| กำไร/ขาดทุนของขาที่ปิด (`+18.2%`) | fill จริง + ราคาเข้า | ยังไม่มีตารางไม้ — ใบ 13 |
| เหตุผลของ `risk ปฏิเสธ` ระดับชั้น | `decision_risk_checks` | เป็นตารางลูก หน้านี้ไม่อ่านลูก — อยู่ที่หน้า symbol detail (ใบ 25) |
| ปุ่ม `ใบสรุป` · `cold start` | หน้า symbol detail · endpoint cold start ต่อเหรียญ | ยังไม่มีปลายทาง (ใบ 25 · schema ยังไม่ตัดสิน) |

ช่องที่ยังไม่มีต้นทางขึ้นเป็น `—` ไม่ใช่ `0` เหมือนหน้าอื่น

## ทำไมไม่อ่านผ่าน `decisions_for()` ตามที่ใบเขียนไว้

`decisions_for()` อ่านทีละเหรียญและประกอบ `DecisionRecord` เต็มใบผ่าน `_load()`
ซึ่งอ่านลูกครบหกตาราง · หน้านี้อ่านข้ามเหรียญทั้งโปรไฟล์ทีละหน้า และห้าในหกตาราง
นั้นไม่มีช่องให้แสดงเลย · `decisions_repo.journal()` จึงเป็นตัวอ่านเฉพาะหัว
(บวก `decision_flip` ที่เป็นลูกหนึ่งต่อหนึ่ง) ตามที่ใบ 22 ฝากไว้ให้ใบ 24
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import current_mode, get_db, require_cap
from cane.api.templating import templates
from cane.db.repo import decisions as decisions_repo
from cane.db.repo.decisions import CHIPS, JournalRow
from cane.db.repo.users import User

router = APIRouter()

#: ข้อความที่ใช้แทนค่าที่ยังคำนวณไม่ได้ · ห้ามใช้ `0` แทน (เหมือน `api/overview.py`)
UNKNOWN = "—"

#: กี่แถวต่อหนึ่งหน้า · ปุ่ม `แสดงเพิ่ม` ต่อท้ายทีละชุดเท่านี้
PAGE_SIZE = 50

#: ป้ายไทยของชิป · คีย์ตรงกับ `decisions_repo.CHIPS` และเป็นค่าของ `?chip=`
#:
#: อยู่ที่นี่ไม่ใช่ที่ repo เพราะ repo ตอบได้แค่ว่า "แถวไหนเข้าพวก" ส่วนคำที่หน้าจอ
#: เรียกมันเป็นเรื่องของชั้นที่ render
CHIP_LABELS: dict[str, str] = {
    "all": "ทั้งหมด",
    "orders": "มีออเดอร์",
    "long": "ฝั่ง long",
    "short": "ฝั่ง short",
    "flip": "กลับข้าง",
    "risk": "risk ปฏิเสธ",
    "llm": "LLM ตอบไม่ได้",
    "capped": "ถูกเพดานตัด",
}

#: `skip_reason` → ประโยคที่คนอ่านรู้เรื่อง · ชุดปิดเดียวกับ `ck_decisions_skip_reason`
#:
#: ค่าที่ไม่อยู่ในนี้แสดงเป็นชื่อดิบ ไม่ใช่ช่องว่าง — วันที่ใบ 12 เพิ่มค่าใหม่เข้าชุด
#: หน้าจอต้องบอกว่ามีอะไรที่ยังไม่ได้แปล ไม่ใช่กลืนแถวนั้นหายไปเงียบๆ
SKIP_TEXT: dict[str, str] = {
    "flip_aborted": "กลับข้างไม่สำเร็จ",
    "no_signal": "ไม่ใช่แท่งสัญญาณ — ไม่ทำอะไร",
    "already_positioned": "ถือฝั่งนี้อยู่แล้ว — ไม่เปิดทับ",
    "short_disabled": "ฝั่ง short ปิดอยู่",
    "cane_rule": "กฎไม้เรียวปฏิเสธ — เทรนด์เดินไปแล้วแต่แท่งนี้ไม่ใช่จุดสัญญาณ",
    "rr_too_low": "RR ไม่ถึงเกณฑ์ของ cold start",
    "risk_rejected": "risk ปฏิเสธ — ไม่เกิดไม้",
    "order_error": "ส่งออเดอร์ไม่สำเร็จ",
    "dry_run": "โหมดทดลอง — ไม่ส่งคำสั่งจริง",
}

_OPPOSITE = {"long": "short", "short": "long"}


@dataclass(frozen=True, slots=True)
class Chip:
    """ชิปหนึ่งใบพร้อมตัวนับของมัน · `count` มาจาก SQL ไม่ใช่จากแถวที่โหลดมา"""

    key: str
    label: str
    count: int
    active: bool


@dataclass(frozen=True, slots=True)
class Line:
    """หนึ่งบรรทัดของตาราง — ข้อความเตรียมไว้แล้ว เทมเพลตเหลือแค่เลือกคลาส

    `tint` เป็น `"long"` / `"short"` / `""` — ย้อมตาม **ฝั่งที่แท่งนั้นลงไม้**
    เขียวคือ long แดงคือ short · แท่งที่ไม่ได้ลงไม้ไม่ย้อม ไม่ว่าจะมีสัญญาณหรือไม่

    **`dry_run` นับว่าลงไม้** ทั้งที่ไม่มีคำสั่งออกไปจริง เพราะ
    `ck_config_settings_paper_dry_run` บังคับ `dry_run = true` ให้ paper ตายตัว —
    ถ้านับเฉพาะ `skip_reason IS NULL` บันทึกของ paper จะไม่มีแถวไหนได้สีเลยสักแถว
    ทั้งที่มันคือโปรไฟล์ที่จะมีข้อมูลก่อน · ไฟล์ design ก็ย้อมแถว dry-run สองแถวแรก

    แท่งที่ risk ปฏิเสธ **ไม่ย้อม** ถึงไฟล์ design จะย้อมแดงไว้ — สีของแถวตอบว่า
    "ลงไม้ฝั่งไหน" ส่วน "ถูกปฏิเสธ" เป็นคนละแกน และคอลัมน์ `ผลลัพธ์` บอกด้วย
    ตัวอักษรสีแดงอยู่แล้ว · ย้อมทั้งแถวแดงให้แท่งที่ไม่มีไม้เกิดขึ้นคืออ่านผิดได้ตรงๆ
    """

    bar: str
    pair: str
    market: str
    zone: str
    side: str
    signal: str
    signal_side: str
    factors_kind: str
    factors_filled: int
    factors_side: str
    size_from: str
    size_to: str
    capped: bool
    outcome: str
    outcome_kind: str
    tint: str


def _bar(ts: int) -> str:
    """`MM-DD` แบบ UTC ตามไฟล์ design

    UTC ไม่ใช่เวลาเครื่อง — แท่งปิดที่ขอบวัน UTC (`utc_day` ในสคีมามีไว้เพราะเรื่อง
    เดียวกัน) · แปลงเป็นเวลาท้องถิ่นเมื่อไหร่ แท่งของวันที่ 26 จะขึ้นเป็นวันที่ 25
    หรือ 26 แล้วแต่เครื่องที่เปิดดู ซึ่งทำให้เทียบกับ log ของ engine ไม่ได้
    """
    return f"{datetime.fromtimestamp(ts / 1000, tz=UTC):%m-%d}"


def _num(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:g}"


def _factors(row: JournalRow) -> tuple[str, int, str]:
    """`ไม่เรียก` / `ไม้พื้นฐาน` / ช่องสามช่อง — สามสถานะที่ห้ามปนกัน

    `llm_fallback` ตอบว่า "input จริงหรือเปล่า" ส่วน `judge_called` ตอบว่า
    "ถามหรือเปล่า" (ดูคอมเมนต์ที่คอลัมน์ `llm_fallback` ใน schema) · ถ้าย่อสองอย่างนี้
    เป็นช่องว่างเปล่าเหมือนกัน คนอ่านจะแยก "LLM บอกว่าไม่มีปัจจัย" ออกจาก
    "LLM ตอบไม่ได้" ไม่ได้ ซึ่งเป็นเหตุผลที่คอลัมน์นั้นมีอยู่ตั้งแต่แรก
    """
    if row.llm_fallback:
        return "fallback", 0, ""
    if not row.judge_called:
        return "none", 0, ""
    return "boxes", row.factors_present or 0, row.side or "long"


def _size(row: JournalRow) -> tuple[str, str]:
    """`45 → 45` หรือ `100` (ขีดฆ่า) `→ 50` + badge `เพดาน`

    ไม่มีสูตรเลย = แท่งที่ไม่ได้คิดขนาด (ไม่มีสัญญาณ) ไม่ใช่ขนาดศูนย์
    """
    if row.size_pct_formula is None and row.size_pct_final is None:
        return UNKNOWN, ""
    return _num(row.size_pct_formula), _num(row.size_pct_final)


def _outcome(row: JournalRow) -> tuple[str, str]:
    """ประโยคในคอลัมน์ `ผลลัพธ์` กับชนิดของมัน (`good` / `bad` / `warn` / `""`)

    การกลับข้างบอก **ทั้งสองขาในบรรทัดเดียว** ตามใบ · กำไรของขาที่ปิดยังไม่มี
    ต้นทาง (ต้องมี fill กับราคาเข้า — ใบ 13) จึงไม่มีอยู่ในประโยคเลย ดีกว่าใส่
    ช่องว่างที่ดูเหมือนศูนย์เปอร์เซ็นต์
    """
    if row.flip_aborted:
        side = row.flip_residual_side or UNKNOWN
        return (
            f"กลับข้างไม่สำเร็จ — ปิดได้ {_num(row.flip_close_qty)} "
            f"ค้าง {_num(row.flip_residual_qty)} ฝั่ง {side}",
            "bad",
        )
    if row.flip_close_qty is not None and row.side:
        closed = _OPPOSITE.get(row.side, UNKNOWN)
        return (
            f"ปิด {closed} {_num(row.flip_close_qty)} · "
            f"เปิด {row.side} {_num(row.qty)} {row.symbol.split('/')[0]}",
            "",
        )
    if row.skip_reason is not None:
        kind = "bad" if row.skip_reason in ("risk_rejected", "order_error") else ""
        return SKIP_TEXT.get(row.skip_reason, row.skip_reason), kind
    opened = f"เปิด {row.side or UNKNOWN} {_num(row.qty)} {row.symbol.split('/')[0]}"
    if row.margin is not None:
        opened += f" · margin {row.margin:.2f}"
    if row.llm_fallback:
        return f"{opened} — LLM ตอบไม่ได้ ตกมาที่ไม้พื้นฐาน", "warn"
    return opened, "good"


def _line(row: JournalRow) -> Line:
    kind, filled, factors_side = _factors(row)
    size_from, size_to = _size(row)
    outcome, outcome_kind = _outcome(row)
    # `dry_run` นับว่าลงไม้ด้วย — ดูเหตุผลที่ docstring ของ `Line`
    acted = row.skip_reason in (None, "dry_run")
    if row.long_signal:
        signal, signal_side = "เปิด long", "long"
    elif row.short_signal:
        signal, signal_side = "เปิด short", "short"
    else:
        signal, signal_side = UNKNOWN, ""
    return Line(
        bar=_bar(row.bar_close_ts),
        pair=row.symbol,
        market=row.market,
        zone=row.zone,
        side=(row.side or "flat").upper(),
        signal=signal,
        signal_side=signal_side,
        factors_kind=kind,
        factors_filled=filled,
        factors_side=factors_side,
        size_from=size_from,
        size_to=size_to,
        capped=bool(row.capped),
        outcome=outcome,
        outcome_kind=outcome_kind,
        tint=(row.side or "") if acted else "",
    )


def _chip(chip: str) -> str:
    """ชิปที่ไม่รู้จักถอยไปที่ `ทั้งหมด` ไม่ใช่ 404

    query string เป็นของที่คนแก้เองได้และลิงก์เก่าก็ค้างได้ · หน้าที่ตอบ 404 ให้
    ค่ากรองที่สะกดผิดคือหน้าที่อ่านบันทึกไม่ได้เพราะเรื่องที่ไม่สำคัญ
    """
    return chip if chip in CHIP_LABELS else "all"


def _page(
    conn: Connection,
    *,
    profile: str,
    chip: str,
    before: tuple[int, int] | None,
) -> tuple[tuple[Line, ...], tuple[int, int] | None]:
    """หนึ่งหน้า พร้อมกุญแจของแถวถัดไป (`None` = หมดแล้ว)

    ขอมาเกินหนึ่งแถวเพื่อรู้ว่ายังมีต่อไหม — ถูกกว่าการ `count(*)` ซ้ำทุกครั้งที่
    กดแสดงเพิ่ม และตอบคำถามเดียวที่ปุ่มนั้นต้องรู้
    """
    rows = decisions_repo.journal(
        conn, profile, chip=chip, before=before, limit=PAGE_SIZE + 1
    )
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    cursor = (rows[-1].bar_close_ts, rows[-1].id) if more else None
    return tuple(_line(row) for row in rows), cursor


def rows_context(
    conn: Connection,
    *,
    profile: str,
    chip: str,
    before: tuple[int, int] | None = None,
) -> dict[str, object]:
    """เฉพาะแถวกับปุ่มแสดงเพิ่ม — ใช้ทั้งตอน render หน้าเต็มและตอนต่อท้าย"""
    chip = _chip(chip)
    lines, cursor = _page(conn, profile=profile, chip=chip, before=before)
    return {
        "lg_chip": chip,
        "lg_lines": lines,
        "lg_next": cursor,
    }


def page_context(conn: Connection, *, profile: str, chip: str = "all") -> dict[str, object]:
    """ทุกอย่างที่ `partials/log_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `lg_`"""
    chip = _chip(chip)
    counts = decisions_repo.journal_counts(conn, profile)
    return rows_context(conn, profile=profile, chip=chip) | {
        "lg_chips": tuple(
            Chip(key=key, label=CHIP_LABELS[key], count=counts[key], active=key == chip)
            for key in CHIPS
        ),
        "lg_total": counts["all"],
    }


@router.get("/partials/log", response_class=HTMLResponse)
def body(
    request: Request,
    chip: str = Query("all"),
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("read_decisions")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้าบันทึก — ทั้งตอนสลับโหมดและตอนกดชิป

    ชิปกดแล้ว swap ทั้งก้อนไม่ใช่แค่แถว เพราะตัวนับของทุกชิปไม่เปลี่ยน แต่ชิปที่
    active เปลี่ยน และ cursor ของปุ่มแสดงเพิ่มต้องเริ่มใหม่ทั้งชุด
    """
    with db.connect() as conn:
        ctx = page_context(conn, profile=mode, chip=chip)
    return templates.TemplateResponse(request, "partials/log_body.html", ctx)


@router.get("/partials/log/rows", response_class=HTMLResponse)
def rows(
    request: Request,
    chip: str = Query("all"),
    before_ts: int = Query(...),
    before_id: int = Query(...),
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("read_decisions")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """หน้าถัดไป — ต่อท้ายแถวเดิม ไม่ทับ (`hx-swap="beforeend"` ที่ปุ่ม)"""
    with db.connect() as conn:
        ctx = rows_context(
            conn, profile=mode, chip=chip, before=(before_ts, before_id)
        )
    return templates.TemplateResponse(request, "partials/log_rows.html", ctx)
