"""ชั้นล่างสุดของ auth — TOTP, hash, ciphertext (spec/09 §TOTP, §9. ที่เก็บ)

ไม่แตะ DB และไม่แตะเน็ต · ทุกอย่างที่นี่เป็นฟังก์ชันล้วน

**test vector ของ TOTP มาจาก RFC 6238 Appendix B ตัวจริง** ไม่ใช่ค่าที่เราคำนวณเอง
แล้วบันทึกไว้ — เทสต์ที่เทียบกับผลของตัวเองผ่านเสมอ รวมถึงตอนที่อัลกอริทึมผิด
(รอบแรกของใบนี้ผมพิมพ์ vector ที่ 1111111109 ผิดจากความจำเป็น `07081094`
ของจริงคือ `07081804` — โค้ดถูกมาตลอด เทสต์ต่างหากที่ผิด)
"""

from __future__ import annotations

import base64

import pytest

from cane.auth import secrets as auth_secrets
from cane.auth import totp

#: ASCII "12345678901234567890" ตามที่ RFC 6238 ระบุ
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")

#: (เวลาเป็นวินาที, ค่า TOTP 8 หลักของโหมด SHA1) — Appendix B
RFC_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
)

KEY = {"CANE_SECRET_KEY": "x" * 48}


# ── TOTP ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("seconds,expected", RFC_VECTORS)
def test_the_generated_code_matches_the_rfc_6238_test_vectors(
    seconds: int, expected: str
) -> None:
    """เราใช้ 6 หลัก RFC พิมพ์ 8 หลัก — หกหลักท้ายคือค่าเดียวกัน"""
    assert totp.code(RFC_SECRET, totp.counter_at(seconds * 1000)) == expected[-6:]


def test_a_code_from_the_previous_or_next_window_is_accepted() -> None:
    """±1 ช่วง กันนาฬิกาเครื่องเหลื่อม (spec/09 §TOTP)"""
    now = 1_700_000_000_000
    centre = totp.counter_at(now)

    for counter in (centre - 1, centre, centre + 1):
        assert totp.verify(RFC_SECRET, totp.code(RFC_SECRET, counter), now=now) == counter


def test_a_code_from_two_windows_away_is_refused() -> None:
    now = 1_700_000_000_000
    centre = totp.counter_at(now)
    stale = totp.code(RFC_SECRET, centre - 2)

    assert totp.verify(RFC_SECRET, stale, now=now) is None


def test_a_code_that_already_worked_cannot_be_used_again_while_it_is_still_valid() -> None:
    """spec/09 §TOTP — ถ้าไม่ตรึงข้อนี้ รหัสที่หลุดตายังยิงซ้ำได้อีก 90 วินาที

    นานพอสำหรับการส่งต่อด้วยมือ ซึ่งเป็นภัยที่ 2FA มีไว้กันตั้งแต่แรก
    """
    now = 1_700_000_000_000
    used = totp.verify(RFC_SECRET, totp.code(RFC_SECRET, totp.counter_at(now)), now=now)
    assert used is not None

    again = totp.verify(
        RFC_SECRET, totp.code(RFC_SECRET, used), now=now, last_counter=used
    )
    assert again is None


def test_a_code_newer_than_the_last_one_still_works_after_a_replay_is_blocked() -> None:
    """กันซ้ำต้องไม่กลายเป็นการล็อกบัญชีตัวเองออกจากช่วงถัดไป"""
    now = 1_700_000_000_000
    used = totp.counter_at(now)
    later = now + totp.PERIOD_S * 1000

    assert (
        totp.verify(RFC_SECRET, totp.code(RFC_SECRET, used + 1), now=later, last_counter=used)
        == used + 1
    )


@pytest.mark.parametrize("junk", ["", "12345", "1234567", "abcdef", "12 456"])
def test_something_that_is_not_six_digits_is_refused_without_computing_anything(
    junk: str,
) -> None:
    assert totp.verify(RFC_SECRET, junk, now=1_700_000_000_000) is None


def test_a_fresh_secret_is_base32_without_padding() -> None:
    """แอปบนมือถืออ่าน `=` ท้าย base32 ไม่ตรงกันอยู่บ่อยๆ"""
    secret = totp.new_secret()

    assert "=" not in secret
    assert totp.code(secret, 1).isdigit()


def test_the_provisioning_uri_declares_the_same_parameters_the_verifier_uses() -> None:
    """QR ที่บอกค่าไม่ตรงกับตัวตรวจ = บัญชีที่ผูกแล้วแต่ login ไม่ได้"""
    uri = totp.provisioning_uri("ABC234", "someone@example.com")

    assert "algorithm=SHA1" in uri
    assert f"digits={totp.DIGITS}" in uri
    assert f"period={totp.PERIOD_S}" in uri


