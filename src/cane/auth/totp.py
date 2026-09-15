"""TOTP ตาม RFC 6238 และ backup code (spec/09 §TOTP, §backup code)

เขียนเองด้วย stdlib (`hmac` + `struct` + `base64`) ไม่ดึงไลบรารีเข้ามา —
อัลกอริทึมคือ HMAC-SHA1 กับการตัดตัวเลขออกมา 6 หลัก ซึ่งสั้นกว่าการอ่านสัญญาของ
ไลบรารีที่จะดึงมา และ **ตรวจได้กับ test vector ของ RFC โดยตรง**
(นโยบายเดียวกับที่ `confluence/openai_client.py` ใช้ `urllib` แทน `requests`)

## ค่าที่สเปกตรึงไว้ ห้ามเปลี่ยนเพราะแอปบนมือถือจะไม่ตรง

SHA-1 · 6 หลัก · ช่วง 30 วินาที · ยอมรับ **±1 ช่วง** (กันนาฬิกาเครื่องเหลื่อม)

SHA-1 ตรงนี้ไม่ใช่การเลือกที่ผิด — RFC 6238 กับ Google Authenticator ใช้ตัวนี้ และ
ความปลอดภัยของ TOTP มาจากความลับที่แชร์กัน ไม่ได้มาจากความต้านทานการชนของ hash

## `verify()` คืน counter ไม่ใช่ True

ผู้เรียก**ต้องเอา counter ที่ได้ไปเขียนลง `users.totp_last_counter`** ไม่งั้นข้อบังคับ
"รหัสที่ใช้แล้วใช้ซ้ำไม่ได้" ไม่เกิดขึ้น · การคืน `bool` จะทำให้ลืมข้อนี้ได้เงียบๆ
และรหัสที่หลุดตาไปยังยิงซ้ำได้อีก 90 วินาที ซึ่งพอสำหรับการส่งต่อด้วยมือ
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct

#: ยาว 20 ไบต์ = ขนาดของ HMAC-SHA1 block ที่ RFC 4226 แนะนำ
SECRET_BYTES = 20
DIGITS = 6
PERIOD_S = 30
#: ±1 ช่วง · กว้างกว่านี้คือการยืดอายุของรหัสที่หลุด ไม่ใช่ความสะดวก
WINDOW = 1

#: 10 รหัส สร้างพร้อมกันตอนผูก TOTP (spec/09 §backup code)
BACKUP_CODE_COUNT = 10
#: สองท่อนละ 5 อักษร base32 ≈ 50 บิต · พิมพ์ตามได้ และเก็บเป็น argon2 อีกชั้น
_BACKUP_GROUP = 5
_BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ตัด I O 0 1 ที่อ่านสลับกัน


def new_secret() -> str:
    """base32 ไม่มี `=` ต่อท้าย — แอปบนมือถืออ่าน padding ไม่ตรงกันอยู่บ่อยๆ"""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def counter_at(now_ms: int) -> int:
    """ช่วงเวลาที่ `now_ms` ตกอยู่ · หน่วยเป็น ms เหมือนเวลาอื่นทั้งระบบ"""
    return now_ms // 1000 // PERIOD_S


def code(secret: str, counter: int) -> str:
    """RFC 4226 dynamic truncation · คืนสตริงที่เติมศูนย์หน้าครบ 6 หลักเสมอ"""
    padding = "=" * (-len(secret) % 8)
    key = base64.b32decode(secret + padding, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    chunk = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(chunk % (10**DIGITS)).zfill(DIGITS)


def verify(
    secret: str, candidate: str, *, now: int, last_counter: int | None = None
) -> int | None:
    """คืน counter ที่ตรง หรือ `None` เมื่อไม่ผ่าน — **ผู้เรียกต้องบันทึก counter นั้น**

    ปฏิเสธ counter ที่ `<= last_counter` (spec/09 §TOTP) · ข้อนี้ทำให้รหัสเดิมที่ยัง
    ไม่หมดอายุใช้ซ้ำไม่ได้ ซึ่งเป็นสิ่งที่การตรวจว่า "รหัสถูกไหม" อย่างเดียวให้ไม่ได้

    ไล่จากช่วงล่าสุดไปหาเก่า เพื่อให้เคสปกติ (นาฬิกาตรง) เทียบครั้งเดียวจบ
    """
    if not candidate.isdigit() or len(candidate) != DIGITS:
        return None

    centre = counter_at(now)
    for counter in range(centre + WINDOW, centre - WINDOW - 1, -1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(code(secret, counter), candidate):
            return counter
    return None


def provisioning_uri(secret: str, email: str, issuer: str = "cane") -> str:
    """`otpauth://` สำหรับ QR — ค่าทุกตัวต้องตรงกับที่ `verify()` ใช้จริง"""
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD_S}"
    )


# ── backup code ──────────────────────────────────────────────────────────────


def new_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    """รหัสที่คนพิมพ์ตามได้ · **แสดงครั้งเดียวตอนสร้าง** ที่เก็บคือ hash เท่านั้น"""
    return [
        "-".join(
            "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(_BACKUP_GROUP))
            for _ in range(2)
        )
        for _ in range(count)
    ]


def normalise_backup_code(raw: str) -> str:
    """คนพิมพ์ตัวเล็ก เว้นวรรค หรือลืมขีด — รับหมด แล้วทำให้อยู่ในรูปเดียว

    ทำที่นี่ที่เดียว ทั้งตอนสร้าง hash และตอนตรวจ ไม่งั้นรหัสที่ถูกจะไม่ผ่าน
    ด้วยเหตุผลที่คนกรอกมองไม่เห็น
    """
    return raw.strip().upper().replace(" ", "").replace("-", "")
