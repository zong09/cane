"""หน้า ตั้งค่า — ฟอร์มต่อฟิลด์ของ config เวอร์ชันที่เปิดใช้อยู่ (ใบ 21)

ใบนี้เคยถูกออกแบบไว้เป็น **ตัวดูไฟล์ TOML** พร้อม badge เลขบรรทัด `L4 / L19`
ข้างรายการที่ต้องแก้ · config ย้ายลงตารางใน DB แล้ว (`decisions.md` ข้อ 18/22)
จึงไม่มีไฟล์ให้แสดงและไม่มีบรรทัดให้ชี้ · สิ่งที่แทนคือ **path ของฟิลด์**
ซึ่ง `render_loc()` ผลิตให้อยู่แล้วและตรงกับชื่อ `name` ของช่องกรอกในฟอร์ม
(spec/07 §path ของฟิลด์ที่ผิด เขียน errata ข้อนี้ไว้ตรงๆ)

## ทำไมต้องเรียก `active_settings()` เองที่นี่

`context.build()` กลืน `ConfigError` ทิ้งโดยเจตนา — config ที่พังต้องทำให้ชิป
เปลี่ยน ไม่ใช่ทำให้ทั้งคอนโซลเป็น 500 · แต่หน้านี้เป็นหน้าที่มีไว้ **แก้** ของที่พัง
มันจึงต้องเห็น `exc.problems` ของจริง ไม่ใช่เห็นแค่ `None`

สถานะมีสามแบบ ไม่ใช่สองแบบอย่างที่ไฟล์ design วาดไว้:

| `active_settings()` | แปลว่า |
| --- | --- |
| `None` | ยังไม่มีเวอร์ชันที่เปิดใช้ — **ไม่เทรด** และไม่มีอะไรให้ลอกไปแก้ |
| raise `ConfigError` | มีเวอร์ชันที่เปิดใช้ แต่กฎเข้มขึ้นทีหลังจนมันไม่ผ่านแล้ว |
| คืน `Settings` | ปกติ |

## สิทธิ์

เปิดหน้าได้ด้วย `view_overview` เหมือนทุกหน้า · การแก้เป็น `edit_profile` ซึ่ง
spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role ให้ OWNER คนเดียว · `cfg_can_edit`
ตัดสินแค่ว่าช่องกรอกถูก `disabled` ไหม ด่านจริงอยู่ที่ dependency ของ POST
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import (
    client_ip,
    current_mode,
    get_db,
    get_sup,
    require_cap,
    require_profile,
)
from cane.api.templating import templates
from cane.auth import service
from cane.config import diff as config_diff
from cane.config.settings import Loc, Settings
from cane.config.validate import ConfigError, Problem, validate_settings
from cane.db.repo import audit
from cane.db.repo import config as config_repo
from cane.db.repo import permissions as perms
from cane.db.repo import users as users_repo
from cane.db.repo.users import User
from cane.db.types import now_ms
from cane.engine.state import PROFILES
from cane.engine.supervisor import Supervisor

router = APIRouter()


@dataclass(frozen=True, slots=True)
class Tab:
    """แท็บหนึ่งโปรไฟล์ · `running` มาจาก engine ไม่ใช่จากโหมดของ session

    ไฟล์ design รวมสองอย่างนี้เป็นอันเดียว แต่มันแยกกันทันทีที่มีคนเปิดแท็บ live
    ตอน engine ของ live หยุดอยู่ — ชิป `กำลังทำงาน` ต้องพูดถึง engine
    """

    profile: str
    running: bool
    current: bool


@dataclass(frozen=True, slots=True)
class VersionRow:
    """หนึ่งบรรทัดในประวัติ · `who` เป็น `—` เมื่อไม่ใช่คนกด (seed หรือ migration)"""

    id: int
    version: int
    source: str
    note: str
    when: str
    who: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class FormField:
    """ช่องหนึ่งช่องในฟอร์ม · `path` คือทั้งชื่อ `name` และคีย์ของ error

    ประกอบที่ฝั่ง Python ไม่ใช่เขียนซ้ำ 16 บล็อกในเทมเพลต เพราะชื่อช่องต้องตรงกับ
    `render_loc()` เป๊ะ ที่เดียวที่รู้เรื่องนั้นคือโค้ด ไม่ใช่ HTML
    """

    path: str
    label: str
    value: str
    kind: str = "text"  # text | number | select | readonly
    options: tuple[tuple[str, str], ...] = ()
    hint: str = ""
    error: Problem | None = field(default=None)


#: ค่าที่ฐานยอมรับจริง (`ck_config_settings_timeframe`) — **ไม่ใช่ที่ mockup วาดไว้**
#: `Settings.timeframe` เป็น `str` เปล่า ค่าอย่าง `4h` จึงผ่าน validator แล้วไปตาย
#: ที่ INSERT · ปิดช่องด้วย select ตั้งแต่ต้นทาง
TIMEFRAMES = (("1d", "1d — รายวัน"), ("1h", "1h — ราย 1 ชั่วโมง"))

COLD_STARTS = (
    ("", "ไม่เข้าเส้นทาง cold start"),
    ("wait_1h", "wait_1h"),
    ("trailing", "trailing"),
    ("skip", "skip"),
)

BROKER_KINDS = (("ccxt", "ccxt"), ("paper", "paper"))
MARGIN_MODES = (("isolated", "isolated"), ("cross", "cross"))


def _text(value: object) -> str:
    """`None` เป็นช่องว่าง ไม่ใช่ `"None"` · `bool` ไม่เคยมาถึงตรงนี้"""
    return "" if value is None else str(value)


def _when(ts: int) -> str:
    """เวลาแบบที่คอนโซลแสดง — พ.ศ. และเวลาท้องถิ่นของเครื่องที่รัน"""
    moment = datetime.fromtimestamp(ts / 1000)
    return f"{moment.day}/{moment.month}/{moment.year + 543} {moment:%H:%M}"


def _groups(
    settings: Settings | None, errors: Mapping[str, Problem] | None = None
) -> tuple[tuple[str, tuple[FormField, ...]], ...]:
    """ช่องกรอกทั้งหมด จัดกลุ่มตามหัวข้อที่ design แบ่งไว้

    ไม่มีเวอร์ชัน active = ไม่มีค่าให้ลอก จึงคืนกลุ่มว่าง — ฟอร์มเปล่าที่กรอกได้
    จะกลายเป็นการสร้าง config จากศูนย์ ซึ่งไม่ใช่สิ่งที่หน้านี้ทำ (ทางเข้าครั้งแรก
    คือ `cane db seed` ตาม spec/07 §Config profile)

    `symbols` ไม่อยู่ที่นี่เลย — แก้เหรียญเป็นของใบ 26 · `dry_run` กับ `allow_short`
    ก็ไม่อยู่ ปุ่มสลับสองตัวนั้นเป็นของใบ 23
    """
    if settings is None:
        return ()

    risk, broker = settings.risk, settings.broker
    groups = (
        (
            "ทั่วไป",
            (
                FormField("timeframe", "timeframe", settings.timeframe, "select", TIMEFRAMES),
                FormField(
                    "cold_start",
                    "cold_start",
                    _text(settings.cold_start),
                    "select",
                    COLD_STARTS,
                ),
                FormField(
                    "base_pct",
                    "base_pct",
                    _text(settings.base_pct),
                    "number",
                    hint="ต้องอยู่ในช่วง 5–20",
                ),
            ),
        ),
        (
            "risk limit",
            (
                FormField(
                    "risk.max_position_pct_long",
                    "max_position_pct_long",
                    _text(risk.max_position_pct_long),
                    "number",
                ),
                FormField(
                    "risk.max_position_pct_short",
                    "max_position_pct_short",
                    _text(risk.max_position_pct_short),
                    "number",
                ),
                FormField(
                    "risk.max_leverage", "max_leverage", _text(risk.max_leverage), "number"
                ),
                FormField(
                    "risk.min_liq_buffer_pct",
                    "min_liq_buffer_pct",
                    _text(risk.min_liq_buffer_pct),
                    "number",
                ),
                FormField(
                    "risk.max_daily_loss_pct",
                    "max_daily_loss_pct",
                    _text(risk.max_daily_loss_pct),
                    "number",
                ),
                FormField(
                    "risk.consecutive_loss_breaker",
                    "consecutive_loss_breaker",
                    _text(risk.consecutive_loss_breaker),
                    "number",
                ),
            ),
        ),
        (
            "broker",
            (
                FormField("broker.kind", "kind", broker.kind, "select", BROKER_KINDS),
                FormField(
                    "broker.exchange",
                    "exchange",
                    _text(broker.exchange),
                    hint="บังคับเมื่อ kind = ccxt",
                ),
                FormField(
                    "broker.margin_mode",
                    "margin_mode",
                    broker.margin_mode,
                    "select",
                    MARGIN_MODES,
                ),
                FormField(
                    "broker.position_mode",
                    "position_mode",
                    broker.position_mode,
                    "readonly",
                    hint="ระบบรองรับ one_way อย่างเดียว — hedge ละเมิดหลักหนึ่งฝั่งต่อเหรียญ",
                ),
                FormField(
                    "broker.seed_quote",
                    "seed_quote",
                    _text(broker.seed_quote),
                    "number",
                    hint="เฉพาะ kind = paper",
                ),
                FormField(
                    "broker.taker_fee_pct",
                    "taker_fee_pct",
                    _text(broker.taker_fee_pct),
                    "number",
                    hint="เฉพาะ kind = paper",
                ),
                FormField(
                    "broker.maintenance_margin_pct",
                    "maintenance_margin_pct",
                    _text(broker.maintenance_margin_pct),
                    "number",
                    hint="เฉพาะ kind = paper",
                ),
            ),
        ),
        ("ข้อมูลราคา", (FormField("data.exchange", "exchange", settings.data.exchange),)),
    )
    if not errors:
        return groups
    # แขวน `Problem` ไว้กับช่องของมันเอง — เทมเพลตจึงไม่ต้องรู้จัก path
    return tuple(
        (title, tuple(replace(f, error=errors.get(f.path)) for f in fields))
        for title, fields in groups
    )


def _switches(settings: Settings | None) -> tuple[tuple[str, str, str], ...]:
    """`dry_run` / `allow_short` แสดงอย่างเดียวที่หน้านี้ — ปุ่มสลับอยู่ที่หน้าความเสี่ยง

    สองค่านี้มี endpoint ของตัวเองที่ต้อง step-up (spec/10 §เขียน) ต่างจากช่องอื่นใน
    หน้านี้ที่บันทึกเป็นร่างได้โดยไม่ต้องยืนยันซ้ำ · การใส่ปุ่มไว้ที่นี่ด้วยแปลว่ามีสอง
    ทางเข้าที่ด่านไม่เท่ากันไปหาค่าเดียวกัน

    `paper` สลับ `dry_run` ไม่ได้เลยไม่ว่าจะกดจากที่ไหน เพราะ CHECK
    `ck_config_settings_paper_dry_run` ปฏิเสธที่ฐาน
    """
    if settings is None:
        return ()
    forced = (
        "paper บังคับ true ที่ฐาน (ck_config_settings_paper_dry_run)"
        if settings.profile == "paper"
        else "สลับได้ที่หน้า ความเสี่ยง"
    )
    return (
        ("dry_run", str(settings.dry_run).lower(), forced),
        ("allow_short", str(settings.allow_short).lower(), "สวิตช์ระดับระบบ — สลับได้ที่หน้า ความเสี่ยง"),
    )


def _history(conn: Connection, profile: str) -> tuple[VersionRow, ...]:
    names = {person.id: person.name for person in users_repo.everyone(conn)}
    return tuple(
        VersionRow(
            id=head.id,
            version=head.version,
            source=head.source,
            note=head.note or "",
            when=_when(head.created_ts),
            who=names.get(head.created_by_user_id or -1, "—"),
            is_active=head.is_active,
        )
        for head in config_repo.versions(conn, profile)
    )


#: ช่องที่ฟอร์มแก้ได้ → วิธีอ่านค่าจากสตริงที่ browser ส่งมา
#:
#: **ต้องแปลงเองก่อนส่งเข้า `validate_settings()`** ไม่ใช่โยนสตริงดิบเข้าไปแล้วให้
#: pydantic coerce ให้ · `cross_checks()` ทำงานกับ dict ดิบและใช้ `_is_number()`
#: ซึ่งคืน `False` ให้ `"0.5"` — กฎ `leverage` ของเหรียญเทียบ `max_leverage` จะเงียบ
#: ไปทั้งข้อ และนั่นเป็น **กฎเดียวที่ฐานเขียนเป็น CHECK ไม่ได้** (spec/07 §กฎการตรวจ config)
#:
#: `symbols` ไม่อยู่ในนี้ (ใบ 26) · `profile` ไม่อยู่เลย เวอร์ชันใหม่เป็นของ profile เดิมเสมอ
#:
#: **`dry_run` กับ `allow_short` ต้องไม่อยู่ในนี้** — สองค่านั้นมี endpoint ของตัวเองที่
#: ต้อง step-up (spec/10 §เขียน) · ใส่ลงที่นี่เมื่อไหร่ ฟอร์มของหน้าตั้งค่าซึ่งไม่ต้อง
#: ยืนยันซ้ำจะส่งมันมาได้ และด่านที่สเปกแยกไว้ก็หายไปเงียบๆ
EDITABLE: dict[str, str] = {
    "timeframe": "text",
    "cold_start": "text?",
    "base_pct": "number",
    "risk.max_position_pct_long": "number",
    "risk.max_position_pct_short": "number",
    "risk.max_leverage": "number",
    "risk.min_liq_buffer_pct": "number",
    "risk.max_daily_loss_pct": "number",
    "risk.consecutive_loss_breaker": "whole",
    "broker.kind": "text",
    "broker.exchange": "text?",
    "broker.margin_mode": "text",
    "broker.seed_quote": "number?",
    "broker.taker_fee_pct": "number?",
    "broker.maintenance_margin_pct": "number?",
    "data.exchange": "text",
}


def _coerce(text: str, kind: str) -> object:
    """สตริงหนึ่งช่อง → ค่าที่ `validate_settings()` อ่านรู้เรื่อง

    ช่องว่างของค่าที่ **ไม่บังคับ** เป็น `None` · ช่องว่างของค่าที่ **บังคับ** คืน
    `_ABSENT` เพื่อให้ผู้เรียกลบคีย์นั้นทิ้ง แล้ว pydantic รายงานว่า "ขาด" ที่ฟิลด์นั้น
    ซึ่งเป็นข้อความที่ตรงกว่า "ค่าต้องเป็นตัวเลข" ของค่าว่าง

    `bool` รับแค่ `"true"`/`"false"` ตรงตัว ไม่ใช่ความจริงเชิง truthiness — ค่าที่สะกด
    อย่างอื่นต้องดังตรงนี้ ไม่ใช่เงียบแล้วกลายเป็น `False` ซึ่งกับสวิตช์ `dry_run`
    แปลว่า "ยิงจริง" (ใบ 23)
    """
    if kind == "bool":
        if text not in ("true", "false"):
            raise ValueError(f"{text!r} ไม่ใช่ true หรือ false")
        return text == "true"
    if text == "":
        return None if kind.endswith("?") else _ABSENT
    if kind.startswith("number"):
        return float(text)
    if kind == "whole":
        return int(text)
    return text


#: ค่าที่แปลว่า "ลบคีย์นี้ทิ้ง" — `None` ใช้แทนไม่ได้เพราะมันเป็นค่าที่ถูกต้องของ
#: ฟิลด์ที่เว้นว่างได้
_ABSENT = object()


def _dig(raw: dict, loc: Loc) -> dict | None:
    """เดินลงไปที่ dict ที่ถือคีย์สุดท้ายของ `loc` · ไม่มีทางเดินไป = `None`"""
    node: object = raw
    for part in loc[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def patched(
    base: Settings, form: Mapping[str, str], *, editable: Mapping[str, str] = EDITABLE
) -> tuple[dict, list[Problem]]:
    """ลอกค่าของเวอร์ชันที่เปิดใช้อยู่ แล้วทับเฉพาะช่องที่ฟอร์มส่งมา

    ลอกทั้งชุดไม่ใช่ประกอบใหม่จากฟอร์ม เพราะฟอร์มไม่มี `symbols` (เป็นของใบ 26) —
    เวอร์ชันใหม่ต้องพาของพวกนั้นไปด้วยครบถ้วน

    `editable` เป็นพารามิเตอร์เพราะ **ทางเข้าคนละทางรับคนละช่อง** — หน้าความเสี่ยง
    (ใบ 23) สลับ `dry_run`/`allow_short` ผ่าน endpoint ของตัวเองที่มีสิทธิ์ต่างกัน
    (spec/10 §เขียน) · ใส่สองช่องนั้นลง `EDITABLE` แทนจะทำให้ฟอร์มหน้าตั้งค่าซึ่ง
    **ไม่ต้อง step-up** ส่งมันมาได้ด้วย ซึ่งเป็นการยกเลิกการแยกทางเข้าที่สเปกจงใจทำ

    คืนค่าดิบที่พร้อมส่งเข้า `validate_settings()` กับรายการปัญหาของ **การแปลงค่า**
    ซึ่งเป็นคนละชั้นกับปัญหาของกฎ · ตัวเลขที่พิมพ์ผิดต้องชี้ที่ช่องนั้น ไม่ใช่โผล่มา
    เป็น 500 จากชั้นที่ลึกกว่า
    """
    raw = deepcopy(base.model_dump())
    problems: list[Problem] = []

    for path, kind in editable.items():
        if path not in form:
            continue
        loc: Loc = tuple(path.split("."))
        holder = _dig(raw, loc)
        if holder is None:
            continue
        try:
            value = _coerce(form[path].strip(), kind)
        except ValueError:
            problems.append(
                Problem(loc, f"{loc[-1]} = {form[path]!r} ไม่ใช่ตัวเลข", "ช่องนี้รับตัวเลขเท่านั้น")
            )
            continue
        if value is _ABSENT:
            holder.pop(loc[-1], None)
        else:
            holder[loc[-1]] = value

    return raw, problems


def _compare(
    conn: Connection, settings: Settings | None, *, profile: str, other: str
) -> tuple[tuple[config_diff.Row, ...], str]:
    """ตารางเทียบกับอีกโปรไฟล์ · คืนรายการที่ต่าง หรือเหตุผลที่เทียบไม่ได้

    อีกโปรไฟล์มีสามสถานะเหมือนกันทุกประการ และสองในสามเทียบไม่ได้ · ต้องแยก
    ข้อความให้ชัดว่าเป็น "ยังไม่มีเวอร์ชัน" หรือ "มีแต่โหลดไม่ผ่าน" เพราะสองอันนี้
    ต้องไปแก้คนละที่ (แบบเดียวกับที่ `context.live_warning()` แยกไว้แล้ว)
    """
    if settings is None:
        return (), f"{profile} ยังไม่มีเวอร์ชันที่เปิดใช้ — เทียบไม่ได้"
    try:
        theirs = config_repo.active_settings(conn, other)
    except ConfigError as exc:
        return (), f"{other} โหลดไม่ผ่าน {len(exc.problems)} ข้อ — เทียบไม่ได้"
    if theirs is None:
        return (), f"{other} ยังไม่มีเวอร์ชัน active — เทียบไม่ได้"
    return tuple(config_diff.diff(settings, theirs)), ""


def page_context(
    conn: Connection,
    sup: Supervisor,
    *,
    profile: str,
    user: User,
    mode: str,
    errors: Mapping[str, Problem] | None = None,
    unplaced: tuple[Problem, ...] = (),
    saved: str = "",
    saved_note: str = "",
) -> dict[str, object]:
    """ทุกอย่างที่ `partials/config_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `cfg_` ทั้งหมด

    หน้าเต็มรวม dict นี้เข้ากับ `context.build()` ซึ่งมีคีย์อย่าง `symbols` และ
    `mode` อยู่แล้ว — prefix กันชนกันเงียบๆ ไม่ใช่เพื่อความสวย
    """
    problems: tuple[Problem, ...] = ()
    try:
        settings = config_repo.active_settings(conn, profile)
    except ConfigError as exc:
        settings = None
        problems = tuple(exc.problems)

    found = dict(errors or {})
    running = {view.profile: view.should_run for view in sup.status(conn)}
    other = PROFILES[1] if profile == PROFILES[0] else PROFILES[0]
    rows, diff_note = _compare(conn, settings, profile=profile, other=other)
    return {
        "cfg_profile": profile,
        "cfg_other": other,
        "cfg_diff": rows,
        "cfg_diff_note": diff_note,
        "cfg_tabs": tuple(
            Tab(name, running.get(name, False), name == profile) for name in PROFILES
        ),
        "cfg_off_mode": profile != mode,
        "cfg_settings": settings,
        "cfg_problems": problems,
        "cfg_no_version": settings is None and not problems,
        "cfg_groups": _groups(settings, found),
        "cfg_errors": found,
        "cfg_unplaced": unplaced,
        "cfg_saved": saved,
        "cfg_saved_note": saved_note,
        "cfg_oob": False,
        "cfg_switches": _switches(settings),
        "cfg_history": _history(conn, profile),
        "cfg_can_edit": perms.allowed(conn, role=user.role, cap="edit_profile"),
    }


@router.get("/partials/config/{profile}", response_class=HTMLResponse)
def body(
    profile: str,
    request: Request,
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    user: User = Depends(require_cap("view_overview")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้า — ใช้เป็นทั้งการสลับแท็บและปุ่ม `ตรวจอีกครั้ง`

    ปุ่ม `ตรวจอีกครั้ง` ไม่มี endpoint ของตัวเองเพราะไม่มีอะไรให้ "สั่งตรวจ" —
    `settings_of()` validate ทุกครั้งที่อ่านอยู่แล้ว การขอเนื้อหน้าใหม่จึงคือ
    การตรวจใหม่ · และสถานะ "โหลดไม่ผ่าน" เกิดได้ทางเดียวคือเวอร์ชันที่บันทึกไว้
    ก่อนกฎจะเข้มขึ้น เพราะค่าที่ไม่ผ่านลงฐานไม่ได้ตั้งแต่ต้น (spec/07 §กฎการตรวจ config)

    อยู่ใต้ `/partials/` ไม่ใช่ `/api/` — `/api/` เป็นรายการที่ผูกกับตารางสิทธิ์ของ
    spec/09 การเพิ่ม endpoint ฝั่ง HTML เข้าไปจะทำให้สองเอกสารไม่ตรงกัน
    """
    target = require_profile(profile)
    with db.connect() as conn:
        ctx = page_context(conn, sup, profile=target, user=user, mode=mode)
    return templates.TemplateResponse(request, "partials/config_body.html", ctx)


async def form_values(request: Request) -> dict[str, str]:
    """ค่าทุกช่องในฟอร์มเป็น dict — ชื่อช่องเป็น path จึงประกาศเป็นพารามิเตอร์ไม่ได้

    เป็น dependency แบบ async ทั้งที่ route เป็น `def` ธรรมดาโดยตั้งใจ: FastAPI
    แก้ dependency ใน event loop แล้วพา handler ไปรันใน threadpool · route ที่เป็น
    `async def` เองจะลาก SQLAlchemy แบบ sync เข้ามาบล็อก loop ซึ่ง `cane serve`
    รับไม่ได้เพราะมันรัน worker เดียว (ดู `app.py`)
    """
    return {key: str(value) for key, value in (await request.form()).items()}


def _rendered(
    request: Request,
    db: Engine,
    sup: Supervisor,
    *,
    profile: str,
    user: User,
    mode: str,
    errors: Mapping[str, Problem] | None = None,
    unplaced: tuple[Problem, ...] = (),
    saved: str = "",
    saved_note: str = "",
) -> HTMLResponse:
    with db.connect() as conn:
        ctx = page_context(
            conn,
            sup,
            profile=profile,
            user=user,
            mode=mode,
            errors=errors,
            unplaced=unplaced,
            saved=saved,
            saved_note=saved_note,
        )
    return templates.TemplateResponse(request, "partials/config_body.html", ctx)


@router.post("/api/{profile}/config", response_class=HTMLResponse)
def save(
    profile: str,
    request: Request,
    form: Mapping[str, str] = Depends(form_values),
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    user: User = Depends(require_cap("edit_profile")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """บันทึก = **สร้างเวอร์ชันใหม่ที่ยังไม่เปิดใช้** ไม่ใช่แก้ของเดิม

    ฐานบังคับไว้แล้วตั้งแต่ระดับสิทธิ์ — `cane_console` มีแค่ `SELECT, INSERT` กับ
    `UPDATE (is_active)` (migration 0002) · แก้ทับคอลัมน์ config ทำไม่ได้แม้อยากทำ
    การเลื่อนตัวชี้เป็นการกระทำแยกที่ต้อง step-up ต่างหาก

    **ล้มเหลวคืน 200 ไม่ใช่ 4xx** — htmx ทิ้งคำตอบ 4xx ทุกตัวยกเว้นที่อยู่ในลิสต์
    `responseHandling` ของ `base.html` (มีแค่ 403 ของ step-up) · รายการที่ต้องแก้ที่
    ตอบกลับมาเป็น 422 จะหายเงียบและหน้าจอจะดูเหมือนปุ่มไม่ทำงาน
    """
    target = require_profile(profile)

    with db.connect() as conn:
        try:
            base = config_repo.active_settings(conn, target)
        except ConfigError:
            base = None

    if base is None:
        # ไม่มีของให้ลอก · ฟอร์มก็ไม่ถูก render ตั้งแต่แรก คำขอนี้จึงมาจากที่อื่น
        return _rendered(
            request, db, sup, profile=target, user=user, mode=mode,
            unplaced=(Problem(message="ยังไม่มีเวอร์ชันที่เปิดใช้ให้แก้ — ทางเข้าครั้งแรกคือ cane db seed"),),
        )

    raw, problems = patched(base, form)
    settings: Settings | None = None
    try:
        settings = validate_settings(raw, source="console")
    except ConfigError as exc:
        problems.extend(exc.problems)

    if problems:
        errors = {p.field_path: p for p in problems if p.field_path in EDITABLE}
        unplaced = tuple(p for p in problems if p.field_path not in EDITABLE)
        return _rendered(
            request, db, sup, profile=target, user=user, mode=mode,
            errors=errors, unplaced=unplaced,
        )

    assert settings is not None
    note = form.get("note", "").strip()
    now = now_ms()
    with db.begin() as conn:
        head = config_repo.insert_version(
            conn,
            settings,
            source="console",
            note=note or None,
            created_by_user_id=user.id,
            created_ts=now,
        )
        audit.record(
            conn,
            action="config.save",
            ts=now,
            actor_user_id=user.id,
            target=f"{target} v{head.version}",
            ip=client_ip(request),
        )

    return _rendered(
        request, db, sup, profile=target, user=user, mode=mode,
        saved=f"บันทึกเป็นเวอร์ชัน v{head.version} แล้ว — ยังไม่เปิดใช้",
        saved_note='กด "ใช้เวอร์ชันนี้" ในประวัติเพื่อให้ engine อ่านค่าชุดใหม่',
    )


def _owned(conn: Connection, profile: str, version_id: int) -> config_repo.ConfigVersion:
    """เวอร์ชันที่ไม่ใช่ของ profile นี้ = **404**

    `activate()` เลื่อนตัวชี้ของ profile ที่อยู่ในแถวที่มันอ่าน ไม่ใช่ของ profile ที่
    อยู่ใน URL · ถ้าไม่กันไว้ `/api/paper/config/<id ของ live>/activate` จะไปเปิดใช้
    เวอร์ชันของ live โดยที่หน้าจอบอกว่ากำลังทำอะไรกับ paper
    """
    for head in config_repo.versions(conn, profile):
        if head.id == version_id:
            return head
    raise HTTPException(status_code=404, detail=f"ไม่มีเวอร์ชัน {version_id} ของ {profile}")


@router.get(
    "/partials/config/{profile}/activate/{version_id}", response_class=HTMLResponse
)
def activate_modal(
    profile: str,
    version_id: int,
    request: Request,
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("edit_profile")),
) -> HTMLResponse:
    target = require_profile(profile)
    with db.connect() as conn:
        head = _owned(conn, target, version_id)
    return templates.TemplateResponse(
        request,
        "partials/stepup_modal.html",
        {
            "title": f"ใช้เวอร์ชัน v{head.version} · {target}",
            "detail": "engine จะอ่านค่าชุดนี้ในรอบถัดไป · เวอร์ชันเดิมยังอ่านย้อนหลังได้",
            "action": f"/api/{target}/config/{version_id}/activate",
        },
    )


@router.post("/api/{profile}/config/{version_id}/activate", response_class=HTMLResponse)
def activate(
    profile: str,
    version_id: int,
    request: Request,
    step_up_code: str = Form(""),
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    user: User = Depends(require_cap("edit_profile")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เลื่อนตัวชี้ — จังหวะเดียวของหน้านี้ที่เปลี่ยนสิ่งที่ engine อ่านจริง

    ยืนยัน step-up **ในตัว handler** แบบเดียวกับ `session.switch_mode` ไม่ใช่ผ่าน
    dependency `require_step_up` · `StepUpFailed` ถูกแปลงที่ `app.py` ด้วย handler ที่
    แกะ URL เป็น `/api/{profile}/engine/{action}` ตายตัว — เส้นทางนี้จะได้ modal ที่
    ยิงกลับไปที่ router ของ engine ซึ่งผิดทั้งเส้น

    คืน 403 พร้อม modal ใบเดิม (403 อยู่ในลิสต์ `responseHandling` ของ `base.html`
    จึง swap ได้จริง) · สำเร็จแล้วคืนเนื้อหน้าแบบ out-of-band ซึ่งทำให้ modal ปิดเอง
    """
    target = require_profile(profile)
    now = now_ms()

    with db.connect() as conn:
        head = _owned(conn, target, version_id)

    with db.begin() as conn:
        ok = service.verify_step_up(conn, user, step_up_code.strip(), now=now)
        if not ok:
            audit.record(
                conn,
                action="config.activate_refused",
                ts=now,
                actor_user_id=user.id,
                target=f"{target} v{head.version}",
                ip=client_ip(request),
            )
    if not ok:
        return templates.TemplateResponse(
            request,
            "partials/stepup_modal.html",
            {
                "title": f"ใช้เวอร์ชัน v{head.version} · {target}",
                "detail": "รหัสเปลี่ยนทุก 30 วินาที — ใช้รหัสล่าสุดจากแอป",
                "action": f"/api/{target}/config/{version_id}/activate",
                "error": "รหัส 6 หลักไม่ถูกต้อง",
            },
            status_code=403,
        )

    with db.begin() as conn:
        config_repo.activate(conn, version_id)
        audit.record(
            conn,
            action="config.activate",
            ts=now,
            actor_user_id=user.id,
            target=f"{target} v{head.version}",
            ip=client_ip(request),
            step_up_verified=True,
        )

    with db.connect() as conn:
        ctx = page_context(
            conn,
            sup,
            profile=target,
            user=user,
            mode=mode,
            saved=f"เปิดใช้เวอร์ชัน v{head.version} แล้ว",
            saved_note="engine อ่านค่าชุดนี้ในรอบถัดไป · เวอร์ชันเดิมยังอ่านย้อนหลังได้",
        )
    ctx["cfg_oob"] = True
    return templates.TemplateResponse(request, "partials/config_body.html", ctx)
