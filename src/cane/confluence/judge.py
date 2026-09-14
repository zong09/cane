"""ถาม LLM ทีละ factor ผ่าน cache แล้วคืนคำตัดสินของทั้งฝั่ง (spec/04, spec/08:31)

ไฟล์นี้ไม่รู้จัก SDK ของผู้ให้บริการรายไหน มันรู้จักแค่ `LlmClient` — ท่าเดียวกับที่
`execution/broker.py` ไม่รู้ว่าปลายทางเป็น PaperBroker หรือ CcxtBroker (ADR 3)
เหตุผลตรงกัน: ตรรกะที่ตัดสินว่า "คำตอบนี้ใช้ได้ไหม" ต้องเขียนและทดสอบได้โดยไม่ต้องมี
คีย์ ไม่ต้องต่อเน็ต และไม่ต้องจ่ายเงินต่อการรันเทสต์หนึ่งครั้ง

## `temperature 0` ของสเปกทำไม่ได้แล้ว — และไม่จำเป็นต้องทำ

spec/04:64 สั่ง `temperature 0` คู่กับ structured output · โมเดล Claude รุ่นปัจจุบัน
**ถอด `temperature` ออกจาก API แล้ว ส่งไปได้ 400** · ข้อนี้ไม่ทำให้เจตนาของสเปกพัง
เพราะ temperature 0 ไม่เคยรับประกัน determinism ของ LLM จริง ตัวที่รับประกันคือข้อ 2
ของสเปกเอง — cache ต่อแท่ง ซึ่งอยู่ที่ `cache.py` · **สเปกข้อ 1 ควรถูกแก้**

## `prompt_hash` เป็นค่าเดียวต่อฝั่ง ไม่ใช่ค่าต่อ factor

สามไฟล์ prompt ของฝั่งหนึ่งถูก hash รวมกันเป็นค่าเดียว **พร้อม `model_id` และ JSON
schema ของคำตอบ** · แลกกันตรงนี้: การแก้ถ้อยคำของ `HIGHER_LOW` จะทำให้ cache ของอีก
สองตัวในฝั่ง long ใช้ไม่ได้ไปด้วย ซึ่งเสียการเรียกเพิ่มไม่กี่ครั้งเฉพาะตอนแก้ prompt
· ที่ได้กลับมาคือแนวคิดเดียว ค่าเดียว และมันลงคอลัมน์ `decisions.prompt_hash` ที่มี
ช่องเดียวได้พอดี ไม่ต้องมี hash สองระดับให้ตรวจสอบว่าตรงกันไหม

**`model_id` อยู่ใน hash ด้วย** สเปกไม่ได้เขียนไว้ แต่การเปลี่ยนโมเดลมีผลต่อคำตอบ
ไม่น้อยกว่าการแก้ถ้อยคำ ถ้าไม่รวม การสลับโมเดลจะอ่านคำตัดสินของโมเดลเก่ามาใช้ต่อ
เงียบๆ ซึ่งเป็นการ "หลอก" ข้อเดียวกับที่สเปกตั้งใจกัน (spec/04:60)

## fallback ไม่เคยลง cache

ADR 6 บอกว่า LLM ล่ม → ถือว่าไม่มีปัจจัยแล้วเข้าไม้ที่ `base_pct` · ถ้าคำตอบ fallback
นั้นถูกเขียนลง cache timeout ครั้งเดียวจะกลายเป็น `present = false` **ถาวร**ของแท่งนั้น
แล้วความเสถียรของผู้ให้บริการจะกลายเป็นตัวแปรของกลยุทธ์อย่างถาวร ซึ่งตรงข้ามกับ
เหตุผลที่ ADR 6 เลือก fallback แทนการข้ามสัญญาณ · คำตัดสินที่ `validate()` ไม่ผ่าน
ก็เช่นกัน — มันไม่ใช่คำตอบ มันคือขยะที่บังเอิญมาถึง

## ล้มทั้งฝั่ง ไม่ใช่ล้มทีละตัว

factor ตัวใดตัวหนึ่งพังทำให้ทั้งฝั่งเป็น fallback · เหตุผล: `factors_present` เข้าสูตร
ขนาดไม้โดยตรง (spec/05) การนับจากของที่ครบบ้างไม่ครบบ้างให้ตัวเลขที่อ่านย้อนหลังไม่ได้
— "2 ปัจจัย" ที่แปลว่า "ตอบได้ 2 จาก 3" กับที่แปลว่า "ตอบครบ 3 มี 2" เป็นคนละเรื่อง
แต่ลงคอลัมน์เดียวกัน · ตัวที่ตอบได้แล้วยังอยู่ใน cache ไม่เสียเปล่า รอบหน้าไม่ต้องถามซ้ำ
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Protocol

from cane.confluence import cache
from cane.confluence.schema import (
    FACTORS_BY_SIDE,
    SIDE_OF_FACTOR,
    ConfluenceVerdict,
    absent,
    validate,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection

    from cane.data.ohlcv import Bar
    from cane.indicators.features import Features

#: JSON schema ที่บังคับรูปคำตอบ (spec/04:44-56) · `additionalProperties: false` กับ
#: `required` ครบทุกช่องเป็นข้อบังคับของ structured output ฝั่งผู้ให้บริการ ไม่ใช่
#: ความเข้มงวดที่เราเลือกเอง — ขาดไปแล้วคำขอถูกปฏิเสธ
#:
#: `factor` กับ `side` อยู่ในคำตอบทั้งที่เรารู้อยู่แล้วว่าถามอะไร **โดยเจตนา** —
#: `validate()` เอาสองช่องนี้ไปเทียบกับคำถาม ซึ่งเป็นด่านเดียวที่จับได้ว่าโมเดล
#: ตอบคำถามอื่นอยู่ (ดู `schema.py`)
VERDICT_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "factor": {"type": "string"},
        "side": {"type": "string", "enum": ["long", "short"]},
        "present": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_bars": {"type": "array", "items": {"type": "integer"}},
        "rationale": {"type": "string"},
    },
    "required": [
        "factor",
        "side",
        "present",
        "confidence",
        "evidence_bars",
        "rationale",
    ],
    "additionalProperties": False,
}

#: เหตุผลที่ทำให้ทั้งฝั่งตกไป fallback · ชุดปิดในโค้ด ไม่ใช่ข้อความอิสระ เพราะมันลง
#: `decisions.llm_fallback_reason` แล้วต้องนับกลุ่มได้ตอนอ่านย้อนหลัง
#: (คอลัมน์นั้นเป็น `Text` ไม่มี CHECK — ชุดปิดจึงต้องถูกรักษาที่นี่)
FALLBACK_REASONS = ("transport", "bad_schema", "bad_verdict")

#: จำนวนแท่งท้ายสุดที่ใส่ลง prompt เป็นตาราง OHLCV · มากกว่านี้ไม่ได้เพิ่มข้อมูลให้
#: factor ตัวไหนเลย (หน้าต่างที่ยาวที่สุดที่ `features()` ใช้คือ 20 แท่ง) แต่ทำให้
#: ค่าใช้จ่ายต่อการตัดสินหนึ่งครั้งโตขึ้นตรงๆ
CONTEXT_BARS = 40


class LlmClient(Protocol):
    """สัญญากับผู้ให้บริการ LLM — ชั้นนี้ไม่รู้ว่าปลายทางเป็นใคร (ท่าเดียวกับ ADR 3)

    `ask()` คืน dict ที่แกะจาก JSON แล้ว หรือ **ยก exception** เมื่อคุยไม่สำเร็จ
    (timeout, rate limit, ตอบไม่เป็น JSON) · ไม่มีค่าคืนที่แปลว่า "พัง" เพราะการ
    บังคับให้ผู้เรียกดูค่าคืนก่อนใช้คือกลไกที่ถูกลืมได้ ส่วน exception ไม่ถูกลืม

    ตัว adapter จริงต้องส่ง `schema` เข้าพารามิเตอร์ structured output ของผู้ให้บริการ
    **ไม่ใช่แปะลงใน prompt แล้วหวัง** — ความต่างคือคำตอบที่ผิดรูปเป็นไปไม่ได้
    กับคำตอบที่ผิดรูปได้แต่เราขอไว้ว่าอย่า
    """

    def ask(
        self, *, system: str, user: str, schema: dict[str, object]
    ) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """ผลของการตัดสินทั้งฝั่ง — ตรงกับช่องที่ `DecisionRecord` ต้องกรอก (spec/07:166)

    `fallback` แยก "LLM ตัดสินว่าไม่มีปัจจัย" ออกจาก "LLM ตอบไม่ได้" ซึ่ง spec/04:101
    บอกว่าต้องแยกให้ออก ไม่งั้นตอนอ่านย้อนหลังจะแยกไม่ได้ว่าไม้เล็กเพราะอะไร

    `verdicts` เรียงตาม `FACTORS_BY_SIDE[side]` เสมอ ไม่ใช่ตามลำดับที่ตอบกลับมา
    """

    verdicts: tuple[ConfluenceVerdict, ...]
    cached: tuple[bool, ...]
    fallback: bool
    fallback_reason: str | None
    prompt_hash: str

    @property
    def factors_present(self) -> int:
        """จำนวนปัจจัยที่ `present` — ตัวที่เข้าสูตรขนาดไม้ (spec/05)"""
        return sum(1 for verdict in self.verdicts if verdict.present)


def prompt_text(factor: str) -> str:
    """เนื้อ prompt ของ factor หนึ่งตัว อ่านจากไฟล์ในแพ็กเกจ

    แยกเป็นไฟล์ไม่ใช่สตริงในโค้ดเพราะ prompt เป็นของที่คนแก้ถ้อยคำ และ diff ของ
    ไฟล์ `.md` อ่านรู้เรื่องกว่า diff ของสตริงหลายบรรทัดใน `.py` · `prompts/long/`
    กับ `prompts/short/` เป็นคนละชุดตาม spec/04:78-82 ไม่ใช่ template ที่สลับคำ
    """
    side = SIDE_OF_FACTOR[factor]
    name = f"{factor.lower()}.md"
    return (
        resources.files("cane.confluence.prompts")
        .joinpath(side, name)
        .read_text(encoding="utf-8")
    )


def prompt_hash(side: str, model_id: str) -> str:
    """ลายนิ้วมือของ "ชุด prompt + โมเดล + รูปคำตอบ" ที่ใช้ตัดสินฝั่งนี้

    เข้าไปอยู่ในคีย์ของ cache และลง `decisions.prompt_hash` · ดูหัวไฟล์ว่าทำไมถึง
    เป็นค่าเดียวต่อฝั่ง และทำไม `model_id` ถึงต้องอยู่ในนั้น

    `sort_keys=True` ตอน dump schema เพราะลำดับคีย์ของ dict ไม่ใช่ข้อมูล — ถ้าไม่
    บังคับเรียง การจัดบรรทัดใหม่ในโค้ดจะล้าง cache ทั้งก้อนโดยไม่มีอะไรเปลี่ยนจริง
    """
    parts = [model_id, json.dumps(VERDICT_JSON_SCHEMA, sort_keys=True)]
    parts.extend(prompt_text(factor) for factor in FACTORS_BY_SIDE[side])
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8"))
    return digest.hexdigest()[:32]


def render_context(bars: Sequence[Bar], feat: Features) -> str:
    """ตัวเลขที่ LLM เห็น — OHLCV ท้ายชุดพร้อม feature ที่คำนวณมาให้แล้ว (ADR 4)

    **ดัชนีแท่งในตารางคือดัชนีเดียวกับที่ `features()` อ้างถึง** ซึ่งคือตำแหน่งใน
    `bars` ที่ส่งเข้ามา ไม่ใช่ลำดับของแถวในตาราง · ข้อนี้คือสิ่งที่ทำให้
    `evidence_bars` ที่ LLM ตอบกลับมาชี้ไปที่แท่งเดียวกับที่ `swing_lows` และ
    `points` ของเส้นแนวโน้มพูดถึง ถ้าตารางเริ่มนับหนึ่งใหม่ ทุกเลขจะเลื่อนโดยไม่มี
    อะไรฟ้อง (ดูหัวไฟล์ `indicators/features.py`)

    ปัดทศนิยมคงที่และไม่ใส่เวลานาฬิกาใดๆ — ข้อความนี้ต้องเหมือนเดิมเป๊ะเมื่อป้อน
    แท่งชุดเดิม ไม่งั้น cache ที่คีย์ด้วย `bar_close_ts` จะตรงแต่เนื้อที่ส่งไปไม่ตรง
    """
    start = max(0, len(bars) - CONTEXT_BARS)
    rows = "\n".join(
        f"| {i} | {bars[i].open:.8g} | {bars[i].high:.8g} | "
        f"{bars[i].low:.8g} | {bars[i].close:.8g} | {bars[i].volume:.8g} |"
        for i in range(start, len(bars))
    )
    return (
        f"## แท่งย้อนหลัง (ดัชนี {start}–{len(bars) - 1} · แท่งที่ตัดสินคือ "
        f"{feat.bar_index})\n\n"
        "| i | open | high | low | close | volume |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"{rows}\n\n"
        "## feature ที่คำนวณมาให้แล้ว\n\n"
        f"```json\n{json.dumps(_features_json(feat), ensure_ascii=False, indent=2)}\n```"
    )


def judge_side(
    conn: Connection,
    client: LlmClient,
    *,
    market: str,
    symbol: str,
    timeframe: str,
    bars: Sequence[Bar],
    feat: Features,
    side: str,
    model_id: str,
) -> JudgeResult:
    """ตัดสิน 3 factor ของฝั่งที่กำลังจะเข้า · ไม่ถามฝั่งตรงข้ามเลย (spec/04:26)

    ถามทีละตัวตามลำดับ ไม่ขนาน — spec/08:31 บอกว่า "ขนานกันได้" ซึ่งเป็นการอนุญาต
    ไม่ใช่ข้อบังคับ และการทำ concurrency ตอนที่ยังไม่มีหลักฐานว่าช้าคือการเพิ่มทาง
    ให้บั๊กเข้ามาโดยไม่ได้อะไรตอบแทน

    `conn` เป็นของผู้เรียก — แถวที่เขียนลง cache อยู่ในทรานแซกชันเดียวกับไม้นั้น
    ถ้าไม้ถูกย้อนกลับ คำตัดสินก็หายไปด้วย ซึ่งถูกแล้ว เพราะการมีคำตัดสินค้างอยู่โดย
    ไม่มีบันทึกการตัดสินใจคู่กันคือสถานะที่อธิบายไม่ได้
    """
    if side not in FACTORS_BY_SIDE:
        raise ValueError(f"ฝั่งต้องเป็น long หรือ short ไม่ใช่ {side!r}")

    fingerprint = prompt_hash(side, model_id)
    context = render_context(bars, feat)
    verdicts: list[ConfluenceVerdict] = []
    from_cache: list[bool] = []

    for factor in FACTORS_BY_SIDE[side]:
        key = cache.CacheKey(
            market=market,
            symbol=symbol,
            timeframe=timeframe,
            bar_close_ts=feat.bar_close_ts,
            side=side,
            factor=factor,
            prompt_hash=fingerprint,
        )
        hit = cache.get(conn, key)
        if hit is not None:
            verdicts.append(hit)
            from_cache.append(True)
            continue

        try:
            verdict = _ask_one(client, factor=factor, side=side, context=context)
        except _JudgeFailed as failure:
            # ล้มทั้งฝั่ง ดูหัวไฟล์ · ตัวที่ตอบไปแล้วยังอยู่ใน cache ไม่เสียเปล่า
            return _fallback(side, fingerprint, failure.reason)

        cache.put(conn, key, verdict)
        verdicts.append(verdict)
        from_cache.append(False)

    return JudgeResult(
        verdicts=tuple(verdicts),
        cached=tuple(from_cache),
        fallback=False,
        fallback_reason=None,
        prompt_hash=fingerprint,
    )


class _JudgeFailed(Exception):
    """ความล้มเหลวที่แปลเป็น fallback ได้ · `reason` เป็นหนึ่งใน `FALLBACK_REASONS`"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _ask_one(
    client: LlmClient, *, factor: str, side: str, context: str
) -> ConfluenceVerdict:
    """ถาม LLM หนึ่งครั้งแล้วแปลงเป็นคำตัดสินที่ตรวจแล้ว

    ดัก `Exception` กว้างตรงที่เรียก `client.ask()` **โดยเจตนา** — ผู้ให้บริการแต่ละ
    รายยก exception คนละชุด และชั้นนี้ไม่ควรต้องรู้จักชื่อคลาสของใครเลย สิ่งที่
    สำคัญคือ "คุยไม่สำเร็จ" ซึ่งจบเหมือนกันหมดตาม ADR 6 · การดักแคบกว่านี้แปลว่า
    exception ที่ไม่ได้ระบุไว้จะทะลุขึ้นไปหยุดไปป์ไลน์ทั้งแท่ง ซึ่งคือ "ข้ามสัญญาณ"
    ที่ ADR 6 ปฏิเสธไปแล้ว
    """
    try:
        raw = client.ask(
            system=prompt_text(factor), user=context, schema=VERDICT_JSON_SCHEMA
        )
    except Exception as error:  # noqa: BLE001 — ดูเหตุผลใน docstring
        raise _JudgeFailed("transport") from error

    try:
        verdict = ConfluenceVerdict(
            factor=str(raw["factor"]),
            side=str(raw["side"]),
            present=_strict_bool(raw["present"]),
            confidence=None if raw["confidence"] is None else float(raw["confidence"]),
            evidence_bars=tuple(int(bar) for bar in raw["evidence_bars"]),
            rationale=None if raw["rationale"] is None else str(raw["rationale"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _JudgeFailed("bad_schema") from error

    try:
        validate(verdict, asked_factor=factor, asked_side=side)
    except ValueError as error:
        raise _JudgeFailed("bad_verdict") from error
    return verdict


def _strict_bool(value: object) -> bool:
    """`present` ต้องเป็น `bool` จริง ไม่ใช่ของที่ truthy

    `bool("ไม่ใช่")` เป็น `True` และ `bool("false")` ก็เป็น `True` — การ coerce ตรงนี้
    จะเปลี่ยนคำตอบที่ผิดรูปให้กลายเป็นคำตัดสิน "มีปัจจัย" ที่ดูปกติทุกประการ แล้วมัน
    จะถูกเขียนลง cache อย่างถาวรและเข้าสูตรขนาดไม้ · structured output ควรกันไว้ให้
    แล้ว แต่ชั้นนี้มีไว้เพราะ "ควรกัน" ไม่เท่ากับ "กัน"
    """
    if not isinstance(value, bool):
        raise TypeError(f"present ต้องเป็น bool ไม่ใช่ {type(value).__name__}: {value!r}")
    return value


def _fallback(side: str, fingerprint: str, reason: str) -> JudgeResult:
    return JudgeResult(
        verdicts=tuple(absent(factor) for factor in FACTORS_BY_SIDE[side]),
        cached=(False,) * len(FACTORS_BY_SIDE[side]),
        fallback=True,
        fallback_reason=reason,
        prompt_hash=fingerprint,
    )


def _features_json(feat: Features) -> dict[str, object]:
    """feature เป็น dict ที่ JSON แปลงได้ · ชื่อช่องตรงกับที่ prompt อ้างถึง"""

    def line(value):
        if value is None:
            return None
        return {
            "slope": round(value.slope, 8),
            "intercept": round(value.intercept, 8),
            "distance_atr": round(value.distance_atr, 4),
            "points": list(value.points),
        }

    return {
        "bar_index": feat.bar_index,
        "close": round(feat.close_px, 8),
        "atr": round(feat.atr, 8),
        "swing_lows": [
            {"index": p.index, "price": round(p.price, 8)} for p in feat.swing_lows
        ],
        "swing_highs": [
            {"index": p.index, "price": round(p.price, 8)} for p in feat.swing_highs
        ],
        "resistance": line(feat.resistance),
        "support": line(feat.support),
        "red_run": feat.red_run,
        "green_run": feat.green_run,
        "body_atr": round(feat.body_atr, 4),
        "body_z": None if feat.body_z is None else round(feat.body_z, 4),
        "gap_atr": round(feat.gap_atr, 4),
    }
