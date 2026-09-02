"""log redaction — ต้องลบความลับ แต่ห้ามลบ epoch timestamp"""

from __future__ import annotations

import logging

import pytest

from cane.log import RedactingFilter, redact


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("api_key=abc123def", "api_key=***"),
        ('{"secret": "s3cr3t"}', '{"secret": "***"}'),
        ("binance_api_secret: hunter2", "binance_api_secret: ***"),
        ("passphrase = 'let me in'", "passphrase = '***'"),
        ("access_token: eyJhbGciOi.J9", "access_token: ***"),
        ("password=p@ss, symbol=BTC/USDT", "password=***, symbol=BTC/USDT"),
    ],
)
def test_masks_secret_values(raw, expected):
    assert redact(raw) == expected


def test_masks_hex_addresses():
    assert redact("wallet 0xAbCd1234EF ok") == "wallet 0x… ok"


def test_keeps_epoch_timestamps_intact():
    """ตั้งใจไม่กรองเลขล้วนยาว — bar_close_ts เป็น epoch 13 หลัก (spec/06)"""
    line = "symbol=BTC/USDT bar_close_ts=1756684800000 qty=0.0123"
    assert redact(line) == line


def test_keeps_ordinary_fields_intact():
    line = "size_pct=65.0 leverage=2.0 side=long leg=open"
    assert redact(line) == line


def test_filter_masks_values_passed_as_args(caplog):
    """ค่าที่ซ่อนใน args ต้องโดนกรองด้วย ไม่ใช่แค่ที่อยู่ใน msg"""
    logger = logging.getLogger("cane.test.redact")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="cane.test.redact"):
        logger.info("connecting api_key=%s to %s", "SUPER-SECRET", "binance")
    logger.filters.clear()

    message = caplog.records[0].getMessage()
    assert "SUPER-SECRET" not in message
    assert message == "connecting api_key=*** to binance"
