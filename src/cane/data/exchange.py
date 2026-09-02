"""ตัวต่อ ccxt — ส่วนเดียวของชั้นข้อมูลที่แตะเครือข่ายจริง

ชั้นข้อมูล **ไม่อ่าน API key เลย** ทั้ง OHLCV และ funding rate เป็น public endpoint
ทำให้ข้อบังคับของ spec/07 ("profile paper ไม่อ่าน API key เลย") เป็นจริงด้วยโครงสร้าง
ไม่ใช่ด้วยความระมัดระวังของคนเขียน — ไม่มี credential ให้หลุดเพราะไม่เคยรับเข้ามา

แยกไฟล์นี้ออกมาเพื่อให้ตรรกะที่เหลือทดสอบได้โดยไม่ต่อเน็ต ตัว client ถูกฉีดเข้าไป
ทุกจุด ไม่มีใครสร้างเองข้างใน
"""

from __future__ import annotations

from typing import Any, Protocol

import ccxt

#: error ทั้งหมดของ ccxt สืบจากตัวนี้ — จับให้แคบไว้โดยเจตนา
#: `except Exception` จะกลบ `TypeError`/`KeyError` ที่เกิดจากบั๊กของเราเอง
#: ให้กลายเป็น "ดึงข้อมูลไม่ได้" ซึ่งซ่อนบั๊กไว้ในข้อมูลที่ดูปกติ
DATA_ERRORS: tuple[type[BaseException], ...] = (ccxt.BaseError,)


class ExchangeClient(Protocol):
    """สิ่งเดียวที่ชั้นข้อมูลต้องการจาก ccxt — เทสต์ปลอมได้ทั้งก้อน"""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list[float]]: ...

    def fetch_funding_rate(self, symbol: str) -> dict[str, Any]: ...


def perp_symbol(symbol: str) -> str:
    """`BTC/USDT` → `BTC/USDT:USDT` — unified symbol ของ USDT-M perp

    **จำเป็น ไม่ใช่ความสวยงาม** ccxt จับคู่ชื่อแบบตรงตัวจาก `markets` ก่อน แล้วจึงค่อย
    พึ่ง `defaultType` เฉพาะตอนค้นด้วย id ของ venue ผลคือ `BTC/USDT` ได้ตลาด **spot**
    มาเสมอแม้ตั้ง `defaultType` ไว้แล้ว และ `fetch_ohlcv` เลือก endpoint จาก
    `market['linear']` ของตลาดที่ได้ จึงไปหยิบแท่ง spot มาแทนแท่ง perp
    เงียบๆ — ข้อมูลผิดที่เทสต์ทุกตัวยังเขียว

    config เขียน `BTC/USDT` ตามเดิม (spec/07) การแปลงเป็นหน้าที่ของชั้นนี้
    """
    if ":" in symbol:
        return symbol
    base, sep, quote = symbol.partition("/")
    if not (base and sep and quote):
        raise ValueError(f"symbol ต้องอยู่รูป BASE/QUOTE ไม่ใช่ {symbol!r}")
    return f"{symbol}:{quote}"


def make_client(exchange: str) -> ExchangeClient:
    """สร้าง ccxt client ของ USDT-M perp **โดยไม่ส่ง key/secret**

    `defaultType = "swap"` เพราะ perp คือ swap ในคำศัพท์ของ ccxt ส่วน `"future"`
    หมายถึงสัญญาที่มีวันหมดอายุ ซึ่งไม่ใช่ตลาดของระบบนี้ (spec/07 `usdtm_perp`)
    """
    factory = getattr(ccxt, exchange, None)
    if factory is None:
        raise ValueError(f"ccxt ไม่รู้จัก exchange {exchange!r}")
    return factory({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
