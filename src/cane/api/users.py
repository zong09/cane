"""หน้า ผู้ใช้ — 4 แท็บ อ่านอย่างเดียว (ใบ 20 · PR แรกของสาม)

ทุกแท็บอ่านจากตารางที่ spec/09 บอกว่าเป็นความจริง ไม่มีค่าที่หน้าจอคิดเอง:
บัญชีจาก `users` · ตารางสิทธิ์จาก**เวอร์ชันที่ active** ของ `role_permissions` (ไม่ใช่
`DEFAULT_MATRIX` — นั่นคือค่าตั้งต้นที่ใส่ครั้งเดียว) · session จาก `sessions` ที่ยังไม่ถูกตัด
และยังไม่หมดอายุ · บันทึกจาก `user_audit_log` ที่ redact มาแล้วตั้งแต่ตอนเขียน

ปุ่มที่เปลี่ยนอะไร (เชิญ เปลี่ยน role ระงับ ปลดล็อก reset 2FA ตัด session แก้สิทธิ์)
มากับ PR ถัดไปพร้อม step-up · PR นี้ไม่วาดปุ่มที่ยังกดไม่ได้

## ที่ต่างจากไฟล์ design

| ในไฟล์ design | ที่นี่ | เพราะ |
| --- | --- | --- |
| `ที่มา` = `203.0.113.44 · Bangkok` | `ที่มา (IP)` แสดง IP ล้วน | spec/09 §6. session — GeoIP ไม่อยู่ในเฟสนี้ |
| `อุปกรณ์` = `Chrome · macOS` | user-agent ตามที่เบราว์เซอร์ส่งมา | การแยก UA เป็นการเดา ไม่ใช่ข้อมูลที่มี |
| ป้าย action ภาษาไทยทุกแถว | ป้ายไทยเฉพาะ action ที่รู้จัก บรรทัดล่างมีรหัส action จริงเสมอ | รหัสคือสิ่งที่ค้นในตารางได้ · action ใหม่ต้องไม่หายจากหน้าจอเพราะไม่มีป้าย |
| KPI `ยิงจริงได้ (owner)` นับ OWNER ทุกคน | นับ OWNER ที่ `active` | OWNER ที่ถูกระงับหรือยังไม่ผูก 2FA ยิงอะไรไม่ได้ — ตัวเลขเดียวกับด่าน "ห้ามเหลือศูนย์ OWNER" |

เจ้าของยืนยันข้อความที่เปลี่ยนจาก design ในตารางนี้แล้วทุกแถว (handoff §15 ข้อ 9 · 2026-09-24)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api.context import _initials
from cane.api.deps import current_session, get_db, require_cap
from cane.api.templating import templates
from cane.auth.matrix import ROLES
from cane.db.repo import audit
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.db.types import now_ms

router = APIRouter()

UNKNOWN = "—"

TABS = (
    ("people", "บัญชีผู้ใช้"),
    ("perms", "สิทธิ์ต่อ role"),
    ("sessions", "session ที่เปิดอยู่"),
    ("log", "บันทึกผู้ใช้"),
)

STATUS_TEXT = {"active": "ใช้งาน", "pending": "รอรับเชิญ", "suspended": "ระงับ"}

#: `cap` → คำบรรยายตาม spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role · ลำดับเดียวกับตารางใน spec
CAP_TEXT = {
    "view_overview": "ดูภาพรวม กราฟ โซน และสถานะ engine",
    "read_decisions": "อ่านบันทึกการตัดสินใจ",
    "choose_cold_start_route": "เลือกเส้นทาง cold start",
    "close_position_manual": "ปิดไม้ด้วยมือ (ฉุกเฉิน)",
    "killswitch_latch": "กด kill switch หยุดฉุกเฉิน",
    "killswitch_unlatch": "ปลด kill switch",
    "engine_control": "start / stop engine",
    "toggle_dry_run": "สลับ dry_run เป็นยิงจริง",
    "edit_profile": "แก้โปรไฟล์ risk limit allow_short และการตั้งค่าแจ้งเตือน",
    "edit_symbols": "เพิ่ม–ลบคู่เหรียญ",
    "manage_users": "เชิญ ย้าย role ระงับ ปลดล็อกบัญชี ตัด session แก้ตารางสิทธิ์",
    "reset_other_2fa": "reset 2FA ของคนอื่น",
    "export_records": "ส่งออกบันทึกทั้งหมด",
}

#: สิทธิ์ที่คอลัมน์ step-up ทำเครื่องหมาย **ต้อง** (spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role)
STEP_UP_CAPS = frozenset({
    "close_position_manual", "killswitch_unlatch", "engine_control", "toggle_dry_run",
    "edit_profile", "edit_symbols", "manage_users", "reset_other_2fa",
})

#: รหัส action ใน `user_audit_log` → ป้ายในคอลัมน์ `ทำอะไร` · ที่ไม่อยู่ในนี้แสดงรหัสตรงๆ
ACTION_TEXT = {
    "login.ok": "เข้าสู่ระบบ",
    "login.stage1_failed": "เข้าสู่ระบบไม่สำเร็จ (ขั้นที่ 1)",
    "login.stage2_failed": "เข้าสู่ระบบไม่สำเร็จ (ขั้นที่ 2)",
    "login.refused_locked": "ปฏิเสธการเข้าสู่ระบบ — บัญชีถูกล็อก",
    "logout": "ออกจากระบบ",
    "totp.enrolled": "ผูก 2FA",
    "stepup.failed": "ยืนยัน 2FA ไม่ผ่าน",
    "session.mode_live": "สลับไปโหมด live",
    "session.mode_live_refused": "ปฏิเสธการสลับไปโหมด live",
    "engine.start": "start engine",
    "engine.stop": "stop engine",
    "killswitch.latch": "กด kill switch",
    "killswitch.unlatch": "ปลด kill switch",
    "killswitch.unlatch_refused": "ปฏิเสธการปลด kill switch",
    "config.save": "บันทึกร่าง config",
    "config.activate": "เปิดใช้ config",
    "config.activate_refused": "ปฏิเสธการเปิดใช้ config",
    "config.symbols": "บันทึกคู่เหรียญ",
    "config.symbols_removed": "ลบคู่เหรียญ",
    "coldstart.choose": "เลือกเส้นทาง cold start",
}

#: ย้อมแดงเมื่อเป็นการถูกปฏิเสธหรือล้มเหลว — สิ่งที่คนอ่านบันทึกต้องเห็นก่อน
_DANGER_SUFFIXES = ("_refused", "_rejected", "_failed", ".failed", "refused_locked")

LOG_LIMIT = 100


def _utc(ts: int | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if ts is None:
        return UNKNOWN
    return datetime.fromtimestamp(ts / 1000, tz=UTC).strftime(fmt)


@dataclass(frozen=True, slots=True)
class PersonRow:
    initials: str
    name: str
    email: str
    role: str
    has_2fa: bool
    last: str
    status: str
    status_text: str


@dataclass(frozen=True, slots=True)
class PermRow:
    cap: str
    text: str
    step_up: bool
    #: `(role, allowed)` ตามลำดับ `ROLES`
    cells: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class SessionRow:
    id: int
    user: str
    current: bool
    device: str
    origin: str
    since: str


@dataclass(frozen=True, slots=True)
class LogRow:
    time: str
    actor: str
    action: str
    detail: str
    danger: bool


def _people(everyone: list[User]) -> tuple[PersonRow, ...]:
    return tuple(
        PersonRow(
            initials=_initials(u.name),
            name=u.name,
            email=u.email,
            role=u.role,
            has_2fa=u.totp_enrolled_ts is not None,
            last=_utc(u.last_login_ts),
            status=u.status,
            status_text=STATUS_TEXT[u.status],
        )
        for u in everyone
    )


def _perm_rows(conn: Connection) -> tuple[PermRow, ...]:
    version = perms.active_version(conn)
    matrix = perms.matrix_of(conn, version.id) if version is not None else {}
    caps = perms.all_caps(conn)
    ordered = [cap for cap in CAP_TEXT if cap in caps] + [cap for cap in caps if cap not in CAP_TEXT]
    return tuple(
        PermRow(
            cap=cap,
            text=CAP_TEXT.get(cap, cap),
            step_up=cap in STEP_UP_CAPS,
            # OWNER ได้ทุกข้อเสมอ — ตัวตรวจสิทธิ์คืน True ก่อนแตะตาราง หน้าจอจึงต้องบอกตรงกัน
            # (spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role)
            cells=tuple(
                (role, role == "OWNER" or matrix.get(role, {}).get(cap, False)) for role in ROLES
            ),
        )
        for cap in ordered
    )


def _session_rows(conn: Connection, names: dict[int, str], current: Session, now: int) -> tuple[SessionRow, ...]:
    return tuple(
        SessionRow(
            id=s.id,
            user=names.get(s.user_id, UNKNOWN),
            current=s.id == current.id,
            device=s.user_agent or UNKNOWN,
            origin=s.ip or UNKNOWN,
            since=_utc(s.created_ts),
        )
        for s in sessions_repo.live_all(conn, now=now)
    )


def _detail(entry: audit.AuditEntry) -> str:
    parts = [entry.action]
    if entry.target:
        parts.append(entry.target)
    parts += [f"{key}={value}" for key, value in (entry.detail or {}).items()]
    if entry.ip:
        parts.append(f"IP {entry.ip}")
    if entry.step_up_verified:
        parts.append("ยืนยัน 2FA แล้ว")
    return " · ".join(parts)


def _log_rows(conn: Connection, names: dict[int, str]) -> tuple[LogRow, ...]:
    return tuple(
        LogRow(
            time=_utc(e.ts, "%Y-%m-%d %H:%M:%S"),
            actor=names.get(e.actor_user_id, UNKNOWN) if e.actor_user_id is not None else UNKNOWN,
            action=ACTION_TEXT.get(e.action, e.action),
            detail=_detail(e),
            danger=e.action.endswith(_DANGER_SUFFIXES),
        )
        for e in audit.recent(conn, limit=LOG_LIMIT)
    )


def page_context(conn: Connection, *, session: Session, tab: str) -> dict[str, object]:
    tab = tab if tab in dict(TABS) else TABS[0][0]
    everyone = users_repo.everyone(conn)
    names = {u.id: u.name for u in everyone}
    now = now_ms()

    ctx: dict[str, object] = {
        "us_tab": tab,
        "us_tabs": TABS,
        "us_count": len(everyone),
        "us_kpi": {
            "active": sum(u.status == "active" for u in everyone),
            "owners": sum(u.role == "OWNER" and u.status == "active" for u in everyone),
            "pending": sum(u.status == "pending" for u in everyone),
            "no2fa": sum(u.totp_enrolled_ts is None for u in everyone),
        },
    }
    if tab == "people":
        ctx["us_people"] = _people(everyone)
    elif tab == "perms":
        ctx["us_roles"] = ROLES
        ctx["us_perms"] = _perm_rows(conn)
    elif tab == "sessions":
        ctx["us_sessions"] = _session_rows(conn, names, session, now)
    else:
        ctx["us_log"] = _log_rows(conn, names)
    return ctx


@router.get("/partials/users", response_class=HTMLResponse)
def body(
    request: Request,
    tab: str = Query("people"),
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("manage_users")),
    session: Session = Depends(current_session),
) -> HTMLResponse:
    """เนื้อของหน้าผู้ใช้ตอนสลับแท็บ · ไม่ฟัง `cane:mode` — หน้านี้ไม่ผูกกับโหมด"""
    with db.connect() as conn:
        ctx = page_context(conn, session=session, tab=tab)
    return templates.TemplateResponse(request, "partials/users_body.html", ctx)
