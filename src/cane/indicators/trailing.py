"""CDC ATR Trailing Stop V2.1 — พอร์ตจาก `reference/cdc_trailing_stop.pine` (spec/03)

เส้น **Slow Trail** (`Trail2`) คือที่ที่ stop ของ cold start ทางที่ 2 ถูกวางไว้จริงที่
exchange (ADR 17) และเลื่อนตามทุกแท่ง · ทั้งไฟล์มีไว้เพื่อบรรทัดนั้นบรรทัดเดียว

**ไม่ใช้ pandas และไม่แตะ ccxt** ธรรมเนียมเดียวกับ `action_zone.py` — `Bar` นำเข้าใต้
`TYPE_CHECKING` เพราะ `cane.data` ลาก ccxt เข้ามาด้วย

## สูตรเป็น recursive — ต้องเดินทีละแท่ง ไม่ใช่ vectorized

`Trail2` อ้าง `Trail2[1]` ของตัวเอง ไม่มีรูปปิดที่คำนวณพร้อมกันทั้งเส้นได้ · การ
เขียนเป็น vectorized ใดๆ คือการเปลี่ยนสูตร ไม่ใช่การเร่งความเร็ว

## `nz(Trail[1], 0)` คือศูนย์จริง ไม่ใช่ "ไม่มีค่า"

แท่งแรกที่ ATR นิยามได้จะเห็น `Trail[1]` เป็น `na` แล้ว `nz()` แปลงเป็น **0** ·
ราคาคริปโตเป็นบวกเสมอ เงื่อนไขแรกจึงอ่านว่า `SC > 0` ซึ่งจริง แล้วตกลงมาที่สาขา
`iff(SC > Trail[1], SC - SL, ...)` ได้ `SC - SL` — นั่นคือ seed ของเส้นนี้ **ห้าม
แทนด้วย `None` แล้วข้ามแท่งนั้นไป** เพราะจะได้เส้นที่เริ่มช้ากว่าต้นฉบับหนึ่งแท่ง

## `SC[1]` กับ `Trail[1]` เป็นคนละอย่าง และนี่คือจุดที่พอร์ตผิดง่ายที่สุด

สองเงื่อนไขแรกของ `iff` ซ้อนต้องการ **ทั้ง** ราคาปิดแท่งนี้และแท่งก่อนหน้าอยู่ข้าง
เดียวกันของเส้น · `SC[1]` คือราคาปิดของแท่งก่อน (มีเสมอถ้าไม่ใช่แท่งแรก) ส่วน
`Trail[1]` คือค่าของเส้นเอง (เป็น `na` ได้ยาว) — การเผลอใช้ตัวเดียวแทนอีกตัวทำให้
เส้นล็อกไม่ตรงจุดโดยที่รูปกราฟยังดูสมเหตุสมผล

## ยังไม่ได้เทียบกับไฟล์จริงจาก TradingView

สถานะเดียวกับการ seed EMA ใน `action_zone.py` และ ATR ใน `features.py` — `Trail1`
กับ `Trail2` ถูก `plot()` ในสคริปต์ต้นทางจึงออกมาใน CSV และ **golden test ได้** ส่วน
`Hst`/`Sig` ถูกคอมเมนต์ไว้ ไม่ถูก plot จึงเทียบไม่ได้ตลอดไป

`tests/fixtures/` ยังไม่มีไฟล์ export ของอินดิเคเตอร์ตัวไหนเลย (ของใบ 04 ก็ยังไม่มี)
ถ้าเส้นไม่ตรงหลังได้ไฟล์มา จุดที่ต้องสงสัยเรียงตามความน่าจะเป็น: การ seed ATR
(`wilder_atr_series` — ดูหัวข้อในไฟล์นั้น), แล้วจึงเงื่อนไข `SC[1]` ข้างบน
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cane.indicators.action_zone import pine_ema
from cane.indicators.features import wilder_atr_series

if TYPE_CHECKING:
    from cane.data.ohlcv import Bar

#: ค่าตั้งต้นของสคริปต์ต้นทาง (`reference/cdc_trailing_stop.pine:15-23`)
FAST_ATR_PERIOD = 5
FAST_ATR_FACTOR = 0.5
SLOW_ATR_PERIOD = 10
SLOW_ATR_FACTOR = 2.0

#: คาบของ `Sig = ema(Hst, 9)` — ไม่ใช่ค่าที่ผู้ใช้ตั้งได้ในสคริปต์ต้นทาง
SIGNAL_PERIOD = 9


@dataclass(frozen=True, slots=True)
class TrailPoint:
    """ค่าของเส้นทั้งชุดที่แท่งหนึ่ง · `None` คือ `na` ของ Pine ไม่ใช่ศูนย์

    `slow` คือเส้นที่ stop ของ cold start ทางที่ 2 ถูกวางไว้ (ADR 17) — ฟิลด์อื่นมี
    ไว้ให้ golden test และให้คนอ่านย้อนหลัง **ไม่มีอะไรในเส้นทางตัดสินใจใช้มันเลย**

    `hst` กับ `sig` ไม่ถูก `plot()` ในสคริปต์ต้นทาง (ถูกคอมเมนต์ไว้) จึงไม่มีวัน
    เทียบกับ TradingView ได้ — ถือเป็นของที่พอร์ตมาตามใบสั่ง ไม่ใช่ของที่พิสูจน์แล้ว
    """

    bar_close_ts: int
    close_px: float
    fast: float | None
    slow: float | None
    hst: float | None
    sig: float | None


def cdc_trail(
    bars: Sequence[Bar], *, period: int, factor: float
) -> list[float | None]:
    """`Trail1` หรือ `Trail2` แล้วแต่ `(period, factor)` ที่ให้มา — ทั้งเส้น

    ฟังก์ชันเดียวสำหรับสองเส้นเพราะสูตรในไฟล์ต้นทางเหมือนกันเป๊ะทุกตัวอักษร ต่างแค่
    คาบกับตัวคูณ · การเขียนสองฟังก์ชันคือการเปิดช่องให้แก้ตัวหนึ่งแล้วลืมอีกตัว

    ไล่ `iff` ซ้อนของต้นฉบับตามลำดับเดิมทีละสาขา ไม่ยุบเป็นนิพจน์เดียว เพื่อให้
    ตรวจทานเทียบบรรทัดต่อบรรทัดกับ Pine ได้
    """
    atr = wilder_atr_series(bars, period)
    out: list[float | None] = []
    prev_trail: float | None = None

    for i, bar in enumerate(bars):
        if atr[i] is None:
            # `SL = factor * na` เป็น `na` → `Trail` ของแท่งนี้เป็น `na` ด้วย
            out.append(None)
            continue

        stop_distance = factor * atr[i]
        px = bar.close
        # `nz(Trail[1], 0)` — ศูนย์จริง ดูหัวไฟล์ว่าทำไมห้ามข้ามแท่งนี้แทน
        prev_line = 0.0 if prev_trail is None else prev_trail
        prev_px = bars[i - 1].close if i else None

        if px > prev_line and prev_px is not None and prev_px > prev_line:
            trail = max(prev_line, px - stop_distance)
        elif px < prev_line and prev_px is not None and prev_px < prev_line:
            trail = min(prev_line, px + stop_distance)
        elif px > prev_line:
            trail = px - stop_distance
        else:
            trail = px + stop_distance

        out.append(trail)
        prev_trail = trail

    return out


def cdc_trailing_stop(
    bars: Sequence[Bar],
    *,
    fast_period: int = FAST_ATR_PERIOD,
    fast_factor: float = FAST_ATR_FACTOR,
    slow_period: int = SLOW_ATR_PERIOD,
    slow_factor: float = SLOW_ATR_FACTOR,
    signal_period: int = SIGNAL_PERIOD,
) -> list[TrailPoint]:
    """ทั้งอินดิเคเตอร์ ทุกแท่ง เรียงตามเวลา — คืนรายการยาวเท่า `bars`

    `bars` ต้องเรียงจากเก่าไปใหม่ · ไม่เรียงให้และไม่ตรวจ ธรรมเนียมเดียวกับ
    `action_zones()` — การเรียงเงียบๆ จะกลบบั๊กของผู้เรียกที่ส่งย้อนลำดับมา

    `Sig` คือ EMA ของ `Hst` ซึ่งมีหัวเป็น `None` · `pine_ema()` รับหัวที่เป็น `None`
    และคืนหัวที่เป็น `None` ให้เอง (`na` ของ Pine) จึงส่งเข้าไปตรงๆ ได้ — เคยมี
    `_ema_over_defined()` ตัดหัว/ต่อหัวให้ที่นี่ ตอนที่ `pine_ema()` ยังไม่รับ `None`
    """
    fast = cdc_trail(bars, period=fast_period, factor=fast_factor)
    slow = cdc_trail(bars, period=slow_period, factor=slow_factor)

    hst: list[float | None] = [
        None if f is None or s is None else f - s
        for f, s in zip(fast, slow, strict=True)
    ]
    sig = pine_ema(hst, signal_period)

    return [
        TrailPoint(
            bar_close_ts=bar.close_ts,
            close_px=bar.close,
            fast=fast[i],
            slow=slow[i],
            hst=hst[i],
            sig=sig[i],
        )
        for i, bar in enumerate(bars)
    ]
