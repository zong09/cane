"""ตัวขับ replay ย้อนหลัง — เดิน `as_of` ทีละแท่งผ่าน `run_bar()` ตัวเดียวกับ live (ADR 9, ADR 29)

ที่นี่ **ไม่มีตรรกะการตัดสินใจเลย** — มีแค่การประกอบของที่ฉีดเข้า `run_bar()` (แหล่งแท่งแบบ replay, `PaperBroker`,
ตัวตัดสิน) กับการเดินเวลา · ถ้าตรรกะไหนของไปป์ไลน์ต้องรู้ว่ากำลัง replay อยู่ นั่นคือบั๊กของไปป์ไลน์

## ต้องรันบน scratch database เท่านั้น

`PaperBroker` เก็บเงินสดในหน่วยความจำและตารางข้อเท็จจริงเป็น append-only (ADR 23): replay บน database ของ paper/live
จริงจะทิ้งการตัดสินใจปลอมหลายร้อยแถวไว้ถาวรโดยลบไม่ได้ · จึงตรวจชื่อ database ก่อนเริ่มทุกครั้ง (`REPLAY_DB_MARK`)
และ `replay_cursor` กันการรันซ้ำใน scratch เดิม

## transaction

connection หลักถือ transaction ต่อ `as_of` — `commit` หลังทุก symbol ที่ตัดสินแล้วของ `as_of` นั้นพร้อมกับขยับ cursor ·
connection ที่สองสำหรับ verdict cache ถูก commit ทันทีหลัง `run_bar()` ทุกครั้ง แม้ทรานแซกชันหลักจะถูกย้อนกลับ เพราะ
คำตัดสินของ LLM คือของที่จ่ายเงินซื้อมา (`judge_side` หมายเหตุเรื่อง connection) · **ข้อผิดพลาดใน `run_bar()` หยุดทั้งรัน**:
สถานะของ `PaperBroker` ในหน่วยความจำจะเพี้ยนจากฐานที่ถูกย้อนกลับ ทำต่อไม่ได้อย่างถูกต้อง
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import Engine

from cane.confluence import LlmClient
from cane.data.offline import OfflineClient
from cane.data.ohlcv import ReplayBarSource
from cane.db.repo import bars as bars_repo
from cane.db.repo import config as config_repo
from cane.db.repo import replay_cursor
from cane.engine.funding_cycles import funding_cycles
from cane.engine.lots import StaticLotSource
from cane.engine.pipeline import DayPnl, LotSource, RunContext, SymbolRuntime, run_bar
from cane.execution.paper import PaperBroker

log = logging.getLogger(__name__)

#: ชื่อ database ต้องมีคำนี้ ไม่งั้นไม่ยอมรัน — ดูหัวไฟล์ว่าทำไม
REPLAY_DB_MARK = "replay"

#: ทุกกี่แท่งถึงจะ log ความคืบหน้าหนึ่งครั้ง
PROGRESS_EVERY = 30


class ReplayError(RuntimeError):
    """replay เริ่มไม่ได้หรือไม่ควรเริ่ม — ข้อความบอกว่าต้องแก้อะไร"""


class NoJudge:
    """ตัวตัดสินที่ไม่มีตัวตัดสิน — ทุกครั้งยกข้อผิดพลาด จึงทุกไม้ตกไป fallback ที่ `base_pct` (ADR 6)

    ใช้รัน replay โดยไม่มี LLM: ผลคือเทรดกับ P&L ที่สะท้อนเฉพาะสัญญาณกับกฎ ไม่สะท้อน Judge · ทุกแถวติดธง
    `llm_fallback` ให้คนอ่านเห็นชัด ไม่ใช่ทำเหมือน Judge ตอบว่า "ไม่มีปัจจัย" (ซึ่งเป็นคำตอบอีกแบบ)
    """

    model_id = "none|fallback-only"

    def ask(self, **_: object) -> dict[str, object]:
        raise RuntimeError("replay นี้ไม่มีตัวตัดสิน (--judge none)")


@dataclass(slots=True)
class _Clock:
    """`decided_ts` ของ replay คือเวลาที่แท่งปิด ไม่ใช่นาฬิกาจริงของวันที่รัน — ไม่งั้นทุกแถวมีเวลาตัดสินเป็น "วันนี้" """

    value: int = 0

    def __call__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    bars: int
    decisions: int
    first_close_ts: int
    last_close_ts: int
    per_symbol: dict[tuple[str, str], int] = field(default_factory=dict)


def check_scratch(db: Engine) -> None:
    """ปฏิเสธถ้า database ที่ต่ออยู่ไม่ใช่ scratch ของ replay"""
    name = db.url.database or ""
    if REPLAY_DB_MARK not in name:
        raise ReplayError(
            f"database {name!r} ไม่ใช่ scratch ของ replay (ชื่อต้องมี {REPLAY_DB_MARK!r}) — "
            "replay เขียนการตัดสินใจปลอมลงตาราง append-only ห้ามรันบน database จริง · "
            "สร้าง database แยกแล้วตั้ง CANE_DB_DSN ไปที่นั่น (ADR 29)"
        )


def run_replay(
    *,
    db: Engine,
    profile: str,
    from_close_ts: int,
    to_close_ts: int,
    judge: LlmClient,
    model_id: str,
    lots: LotSource | None = None,
) -> ReplaySummary:
    """เดินทุกแท่งที่ปิดในช่วง `[from_close_ts, to_close_ts]` ของทุก symbol ที่ enabled

    `db` ต้องสวม role `engine` และชี้ไป scratch database · แท่งต้องอยู่ในตาราง `bars` แล้ว
    (`cane data import-bars`) — replay ไม่ดึงอะไรจากเครือข่าย
    """
    check_scratch(db)
    with db.begin() as conn:
        settings = config_repo.active_settings(conn, profile)
        version = config_repo.active_version(conn, profile)
    if settings is None or version is None:
        raise ReplayError(
            f"profile {profile!r} ยังไม่มี config ที่ active — สั่ง `cane db seed` ก่อน (ไม่มีค่าตั้งต้นให้เดา)"
        )
    symbols = [s for s in settings.symbols if s.enabled]

    with db.connect() as conn, db.connect() as judge_conn:
        timeline = _timeline(conn, settings.timeframe, symbols, from_close_ts, to_close_ts)
        if not timeline:
            raise ReplayError(
                f"ไม่มีแท่งที่ปิดในช่วง {from_close_ts}–{to_close_ts} ของ symbol ที่ enabled — "
                "ตรวจว่า import แท่งแล้ว (`cane data import-bars`) และช่วงวันที่ถูกต้อง"
            )
        # ปลายทางของ cursor คือ as_of สุดท้ายที่จะเดินจริง (ไม่ใช่วันที่ผู้ใช้พิมพ์) — CHECK ของตารางคือ
        # `end_ts >= as_of_ms` ถ้าตั้งไว้ต่ำกว่า แท่งสุดท้ายจะถูกฐานปฏิเสธหลังเดินมาทั้งปี
        first, last = timeline[0][0], timeline[-1][0]
        try:
            replay_cursor.start(conn, profile, as_of_ms=first, end_ts=last + 1)
        except replay_cursor.ReplayCursorExists as exists:
            raise ReplayError(str(exists)) from exists
        conn.commit()

        clock = _Clock()
        ctx = RunContext(
            profile=profile,
            timeframe=settings.timeframe,
            settings=settings,
            config_version_id=version.id,
            judge=judge,
            model_id=model_id,
            lots=lots or StaticLotSource(),
            now=clock,
        )
        sources: dict[str, ReplayBarSource] = {}
        brokers: dict[str, PaperBroker] = {}
        day_pnl: dict[str, DayPnl] = {}
        runtimes: dict[tuple[str, str], SymbolRuntime] = {}
        for cfg in symbols:
            if cfg.market not in sources:
                sources[cfg.market] = ReplayBarSource(conn, OfflineClient(), cfg.market, as_of=first)
                brokers[cfg.market] = _broker(conn, settings, cfg.market, sources[cfg.market])
                day_pnl[cfg.market] = DayPnl()
            runtimes[(cfg.market, cfg.symbol)] = SymbolRuntime(
                cfg=cfg, bars=sources[cfg.market], broker=brokers[cfg.market], day=day_pnl[cfg.market]
            )

        written: dict[tuple[str, str], int] = {}
        for index, (close_ts, due) in enumerate(timeline, start=1):
            clock.value = close_ts + 1
            for source in sources.values():
                # `closed_as_of()` ใช้ `close_ts < as_of` — ตั้งเท่ากับ close_ts แท่งที่เพิ่งปิดจะยังไม่โผล่
                source.as_of = close_ts + 1
            for key in due:
                _decide(conn, judge_conn, ctx, runtimes[key])
                written[key] = written.get(key, 0) + 1
            replay_cursor.advance(conn, profile, close_ts + 1)
            conn.commit()
            if index % PROGRESS_EVERY == 0 or index == len(timeline):
                log.info("replay %d/%d แท่ง (ปิด %d)", index, len(timeline), close_ts)

        return ReplaySummary(
            bars=len(timeline),
            decisions=sum(written.values()),
            first_close_ts=first,
            last_close_ts=last,
            per_symbol=written,
        )


def _decide(conn, judge_conn, ctx: RunContext, sym: SymbolRuntime) -> None:  # noqa: ANN001
    try:
        run_bar(conn, ctx, sym, judge_conn=judge_conn)
    except BaseException:
        # ทรานแซกชันของแท่งนี้ย้อนกลับ แต่คำตัดสินที่ซื้อมาแล้วต้องรอด แล้วหยุดทั้งรัน (ดูหัวไฟล์)
        conn.rollback()
        judge_conn.commit()
        raise
    judge_conn.commit()


def _timeline(
    conn, timeframe: str, symbols: Sequence, from_ts: int, to_ts: int  # noqa: ANN001
) -> list[tuple[int, list[tuple[str, str]]]]:
    """`[(close_ts, [(market, symbol) ที่มีแท่งปิดพอดีเวลานั้น])]` เรียงตามเวลา

    ตัดสินเฉพาะ symbol ที่มีแท่งปิดที่ `close_ts` นั้นจริง — symbol ที่แท่งขาดหายไปวันหนึ่งจะไม่ถูกตัดสินซ้ำบน
    แท่งเดิมของวันก่อน (ทุกแถวของ `decisions` ต้องมาจากแท่งใหม่หนึ่งแท่ง)
    """
    due: dict[int, list[tuple[str, str]]] = {}
    for cfg in symbols:
        history = bars_repo.closed_bars(conn, cfg.market, cfg.symbol, timeframe, as_of=to_ts + 1)
        for bar in history:
            if bar.close_ts >= from_ts:
                due.setdefault(bar.close_ts, []).append((cfg.market, cfg.symbol))
    return sorted(due.items())


def _broker(conn, settings, market: str, source: ReplayBarSource) -> PaperBroker:  # noqa: ANN001
    b = settings.broker
    if b.kind != "paper":
        raise ReplayError(f"replay จำลองได้เฉพาะ broker.kind = 'paper' ไม่ใช่ {b.kind!r}")
    return PaperBroker(
        conn=conn,
        market=market,
        profile=settings.profile,
        bars=source,
        timeframe=settings.timeframe,
        seed_quote=b.seed_quote,
        taker_fee_pct=b.taker_fee_pct,
        maintenance_margin_pct=b.maintenance_margin_pct,
        funding_source=funding_cycles if market == "usdtm_perp" else None,
    )
