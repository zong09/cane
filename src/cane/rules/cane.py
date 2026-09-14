"""กฎไม้เรียว — เข้าได้เฉพาะที่แท่งสัญญาณ บังคับทั้งสองฝั่ง ไม่มี config ให้ปิด (spec/03)

> "นักเรียนทุกคนต้องเข้าซื้อที่แท่งเขียวแรกสุดเท่านั้น หากเห็นราคาพุ่งสูงไปแล้วหลายวัน
> ห้ามเข้าซื้อเด็ดขาด"

**เลขคณิตและตรรกะล้วน ไม่แตะ DB ไม่ต่อเน็ต ไม่รู้จัก broker** · ไฟล์นี้ตอบคำถามเดียว:
แท่งนี้ควรปิดอะไร เปิดอะไร และถ้าไม่เปิด เพราะประตูบานไหนปิด

## ทำไมไม่มีสวิตช์ปิดกฎนี้

เพราะมันเป็นกฎ ไม่ใช่พารามิเตอร์ · เหตุผลเชิงเทคนิคที่เอกสารต้นทางให้คือเข้าช้าแล้ว
จุดตัดขาดทุนอยู่ไกลเกินไป ความเสี่ยงต่อไม้จึงโตขึ้นโดยที่ขนาดไม้เท่าเดิม — บน perp
ที่มี leverage ข้อนี้หนักขึ้นอีกเพราะจุดตัดที่ไกลกว่าแปลว่าใกล้ราคา liquidation กว่า
· ข้อยกเว้นเดียวของทั้งระบบคือ cold start (ใบ 09) ซึ่งมีเงื่อนไขล้อมแน่นและมี stop บังคับ

## `no_signal` กับ `cane_rule` ต่างกันตรงไหน — และนี่คือการตีความ

`SKIP_REASONS` ของใบ 03 มีทั้งสองค่าและคอมเมนต์บอกให้ "เลือกจากประตูแรกที่ปิด"
แต่ไม่ได้เขียนว่าเส้นแบ่งอยู่ตรงไหน · ที่นี่อ่านว่า:

- **`no_signal`** — ไม่มีเทรนด์ให้ตามอยู่แล้ว (`state` ยังไม่ตั้งตัว) แท่งธรรมดาที่
  ไม่มีอะไรเกิดขึ้น ซึ่งคือแท่งส่วนใหญ่ของทั้งปี
- **`cane_rule`** — **มีเทรนด์เดินอยู่** แต่แท่งนี้ไม่ใช่จุดสัญญาณ · การเข้าตรงนี้คือ
  การไล่ราคา และนี่คือจุดที่กฎไม้เรียวทำงานจริง ไม่ใช่จุดที่ไม่มีอะไรให้ทำ

สองอย่างนี้ต้องแยกกันเพราะคอนโซลกับรายงานตอบคนละคำถาม: "วันนี้เงียบ" กับ "เทรนด์
กำลังวิ่งแต่เราอยู่นอกเพราะตกรถ" — อย่างหลังคือสถานะที่ใบ 09 (cold start) มีไว้เพื่อ
จัดการโดยเฉพาะ ถ้ายุบเป็นค่าเดียวจะมองไม่เห็นว่ามันเกิดบ่อยแค่ไหน

## ขาปิดมาก่อนขาเปิดเสมอ

spec/03 บอกว่า "การออกต้องถูกประเมินก่อนการเข้าเสมอ" เพราะถ้าลำดับกลับกันจะมีจังหวะ
ที่ระบบถือสถานะเกินเพดานที่ risk limit อนุญาต · ที่นี่บังคับด้วย**รูปของผลลัพธ์** —
`BarPlan` ถือทั้งสองขาไว้ในวัตถุเดียว และ `rules/flip.py` เป็นที่เดียวที่ยิงมันออกไป
ตามลำดับ ผู้เรียกจึงไม่มีทางยิงขาเปิดก่อนได้แม้จะเขียนผิด
"""

