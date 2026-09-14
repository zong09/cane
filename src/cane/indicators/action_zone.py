"""CDC Action Zone — พอร์ตจาก `reference/cdc_action_zone.pine` (spec/02)

โซนคือตำแหน่งของราคาเทียบ EMA สองเส้น สัญญาณคือการเปลี่ยนโซนที่ **เกิดตอนสถานะ
ตรงข้าม** ไม่ใช่แค่การเปลี่ยนสี — สองประโยคนี้คือทั้งไฟล์

**ไม่ใช้ pandas และไม่แตะ ccxt** ราคาเข้าสูตรเป็น `float` ตามที่ `db/types.py` ถือกฎไว้
(`Decimal` จะให้เลขต่างจาก TradingView ในหลักท้ายๆ แล้ว golden test เทียบไม่ผ่านทั้งชุด)
`Bar` ถูกนำเข้าใต้ `TYPE_CHECKING` เท่านั้น เพราะ `cane.data` ลาก ccxt เข้ามาด้วย และ
สูตร indicator ไม่ควรต้องมี exchange client อยู่ในเครื่องจึงจะทดสอบได้

## สามจุดที่พลาดง่ายตอน port (spec/02 §สามจุดที่พลาดง่ายตอน port)

1. **`long_signal` ไม่เท่ากับ "แท่งเขียวแรก"** — ต้องมี `state` ของแท่งก่อนหน้าเป็น
   `BEARISH` ด้วย เขียวแรกที่โผล่ระหว่างที่ระบบยัง `BULLISH` (ราคาย่อไปโซนเหลือง
   แล้วเด้งกลับ) เป็น `longcond` แต่ **ไม่ใช่สัญญาณ**
2. **`barssince` ที่ยังไม่เคยเกิดคือ `na` ไม่ใช่อนันต์** — ที่นี่คือ `None` และการ
   เทียบใดๆ กับมันให้ผล false เหมือน Pine ทุกกรณี ห้ามแทนด้วย `+inf` เพราะจะทำให้
   `BEARISH` เป็นจริงก่อนเวลาแล้วเกิดสัญญาณผี · ผลตามมาคือ `longcond` ตัวแรกสุด
   ของชุดข้อมูล **ไม่เคยเป็นสัญญาณ** เว้นแต่มี `shortcond` มาก่อนหน้า
3. **ไม่รองรับโหมด fixed timeframe** (`xfixtf`) ของสคริปต์ต้นทาง มันใช้
   `request.security` กับ `lookahead_on` ซึ่งขัดกับหลักที่ระบบตัดสินบนแท่งปิดแล้ว

## การ seed EMA — ตัดสินแล้วด้วยไฟล์จริง ไม่ใช่ด้วยการอ่านคู่มือ

คู่มือ Pine เขียน reference implementation ของ `ta.ema` ว่า seed ด้วยค่าแรกของ source
ตรงๆ (เท่ากับ `pandas.ewm(adjust=False)`) · **ฟังก์ชัน built-in ไม่ได้ทำตามนั้น** —
ไฟล์ export จาก TradingView เว้นช่องว่างไว้ `length - 1` แท่งพอดีทั้งสองเส้น (11 กับ
25) และค่าแรกเท่ากับ SMA ของ `length` แท่งแรกเป๊ะ · `pine_ema()` จึง seed ด้วย SMA
และคืน `None` ก่อนแท่งที่ `length` ตามนั้น (`tests/test_action_zone.py` บล็อก golden)

ผลตามมาที่ต้องรู้: **เส้น EMA เป็น `None` ได้** โซนของแท่งที่ยังไม่มีค่าจึงเป็น
`BLACK` ตามกฎของ Pine ที่การเทียบกับ `na` ให้ false เสมอ ไม่ใช่ค่าที่หายไป

ของเดิม (seed ด้วยค่าแรก) ต่างจากไฟล์ 88 แท่งบนเส้นเร็วและ 253 แท่งบนเส้นช้า แต่
**ไม่ทำให้โซนหรือสัญญาณเปลี่ยนแม้แท่งเดียว** — วัดกับไฟล์ 2564 แท่งแล้ว การแก้นี้
จึงเป็นการทำให้ตัวเลขตรงกับต้นทาง ไม่ใช่การแก้สัญญาณที่ผิด

**130 กับ 85 คนละเรื่องกัน** (spec/02 §warm-up สองตัวเลข — คนละเรื่องกัน) — 130 (`5 × slow`) คือช่วงหัวชุดข้อมูล
ที่ตัดทิ้ง *ตอนเทียบ golden test* เท่านั้น · 85 คือเกณฑ์ "พร้อมเทรด" ของ symbol ซึ่ง
อยู่ที่ `data.MIN_CLOSED_BARS`
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cane.data.ohlcv import Bar

#: ค่าตั้งต้นของสคริปต์ต้นทาง (spec/02 §พารามิเตอร์) · `smooth = 1` ทำให้ `xPrice = close`
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

    `fast_ma` / `slow_ma` เป็น `None` ช่วงหัวชุดข้อมูลจนกว่า EMA จะ seed ได้ (`na`
    ของ Pine) — ดูหัวข้อการ seed ที่หัวไฟล์ · `zone` ของแท่งพวกนั้นคือ `BLACK`

    `bars_since_long` / `bars_since_short` เป็น `None` แปลว่า **ยังไม่เคยเกิด**
    (`na` ของ Pine) ไม่ใช่ "นานมาก" — เก็บออกมาด้วยเพราะนี่คือที่ที่ `na` อยู่ และ
    การตรวจ semantics ของมันตรงๆ ทำได้เฉพาะเมื่อมันไม่ถูกซ่อนไว้ในลูป
    """

    bar_close_ts: int
    close_px: float
    fast_ma: float | None
    slow_ma: float | None
    zone: str
    state: str
    longcond: bool
    shortcond: bool
    long_signal: bool
    short_signal: bool
    bars_since_long: int | None
    bars_since_short: int | None


