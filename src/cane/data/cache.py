"""cache แท่งที่ปิดแล้วลงดิสก์

ปลอดภัยเพราะ **แท่งที่ปิดแล้วไม่เปลี่ยนอีก** (spec/07) ข้อมูลที่เปลี่ยนได้ห้ามลงที่นี่
โดยเฉพาะแท่งที่ยังวิ่งอยู่ — ผู้เรียกเป็นคนกรองก่อนส่งมา (`_load` ใน ohlcv.py)
และไฟล์นี้กรองซ้ำอีกชั้นไม่ได้เพราะไม่รู้เวลานาฬิกาของผู้เรียก

funding rate **ไม่ cache** ที่นี่ มันคือค่า ณ ปัจจุบันที่เปลี่ยนทุก 8 ชม.
ไม่ใช่ข้อเท็จจริงที่ตายแล้วเหมือนแท่งที่ปิด
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from cane.data.ohlcv import Bar, to_bars


class BarCache:
    """หนึ่งไฟล์ JSON ต่อ (symbol, timeframe) เก็บเป็น **แถวดิบแบบ ccxt**

    เก็บแถวดิบ ไม่ใช่ `Bar` ที่ serialize แล้ว เพื่อให้ `close_ts` มาจาก `to_bars()`
    ทางเดียวเสมอ ถ้าเก็บ `close_ts` ลงไฟล์ด้วย ไฟล์เก่าที่เขียนไว้ตอน timeframe
    ต่างกันจะย้อนกลับมาขัดกับค่าที่คำนวณสด
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    def _path(self, symbol: str, timeframe: str) -> Path:
        # `BTC/USDT` มี `/` ซึ่งเป็นตัวคั่นพาธ ต้องแปลงก่อนไม่ให้กลายเป็นไดเรกทอรี
        safe = symbol.replace("/", "-").replace(":", "_")
        return self._root / f"{safe}__{timeframe}.json"

    def load(self, symbol: str, timeframe: str) -> list[Bar]:
        """คืนแท่งจาก cache เรียงเก่า → ใหม่ · ไม่มีไฟล์หรือไฟล์เสีย = ไม่มีข้อมูล

        ไฟล์เสียถือว่าว่าง ไม่ใช่ยก exception — cache เป็นของที่ทิ้งแล้วสร้างใหม่ได้
        การล้มทั้งระบบเพราะไฟล์ชั่วคราวพังเป็นการแลกที่ไม่คุ้ม ตัวข้อมูลจริงยังดึงใหม่ได้
        """
        path = self._path(symbol, timeframe)
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(rows, list):
            return []
        try:
            return to_bars(rows, timeframe)
        except (TypeError, IndexError):
            # JSON ถูกต้องแต่รูปร่างไม่ใช่แถวราคา เช่น `[1,2,3]` — ยังถือว่า cache เสีย
            return []

    def save(self, symbol: str, timeframe: str, bars: Sequence[Bar]) -> None:
        """เขียนทับทั้งไฟล์แบบ atomic

        เขียนลงไฟล์ชั่วคราวแล้ว `os.replace` เพราะถ้าโปรเซสตายกลางการเขียน ไฟล์ที่
        เหลือครึ่งเดียวจะกลายเป็นประวัติราคาที่ขาดตรงกลางโดยไม่มีใครรู้ — `os.replace`
        เป็น atomic บนระบบไฟล์เดียวกัน จึงได้ทั้งของเก่าครบหรือของใหม่ครบเท่านั้น
        """
        path = self._path(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [[b.open_ts, b.open, b.high, b.low, b.close, b.volume] for b in bars]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
