"""สัญญาของคำตัดสิน — หกตัว สามต่อฝั่ง และกฎที่คำตัดสินต้องผ่านก่อนถูกเชื่อ (spec/04)

ไฟล์นี้ไม่รู้จัก LLM ไม่รู้จัก DB และไม่รู้จัก cache · มันตอบคำถามเดียว: **คำตัดสิน
หน้าตาแบบไหนถึงนับว่าใช้ได้** ซึ่งเป็นคำถามที่ต้องตอบได้ก่อนจะมีใครไปเรียกใคร

## ทำไม `validate()` ไม่ใช่ `__post_init__`

คำตัดสินที่มาจาก LLM เป็น **ข้อมูลที่ไม่น่าเชื่อถือ** — มันตอบ factor ผิดตัวได้ ตอบฝั่ง
ผิดได้ ตอบ `present = true` โดยไม่อ้างแท่งไหนเลยได้ · ถ้าตรวจใน `__post_init__` การ
สร้างวัตถุจะพังกลางทางแล้วผู้เรียกต้องดัก `TypeError` ปนกับ `ValueError` ของจริง
แยกเป็นฟังก์ชันทำให้ `judge.py` เขียนได้ตรงๆ ว่า "แปลงแล้วตรวจ ไม่ผ่านคือ fallback"
ซึ่งเป็นเส้นทางที่สเปกสั่ง (spec/04:93-99) ไม่ใช่ข้อยกเว้นที่หลุดขึ้นไปข้างบน

## `ConfluenceVerdict` ไม่ใช่ `Verdict` ของ `db/repo/decisions.py`

ตัวนี้คือ **สิ่งที่ LLM ยืนยัน** ตัวนั้นคือ **แถวที่บันทึกลงตาราง** ต่างกันที่ `cached`
ซึ่งไม่ใช่คุณสมบัติของคำตัดสิน แต่เป็นข้อเท็จจริงว่าคำตัดสินนั้น*มาถึงเราอย่างไร* —
LLM ไม่มีทางรู้และไม่ควรมีสิทธิ์บอก · `to_record()` เป็นที่เดียวที่สองโลกนี้มาต่อกัน
ถ้าปล่อยให้ `judge.py` ประกอบ `Verdict` เองจะมีจุดที่ตั้ง `cached` ได้มากกว่าหนึ่งจุด
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cane.db.repo.decisions import Verdict

#: factor ของแต่ละฝั่ง เรียงตาม spec/04:18-25 · **ชื่อต้องตรงกับ
#: `ck_decision_verdicts_factor` ใน `db/schema.py`** ไม่งั้นเขียนลงตารางไม่ผ่าน
#:
#: ใบ 06 เขียน `SUPPORT_BREAKDOWN` แต่ทั้งสเปก glossary และ CHECK ของตารางเขียน
#: `CHANNEL_BREAKDOWN` — เรื่องนี้ไม่ใช่เรื่องรสนิยม ฐานข้อมูลปฏิเสธอีกชื่อจริงๆ
FACTORS_BY_SIDE: dict[str, tuple[str, ...]] = {
    "long": ("CHANNEL_BREAKOUT", "RETAIL_CAPITULATION", "HIGHER_LOW"),
    "short": ("CHANNEL_BREAKDOWN", "BUYING_EXHAUSTION", "LOWER_HIGH"),
}

#: ฝั่งไหนเป็นเจ้าของ factor ไหน — กลับด้านของตารางข้างบน สร้างจากตัวเดียวกันเพื่อ
#: ไม่ให้มีสองรายชื่อที่หลุดจากกันได้
SIDE_OF_FACTOR: dict[str, str] = {
    factor: side for side, factors in FACTORS_BY_SIDE.items() for factor in factors
}

#: ทุก factor เรียงแบบคงที่ · ใช้ตอนสร้าง CHECK และตอนวนทดสอบ
FACTORS: tuple[str, ...] = tuple(SIDE_OF_FACTOR)

#: ฝั่งของ**ไม้** ตรงกับ `side_t` ของ schema ไม่ใช่ฝั่งของออเดอร์
SIDES: tuple[str, ...] = tuple(FACTORS_BY_SIDE)


@dataclass(frozen=True, slots=True)
class ConfluenceVerdict:
    """สิ่งที่ LLM ยืนยันเกี่ยวกับ factor หนึ่งตัว — ยังไม่ผ่านการตรวจ

    **การมีวัตถุตัวนี้อยู่ไม่ได้แปลว่ามันถูกต้อง** ต้องผ่าน `validate()` ก่อนเสมอ
    ดูเหตุผลที่แยกกันในหัวไฟล์

    `confidence` มีไว้ให้คนตรวจย้อนหลังเท่านั้น **ห้ามผูกกับขนาดไม้** (ADR 12,
    spec/04:58) · `evidence_bars` คือดัชนีแท่งในชุดที่ถูกใส่ลง prompt ซึ่งต้องเป็น
    ชุดเดียวกับที่ `features()` อ่าน (ดูหัวไฟล์ `indicators/features.py`)
    """

    factor: str
    side: str
    present: bool
    confidence: float | None = None
    evidence_bars: tuple[int, ...] = ()
    rationale: str | None = None

    def to_record(self, *, cached: bool) -> Verdict:
        """แปลงเป็นแถวของ `decision_verdicts` · `cached` มาจากผู้เรียก ไม่ใช่จาก LLM

        เป็นที่ **เดียว** ที่ `cached` ถูกตั้ง — ดูหัวไฟล์ว่าทำไมถึงไม่ยอมให้มีสองที่
        """
        from cane.db.repo.decisions import Verdict

        return Verdict(
            factor=self.factor,
            side=self.side,
            present=self.present,
            cached=cached,
            confidence=self.confidence,
            evidence_bars=self.evidence_bars,
            rationale=self.rationale,
        )


def validate(verdict: ConfluenceVerdict, *, asked_factor: str, asked_side: str) -> None:
    """ตรวจว่าคำตัดสินตอบคำถามที่ถามจริง · ไม่ผ่าน = `ValueError` ให้ผู้เรียก fallback

    ตรวจ `asked_*` ด้วยไม่ใช่ตรวจแค่ความสอดคล้องภายใน เพราะความล้มเหลวที่อันตราย
    ที่สุดของ LLM ไม่ใช่การตอบมั่ว แต่คือการ**ตอบคำถามอื่น**อย่างมั่นใจ — verdict ของ
    `HIGHER_LOW` ที่ถูกเก็บลงช่องของ `CHANNEL_BREAKOUT` จะดูสมเหตุสมผลทุกประการ
    ตอนอ่านย้อนหลัง และไม่มีอะไรในระบบจับได้เลย

    `present = True` ที่ไม่มี `evidence_bars` ถูกปฏิเสธตาม spec/04:55 — คำตัดสินที่
    ชี้แท่งไม่ได้คือความเห็น ไม่ใช่หลักฐาน และไม่ควรมีผลต่อขนาดไม้
    """
    if asked_factor not in SIDE_OF_FACTOR:
        raise ValueError(f"ไม่รู้จัก factor {asked_factor!r} — มีแต่ {', '.join(FACTORS)}")
    if SIDE_OF_FACTOR[asked_factor] != asked_side:
        raise ValueError(
            f"factor {asked_factor!r} เป็นของฝั่ง {SIDE_OF_FACTOR[asked_factor]!r} "
            f"ไม่ใช่ {asked_side!r} — ไม่มีการหักลบข้ามฝั่ง (spec/04:26)"
        )

    if verdict.factor != asked_factor:
        raise ValueError(f"ถาม {asked_factor!r} แต่ตอบ {verdict.factor!r}")
    if verdict.side != asked_side:
        raise ValueError(f"ถามฝั่ง {asked_side!r} แต่ตอบฝั่ง {verdict.side!r}")
    if verdict.confidence is not None and not 0.0 <= verdict.confidence <= 1.0:
        raise ValueError(f"confidence ต้องอยู่ใน 0..1 ไม่ใช่ {verdict.confidence}")
    if verdict.present and not verdict.evidence_bars:
        raise ValueError(
            f"{asked_factor} ตอบว่ามีปัจจัยแต่ไม่อ้างแท่งไหนเลย — "
            "evidence_bars ห้ามว่างเมื่อ present (spec/04:55)"
        )
    if any(bar < 0 for bar in verdict.evidence_bars):
        raise ValueError(f"evidence_bars ต้องเป็นดัชนีแท่งที่ไม่ติดลบ: {verdict.evidence_bars}")


def absent(factor: str) -> ConfluenceVerdict:
    """คำตัดสิน "ไม่มีปัจจัย" ที่ใช้ตอน LLM ล่ม (ADR 6, spec/04:91-99)

    **ไม่ใช่คำตัดสินของ LLM** และห้ามเขียนลง cache — ถ้า timeout ครั้งเดียวกลายเป็น
    `present = false` ถาวรของแท่งนั้น ความเสถียรของผู้ให้บริการจะกลายเป็นตัวแปรของ
    กลยุทธ์อย่างถาวร ซึ่งตรงข้ามกับเหตุผลที่ ADR 6 เลือก fallback แทนการข้ามสัญญาณ

    `rationale` เป็น `None` โดยเจตนา — ตัวที่บอกว่านี่คือ fallback คือ `llm_fallback`
    ใน `DecisionRecord` ไม่ใช่ข้อความที่แอบเขียนไว้ในช่องของ LLM
    """
    return ConfluenceVerdict(
        factor=factor, side=SIDE_OF_FACTOR[factor], present=False
    )