def pine_ema(values: Sequence[float | None], length: int) -> list[float | None]:
    """EMA ตามที่ `ta.ema` ทำจริง — seed ด้วย SMA ของ `length` แท่งแรก ก่อนนั้นเป็น `None`

    `alpha = 2/(length+1)` เหมือนที่คู่มือเขียน ต่างกันแค่จุดตั้งต้น · `length = 1`
    ให้ `alpha = 1.0` และ SMA ของหนึ่งแท่งคือตัวมันเอง ผลลัพธ์จึงเท่ากับ source ทุกตัว
    แบบไม่มีความคลาดเคลื่อน ซึ่งเป็นเหตุผลที่ `smooth = 1` ใช้ได้โดยไม่ต้องมีทางแยก

    รับ `None` ช่วงหัวได้เพราะผลของมันเป็น input ของตัวมันเองอีกชั้น (`xPrice` →
    FastMA/SlowMA) และ `Hst` ที่ `trailing.py` ส่งเข้ามาก็มีหัวเป็น `None` · Pine ให้
    `ema()` ของ `na` เป็น `na` แล้วเริ่มนับเมื่อ source มีค่า — ที่นี่ทำแบบเดียวกันโดย
    ตัดหัวออกก่อน ไม่ใช่แทนด้วย 0.0 ซึ่งเป็นค่าต่ำผิดปกติที่จะดึง EMA ลงหลายสิบแท่ง

    **ขอบเขตของหลักฐาน**: ไฟล์ export ยืนยันการ seed ได้เฉพาะ source ที่**ไม่มี `na`
    หัว** (`smooth = 1` → `xPrice = close`) · การนับใหม่เมื่อ source มี `na` หัว —
    ซึ่งคือทาง `smooth > 1` และ `Sig = ta.ema(Hst, 9)` ของ `trailing.py` — เดินตาม
    คำอธิบายของคู่มือ ยังไม่มีไฟล์เทียบ · ใบ 04 ข้อ 5 (export ที่มี CDC ATR Trailing
    Stop มาด้วย) จะตัดสินข้อนี้พร้อมกับการ seed ของ ATR ในคราวเดียว

    รูที่ **กลาง** เส้นเป็นคนละเรื่องและเกิดไม่ได้จากผู้เรียกที่มีอยู่ — โยน
    `ValueError` แทนที่จะกลืน เพราะมันแปลว่าผู้เรียกส่งของที่ไม่ควรมีมา
    """
    if length < 1:
        raise ValueError(f"คาบของ EMA ต้อง >= 1 ไม่ใช่ {length}")

    start = next((i for i, v in enumerate(values) if v is not None), len(values))
    defined = [v for v in values[start:] if v is not None]
    if len(defined) != len(values) - start:  # pragma: no cover - ดู docstring
        raise ValueError("source ของ EMA มีรูกลางเส้น ซึ่งผู้เรียกไม่ควรสร้างได้")
    if len(defined) < length:
        return [None] * len(values)

    alpha = 2.0 / (length + 1.0)
    prev = sum(defined[:length]) / length
    tail: list[float] = [prev]
    for value in defined[length:]:
        prev = alpha * value + (1.0 - alpha) * prev
        tail.append(prev)

    return [None] * (start + length - 1) + tail


