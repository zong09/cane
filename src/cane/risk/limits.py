"""ชั้น risk — สามด่านเรียงกัน ตรวจ**ก่อนยิงทุกออเดอร์** (spec/06, spec/08 ขั้น 12)

ระบบยิงออเดอร์อัตโนมัติโดยไม่มีคนกดยืนยันต่อไม้ (ADR 7) น้ำหนักของความปลอดภัยทั้งหมด
จึงอยู่ที่ไฟล์นี้

## สามชั้น ไม่ใช่ห้า — ใบ 10 รวมสามกลไกที่คนละเรื่องกันไว้ในรายการเดียว

spec/06 แยกไว้ชัด และตาราง `decision_risk_checks` ของใบ 03 บังคับไว้แล้วด้วย CHECK
`layer IN ('kill_switch', 'daily_loss', 'liq_buffer')`:

| ตัว | เมื่อชน | อยู่ที่ไหน |
| --- | --- | --- |
| `max_position_pct_<side>` | **ย่อขนาด** แล้วเดินต่อ | `sizing/matrix.py` (ใบ 07) |
| `max_leverage` | **โหลด config ไม่ผ่าน** | `config/validate.py` (ใบ 03b) |
| kill switch · daily loss · liq buffer | **ปฏิเสธทั้งไม้** ตามลำดับนี้ | ไฟล์นี้ |

สับสนเมื่อไหร่จะได้ระบบที่ข้ามสัญญาณทิ้งทั้งที่ควรแค่ลงไม้เล็กลง (spec/06)

## ชั้นที่ไม่มีในตารางคือหลักฐานว่าลำดับถูกเคารพ

หยุดที่ชั้นแรกที่ไม่ผ่าน **ไม่ตรวจชั้นที่เหลือ** · `decision_risk_checks` จึงมีแถว
เท่าที่เดินไปถึงจริง ซึ่งใบ 03 ตั้งใจให้อ่านย้อนหลังได้ว่าไม้ถูกปฏิเสธที่ด่านไหน
· `seq` เรียง 1..n ไม่มีช่อง และมีได้ไม่เกินหนึ่งแถวที่ `passed = false` ซึ่งต้องเป็น
`seq` สูงสุด — invariant นั้นเป็นจริงเองจากการหยุดที่ชั้นแรกที่ไม่ผ่าน

## ไม้ spot มีสองชั้น ไม่ใช่สามชั้นที่ผ่านฟรี

`spot` ไม่มี liquidation **อยู่จริง** ไม่ใช่มีแล้วคำนวณไม่ได้ (ADR 26) จึง**ไม่เรียก**
ชั้น `liq_buffer` เลยและบันทึกไว้แค่สองแถว · การเรียกแล้วให้ผ่านเสมอจะทำให้รายงาน
"ไม้ที่ผ่านด่าน liquidation" นับไม้ spot รวมเข้าไปด้วย ซึ่งเป็นตัวเลขที่ไม่มีความหมาย

## fail-closed อย่างเคร่งครัดที่ชั้น liquidation

spec/06 · "คำนวณระยะไม่ได้ = ไม่เปิด" · `liquidation_px` ที่เป็น `None` บน perp คือ
กรณีนั้น — ต่างจากบน spot ที่ `None` แปลว่าไม่มีอยู่จริง **ค่าเดียวกันสองความหมาย
แยกด้วย `market` ไม่ใช่ด้วยตัวมันเอง**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Connection

from cane.db.repo.decisions import RiskCheck
from cane.db.repo.killswitch import is_latched
from cane.db.types import PRICE_SCALE

#: ลำดับของด่าน (spec/08 ขั้น 12) · ตรงกับ `ck_decision_risk_checks_layer`
LAYERS = ("kill_switch", "daily_loss", "liq_buffer")

#: ชั้นที่แต่ละตลาด**ไม่เดินผ่านเลย** เพราะสิ่งที่ชั้นนั้นตรวจไม่มีอยู่บนตลาดนั้น
#: (ADR 26) · เขียนเป็นตารางต่อตลาดแทนที่จะเป็น `if market == "spot"` กระจายในโค้ด
#: เพราะตลาดที่สามในอนาคตจะเพิ่มแถว ไม่ใช่เพิ่มสาขา
SKIPPED_LAYERS: dict[str, tuple[str, ...]] = {
    "usdtm_perp": (),
    "spot": ("liq_buffer",),
}


@dataclass(frozen=True, slots=True)
class RiskVerdict:
    """ผลของทั้งชั้น · `checks` คือแถวที่จะลง `decision_risk_checks` ตรงๆ

    `passed` เป็นเท็จเมื่อมีชั้นใดชั้นหนึ่งไม่ผ่าน — และเมื่อนั้น `checks[-1]` คือ
    ชั้นนั้นเสมอ เพราะเราหยุดทันทีที่เจอ
    """

    checks: tuple[RiskCheck, ...]
    passed: bool

    @property
    def blocked_by(self) -> str | None:
        return None if self.passed else self.checks[-1].layer


def check_all(
    conn: Connection,
    *,
    profile: str,
    market: str,
    day_pnl_pct: float | None,
    max_daily_loss_pct: float,
    entry_px: float,
    liquidation_px: float | None,
    min_liq_buffer_pct: float,
) -> RiskVerdict:
    """เดินสามด่านตามลำดับ หยุดที่ด่านแรกที่ไม่ผ่าน (ดูหัวไฟล์)

    `day_pnl_pct` คือกำไรขาดทุนสะสมของวัน **นับ mark-to-market ไม่ใช่แค่ realized**
    และรีเซ็ตเที่ยงคืน UTC (spec/06) · ค่าติดลบคือขาดทุน · **ที่นี่ไม่คำนวณให้** —
    มันต้องการยอดพอร์ตกับสถานะที่เปิดอยู่ทั้งหมด ซึ่งเป็นของชั้นที่อ่านปลายทาง
    · `None` แปลว่า **คำนวณไม่ได้** ซึ่ง fail-closed เหมือนกับที่ชั้น liquidation ทำ

    `liquidation_px` เป็น `None` ได้สองความหมาย: บน `spot` แปลว่าไม่มีอยู่จริง (ชั้นนี้
    ไม่ถูกเรียกเลย) บน `usdtm_perp` แปลว่าคำนวณไม่ได้ → ปฏิเสธ
    """
    checks: list[RiskCheck] = []

    def record(layer: str, passed: bool, **fields) -> RiskVerdict | None:
        # ปัดเฉพาะค่าที่ **บันทึก** — การตัดสินผ่าน/ไม่ผ่านเทียบค่าดิบไปแล้วก่อนถึงตรงนี้ · ตารางเก็บได้แค่
        # `PRICE_SCALE` ตำแหน่งและ `validate_record()` ปฏิเสธค่าที่ละเอียดกว่า (ไม่ปัดให้) ระยะเป็น % ที่หาร
        # แล้วมีทศนิยมยาวเสมอ จึงต้องปัดที่ต้นทางของค่า ไม่ใช่ปล่อยให้ไปพังตอนเขียนบันทึกของแท่งนั้นทั้งแถว
        for name in ("value", "limit_value"):
            if isinstance(fields.get(name), float):
                fields[name] = round(fields[name], PRICE_SCALE)
        checks.append(RiskCheck(seq=len(checks) + 1, layer=layer, passed=passed, **fields))
        return None if passed else RiskVerdict(checks=tuple(checks), passed=False)

    if market not in SKIPPED_LAYERS:
        raise ValueError(f"market ต้องเป็นหนึ่งใน {tuple(SKIPPED_LAYERS)} ไม่ใช่ {market!r}")
    skip = SKIPPED_LAYERS[market]

    latched = is_latched(conn, profile)
    stopped = record("kill_switch", not latched, detail="latched" if latched else None)
    if stopped:
        return stopped

    if day_pnl_pct is None:
        stopped = record("daily_loss", False, limit_value=max_daily_loss_pct,
                         detail="คำนวณกำไรขาดทุนของวันไม่ได้")
    else:
        # `day_pnl_pct` ติดลบคือขาดทุน · เทียบขนาดของการขาดทุนกับเพดานที่เป็นบวก
        loss_pct = -day_pnl_pct
        stopped = record("daily_loss", loss_pct < max_daily_loss_pct,
                         value=loss_pct, limit_value=max_daily_loss_pct)
    if stopped:
        return stopped

    if "liq_buffer" not in skip:
        stopped = _liq_buffer(record, entry_px, liquidation_px, min_liq_buffer_pct)
        if stopped:
            return stopped

    return RiskVerdict(checks=tuple(checks), passed=True)


def _liq_buffer(record, entry_px: float, liquidation_px: float | None, floor_pct: float):
    """ระยะจากราคาเข้าถึงราคา liquidation เป็น % ของราคาเข้า · ต้อง ≥ เพดาน

    ใช้ค่าสัมบูรณ์เพราะไม้ฝั่ง short มีราคา liquidation **เหนือ** ราคาเข้า ระยะจึง
    ติดลบถ้าลบตรงๆ · สิ่งที่ชั้นนี้ถามคือ "ห่างแค่ไหน" ไม่ใช่ "ไปทางไหน"
    """
    if entry_px <= 0:
        raise ValueError(f"entry_px ต้องมากกว่าศูนย์ ไม่ใช่ {entry_px}")
    if liquidation_px is None:
        # fail-closed เคร่งครัด (spec/06) — คำนวณระยะไม่ได้ = ไม่เปิด
        return record("liq_buffer", False, limit_value=floor_pct,
                      detail="ปลายทางไม่ได้คืนราคา liquidation")
    buffer_pct = abs(entry_px - liquidation_px) / entry_px * 100.0
    return record("liq_buffer", buffer_pct >= floor_pct,
                  value=buffer_pct, limit_value=floor_pct)
