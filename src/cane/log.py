"""ตัวกรอง log ที่ลบค่าที่เป็นความลับออกก่อนเขียน

ระบบนี้ log เยอะโดยเจตนา (DecisionRecord ทุกแท่ง) ความเสี่ยงที่ตามมาคือ
credential หลุดลงไฟล์ log จึงกรองที่ขาออกเสมอ ไม่ใช่หวังว่าจะไม่มีใครเผลอ log

**ตั้งใจไม่ลบเลขล้วนยาวๆ** — `bar_close_ts` เป็น epoch มิลลิวินาที 13 หลัก
ถ้ากรองด้วยความยาวตัวเลข บันทึกจะอ่านไม่ออกทั้งระบบ (spec/06)
"""

from __future__ import annotations

import logging
import re

MASK = "***"

#: `api_key = "abc"` / `{"secret": "abc"}` / `token: abc`
#: จับที่ชื่อคีย์ ไม่ใช่ที่รูปร่างของค่า — ค่าที่เป็นความลับหน้าตาเหมือนค่าธรรมดา
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<head>
        ["']? [\w.\-]*
        (?: key | secret | token | password | passphrase )
        [\w.\-]* ["']?
        \s* [=:] \s*
    )
    (?:
        " (?P<dq> [^"]* ) "
      | ' (?P<sq> [^']* ) '
      |   (?P<bare> [^\s,;}\]]+ )
    )
    """
)

#: address แบบ `0x…` ยาวพอที่จะไม่ใช่เลขฐานสิบหกทั่วไป
_HEX_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")


def _mask_assignment(match: re.Match[str]) -> str:
    head = match["head"]
    if match["dq"] is not None:
        return f'{head}"{MASK}"'
    if match["sq"] is not None:
        return f"{head}'{MASK}'"
    return f"{head}{MASK}"


def redact(text: str) -> str:
    """คืนข้อความที่ค่าของคีย์อ่อนไหวถูกแทนด้วย `***` แล้ว"""
    masked = _SECRET_ASSIGNMENT.sub(_mask_assignment, text)
    return _HEX_ADDRESS.sub("0x…", masked)


class RedactingFilter(logging.Filter):
    """กรองทุก record ที่ผ่าน handler ที่ติดตัวกรองนี้

    **ประกอบข้อความให้เสร็จก่อนแล้วค่อยกรอง** เพราะ `log.info("api_key=%s", secret)`
    ซ่อนค่าไว้ใน args ถ้ากรองแต่ `msg` ค่าจริงจะรอดออกไป และถ้ากรอง `msg`
    ก่อนประกอบ ตัว `%s` จะถูกกลบจนจำนวน placeholder ไม่ตรงกับ args

    ผลข้างเคียงที่ยอมรับ: record ที่ผ่านตัวกรองนี้จะไม่มี `args` เหลือให้
    formatter แบบ structured ใช้ต่อ — บันทึกที่ต้องอ่านด้วยเครื่องคือ
    DecisionRecord ซึ่งเขียนเป็น JSONL ผ่านทางของตัวเอง ไม่ผ่าน logging
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            # ประกอบไม่ได้ ปล่อยผ่านให้ logging รายงาน error ตามปกติ
            return True
        record.msg = redact(message)
        record.args = ()
        return True


def install(handler: logging.Handler) -> logging.Handler:
    """ติดตัวกรองเข้ากับ handler แล้วคืน handler ตัวเดิม"""
    handler.addFilter(RedactingFilter())
    return handler
