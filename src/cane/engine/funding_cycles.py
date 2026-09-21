"""รอบ funding ของ replay แบบออฟไลน์ — มีรอบ แต่ไม่มีอัตรา

ระบบไม่มีแหล่ง funding rate ย้อนหลัง P&L ของ replay จึง **ไม่รวม funding** · แต่ละรอบถูกเขียนลง ledger
ตามจริงว่า "ไม่มีอัตราของรอบนี้" (`rate = None`) ไม่ใช่ศูนย์ ซึ่งจะอ้างว่าไม้นั้นไม่เสีย funding
"""

from __future__ import annotations

#: 8 ชั่วโมงเป็นมิลลิวินาที — รอบ funding ของ perp อยู่ที่ 00:00, 08:00, 16:00 UTC
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000


def funding_cycles(symbol: str, after_ts: int, through_ts: int) -> list[tuple[int, None]]:
    """รอบ funding ที่ `after_ts < ts <= through_ts` เรียงจากเก่าไปใหม่

    ขอบล่างไม่รวม ขอบบนรวม — ตรงกับที่ `PaperBroker._charge_funding` เรียกด้วย
    `(settled_through_ts, bar.close_ts)` ทำให้หน้าต่างต่อกันแล้วไม่มีรอบไหนหายหรือถูกนับสองครั้ง ·
    `symbol` รับไว้เพื่อให้ลายเซ็นตรงกับ `FundingSource` เท่านั้น
    """
    if through_ts <= after_ts:
        return []
    # ผลคูณแรกของรอบที่อยู่หลัง `after_ts` อย่างเคร่งครัด
    first = (after_ts // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
    last = (through_ts // FUNDING_INTERVAL_MS) * FUNDING_INTERVAL_MS
    return [(ts, None) for ts in range(first, last + 1, FUNDING_INTERVAL_MS)]
