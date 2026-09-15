"""ข้อมูลที่ layout ต้องใช้ทุกหน้า — rail, PROFILE card, engine card

รวมไว้ที่เดียวเพราะสามชิ้นนี้ถูก render ทั้งจากหน้าเต็มและจาก partial ที่ HTMX swap
ถ้าปล่อยให้แต่ละ handler ประกอบเอง สองทางนั้นจะค่อยๆ ไม่ตรงกัน
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version

from sqlalchemy import Connection

from cane.api.deps import ConsoleUser
from cane.config.settings import Settings
from cane.config.validate import ConfigError
from cane.db.repo import config as config_repo
from cane.engine.state import PROFILES
from cane.engine.supervisor import EngineView, Supervisor

#: เมนูกลุ่ม MODE · หมายเลข 01–06 กับป้ายตรงตามไฟล์ design
#: ใบที่จะมาเติมเนื้อของแต่ละหน้าอยู่ในช่องที่สาม — placeholder ของใบ 19 อ่านจากตรงนี้
NAV: tuple[tuple[str, str, int], ...] = (
    ("overview", "ภาพรวม", 22),
    ("symbols", "คู่เหรียญ", 26),
    ("risk", "ความเสี่ยง", 23),
    ("log", "บันทึก", 24),
    ("report", "รายงาน", 25),
    ("config", "ตั้งค่า", 21),
)

#: กลุ่ม "ทั้งระบบ" — ไม่ผูกกับโหมด จึงไม่มีเลข ใช้จุดเป็น marker แทน
GLOBAL_NAV: tuple[tuple[str, str, int], ...] = (("users", "ผู้ใช้", 20),)

#: สีจุดของ symbol ใน rail · ใบ 19 ยังไม่มีการคำนวณโซน (เป็นของใบ 22) จึงใช้สี
#: BLACK ของ zone palette ซึ่งแปลว่า "ไม่มีข้อมูล" อยู่แล้ว — ดีกว่าเดาสีเขียวไว้ก่อน
ZONE_UNKNOWN = "#c3d4e0"


@dataclass(frozen=True, slots=True)
class Chip:
    """ชิปข้าง badge ของ PROFILE card · `kind` แปลงเป็น class ใน CSS"""

    label: str
    kind: str  # "live" | "paper" | "warn"


@dataclass(frozen=True, slots=True)
class SymbolRow:
    pair: str
    dot: str


def profile_chip(profile: str, settings: Settings | None) -> Chip:
    """ชิป `ยิงจริง` / `dry-run` / `จำลองทั้งหมด` มาจาก config เวอร์ชันที่ active

    **ไม่ได้มาจากไฟล์ `{profile}.toml`** ถึงแม้ badge ข้างๆ จะเขียนชื่อไฟล์นั้น —
    TOML เหลือหน้าที่เดียวคือทางเข้าของ `cane db seed` (spec/07 §Config profile)

    ไม่มีเวอร์ชัน active แปลว่า **ไม่เทรด** ไม่ใช่ "ใช้ค่าตั้งต้น" จึงต้องเห็นบนหน้าจอ
    """
    if settings is None:
        return Chip("ไม่มีเวอร์ชัน active", "warn")
    if profile == "paper":
        return Chip("จำลองทั้งหมด", "paper")
    return Chip("dry-run", "paper") if settings.dry_run else Chip("ยิงจริง", "live")


def live_warning(conn: Connection) -> str:
    """คำเตือนใน modal สลับโหมด · คืนสตริงว่างเมื่อ live พร้อมใช้งาน

    ไฟล์ design เขียนประโยคนี้ไว้ตายตัวว่า "live.toml ยังโหลดไม่ผ่าน 4 ข้อ" ซึ่งเป็น
    ของก่อน config ย้ายลง DB · เลขที่ถูกต้องนับจาก `ConfigError.problems` ของจริง
    และกรณี "ไม่มีเวอร์ชัน active" เป็นคนละเรื่องกับ "มีแต่ค่าผิด" จึงแยกข้อความ
    """
    try:
        settings = config_repo.active_settings(conn, "live")
    except ConfigError as exc:
        return (
            f"live ยังโหลดไม่ผ่าน {len(exc.problems)} ข้อ — "
            "สลับได้แต่ระบบจะยังไม่เทรดจนกว่าจะแก้ครบ"
        )
    if settings is None:
        return "live ยังไม่มีเวอร์ชัน config ที่เปิดใช้ — สลับได้แต่ระบบจะยังไม่เทรด"
    return ""


def _settings_or_none(conn: Connection, profile: str) -> Settings | None:
    """`active_settings()` ตรวจค่าใหม่ตอนอ่าน จึง raise ได้ — จับที่นี่ที่เดียว

    config ที่พังต้องทำให้ชิปเปลี่ยน ไม่ใช่ทำให้ทั้งหน้าเป็น 500 · หน้าจอที่เปิดไม่ขึ้น
    คือหน้าจอที่กด stop engine ไม่ได้ ซึ่งเป็นตอนที่ต้องกดที่สุด
    """
    try:
        return config_repo.active_settings(conn, profile)
    except ConfigError:
        return None


def build(
    conn: Connection,
    sup: Supervisor,
    *,
    user: ConsoleUser,
    mode: str,
    active: str = "",
) -> dict[str, object]:
    views = {view.profile: view for view in sup.status(conn)}
    settings = _settings_or_none(conn, mode)
    other = PROFILES[1] if mode == PROFILES[0] else PROFILES[0]

    symbols = (
        [SymbolRow(pair=s.symbol, dot=ZONE_UNKNOWN) for s in settings.symbols if s.enabled]
        if settings is not None
        else []
    )

    return {
        "app_version": version("cane"),
        "user": user,
        "mode": mode,
        "other_mode": other,
        "active": active,
        "nav": NAV,
        "global_nav": GLOBAL_NAV,
        "nav_scope_label": f"ขอบเขต {mode}.toml",
        "profile_file": f"{mode}.toml",
        "chip": profile_chip(mode, settings),
        "engine": views[mode],
        "other_engine_label": f"{other}: {views[other].status}",
        "symbols": symbols,
        "symbol_count": f"{len(symbols)} คู่",
    }


def engine_fragment(
    conn: Connection, sup: Supervisor, *, mode: str, just_changed: bool = False
) -> dict[str, object]:
    """ข้อมูลเฉพาะการ์ด engine — สำหรับ partial ที่ poll ทุกรอบ heartbeat

    `just_changed` เป็นจริงเฉพาะการ์ดที่ตอบกลับทันทีหลังกด start/stop · ดูเหตุผล
    ที่ `partials/engine.html`
    """
    views: dict[str, EngineView] = {view.profile: view for view in sup.status(conn)}
    other = PROFILES[1] if mode == PROFILES[0] else PROFILES[0]
    return {
        "mode": mode,
        "engine": views[mode],
        "other_engine_label": f"{other}: {views[other].status}",
        "just_changed": just_changed,
    }
