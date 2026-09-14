"""feature ที่ป้อน Confluence Judge — ตัวเลขล้วน ไม่มีคำตัดสิน (spec/04:29-40)

ADR [4](../../../docs/adr/0004-llm-judges-numbers-only.md) บอกว่า LLM เห็นเฉพาะตัวเลข
ไม่ใช่ภาพกราฟ ไฟล์นี้คือตัวเลขชุดนั้น · **หน้าที่ของมันคือคำนวณ ไม่ใช่ตัดสิน**

spec/04:5-11 ระบุไว้ตรงๆ ว่าคำว่า "สำเร็จ" "ขนาดใหญ่" "อย่างชัดเจน" ไม่มีเกณฑ์ตายตัว
และการเขียนเป็น threshold แข็งๆ คือการแอบตัดสินใจแทนคนออกแบบระบบ — ดังนั้นที่นี่
**ห้ามมีฟิลด์ bool แม้แต่ตัวเดียว** ไม่มี `is_higher_low` ไม่มี `broke_out` มีแต่จุด
เหวี่ยงสองจุดพร้อมราคา, เส้นที่ฟิตแล้วพร้อมระยะห่างที่มีเครื่องหมาย, และจำนวนแท่ง
ถ้าวันหนึ่งมี bool โผล่มาในไฟล์นี้ แปลว่าเกณฑ์ถูกย้ายจาก LLM มาอยู่ในโค้ดเงียบๆ แล้ว

**ไม่แตะ DB ไม่ต่อเน็ต ไม่ใช้ pandas** ธรรมเนียมเดียวกับ `action_zone.py` — `Bar` ถูก
นำเข้าใต้ `TYPE_CHECKING` เพราะ `cane.data` ลาก ccxt เข้ามาด้วย

## คืนค่าของ *แท่งสุดท้ายแท่งเดียว* ไม่ใช่ทั้งชุด

ต่างจาก `action_zones()` ที่คืนรายการยาวเท่า `bars` เพราะ `state` ของมันสะสมข้ามแท่ง
ที่นี่ทุกฟิลด์คำนวณจากหน้าต่างถอยหลังคงที่ ไม่มีอะไรสะสม การคืนทั้งชุดจึงเป็นงานเปล่า
· replay ของใบ 12 เดินด้วย `closed_as_of(as_of)` ทีละก้าวอยู่แล้ว แต่ละก้าวเรียกที่นี่
หนึ่งครั้ง — เส้นทางเดียวกับ live เป๊ะ

## ดัชนีแท่งคือตำแหน่งใน `bars` ที่ส่งเข้ามา

`evidence_bars` ของ `ConfluenceVerdict` (spec/04:44-56) เป็น `[int]` ที่ LLM ต้องอ้างถึงได้
เลขที่ออกจากไฟล์นี้จึงเป็น index ใน `bars` ตรงๆ **ผู้เรียกต้องส่ง `bars` ชุดเดียวกับที่
ใส่ลง prompt** ถ้าตัดหัวชุดทิ้งก่อนส่งเข้า prompt แต่ไม่ตัดก่อนเรียกที่นี่ ทุกเลขจะเลื่อน
และ `rationale` จะอ้างถึงแท่งผิดตัวโดยที่ไม่มีอะไรฟ้อง

## จุด pivot ยืนยันได้ช้ากว่าราคาเสมอ

จุดเหวี่ยงที่ index `i` จะรู้ว่าเป็นจุดเหวี่ยงก็ต่อเมื่อผ่านไปอีก `right` แท่งแล้ว —
**`right` แท่งท้ายสุดจึงไม่มีวันเป็น pivot** นี่ไม่ใช่ข้อจำกัดของโค้ด แต่เป็นผลโดยตรง
ของ ADR [10](../../../docs/adr/0010-closed-bars-only.md) ที่ห้ามมองอนาคต · ก้นที่เพิ่งเกิด
เมื่อวานยัง "ไม่มีอยู่" ในสายตาของระบบ ซึ่งถูกแล้ว เพราะมันยังกลับไปทำ low ใหม่ได้

## สองที่ที่สเปกอ่านได้สองทาง — เลือกทางไหนและทำไม

1. **"ขนาด body เทียบ ATR (z-score)"** (spec/04:38) อ่านได้ทั้ง "body/ATR" และ "z-score
   ของ body" · ที่นี่อ่านรวมทั้งประโยค: normalize ด้วย ATR ก่อน แล้วหา z-score ของ
   *อัตราส่วนนั้น* เทียบ `body_lookback` แท่งหลังสุด — ได้ตัวเลขไร้หน่วยที่เทียบข้าม
   คู่เหรียญและข้าม timeframe ได้ ซึ่งเป็นสิ่งที่ prompt ชุดเดียวต้องการ
2. **"linear fit"** (spec/04:36-37) ฟิตบนอะไร · ที่นี่ฟิตบน **จุด pivot** ไม่ใช่ high/low
   ของทุกแท่ง เพราะเส้นแนวโน้มที่เอกสารต้นทางพูดถึงคือเส้นที่ลากผ่านยอด/ก้น ไม่ใช่
   regression ของราคาทั้งเส้น — สองอย่างนี้ให้เส้นคนละเส้นเมื่อราคาแกว่งแรง
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cane.data.ohlcv import Bar

#: หน้าต่างยืนยัน pivot — `left` แท่งซ้าย `right` แท่งขวาต้องสูง/ต่ำกว่าทั้งหมด
#: ค่า 2/2 คือค่าที่เล็กที่สุดที่ยังกรอง noise ของแท่งเดี่ยวได้ · ใหญ่กว่านี้จะเห็น
#: เฉพาะยอดใหญ่แต่ยืนยันช้าลงอีก (ดู "pivot ยืนยันได้ช้ากว่าราคาเสมอ" ในหัวไฟล์)
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

#: จำนวนจุดเหวี่ยงล่าสุดที่ส่งให้ LLM ต่อฝั่ง · `HIGHER_LOW` ต้องการอย่างน้อยสองจุด
#: (จุดใหม่เทียบจุดเดิม) ให้สามเพื่อให้เห็นว่าเป็นแนวโน้มหรือเป็นจังหวะเดียว
MAX_SWINGS = 3

#: คาบ ATR แบบ Wilder · **ไม่ใช่ 5/10 ของ CDC ATR Trailing Stop** (spec/03:140-141)
#: คนละงานกัน — ที่นั่น ATR เป็นระยะตั้ง stop จริง ที่นี่เป็นแค่หน่วยวัดที่ทำให้ตัวเลข
#: เทียบข้ามคู่เหรียญได้ 14 คือค่ามาตรฐานของ Wilder ไม่มีอะไรในเอกสารต้นทางบังคับ
ATR_PERIOD = 14

#: หน้าต่างที่ใช้หา mean/std ของ body — ประมาณหนึ่งเดือนเทรดบน timeframe รายวัน
BODY_LOOKBACK = 20


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """จุดเหวี่ยงหนึ่งจุด — ดัชนีแท่งกับราคา ไม่มีคำว่า "สำคัญ" หรือ "ชัดเจน"

    `price` คือ `low` ของแท่งสำหรับ swing low และ `high` สำหรับ swing high ไม่ใช่
    ราคาปิด — จุดเหวี่ยงคือปลายไส้เทียน การใช้ราคาปิดจะทำให้ `HIGHER_LOW` ตัดสิน
    จากตัวเลขที่ไม่ใช่ก้นจริงของแท่ง
    """

    index: int
    price: float


@dataclass(frozen=True, slots=True)
class TrendLine:
    """เส้นตรงที่ฟิตด้วย least squares ผ่านจุด pivot — `price = slope * index + intercept`

    `slope` มีหน่วยเป็น *ราคาต่อแท่ง* ไม่ใช่เปอร์เซ็นต์ ตีความข้ามคู่เหรียญไม่ได้
    ตัวที่ตีความข้ามคู่ได้คือ `distance_atr` ซึ่งหารด้วย ATR แล้ว

    `distance_atr` = (ราคาปิดแท่งสุดท้าย − ค่าของเส้นที่แท่งสุดท้าย) ÷ ATR **มีเครื่องหมาย**
    บวกคือราคาอยู่เหนือเส้น ลบคืออยู่ใต้เส้น · สำหรับเส้นกรอบกด (ฟิตบน swing high)
    ค่าบวกคือสิ่งที่ `CHANNEL_BREAKOUT` มองหา สำหรับเส้นกรอบรับ (ฟิตบน swing low)
    ค่าลบคือสิ่งที่ `CHANNEL_BREAKDOWN` มองหา — **แต่เท่าไหร่ถึงเรียกว่าเบรคสำเร็จ
    เป็นคำตัดสินของ LLM ไม่ใช่ของที่นี่**

    `points` คือดัชนีแท่งที่ถูกใช้ฟิต ส่งไปด้วยเพื่อให้ `evidence_bars` อ้างถึงได้จริง
    """

    slope: float
    intercept: float
    distance_atr: float
    points: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Features:
    """feature ของแท่งสุดท้ายใน `bars` — หนึ่งฟิลด์ต่อหนึ่งบรรทัดในตารางของ spec/04:31-40

    ฟิลด์ที่เป็น `None` ได้คือ **สภาวะตลาดจริง ไม่ใช่ข้อผิดพลาด**:
    `resistance` / `support` เป็น `None` เมื่อมีจุด pivot ไม่ถึงสองจุดในชุดข้อมูล
    (ตลาดที่เพิ่งเปิดหรือวิ่งทางเดียวไม่มีจุดให้ลากเส้น) · `body_atr_z` เป็น `None`
    เมื่อ body ทุกแท่งในหน้าต่างเท่ากันเป๊ะจน std เป็นศูนย์ — z-score ไม่นิยามตรงนั้น
    และการคืน 0.0 จะโกหกว่า "ปกติ" ทั้งที่ความจริงคือ "เทียบไม่ได้"
    """

    bar_index: int
    bar_close_ts: int
    close_px: float
    atr: float

    swing_lows: tuple[SwingPoint, ...]
    swing_highs: tuple[SwingPoint, ...]

    resistance: TrendLine | None
    support: TrendLine | None

    red_run: int
    green_run: int
    body_atr_z: float | None
    gap_atr: float


def true_range(bar: Bar, prev_close: float | None) -> float:
    """TR ของหนึ่งแท่ง · แท่งแรกไม่มีราคาปิดก่อนหน้า → ใช้ high−low ตรงๆ

    `prev_close is None` คือ "ไม่มีแท่งก่อนหน้า" ไม่ใช่ "ราคาปิดเป็นศูนย์" — แยก
    ให้ชัดเพราะการแทนด้วย 0.0 จะทำให้ TR ของแท่งแรกเท่ากับราคาทั้งก้อน
    """
    if prev_close is None:
        return bar.high - bar.low
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


def wilder_atr(bars: Sequence[Bar], length: int = ATR_PERIOD) -> float:
    """ATR ของแท่งสุดท้าย ตามสูตร `ta.atr` ของ Pine = RMA ของ TR

    RMA คือ EMA ที่ `alpha = 1/length` (ไม่ใช่ `2/(length+1)` ของ `pine_ema`) seed ด้วย
    SMA ของ `length` ตัวแรก — นี่คือสูตรที่ Wilder เขียนไว้และที่ `reference/
    cdc_trailing_stop.pine` เรียกใช้ · ใบ 09 ที่ทำ trailing stop **ต้องเรียกตัวนี้
    ไม่ใช่เขียนใหม่** ไม่งั้น stop ที่ตั้งจริงกับตัวเลขที่ LLM เห็นจะมาจากคนละสูตร

    คืน `float` ตัวเดียวเพราะไม่มีใครต้องการเส้น ATR ทั้งเส้น — ถ้าใบ 09 ต้องการ
    ค่อยแยกตัวที่คืนทั้งเส้นออกมาตอนนั้น ไม่ใช่เดาไว้ก่อน
    """
    if length < 1:
        raise ValueError(f"คาบของ ATR ต้อง >= 1 ไม่ใช่ {length}")
    if len(bars) < length + 1:
        raise ValueError(
            f"ATR คาบ {length} ต้องมีอย่างน้อย {length + 1} แท่ง มีมา {len(bars)}"
        )

    trs = [
        true_range(bar, bars[i - 1].close if i else None)
        for i, bar in enumerate(bars)
    ]
    # ข้าม TR ตัวแรกทิ้งเสมอ — มันเกิดจากแท่งที่ไม่มีราคาปิดก่อนหน้าจึงเป็น TR
    # คนละนิยามกับตัวอื่น · Pine ก็คืน `na` ตรงนั้น การเอามาเฉลี่ยด้วยคือการปน
    trs = trs[1:]
    atr = sum(trs[:length]) / length
    for tr in trs[length:]:
        atr = (atr * (length - 1) + tr) / length
    return atr


def pivot_lows(
    bars: Sequence[Bar], *, left: int = PIVOT_LEFT, right: int = PIVOT_RIGHT
) -> list[SwingPoint]:
    """ก้นที่ยืนยันแล้ว เรียงเก่าไปใหม่ · `right` แท่งท้ายสุดไม่มีวันติดรายการ

    เกณฑ์เป็น **น้อยกว่าอย่างเข้ม** (`<`) ทุกแท่งในหน้าต่าง ไม่ใช่ `<=` — ที่ราบ
    (low เท่ากันสองแท่ง) จึงไม่นับเป็นจุดเหวี่ยงเลยแทนที่จะนับเป็นสองจุดที่ราคาเท่ากัน
    ซึ่งจะทำให้ `HIGHER_LOW` เห็น "ก้นใหม่สูงกว่าก้นเดิม 0.0" เป็นข้อมูลเข้า
    """
    return _pivots(bars, left=left, right=right, low_side=True)


def pivot_highs(
    bars: Sequence[Bar], *, left: int = PIVOT_LEFT, right: int = PIVOT_RIGHT
) -> list[SwingPoint]:
    """ยอดที่ยืนยันแล้ว — ภาพสะท้อนของ `pivot_lows()` ทีละข้อ (spec/04:24)"""
    return _pivots(bars, left=left, right=right, low_side=False)


def _pivots(
    bars: Sequence[Bar], *, left: int, right: int, low_side: bool
) -> list[SwingPoint]:
    if left < 1 or right < 1:
        raise ValueError(f"หน้าต่าง pivot ต้อง >= 1 ทั้งสองข้าง ไม่ใช่ {left}/{right}")

    prices = [bar.low if low_side else bar.high for bar in bars]
    out: list[SwingPoint] = []
    for i in range(left, len(prices) - right):
        px = prices[i]
        window = prices[i - left : i + right + 1]
        extreme = min(window) if low_side else max(window)
        # `window.count(px) == 1` คือข้อ "อย่างเข้ม" — px เป็นค่าสุดขั้วของหน้าต่าง
        # และไม่มีตัวไหนเสมอ · เขียนแยกจากการเทียบ min/max เพื่อให้อ่านออกว่า
        # ที่ราบถูกตัดทิ้งโดยเจตนา ไม่ใช่ผลข้างเคียงของ `<` ที่บังเอิญได้มา
        if px == extreme and window.count(px) == 1:
            out.append(SwingPoint(index=i, price=px))
    return out


def fit_line(points: Sequence[SwingPoint]) -> tuple[float, float] | None:
    """least squares ผ่านจุด pivot → `(slope, intercept)` · น้อยกว่าสองจุดคืน `None`

    จุดสองจุดให้เส้นที่ลากผ่านทั้งคู่พอดี (residual เป็นศูนย์) สามจุดขึ้นไปเป็นการฟิตจริง
    · `None` แปลว่า **ลากเส้นไม่ได้** ไม่ใช่ "เส้นแบน" — ตลาดที่ยังไม่เคยเหวี่ยงไม่มี
    กรอบแนวโน้มให้เบรค ซึ่งเป็นคำตอบที่ถูกต้อง ไม่ใช่ค่าที่หายไป
    """
    if len(points) < 2:
        return None

    n = float(len(points))
    mean_x = sum(p.index for p in points) / n
    mean_y = sum(p.price for p in points) / n
    sxx = sum((p.index - mean_x) ** 2 for p in points)
    if sxx == 0.0:
        # ดัชนีซ้ำกันทุกจุด — เกิดไม่ได้จาก `_pivots()` ที่เดินทีละ i
        return None
    sxy = sum((p.index - mean_x) * (p.price - mean_y) for p in points)
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def min_bars(
    *,
    atr_period: int = ATR_PERIOD,
    body_lookback: int = BODY_LOOKBACK,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
) -> int:
    """จำนวนแท่งน้อยที่สุดที่ `features()` รับได้

    **ไม่เกี่ยวกับ `data.MIN_CLOSED_BARS` (85)** ซึ่งเป็นเกณฑ์ว่าคู่เหรียญพร้อมให้
    ตัดสินใจหรือยัง — คนละชั้นกัน ที่นี่ตอบแค่ว่า "สูตรพวกนี้มีข้อมูลพอจะคำนวณไหม"
    """
    return max(atr_period + 1, body_lookback, left + right + 1, 2)


def features(
    bars: Sequence[Bar],
    *,
    atr_period: int = ATR_PERIOD,
    body_lookback: int = BODY_LOOKBACK,
    left: int = PIVOT_LEFT,
    right: int = PIVOT_RIGHT,
    max_swings: int = MAX_SWINGS,
) -> Features:
    """คำนวณ feature ทั้งชุดของแท่งสุดท้าย · `bars` เรียงเก่าไปใหม่ ไม่เรียงให้

    ธรรมเนียมเดียวกับ `action_zones()` — ไม่เรียงให้และไม่ตรวจ เพราะการเรียงเงียบๆ
    จะกลบบั๊กของผู้เรียกที่ส่งย้อนลำดับมา
    """
    need = min_bars(
        atr_period=atr_period, body_lookback=body_lookback, left=left, right=right
    )
    if len(bars) < need:
        raise ValueError(f"ต้องมีอย่างน้อย {need} แท่ง มีมา {len(bars)}")

    atr = wilder_atr(bars, atr_period)
    if atr == 0.0:
        # ทุกแท่งในชุดมีช่วงเป็นศูนย์ · ทุก feature ของไฟล์นี้หารด้วย ATR ดังนั้น
        # ทางเลือกมีแค่ดังตรงนี้ หรือส่ง inf/nan ให้ LLM ตีความ — เลือกดัง
        raise ValueError("ATR เป็นศูนย์ — ชุดข้อมูลนี้ราคาไม่ขยับเลย คำนวณ feature ไม่ได้")

    last = len(bars) - 1
    lows = pivot_lows(bars, left=left, right=right)[-max_swings:]
    highs = pivot_highs(bars, left=left, right=right)[-max_swings:]

    return Features(
        bar_index=last,
        bar_close_ts=bars[last].close_ts,
        close_px=bars[last].close,
        atr=atr,
        swing_lows=tuple(lows),
        swing_highs=tuple(highs),
        resistance=_trend_line(highs, close=bars[last].close, at=last, atr=atr),
        support=_trend_line(lows, close=bars[last].close, at=last, atr=atr),
        red_run=_run(bars, up=False),
        green_run=_run(bars, up=True),
        body_atr_z=_body_atr_z(bars, atr=atr, lookback=body_lookback),
        gap_atr=(bars[last].open - bars[last - 1].close) / atr,
    )


def _trend_line(
    points: Sequence[SwingPoint], *, close: float, at: int, atr: float
) -> TrendLine | None:
    fit = fit_line(points)
    if fit is None:
        return None
    slope, intercept = fit
    return TrendLine(
        slope=slope,
        intercept=intercept,
        distance_atr=(close - (slope * at + intercept)) / atr,
        points=tuple(p.index for p in points),
    )


def _run(bars: Sequence[Bar], *, up: bool) -> int:
    """จำนวนแท่งสีเดียวกันติดกันที่ **จบลงที่แท่งสุดท้าย**

    แท่งที่ `close == open` (doji) ไม่ใช่ทั้งแดงและเขียว มันจึงตัดทั้งสองชุด — ไม่ใช่
    ตัดชุดเดียวแล้วปล่อยอีกชุดวิ่งต่อ · ถ้าแท่งสุดท้ายเป็น doji ทั้งสองค่าเป็น 0
    """
    n = 0
    for bar in reversed(bars):
        same = bar.close > bar.open if up else bar.close < bar.open
        if not same:
            break
        n += 1
    return n


def _body_atr_z(bars: Sequence[Bar], *, atr: float, lookback: int) -> float | None:
    """z-score ของ (|body| ÷ ATR) ของแท่งสุดท้าย เทียบ `lookback` แท่งหลังสุด

    หน้าต่าง **รวมแท่งสุดท้ายเอง** ตามนิยามมาตรฐานของ z-score — แท่งที่ใหญ่ผิดปกติ
    จึงดันค่าเฉลี่ยขึ้นเองส่วนหนึ่ง ทำให้ค่าที่ได้อนุรักษ์นิยมกว่าการกันตัวเองออก
    ซึ่งเป็นทิศที่ถูกสำหรับฟีเจอร์ที่ใช้ตัดสินว่า "ใหญ่พอจะเป็น capitulation ไหม"

    std เป็น population (หาร n) ไม่ใช่ sample (หาร n−1) — หน้าต่างนี้คือประชากร
    ทั้งหมดที่คำถามสนใจ ไม่ได้สุ่มมาจากอะไร
    """
    window = bars[-lookback:]
    sizes = [abs(bar.close - bar.open) / atr for bar in window]
    n = float(len(sizes))
    mean = sum(sizes) / n
    var = sum((s - mean) ** 2 for s in sizes) / n
    if var == 0.0:
        return None
    return (sizes[-1] - mean) / var**0.5
