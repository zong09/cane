"""ลูปของ engine หนึ่ง profile — โครงขั้นต่ำ ใบ 12 มาเติมไปป์ไลน์ข้างใน

## ที่นี่ยังไม่เทรด และนั่นถูกแล้ว

ใบ 18 ต้องการตัว process ที่ **มีอยู่จริงและเต้นได้** เพราะเกณฑ์ของ
spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 4 (ฆ่า process ทิ้งแล้วสถานะต้องกลายเป็น
`crashed`) กับข้อ 7 (รีสตาร์ท engine แล้ว kill switch ยัง latched) พิสูจน์ด้วย
process ปลอมไม่ได้ · ไปป์ไลน์ 13 ขั้นของ spec/08
ลงตรงจุดที่ทำเครื่องหมายไว้ในลูป ไม่ใช่ลูปตัวที่สอง

## ลำดับในลูปคือตัวออกแบบ ไม่ใช่รายละเอียด

1. **อ่านเจตนาก่อนเต้นครั้งแรก** — engine ที่ถูกสั่งรันด้วยมือโดยไม่มีใครกด start
   ต้องออกไปโดยไม่เคยทำท่าว่ามีชีวิต ไม่งั้นคอนโซลจะเห็น `stopping` ของ process ที่
   ไม่มีใครเรียกมา
2. **ตรวจธง SIGTERM ที่ต้นรอบเท่านั้น** — spec/10 §4. รอบชีวิตของ engine บอกว่าตอนหยุด
   ให้ "จบรอบที่กำลังทำอยู่ก่อนแล้วปิดตัว ไม่ตัดกลางการส่งออเดอร์" · ธงถูกตั้งเมื่อไหร่
   ก็ได้ แต่ถูกอ่านที่เดียว
3. **config ที่ใช้ไม่ได้ไม่ทำให้ลูปจบ** — เขียน `blocked_reason` แล้ววนต่อ (§4 เขียนว่า
   **ไม่ exit** เพราะ process ที่ตายแล้วบอกไม่ได้ว่าตายเพราะอะไร แล้วคอนโซลจะแสดงได้
   แค่ `crashed` ซึ่งชี้ไปผิดที่)

## การรอแท่งปิดถูกแบ่งซอยตั้งแต่ตอนนี้

`_wait_beating()` แบ่งการรอเป็นช่วงละ `HEARTBEAT_PERIOD_S` แล้วเต้นหนึ่งครั้งต่อช่วง ·
ในใบ 18 กำหนดเส้นตายไว้แค่หนึ่งช่วงจึงแทบไม่ทำอะไร แต่เขียนไว้ตอนนี้เพราะใบ 12 จะส่ง
เวลาปิดแท่งจริงเข้ามา และตอนนั้น "รอแท่งถัดไป" กับ "ทำให้ heartbeat ไม่เก่า" คือการรอ
อันเดียวกัน — ถ้าไม่เขียนรวมไว้ตั้งแต่แรก มันจะกลายเป็นสองอันที่ลืมซิงก์กัน

## ไม่ติดตั้ง signal handler ที่นี่

`run()` รับ `StopFlag` ที่ถูกตั้งไว้แล้วเข้ามา ส่วนการผูกเข้ากับ `SIGTERM` เป็นของ
`cli.py` · แยกไว้เพราะ handler เป็นของทั้ง process ติดตั้งจากในฟังก์ชันที่ถูกเทสต์เรียก
จะไปทับของ pytest เอง — และเพราะ `sleep` ที่ inject ได้ทำให้ทดสอบทั้งลูปในกระบวนการ
เดียวกันได้โดยไม่ต้องมีพารามิเตอร์ `max_cycles` ที่มีไว้ให้เทสต์อย่างเดียว
"""

from __future__ import annotations

import time
from collections.abc import Callable

from sqlalchemy import Engine

from cane.db.repo import enginestate
from cane.db.types import now_ms
from cane.engine.state import HEARTBEAT_PERIOD_S


class StopFlag:
    """ธงที่ handler ตั้ง แล้วลูปอ่านที่ต้นรอบ · ไม่ใช่การออกทันที"""

    __slots__ = ("_requested",)

    def __init__(self) -> None:
        self._requested = False

    def request_stop(self, *_: object) -> None:
        """รับ `(signum, frame)` ของ `signal.signal` ได้ และรับการเรียกเปล่าๆ ก็ได้"""
        self._requested = True

    def __bool__(self) -> bool:
        return self._requested


def _wait_beating(
    profile: str,
    *,
    db: Engine,
    until_ms: int,
    stopping: StopFlag,
    sleep: Callable[[float], None],
    now: Callable[[], int],
) -> None:
    """รอจนถึง `until_ms` โดยเต้นทุก `HEARTBEAT_PERIOD_S` · ออกก่อนได้ถ้าถูกสั่งหยุด"""
    while not stopping and now() < until_ms:
        remaining_s = (until_ms - now()) / 1000
        sleep(min(HEARTBEAT_PERIOD_S, remaining_s))
        if stopping:
            return
        with db.begin() as conn:
            enginestate.beat(conn, profile)


def run(
    profile: str,
    *,
    db: Engine,
    stopping: StopFlag,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], int] = now_ms,
) -> int:
    """วนจนกว่าจะถูกสั่งหยุด · คืน 0 เสมอเมื่อจบอย่างสงบ

    `db` ต้องเป็น Engine ที่สวม role `engine` — ถ้าเผลอส่งตัวที่สวม `console` มา
    process นี้จะปลด kill switch ได้ ซึ่งเป็นสิ่งเดียวที่ spec/06 บอกว่าทำได้เฉพาะคน

    คืน 0 แม้ config ใช้ไม่ได้ เพราะ process ที่ตายเพราะ config ทำให้คอนโซลแสดง
    `crashed` ซึ่งชี้ไปผิดที่ (spec/10 §4. รอบชีวิตของ engine)
    """
    while True:
        if stopping:
            return 0

        with db.begin() as conn:
            if not enginestate.read(conn, profile).should_run:
                return 0

            # ── อ่าน config ใหม่ทุกต้นรอบ ไม่ใช่ตอนสตาร์ทครั้งเดียว ──
            # ใบ 12 เป็นคนต่อสายกับ `db.repo.config.active_settings()` · ที่นี่วางช่อง
            # ไว้ให้เห็นว่ามันอยู่ต้นรอบ ไม่ใช่นอกลูป
            blocked_reason: str | None = None

            enginestate.beat(conn, profile, blocked_reason=blocked_reason)

        # ── ใบ 12 วางไปป์ไลน์ 13 ขั้นของ spec/08 ตรงนี้ ──

        _wait_beating(
            profile,
            db=db,
            until_ms=now() + HEARTBEAT_PERIOD_S * 1000,
            stopping=stopping,
            sleep=sleep,
            now=now,
        )
