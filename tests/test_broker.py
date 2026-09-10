"""สัญญาของ Broker — สิ่งที่ต้องดังก่อนออเดอร์หลุดไปถึงปลายทาง

ไฟล์นี้ไม่แตะฐานข้อมูลและไม่แตะเน็ต ทุกอย่างที่ตรวจอยู่ในชั้นค่าล้วน — นั่นเป็น
เจตนา ชั้นนี้คือด่านสุดท้ายที่ยังปฏิเสธออเดอร์ผิดรูปได้ฟรี หลังจากนี้ราคาของ
ความผิดคือคำสั่งจริงที่ venue รับไปแล้ว
"""

from __future__ import annotations

import string
from decimal import Decimal

import pytest

from cane.execution import (
    Balance,
    OpenOrder,
    Order,
    OrderResult,
    Position,
    check_market_supports,
    client_order_id,
)

BAR = 1_787_961_600_000


def test_both_legs_of_a_flip_differ_only_by_leg():
    """เหตุผลทั้งหมดที่ `leg` ต้องมีอยู่ในกุญแจ (spec/06)

    flip จาก short เป็น long คือ **buy สองครั้ง** ในแท่งเดียว — ปิด short แล้วเปิด
    long · ถ้ากุญแจไม่มี `leg` สองขานี้จะได้ id ชนกันเอง แล้วขาที่สองจะถูก venue
    ปฏิเสธในฐานะออเดอร์ซ้ำ ทั้งที่มันเป็นออเดอร์คนละใบ

    เทสต์นี้จึงเป็นหลักฐานของมติที่ว่า `side` = ฝั่งของ**ออเดอร์** ไม่ใช่ฝั่งของไม้
    ถ้าเป็นฝั่งไม้ สองขาจะต่างกันตั้งแต่ `side` แล้วและ `leg` จะไม่จำเป็น
    """
    close_leg = client_order_id("BTC/USDT", BAR, "buy", "close")
    open_leg = client_order_id("BTC/USDT", BAR, "buy", "open")

    assert close_leg != open_leg
    assert close_leg == "cane-BTC/USDT-1787961600000-buy-close"
    assert open_leg == "cane-BTC/USDT-1787961600000-buy-open"


def test_the_id_is_the_same_whichever_form_of_the_symbol_it_is_given():
    """`BTC/USDT:USDT` กับ `BTC/USDT` ต้องให้ id เดียวกัน

    ชั้นข้อมูลแปลงเป็น unified symbol ก่อนคุย ccxt ส่วน config กับตารางใช้รูปสั้น
    ถ้า id เปลี่ยนตามรูปที่ผู้เรียกบังเอิญถืออยู่ ระบบจะสร้าง id คนละตัวสำหรับแท่ง
    เดียวกัน ซึ่งทำลายสิ่งเดียวที่ id นี้มีหน้าที่ป้องกัน
    """
    assert client_order_id("BTC/USDT:USDT", BAR, "sell", "stop") == client_order_id(
        "BTC/USDT", BAR, "sell", "stop"
    )


def test_the_id_refuses_a_position_side_where_an_order_side_belongs():
    """`long` เป็นค่าที่ถูกของ `side_t` แต่ผิดของ `order_side_t`

    mock ใน design handoff พิมพ์ `-long` ไว้ ถ้าปล่อยผ่าน ระบบจะสร้าง id ที่หน้าตา
    ถูกต้องแต่ชนกันเองที่ขา flip — ต้องดังตรงนี้ ไม่ใช่ไปโป๊ะตอน venue ปฏิเสธ
    """
    with pytest.raises(ValueError, match="order_side"):
        client_order_id("BTC/USDT", BAR, "long", "open")

    with pytest.raises(ValueError, match="leg"):
        client_order_id("BTC/USDT", BAR, "buy", "entry")


def test_a_stop_order_without_a_price_does_not_exist():
    with pytest.raises(ValueError, match="stop_px"):
        Order(
            symbol="BTC/USDT",
            side="sell",
            type="stop_market",
            qty=1.0,
            client_order_id=client_order_id("BTC/USDT", BAR, "sell", "stop"),
        )


def test_a_market_order_carrying_a_stop_price_does_not_exist_either():
    """ค่าที่ไม่มีใครอ่านคือค่าที่คนเขียนโค้ดเชื่อว่ามีผล — ต้องปฏิเสธ ไม่ใช่เมิน"""
    with pytest.raises(ValueError, match="stop_px"):
        Order(
            symbol="BTC/USDT",
            side="buy",
            type="market",
            qty=1.0,
            client_order_id=client_order_id("BTC/USDT", BAR, "buy", "open"),
            stop_px=100.0,
        )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"side": "long"}, "side"),
        ({"type": "limit"}, "type"),
        ({"qty": 0.0}, "qty"),
        ({"qty": -1.0}, "qty"),
    ],
)
def test_an_order_outside_the_vocabulary_of_the_schema_is_refused(kwargs, message):
    base = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "type": "market",
        "qty": 1.0,
        "client_order_id": client_order_id("BTC/USDT", BAR, "buy", "open"),
    }

    with pytest.raises(ValueError, match=message):
        Order(**{**base, **kwargs})


