"""ตารางสิทธิ์ตั้งต้น 13 สิทธิ์ × 5 role (spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role)

**นี่คือเวอร์ชันตั้งต้น ไม่ใช่ความจริงตลอดกาล** — คอนโซลแก้ได้ (OWNER เท่านั้น
+ step-up — spec/09 §2. บัญชี role และสถานะ) แล้วเกิดเป็นเวอร์ชันใหม่ · ค่าที่นี่ถูกใส่ลงฐานครั้งเดียวโดย
`cane auth seed-permissions` แล้วไม่มีใครอ่านมันอีก ตัวที่ตอบว่า "ทำได้ไหม" คือ
เวอร์ชันที่ active อยู่ในฐาน

คอลัมน์ OWNER เป็น `True` ทุกช่องและ **ถอนไม่ได้** — ตัวตรวจสิทธิ์คืน `True` ให้
OWNER ก่อนแตะตารางอยู่แล้ว และ trigger ที่ฐานปฏิเสธการเขียนแถวที่ขัดกับข้อนี้
"""

from __future__ import annotations

ROLES = ("OWNER", "ADMIN", "TRADER", "VIEWER", "AUDITOR")

#: cap → role ที่ได้สิทธิ์นั้นในเวอร์ชันตั้งต้น · role ที่ไม่อยู่ในชุดคือไม่ได้
_GRANTED: dict[str, frozenset[str]] = {
    "view_overview": frozenset(ROLES),
    "read_decisions": frozenset(ROLES),
    "choose_cold_start_route": frozenset({"OWNER", "TRADER"}),
    "close_position_manual": frozenset({"OWNER", "TRADER"}),
    "killswitch_latch": frozenset({"OWNER", "ADMIN", "TRADER"}),
    "killswitch_unlatch": frozenset({"OWNER"}),
    "engine_control": frozenset({"OWNER", "ADMIN"}),
    "toggle_dry_run": frozenset({"OWNER"}),
    "edit_profile": frozenset({"OWNER"}),
    "edit_symbols": frozenset({"OWNER"}),
    "manage_users": frozenset({"OWNER", "ADMIN"}),
    "reset_other_2fa": frozenset({"OWNER", "ADMIN"}),
    "export_records": frozenset({"OWNER", "ADMIN", "AUDITOR"}),
}

#: `{role: {cap: allowed}}` — รูปที่ `repo/permissions.insert_version()` รับ
DEFAULT_MATRIX: dict[str, dict[str, bool]] = {
    role: {cap: role in granted for cap, granted in _GRANTED.items()} for role in ROLES
}
