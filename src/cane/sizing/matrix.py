"""ขนาดหน้าตัก — สูตรเดียวของ spec/05 ที่เดินเหมือนกันทั้ง perp และ spot

**เลขคณิตล้วน ไม่แตะ DB ไม่ต่อเน็ต ไม่รู้จัก LLM** · มันรับ "จำนวนปัจจัยที่ present"
เป็น `int` ตัวเดียว ไม่ใช่รายการ verdict — `confidence` ของ LLM ไม่มีทางเข้ามาถึงที่นี่
ได้เลยแม้จะอยากให้เข้า ซึ่งคือวิธีบังคับ ADR 12 ("ห้ามผูกขนาดไม้กับ confidence")
ด้วยรูปของ interface แทนที่จะด้วยวินัยของคนเขียน

## ชื่อฟิลด์ตรงกับคอลัมน์ของ `DecisionRecord` โดยเจตนา

`size_pct_formula` / `size_pct_final` / `capped` / `margin` / `notional` / `qty` /
`ref_px` มีอยู่แล้วใน `db/repo/decisions.py` (ใบ 03) · ถ้าตั้งชื่อใหม่ ชั้นที่ประกอบ
บันทึกจะต้องมีตารางแปลงชื่อ ซึ่งเป็นที่ที่การจับคู่ผิดจะเกิดโดยไม่มีอะไรฟ้อง

**`size_pct_formula` กับ `size_pct_final` ต้องเก็บทั้งคู่** ไม่ใช่เก็บตัวสุดท้ายอย่างเดียว
— spec/05:72 บอกว่าต้องแยกออกว่าไม้เล็กเพราะปัจจัยน้อยหรือเพราะชนเพดาน และการเก็บแค่
ค่าสุดท้ายทำให้สองเหตุผลนั้นหน้าตาเหมือนกันเป๊ะตอนอ่านย้อนหลัง

## leverage คูณที่ notional ไม่ใช่ที่ size_pct

ถ้าคูณที่ `size_pct` เพดาน `max_position_pct_<side>` จะไม่มีความหมายอีกต่อไป
(spec/05:60) · ผลคือ `leverage = 1` ของ spot ทำให้ `notional == margin` เองโดยไม่ต้อง
มี `if` ที่ไหนเลย — ไฟล์นี้จึงไม่รู้จักคำว่า spot และไม่ควรรู้จัก

## ปัดลงด้วย Decimal ไม่ใช่ float

`floor(qty / step) * step` บน float ให้คำตอบผิดในเคสธรรมดามาก: `0.3 / 0.1` เป็น
`2.9999999999999996` แล้ว `floor` ได้ 2 → qty กลายเป็น 0.2 แทนที่จะเป็น 0.3 ·
เงินหายไปหนึ่งในสามของไม้เพราะการแทนเลขฐานสอง ซึ่งเป็นความล้มเหลวที่ไม่มีเทสต์
ระดับบนจับได้เลย · คืน `float` ที่ขอบเพราะ `Order.qty` กับ config เป็น `float` ทั้งชุด
แต่**การปัดเกิดใน `Decimal` เสมอ**

## ตัวกรองของ venue ไม่ได้มีแค่ lot size

`step` อย่างเดียวไม่พอ — `min_qty` และ `min_notional` ปฏิเสธออเดอร์ได้ทั้งที่ปัดตาม
step มาถูกต้องแล้ว · ไม้ที่เล็กเกินจนส่งไม่ได้ต้องเป็น **การปฏิเสธที่อธิบายตัวเองได้**
ไม่ใช่ `qty = 0` ที่ถูกส่งต่อไปให้ `Order` ยก `ValueError` ในอีกสามชั้นถัดไป
โดยที่ข้อความไม่ได้บอกว่าเพราะอะไร

ค่าพวกนี้มาจาก venue (ใบ 13) **ไม่มีค่าตั้งต้นในไฟล์นี้** — ตัวเลขที่เดาเอาเองเรื่อง
ขนาดขั้นต่ำของ venue คือตัวเลขที่จะผิดเงียบๆ จนกว่าจะมีออเดอร์จริงถูกปฏิเสธ
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

#: ปัจจัยละกี่เปอร์เซ็นต์ (spec/05:30-36) · เอกสารต้นทางเขียน "+20" ต่อปัจจัย
FACTOR_STEP_PCT = 20.0

#: มีสามปัจจัยต่อฝั่ง (spec/04) · ตรงกับ `ck_decisions_factors_present` ที่บังคับ
#: `BETWEEN 0 AND 3` ไว้ที่ฐานแล้ว — ที่นี่ปฏิเสธ ไม่ใช่หนีบให้เข้าช่วง เพราะ `n = 4`
#: แปลว่าผู้เรียกนับผิด และการหนีบจะซ่อนการนับผิดนั้นไว้ใต้ตัวเลขที่ดูสมเหตุสมผล
MAX_FACTORS = 3

#: เพดานของสูตรก่อนเพดานของ risk (spec/05:22) · ที่ `base_pct` สูงสุด 20 สูตรให้ 80
#: จึงไม่มีทางแตะ 100 ในรูปปัจจุบัน — คงไว้ตามสเปกเพราะมันคือเพดานเชิงความหมาย
#: ("ห้ามเกินทั้ง bucket") ไม่ใช่ค่าที่คำนวณมาให้พอดี
FORMULA_CEILING_PCT = 100.0


@dataclass(frozen=True, slots=True)
class LotFilter:
    """ตัวกรองของ venue สามตัว — มาจากปลายทาง ไม่มีค่าตั้งต้น (ดูหัวไฟล์)

    `min_notional` เป็น `None` ได้เพราะ venue บางแห่งไม่มีเกณฑ์นี้จริงๆ · `None`
    แปลว่า **ไม่มีเกณฑ์** ไม่ใช่ "ศูนย์" — สองอย่างนี้ให้ผลเดียวกันโดยบังเอิญ แต่
    ตัวหลังคือการอ้างว่ารู้ค่าที่จริงๆ แล้วไม่รู้
    """

    step: float
    min_qty: float
    min_notional: float | None = None

    def __post_init__(self) -> None:
        if self.step <= 0:
            raise ValueError(f"step ต้องมากกว่าศูนย์ ไม่ใช่ {self.step}")
        if self.min_qty < 0:
            raise ValueError(f"min_qty ติดลบไม่ได้: {self.min_qty}")
        if self.min_notional is not None and self.min_notional < 0:
            raise ValueError(f"min_notional ติดลบไม่ได้: {self.min_notional}")


@dataclass(frozen=True, slots=True)
class SizeDecision:
    """ผลของสูตร · ชื่อฟิลด์ตรงกับคอลัมน์ของ `DecisionRecord` (ดูหัวไฟล์)

    `qty = 0.0` คู่กับ `refused_reason` ที่ไม่ใช่ `None` คือ **ไม้ที่คำนวณได้แต่ส่งไม่ได้**
    ต่างจากไม้ที่ไม่มีสัญญาณ — ตัวเลข `size_pct` กับ `margin` ยังเป็นของจริงและยัง
    ควรถูกบันทึก เพราะมันตอบว่า "ระบบตั้งใจจะลงเท่าไหร่" ซึ่งเป็นคำถามที่ต่างจาก
    "ลงไปเท่าไหร่"
    """

    factors_present: int
    size_pct_formula: float
    size_pct_final: float
    capped: bool
    margin: float
    notional: float
    qty: float
    ref_px: float
    leverage: float
    refused_reason: str | None = None

    @property
    def sendable(self) -> bool:
        return self.refused_reason is None


def size_pct(
    *, base_pct: float, factors_present: int, max_position_pct: float
) -> tuple[float, float, bool]:
    """`(ก่อนเพดาน, หลังเพดาน, ชนเพดานไหม)` — ส่วนที่เป็นเปอร์เซ็นต์ล้วนของสูตร

    แยกออกมาเป็นฟังก์ชันสาธารณะเพราะเส้นทาง cold start ใช้ `base_pct` ตรงๆ โดยไม่
    เรียก Judge (spec/05:76) — มันจึงเรียกที่นี่ด้วย `factors_present = 0` ได้ตรงๆ
    แทนที่จะมีสูตรที่สองที่ต้องดูแลให้ตรงกัน

    `capped` เป็นจริงเมื่อเพดานของ risk **กด**ค่าลงจริง ไม่ใช่เมื่อเพดานบังเอิญเท่ากับ
    ค่าที่สูตรให้ — ถ้าตั้งเพดานไว้ 65 แล้วสูตรให้ 65 พอดี ไม้นั้นไม่ได้ถูกกด และการ
    บันทึกว่า `capped` จะทำให้รายงาน "กี่ไม้ที่ชนเพดาน" นับเกินทุกครั้ง
    """
    if not 0 <= factors_present <= MAX_FACTORS:
        raise ValueError(
            f"factors_present ต้องอยู่ใน 0..{MAX_FACTORS} ไม่ใช่ {factors_present} "
            "(ฐานบังคับช่วงนี้ไว้ที่ ck_decisions_factors_present แล้ว)"
        )
    if max_position_pct <= 0:
        raise ValueError(f"max_position_pct ต้องมากกว่าศูนย์ ไม่ใช่ {max_position_pct}")

    formula = min(base_pct + FACTOR_STEP_PCT * factors_present, FORMULA_CEILING_PCT)
    final = min(formula, max_position_pct)
    return formula, final, final < formula


def floor_to_step(value: float, step: float) -> float:
    """ปัดลงให้เป็นจำนวนเท่าของ `step` · **ปัดลงเสมอ ไม่มีกรณีปัดขึ้น** (spec/05:26)

    ทำใน `Decimal` เพราะบน float `0.3 / 0.1` เป็น `2.9999999999999996` แล้วผลลัพธ์
    จะเป็น `0.2` ซึ่งผิดไปหนึ่งขั้นเต็มๆ (ดูหัวไฟล์)

    `Decimal(str(x))` ไม่ใช่ `Decimal(x)` — ตัวหลังรับค่าฐานสองที่แท้จริงของ float
    เข้ามาทั้งดุ้น (`Decimal(0.1)` คือ `0.1000000000000000055511151231257827...`)
    แล้วการหารจะได้เศษที่ทำให้ปัดลงเกินไปหนึ่งขั้นในบางเคส
    """
    if step <= 0:
        raise ValueError(f"step ต้องมากกว่าศูนย์ ไม่ใช่ {step}")
    if value < 0:
        raise ValueError(f"ปัดจำนวนติดลบไม่ได้: {value}")
    quantum = Decimal(str(step))
    steps = (Decimal(str(value)) / quantum).to_integral_value(rounding=ROUND_DOWN)
    return float(steps * quantum)


def plan_size(
    *,
    base_pct: float,
    factors_present: int,
    bucket_quote: float,
    max_position_pct: float,
    leverage: float,
    ref_px: float,
    lot: LotFilter,
) -> SizeDecision:
    """สูตรเต็มของ spec/05:18-26 · ทุกค่าต้องมาจาก**ฝั่งเดียวกัน**หมด

    ไฟล์นี้ไม่รู้ว่ากำลังคิดฝั่งไหนอยู่ และไม่ควรรู้ — ผู้เรียกเลือก `bucket_quote`
    กับ `max_position_pct` ของฝั่งนั้นมาให้ · การส่ง `side` เข้ามาแล้วให้ที่นี่เลือก
    เองจะย้ายโอกาสผสมฝั่งมาไว้ในไฟล์นี้แทนที่จะกำจัดมัน และจะบังคับให้ทุกเทสต์ต้อง
    ประกอบ config ทั้งก้อนเพื่อทดสอบเลขคณิตสี่บรรทัด

    `ref_px` คือราคาที่ชั้นตัดสินใจเห็นตอนสั่ง — เก็บลงบันทึกด้วยเพราะ slippage คือ
    ราคาที่ได้จริงเทียบกับค่านี้ (`fills.ref_px`, ใบ 11)
    """
    if bucket_quote <= 0:
        raise ValueError(f"bucket_quote ต้องมากกว่าศูนย์ ไม่ใช่ {bucket_quote}")
    if leverage <= 0:
        raise ValueError(f"leverage ต้องมากกว่าศูนย์ ไม่ใช่ {leverage}")
    if ref_px <= 0:
        raise ValueError(f"ref_px ต้องมากกว่าศูนย์ ไม่ใช่ {ref_px}")

    formula, final, capped = size_pct(
        base_pct=base_pct,
        factors_present=factors_present,
        max_position_pct=max_position_pct,
    )
    margin = bucket_quote * final / 100.0
    notional = margin * leverage
    qty = floor_to_step(notional / ref_px, lot.step)

    return SizeDecision(
        factors_present=factors_present,
        size_pct_formula=formula,
        size_pct_final=final,
        capped=capped,
        margin=margin,
        notional=notional,
        qty=qty,
        ref_px=ref_px,
        leverage=leverage,
        refused_reason=_refusal(qty, ref_px, lot),
    )


def _refusal(qty: float, ref_px: float, lot: LotFilter) -> str | None:
    """เหตุผลที่ venue จะปฏิเสธไม้นี้ หรือ `None` ถ้าส่งได้

    ตรวจ `min_qty` ก่อน `min_notional` เพราะ `qty = 0` (ปัดลงจนหมด) เข้าข่ายทั้งสอง
    ข้อพร้อมกัน และเหตุผลที่ตรงกว่าคือ "เล็กกว่าขนาดขั้นต่ำ" ไม่ใช่ "มูลค่าน้อยไป"
    """
    if qty <= 0 or qty < lot.min_qty:
        return "below_min_qty"
    if lot.min_notional is not None and qty * ref_px < lot.min_notional:
        return "below_min_notional"
    return None