# ── backup code ──────────────────────────────────────────────────────────────


def test_a_fresh_set_has_ten_distinct_codes() -> None:
    codes = totp.new_backup_codes()

    assert len(codes) == totp.BACKUP_CODE_COUNT
    assert len(set(codes)) == totp.BACKUP_CODE_COUNT


def test_backup_codes_avoid_the_characters_people_read_as_each_other() -> None:
    """`I`/`1` และ `O`/`0` บนกระดาษคือรหัสที่ถูกแต่พิมพ์ไม่ผ่าน"""
    joined = "".join(totp.new_backup_codes(50)).replace("-", "")

    assert not (set(joined) & set("IO01"))


@pytest.mark.parametrize("typed", ["abcde-fghjk", "ABCDE FGHJK", " ABCDEFGHJK "])
def test_a_code_typed_loosely_normalises_to_the_same_thing(typed: str) -> None:
    assert totp.normalise_backup_code(typed) == "ABCDEFGHJK"


# ── การเก็บความลับ ───────────────────────────────────────────────────────────


def test_a_password_hash_is_argon2id_and_never_contains_the_password() -> None:
    stored = auth_secrets.hash_password("ความลับที่ยาวพอสมควร")

    assert stored.startswith("$argon2id$")
    assert "ความลับ" not in stored
    assert auth_secrets.verify_password(stored, "ความลับที่ยาวพอสมควร")
    assert not auth_secrets.verify_password(stored, "ความลับที่ยาวพอสมควรX")


def test_the_same_password_hashes_differently_every_time() -> None:
    """salt ต่อแถว — ตารางที่ hash ซ้ำกันบอกได้ว่าใครใช้รหัสผ่านเดียวกัน"""
    assert auth_secrets.hash_password("เหมือนกันเป๊ะ") != auth_secrets.hash_password(
        "เหมือนกันเป๊ะ"
    )


def test_a_broken_hash_reads_as_a_wrong_password_rather_than_an_error() -> None:
    """แถวที่เสียต้องไม่กลายเป็น 500 ที่หน้า login

    หน้าจอที่ตอบต่างออกไปสำหรับบัญชีหนึ่งคือการยืนยันให้คนที่กำลังเดาว่าบัญชีนั้นพิเศษ
    """
    assert not auth_secrets.verify_password("ไม่ใช่ hash", "อะไรก็ได้")


def test_a_totp_secret_round_trips_through_ciphertext() -> None:
    secret = totp.new_secret()
    stored = auth_secrets.encrypt_secret(secret, KEY)

    assert secret not in stored
    assert auth_secrets.decrypt_secret(stored, KEY) == secret


def test_a_ciphertext_written_with_another_key_refuses_loudly() -> None:
    """ADR 25 — คีย์หายแล้ว TOTP ของทุกคนใช้ไม่ได้ · ต้องรู้ทันที ไม่ใช่ "รหัสผิด" """
    stored = auth_secrets.encrypt_secret("JBSWY3DPEHPK3PXP", KEY)

    with pytest.raises(ValueError, match="CANE_SECRET_KEY"):
        auth_secrets.decrypt_secret(stored, {"CANE_SECRET_KEY": "y" * 48})


def test_the_same_secret_encrypts_differently_every_time() -> None:
    """ciphertext ที่ซ้ำกันบอกได้ว่าสองบัญชีมี secret เดียวกัน"""
    assert auth_secrets.encrypt_secret("JBSWY3DPEHPK3PXP", KEY) != (
        auth_secrets.encrypt_secret("JBSWY3DPEHPK3PXP", KEY)
    )


@pytest.mark.parametrize("bad", [{}, {"CANE_SECRET_KEY": ""}, {"CANE_SECRET_KEY": "สั้น"}])
def test_a_missing_or_short_secret_key_fails_at_setup_not_at_first_use(
    bad: dict[str, str],
) -> None:
    """เหตุผลเดียวกับ `CANE_DB_DSN` — ค่าตั้งต้นที่ซ่อนอยู่คือค่าที่ prod ใช้จริงโดยไม่มีใครรู้"""
    with pytest.raises(RuntimeError, match="CANE_SECRET_KEY"):
        auth_secrets.secret_key(bad)


def test_a_session_token_is_stored_only_as_a_hash() -> None:
    token = auth_secrets.new_token()
    stored = auth_secrets.token_hash(token)

    assert token not in stored
    assert auth_secrets.tokens_match(stored, token)
    assert not auth_secrets.tokens_match(stored, auth_secrets.new_token())
