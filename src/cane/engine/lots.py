"""ตัวกรอง lot ของ replay แบบออฟไลน์ — ค่าที่คนกรอกเพื่อจำลอง ไม่ใช่ค่าจาก venue

`plan_size` ต้องมี `LotFilter` ถึงจะคำนวณ qty ได้ แต่ paper ไม่มีปลายทางให้ถาม · ตัวกรองจริงของ
venue มากับ broker ของ live (ใบ 13) จนกว่าจะถึงตอนนั้น replay ใช้ตารางคงที่ที่นี่
"""

from __future__ import annotations

from collections.abc import Mapping

from cane.sizing.matrix import LotFilter


class UnknownLot(KeyError):
    """ไม่มีตัวกรอง lot ของ `(market, symbol)` นี้

    ล้มดังโดยเจตนา (fail-closed) — เหรียญที่ไม่รู้จักต้องไม่ถูกเดา lot size เอง เพราะ qty ที่ปัดผิดขั้น
    คือออเดอร์ที่ venue ปฏิเสธ หรือไม้ที่ใหญ่/เล็กกว่าที่สูตรตั้งใจ
    """


# ค่าที่คนกรอกไว้เพื่อ **จำลอง** เท่านั้น ไม่ได้ดึงจาก venue ใดเลย · ตัวจริงมากับ broker ของ live (ใบ 13)
# มีเฉพาะสองเหรียญของ `config/paper.toml` — เพิ่มเหรียญใหม่ต้องเพิ่มที่นี่ด้วย ไม่งั้น replay ล้ม
DEFAULT_LOTS: dict[tuple[str, str], LotFilter] = {
    ("usdtm_perp", "BTC/USDT"): LotFilter(step=0.001, min_qty=0.001, min_notional=100.0),
    ("spot", "ETH/USDT"): LotFilter(step=0.0001, min_qty=0.0001, min_notional=5.0),
}


class StaticLotSource:
    """ค้นตัวกรอง lot จากตารางคงที่ — ตัวแทนชั่วคราวของแหล่งข้อมูลจริงที่จะมากับใบ 13"""

    def __init__(self, table: Mapping[tuple[str, str], LotFilter] | None = None) -> None:
        # คัดลอก — ไม่งั้นผู้เรียกแก้ `DEFAULT_LOTS` ที่ใช้ร่วมกันผ่านเราได้
        self._table: dict[tuple[str, str], LotFilter] = dict(table) if table is not None else dict(DEFAULT_LOTS)

    def lot(self, market: str, symbol: str) -> LotFilter:
        """ตัวกรองของ `(market, symbol)` · ไม่มี = `UnknownLot`"""
        try:
            return self._table[(market, symbol)]
        except KeyError:
            known = ", ".join(f"{m!r}/{s!r}" for m, s in sorted(self._table))
            raise UnknownLot(
                f"ไม่มีตัวกรอง lot ของ market={market!r} symbol={symbol!r} — ที่รู้จักมีแค่ {known}"
            ) from None
