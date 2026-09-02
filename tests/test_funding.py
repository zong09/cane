"""funding rate — "ไม่มีข้อมูล" กับ "ศูนย์" ต้องไม่ปนกัน

spec/07: ดึงไม่ได้ = บันทึกว่าไม่มีข้อมูล **ไม่ใช่เดาเป็น 0** ต้นทุนที่หายไปเงียบๆ
ทำให้รายงานสวยกว่าความจริง เทสต์ชุดนี้ยืนกรานทั้งสองทาง: ค่าที่หายต้องไม่กลายเป็น 0
และศูนย์ที่ venue ส่งมาจริงต้องไม่ถูกอ่านว่าหาย
"""

from __future__ import annotations

import ccxt
import pytest

from cane.data import fetch_funding_rate

SYMBOL = "BTC/USDT"

#: รูปร่างจริงที่ ccxt คืนจาก `fapi/v1/premiumIndex`
#: `fundingRate` ← `lastFundingRate`, `fundingTimestamp` ← `nextFundingTime`
RAW = {
    "symbol": "BTC/USDT:USDT",
    "fundingRate": 0.00007542,
    "fundingTimestamp": 1_788_336_000_000,
    "markPrice": 77_099.5,
}


class FakeClient:
    def __init__(self, raw=None, error=None):
        self._raw = raw
        self._error = error
        self.calls: list[str] = []

    def fetch_funding_rate(self, symbol):
        self.calls.append(symbol)
        if self._error is not None:
            raise self._error
        return self._raw

    def fetch_ohlcv(self, *a, **kw):  # pragma: no cover - ไม่ใช้ในไฟล์นี้
        raise AssertionError("ไม่ควรถูกเรียกจากเส้นทาง funding")


def test_reads_the_rate_and_the_cycle_timestamp():
    got = fetch_funding_rate(FakeClient(RAW), SYMBOL)

    assert got.available is True
    assert got.rate == pytest.approx(0.00007542)
    assert got.next_funding_ts == 1_788_336_000_000
    assert got.unavailable_reason is None


def test_asks_for_the_perp_symbol():
    client = FakeClient(RAW)
    fetch_funding_rate(client, SYMBOL)
    assert client.calls == ["BTC/USDT:USDT"]


# ── ไม่มีข้อมูล ≠ ศูนย์ ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error",
    [
        ccxt.NetworkError("เน็ตล่ม"),
        ccxt.ExchangeError("venue ปฏิเสธ"),
        ccxt.NotSupported("venue ไม่รองรับ"),
        ccxt.RequestTimeout("ช้าเกิน"),
    ],
)
def test_a_failed_fetch_is_recorded_as_no_data_never_as_zero(error):
    got = fetch_funding_rate(FakeClient(error=error), SYMBOL)

    assert got.rate is None
    assert got.rate != 0
    assert got.available is False
    assert got.unavailable_reason == type(error).__name__


def test_a_successful_fetch_with_a_missing_rate_is_also_no_data():
    """`safe_number` ของ ccxt คืน None ได้แม้ request สำเร็จ — ยังไม่ใช่ 0 อยู่ดี"""
    raw = dict(RAW, fundingRate=None)

    got = fetch_funding_rate(FakeClient(raw), SYMBOL)

    assert got.rate is None
    assert got.available is False
    assert got.unavailable_reason is not None
    assert got.next_funding_ts == RAW["fundingTimestamp"], "เวลารอบยังบันทึกไว้ได้"


def test_a_real_zero_from_the_venue_stays_a_real_zero():
    """อีกด้านของกฎเดียวกัน — funding เป็น 0 ได้จริง ห้ามอ่านว่า "ไม่มีข้อมูล\""""
    got = fetch_funding_rate(FakeClient(dict(RAW, fundingRate=0.0)), SYMBOL)

    assert got.rate == 0.0
    assert got.available is True
    assert got.unavailable_reason is None


def test_a_bug_in_our_own_code_is_not_disguised_as_no_data():
    """จับเฉพาะ error ของ ccxt — `TypeError` ของเราเองต้องดังออกมา"""
    with pytest.raises(TypeError):
        fetch_funding_rate(FakeClient(error=TypeError("บั๊กของเราเอง")), SYMBOL)