def zone_of(px: float | None, fast_ma: float | None, slow_ma: float | None) -> str:
    """โซนของหนึ่งแท่งจาก (`xPrice`, FastMA, SlowMA) — ฟังก์ชันบริสุทธิ์

    แยกออกมาเป็นฟังก์ชันสาธารณะเพราะ golden test เทียบกับไฟล์ export ที่มีคอลัมน์
    `close` `Fast EMA` `Slow EMA` อยู่แล้ว (`barcolor` ของ Pine ไม่ออกมาใน CSV แต่
    `plot` ออก) จึงเทียบได้โดยไม่ต้องสร้าง `Bar` หรือคำนวณ EMA ซ้ำ

    เขียนเรียงทีละเงื่อนไขตามไฟล์ Pine โดยเจตนา (ไม่ยุบเป็น if ซ้อน) เพื่อให้
    ตรวจทานเทียบบรรทัดต่อบรรทัดกับต้นฉบับได้ · `BLACK` คือช่องว่างที่เหลือจากการที่
    เงื่อนไขทั้งหกใช้ `>` และ `<` ล้วน — `fast_ma == slow_ma` หรือ `px == fast_ma`
    พอดีจึงไม่เข้าโซนใดเลย ซึ่งเป็นสถานะจริง ไม่ใช่ค่าที่หายไป

    `None` (= `na`) ออก `BLACK` ด้วยเหตุผลเดียวกัน ไม่ใช่กรณีพิเศษ — ใน Pine การ
    เทียบใดๆ กับ `na` ให้ false ทั้งหกเงื่อนไขจึงเป็นเท็จพร้อมกัน · เขียนเป็น early
    return เพราะกระจาย `is not None` ลงหกบรรทัดจะทำให้เทียบกับต้นฉบับทีละบรรทัดไม่ออก
    """
    if px is None or fast_ma is None or slow_ma is None:
        return "BLACK"

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
        # `slow - 1` แท่งแรกเป็น `BLACK` เพราะ SlowMA ยัง seed ไม่ได้ (ดูหัวไฟล์)
        # `prev_zone is None` จึงเป็นจริงได้เฉพาะแท่งที่ 0 ซึ่งเป็น `BLACK` อยู่แล้ว
        # — เงื่อนไขนี้คือตาข่ายที่ตอนนี้ไม่มีอะไรตกลงมา ไม่ใช่ตรรกะที่ตายแล้ว มันคือ
        # `na` ของ `Green[1]` ตรงตัว และยังถูกอยู่ถ้ามีใครตั้ง `slow = 1`
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