from __future__ import annotations

from dataclasses import dataclass

#: ฝั่งของ**ไม้** ตรงกับ `side_t` ของ schema — ไม่ใช่ฝั่งของออเดอร์ (`buy`/`sell`)
SIDES = ("long", "short")

#: ฝั่งตรงข้าม · ตารางเล็กแทน `if` เพราะมันถูกใช้สามที่และการเขียน `if` ซ้ำคือที่ที่
#: การพิมพ์ผิดจะรอดสายตา
OPPOSITE = {"long": "short", "short": "long"}


@dataclass(frozen=True, slots=True)
class BarPlan:
    """แท่งนี้ต้องทำอะไร · `close_side` ถูกประเมินก่อน `open_side` เสมอ (ดูหัวไฟล์)

    `needs_judge` เป็นเท็จเมื่อไม่มีขาเปิด — spec/04:105 บอกว่า "ขาปิดของ flip ไม่เรียก
    Judge เลย การปิดไม่ต้องการขนาดไม้ ปิดคือปิดทั้งหมดเสมอ" · ความล้มเหลวของ LLM
    จึงไม่มีวันขวางการปิดสถานะ ซึ่งเป็นคุณสมบัติที่ต้องอ่านออกจากตรงนี้ ไม่ใช่ต้อง
    ไปไล่ดูว่าใครเรียก Judge บ้าง

    `skip_reason` ไม่เป็น `None` เมื่อ **ไม่มีขาเปิด** — ตรงกับ invariant ของใบ 03
    ที่ `_check_skip_reason()` บังคับไว้ (`skip_reason IS NULL ⟺ มีออเดอร์เปิดที่
    venue รับแล้ว`) · ที่นี่ตอบครึ่งแรกของ biconditional นั้น ครึ่งหลัง (venue รับ
    หรือไม่) เป็นของชั้นที่ยิงออเดอร์
    """

    close_side: str | None = None
    open_side: str | None = None
    needs_judge: bool = False
    skip_reason: str | None = None

    @property
    def is_flip(self) -> bool:
        """สองขาในแท่งเดียว — เกิดได้เฉพาะบน perp (spec/03 ไม่มี flip บน spot)"""
        return self.close_side is not None and self.open_side is not None

    def __post_init__(self) -> None:
        for field, value in (("close_side", self.close_side), ("open_side", self.open_side)):
            if value is not None and value not in SIDES:
                raise ValueError(f"{field} ต้องเป็น long/short หรือ None ไม่ใช่ {value!r}")
        if self.is_flip and self.close_side == self.open_side:
            raise ValueError(f"flip ต้องกลับข้าง ไม่ใช่ปิดแล้วเปิด {self.open_side!r} ซ้ำ")
        # สองข้อนี้คือ invariant ของใบ 03 เขียนเป็นโค้ดแทนที่จะเป็นข้อตกลง
        if (self.open_side is None) != (self.skip_reason is not None):
            raise ValueError(
                "ต้องมี skip_reason เมื่อไม่มีขาเปิด และต้องไม่มีเมื่อมีขาเปิด — "
                f"ได้ open_side={self.open_side!r} skip_reason={self.skip_reason!r}"
            )
        if self.needs_judge and self.open_side is None:
            raise ValueError("ไม่มีขาเปิดแล้วเรียก Judge ไปทำไม (spec/04:105)")


