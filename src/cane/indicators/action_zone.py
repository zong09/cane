"""CDC Action Zone — พอร์ตจาก `reference/cdc_action_zone.pine` (spec/02)

โซนคือตำแหน่งของราคาเทียบ EMA สองเส้น สัญญาณคือการเปลี่ยนโซนที่ **เกิดตอนสถานะ
ตรงข้าม** ไม่ใช่แค่การเปลี่ยนสี — สองประโยคนี้คือทั้งไฟล์

**ไม่ใช้ pandas และไม่แตะ ccxt** ราคาเข้าสูตรเป็น `float` ตามที่ `db/types.py` ถือกฎไว้
(`Decimal` จะให้เลขต่างจาก TradingView ในหลักท้ายๆ แล้ว golden test เทียบไม่ผ่านทั้งชุด)
`Bar` ถูกนำเข้าใต้ `TYPE_CHECKING` เท่านั้น เพราะ `cane.data` ลาก ccxt เข้ามาด้วย และ
สูตร indicator ไม่ควรต้องมี exchange client อยู่ในเครื่องจึงจะทดสอบได้

## สามจุดที่พลาดง่ายตอน port (spec/02:71-85)

1. **`long_signal` ไม่เท่ากับ "แท่งเขียวแรก"** — ต้องมี `state` ของแท่งก่อนหน้าเป็น
   `BEARISH` ด้วย เขียวแรกที่โผล่ระหว่างที่ระบบยัง `BULLISH` (ราคาย่อไปโซนเหลือง
   แล้วเด้งกลับ) เป็น `longcond` แต่ **ไม่ใช่สัญญาณ**
2. **`barssince` ที่ยังไม่เคยเกิดคือ `na` ไม่ใช่อนันต์** — ที่นี่คือ `None` และการ
   เทียบใดๆ กับมันให้ผล false เหมือน Pine ทุกกรณี ห้ามแทนด้วย `+inf` เพราะจะทำให้
   `BEARISH` เป็นจริงก่อนเวลาแล้วเกิดสัญญาณผี · ผลตามมาคือ `longcond` ตัวแรกสุด
   ของชุดข้อมูล **ไม่เคยเป็นสัญญาณ** เว้นแต่มี `shortcond` มาก่อนหน้า
3. **ไม่รองรับโหมด fixed timeframe** (`xfixtf`) ของสคริปต์ต้นทาง มันใช้
   `request.security` กับ `lookahead_on` ซึ่งขัดกับหลักที่ระบบตัดสินบนแท่งปิดแล้ว

## การ seed EMA — จุดที่ golden test ตัดสิน ไม่ใช่การอ่านโค้ด

`pine_ema()` ทำตาม reference implementation ที่คู่มือ Pine เขียนไว้สำหรับ `ta.ema`
คือ seed ด้วยค่าแรกของ source ตรงๆ (ซึ่งเท่ากับ `pandas.ewm(adjust=False)`) **ยังไม่มี
หลักฐานว่าฟังก์ชัน built-in ทำตามคู่มือของตัวเองเป๊ะ** — บางแหล่งบอกว่ามัน seed ด้วย
SMA ของ `length` แท่งแรกและคืน `na` ก่อนนั้น อ่านโค้ดชี้ขาดไม่ได้ ต้องมีไฟล์จริงจาก
TradingView มาเทียบ (ใบ 04)

ถ้า golden test ไม่ตรงหลังตัด warm-up **ให้แก้ที่ `pine_ema()` จุดเดียว** ไม่ใช่ขยาย
ช่วงที่ตัดทิ้ง · แต่พึงรู้ว่าอิทธิพลของ seed หลัง 130 แท่งเหลือราว `(25/27)**130`
≈ 4e-5 ของผลต่างตอนเริ่ม สาเหตุที่น่าจะจริงกว่าคือการจัดแถวเวลาหรือการปัดเศษ

**130 กับ 85 คนละเรื่องกัน** (spec/02:97-104) — 130 (`5 × slow`) คือช่วงหัวชุดข้อมูล
ที่ตัดทิ้ง *ตอนเทียบ golden test* เท่านั้น · 85 คือเกณฑ์ "พร้อมเทรด" ของ symbol ซึ่ง
อยู่ที่ `data.MIN_CLOSED_BARS`
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cane.data.ohlcv import Bar

#: ค่าตั้งต้นของสคริปต์ต้นทาง (spec/02:10-13) · `smooth = 1` ทำให้ `xPrice = close`
#: ตรงตัว (alpha = 2/(1+1) = 1) แต่ยังต้องคง parameter ไว้เพราะผู้ใช้เปลี่ยนได้
FAST_PERIOD = 12
SLOW_PERIOD = 26
SMOOTH_PERIOD = 1


@dataclass(frozen=True, slots=True)
class ActionZone:
    """ผลของหนึ่งแท่ง — ชื่อฟิลด์ตรงกับคอลัมน์ของ `DecisionRecord` โดยเจตนา

    `close_px` คือราคาปิดของแท่ง ส่วนค่าที่ **เข้าสูตรเทียบกับ EMA** คือ `xPrice`
    ซึ่งเท่ากับราคาปิดเมื่อ `smooth = 1` (ค่าตั้งต้น) ถ้าตั้ง `smooth > 1` การเอา
    `close_px` ไปเทียบ `fast_ma` เองจะได้โซนไม่ตรงกับ `zone` — ใช้ `zone` ที่ให้มา

    `bars_since_long` / `bars_since_short` เป็น `None` แปลว่า **ยังไม่เคยเกิด**
    (`na` ของ Pine) ไม่ใช่ "นานมาก" — เก็บออกมาด้วยเพราะนี่คือที่ที่ `na` อยู่ และ
    การตรวจ semantics ของมันตรงๆ ทำได้เฉพาะเมื่อมันไม่ถูกซ่อนไว้ในลูป
    """

    bar_close_ts: int
    close_px: float
    fast_ma: float
    slow_ma: float
    zone: str
    state: str
    longcond: bool
    shortcond: bool
    long_signal: bool
    short_signal: bool
    bars_since_long: int | None
    bars_since_short: int | None


def pine_ema(values: Sequence[float], length: int) -> list[float]:
    """EMA ตามสูตรของ `ta.ema` — `alpha = 2/(length+1)` seed ด้วยค่าแรกของ source

    `length = 1` ให้ `alpha = 1.0` พอดี ผลลัพธ์จึงเท่ากับ source ทุกตัวแบบไม่มี
    ความคลาดเคลื่อน (`1.0*v + 0.0*prev`) ซึ่งเป็นเหตุผลที่ `smooth = 1` ใช้ได้
    โดยไม่ต้องมีทางแยกพิเศษในโค้ด

    ไม่คืน `na`/`None` ช่วงหัว — ดูหัวข้อการ seed ในเอกสารหัวไฟล์ว่าทำไม และว่า
    ที่นี่คือจุดเดียวที่ต้องแก้ถ้า golden test ชี้ว่าผิด
    """
    if length < 1:
        raise ValueError(f"คาบของ EMA ต้อง >= 1 ไม่ใช่ {length}")

    alpha = 2.0 / (length + 1.0)
    out: list[float] = []
    prev: float | None = None
    for value in values:
        prev = value if prev is None else alpha * value + (1.0 - alpha) * prev
        out.append(prev)
    return out


def zone_of(px: float, fast_ma: float, slow_ma: float) -> str:
    """โซนของหนึ่งแท่งจาก (`xPrice`, FastMA, SlowMA) — ฟังก์ชันบริสุทธิ์

    แยกออกมาเป็นฟังก์ชันสาธารณะเพราะ golden test เทียบกับไฟล์ export ที่มีคอลัมน์
    `close` `Fast EMA` `Slow EMA` อยู่แล้ว (`barcolor` ของ Pine ไม่ออกมาใน CSV แต่
    `plot` ออก) จึงเทียบได้โดยไม่ต้องสร้าง `Bar` หรือคำนวณ EMA ซ้ำ

    เขียนเรียงทีละเงื่อนไขตามไฟล์ Pine โดยเจตนา (ไม่ยุบเป็น if ซ้อน) เพื่อให้
    ตรวจทานเทียบบรรทัดต่อบรรทัดกับต้นฉบับได้ · `BLACK` คือช่องว่างที่เหลือจากการที่
    เงื่อนไขทั้งหกใช้ `>` และ `<` ล้วน — `fast_ma == slow_ma` หรือ `px == fast_ma`
    พอดีจึงไม่เข้าโซนใดเลย ซึ่งเป็นสถานะจริง ไม่ใช่ค่าที่หายไป
    """
    bull = fast_ma > slow_ma
    bear = fast_ma < slow_ma

    green = bull and px > fast_ma
    blue = bear and px > fast_ma and px > slow_ma
    lblue = bear and px > fast_ma and px < slow_ma
    red = bear and px < fast_ma
    orange = bull and px < fast_ma and px < slow_ma
    yellow = bull and px < fast_ma and px > slow_ma

    if green:
        return "GREEN"
    if blue:
        return "BLUE"
    if lblue:
        return "LBLUE"
    if red:
        return "RED"
    if orange:
        return "ORANGE"
    if yellow:
        return "YELLOW"
    return "BLACK"


def action_zones(
    bars: Sequence[Bar],
    *,
    fast: int = FAST_PERIOD,
    slow: int = SLOW_PERIOD,
    smooth: int = SMOOTH_PERIOD,
) -> list[ActionZone]:
    """คำนวณทุกแท่งเรียงตามเวลา — คืนรายการยาวเท่า `bars`

    ต้องคำนวณทั้งชุดไม่ใช่ทีละแท่ง เพราะ `state` ของแท่งหนึ่งขึ้นกับว่า `longcond`
    กับ `shortcond` เกิดล่าสุดเมื่อไหร่ ซึ่งเป็นประวัติที่ย้อนกลับไปได้ไม่จำกัด
    ผู้เรียกในไปป์ไลน์ใช้ตัวท้ายสุด (`action_zones(bars)[-1]`)

    `bars` ต้องเรียงจากเก่าไปใหม่ตามที่ `data.closed_as_of()` คืนมา — ฟังก์ชันนี้
    ไม่เรียงให้และไม่ตรวจ เพราะการเรียงเงียบๆ จะกลบบั๊กของผู้เรียกที่ส่งย้อนลำดับมา
    """
    prices = pine_ema([bar.close for bar in bars], smooth)
    fast_line = pine_ema(prices, fast)
    slow_line = pine_ema(prices, slow)

    out: list[ActionZone] = []
    prev_zone: str | None = None
    prev_state: str | None = None
    since_long: int | None = None
    since_short: int | None = None

    for bar, px, fast_ma, slow_ma in zip(
        bars, prices, fast_line, slow_line, strict=True
    ):
        zone = zone_of(px, fast_ma, slow_ma)

        # `Green[1] == 0` ของแท่งแรกเป็น `na` ใน Pine (ไม่ใช่ false) และ
        # `Green and na` ให้ false — แท่งแรกจึงไม่เป็น `longcond` แม้จะเขียว
        # `prev_zone is None` คือ `na` ตัวนั้น ไม่ใช่ "ไม่รู้แล้วเดาว่าไม่เขียว"
        #
        # ด้วยวิธี seed ปัจจุบัน EMA ทั้งสองเส้นของแท่งแรกเท่ากัน (= ราคาปิดแท่งแรก)
        # แท่งแรกจึงเป็น `BLACK` เสมอ เงื่อนไขนี้จะไม่มีวันได้ทำงาน — คงไว้เพราะ
        # วิธี seed เป็นข้อที่รอ golden test ตัดสิน ไม่ใช่ข้อที่นิ่งแล้ว
        longcond = zone == "GREEN" and prev_zone not in (None, "GREEN")
        shortcond = zone == "RED" and prev_zone not in (None, "RED")

        if longcond:
            since_long = 0
        elif since_long is not None:
            since_long += 1
        if shortcond:
            since_short = 0
        elif since_short is not None:
            since_short += 1

        # ทั้ง `bullish` และ `bearish` ของ Pine เทียบ `barssince` สองตัวตรงๆ
        # การเทียบกับ `na` ให้ false เสมอ → ยังไม่เกิดครบทั้งสองอย่าง = `UNSET`
        # และเพราะ GREEN กับ RED เกิดพร้อมกันไม่ได้ ค่าเท่ากันจึงเกิดได้เฉพาะตอน
        # ที่ทั้งคู่ยังเป็น `None` — เงื่อนไขนี้จึงครอบ `UNSET` ไว้ครบเองโดยไม่ต้อง
        # มีสาขาที่วิ่งไปไม่ถึง
        known = since_long is not None and since_short is not None
        if known and since_long < since_short:
            state = "BULLISH"
        elif known and since_short < since_long:
            state = "BEARISH"
        else:
            state = "UNSET"

        # `bearish[1]` — สถานะของ **แท่งก่อนหน้า** ไม่ใช่แท่งนี้ · แท่งแรกไม่มี
        # แท่งก่อนหน้า (`prev_state is None` = `na`) จึงไม่มีสัญญาณได้เลย
        long_signal = prev_state == "BEARISH" and longcond
        short_signal = prev_state == "BULLISH" and shortcond

        out.append(
            ActionZone(
                bar_close_ts=bar.close_ts,
                close_px=bar.close,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                zone=zone,
                state=state,
                longcond=longcond,
                shortcond=shortcond,
                long_signal=long_signal,
                short_signal=short_signal,
                bars_since_long=since_long,
                bars_since_short=since_short,
            )
        )
        prev_zone = zone
        prev_state = state

    return out
