"""กล่องผลของแท็บกราฟ (`symbol_chart._verdict`) — สี่แบบตาม handoff §9.2a"""

from __future__ import annotations

import pytest

from cane.api.symbol_chart import _verdict


@pytest.mark.parametrize(
    ("signals", "held", "short_ok", "spot", "kind", "title_has", "body_has"),
    [
        ((True, False), "flat", True, False, "long", "ฝั่ง long", "ไม่มีสถานะ short ค้าง"),
        ((True, False), "short", True, False, "long", "ฝั่ง long", "ปิดก่อนแล้วจึงเปิด long"),
        ((False, True), "long", True, False, "short", "ปิด long แล้วเปิด short", "โหมดทางเดียว"),
        ((False, True), "flat", True, False, "short", "เปิดไม้ที่แท่งถัดไป", "ไม่มีสถานะ long ค้าง"),
        ((False, True), "long", False, False, "short-off", "ปิดอยู่ในโปรไฟล์", "ยังปิด LONG"),
        ((False, True), "long", False, True, "short-off", "spot ไม่มีฝั่ง short", "ยังปิด LONG"),
        ((False, False), "long", True, False, "none", "ไม่ทำอะไร", "กฎไม้เรียว"),
    ],
)
def test_the_verdict_box_follows_the_signal_the_position_and_the_short_switch(
    signals, held, short_ok, spot, kind, title_has, body_has
):
    v = _verdict(*signals, None, held, short_ok, "LONG 25%", spot=spot)

    assert v.kind == kind
    assert title_has in v.title
    assert body_has in v.body
    # ไม่มีสัญญาณ → ไปหน้าบันทึก · มีสัญญาณ → ไปแท็บการตัดสินใจ
    assert v.tab == ("" if kind == "none" else "decision")