def decide(
    *,
    long_signal: bool,
    short_signal: bool,
    state: str,
    position_side: str | None,
    market: str,
    allow_short: bool,
) -> BarPlan:
    """ตารางเข้าไม้ของ spec/03:34-42 ทั้งตาราง บวกกฎไม้เรียวและ `allow_short`

    `state` มาจาก `ActionZone.state` (`BULLISH` / `BEARISH` / `UNSET`) ใช้ตัวเดียว
    เพื่อแยก `no_signal` ออกจาก `cane_rule` — ดูหัวไฟล์ว่าเส้นแบ่งอยู่ตรงไหนและ
    ทำไมมันเป็นการตีความ ไม่ใช่ข้อที่สเปกเขียนไว้ตรงๆ

    `position_side` เป็นสถานะที่ **อ่านจากปลายทางจริง** ไม่ใช่ความจำของระบบ
    (spec/08 ขั้น 3) — ที่นี่ไม่รู้และไม่ควรรู้ว่ามันมาจากไหน

    `market` ใช้ตัดสินว่า short เป็นไปได้ไหม · บน `spot` การขายชอร์ตทำไม่ได้จริง
    ไม่ใช่ถูกปิดไว้ด้วย config — `allow_short = True` บน spot จึงเป็นสถานะที่ไม่ควร
    มีอยู่ และดังที่นี่ ไม่ใช่ถูกตีความเป็น false เงียบๆ (spec/07 กันไว้ตั้งแต่โหลด
    config แล้ว ด่านนี้จับบั๊กของชั้นบน)
    """
    if market not in ("usdtm_perp", "spot"):
        raise ValueError(f"market ต้องเป็น usdtm_perp หรือ spot ไม่ใช่ {market!r}")
    if market == "spot" and allow_short:
        raise ValueError(
            "spot ขายชอร์ตไม่ได้ — allow_short บน spot ต้องเป็น false (spec/03, ADR 26)"
        )
    if position_side is not None and position_side not in SIDES:
        raise ValueError(f"position_side ต้องเป็น long/short หรือ None ไม่ใช่ {position_side!r}")
    if long_signal and short_signal:
        # `action_zones()` ให้สองค่านี้จากโซน GREEN กับ RED ซึ่งเกิดพร้อมกันไม่ได้
        # ถ้ามาถึงที่นี่แปลว่าผู้เรียกประกอบข้อมูลผิด ไม่ใช่ตลาดทำอะไรแปลก
        raise ValueError("แท่งเดียวเป็นทั้ง long_signal และ short_signal ไม่ได้")

    if not (long_signal or short_signal):
        return BarPlan(skip_reason=_idle_reason(state))

    wanted = "long" if long_signal else "short"

    # ── ถือฝั่งเดียวกับสัญญาณอยู่แล้ว — ไม่มี pyramiding (spec/03:40-41) ──────
    if position_side == wanted:
        return BarPlan(skip_reason="already_positioned")

    # ── ขาปิด ประเมินก่อนขาเปิดเสมอ (spec/03 "ลำดับความสำคัญ") ────────────────
    close_side = position_side if position_side == OPPOSITE[wanted] else None

    # ── ขาเปิด · short ที่เปิดไม่ได้ยังต้องปิด long ให้แบน (spec/03:50) ────────
    if wanted == "short" and not allow_short:
        return BarPlan(close_side=close_side, skip_reason="short_disabled")

    return BarPlan(close_side=close_side, open_side=wanted, needs_judge=True)


def _idle_reason(state: str) -> str:
    """ไม่มีสัญญาณบนแท่งนี้ — แต่เพราะตลาดเงียบ หรือเพราะเราตกรถ

    `state` ที่ตั้งตัวแล้วแปลว่ามีเทรนด์เดินอยู่ การเข้าตรงนี้คือการไล่ราคา ซึ่งคือ
    สิ่งที่กฎไม้เรียวห้ามไว้ตรงๆ · `UNSET` แปลว่ายังไม่เคยเกิดสัญญาณครบทั้งสองฝั่ง
    ในชุดข้อมูล (ดู `bars_since_*` ที่เป็น `None` ใน `action_zone.py`) จึงยังไม่มี
    เทรนด์ให้ตกรถ
    """
    return "cane_rule" if state in ("BULLISH", "BEARISH") else "no_signal"
