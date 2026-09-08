"""OHLCV จาก ccxt — คืนเฉพาะแท่งที่ปิดแล้วเท่านั้น

สีของ Action Zone เปลี่ยนไปมาได้จนกว่าแท่งจะปิด (repainting) การตัดสินใจบนแท่งที่ยัง
วิ่งอยู่จึงเห็น "เขียวแรก" ที่หายไปตอนบ่าย — decisions #10 ปิดประตูนี้ไว้ทั้งบาน

**`as_of` คือแกนเดียวของไฟล์นี้** การตัดแท่งที่ยังไม่ปิด (live) กับการเดินย้อนเวลา
(replay, #12) เป็น *การกระทำเดียวกัน* ต่างกันแค่ค่า `as_of`: live ใช้เวลานาฬิกา
replay ใช้ตำแหน่งของ cursor ทั้งสองเส้นทางลงมาที่ `closed_as_of()` ตัวเดียว
#12 จึงรันบนโค้ดเส้นเดียวกัน ไม่ใช่ runner ตัวที่สองที่ต้องดูแลขนานกันไป
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from cane.data.exchange import ExchangeClient, unified_symbol

if TYPE_CHECKING:
    from sqlalchemy import Connection

#: timeframe ที่ระบบใช้จริง — รายวันเป็นหลัก ราย 1 ชม. สำหรับ cold start ทางที่ 1 (spec/07)
#: ตั้งใจไม่เขียน parser ทั่วไป ค่าที่ไม่รู้จักต้องดังตั้งแต่ตอนเรียก ไม่ใช่ตอนคำนวณผิด
TIMEFRAME_MS: dict[str, int] = {
    "1h": 3_600_000,
    "1d": 86_400_000,
}

#: ขนาดหน้าที่ขอต่อครั้ง — เท่ากับค่าตั้งต้นของ Binance futures
#: ระบุชัดเจนแทนการปล่อย `None` เพราะการรู้ว่า "หน้าเต็มแล้ว" คือเงื่อนไขที่ใช้ตัดสิน
#: ว่าต้องขอหน้าถัดไปไหม ถ้าไม่รู้ขนาดหน้า ก็ไม่รู้ว่าข้อมูลขาดหรือหมดพอดี
DEFAULT_LIMIT = 500

#: คู่ที่มีแท่งปิดน้อยกว่านี้ **ยังไม่ตัดสินใจ** — คอนโซลแสดง `คู่ใหม่ · รอครบ 85 แท่ง`
#: อย่าสับสนกับ warm-up 130 แท่ง (5×xprd2) ที่ตัดทิ้งตอนเทียบ golden test ใน #04
#: คนละเรื่องกัน: 85 คือ "ข้อมูลพอจะตัดสินใจไหม" 130 คือ "ค่า EMA นิ่งแล้วหรือยัง"
MIN_CLOSED_BARS = 85


@dataclass(frozen=True, slots=True)
class Bar:
    """แท่งเดียว เวลาเป็น epoch มิลลิวินาที

    `close_ts` คือ **ขณะที่แท่งปิด** = `open_ts + timeframe` ไม่ใช่ `closeTime` ของ
    Binance ที่เป็น `open_ts + timeframe - 1` (ปลายช่วงแบบรวมปลาย) เก็บไว้บนแท่งเลย
    เพื่อให้แท่งอธิบายตัวเองได้ ผู้ใช้ปลายทางไม่ต้องถือ timeframe ไปด้วยเพื่อคำนวณซ้ำ

    ชื่อ `close_ts` ตรงกับ `bar_close_ts` ที่ DecisionRecord (#03) จะเขียน (spec/06)
    """

    open_ts: int
    close_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def timeframe_ms(timeframe: str) -> int:
    if timeframe not in TIMEFRAME_MS:
        known = ", ".join(sorted(TIMEFRAME_MS))
        raise ValueError(f"timeframe {timeframe!r} ไม่รองรับ — มีแต่ {known}")
    return TIMEFRAME_MS[timeframe]


def to_bars(rows: Iterable[Sequence[float]], timeframe: str) -> list[Bar]:
    """แปลงแถวดิบของ ccxt `[open_ts, o, h, l, c, v]` เป็น `Bar`

    cache เก็บของในรูปแถวดิบเหมือนกัน แท่งจาก cache กับแท่งจากเน็ตจึงเกิดจาก
    โค้ดบรรทัดเดียวกัน ไม่มีทางแตกกันเพราะแปลงคนละที่
    """
    span = timeframe_ms(timeframe)
    bars = []
    for row in rows:
        open_ts = int(row[0])
        bars.append(
            Bar(
                open_ts=open_ts,
                close_ts=open_ts + span,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        )
    return bars


def merge_bars(*groups: Iterable[Bar]) -> list[Bar]:
    """รวมหลายชุดเป็นชุดเดียว เรียงตามเวลา ซ้ำกันเอาตัวหลัง

    ซ้ำได้เป็นปกติเพราะการดึงต่อจาก cache ตั้ง `since` ที่แท่งสุดท้ายที่มีอยู่ ไม่ใช่
    แท่งถัดไป — `startTime` ของ Binance รวมปลายซ้าย การดึงทับหนึ่งแท่งจึงเป็น
    การจงใจเพื่อเลี่ยงปัญหา off-by-one ที่ต้องเดาว่า endpoint รวมปลายหรือไม่
    """
    by_open: dict[int, Bar] = {}
    for group in groups:
        for bar in group:
            by_open[bar.open_ts] = bar
    return [by_open[k] for k in sorted(by_open)]


def closed_as_of(bars: Iterable[Bar], as_of: int) -> list[Bar]:
    """แท่งที่ปิดแล้ว ณ เวลา `as_of` — **ประตูเดียว** ที่ทั้ง live และ replay ผ่าน

    เทียบแบบ `<` ไม่ใช่ `<=` โดยเจตนา: ข้อรับประกันของชั้นนี้คือ "แท่งล่าสุดที่คืนมา
    มี close_ts < as_of เสมอ" ถ้าใช้ `<=` แท่งที่ปิดตรงวินาทีนั้นพอดีจะหลุดออกไป
    พร้อมค่าที่เพิ่งนิ่ง ทำให้ข้อรับประกันเป็นเท็จที่ขอบเขตพอดี — ทางที่ปลอดภัยกว่า
    คือช้าไปหนึ่งแท่ง ไม่ใช่เร็วไปหนึ่งแท่ง
    """
    return [bar for bar in bars if bar.close_ts < as_of]


def bars_needed(bars: Sequence[Bar]) -> int:
    """ยังขาดอีกกี่แท่งจึงจะตัดสินใจได้ — `0` คือพร้อม

    คืนจำนวน ไม่ใช่ bool เพราะคอนโซลต้องบอกให้ได้ว่า "รอครบ 85 แท่ง" ขาดอีกเท่าไร
    และไม่ยกเป็น exception เพราะข้อมูลยังไม่ครบ **ไม่ใช่ความผิดพลาด** มันเป็นสถานะ
    ปกติของคู่ที่เพิ่งเพิ่มเข้ามา
    """
    return max(0, MIN_CLOSED_BARS - len(bars))


class BarSource(Protocol):
    """คืนแท่งที่ปิดแล้วทั้งหมดที่มี ณ เวลาของตัวเอง เรียงเก่า → ใหม่

    ผู้เรียกเป็นคนตัดเอาท้าย N แท่งตามที่ต้องการ (#04 ใช้ 130 เพื่อ warm-up, การ
    ตัดสินใจใช้ 85) ชั้นนี้ไม่ตัดให้เพราะไม่รู้ว่าใครต้องใช้เท่าไร
    """

    def bars(self, symbol: str, timeframe: str) -> list[Bar]: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def fetch_forward(
    client: ExchangeClient,
    market: str,
    symbol: str,
    timeframe: str,
    since: int | None,
    limit: int,
) -> list[list[float]]:
    """ดึงไปข้างหน้าจนหมด ไม่ใช่หน้าเดียว

    **หน้าเดียวไม่พอเมื่อประวัติในตารางค้างเก่ากว่า `limit` แท่ง** — bot ที่หยุดไป
    สามสัปดาห์แล้วกลับมาบน timeframe 1h จะมีช่องว่างเกิน 500 แท่ง การขอครั้งเดียวจาก
    `since` จะได้ 500 แท่งแรกของช่องว่างนั้นมา แล้วหยุด แท่งที่ใหม่ที่สุดจะหายไป
    เงียบๆ และร้ายที่สุดคือ **ทุกแท่งที่ได้มานั้นปิดแล้วจริง** ตัวกรอง `as_of` จึงไม่
    เห็นอะไรผิด engine จะคำนวณ Action Zone บนแท่งของเมื่อสามสัปดาห์ก่อนโดยเชื่อว่า
    เป็นแท่งล่าสุด — ข้อมูลเก่าที่หน้าตาเหมือนข้อมูลสด

    `since = None` (ยังไม่มีแท่งในตาราง) ไม่ต้องไล่หน้า เพราะ endpoint คืน `limit`
    แท่ง **ล่าสุด** ให้อยู่แล้ว ซึ่งเกินเกณฑ์ 85 แท่งไปไกล
    """
    venue_symbol = unified_symbol(symbol, market)
    if since is None:
        return client.fetch_ohlcv(venue_symbol, timeframe, since=None, limit=limit)

    rows: list[list[float]] = []
    cursor = since
    while True:
        page = client.fetch_ohlcv(venue_symbol, timeframe, since=cursor, limit=limit)
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        last_open = int(page[-1][0])
        if last_open <= cursor:
            # cursor ไม่ขยับ = ขอต่อไปก็ได้ของเดิม ต้องออกก่อนกลายเป็นลูปไม่จบ
            break
        cursor = last_open
    return rows


def _load(
    conn: "Connection",
    client: ExchangeClient,
    market: str,
    symbol: str,
    timeframe: str,
    limit: int,
    wall_now: int,
) -> list[Bar]:
    """ดึง + รวมกับประวัติในตาราง แล้วเขียนกลับ — คืน **ทุกแท่งที่มี รวมแท่งที่ยังวิ่ง**

    ตัวกรอง `as_of` ไม่อยู่ที่นี่โดยเจตนา เพราะ replay มี `as_of` อยู่ในอดีตขณะที่
    ตารางล้ำหน้าไปแล้ว ถ้ากรองตอนโหลด replay จะอ่านประวัติที่มีอยู่ไม่ได้เลย
    การกรองจึงเกิดตอน *อ่าน* ทุกครั้ง ไม่ใช่ตอนเก็บ

    ที่เขียนลงตารางใช้ **เวลานาฬิกาจริง** ไม่ใช่ `as_of` ของผู้เรียก และ
    `closed_as_of()` บรรทัดนั้น **เป็นด่านเดียวของระบบ** ที่กันแท่งที่ยังวิ่งไม่ให้ลง
    ตาราง — `bars` ไม่มี CHECK ที่รู้จักเวลาปัจจุบัน (schema.py:66) และ `cane_engine`
    ไม่มี `UPDATE`/`DELETE` แถวที่หลุดไปจึงเป็นข้อมูลผิด **ถาวร** ไม่ใช่ cache ที่ลบ
    แล้วสร้างใหม่ได้เหมือนเดิม

    ไม่ commit — ผู้เรียกเป็นเจ้าของทรานแซกชันตามสัญญาของชั้น repo
    (`db/repo/__init__.py`) · `conn` ไม่มีค่าตั้งต้น: source ที่ลืมส่ง connection จะ
    ดึงใหม่ทั้งชุดทุกแท่งแล้วไม่เก็บอะไรเลย ซึ่งเป็นความเสื่อมแบบเงียบ ๆ ที่ทั้งไฟล์นี้
    เขียนมาเพื่อไม่ให้เกิด
    """
    # import ที่นี่ ไม่ใช่หัวไฟล์ — `db/repo/bars.py` นำเข้า `Bar` จากไฟล์นี้
    # การนำเข้าที่หัวไฟล์จึงผูกวงกันตอน `cane.data` ถูกโหลดก่อน `cane.db`
    from cane.db.repo.bars import closed_bars, insert_bars

    stored = closed_bars(conn, market, symbol, timeframe, as_of=wall_now)
    since = stored[-1].open_ts if stored else None
    fetched = to_bars(
        fetch_forward(client, market, symbol, timeframe, since, limit), timeframe
    )
    insert_bars(
        conn,
        market,
        symbol,
        timeframe,
        closed_as_of(fetched, wall_now),
        created_ts=wall_now,
    )
    # `stored` มาหลัง `fetched` เพราะ `merge_bars` เอาตัวหลังเมื่อ open_ts ซ้ำ และ
    # `insert_bars` เป็น `ON CONFLICT DO NOTHING` — แถวเดิมในตารางชนะการเขียนซ้ำ
    # เสมอ ถ้าคืนค่าที่ดึงมาใหม่ทับ indicator จะคำนวณบนตัวเลขที่ตารางไม่ได้ถืออยู่
    return merge_bars(fetched, stored)


class LiveBarSource:
    """แท่งที่ปิดแล้ว ณ เวลาจริง

    "poll" ในความหมายนี้คือ *ดึงใหม่ทุกครั้งที่ถูกเรียก* ไม่ใช่ลูปที่นอนรอแท่งปิด
    ตัวลูปเป็นของ #12 ชั้นนี้ตอบคำถามเดียว: ตอนนี้มีแท่งที่ปิดแล้วอะไรบ้าง
    """

    def __init__(
        self,
        conn: "Connection",
        client: ExchangeClient,
        market: str,
        *,
        clock: Callable[[], int] = _now_ms,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._conn = conn
        self._client = client
        self._market = market
        self._clock = clock
        self._limit = limit

    def bars(self, symbol: str, timeframe: str) -> list[Bar]:
        now = self._clock()
        history = _load(
            self._conn,
            self._client,
            self._market,
            symbol,
            timeframe,
            self._limit,
            now,
        )
        return closed_as_of(history, now)


class ReplayBarSource:
    """เดินย้อนเวลาบนโค้ดเส้นเดียวกับ live — ต่างกันแค่ `as_of` มาจากไหน

    `as_of` เป็นแอตทริบิวต์เปิด ให้ #12 เลื่อนไปข้างหน้าเองทีละแท่ง ไม่ห่อเป็นเมธอด
    เพราะยังไม่รู้ว่า #12 จะเดินด้วยจังหวะไหน — ห่อไว้ตอนนี้คือเดาล่วงหน้า

    ดึงจากปลายทางครั้งเดียวต่อ (symbol, timeframe) แล้วจำไว้ ถ้าดึงใหม่ทุกก้าว
    การ replay หนึ่งปีจะยิง request หลายร้อยครั้งเพื่อข้อมูลชุดเดิม
    """

    def __init__(
        self,
        conn: "Connection",
        client: ExchangeClient,
        market: str,
        *,
        as_of: int,
        clock: Callable[[], int] = _now_ms,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._conn = conn
        self._client = client
        self._market = market
        self._clock = clock
        self._limit = limit
        self.as_of = as_of
        self._history: dict[tuple[str, str], list[Bar]] = {}

    def bars(self, symbol: str, timeframe: str) -> list[Bar]:
        key = (symbol, timeframe)
        # กุญแจไม่มี market เพราะ market ผูกกับ instance แล้ว (หนึ่ง client หนึ่งตลาด)
        # ถ้าวันหนึ่งมีใครขยาย `BarSource.bars()` ให้รับ market กุญแจนี้คือบั๊กตัวแรก
        if key not in self._history:
            self._history[key] = _load(
                self._conn,
                self._client,
                self._market,
                symbol,
                timeframe,
                self._limit,
                self._clock(),
            )
        return closed_as_of(self._history[key], self.as_of)
