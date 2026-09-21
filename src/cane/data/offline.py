"""ตัวแทน exchange ที่ไม่แตะเครือข่ายเลย — สำหรับ replay ย้อนหลัง

ใช้คู่กับ `ReplayBarSource` เพื่อให้ `_load()` คืนเฉพาะแท่งที่เก็บอยู่ในตาราง `bars` แล้ว ไม่ดึง
อะไรเพิ่ม เพราะ replay ต้องเดินบนประวัติที่นำเข้าไว้ก่อนแล้วเท่านั้น (ADR 29)
"""

from __future__ import annotations

from typing import Any


class OfflineClient:
    """`ExchangeClient` ที่ `fetch_ohlcv` คืน `[]` เสมอ

    `fetch_ohlcv` ว่างเสมอ → `_load()` ไม่ได้แท่งใหม่ จึงคืนแค่แท่งในตาราง · `fetch_funding_rate` ล้มดัง
    เพราะ replay ไม่มีแหล่ง funding — P&L ของ paper ที่ได้จึงไม่รวม funding และต้องไม่มีใครเดาอัตรามาเติม
    """

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int | None = None,
        limit: int | None = None,
    ) -> list[list[float]]:
        return []

    def fetch_funding_rate(self, symbol: str) -> dict[str, Any]:
        raise RuntimeError(
            "replay แบบออฟไลน์ไม่มีแหล่ง funding — P&L ของ paper ไม่รวม funding "
            "และห้ามเดาอัตราเอง"
        )
