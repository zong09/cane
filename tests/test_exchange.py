"""การแปลงชื่อและ defaultType ต่อตลาด — ฟังก์ชันบริสุทธิ์ที่ต้องรันได้โดยไม่ต่อเน็ต

`make_client()` ต่อ Binance จริงไม่ได้บนเครื่องที่ Zscaler คั่น TLS ของ Python อยู่
(curl ผ่าน แต่ Python ไม่ผ่าน) ถ้าตรรกะการเลือกตลาดฝังอยู่ในเส้นทางนั้น มันจะเป็น
ตรรกะที่ไม่มีใครเคยเห็นทำงานเลยจนถึงใบ 12 — ใบ 02b จึงยกมันออกมาเป็นฟังก์ชันบริสุทธิ์
และไฟล์นี้คือที่ที่มันถูกยิงจริง

**ค่าที่ผิดต้องดัง** ไม่ใช่ตกลง perp เงียบ ๆ การเดาตลาดผิดคือการดึงแท่งของอีกตลาดมา
คำนวณ indicator โดยไม่มีอะไรส่งเสียง ซึ่งเป็นบั๊กข้อมูลเดียวกับที่ ADR 26 มาปิด
"""

from __future__ import annotations

import pytest

from cane.data import default_type, make_client, unified_symbol

PERP = "usdtm_perp"
SPOT = "spot"


# ── unified_symbol ──────────────────────────────────────────────────────────


def test_a_perp_symbol_gets_the_quote_suffix():
    """ccxt จับคู่ชื่อตรงตัวก่อน `BTC/USDT` เฉย ๆ จึงได้ตลาด spot มาแม้ตั้ง defaultType"""
    assert unified_symbol("BTC/USDT", PERP) == "BTC/USDT:USDT"


def test_a_spot_symbol_keeps_the_config_spelling():
    """spot ใช้รูปสั้นเป็น unified symbol อยู่แล้ว — ไม่ต้องแปลงอะไร"""
    assert unified_symbol("BTC/USDT", SPOT) == "BTC/USDT"


@pytest.mark.parametrize("market", [PERP, SPOT])
def test_a_symbol_that_is_already_unified_is_refused(market):
    """แปลงซ้ำสองรอบคือบั๊กของผู้เรียก ไม่ใช่ของข้อมูล

    ถ้าปล่อยผ่านเงียบ ๆ market ที่ส่งมาจะไม่มีผลอะไรเลย — `("BTC/USDT:USDT", "spot")`
    จะคืนชื่อ perp ให้ฝั่ง spot ซึ่งคือการดึงแท่งผิดตลาดที่เทสต์ทุกตัวยังเขียว
    """
    with pytest.raises(ValueError, match="spec/07"):
        unified_symbol("BTC/USDT:USDT", market)


def test_a_symbol_without_a_quote_is_refused():
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        unified_symbol("BTCUSDT", PERP)


def test_unified_symbol_refuses_an_unknown_market():
    with pytest.raises(ValueError, match="coinm_perp"):
        unified_symbol("BTC/USDT", "coinm_perp")


# ── default_type ────────────────────────────────────────────────────────────


def test_the_perp_default_type_is_swap_not_future():
    """`future` ของ ccxt คือสัญญาที่มีวันหมดอายุ ซึ่งไม่ใช่ตลาดของระบบนี้ (spec/07)"""
    assert default_type(PERP) == "swap"


def test_the_spot_default_type_is_spot():
    assert default_type(SPOT) == "spot"


def test_default_type_refuses_an_unknown_market():
    with pytest.raises(ValueError, match="futures"):
        default_type("futures")


# ── make_client — ต่อเน็ตไม่ได้ แต่การต่อสายวัดได้ ──────────────────────────


@pytest.mark.parametrize(("market", "expected"), [(PERP, "swap"), (SPOT, "spot")])
def test_make_client_carries_the_default_type_of_its_market(market, expected):
    """สร้าง client แล้วอ่าน option เท่านั้น ไม่เรียก endpoint ใด ๆ — Zscaler ไม่เกี่ยว

    เทสต์ตัวนี้คือหลักฐานว่า `default_type()` ถูก *ต่อสาย* เข้ากับ client จริง
    ไม่ใช่มีอยู่เฉย ๆ ข้าง ๆ ค่าตรึง `"swap"` เดิม
    """
    client = make_client("binance", market)

    assert client.options["defaultType"] == expected


def test_make_client_refuses_an_unknown_exchange():
    with pytest.raises(ValueError, match="ไม่รู้จัก exchange"):
        make_client("ไม่มี venue นี้", PERP)


def test_make_client_refuses_an_unknown_market_before_building_anything():
    with pytest.raises(ValueError, match="market ที่ไม่รู้จัก"):
        make_client("binance", "coinm_perp")
