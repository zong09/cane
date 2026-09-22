"""ตัวกรอง lot — ของจริงจาก venue (`CcxtLotSource`) และของที่คนกรอกไว้จำลอง (`StaticLotSource`)

`plan_size` ต้องมี `LotFilter` ถึงจะคำนวณ qty ได้ · live ถาม venue ตรงๆ ส่วน replay ไม่มีปลายทาง
ให้ถาม (และไม่ควรต่อเน็ตอยู่แล้ว — ADR 29) จึงใช้ตารางคงที่ที่กรอกไว้เอง
"""

from __future__ import annotations

from collections.abc import Mapping

from ccxt.base.decimal_to_precision import DECIMAL_PLACES

from cane.data.exchange import unified_symbol
from cane.db.types import store_symbol
from cane.sizing.matrix import LotFilter


class UnknownLot(KeyError):
    """ไม่มีตัวกรอง lot ของ `(market, symbol)` นี้

    ล้มดังโดยเจตนา (fail-closed) — เหรียญที่ไม่รู้จักต้องไม่ถูกเดา lot size เอง เพราะ qty ที่ปัดผิดขั้น
    คือออเดอร์ที่ venue ปฏิเสธ หรือไม้ที่ใหญ่/เล็กกว่าที่สูตรตั้งใจ
    """


# ค่าที่คนกรอกไว้เพื่อ **จำลอง** เท่านั้น ไม่ได้ดึงจาก venue ใดเลย · ตัวจริงคือ `CcxtLotSource` ข้างล่าง
# มีเฉพาะสองเหรียญของ `config/paper.toml` — เพิ่มเหรียญใหม่ต้องเพิ่มที่นี่ด้วย ไม่งั้น replay ล้ม
DEFAULT_LOTS: dict[tuple[str, str], LotFilter] = {
    ("usdtm_perp", "BTC/USDT"): LotFilter(step=0.001, min_qty=0.001, min_notional=100.0),
    ("spot", "ETH/USDT"): LotFilter(step=0.0001, min_qty=0.0001, min_notional=5.0),
}


class StaticLotSource:
    """ค้นตัวกรอง lot จากตารางคงที่ — ของ replay ที่ไม่ต่อเน็ต (live ใช้ `CcxtLotSource`)"""

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


class CcxtLotSource:
    """ตัวกรอง lot **จริงของ venue** — อ่านจาก `markets` ของ ccxt (ใบ 13)

    หนึ่ง client ต่อหนึ่งตลาด (ADR 28) จึงรับเป็น mapping ไม่ใช่ตัวเดียว · ค่าถูกจำไว้
    หลังอ่านครั้งแรกเพราะ `load_markets()` ดึงตารางทั้ง venue มาทีเดียว การเรียกซ้ำทุกแท่ง
    คือการขอของเดิมใหม่ทั้งก้อน

    **เหรียญที่ venue ไม่รู้จัก = `UnknownLot`** ไม่ใช่ค่าที่เดาให้ · เหตุผลเดียวกับ
    `StaticLotSource`: qty ที่ปัดผิดขั้นคือออเดอร์ที่ถูกปฏิเสธ หรือไม้ที่ใหญ่กว่าที่สูตร
    ตั้งใจ (fail-closed, spec/06)
    """

    def __init__(self, clients: Mapping[str, object]) -> None:
        self._clients = dict(clients)
        self._cache: dict[tuple[str, str], LotFilter] = {}

    def lot(self, market: str, symbol: str) -> LotFilter:
        key = (market, store_symbol(symbol))
        if key not in self._cache:
            self._cache[key] = self._read(market, key[1])
        return self._cache[key]

    def _read(self, market: str, symbol: str) -> LotFilter:
        client = self._clients.get(market)
        if client is None:
            raise UnknownLot(
                f"ไม่มี client ของตลาด {market!r} — ตัวกรอง lot มาจาก venue ของตลาดนั้นเท่านั้น"
            )
        usym = unified_symbol(symbol, market)
        markets = client.load_markets()
        spec = markets.get(usym)
        if spec is None:
            raise UnknownLot(f"venue ไม่รู้จัก {usym!r} (market={market!r})")
        limits = spec.get("limits") or {}
        amount, cost = limits.get("amount") or {}, limits.get("cost") or {}
        step = _step_of(client, spec)
        if step is None:
            raise UnknownLot(
                f"venue ไม่บอกขั้นของปริมาณสำหรับ {usym!r} — ปัด qty เองคือการเดา"
            )
        return LotFilter(
            step=step,
            # `min` ที่ไม่มีแปลว่าไม่มีเกณฑ์จริงๆ — ศูนย์คือคำตอบเดียวกันโดยบังเอิญ
            # แต่เป็นค่าที่ venue บอก ไม่ใช่ค่าที่เราคิดแทน
            min_qty=float(amount.get("min") or 0.0),
            min_notional=None if cost.get("min") is None else float(cost["min"]),
        )


def _step_of(client: object, spec: Mapping[str, object]) -> float | None:
    """ขั้นของปริมาณจาก `precision.amount` — ความหมายขึ้นกับ `precisionMode` ของ venue

    ccxt มีสองแบบ: `TICK_SIZE` (ค่าคือขั้นเลย เช่น `0.001`) กับ `DECIMAL_PLACES` (ค่าคือ
    **จำนวนหลัก** เช่น `3` ซึ่งแปลว่าขั้น `0.001`) · อ่านผิดแบบหนึ่งหลักคือ qty ที่ผิดพันเท่า
    จึงไม่เดาจากขนาดของตัวเลข แต่ถาม client ว่ามันนับแบบไหน
    """
    precision = (spec.get("precision") or {}).get("amount")  # type: ignore[union-attr]
    if precision is None:
        return None
    value = float(precision)
    if getattr(client, "precisionMode", None) == DECIMAL_PLACES:
        return 10.0 ** -int(value)
    return value
