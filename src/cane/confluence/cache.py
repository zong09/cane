"""อ่าน/เขียน `verdict_cache` — กลไกที่ทำให้ "ตัดสินครั้งเดียวจบ" เป็นจริง (spec/04 §ข้อบังคับเรื่องความคงเส้นคงวา)

ฟังก์ชันธรรมดาบน `Connection` ท่าเดียวกับ `db/repo/*` ไม่ใช่คลาส — ไม่มีสถานะอะไรให้ถือ
ระหว่างการเรียก และการรับ `Connection` เข้ามาทำให้ผู้เรียกเป็นเจ้าของทรานแซกชัน ซึ่งเป็น
สิ่งที่ไปป์ไลน์ต่อแท่งต้องการ (ทั้งแท่งสำเร็จหรือทั้งแท่งย้อนกลับ)

## `CacheKey` มีอยู่เพราะคีย์เจ็ดช่องที่ส่งเป็น argument เรียงกันคือบั๊กที่รอเกิด

`market` กับ `symbol` กับ `timeframe` เป็น `str` ทั้งสามช่อง สลับกันแล้วไม่มีอะไรฟ้อง
จนกว่าจะอ่านไม่เจอ — ซึ่ง cache ที่อ่านไม่เจอ**ไม่ดัง** มันแค่ไปเรียก LLM ใหม่
เงียบๆ แล้วบิลก็ขึ้น · นี่คือความล้มเหลวที่ต้องกันด้วยชนิดข้อมูล ไม่ใช่ด้วยความระวัง

## `side` อยู่ในคีย์ทั้งที่ซ้ำซ้อนกับ `factor`

spec/04 §ข้อบังคับเรื่องความคงเส้นคงวา บอกว่า `side` อยู่ในคีย์เพราะมันอยู่ใน PK คู่กับ CHECK ที่ผูก factor↔side
**ไม่ใช่** เพราะคำตัดสินสองฝั่งจะทับกัน — ชื่อ factor ของสองฝั่งไม่ซ้ำกันเลยสักตัว
(`SIDE_OF_FACTOR` คือฟังก์ชันสมบูรณ์จาก factor ไป side) คำถามของฝั่ง long จึงชนแถว
ของฝั่ง short ไม่ได้อยู่แล้วแม้จะถอด `side` ออกจาก `WHERE`

พิสูจน์แล้วด้วยการกลายพันธุ์: ถอด `side` ออกจาก `_match()` แล้วเทสต์ทั้งชุดยังเขียว
**สิ่งที่กันการทับกันจริงคือความไม่ซ้ำของชื่อ factor ไม่ใช่คอลัมน์นี้**

คงไว้เพราะสองอย่าง: มันอยู่ใน PK ของตารางคู่กับ CHECK ที่ผูก factor กับ side
(แถวที่จับคู่ผิดเขียนลงไม่ได้เลย) และมันทำให้แถวอธิบายตัวเองได้ตอนอ่าน SQL ตรงๆ
· `test_the_factor_sets_of_the_two_sides_never_overlap` คือด่านที่จะดังถ้าวันหนึ่งมี
ใครใส่ชื่อซ้ำเข้าไป ซึ่งเป็นวันที่ `side` จะกลายเป็นสิ่งจำเป็นจริงๆ

## ไม่มีฟังก์ชันลบ และไม่มี TTL

ADR 23 ให้สิทธิ์ engine แค่ `SELECT, INSERT` — ต่อให้เขียนโค้ดลบ ฐานก็ปฏิเสธ ·
cache ตัวนี้ไม่ต้องมี invalidation เพราะทุกอย่างที่ทำให้คำตอบเปลี่ยนได้อยู่ในคีย์
อยู่แล้ว (prompt, โมเดล, แท่ง, ฝั่ง, factor) แถวเก่าไม่ผิด มันแค่ไม่ถูกถามอีก
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, and_, select

from cane.confluence.schema import ConfluenceVerdict
from cane.db.schema import verdict_cache
from cane.db.types import now_ms, pct_from_db, pct_to_db, store_symbol


@dataclass(frozen=True, slots=True)
class CacheKey:
    """คีย์เจ็ดช่องของ `verdict_cache` ตาม spec/04 §ข้อบังคับเรื่องความคงเส้นคงวา

    `market` อยู่ในคีย์เพราะ ADR 26 ทำให้มันเป็นมิติของ symbol · ตอนสร้างตารางนี้
    สเปกยังเขียนคีย์เป็นหกช่อง (แก้แล้ว) · ที่มาเต็มอยู่ในหัวไฟล์ของ migration 0007

    `symbol` ถูกทำให้เป็นรูปสั้นตอนสร้าง (`store_symbol`) ไม่ใช่ตอนเขียนลงตาราง —
    ถ้าปล่อยไว้ถึงชั้น SQL คีย์ที่สร้างจาก `BTC/USDT:USDT` กับจาก `BTC/USDT` จะเป็น
    คนละวัตถุที่เท่ากันไม่ได้ ทั้งที่ชี้ไปแถวเดียวกัน แล้ว cache ในหน่วยความจำของ
    ผู้เรียกชั้นบน (ถ้ามีวันหนึ่ง) จะพลาดทุกครั้ง
    """

    market: str
    symbol: str
    timeframe: str
    bar_close_ts: int
    side: str
    factor: str
    prompt_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", store_symbol(self.symbol))


def get(conn: Connection, key: CacheKey) -> ConfluenceVerdict | None:
    """คำตัดสินที่เคยตัดสินไว้ หรือ `None` ถ้ายังไม่เคยถูกถาม

    `None` แปลว่า **ยังไม่เคยถาม** ไม่ใช่ "ถามแล้วตอบว่าไม่มีปัจจัย" — อย่างหลังเป็น
    แถวจริงที่มี `present = false` · ผู้เรียกต้องแยกสองอย่างนี้ออกจากกัน ไม่งั้น
    ทุกแท่งที่ LLM ตอบว่า "ไม่มีปัจจัย" จะถูกถามใหม่ทุกครั้งที่ process กลับมา
    """
    row = conn.execute(
        select(
            verdict_cache.c.present,
            verdict_cache.c.confidence,
            verdict_cache.c.evidence_bars,
            verdict_cache.c.rationale,
        ).where(_match(key))
    ).one_or_none()
    if row is None:
        return None
    return ConfluenceVerdict(
        factor=key.factor,
        side=key.side,
        present=row.present,
        confidence=None if row.confidence is None else pct_from_db(row.confidence),
        evidence_bars=tuple(row.evidence_bars or ()),
        rationale=row.rationale,
    )


def put(conn: Connection, key: CacheKey, verdict: ConfluenceVerdict) -> None:
    """เขียนคำตัดสินลง cache · เขียนซ้ำคีย์เดิม = `IntegrityError` ไม่ใช่ทับเงียบๆ

    **ไม่มี upsert โดยเจตนา** — engine ไม่มีสิทธิ์ `UPDATE` อยู่แล้ว (ADR 23) และการ
    เขียนทับคำตัดสินของแท่งเดิมด้วยคีย์เดิมคือการเปลี่ยนอดีต ซึ่งเป็นสิ่งเดียวที่
    ตารางนี้มีไว้เพื่อทำให้เป็นไปไม่ได้ · ถ้าผู้เรียกยิงซ้ำแปลว่ามันลืมเรียก `get()`
    ก่อน ซึ่งเป็นบั๊กที่ควรดัง

    **ห้ามเรียกด้วย verdict ที่มาจาก fallback หรือที่ `validate()` ไม่ผ่าน** —
    ที่นี่บังคับไม่ได้เพราะมันแยกไม่ออกด้วยตัวเอง ผู้เรียกคือ `judge.py` ที่รู้
    (ดูหัวข้อของมัน) · ฐานกัน `present` ที่ไม่มีหลักฐานไว้ให้ชั้นหนึ่งแล้ว
    """
    conn.execute(
        verdict_cache.insert().values(
            market=key.market,
            symbol=key.symbol,
            timeframe=key.timeframe,
            bar_close_ts=key.bar_close_ts,
            side=key.side,
            factor=key.factor,
            prompt_hash=key.prompt_hash,
            present=verdict.present,
            confidence=(
                None if verdict.confidence is None else pct_to_db(verdict.confidence)
            ),
            evidence_bars=list(verdict.evidence_bars) or None,
            rationale=verdict.rationale,
            created_ts=now_ms(),
        )
    )


def _match(key: CacheKey):
    return and_(
        verdict_cache.c.market == key.market,
        verdict_cache.c.symbol == key.symbol,
        verdict_cache.c.timeframe == key.timeframe,
        verdict_cache.c.bar_close_ts == key.bar_close_ts,
        verdict_cache.c.side == key.side,
        verdict_cache.c.factor == key.factor,
        verdict_cache.c.prompt_hash == key.prompt_hash,
    )
