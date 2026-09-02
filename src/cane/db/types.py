"""จุดแปลงชนิดข้อมูลที่ขอบระหว่าง Postgres กับโค้ด — **ที่เดียวเท่านั้น**

กฎที่ไฟล์นี้ถือไว้ และเป็นเหตุผลที่มันมีอยู่:

- **ราคาที่เข้าสูตร indicator เป็น `float`** สูตร Action Zone ถูกเทียบกับ TradingView
  ด้วย golden test (#04) ซึ่งคำนวณด้วยเลขทศนิยมฐานสอง การเปลี่ยนไปเป็น `Decimal`
  จะทำให้ค่าที่ได้ต่างจากของ TradingView ในหลักท้ายๆ แล้วเทียบไม่ผ่านทั้งชุด
- **เงินใน ledger เป็น `Decimal`** ค่าธรรมเนียม funding และกำไรขาดทุนต้องบวกกัน
  ได้ยอดที่ตรงกับใบแจ้งของ venue เลขทศนิยมฐานสองบวกกันหลายพันครั้งแล้วเพี้ยน
- ใน DB ทั้งคู่เก็บเป็น `NUMERIC` เพราะที่เก็บไม่ควรตัดสินความละเอียดแทนเรา

การแปลง `float` → `Decimal` **ต้องผ่าน `str` เสมอ** — `Decimal(0.1)` ให้
`0.1000000000000000055511151231257827` ซึ่งเป็นค่าจริงของ float ตัวนั้นแบบไม่ปัด
พอ quantize แล้วยังเห็นขยะติดมาในหลักท้าย ส่วน `Decimal(str(0.1))` ให้ `0.1` ตรงตัว
"""

from __future__ import annotations

import time
from decimal import ROUND_HALF_EVEN, Decimal

#: ทศนิยมของราคาและปริมาณ — ตรงกับ NUMERIC(24,8) ใน schema
PRICE_SCALE = 8
#: ทศนิยมของอัตรา funding — ตรงกับ NUMERIC(12,10) · funding เป็นเลขเล็กมาก
#: (ระดับ 0.0001) การปัดที่ 8 ตำแหน่งจะกินนัยสำคัญของมันไปเลย
RATE_SCALE = 10

_PRICE_QUANT = Decimal(1).scaleb(-PRICE_SCALE)
_RATE_QUANT = Decimal(1).scaleb(-RATE_SCALE)


def now_ms() -> int:
    """เวลานาฬิกาเป็น epoch มิลลิวินาที

    ทั้งระบบใช้ `BIGINT` epoch ms ไม่มี `datetime` ใน `src/` เลย — เวลาที่มี timezone
    ติดมาด้วยคือแหล่งของบั๊กที่โผล่ตอนข้ามวันของ UTC เท่านั้น ซึ่งเป็นขอบวันที่
    risk limit ของ spec/06 ใช้จริง
    """
    return int(time.time() * 1000)


def _to_numeric(value: float | int | Decimal, quant: Decimal) -> Decimal:
    if isinstance(value, Decimal):
        dec = value
    else:
        dec = Decimal(str(value))
    return dec.quantize(quant, rounding=ROUND_HALF_EVEN)


def price_to_db(value: float | int | Decimal) -> Decimal:
    """ราคา/ปริมาณ → `NUMERIC(24,8)`

    ปัดที่นี่โดยเจตนา ไม่ปล่อยให้ Postgres ปัดเอง — ถ้าปล่อย การปัดจะเกิดในที่ที่
    ไม่มีเทสต์ไหนเห็นและเปลี่ยนไปตามชนิดคอลัมน์ที่ migration ในอนาคตแก้ได้
    """
    return _to_numeric(value, _PRICE_QUANT)


def price_from_db(value: Decimal | float | int) -> float:
    """`NUMERIC` → `float` สำหรับป้อนสูตร indicator"""
    return float(value)


def rate_to_db(value: float | int | Decimal) -> Decimal:
    """อัตรา funding → `NUMERIC(12,10)`"""
    return _to_numeric(value, _RATE_QUANT)


def rate_from_db(value: Decimal | float | int) -> float:
    return float(value)


def store_symbol(symbol: str) -> str:
    """`BTC/USDT:USDT` → `BTC/USDT` — ในตารางเก็บรูปเดียวกับที่ config เขียน

    ชั้นข้อมูลแปลงเป็น unified symbol ของ perp (`:USDT` ต่อท้าย) ก่อนคุยกับ ccxt
    ส่วน config, รายงาน และคอนโซลใช้รูปสั้น (spec/07) ถ้าปล่อยให้ทั้งสองรูปลงตาราง
    ได้ เหรียญเดียวจะมีสองแถวประวัติที่ไม่มีใครสังเกต — normalize ที่ขอบของ DB
    ให้เหลือรูปเดียว ตลาดของระบบนี้เป็น `usdtm_perp` อยู่แล้ว รูปสั้นจึงไม่กำกวม
    """
    base, sep, _ = symbol.partition(":")
    return base if sep else symbol
