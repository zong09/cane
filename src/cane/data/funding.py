"""funding rate ของ perp — ต้นทุนจริงของการถือไม้ข้ามรอบ

perp คิด funding ทุก 8 ชม. spec/07 ถือว่านี่เป็นต้นทุน ไม่ใช่ตัวเลขประกอบ

**ดึงไม่ได้ = บันทึกว่าไม่มีข้อมูล ห้ามเดาเป็น 0** ต้นทุนที่หายไปเงียบๆ ทำให้รายงาน
สวยกว่าความจริง และความสวยแบบนั้นเป็นสิ่งที่คนอ่านรายงานไม่มีทางจับได้เอง

**funding ไม่เข้าสูตรขนาดไม้** ([05](../../../docs/spec/05-position-sizing.md))
มันเกิดหลังเปิดไม้แล้ว การเอามาปรับขนาดจะทำให้สัญญาณเดียวกันให้ผลต่างกันตามเวลา
ที่รัน ซึ่งขัดหลักความคงเส้นคงวา — โมดูลนี้จึงไม่มีทางออกไปทางฝั่ง sizing เลย
"""

from __future__ import annotations

from dataclasses import dataclass

from cane.data.exchange import DATA_ERRORS, ExchangeClient, perp_symbol


@dataclass(frozen=True, slots=True)
class FundingRate:
    """`rate is None` คือ **ไม่มีข้อมูล** ไม่ใช่ศูนย์

    แยกสองอย่างนี้ให้ขาดกันในตัวชนิดข้อมูลเอง เพราะถ้าปล่อยให้ค่าที่หายไปกลายเป็น
    `0.0` ตรงไหนก็ตาม รายงานจะรวมต้นทุนได้ผลที่ดูสมเหตุสมผลแต่ต่ำกว่าความจริง
    ซึ่งตรวจจับย้อนหลังไม่ได้เลย

    `unavailable_reason` มีไว้ให้ DecisionRecord เขียนว่า *ทำไม* ไม่มีข้อมูล
    ไม่ใช่แค่ว่าไม่มี — คนอ่านบันทึกย้อนหลังต้องแยกได้ว่าเน็ตล่มหรือ venue ไม่รองรับ
    """

    symbol: str
    rate: float | None
    #: เวลาที่ funding รอบถัดไปจะถูกเก็บ (`nextFundingTime` ของ Binance)
    #: rate ที่ไม่มีเวลาของรอบกำกับ กระทบยอดย้อนหลังไม่ได้ จึงเก็บคู่กันเสมอ
    next_funding_ts: int | None = None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.rate is not None


def fetch_funding_rate(client: ExchangeClient, symbol: str) -> FundingRate:
    """ดึง funding rate ปัจจุบันของ symbol — ไม่เคยยก exception ออกไป

    ความล้มเหลวของ funding **ต้องไม่ล้มรอบการตัดสินใจ** ราคาและ Action Zone ยังใช้ได้
    อยู่ ที่หายไปคือตัวเลขที่คอนโซลแสดงคู่กับ leverage เท่านั้น จึงคืนเป็นสถานะ
    "ไม่มีข้อมูล" แล้วให้บันทึกไว้ ไม่ใช่ดันข้อผิดพลาดขึ้นไปหยุดทั้งรอบ

    จับเฉพาะ error ของ ccxt (`DATA_ERRORS`) ไม่จับ `Exception` เปล่า — บั๊กของเราเอง
    ต้องดังออกมา ไม่ใช่ปลอมตัวเป็น "ดึงข้อมูลไม่ได้"
    """
    try:
        raw = client.fetch_funding_rate(perp_symbol(symbol))
    except DATA_ERRORS as error:
        return FundingRate(
            symbol=symbol,
            rate=None,
            unavailable_reason=type(error).__name__,
        )

    next_ts = raw.get("fundingTimestamp")
    rate = raw.get("fundingRate")
    if rate is None:
        # ดึงสำเร็จแต่ venue ไม่ได้ส่งค่ามา — `safe_number` ของ ccxt คืน None ได้
        # ตามปกติ ไม่ใช่กรณีพิเศษ และก็ยังไม่ใช่ 0 อยู่ดี
        return FundingRate(
            symbol=symbol,
            rate=None,
            next_funding_ts=next_ts,
            unavailable_reason="venue ไม่ส่ง fundingRate มา",
        )

    return FundingRate(symbol=symbol, rate=float(rate), next_funding_ts=next_ts)
