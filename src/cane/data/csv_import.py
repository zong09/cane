"""อ่านไฟล์ export รายวันของ TradingView เป็น `Bar`

Python ไปหา exchange ผ่าน TLS ไม่ได้บนเครือข่ายของเครื่อง dev (Zscaler ดักกลาง) จึงดึงประวัติด้วย
ccxt ไม่ได้ · ไฟล์ export จากชาร์ตของ TradingView เป็นทางเข้าของประวัติรายวันแทน · การเขียนลงตาราง
`bars` เป็นของคำสั่ง `cane data import-bars` ไม่ใช่ของไฟล์นี้

ตัวกรองแท่งที่ยังไม่ปิด (`closed_as_of()`) ยังทำ **ตอนอ่าน** ตามเดิม ไม่ได้ย้ายมาที่นี่ ·
ฟังก์ชันนี้แค่แปลงไฟล์เป็นแท่งเรียงตามเวลาและยืนยันว่าข้อมูลครบ
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from cane.data.ohlcv import Bar, to_bars

#: คอลัมน์ที่ต้องมีในไฟล์ — `Volume` ขึ้นต้นด้วยตัวพิมพ์ใหญ่ตามที่ TradingView export
_REQUIRED = ("time", "open", "high", "low", "close", "Volume")


def read_tradingview_csv(path: Path, timeframe: str) -> list[Bar]:
    """อ่านไฟล์ export รายวันของ TradingView เป็น `Bar` เรียงตามเวลาเปิด

    ใช้ `csv.reader` ไม่ใช่ `DictReader` เพราะไฟล์จริง **มีชื่อคอลัมน์ซ้ำ** (`Buy/Sell Ribbon`
    โผล่สองครั้ง) และ `DictReader` จะยุบคู่ที่ซ้ำให้เหลือตัวท้ายเงียบๆ · จับคู่ชื่อกับดัชนีเอง
    โดยเอา **ตัวแรก** ที่เจอ

    เวลาในไฟล์เป็น `YYYY-MM-DD` ของแท่ง **เปิด** ตามธรรมเนียมของ TradingView คอลัมน์ที่มีแค่วัน
    จึงบอกเวลาเปิดของแท่งรายวันได้อย่างเดียว — ไฟล์ 1 ชั่วโมงต้องมีเวลาเปิดภายในวัน ซึ่งไฟล์นี้ไม่มี
    จึงปฏิเสธ `timeframe != "1d"`

    ล้มดังก่อนถึงตาราง: วันซ้ำกัน หรือเซลล์ OHLC ว่าง — แท่งที่ขาดหายเงียบๆ ทำให้ indicator
    คำนวณบนชุดที่ไม่ครบโดยไม่มีอะไรบอก
    """
    if timeframe != "1d":
        raise ValueError(
            f"ไฟล์ export ที่มีแค่วันนำเข้าได้เฉพาะ 1d ไม่ใช่ {timeframe!r} — "
            "ไม่มีเวลาเปิดภายในวันให้แยกแท่งย่อย"
        )

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    header, body = rows[0], rows[1:]
    at: dict[str, int] = {}
    for index, name in enumerate(header):
        at.setdefault(name, index)
    missing = [name for name in _REQUIRED if name not in at]
    if missing:
        raise ValueError(f"ไฟล์ {path.name} ขาดคอลัมน์ {', '.join(missing)}")

    out: list[tuple] = []
    seen: set[int] = set()
    for row in body:
        opened = datetime.strptime(row[at["time"]], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        open_ts = int(opened.timestamp() * 1000)
        if open_ts in seen:
            raise ValueError(f"มีสองแถวที่เปิดวันเดียวกัน: {row[at['time']]}")
        seen.add(open_ts)

        values = [
            row[at[name]].strip()
            for name in ("open", "high", "low", "close", "Volume")
        ]
        if not all(values):
            raise ValueError(f"แถว {row[at['time']]} มีเซลล์ OHLCV ว่าง")

        out.append((open_ts, *[float(v) for v in values]))

    out.sort(key=lambda r: r[0])
    return to_bars(out, timeframe)
