"""audit log ของผู้ใช้ (spec/09 §8. audit log ของผู้ใช้)

**redaction เกิดตอนเขียน ไม่ใช่ตอนอ่าน** — ตารางนี้ไม่มี `UPDATE`/`DELETE` ให้ใคร
ค่าที่หลุดลงไปแล้วลบไม่ได้อีก · การกรองตอนอ่านคือการปล่อยให้ความลับนอนอยู่ในตาราง
ถาวรโดยหวังว่าทุกเส้นทางการอ่านจะจำกรอง

ใช้ `cane.log.redact()` **ตัวเดียวกับที่ระบบเทรดใช้** ไม่ใช่ตัวที่เขียนใหม่ให้ auth —
ตัวกรองสองตัวคือกฎสองชุดที่จะต่างกันในวันที่มีใครแก้ตัวหนึ่ง
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import Connection, select

from cane import log
from cane.db.schema import user_audit_log


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    actor_user_id: int | None
    action: str
    target: str | None
    detail: dict | None
    ip: str | None
    step_up_verified: bool
    ts: int


def _redacted(detail: dict | None) -> dict | None:
    """กรองทั้งก้อนในรูป JSON แล้วแปลงกลับ

    กรองที่ข้อความ JSON ไม่ใช่ไล่ทีละคีย์ เพราะ `redact()` จับ `"secret": "ค่า"`
    ในรูปนั้นอยู่แล้ว และการไล่เองแปลว่ามีกฎชุดที่สองให้ต้องดูแลให้ตรงกัน

    ถ้าแปลงกลับไม่ได้ (ค่าที่มี `"` ข้างในทำให้ regex ตัดผิดตำแหน่ง) **เก็บเป็น
    ข้อความที่กรองแล้ว** ไม่ใช่เก็บของเดิม — ทางออกที่ปลอดภัยของตารางที่ลบไม่ได้
    คือยอมให้รูปทรงเพี้ยน ไม่ใช่ยอมให้ค่าจริงหลุด
    """
    if detail is None:
        return None
    masked = log.redact(json.dumps(detail, ensure_ascii=False, sort_keys=True))
    try:
        return json.loads(masked)
    except json.JSONDecodeError:
        return {"redacted_text": masked}


def record(
    conn: Connection,
    *,
    action: str,
    ts: int,
    actor_user_id: int | None = None,
    target: str | None = None,
    detail: dict | None = None,
    ip: str | None = None,
    step_up_verified: bool = False,
) -> None:
    """`step_up_verified` เป็นอาร์กิวเมนต์ที่ไม่มีค่าเดาให้ — คนอ่านย้อนหลังต้องแยกได้

    ค่าตั้งต้นเป็น `False` เพราะ action ส่วนใหญ่ไม่ต้อง step-up · เส้นทางที่ต้อง
    ยืนยันซ้ำต้องส่ง `True` มาเอง ซึ่งแปลว่าการลืมส่งทำให้บันทึกดู *เข้มน้อยกว่า*
    ความจริง ไม่ใช่ดูเข้มกว่า
    """
    conn.execute(
        user_audit_log.insert().values(
            actor_user_id=actor_user_id,
            action=action,
            target=target,
            detail=_redacted(detail),
            ip=ip,
            step_up_verified=step_up_verified,
            ts=ts,
        )
    )


def recent(conn: Connection, limit: int = 100) -> list[AuditEntry]:
    rows = conn.execute(
        select(
            user_audit_log.c.id,
            user_audit_log.c.actor_user_id,
            user_audit_log.c.action,
            user_audit_log.c.target,
            user_audit_log.c.detail,
            user_audit_log.c.ip,
            user_audit_log.c.step_up_verified,
            user_audit_log.c.ts,
        )
        .order_by(user_audit_log.c.ts.desc(), user_audit_log.c.id.desc())
        .limit(limit)
    )
    return [AuditEntry(**row._mapping) for row in rows]
