"""การสังเกต funding ลงตาราง `funding_observations` — ทั้งตอนได้ค่าและตอนดึงไม่ได้

`test_funding.py` ยิงกฎ "ไม่มีข้อมูล ≠ ศูนย์" ที่ตัวฟังก์ชันโดยไม่ต้องมี DB · ไฟล์นี้
ยิงเส้นทางเขียน: ช่องว่างในประวัติต้องเป็น **แถวที่บอกว่าดึงไม่ได้** ไม่ใช่ความเงียบ
ที่ดูเหมือนช่วงที่ไม่มีต้นทุน
"""

from __future__ import annotations

import ccxt
import pytest

from cane.data import observe_funding_rate
from cane.db import latest_observation
from test_funding import RAW, SYMBOL, FakeClient

pytestmark = pytest.mark.db

PERP = "usdtm_perp"


def test_an_observation_reaches_the_table(db):
    got = observe_funding_rate(db, FakeClient(RAW), PERP, SYMBOL)

    from_table = latest_observation(db, SYMBOL)
    assert from_table is not None
    assert from_table.rate == pytest.approx(got.rate)
    assert from_table.available is True


def test_a_failed_fetch_is_recorded_as_missing_not_skipped(db):
    """ดึงไม่ได้ก็ต้องมีแถว — ไม่มีแถวเลยคือช่องว่างที่อ่านได้ว่า "ไม่มีต้นทุนช่วงนั้น\""""
    client = FakeClient(error=ccxt.NetworkError("เน็ตล่ม"))

    observe_funding_rate(db, client, PERP, SYMBOL)

    from_table = latest_observation(db, SYMBOL)
    assert from_table is not None
    assert from_table.rate is None
    assert from_table.available is False
    assert from_table.unavailable_reason == "NetworkError"


def test_a_spot_symbol_writes_nothing(db):
    """ปฏิเสธก่อนถึงบรรทัดเขียน — ตารางนี้เป็นของ perp เท่านั้น (schema.py:101)"""
    client = FakeClient(RAW)
    before = latest_observation(db, SYMBOL)

    with pytest.raises(ValueError, match="usdtm_perp"):
        observe_funding_rate(db, client, "spot", SYMBOL)

    assert client.calls == []
    assert latest_observation(db, SYMBOL) == before


def test_observing_never_commits(db, db_engine):
    """ผู้เรียกเป็นเจ้าของทรานแซกชัน — commit ที่หลงอยู่จะทำให้ข้อมูลเทสต์อยู่ยืนกว่าเทสต์"""
    with db_engine.connect() as outsider:
        before = latest_observation(outsider, SYMBOL)

    observe_funding_rate(db, FakeClient(RAW), PERP, SYMBOL)
    assert latest_observation(db, SYMBOL) is not None

    with db_engine.connect() as outsider:
        assert latest_observation(outsider, SYMBOL) == before
