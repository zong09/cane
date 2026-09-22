"""ลูปของ engine หนึ่ง profile — เต้น อ่าน config และเรียกไปป์ไลน์เมื่อมีแท่งใหม่ปิด

## ลูปเป็นแค่ตัวจับจังหวะ ไม่ใช่ตัวตัดสินใจ

ใบ 18 วางตัว process ที่ **มีอยู่จริงและเต้นได้** (เกณฑ์ของ spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 4 และ 7
พิสูจน์ด้วย process ปลอมไม่ได้) ใบ 12 เติมสองอย่าง: อ่าน config ที่ active ทุกต้นรอบ และเรียก `on_bar` เมื่อแท่ง
ใหม่ปิด · **ไปป์ไลน์ 13 ขั้นอยู่ที่ `engine/pipeline.py`** ลูปไม่รู้จักมัน รู้แค่ว่า "ถึงเวลาแล้ว เรียกตัวนี้"

`on_bar` เป็นของที่ฉีดเข้ามา ไม่มีค่าตั้งต้น — ตัวที่ต่อจริงของ live/paper ต้องมี client ของ exchange, broker ต่อ
market และตัวกรอง lot ของ venue ซึ่งมากับ `CcxtBroker` (ใบ 13) จึงยังไม่ถูกต่อจาก `cane engine run` · ที่ต่อแล้วและ
พิสูจน์แล้วคือ replay (`engine/replay.py`) ซึ่งเรียก `run_bar()` ตัวเดียวกัน

## รอบเท่ากับหนึ่งช่วง heartbeat ไม่ใช่หนึ่งแท่ง

ตื่นทุก `HEARTBEAT_PERIOD_S` เพื่ออ่านเจตนากับ config ใหม่ แล้วดูว่ามีแท่งที่ปิดแล้วยังไม่ได้ตัดสินไหม · บน 1d การรอ
ทั้งวันในก้าวเดียวทำให้ปุ่มหยุดดูเหมือนไม่ทำงาน และ config ที่คอนโซลเพิ่งเปิดใช้ไม่ถูกเห็นจนแท่งถัดไป

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

import logging
import time
from collections.abc import Callable

from sqlalchemy import Connection, Engine

from cane.config.settings import Settings
from cane.data.ohlcv import timeframe_ms
from cane.db.repo import config as config_repo
from cane.db.repo import enginestate
from cane.db.types import now_ms
from cane.engine.state import HEARTBEAT_PERIOD_S

log = logging.getLogger(__name__)

#: ตัวที่ต่อไปป์ไลน์เข้าลูป — รับ connection ของทรานแซกชันของแท่งนั้น, config ที่ใช้, และเวลาปิดแท่ง (epoch ms)
BarHook = Callable[[Connection, Settings, int], None]

#: หลังเวลาปิดแท่งกี่มิลลิวินาทีถึงจะเรียก — exchange ต้องใช้เวลาเผยแพร่แท่งที่เพิ่งปิด ถามเร็วเกินได้แท่งเก่ามา
#: ซึ่งทำให้ตัดสินใจบนแท่งเดิมซ้ำ (แถวที่สองของแท่งเดียวกัน)
BAR_GRACE_MS = 10_000

#: ข้อความยาวสุดของเหตุผลที่ลง `engine_state.blocked_reason`
_REASON_LIMIT = 300


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
    blocked_reason: str | None = None,
) -> None:
    """รอจนถึง `until_ms` โดยเต้นทุก `HEARTBEAT_PERIOD_S` · ออกก่อนได้ถ้าถูกสั่งหยุด

    **อ่านเจตนาทุกครั้งที่เต้น ไม่ใช่แค่ธง SIGTERM** — คอนโซลที่รีสตาร์ทแล้วไม่มี pid
    ส่งสัญญาณไม่ได้ เหลือแต่ `should_run` ในตาราง · ถ้าอ่านเฉพาะที่ต้นรอบ การกด stop
    ในกรณีนั้นจะมีผลก็ต่อเมื่อการรอจบลงเอง ซึ่งในใบ 12 คือรอจนแท่งถัดไปปิด

    อ่านในทรานแซกชันเดียวกับการเต้น จึงไม่มี query เพิ่มและไม่มีช่องให้สองค่าคาบเกี่ยวกัน

    `blocked_reason` คือเหตุผลของรอบที่เพิ่งผ่านมา (จาก config หรือจากไปป์ไลน์ล้ม) —
    ต้องส่งต่อมาที่นี่ ไม่งั้นการเต้นระหว่างรอจะเขียนทับด้วย `None` เสมอ ซึ่งทำให้
    เหตุผลหายไปครึ่งหนึ่งของการเต้นทั้งที่ยังไม่มีแท่งไหนสำเร็จ
    """
    while not stopping and now() < until_ms:
        remaining_s = (until_ms - now()) / 1000
        sleep(min(HEARTBEAT_PERIOD_S, remaining_s))
        if stopping:
            return
        with db.begin() as conn:
            if not enginestate.read(conn, profile).should_run:
                return
            enginestate.beat(conn, profile, blocked_reason=blocked_reason)


def _read_config(conn: Connection, profile: str) -> tuple[Settings | None, str | None]:
    """`(config, เหตุผลที่ยังเทรดไม่ได้)` — สองค่าไม่มีทางเป็น `None` พร้อมกัน

    ไม่มี config ที่ active = **ยังไม่เทรด** ไม่ใช่ใช้ค่าตั้งต้น (fail-closed ตาม spec/06) · config ที่เก็บไว้แต่
    อ่านกลับแล้วไม่ผ่านการตรวจ = เหตุผลเดียวกัน · ทั้งสองกรณีลูปวนต่อ ไม่ออก: process ที่ตายแล้วบอกไม่ได้ว่า
    ตายเพราะอะไร แล้วคอนโซลจะแสดงได้แค่ `crashed` ซึ่งชี้ไปผิดที่ (spec/10 §4. รอบชีวิตของ engine)

    ดักเฉพาะ `ValueError` (ซึ่ง `ValidationError` ของ pydantic เป็นลูก) — ข้อผิดพลาดของฐานต้องทะลุขึ้นไป เพราะ
    ทรานแซกชันที่พังแล้วเขียน heartbeat ต่อไม่ได้
    """
    try:
        settings = config_repo.active_settings(conn, profile)
    except ValueError as error:
        return None, f"config ที่ active อ่านกลับแล้วไม่ผ่านการตรวจ: {error}"[:_REASON_LIMIT]
    if settings is None:
        return None, f"profile {profile} ยังไม่มี config ที่ active — สั่ง `cane db seed` หรือเปิดใช้เวอร์ชันจากคอนโซล"
    return settings, None


def due_close(timeframe: str, now_ms_: int, done_close_ts: int) -> int | None:
    """เวลาปิดของแท่งล่าสุดที่ปิดแล้ว **และยังไม่ได้ตัดสิน** — หรือ `None` ถ้ายังไม่ถึงเวลา

    แท่ง 1h/1d ปิดที่ขอบของ epoch พอดี จึงคำนวณด้วยการปัดลง · `done_close_ts = 0` คือยังไม่เคยตัดสินอะไรใน run นี้
    ซึ่งทำให้รอบแรกตัดสินแท่งล่าสุดที่ปิดทันที (และเป็นรอบที่ cold start ถูกประเมิน — spec/08 §cold start)
    """
    step = timeframe_ms(timeframe)
    latest = now_ms_ // step * step
    if latest > done_close_ts and now_ms_ >= latest + BAR_GRACE_MS:
        return latest
    return None


def _run_bar_cycle(db: Engine, on_bar: BarHook, settings: Settings, close_ts: int) -> str | None:
    """เรียกไปป์ไลน์ในทรานแซกชันของแท่งเดียว · ล้ม = คืนเหตุผล ไม่ใช่ยกขึ้นไป

    ล้มแล้ว **ไม่ลองแท่งเดิมซ้ำทุกห้าวินาที** (ผู้เรียกถือว่าแท่งนั้นทำแล้ว) — การยิงคำสั่งซ้ำรัวๆ ตอนที่ยังไม่รู้ว่า
    ครั้งก่อนไปถึงไหนคือความเสี่ยงที่ใหญ่กว่าการข้ามแท่ง · ขั้น 3 ของแท่งถัดไปอ่านสถานะจริงเอง · เหตุผลถูกแสดงที่
    คอนโซลผ่าน `blocked_reason` จนกว่าแท่งถัดไปจะสำเร็จ
    """
    try:
        with db.begin() as conn:
            on_bar(conn, settings, close_ts)
    except Exception as error:  # noqa: BLE001 — ลูปต้องอยู่รอดข้อผิดพลาดของแท่งเดียว แต่ต้องดัง
        log.exception("ไปป์ไลน์ล้มที่แท่งปิด %d", close_ts)
        return f"ไปป์ไลน์ล้มที่แท่ง {close_ts}: {error!r}"[:_REASON_LIMIT]
    return None


def run(
    profile: str,
    *,
    db: Engine,
    stopping: StopFlag,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], int] = now_ms,
    on_bar: BarHook | None = None,
) -> int:
    """วนจนกว่าจะถูกสั่งหยุด · คืน 0 เสมอเมื่อจบอย่างสงบ

    `db` ต้องเป็น Engine ที่สวม role `engine` — ถ้าเผลอส่งตัวที่สวม `console` มา
    process นี้จะปลด kill switch ได้ ซึ่งเป็นสิ่งเดียวที่ spec/06 บอกว่าทำได้เฉพาะคน

    คืน 0 แม้ config ใช้ไม่ได้ เพราะ process ที่ตายเพราะ config ทำให้คอนโซลแสดง
    `crashed` ซึ่งชี้ไปผิดที่ (spec/10 §4. รอบชีวิตของ engine)

    `on_bar=None` = ไม่มีอะไรต่อเข้าลูป: ยังเต้นและอ่าน config เหมือนเดิม แต่ไม่ตัดสินใจอะไร (ดูหัวไฟล์)
    """
    if on_bar is None:
        log.warning("ไม่มีไปป์ไลน์ต่อเข้าลูป (on_bar) — engine นี้เต้นและอ่าน config แต่ไม่ตัดสินใจอะไร")
    done_close = 0  # แท่งล่าสุดที่ตัดสินไปแล้วใน run นี้ · อยู่ในหน่วยความจำ ตั้งใจให้หายพร้อม process
    problem: str | None = None  # เหตุผลที่แท่งก่อนล้ม · แสดงจนกว่าแท่งถัดไปจะสำเร็จ
    while True:
        if stopping:
            return 0

        with db.begin() as conn:
            if not enginestate.read(conn, profile).should_run:
                return 0

            # ── อ่าน config ใหม่ทุกต้นรอบ ไม่ใช่ตอนสตาร์ทครั้งเดียว ──
            settings, blocked_reason = _read_config(conn, profile)

            enginestate.beat(conn, profile, blocked_reason=blocked_reason or problem)

        # ── ถึงเวลาตัดสินแท่งใหม่ไหม — ตัวไปป์ไลน์อยู่ที่ `on_bar` ไม่ใช่ที่นี่ ──
        if settings is not None and on_bar is not None:
            close_ts = due_close(settings.timeframe, now(), done_close)
            if close_ts is not None:
                problem = _run_bar_cycle(db, on_bar, settings, close_ts)
                done_close = close_ts

        _wait_beating(
            profile,
            db=db,
            until_ms=now() + HEARTBEAT_PERIOD_S * 1000,
            stopping=stopping,
            sleep=sleep,
            now=now,
            blocked_reason=blocked_reason or problem,
        )
