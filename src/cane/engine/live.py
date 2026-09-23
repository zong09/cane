"""ตัวประกอบของ live — ต่อ `CcxtBroker` กับไปป์ไลน์เข้ากับลูปของ `cane engine run`

คู่แฝดของ `engine/replay.py`: **ไม่มีตรรกะการตัดสินใจเลย** มีแค่การประกอบของที่ฉีดเข้า
`run_bar()` · ถ้าตรรกะไหนของไปป์ไลน์ต้องรู้ว่ากำลังรัน live อยู่ นั่นคือบั๊กของไปป์ไลน์
ไม่ใช่ของที่นี่ (ADR 9)

## อะไรอยู่ข้ามแท่ง อะไรสร้างใหม่ทุกแท่ง

ลูปเปิด **ทรานแซกชันใหม่ต่อหนึ่งแท่ง** (`loop._run_bar_cycle`) และ `CcxtBroker` ถือ `conn`
ไว้เขียน ledger — broker กับแหล่งแท่งจึงสร้างใหม่ทุกแท่งพร้อม connection ของแท่งนั้น

ที่อยู่ข้ามแท่งมีสามอย่าง และทั้งสามอยู่ใน**หน่วยความจำ**โดยตั้งใจ:

- `DayPnl` ต่อตลาด — ฐานของด่าน `daily_loss` คือ equity ที่สังเกตครั้งสุดท้าย ถ้าสร้างใหม่
  ทุกแท่งทุกค่าจะเป็นศูนย์ตลอดและด่านนี้จะไม่มีวันปฏิเสธอะไร
- `cold_start_pending` ต่อ (market, symbol) — spec/08 §cold start บอกว่าต้องหายไปพร้อม
  กระบวนการ **ห้ามเป็น flag บนดิสก์**
- ccxt client ต่อตลาด — `load_markets()` ดึงตารางทั้ง venue มาทีเดียว และตัวจับ rate limit
  ของ ccxt อยู่ใน instance สร้างใหม่ทุกแท่งคือทิ้งทั้งสองอย่าง

## ตัวตัดสินถูกสร้างที่แท่งแรก ไม่ใช่ตอนสตาร์ท

spec/10 §4. รอบชีวิตของ engine บอกว่า config ที่ใช้ไม่ได้ **ไม่ทำให้ process ตาย** ต้อง
เขียน `blocked_reason` แล้ววนต่อ — process ที่ตายแล้วบอกไม่ได้ว่าตายเพราะอะไร แล้วคอนโซล
จะแสดงได้แค่ `crashed` ซึ่งชี้ไปผิดที่ · ค่าที่ขาดใน `.env` ของตัวตัดสินเป็นกรณีเดียวกัน
จึงรับ **โรงงาน** เข้ามาแล้วเรียกที่แท่งแรก ข้อผิดพลาดตรงนั้นถูก `loop._run_bar_cycle`
จับแล้วกลายเป็นเหตุผลที่คอนโซลแสดงได้

(`model_id` ต้องมีก่อนเรียก `run_bar` เพราะมันเข้า `prompt_hash` และกุญแจของ verdict cache
จึงเลื่อนการสร้างทั้งก้อนไปพร้อมกัน ไม่ใช่สร้าง client ตอนหลังแล้วเดา id ไว้ก่อน)

## config เปลี่ยนกลางรันได้

ลูปอ่าน config ที่ active ใหม่ทุกต้นรอบแล้วส่ง `Settings` เข้ามา · ที่นี่จึงอ่าน `settings`
ที่ได้รับ ไม่ใช่ตัวที่จำไว้ตอนสร้าง — คู่เหรียญที่คอนโซลเพิ่งเปิดใช้ต้องมีผลที่แท่งถัดไป
ไม่ใช่ต้องรีสตาร์ท engine · client ของตลาดที่ยังไม่เคยใช้ถูกสร้างเพิ่มตอนนั้น
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import Connection

from collections.abc import Callable

from cane.config.settings import Settings
from cane.confluence import LlmClient
from cane.data.exchange import make_client
from cane.data.ohlcv import LiveBarSource
from cane.db.repo import config as config_repo
from cane.engine.lots import CcxtLotSource
from cane.engine.pipeline import DayPnl, RunContext, SymbolRuntime, run_bar
from cane.execution.ccxt_broker import CcxtBroker, make_trading_client

log = logging.getLogger(__name__)


class LiveError(RuntimeError):
    """ต่อ live ไม่ได้ — ข้อความบอกว่าต้องแก้อะไร"""


@dataclass
class LiveRunner:
    """`on_bar` ของลูป · หนึ่งตัวต่อหนึ่ง process (หนึ่ง profile)

    สร้างแล้ว**ยังไม่แตะเน็ต** — client ถูกสร้างตอนเจอตลาดนั้นครั้งแรก · ทำให้
    `cane engine run` ที่ config ยังไม่มีคู่เหรียญของตลาดไหนไม่ต้องถือคีย์ของตลาดนั้น
    """

    profile: str
    #: `() -> (client, model_id)` · เรียกครั้งเดียวที่แท่งแรก ดูหัวไฟล์ว่าทำไมไม่ใช่ตอนสร้าง
    judge_factory: Callable[[], tuple[LlmClient, str]]

    _judge: tuple[LlmClient, str] | None = field(init=False, default=None)
    _trading: dict[str, object] = field(init=False, default_factory=dict)
    _data: dict[str, object] = field(init=False, default_factory=dict)
    _day: dict[str, DayPnl] = field(init=False, default_factory=dict)
    _cold_pending: dict[tuple[str, str], bool] = field(init=False, default_factory=dict)
    _lots: CcxtLotSource | None = field(init=False, default=None)

    def __call__(self, conn: Connection, settings: Settings, close_ts: int) -> None:
        """หนึ่งแท่งของทุกคู่เหรียญที่ enabled — ลายเซ็นของ `loop.BarHook`

        `close_ts` ที่ลูปส่งมาเป็นเวลาที่**ถึงกำหนดตัดสิน** ส่วนแท่งที่ตัดสินจริงมาจาก
        `BarSource` (ขั้น 1) · ไม่เอามาบังคับกัน เพราะเหรียญที่ venue ยังไม่เผยแพร่แท่ง
        ล่าสุดต้องข้ามไปรอ ไม่ใช่ถูกตัดสินด้วยแท่งเก่าที่สวมเวลาใหม่
        """
        if settings.broker.kind != "ccxt":
            # **ไม่ยกข้อผิดพลาด** — นั่นจะทำให้ engine ของ profile ที่ตั้ง broker จำลองไว้
            # ขึ้นสถานะ `blocked` ทั้งที่มันทำงานปกติของมันอยู่ (เต้นและอ่าน config) ·
            # ใบนี้ต่อเฉพาะปลายทางจริง ส่วน `PaperBroker` เดินด้วย `cane replay run`
            log.warning(
                "profile %s ตั้ง broker.kind = %r — ลูปนี้ต่อเฉพาะ 'ccxt' จึงยังไม่ตัดสินใจอะไร",
                self.profile, settings.broker.kind,
            )
            return
        version = config_repo.active_version(conn, self.profile)
        if version is None:
            raise LiveError(f"profile {self.profile} ไม่มี config ที่ active")

        symbols = [s for s in settings.symbols if s.enabled]
        for cfg in symbols:
            self._ensure_market(settings, cfg.market)
        if self._judge is None:
            self._judge = self.judge_factory()
        judge, model_id = self._judge
        ctx = RunContext(
            profile=self.profile,
            timeframe=settings.timeframe,
            settings=settings,
            config_version_id=version.id,
            judge=judge,
            model_id=model_id,
            lots=self._lot_source(),
            use_intents=True,
        )

        sources: dict[str, LiveBarSource] = {}
        brokers: dict[str, CcxtBroker] = {}
        for cfg in symbols:
            if cfg.market not in sources:
                sources[cfg.market] = LiveBarSource(conn, self._data[cfg.market], cfg.market)  # type: ignore[arg-type]
                brokers[cfg.market] = CcxtBroker(
                    conn=conn,
                    client=self._trading[cfg.market],  # type: ignore[arg-type]
                    market=cfg.market,
                    profile=self.profile,
                    bars=sources[cfg.market],
                    timeframe=settings.timeframe,
                    symbols=tuple(s.symbol for s in symbols if s.market == cfg.market),
                )
            key = (cfg.market, cfg.symbol)
            run_bar(
                conn,
                ctx,
                SymbolRuntime(
                    cfg=cfg,
                    bars=sources[cfg.market],
                    broker=brokers[cfg.market],
                    day=self._day.setdefault(cfg.market, DayPnl()),
                    cold_start_pending=self._cold_pending.get(key, True),
                ),
            )
            # `run_bar` ปิดธงใน `SymbolRuntime` ที่ตัวเองถือ ซึ่งเป็นของชั่วคราวของแท่งนี้ —
            # ตัวที่ข้ามแท่งคือ dict นี้ ถ้าไม่ย้ายค่ากลับมา cold start จะถูกประเมินใหม่ทุกแท่ง
            self._cold_pending[key] = False

    def _ensure_market(self, settings: Settings, market: str) -> None:
        if market in self._trading:
            return
        exchange = settings.broker.exchange
        if not exchange:
            raise LiveError("broker.kind = 'ccxt' แต่ไม่ได้ระบุ broker.exchange ใน config")
        self._trading[market] = make_trading_client(exchange, market)
        # ชั้นข้อมูลใช้ client ที่ **ไม่มีคีย์** ต่างหาก (ดู `data/exchange.py`) และ venue
        # ของข้อมูลกับของการเทรดเป็นคนละค่าใน config ได้ — แท่งจาก venue หนึ่งแล้วยิงที่
        # อีก venue เป็นการตั้งค่าที่คนตั้งใจได้ ไม่ใช่ของที่เราควรรวมให้เอง
        self._data[market] = make_client(settings.data.exchange, market)
        self._lots = None
        log.info("ต่อ %s ตลาด %s แล้ว (ข้อมูลจาก %s)", exchange, market, settings.data.exchange)

    def _lot_source(self) -> CcxtLotSource:
        if self._lots is None:
            self._lots = CcxtLotSource(self._trading)
        return self._lots
