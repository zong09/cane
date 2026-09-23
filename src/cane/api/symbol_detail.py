"""หน้า เหรียญ — header + แท็บ กราฟ และ การตัดสินใจ ของแท่งล่าสุด (ใบ 25 · handoff §9.2)

## อ่านจาก `decisions` ที่เขียนไว้แล้วเท่านั้น

แท็บนี้ตอบว่า "แท่งล่าสุดระบบตัดสินอะไร เพราะอะไร" · คำตอบคือแถวที่ engine เขียนไว้ พร้อม
ตารางลูกครบหกตัว (`latest_decision()`) · คอนโซลไม่คำนวณการตัดสินใจซ้ำ — ถ้าคำนวณใหม่แล้วได้
ไม่ตรงกับที่เขียนไว้ หน้าจอจะโกหกเรื่องที่ระบบทำไปแล้ว

**ค่าคงที่ของสูตรอ่านจากเวอร์ชันที่ตัดสินแท่งนั้น** (`settings_of(config_version_id)`) ไม่ใช่
เวอร์ชันที่ active · `base_pct` หรือเพดานที่แก้ไปหลังแท่งนั้นต้องไม่ย้อนมาเขียนที่มาของขนาดไม้ใหม่

## สิ่งที่ไฟล์ design เขียนไว้แต่ระบบนี้ไม่มี และห้ามลอกเข้ามา

| ในไฟล์ design | ทำไมไม่มี |
| --- | --- |
| ชิป `golden test 500/500` ที่ header | เป็นเกณฑ์ยืนยันของใบ Action Zone (เทสต์) ไม่ใช่สถานะของเหรียญ |
| บรรทัด `ครบ 3 → 100` ในที่มาของขนาดไม้ | spec/05 §ตัวเลข 80–100 ในเอกสารต้นทาง — ตัดสินแล้ว ปฏิเสธกรณีพิเศษนี้ · สูตรคือ `base + 20n` หนีบที่ 100 |
| ชิป `temperature 0` | ไม่ได้เก็บต่อแถว · แสดงจำนวนคำตัดสินที่มาจาก cache แทน |
| `ราคา liquidation` เป็นตัวเลขที่ venue ให้ | แถวเก็บระยะห่างเป็น % (`liq_buffer`) · ราคาในหน้านี้คำนวณกลับจาก `ref_px` และติดป้ายว่าคำนวณ |
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api import context
from cane.api import symbol_chart, symbol_coldstart
from cane.api.deps import (
    client_ip,
    current_mode,
    current_user,
    get_db,
    get_sup,
    require_cap,
    require_profile,
)
from cane.api.log import SKIP_TEXT
from cane.api.templating import templates
from cane.config.settings import Settings, SymbolConfig
from cane.config.validate import ConfigError
from cane.confluence.schema import FACTORS_BY_SIDE
from cane.db.repo import audit, coldstart_intent, enginestate
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import ledger as ledger_repo
from cane.db.repo import permissions as perms
from cane.db.repo import report as report_repo
from cane.db.repo.decisions import DecisionRecord
from cane.db.repo.users import User
from cane.db.types import now_ms, store_symbol
from cane.engine.state import RUNNING, derive_status
from cane.engine.supervisor import Supervisor
from cane.sizing.matrix import FACTOR_STEP_PCT, FORMULA_CEILING_PCT

router = APIRouter()
log = logging.getLogger(__name__)

UNKNOWN = "—"

TABS = (("chart", "กราฟและสัญญาณ"), ("decision", "การตัดสินใจ"), ("coldstart", "Cold start"))

#: ชื่อไทยของ factor ตาม handoff §9.2b · ความหมายเต็มอยู่ที่ spec/04 §หก factor — สามต่อฝั่ง
FACTOR_TEXT = {
    "CHANNEL_BREAKOUT": "เบรคเส้นแนวโน้มกด",
    "RETAIL_CAPITULATION": "รายย่อยโยนของทิ้ง",
    "HIGHER_LOW": "จุดต่ำสุดใหม่สูงกว่าเดิม",
    "CHANNEL_BREAKDOWN": "หลุดเส้นแนวโน้มรับ",
    "BUYING_EXHAUSTION": "แรงซื้อหมดแรงที่ยอด",
    "LOWER_HIGH": "จุดสูงสุดใหม่ต่ำกว่าเดิม",
}

MARKET_TEXT = {"usdtm_perp": "USDT-M perp", "spot": "spot"}

_OPPOSITE = {"long": "short", "short": "long"}


def _utc(ts: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return f"{datetime.fromtimestamp(ts / 1000, tz=UTC):{fmt}}"


def _num(value: float | None, fmt: str = "g") -> str:
    return UNKNOWN if value is None else format(value, fmt)


def _px(value: float | None) -> str:
    if value is None:
        return UNKNOWN
    return f"{value:,.2f}" if value >= 1 else f"{value:.6g}"


def _find_symbol(settings: Settings | None, symbol: str, market: str | None) -> SymbolConfig | None:
    if settings is None:
        return None
    for sym in settings.symbols:
        if store_symbol(sym.symbol) == store_symbol(symbol) and market in (None, sym.market):
            return sym
    return None


# ── header ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Header:
    pair: str
    market: str
    zone: str
    #: `long` / `short` / `flat`
    side: str
    side_label: str
    sub: str
    long_signal: bool
    short_signal: bool


def _held_label(
    conn: Connection, settings: Settings, trade: report_repo.OpenTrade | None
) -> str:
    """`LONG 25% · margin 25.00` ตาม handoff §9.2 + §15 ข้อ 6 (% ของ bucket ต้องมาพร้อม margin)

    % คือ `size_pct_final` ของแถวที่เปิดไม้นั้น ไม่ใช่ margin หารด้วย bucket ของวันนี้ —
    bucket ที่แก้หลังเปิดไม้ต้องไม่เปลี่ยนตัวเลขของไม้ที่ถืออยู่ · หาแถวไม่เจอ = ขึ้นปริมาณแทน
    """
    if trade is None:
        return "FLAT"
    opened = decisions_repo.decision_at(
        conn, settings.profile, trade.market, trade.symbol, settings.timeframe,
        trade.open_bar_close_ts,
    )
    margin = "" if trade.margin is None else f" · margin {trade.margin:.2f}"
    if opened is None or opened.size_pct_final is None:
        return f"{trade.side.upper()} {trade.qty:g}{margin}"
    return f"{trade.side.upper()} {opened.size_pct_final:g}%{margin}"


def _header(
    conn: Connection, settings: Settings, sym: SymbolConfig, record: DecisionRecord | None,
    held: list[report_repo.OpenTrade],
) -> Header:
    mine = next(
        (t for t in held if t.symbol == store_symbol(sym.symbol) and t.market == sym.market), None
    )
    side = mine.side if mine else "flat"
    label = _held_label(conn, settings, mine)
    parts = [settings.timeframe]
    venue = settings.broker.exchange or settings.data.exchange
    parts.append(f"{venue} {MARKET_TEXT.get(sym.market, sym.market)}")
    if sym.market == "usdtm_perp":
        parts.append(f"{settings.broker.margin_mode} {sym.leverage:g}x")
    bucket = f"bucket long {sym.bucket_quote_long:.2f}"
    if sym.bucket_quote_short is not None:
        bucket += f" / short {sym.bucket_quote_short:.2f}"
    parts.append(f"{bucket} USDT")
    if record is not None:
        parts.append(f"แท่ง {_utc(record.bar_close_ts)} UTC")
    return Header(
        pair=sym.symbol,
        market=sym.market,
        zone=record.zone if record else context.ZONE_UNKNOWN,
        side=side,
        side_label=label,
        sub=" · ".join(parts),
        long_signal=bool(record and record.long_signal),
        short_signal=bool(record and record.short_signal),
    )


# ── แท็บ การตัดสินใจ ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Factor:
    name: str
    present: bool
    confidence: str
    rationale: str
    evidence: str


@dataclass(frozen=True, slots=True)
class Gate:
    label: str
    passed: bool


@dataclass(frozen=True, slots=True)
class LedgerLine:
    label: str
    value: str
    faint: bool = False
    total: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    """ทุกอย่างที่แท็บต้องแสดง · `case` เลือกโครงของหน้า

    - `entry` — เปิดไม้ (กรณี A) หรือกลับข้าง (กรณี B ถ้า `flip` มีค่า)
    - `close_only` — มีสัญญาณฝั่งตรงข้ามแต่เปิดฝั่งใหม่ไม่ได้ (กรณี C)
    - `rejected` — มีสัญญาณแต่ไม่เกิดไม้ (risk / ออเดอร์ / flip ไม่ครบ / RR)
    - `quiet` — ไม่ใช่แท่งสัญญาณ (กรณี D)
    """

    case: str
    side: str
    hero: str
    hero_sub: str
    dry_run: bool
    size_pct: str
    signal: str
    ref_px: str
    client_order_id: str
    factors: tuple[Factor, ...]
    factors_present: int
    cached: int
    judge_called: bool
    llm_fallback: bool
    gates: tuple[Gate, ...]
    ledger: tuple[LedgerLine, ...]
    used: tuple[str, str, str] | None
    liq: str
    flip: dict[str, str] | None
    close_leg: str
    close_result: str
    skipped_leg: str
    why_not: tuple[tuple[str, str], ...]
    record_short: str
    record_full: str
    spot: bool


def _factors(record: DecisionRecord, side: str) -> tuple[Factor, ...]:
    """สามช่องของฝั่งนั้นเสมอ เรียงตาม spec · ช่องที่ไม่มีคำตัดสินคือไม่ได้ถาม ไม่ใช่ไม่ผ่าน"""
    by_name = {v.factor: v for v in record.verdicts if v.side == side}
    out = []
    for name in FACTORS_BY_SIDE[side]:
        v = by_name.get(name)
        out.append(Factor(
            name=FACTOR_TEXT.get(name, name),
            present=bool(v and v.present),
            confidence=UNKNOWN if v is None or v.confidence is None else f"{v.confidence:.2f}",
            rationale=(v.rationale or "") if v else "",
            evidence=(
                "แท่งอ้างอิง " + " · ".join(_utc(ts, "%m-%d") for ts in v.evidence_bars)
                if v and v.evidence_bars else ""
            ),
        ))
    return tuple(out)


def _gates(record: DecisionRecord, settings: Settings | None) -> tuple[Gate, ...]:
    gates = [Gate("กฎไม้เรียว — แท่งสัญญาณ", record.long_signal or record.short_signal)]
    for check in record.risk_checks:
        if check.layer == "kill_switch":
            label = "kill switch — clear" if check.passed else "kill switch — latched"
        elif check.layer == "daily_loss":
            label = f"daily loss {_num(check.value, '.1f')} / {_num(check.limit_value, '.1f')}%"
        elif check.layer == "liq_buffer":
            label = (
                f"ห่าง liquidation {_num(check.value, '.1f')}% ≥ {_num(check.limit_value, 'g')}%"
                if check.value is not None else f"ห่าง liquidation — {check.detail or UNKNOWN}"
            )
        else:
            label = check.layer
        gates.append(Gate(label, check.passed))
    if record.leverage is not None and settings is not None and record.market == "usdtm_perp":
        cap = settings.risk.max_leverage
        gates.append(Gate(
            f"เลเวอเรจ {record.leverage:g}x ไม่เกินเพดาน {cap:g}x", record.leverage <= cap
        ))
    gates.append(Gate("สถานะจริงที่ exchange · ไม่มีของค้างนอกระบบ", not record.unmanaged))
    return tuple(gates)


def _ledger(record: DecisionRecord, settings: Settings | None, side: str) -> tuple[LedgerLine, ...]:
    """ที่มาของขนาดไม้ตาม spec/05 §สูตร · ไม่มีเวอร์ชันให้อ่าน `base_pct` = ไม่แต่งบรรทัดแรก"""
    if record.size_pct_formula is None:
        return ()
    lines = [LedgerLine(
        "ไม้พื้นฐาน",
        UNKNOWN if settings is None else f"{settings.base_pct:g}",
    )]
    if record.size_rule == "confluence":
        for factor in _factors(record, side):
            step = FACTOR_STEP_PCT if factor.present else 0.0
            lines.append(LedgerLine(factor.name, f"+{step:g}", faint=not factor.present))
    elif record.llm_fallback:
        lines.append(LedgerLine("LLM ตอบไม่ได้ — ตกไปที่ไม้พื้นฐาน", "+0", faint=True))
    lines.append(LedgerLine(
        f"สูตรให้ {record.size_pct_formula:g}%"
        + (f" (หนีบที่ {FORMULA_CEILING_PCT:g})" if record.size_pct_formula >= FORMULA_CEILING_PCT else ""),
        "", total=True,
    ))
    if settings is not None:
        cap = getattr(settings.risk, f"max_position_pct_{side}")
        lines.append(LedgerLine(
            f"เพดานต่อไม้ฝั่ง {side} {cap:g}% — " + ("ถูกตัด" if record.capped else "ไม่ถูกตัด"), ""
        ))
    return tuple(lines)


def _liq(record: DecisionRecord) -> str:
    """ราคา liquidation คำนวณกลับจาก `ref_px` กับระยะที่ชั้น `liq_buffer` บันทึกไว้"""
    check = next((c for c in record.risk_checks if c.layer == "liq_buffer"), None)
    if check is None or check.value is None or record.ref_px is None or record.side is None:
        return UNKNOWN
    sign = -1 if record.side == "long" else 1
    px = record.ref_px * (1 + sign * check.value / 100)
    return f"{_px(px)} · ห่าง {check.value:.1f}% (คำนวณจาก ref_px)"


def _closed_here(conn: Connection, record: DecisionRecord) -> ledger_repo.ClosedTrade | None:
    """ไม้ที่ขาปิดของแท่งนี้ปิดลง — อ่านจาก VIEW ไม่ใช่คิดเองจากออเดอร์"""
    for trade in ledger_repo.closed_trades(conn, record.profile):
        if (trade.symbol == store_symbol(record.symbol) and trade.market == record.market
                and trade.close_bar_close_ts == record.bar_close_ts):
            return trade
    return None


def _result(trade: ledger_repo.ClosedTrade | None) -> str:
    if trade is None:
        return UNKNOWN
    return f"{trade.net_pct:+.1f}% · {trade.net_quote:+.2f} USDT"


def _record_json(record: DecisionRecord) -> tuple[str, str]:
    full = asdict(record)
    short_keys = ("bar_close_ts", "zone", "state", "long_signal", "short_signal", "side",
                  "leverage", "margin_mode", "size_pct_final", "qty", "skip_reason", "dry_run")
    short = {k: full[k] for k in short_keys}
    if record.flip is not None:
        short["flip"] = {"close": _OPPOSITE.get(record.side or "", UNKNOWN),
                         "qty": record.flip.close_qty_filled}
    dump = lambda d: json.dumps(d, ensure_ascii=False, indent=1, default=str)  # noqa: E731
    return dump(short), dump(full)


def decision_view(
    conn: Connection, record: DecisionRecord, settings: Settings | None
) -> Decision:
    signal_side = "long" if record.long_signal else "short" if record.short_signal else ""
    entered = record.side is not None and record.skip_reason in (None, "dry_run")
    side = record.side or signal_side or "long"
    open_leg = next((o for o in record.orders if o.leg == "open"), None)
    close_leg = next((o for o in record.orders if o.leg == "close"), None)
    base = record.symbol.split("/")[0]
    closed = _closed_here(conn, record) if close_leg else None

    if entered:
        case = "entry"
        hero = f"{side.upper()} {_num(record.qty)} {base}"
        verb = "ปิด " + _OPPOSITE[side] + " แล้วเปิด " + side if record.flip else "เปิด " + side
        hero_sub = (
            f"{side} signal พร้อมปัจจัยสนับสนุน {record.factors_present or 0} จาก 3 · "
            + ("กลับข้างสองขาในแท่งเดียว" if record.flip else f"ไม่มีสถานะ {_OPPOSITE[side]} ค้างจึงไม่ต้องปิดก่อน")
            + f" · ผ่านกฎไม้เรียวและ risk ทุกข้อ · {verb} ที่แท่งถัดไป"
        )
    elif record.skip_reason == "short_disabled":
        case = "close_only"
        hero = (f"ปิด long {_num(close_leg.qty)} · ไม่เปิด short" if close_leg
                else "มี short signal แต่ฝั่ง short ปิดอยู่ · ไม่มีสถานะให้ปิด")
        hero_sub = (
            "ระบบปิดตามสัญญาณ แต่ไม่เรียก Confluence Judge และไม่คำนวณขนาดไม้ฝั่ง short"
        )
    elif signal_side:
        case = "rejected"
        hero = SKIP_TEXT.get(record.skip_reason or "", record.skip_reason or UNKNOWN)
        failed = next((c for c in record.risk_checks if not c.passed), None)
        hero_sub = (
            f"มี {signal_side} signal แต่ไม่เกิดไม้"
            + (f" · ชั้นที่ปฏิเสธ: {failed.layer}"
               + (f" ({_num(failed.value, '.2f')} เทียบเพดาน {_num(failed.limit_value, 'g')})"
                  if failed.value is not None else "")
               + (f" — {failed.detail}" if failed.detail else "")
               if failed else "")
        )
    else:
        case = "quiet"
        hero = "ไม่ทำอะไร"
        hero_sub = (
            "แท่งล่าสุดไม่มี long signal และไม่มี short signal — ไม่มีอะไรต้องเปิด และไม่มีสัญญาณ"
            "ฝั่งตรงข้ามที่ต้องปิดสถานะที่ถืออยู่ · ไม่เรียก Confluence Judge ไม่คำนวณขนาดไม้ "
            "แต่ยังเขียน DecisionRecord ตามปกติ"
        )

    flip = None
    if record.flip is not None and entered:
        flip = {
            "close_side": _OPPOSITE[side],
            "close_qty": _num(record.flip.close_qty_filled),
            "entry_px": _px(closed.entry_px) if closed else UNKNOWN,
            "result": _result(closed),
            "open_margin": _num(record.margin, ".2f"),
            "open_notional": _num(record.notional, ".2f"),
            "open_qty": _num(record.qty),
            "liq": _liq(record),
        }

    used = None
    if entered and record.size_pct_final is not None:
        used = (
            f"{record.size_pct_final:g}% · margin {_num(record.margin, '.2f')} USDT",
            f"notional {_num(record.notional, '.2f')} USDT ที่ {_num(record.leverage)}x · "
            f"{_num(record.qty)} {base} · ปัดลงตาม lot size",
            _liq(record),
        )

    why_not = (
        ("longcond / long signal", "true" if record.long_signal else "false"),
        ("shortcond / short signal", "true" if record.short_signal else "false"),
        ("state", record.state),
        ("สถานะที่ถือ", (record.side or "flat") if entered else UNKNOWN),
        ("กฎไม้เรียว", "ปฏิเสธทั้งสองฝั่ง — ไม่ใช่แท่งสัญญาณ"),
    )
    short, full = _record_json(record)
    return Decision(
        case=case,
        side=side,
        hero=hero,
        hero_sub=hero_sub,
        dry_run=record.dry_run,
        size_pct=UNKNOWN if record.size_pct_final is None else f"{record.size_pct_final:g}%",
        signal=f"เปิด {signal_side} · {record.zone}" if signal_side else f"— · zone {record.zone}",
        ref_px=f"{_px(record.ref_px or record.close_px)} {'ref' if record.ref_px else 'close'}",
        client_order_id=open_leg.client_order_id if open_leg else UNKNOWN,
        factors=_factors(record, side),
        factors_present=record.factors_present or 0,
        cached=sum(v.cached for v in record.verdicts),
        judge_called=bool(record.judge_called),
        llm_fallback=bool(record.llm_fallback),
        gates=_gates(record, settings),
        ledger=_ledger(record, settings, side),
        used=used,
        liq=_liq(record),
        flip=flip,
        close_leg=(
            f"ปิด {_OPPOSITE.get(signal_side, UNKNOWN)} {_num(close_leg.qty)} {base}"
            + (" · reduceOnly" if close_leg.reduce_only else "")
            if close_leg else UNKNOWN
        ),
        close_result=_result(closed),
        skipped_leg=(
            "เปิด short — spot ไม่มีฝั่ง short" if record.market == "spot"
            else "เปิด short — allow_short = false"
        ),
        spot=record.market == "spot",
        why_not=why_not,
        record_short=short,
        record_full=full,
    )


# ── หน้า ─────────────────────────────────────────────────────────────────────


def _settings(conn: Connection, profile: str) -> Settings | None:
    """config ที่ active · `None` = ไม่มีเวอร์ชันที่เปิดใช้

    **config ที่โหลดไม่ผ่านไม่ใช่ `None`** — ถ้ากลืนเป็น `None` หน้าจะตอบ 404 "ไม่มีคู่นี้" ซึ่งโกหก
    ว่าคู่นั้นไม่มีอยู่ ทั้งที่ความจริงคือ config พัง (รีวิว PR #33) · ตอบ 409 พร้อมจำนวนปัญหา
    และชี้ไปหน้าตั้งค่า แบบเดียวกับที่หน้าความเสี่ยงบอกไว้บนแบนเนอร์ · log ไว้ให้คนดูแลเห็นด้วย
    """
    try:
        return config_repo.active_settings(conn, profile)
    except ConfigError as exc:
        log.warning("config ของ %s โหลดไม่ผ่าน %d ข้อ — หน้าเหรียญเปิดไม่ได้", profile, len(exc.problems))
        raise HTTPException(
            status_code=409,
            detail=f"config ของโปรไฟล์ {profile} โหลดไม่ผ่าน {len(exc.problems)} ข้อ — แก้ได้ที่หน้าตั้งค่า",
        ) from exc


def _engine_decides(settings: Settings) -> bool:
    """engine ของคอนโซลตัดสินใจเฉพาะ broker ที่ส่งคำสั่งได้ (`engine/live.py`) · paper เดินด้วย replay
    ซึ่งไม่อ่านเจตนา (ADR 35) — โปรไฟล์ที่ engine ไม่ตัดสินใจเลือกเจตนาไม่ได้"""
    return settings.broker.kind == "ccxt"


def page_context(
    conn: Connection, *, profile: str, symbol: str, market: str | None, tab: str, user: User,
    notice: str = "",
) -> dict[str, object]:
    """ทุกอย่างที่ `partials/symbol_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `sd_`

    เหรียญที่ไม่อยู่ใน config ที่ active คือ 404 — หน้านี้ผูกกับ bucket และ leverage ของมัน
    """
    settings = _settings(conn, profile)
    sym = _find_symbol(settings, symbol, market)
    if settings is None or sym is None:
        raise HTTPException(status_code=404, detail=f"ไม่มีคู่ {symbol!r} ในโปรไฟล์ {profile}")
    record = decisions_repo.latest_decision(conn, profile, sym.market, sym.symbol, settings.timeframe)
    held = report_repo.open_trades(conn, profile)
    decided_by = (
        None if record is None else config_repo.settings_of(conn, record.config_version_id)
    )
    tab = tab if tab in dict(TABS) else "chart"
    header = _header(conn, settings, sym, record, held)
    intent = coldstart_intent.read(conn, profile=profile, market=sym.market, symbol=sym.symbol)
    # จุดเขียวที่แท็บ Cold start ต้องรู้ผลทุกแท็บ ไม่ใช่เฉพาะตอนเปิดแท็บนั้น
    ctx: dict[str, object] = symbol_coldstart.coldstart_context(
        conn, settings=settings, sym=sym, held_side=header.side,
        intent_route=None if intent is None else intent.route,
    )
    if tab == "chart":
        ctx |= symbol_chart.chart_context(
            conn, profile=profile, settings=settings, sym=sym, record=record,
            held_side=header.side, held_label=header.side_label,
        )
    engine = derive_status(enginestate.read(conn, profile), now=now_ms())
    return ctx | {
        # ADR 35 · เจตนาที่รอ run ถัดไป กับว่ามันจะไม่มีผลกับ run ที่กำลังเดินอยู่
        "cs_intent": intent,
        "cs_intent_at": None if intent is None else _utc(intent.chosen_ts),
        "cs_engine_running": engine == RUNNING,
        "cs_can_choose": perms.allowed(conn, role=user.role, cap="choose_cold_start_route"),
        "cs_honoured": _engine_decides(settings),
        "cs_notice": notice,
        "sd_profile": profile,
        "sd_header": header,
        "sd_tabs": TABS,
        "sd_tab": tab,
        "sd_query": f"market={sym.market}",
        "sd_decision": None if record is None else decision_view(conn, record, decided_by),
    }


@router.get("/symbols/{symbol:path}", response_class=HTMLResponse)
def page(
    symbol: str,
    request: Request,
    market: str | None = Query(None),
    tab: str = Query("chart"),
    db: Engine = Depends(get_db),
    sup: Supervisor = Depends(get_sup),
    user: User = Depends(current_user),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    with db.connect() as conn:
        # หน้านี้เป็นเปลือกของ partial ที่อ่านบันทึก — ขอสิทธิ์เดียวกับหน้าบันทึก
        if not perms.allowed(conn, role=user.role, cap="read_decisions"):
            raise HTTPException(status_code=403, detail="ต้องมีสิทธิ์ read_decisions")
        ctx = context.build(conn, sup, user=user, mode=mode, active="")
        ctx |= page_context(conn, profile=mode, symbol=symbol, market=market, tab=tab, user=user)
    return templates.TemplateResponse(request, "pages/symbol.html", ctx)


@router.get("/partials/symbol/{symbol:path}", response_class=HTMLResponse)
def body(
    symbol: str,
    request: Request,
    market: str | None = Query(None),
    tab: str = Query("chart"),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("read_decisions")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้าเหรียญ — ตอนสลับแท็บและตอนสลับโหมด

    สลับโหมดแล้วเหรียญนี้ไม่มีในอีกโปรไฟล์ = 404 ของ partial · htmx ไม่ swap คำตอบ 4xx
    หน้าจึงค้างที่เนื้อเดิมพร้อมแถบบนที่บอกโหมดใหม่ ซึ่งดีกว่าเนื้อว่าง
    """
    with db.connect() as conn:
        ctx = page_context(conn, profile=mode, symbol=symbol, market=market, tab=tab, user=user)
    return templates.TemplateResponse(request, "partials/symbol_body.html", ctx)


@router.post("/api/{profile}/coldstart/{symbol:path}", response_class=HTMLResponse)
def choose_route(
    profile: str,
    symbol: str,
    request: Request,
    route: str = Form(...),
    market: str | None = Form(None),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("choose_cold_start_route")),
) -> HTMLResponse:
    """เลือกเส้นทาง cold start ของ run ถัดไป (ADR 35) · ไม่ต้อง step-up (spec/09 §4. endpoint → สิทธิ์ที่ต้องมี)

    - `wait_1h` = **422** ตราบที่ engine ยังไม่สร้างทางนี้ (ADR 35 §ตัดสินแล้ว) · ค่าอื่นที่ไม่รู้จักก็ 422
    - เลือกซ้ำค่าเดิม = no-op 200 ไม่ขยับเวลาและไม่เขียน audit · เลือกค่าใหม่ = ทับ พร้อม audit
    - คืนเนื้อของหน้าเหรียญที่แท็บ Cold start ของ profile ใน path — ปุ่มอยู่บนหน้านั้น
    """
    target = require_profile(profile)
    if route == "wait_1h":
        raise HTTPException(status_code=422, detail="engine ยังไม่มีเส้นทาง wait_1h — เลือกได้แค่ trailing หรือ skip")
    if route not in coldstart_intent.ROUTES:
        raise HTTPException(status_code=422, detail=f"ไม่รู้จักเส้นทาง {route!r}")
    now = now_ms()
    with db.begin() as conn:
        settings = _settings(conn, target)
        sym = _find_symbol(settings, symbol, market)
        if settings is None or sym is None:
            raise HTTPException(status_code=404, detail=f"ไม่มีคู่ {symbol!r} ในโปรไฟล์ {target}")
        if not _engine_decides(settings):
            # เจตนาที่ไม่มีวันถูกใช้ จะค้างอยู่จนวันที่โปรไฟล์นี้เปลี่ยนเป็น ccxt แล้วโผล่มาใช้
            # ใน run ที่ไม่มีใครตั้งใจ — ปฏิเสธตั้งแต่ตอนเลือกดีกว่า
            raise HTTPException(
                status_code=422,
                detail=f"engine ของโปรไฟล์ {target} ไม่ตัดสินใจเอง (broker = {settings.broker.kind}) — เจตนาจะไม่ถูกใช้",
            )
        changed = coldstart_intent.choose(
            conn, profile=target, market=sym.market, symbol=sym.symbol, route=route,
            by=user.name, now=now,
        )
        if changed:
            audit.record(
                conn, action="coldstart.choose", ts=now, actor_user_id=user.id,
                target=f"{target} {sym.market} {sym.symbol}", detail={"route": route},
                ip=client_ip(request),
            )
        ctx = page_context(
            conn, profile=target, symbol=sym.symbol, market=sym.market, tab="coldstart", user=user,
            notice=(f"เลือก {route} ให้ run ถัดไปแล้ว" if changed else f"{route} ถูกเลือกไว้อยู่แล้ว"),
        )
    return templates.TemplateResponse(request, "partials/symbol_body.html", ctx)
