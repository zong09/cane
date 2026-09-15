"""การเก็บความลับของ auth — hash, ciphertext, และ token ที่สุ่มมา

เกณฑ์ตัดสินของทั้งไฟล์คือประโยคเดียวใน [ADR 25]: **dump ของ DB อย่างเดียวต้องใช้
เข้าระบบไม่ได้** · ทุกฟังก์ชันที่นี่มีไว้เพื่อให้ประโยคนั้นเป็นจริง ไม่ใช่เพื่อความ
เรียบร้อยของโค้ด

สามชนิด สามวิธี **ไม่ใช่วิธีเดียวกัน**:

| ของ | วิธี | ทำไมไม่ใช่วิธีอื่น |
| --- | --- | --- |
| รหัสผ่าน · backup code | argon2id | คนเลือกเอง เดาได้ ต้องแพงต่อการเดาหนึ่งครั้ง |
| session token · ลิงก์ใช้ครั้งเดียว | SHA-256 | เราสุ่มเอง 256 บิต ไม่มีอะไรให้เดา — argon2 ตรงนี้ซื้อความช้าอย่างเดียว |
| TOTP secret | ciphertext (Fernet) | ต้องถอดกลับมาคำนวณรหัสได้ hash จึงใช้ไม่ได้เลย |

`CANE_SECRET_KEY` **ไม่มีค่าตั้งต้น** ด้วยเหตุผลเดียวกับ `CANE_DB_DSN`
(`db/engine.py`) — คีย์ที่ default เป็นค่าคงที่ในโค้ดคือคีย์ที่ prod ใช้จริงโดยไม่มี
ใครรู้ · และตาม ADR 25 คีย์นี้ **หายไม่ได้** หายแล้ว TOTP ของทุกคนใช้ไม่ได้
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets as _stdlib_secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

SECRET_KEY_ENV = "CANE_SECRET_KEY"

#: คีย์ที่สั้นกว่านี้ให้ความปลอดภัยน้อยกว่าที่คอลัมน์ `totp_secret_enc` สัญญาไว้ ·
#: ล้มดังตอนตั้งเครื่องดีกว่าเข้ารหัสด้วยคีย์ที่เดาได้แล้วไม่มีใครรู้
MIN_SECRET_KEY_LEN = 32

#: token ที่เราสุ่มเอง — 32 ไบต์ = 256 บิต · ยาวกว่านี้ไม่ได้ซื้ออะไร
TOKEN_BYTES = 32

_hasher = PasswordHasher()


def secret_key(env: dict[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    key = source.get(SECRET_KEY_ENV, "")
    if not key:
        raise RuntimeError(
            f"ไม่มี {SECRET_KEY_ENV} — ตั้งใน .env แล้วสั่งด้วย "
            f"`uv run --env-file .env ...` (ADR 25)"
        )
    if len(key) < MIN_SECRET_KEY_LEN:
        raise RuntimeError(
            f"{SECRET_KEY_ENV} สั้นเกินไป ({len(key)} อักษร) "
            f"ต้องอย่างน้อย {MIN_SECRET_KEY_LEN}"
        )
    return key


def _fernet(env: dict[str, str] | None = None) -> Fernet:
    """รับ passphrase ความยาวเท่าไหร่ก็ได้ แล้วย่อเป็นคีย์ 32 ไบต์ของ Fernet

    ไม่บังคับให้คนกรอกคีย์ในรูปแบบของ Fernet เอง เพราะรูปแบบที่ผิดจะล้มตอน
    ยิงจริงครั้งแรก ไม่ใช่ตอนตั้งค่า · ความยาวขั้นต่ำเป็นตัวกันคุณภาพแทน
    """
    digest = hashlib.sha256(secret_key(env).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


# ── รหัสผ่านและ backup code — argon2id ────────────────────────────────────────


def hash_password(password: str) -> str:
    """argon2id ตาม ADR 25 · ใช้กับ backup code ด้วย (คนพิมพ์เองเหมือนกัน)"""
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """คืน `False` แทนการโยน — ผู้เรียกทุกที่สนใจแค่ผ่านหรือไม่ผ่าน

    hash ที่เสียก็คือไม่ผ่าน: แถวที่ `password_hash` เสียหรือว่างต้องไม่กลายเป็น
    500 ที่หน้า login ซึ่งบอกคนที่กำลังเดาว่าบัญชีนี้ต่างจากบัญชีอื่น

    `UnicodeEncodeError` อยู่ในรายการเพราะ argon2 encode hash ที่รับมาเป็น ascii
    ก่อนแตะมัน — แถวที่มีอักษรนอก ascii จึงระเบิดคนละแบบกับแถวที่รูปแบบผิด
    และเป็นข้อที่เทสต์จับได้ ไม่ใช่ข้อที่เดาไว้ล่วงหน้า
    """
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError, UnicodeEncodeError):
        return False


# ── token ที่เราสุ่มเอง — SHA-256 ─────────────────────────────────────────────


def new_token() -> str:
    """ค่าสุ่มที่ไม่มีความหมายในตัว (spec/09 §6. session) — ไม่ใช่ JWT ไม่มี role อยู่ข้างใน"""
    return _stdlib_secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """สิ่งที่ลง DB · ของจริงอยู่ในคุกกี้หรือในลิงก์เท่านั้น"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(stored_hash: str, token: str) -> bool:
    """เทียบแบบเวลาคงที่ — การเทียบสตริงธรรมดารั่วความยาวของส่วนที่ตรงกัน"""
    return hmac.compare_digest(stored_hash, token_hash(token))


# ── TOTP secret — ciphertext ─────────────────────────────────────────────────


def encrypt_secret(plaintext: str, env: dict[str, str] | None = None) -> str:
    return _fernet(env).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, env: dict[str, str] | None = None) -> str:
    """คีย์ผิดหรือ ciphertext เสีย → `ValueError` ไม่ใช่ `None` เงียบๆ

    TOTP ที่ถอดไม่ออกแปลว่าบัญชีนั้นเข้าไม่ได้จนกว่าจะ reset 2FA ซึ่งเป็นเรื่อง
    ที่คนต้องรู้ทันที ไม่ใช่เรื่องที่ควรกลายเป็น "รหัสผิด" บนหน้าจอ
    """
    try:
        return _fernet(env).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            f"ถอด TOTP secret ไม่ได้ — {SECRET_KEY_ENV} ไม่ตรงกับตอนที่เข้ารหัสไว้"
        ) from exc
