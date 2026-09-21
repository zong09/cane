"""ไปป์ไลน์ต่อแท่งของหนึ่ง (symbol, market) — ตะเข็บเดียวที่ live กับ replay ใช้ร่วมกัน

`run_bar()` ทำสิบสี่ขั้นของ spec/08 §สิบสี่ขั้นของหนึ่งรอบ กับแท่งที่ปิดล่าสุดที่ `BarSource` ให้มา แล้วเขียน
`DecisionRecord` หนึ่งแถว · replay ต่างจาก live แค่ตัวที่ฉีดเข้ามา (`ReplayBarSource` แทน `LiveBarSource`)
ไม่มีตัวเดินตัวที่สอง (ADR 9)

## ลำดับที่ลงมือจริงต่างจากรูปใน spec เล็กน้อย

spec/08 ขีดขั้น 4 (ปิด) ไว้ก่อนขั้น 8–12 (features · Judge · ขนาดไม้ · risk) แต่ `execute_flip()` ต้องได้คำสั่ง
เปิดของขา 2 ล่วงหน้าพร้อมคำสั่งปิดของขา 1 จึงคำนวณขั้น 8–12 ก่อน แล้วค่อยส่ง · ขั้น 8–12 ไม่เขียนอะไรที่
exchange จึงย้ายได้โดยไม่ละเมิด "ขา 1 ก่อนขา 2 เสมอ" ซึ่งเป็นเรื่องของลำดับการส่ง · ถ้าขาเปิดถูกปฏิเสธหรือข้าม
สัญญาณตรงข้ามยังจริงอยู่ จึงยัง **ปิดสถานะเดิม** ผ่านทางปิดอย่างเดียว

## ล้มแบบไหน

- ชั้นข้อมูลหรือ config ผิด (`UnknownLot`, `ValueError` จาก `decide()`) **ยกขึ้นไป ไม่กลืน** — แท่งที่ตัดสินบน
  ข้อมูลที่รู้ว่าผิดแย่กว่าแท่งที่ไม่ถูกตัดสิน
- broker ล้มตอนส่งคำสั่ง → บันทึก `error` ที่ขานั้นแล้วจบรอบด้วย `order_error` · ขั้น 3 ของแท่งถัดไปอ่านสถานะจริง
  ซ้ำเอง ไม่ต้องมีขั้น "กู้"
- **Judge ล้มไม่ขวางอะไร** — `judge_side()` คืน fallback เอง (ADR 6) และขาปิดไม่เรียก Judge เลย

## สิ่งที่จำไว้ในหน่วยความจำ และทำไมได้

`cold_start_pending` ต่อ (symbol, market) — "ครั้งที่ engine เริ่ม" ของ spec/08 §cold start ต้องหายไปพร้อม
กระบวนการ ตั้งใจให้เป็นอย่างนั้น · `DayPnl` เป็นฐานของ `daily_loss` และเริ่มใหม่ทุก run (ค่าที่ไม่รู้ = 0.0 ไม่ใช่
`None`: `None` ปฏิเสธทุกไม้ ซึ่งจะทำให้แท่งแรกของทุก run เปิดไม้ไม่ได้)

ที่อื่นทั้งหมด (stop ที่ควรมี, ของค้างจาก flip abort) **อ่านกลับจากตาราง `decisions`** ไม่ใช้ flag บนดิสก์
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Connection

from cane.config.settings import Settings, SymbolConfig
from cane.confluence import LlmClient, judge_side
from cane.data.ohlcv import MIN_CLOSED_BARS, BarSource
from cane.db.repo import decisions as decisions_repo
from cane.db.repo.decisions import (
    DecisionRecord,
    Flip,
    OrderAttempt,
    Stop,
    Unmanaged,
    Verdict,
)
from cane.db.types import PRICE_SCALE, now_ms
from cane.execution.broker import Broker, OpenOrder, Order, OrderResult, Position, client_order_id
from cane.execution.paper import liq_price
from cane.indicators import action_zones, features
from cane.indicators.trailing import cdc_trailing_stop
from cane.risk import check_all
from cane.rules.cane import BarPlan, decide
from cane.rules.flip import execute_flip
from cane.rules.late_entry import late_entry, maintain_stop
from cane.sizing.matrix import LotFilter, SizeDecision, plan_size

log = logging.getLogger(__name__)

DAY_MS = 86_400_000

#: `OrderResult.status` ที่แปลว่า venue รับคำสั่งแล้ว — `closed` = fill · `open` = ค้างรอ (stop)
_ACCEPTED = ("open", "closed")

#: เหตุผลที่ลง `decision_unmanaged.source` — ชุดปิดในโค้ด ตารางเป็น TEXT ไม่มี CHECK จึงต้องรักษาชุดนี้ที่นี่
UNMANAGED_SOURCES = ("flip_aborted", "close_partial")


class LotSource(Protocol):
    def lot(self, market: str, symbol: str) -> LotFilter: ...


@dataclass(slots=True)
class DayPnl:
    """กำไรขาดทุนของวัน (UTC) ของ broker หนึ่งตัว นับ mark-to-market — ฐานของด่าน `daily_loss`

    ฐานคือ equity ที่ **สังเกตครั้งสุดท้ายก่อนวันใหม่เริ่ม** ไม่ใช่ equity ตอนสังเกตครั้งแรกของวัน · บนแท่ง 1d
    ทุกแท่งอยู่คนละวัน ถ้าใช้แบบหลังทุกค่าจะเป็นศูนย์ตลอด และด่านนี้จะไม่มีวันปฏิเสธอะไร ·
    วันของแท่งคือวันของ `close_ts - 1` เพราะแท่งที่ปิดเที่ยงคืนพอดีเป็นของวันที่เพิ่งจบ

    ตัวอย่างต่อ broker ไม่ใช่ต่อ symbol: กระเป๋าหนึ่งใบมีกำไรขาดทุนของวันเดียว · ผู้สร้างแชร์ instance เดียว
    ให้ทุก symbol ที่อยู่บน broker เดียวกัน
    """

    day: int | None = None
    baseline: Decimal | None = None
    _last: Decimal | None = field(default=None, repr=False)

    def pct(self, broker: Broker, positions: Sequence[Position], bar_close_ts: int) -> float:
        """เรียก **ทุกแท่ง** ไม่ใช่เฉพาะแท่งที่จะเปิดไม้ — ฐานคือการสังเกตครั้งสุดท้าย ถ้าข้ามแท่งฐานจะเก่า

        `positions` คือที่ขั้น 3 อ่านมาแล้ว ไม่อ่านซ้ำ (live: หนึ่ง request ต่อแท่งที่ประหยัดได้)
        """
        equity = broker.balance().total + sum((p.unrealized_pnl for p in positions), Decimal("0"))
        today = (bar_close_ts - 1) // DAY_MS
        if self.day != today:
            # วันใหม่: ฐานคือค่าที่เห็นครั้งสุดท้าย (ก่อนวันเริ่ม) · ไม่เคยเห็นเลย = ครั้งแรกของ run → 0.0
            self.day, self.baseline = today, self._last if self._last is not None else equity
        self._last = equity
        if not self.baseline:
            return 0.0
        return float((equity - self.baseline) / self.baseline * 100)


@dataclass(frozen=True, slots=True)
class RunContext:
    """ของที่คงที่ตลอดรัน — config ที่ active, ตัวตัดสิน, แหล่ง lot"""

    profile: str
    timeframe: str
    settings: Settings
    config_version_id: int
    judge: LlmClient
    model_id: str
    lots: LotSource
    now: Callable[[], int] = now_ms

    @property
    def dry_run_blocks(self) -> bool:
        """ADR 31: `dry_run` กั้นเฉพาะ broker ที่ส่งคำสั่งจริงได้ — paper จำลองเสมอ"""
        return self.settings.dry_run and self.settings.broker.kind == "ccxt"


@dataclass(slots=True)
class SymbolRuntime:
    """ของต่อ (symbol, market) — broker กับแหล่งแท่งของตลาดนั้น (ADR 28: หนึ่ง client หนึ่งตลาด)"""

    cfg: SymbolConfig
    bars: BarSource
    broker: Broker
    day: DayPnl = field(default_factory=DayPnl)
    #: จริงจนกว่ารอบแรกที่ตัดสินได้จะจบ — ดูหัวไฟล์ว่าทำไมอยู่ในหน่วยความจำ
    cold_start_pending: bool = True


@dataclass(slots=True)
class _Legs:
    """สิ่งที่เกิดกับ exchange ในแท่งนี้ — ประกอบเป็นลูกของ `DecisionRecord`"""

    orders: list[OrderAttempt] = field(default_factory=list)
    flip: Flip | None = None
    stop: Stop | None = None
    unmanaged: list[Unmanaged] = field(default_factory=list)
    skip_reason: str | None = None
    opened_qty: float = 0.0


def run_bar(
    conn: Connection,
    ctx: RunContext,
    sym: SymbolRuntime,
    *,
    judge_conn: Connection | None = None,
) -> DecisionRecord | None:
    """ตัดสินใจหนึ่งแท่งของหนึ่ง (symbol, market) แล้วเขียนบันทึก · คืนบันทึกที่เขียน หรือ `None` ถ้าข้าม

    `None` มีเหตุเดียว: แท่งที่ปิดแล้วมีไม่ถึง `MIN_CLOSED_BARS` (ไม่มี zone/state/close_px ให้แถวบังคับมี ·
    log ระดับ warning แทน) · `enabled = false` ไม่เข้ามาถึงที่นี่ — ผู้เรียกกรองเอง

    `conn` เป็นของผู้เรียก (ไม่ commit) · `judge_conn` คือ connection สำหรับ verdict cache: ค่าตั้งต้นคือ `conn`
    ตัวเดียวกัน (เทสต์ rollback ได้) แต่ replay ส่งตัวที่สองมาเพื่อให้คำตัดสินที่จ่ายเงินซื้อมาแล้วรอดแม้ทรานแซกชัน
    ของแท่งนั้นถูกย้อนกลับ (ดูหมายเหตุใน `judge_side`)
    """
    cfg, settings = sym.cfg, ctx.settings
    market, symbol = cfg.market, cfg.symbol

    # ── 1 · แท่งที่ปิดแล้ว ──────────────────────────────────────────────────
    bars = sym.bars.bars(symbol, ctx.timeframe)
    if len(bars) < MIN_CLOSED_BARS:
        log.warning(
            "ข้าม %s %s: มีแท่งปิดแล้ว %d จาก %d ที่ต้องมี",
            market, symbol, len(bars), MIN_CLOSED_BARS,
        )
        return None
    bar_close_ts = bars[-1].close_ts

    # ── 2 · Action Zone ─────────────────────────────────────────────────────
    zone = action_zones(bars)[-1]

    # ── 3 · สถานะจริงจาก exchange — ก่อนตัดสินใจทุกอย่าง ─────────────────────
    positions = sym.broker.positions()
    position = next((p for p in positions if p.symbol == symbol), None)
    open_orders = sym.broker.open_orders(symbol)
    position_side = position.side if position else None
    day_pnl_pct = sym.day.pct(sym.broker, positions, bar_close_ts)

    # ── Slow Trail — ก่อนขั้น 4 และทำแม้ kill switch latch อยู่ ─────────────
    trail_slow = _stop_px(cdc_trailing_stop(bars)[-1].slow)
    stop = _slow_trail(conn, ctx, sym, position, open_orders, trail_slow, bar_close_ts)

    # ── 4–7 · แผนของแท่ง ───────────────────────────────────────────────────
    allow_short = settings.allow_short and cfg.allow_short
    plan = decide(
        long_signal=zone.long_signal,
        short_signal=zone.short_signal,
        state=zone.state,
        position_side=position_side,
        market=market,
        allow_short=allow_short,
    )

    fields: dict[str, object] = {}
    verdicts: tuple[Verdict, ...] = ()
    feat = None  # คำนวณเมื่อจำเป็นเท่านั้น: features() ไม่ใช่ของถูก

    open_side = plan.open_side
    skip_reason = plan.skip_reason
    size_rule: str | None = None
    factors_present = 0
    cold_stop_px: float | None = None

    # ── cold start: รอบแรกของ run เท่านั้น (spec/08 §cold start) ────────────
    if plan.skip_reason == "cane_rule" and sym.cold_start_pending:
        feat = features(bars)
        cold = late_entry(
            plan,
            route=settings.cold_start,
            state=zone.state,
            position_side=position_side,
            allow_short=allow_short,
            feat=feat,
            trail_slow=trail_slow,
        )
        fields["cold_start"] = settings.cold_start
        if cold.side is not None and cold.route == "trailing":
            open_side, skip_reason, size_rule = cold.side, None, "cold_start"
            cold_stop_px = cold.stop_px
            fields["judge_called"] = False
        elif cold.side is not None:
            # `wait_1h` ยังไม่ถูกสร้าง — บันทึกว่าตั้งใจให้รอ ไม่ใช่ตกรถเงียบๆ
            skip_reason = "cane_rule"
        else:
            skip_reason = cold.skip_reason

    # ── 8–9 · features + Judge (เฉพาะไม้ปกติที่มีขาเปิด) ─────────────────────
    if plan.needs_judge:
        feat = feat or features(bars)
        judged = judge_side(
            judge_conn if judge_conn is not None else conn,
            ctx.judge,
            market=market,
            symbol=symbol,
            timeframe=ctx.timeframe,
            bars=bars,
            feat=feat,
            side=plan.open_side,
            model_id=ctx.model_id,
        )
        factors_present, size_rule = judged.factors_present, "confluence"
        verdicts = tuple(
            Verdict(
                factor=v.factor,
                side=v.side,
                present=v.present,
                cached=cached,
                confidence=v.confidence,
                evidence_bars=v.evidence_bars,
                rationale=v.rationale,
            )
            for v, cached in zip(judged.verdicts, judged.cached, strict=True)
        )
        fields.update(
            judge_called=True,
            llm_fallback=judged.fallback,
            llm_fallback_reason=judged.fallback_reason,
            prompt_hash=judged.prompt_hash,
        )

    # ── 10–12 · ขนาดไม้ → risk (ยังไม่แตะ exchange) ─────────────────────────
    size: SizeDecision | None = None
    refused: OrderAttempt | None = None
    risk_checks: tuple = ()
    lot = ctx.lots.lot(market, symbol)
    if open_side is not None:
        fields.update(side=open_side, size_rule=size_rule)
        size = _size(ctx, cfg, open_side, factors_present, zone.close_px, lot)
        fields.update(
            factors_present=size.factors_present,
            size_pct_formula=size.size_pct_formula,
            size_pct_final=size.size_pct_final,
            capped=size.capped,
            margin=size.margin,
            notional=size.notional,
            qty=size.qty,
            ref_px=size.ref_px,
            leverage=size.leverage,
        )
        if market != "spot":
            fields["margin_mode"] = settings.broker.margin_mode
        if not size.sendable:
            # `order_error` ต้องมีออเดอร์เปิดที่มี `error` รองรับ (`validate_record`) — ไม้ที่คำนวณได้แต่ส่งไม่ได้
            # จึงลงเป็นความพยายามที่ไม่ได้ส่ง พร้อมเหตุผลของสูตร
            skip_reason = "order_error"
            open_side_order = _open_side_order_id(cfg.symbol, open_side, bar_close_ts)
            refused = OrderAttempt(
                leg="open",
                order_side=open_side_order[0],
                order_type="market",
                reduce_only=False,
                qty=size.qty,
                client_order_id=open_side_order[1],
                sent=False,
                accepted=False,
                error=size.refused_reason,
            )
        else:
            verdict = check_all(
                conn,
                profile=ctx.profile,
                market=market,
                day_pnl_pct=day_pnl_pct,
                max_daily_loss_pct=settings.risk.max_daily_loss_pct,
                entry_px=size.ref_px,
                liquidation_px=_liquidation(ctx, cfg, open_side, size),
                min_liq_buffer_pct=settings.risk.min_liq_buffer_pct,
            )
            risk_checks = verdict.checks
            if not verdict.passed:
                skip_reason = "risk_rejected"
            elif ctx.dry_run_blocks:
                skip_reason = "dry_run"

    # ── 13 · ขา 1 (ปิด) ก่อนขา 2 (เปิด) เสมอ ────────────────────────────────
    legs = _Legs(skip_reason=skip_reason, stop=stop)
    if refused is not None:
        legs.orders.append(refused)
    send_open = open_side is not None and skip_reason is None
    if not ctx.dry_run_blocks:
        _execute(ctx, sym, plan, position, open_side, size, lot, send_open, cold_stop_px, bar_close_ts, legs)
    else:
        _record_unsent(plan, position, open_side, size, sym, bar_close_ts, legs)

    unmanaged = legs.unmanaged or _carried_unmanaged(conn, ctx, sym, position, plan)

    # ── 14 · เขียนบันทึก — ทุกเส้นทางที่มาถึงตรงนี้ ─────────────────────────
    record = DecisionRecord(
        profile=ctx.profile,
        market=market,
        symbol=symbol,
        timeframe=ctx.timeframe,
        bar_close_ts=bar_close_ts,
        decided_ts=ctx.now(),
        config_version_id=ctx.config_version_id,
        close_px=zone.close_px,
        zone=zone.zone,
        state=zone.state,
        long_signal=zone.long_signal,
        short_signal=zone.short_signal,
        dry_run=settings.dry_run,
        skip_reason=legs.skip_reason,
        verdicts=verdicts,
        risk_checks=risk_checks,
        orders=tuple(legs.orders),
        unmanaged=tuple(unmanaged),
        flip=legs.flip,
        stop=legs.stop,
        **fields,
    )
    record_id = decisions_repo.insert_decision(conn, record)
    sym.cold_start_pending = False
    return replace(record, id=record_id)


# ── ตัวช่วย ────────────────────────────────────────────────────────────────


def _stop_px(px: float | None) -> float | None:
    """ปัด Trail2 เป็นราคาที่ **ส่งจริง** ก่อนใช้ — ตารางเก็บได้ `PRICE_SCALE` ตำแหน่งและปฏิเสธที่ละเอียดกว่า

    ปัดที่ต้นทาง ไม่ใช่ตอนบันทึก เพื่อให้ราคาที่วางที่ exchange กับราคาที่บันทึกเป็นตัวเดียวกัน (ปัดตอนบันทึกทำให้
    บันทึกบอกราคาที่ไม่เคยถูกส่ง) · ยังไม่ใช่ tick size ของ venue — ตัวนั้นมากับ broker ของ live (ใบ 13)
    """
    return None if px is None else round(px, PRICE_SCALE)


def _open_side_order_id(symbol: str, open_side: str, bar_close_ts: int) -> tuple[str, str]:
    """(ฝั่งของออเดอร์, client_order_id) ของขาเปิด — ใช้ทั้งตอนส่งจริงและตอนบันทึกขาที่ส่งไม่ได้"""
    side = _order_side(open_side, closing=False)
    return side, client_order_id(symbol, bar_close_ts, side, "open")


def _order_side(position_side: str, *, closing: bool) -> str:
    """ฝั่งของ **ออเดอร์** — ปิด long คือขาย · เปิด long คือซื้อ (ตรงข้ามกันสำหรับ short)"""
    buying = (position_side == "long") != closing
    return "buy" if buying else "sell"


def _size(
    ctx: RunContext, cfg: SymbolConfig, side: str, factors_present: int, ref_px: float, lot: LotFilter
) -> SizeDecision:
    bucket = cfg.bucket_quote_long if side == "long" else cfg.bucket_quote_short
    if bucket is None:
        # config ที่เปิด short ต้องมี bucket ของตัวเอง (`validate.py`) — มาถึงนี่แปลว่าด่านนั้นรั่ว ห้ามเดา
        raise ValueError(f"{cfg.symbol} ไม่มี bucket ฝั่ง {side} แต่กำลังจะเปิดไม้ฝั่งนั้น")
    risk = ctx.settings.risk
    return plan_size(
        base_pct=ctx.settings.base_pct,
        factors_present=factors_present,
        bucket_quote=bucket,
        max_position_pct=risk.max_position_pct_long if side == "long" else risk.max_position_pct_short,
        leverage=cfg.leverage,
        ref_px=ref_px,
        lot=lot,
    )


def _liquidation(ctx: RunContext, cfg: SymbolConfig, side: str, size: SizeDecision) -> float | None:
    """ราคา liquidation ของไม้ที่ **ยังไม่เปิด** · spot ไม่มี · ไม่รู้ค่า maintenance margin = `None` (ด่านปฏิเสธเอง)"""
    if cfg.market == "spot":
        return None
    mmr = ctx.settings.broker.maintenance_margin_pct
    if mmr is None:
        return None
    return liq_price(side, size.ref_px, size.leverage, mmr)


def _slow_trail(
    conn: Connection,
    ctx: RunContext,
    sym: SymbolRuntime,
    position: Position | None,
    open_orders: list[OpenOrder],
    trail_slow: float | None,
    bar_close_ts: int,
) -> Stop | None:
    """เลื่อน stop ของไม้ cold start ทางที่ 2 ตาม Trail2 · stop ที่ควรมีแต่หายไป = บันทึกว่าหาย ไม่วางทับ

    "ควรมี" อ่านจากบันทึกล่าสุดที่เข้าไม้จริง: ถ้ามันคือ cold start `trailing` ฝั่งเดียวกับไม้ที่ถืออยู่ ไม้นี้ต้องมี stop
    ที่ exchange (ADR 17) · ไม่มี flag บนดิสก์ (spec/08 §cold start)
    """
    if position is None:
        return None
    existing = next((o for o in open_orders if o.type == "stop_market" and o.reduce_only), None)
    if existing is None:
        entry = decisions_repo.last_entry(
            conn, ctx.profile, sym.cfg.market, sym.cfg.symbol, ctx.timeframe
        )
        expected = entry is not None and entry.cold_start == "trailing" and entry.side == position.side
        return Stop(action="missing") if expected else None
    if trail_slow is None:
        return None
    action = maintain_stop(
        sym.broker,
        symbol=sym.cfg.symbol,
        side=position.side,
        qty=position.qty,
        stop_px=trail_slow,
        bar_close_ts=bar_close_ts,
        existing=existing,
    )
    return Stop(action=action.action, px=action.stop_px, stop_order_id=action.order_id)


def _attempt(order: Order, leg: str, result: OrderResult | None, error: str | None = None, *, sent: bool = True) -> OrderAttempt:
    return OrderAttempt(
        leg=leg,
        order_side=order.side,
        order_type=order.type,
        reduce_only=order.reduce_only,
        qty=order.qty,
        client_order_id=order.client_order_id,
        sent=sent,
        accepted=result is not None and result.status in _ACCEPTED,
        stop_px=order.stop_px,
        venue_order_id=None if result is None else result.venue_order_id,
        error=error,
    )


def _build_orders(
    sym: SymbolRuntime,
    position: Position | None,
    plan: BarPlan,
    open_side: str | None,
    size: SizeDecision | None,
    bar_close_ts: int,
) -> tuple[Order | None, Order | None]:
    market, symbol = sym.cfg.market, sym.cfg.symbol
    close_order = None
    if plan.close_side is not None:
        assert position is not None and position.side == plan.close_side
        side = _order_side(plan.close_side, closing=True)
        close_order = Order(
            symbol=symbol,
            side=side,
            type="market",
            qty=position.qty,
            client_order_id=client_order_id(symbol, bar_close_ts, side, "close"),
            reduce_only=market != "spot",  # spot ไม่มี reduceOnly (spec/06)
        )
    open_order = None
    if open_side is not None and size is not None and size.sendable:
        side = _order_side(open_side, closing=False)
        open_order = Order(
            symbol=symbol,
            side=side,
            type="market",
            qty=size.qty,
            client_order_id=client_order_id(symbol, bar_close_ts, side, "open"),
        )
    return close_order, open_order


def _record_unsent(
    plan: BarPlan,
    position: Position | None,
    open_side: str | None,
    size: SizeDecision | None,
    sym: SymbolRuntime,
    bar_close_ts: int,
    legs: _Legs,
) -> None:
    """`dry_run` บน broker จริง: คำนวณครบ เขียนครบ **ไม่ส่งสักขา** — ทั้งขาปิดด้วย (spec/06 §dry_run)"""
    close_order, open_order = _build_orders(sym, position, plan, open_side, size, bar_close_ts)
    for order, leg in ((close_order, "close"), (open_order, "open")):
        if order is not None:
            legs.orders.append(_attempt(order, leg, None, sent=False))


def _execute(
    ctx: RunContext,
    sym: SymbolRuntime,
    plan: BarPlan,
    position: Position | None,
    open_side: str | None,
    size: SizeDecision | None,
    lot: LotFilter,
    send_open: bool,
    cold_stop_px: float | None,
    bar_close_ts: int,
    legs: _Legs,
) -> None:
    """ส่งขาปิด/ขาเปิด/stop ตามที่ตัดสินไว้ · แตะ exchange ที่นี่ที่เดียว"""
    close_order, open_order = _build_orders(sym, position, plan, open_side, size, bar_close_ts)
    broker, cfg = sym.broker, sym.cfg

    if send_open and cfg.market != "spot":
        # leverage กับ margin mode ที่ exchange ถูกเปลี่ยนจากนอกระบบได้ — ตั้งให้ตรง config ก่อนเปิดไม้ทุกครั้ง
        broker.set_leverage(cfg.symbol, cfg.leverage)
        broker.set_margin_mode(
            cfg.symbol, ctx.settings.broker.margin_mode, ctx.settings.broker.position_mode
        )

    if close_order is not None and send_open and open_order is not None:
        _flip(broker, close_order, open_order, position, lot, bar_close_ts, legs)
    elif close_order is not None:
        _close_only(broker, close_order, position, lot, bar_close_ts, legs)
    elif send_open and open_order is not None:
        try:
            result = broker.place(open_order)
        except Exception as error:  # noqa: BLE001 — จบด้วย order_error ตาม ADR 6; ขั้น 3 แท่งหน้าอ่านของจริงเอง
            log.exception("ส่งขาเปิดของ %s ไม่สำเร็จ", cfg.symbol)
            legs.orders.append(_attempt(open_order, "open", None, repr(error)))
            legs.skip_reason = "order_error"
        else:
            _opened(legs, open_order, result)

    if legs.opened_qty > 0 and cold_stop_px is not None and open_side is not None:
        _cold_stop(broker, cfg.symbol, open_side, legs, cold_stop_px, bar_close_ts)


def _opened(legs: _Legs, order: Order, result: OrderResult) -> None:
    attempt = _attempt(order, "open", result)
    legs.orders.append(attempt)
    if attempt.accepted:
        legs.skip_reason = None
        legs.opened_qty = result.filled_qty
    else:
        legs.skip_reason = "order_error"
        legs.orders[-1] = replace(attempt, error=f"venue ไม่รับ: status={result.status!r}")


def _flip(
    broker: Broker,
    close_order: Order,
    open_order: Order,
    position: Position,
    lot: LotFilter,
    bar_close_ts: int,
    legs: _Legs,
) -> None:
    try:
        flipped = execute_flip(broker, close_order=close_order, open_order=open_order, step=lot.step)
    except Exception as error:  # noqa: BLE001 — ไม่รู้ว่าขาไหนไปถึงไหน: บันทึกความล้มเหลวแล้วให้ขั้น 3 อ่านของจริง
        log.exception("flip ของ %s ล้มกลางทาง", close_order.symbol)
        legs.orders.append(_attempt(close_order, "close", None, repr(error)))
        legs.orders.append(_attempt(open_order, "open", None, f"flip ล้มกลางทาง: {error!r}", sent=False))
        legs.skip_reason = "order_error"
        return
    legs.orders.append(_attempt(close_order, "close", flipped.close))
    legs.flip = Flip(
        close_qty_intended=flipped.close_qty_intended,
        close_qty_filled=flipped.close_qty_filled,
        residual_qty=flipped.residual_qty,
        aborted=flipped.aborted,
        residual_side=position.side if flipped.residual_qty > 0 else None,
    )
    if flipped.aborted:
        legs.skip_reason = "flip_aborted"
        legs.orders.append(_attempt(open_order, "open", None, sent=False))
        legs.unmanaged.append(
            Unmanaged(
                side=position.side,
                qty=flipped.residual_qty,
                source="flip_aborted",
                first_seen_bar_close_ts=bar_close_ts,
            )
        )
        return
    assert flipped.open is not None
    _opened(legs, open_order, flipped.open)


def _close_only(
    broker: Broker,
    close_order: Order,
    position: Position,
    lot: LotFilter,
    bar_close_ts: int,
    legs: _Legs,
) -> None:
    """ปิดสถานะเดิมตามสัญญาณ โดยไม่มีขาเปิดตาม — ขาเปิดถูก risk ปฏิเสธ, short ปิดไว้ หรือเป็น spot"""
    try:
        result = broker.place(close_order)
    except Exception as error:  # noqa: BLE001
        log.exception("ส่งขาปิดของ %s ไม่สำเร็จ", close_order.symbol)
        legs.orders.append(_attempt(close_order, "close", None, repr(error)))
        return
    legs.orders.append(_attempt(close_order, "close", result))
    residual = close_order.qty - result.filled_qty
    if residual >= lot.step:
        legs.unmanaged.append(
            Unmanaged(
                side=position.side,
                qty=residual,
                source="close_partial",
                first_seen_bar_close_ts=bar_close_ts,
            )
        )


def _cold_stop(
    broker: Broker, symbol: str, side: str, legs: _Legs, stop_px: float, bar_close_ts: int
) -> None:
    """วาง stop ของ cold start ทางที่ 2 ทันทีหลังเปิดไม้ (ADR 17) · วางไม่สำเร็จ = ไม้ไม่มี stop ซึ่งต้องดังทุกแท่ง"""
    order_side = _order_side(side, closing=True)
    order = Order(
        symbol=symbol,
        side=order_side,
        type="stop_market",
        qty=legs.opened_qty,
        client_order_id=client_order_id(symbol, bar_close_ts, order_side, "stop"),
        reduce_only=True,
        stop_px=stop_px,
    )
    try:
        action = maintain_stop(
            broker,
            symbol=symbol,
            side=side,
            qty=legs.opened_qty,
            stop_px=stop_px,
            bar_close_ts=bar_close_ts,
            existing=None,
        )
    except Exception as error:  # noqa: BLE001
        log.exception("วาง stop ของ %s ไม่สำเร็จ — ไม้นี้ไม่มี stop คุ้มอยู่", symbol)
        legs.orders.append(_attempt(order, "stop", None, repr(error)))
        legs.stop = Stop(action="missing")
        return
    legs.orders.append(_attempt(order, "stop", action.result))
    legs.stop = Stop(action=action.action, px=action.stop_px, stop_order_id=action.order_id)


def _carried_unmanaged(
    conn: Connection,
    ctx: RunContext,
    sym: SymbolRuntime,
    position: Position | None,
    plan: BarPlan,
) -> list[Unmanaged]:
    """ของค้างจากแท่งก่อนที่ยังอยู่ — เขียนซ้ำทุกแท่งจนกว่าคนจะปิด (spec/08 §กฎที่ห้ามผิดลำดับ · ADR 19)

    ยกมาเฉพาะเมื่อไม้ที่ถืออยู่ตอนนี้ยังเป็นฝั่งเดียวกับของค้าง **และแท่งนี้ไม่ได้ปิดมัน** — ถ้าแท่งนี้มีสัญญาณ
    ตรงข้ามแล้วปิดสำเร็จ ของค้างหมดไปแล้ว การเขียนต่อคือบอกว่ามีของค้างทั้งที่ไม่มี

    `qty` คือ **ขนาดของไม้ที่ถืออยู่ตอนนี้** ไม่ใช่ `qty` ที่บันทึกไว้ตอนเกิดของค้าง: หลัง flip abort ไม้ที่เหลือ
    ทั้งไม้คือของค้าง (ขาปิดไม่ fill เลยหรือ fill บางส่วน) และสิ่งที่คนต้องไปปิดคือไม้ตามที่ exchange ถือจริง ·
    ถ้าไม้ถูกลดจากนอกระบบ ตัวเลขนี้ตามไปด้วยโดยตั้งใจ
    """
    if position is None or plan.close_side is not None:
        return []
    previous = decisions_repo.latest_decision(
        conn, ctx.profile, sym.cfg.market, sym.cfg.symbol, ctx.timeframe
    )
    if previous is None:
        return []
    return [
        Unmanaged(
            side=u.side,
            qty=position.qty,
            source=u.source,
            first_seen_bar_close_ts=u.first_seen_bar_close_ts,
        )
        for u in previous.unmanaged
        if u.side == position.side
    ]