def test_a_fill_cannot_both_know_and_not_know_its_fee():
    """คู่นี้เป็น CHECK ของ `fills` — ปฏิเสธที่ชั้นค่าด้วย จะได้ไม่ต้องรอถึงตอน insert"""
    with pytest.raises(ValueError, match="fee"):
        OrderResult(
            client_order_id="cane-BTC/USDT-1-buy-open",
            venue_order_id="1",
            status="closed",
            filled_qty=1.0,
            fee_quote=Decimal("0.05"),
            fee_unavailable_reason="venue ไม่คืนค่าธรรมเนียม",
        )


def test_a_fill_that_does_not_know_its_fee_is_allowed_to_say_so():
    """"ยังไม่รู้" ต่างจาก "ไม่มี" — ศูนย์จะทำให้รายงานอ้างว่าไม้นั้นไม่มีค่าธรรมเนียม"""
    result = OrderResult(
        client_order_id="cane-BTC/USDT-1-buy-open",
        venue_order_id="1",
        status="closed",
        filled_qty=1.0,
        fee_unavailable_reason="venue ไม่คืนค่าธรรมเนียม",
    )

    assert result.fee_quote is None


def test_a_position_carries_a_position_side_not_an_order_side():
    with pytest.raises(ValueError, match="long หรือ short"):
        Position(
            symbol="BTC/USDT",
            side="buy",
            qty=1.0,
            entry_px=100.0,
            mark_px=101.0,
            unrealized_pnl=Decimal("1"),
            leverage=2.0,
        )


def test_spot_refuses_reduce_only_instead_of_ignoring_it():
    """spot ไม่มีธงนี้ ปล่อยผ่านแล้วขาปิดจะดูเหมือนถูกกันไว้ทั้งที่ไม่มีอะไรกัน"""
    order = Order(
        symbol="BTC/USDT",
        side="sell",
        type="market",
        qty=1.0,
        client_order_id=client_order_id("BTC/USDT", BAR, "sell", "close"),
        reduce_only=True,
    )

    check_market_supports(order, "usdtm_perp")

    with pytest.raises(ValueError, match="reduce_only"):
        check_market_supports(order, "spot")


def test_an_unknown_market_is_refused_before_anything_else():
    order = Order(
        symbol="BTC/USDT",
        side="buy",
        type="market",
        qty=1.0,
        client_order_id=client_order_id("BTC/USDT", BAR, "buy", "open"),
    )

    with pytest.raises(ValueError, match="market"):
        check_market_supports(order, "margin")


def test_an_open_order_and_a_balance_hold_the_shapes_reconcile_reads():
    """โครงสองตัวนี้ไม่มีกฎในตัวเอง — เทสต์นี้ตรึงรูปไว้ให้ใบ 13 กับใบ 12 อ้างได้"""
    stop = OpenOrder(
        venue_order_id="9",
        client_order_id=client_order_id("BTC/USDT", BAR, "sell", "stop"),
        symbol="BTC/USDT",
        side="sell",
        type="stop_market",
        qty=1.0,
        stop_px=95.0,
        reduce_only=True,
    )
    wallet = Balance(quote="USDT", free=Decimal("10000"), used=Decimal("0"),
                     total=Decimal("10000"))

    assert stop.stop_px == 95.0
    assert wallet.total == wallet.free + wallet.used


def test_the_id_length_is_pinned_so_ticket_13_can_check_it_against_the_venue():
    """รูปที่ spec/06 กำหนดให้ id ยาว 38 ตัวอักษรสำหรับเหรียญชื่อสั้นที่สุดที่เราใช้

    **ข้อนี้ยังไม่ได้ตรวจกับ venue จริง** — `clientOrderId` ของ exchange มีเพดาน
    ความยาวและมีชุดอักขระที่รับได้ ซึ่งยืนยันจากเครื่องนี้ไม่ได้เพราะต่อเน็ตออกไป
    ไม่ได้ (`make_client()` ยังไม่เคยรันจริง) · ใบ 13 เป็นใบที่คุยกับ venue จริง
    จึงเป็นที่ที่ต้องเทียบ ถ้าเกินเพดานขึ้นมา สิ่งที่ต้องแก้คือ**รูปใน spec/06**
    ไม่ใช่แอบตัดให้สั้นลงที่ชั้นนี้ เพราะ id ที่ตัดแล้วอาจชนกันเองซึ่งลบเหตุผล
    ทั้งหมดที่มันมีอยู่

    เทสต์นี้ตรึงตัวเลขไว้ให้มีของให้เทียบ ไม่ใช่ประกาศว่าตัวเลขนี้ผ่าน
    """
    shortest = client_order_id("BTC/USDT", BAR, "sell", "close")
    longer = client_order_id("MATIC/USDT", BAR, "sell", "close")

    assert len(shortest) == 38
    assert len(longer) == 40
    # อักขระที่ไม่ใช่ตัวอักษร ตัวเลข หรือ `-` มีตัวเดียวคือ `/` ของรูป symbol
    alnum = set(string.ascii_letters + string.digits + "-")
    assert set(shortest) - alnum == {"/"}
    assert set(longer) - alnum == {"/"}
