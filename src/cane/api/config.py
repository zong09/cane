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

from dataclasses import dataclass, field
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.deps import current_mode, get_db, get_sup, require_cap, require_profile
from cane.api.templating import templates
from cane.config import diff as config_diff
from cane.config.settings import Settings
from cane.config.validate import ConfigError, Problem
from cane.db.repo import config as config_repo
from cane.db.repo import permissions as perms
from cane.db.repo import users as users_repo
from cane.db.repo.users import User
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


def _groups(settings: Settings | None) -> tuple[tuple[str, tuple[FormField, ...]], ...]:
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
    return (
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


def _switches(settings: Settings | None) -> tuple[tuple[str, str, str], ...]:
    """`dry_run` / `allow_short` แสดงอย่างเดียว — ปุ่มสลับเป็นของใบ 23

    `paper` สลับ `dry_run` ไม่ได้เลยไม่ว่าจะใส่ปุ่มหรือไม่ เพราะ CHECK
    `ck_config_settings_paper_dry_run` ปฏิเสธที่ฐาน · เขียนคำอธิบายไว้ข้างค่า
    แทนที่จะให้คนกดแล้วเจอ constraint ของ Postgres
    """
    if settings is None:
        return ()
    forced = (
        "paper บังคับ true ที่ฐาน (ck_config_settings_paper_dry_run)"
        if settings.profile == "paper"
        else "ปุ่มสลับเป็นของใบ 23"
    )
    return (
        ("dry_run", str(settings.dry_run).lower(), forced),
        ("allow_short", str(settings.allow_short).lower(), "สวิตช์ระดับระบบ — ปุ่มสลับเป็นของใบ 23"),
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
        "cfg_groups": _groups(settings),
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
