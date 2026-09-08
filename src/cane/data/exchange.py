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


def unified_symbol(symbol: str, market: str) -> str:
    """รูปสั้นของ config → unified symbol ของ ccxt ตามตลาดที่ระบุ

    `usdtm_perp` · `BTC/USDT` → `BTC/USDT:USDT` — **จำเป็น ไม่ใช่ความสวยงาม**
    ccxt จับคู่ชื่อแบบตรงตัวจาก `markets` ก่อน แล้วจึงค่อยพึ่ง `defaultType` เฉพาะตอน
    ค้นด้วย id ของ venue ผลคือ `BTC/USDT` ได้ตลาด **spot** มาเสมอแม้ตั้ง `defaultType`
    ไว้แล้ว และ `fetch_ohlcv` เลือก endpoint จาก `market['linear']` ของตลาดที่ได้ จึงไป
    หยิบแท่ง spot มาแทนแท่ง perp เงียบๆ — ข้อมูลผิดที่เทสต์ทุกตัวยังเขียว

    `spot` · `BTC/USDT` → `BTC/USDT` — รูปสั้นคือ unified symbol ของ spot อยู่แล้ว

    config เขียน `BTC/USDT` ตามเดิมทั้งสองตลาด (spec/07) และ `store_symbol()` ตัด
    `:USDT` ทิ้งก่อนลงตาราง — มิติ market อยู่ในคอลัมน์ ไม่ได้เข้ารหัสไว้ในสตริง
    (ADR 26) การแปลงกลับเป็นชื่อที่ venue รู้จักจึงเป็นหน้าที่ของชั้นนี้ที่เดียว

    **ปฏิเสธ symbol ที่ต่อท้ายมาแล้ว** ไม่ทำเป็นฟังก์ชันที่เรียกซ้ำได้ (idempotent)
    เพราะ `BTC/USDT:USDT` ที่โผล่มาถึงชั้นนี้หมายถึงมีใครแปลงไปแล้วหนึ่งรอบ ซึ่งเป็น
    บั๊กของผู้เรียก ไม่ใช่ของข้อมูล — ถ้าปล่อยผ่านเงียบๆ market ที่ส่งมาจะไม่มีผล
    อะไรเลย และ `unified_symbol("BTC/USDT:USDT", "spot")` จะคืนชื่อ perp ให้ฝั่ง spot
    """
    base, sep, quote = symbol.partition("/")
    if not (base and sep and quote):
        raise ValueError(f"symbol ต้องอยู่รูป BASE/QUOTE ไม่ใช่ {symbol!r}")
    if ":" in quote:
        raise ValueError(f"symbol ต้องเป็นรูปสั้นตาม spec/07 ไม่ใช่ {symbol!r}")
    if market == "usdtm_perp":
        return f"{symbol}:{quote}"
    if market == "spot":
        return symbol
    raise ValueError(f"market ที่ไม่รู้จัก: {market!r}")


def default_type(market: str) -> str:
    """market ของระบบ → `defaultType` ในคำศัพท์ของ ccxt

    `usdtm_perp` → `"swap"` เพราะ perp คือ swap ในคำศัพท์ของ ccxt ส่วน `"future"`
    หมายถึงสัญญาที่มีวันหมดอายุ ซึ่งไม่ใช่ตลาดของระบบนี้ (spec/07 `usdtm_perp`)

    market ที่ไม่รู้จักต้องดัง ไม่ใช่ตกลง default — การเดาตลาดผิดคือการดึงแท่งของอีก
    ตลาดมาโดยที่ไม่มีอะไรส่งเสียง ซึ่งเป็นบั๊กข้อมูลแบบเดียวกับที่ ADR 26 มาปิด
    """
    if market == "usdtm_perp":
        return "swap"
    if market == "spot":
        return "spot"
    raise ValueError(f"market ที่ไม่รู้จัก: {market!r}")


def make_client(exchange: str, market: str) -> ExchangeClient:
    """สร้าง ccxt client ของตลาดหนึ่งตลาด **โดยไม่ส่ง key/secret**

    **หนึ่ง client ต่อหนึ่งตลาด** เพราะ `defaultType` เป็นค่าของทั้ง instance
    profile ที่ถือทั้ง perp และ spot (ADR 26) จึงต้องมี client สองตัว ไม่ใช่ตัวเดียว
    ที่สลับไปมา — ทางเลือกอีกทางคือส่ง `params={"type": ...}` ต่อคำสั่ง ซึ่งเป็น
    การตัดสินใจของใบ 12/13 ตอนที่มีเส้นทางออกคำสั่งจริงให้ดู
    """
    factory = getattr(ccxt, exchange, None)
    if factory is None:
        raise ValueError(f"ccxt ไม่รู้จัก exchange {exchange!r}")
    return factory({
        "enableRateLimit": True,
        "options": {"defaultType": default_type(market)},
    })
