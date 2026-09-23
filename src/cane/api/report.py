"""หน้า รายงาน — ไม้ที่ปิดแล้วทั้งสองฝั่ง คิดจาก fill จริง (ใบ 25)

ตัวเลขทุกตัวมาจาก `report.summarize()` บน VIEW `closed_trades` · ไฟล์นี้มีหน้าที่แค่เลือก
ช่วงเวลา จัดรูปข้อความ และวาด equity curve เป็น SVG ฝั่ง server (ADR 20 — ไม่มี chart lib)

## ช่วงเวลาอ่านจากแท่งที่ **ออก** ไม่ใช่แท่งที่เข้า

ไม้ที่เข้าก่อนช่วงแต่ออกในช่วงเป็นผลของช่วงนั้น เพราะเงินเปลี่ยนมือตอนออก · ตัวนับของ
การ์ด "บอททำตามกฎหรือไม่" ที่มาจาก `decisions` ใช้ขอบเดียวกันบน `bar_close_ts`

## สิ่งที่ไฟล์ design เขียนไว้แต่ระบบนี้ไม่มี และห้ามลอกเข้ามา

| ในไฟล์ design | ทำไมไม่มี |
| --- | --- |
| สาเหตุที่ออก `kill switch — แพ้ติดกัน 2 ไม้` · บรรทัด `ปิดด้วย kill switch` | kill switch บล็อกแค่ไม้ใหม่ (spec/10 §`engine.should_run` ≠ `kill_switch.latched`) ไม่ได้ปิดไม้ · `exit_reason` มีสี่ค่าตาม spec (migration 0006) |
| `ปิดเพราะ state กลับข้างแต่ไม่มีสัญญาณ` | ไม่มีทางออกแบบนั้นใน spec/03 — ออกด้วยสัญญาณฝั่งตรงข้ามเท่านั้น |
| `ปิดแล้วไม่เปิดฝั่งตรงข้าม` | ต้องจับคู่ขาปิดกับแท่งที่ไม่ได้เปิดฝั่งใหม่ ซึ่งไม่มีแหล่งเดียวที่ตอบตรงๆ — ไม่แต่งตัวเลข |
| `% ของ bucket` ที่หารด้วย bucket long อย่างเดียว (`+18.55 / 300`) | ทุนคือ bucket **ทั้งสองฝั่ง** (ADR 8) ของเวอร์ชันที่ตัดสินไม้นั้น |
| `ที่ยังถืออยู่ -1.0%` · `SOL -11.0%` | ต้องมี mark price สด ซึ่งไม่อยู่ในตารางไหน (spec/10 §5. state ที่อยู่ในตาราง) — ขึ้น `—` |
| `slippage เฉลี่ย 0.045% ต่อขา` · `ค่าธรรมเนียม taker 0.10% ต่อขา` | อัตราต่อขาไม่ได้เก็บ · แสดงยอดรวมจาก fill แทน |
| `ทุกบรรทัดตรงกับ decisions.jsonl` | บันทึกอยู่ในตาราง `decisions` ไม่ใช่ไฟล์ (ADR 22) |
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import Connection, Engine

from cane.api.deps import current_mode, get_db, require_cap
from cane.api.templating import templates
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import ledger as ledger_repo
from cane.db.repo import report as report_repo
from cane.db.repo.ledger import ClosedTrade
from cane.db.repo.users import User
from cane.engine.state import PROFILES
from cane.report import Summary, summarize

router = APIRouter()

UNKNOWN = "—"
DAY_MS = 86_400_000

#: `exit_reason` → ข้อความในคอลัมน์ `สาเหตุที่ออก` · ชุดปิดเดียวกับ `ck_fills_exit_reason`
EXIT_TEXT = {
    "signal": "สัญญาณ {opposite}",
    "stop": "stop ที่ exchange",
    "liquidation": "liquidation — exchange ปิดให้",
    "manual": "ปิดไม้ฉุกเฉิน",
}
_OPPOSITE = {"long": "short", "short": "long"}

#: ขอบของแถบใน diverging bar · เกินนี้ตัดที่ขอบ (handoff §9.5)
BAR_CLAMP_PCT = 12.0

#: กล่องของ equity curve ตาม `viewBox 0 0 1000 268` ในไฟล์ design
_X0, _X1, _Y0, _Y1 = 40.0, 960.0, 30.0, 230.0

CSV_HEADER = (
    "entry_bar", "exit_bar", "symbol", "side", "size_pct", "entry_fill", "exit_fill",
    "gross_pct", "net_pct", "exit_reason", "cost_complete",
)
CSV_NAME = "cane-report-closed-trades.csv"


# ── ช่วงเวลา ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Range:
    mode: str  # "all" | "custom"
    since_ts: int | None
    until_ts: int | None
    raw_from: str
    raw_to: str


def _day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_range(mode: str, raw_from: str, raw_to: str) -> Range:
    """วันที่ที่อ่านไม่ออกเท่ากับไม่ได้ใส่ขอบข้างนั้น ไม่ใช่ 400

    เหตุผลเดียวกับชิปของหน้าบันทึก — query string เป็นของที่คนแก้เองได้ ·
    ขอบทั้งสองเป็นวัน UTC รวมทั้งวัน (`ถึง 08-26` รวมแท่งที่ปิดตอน 08-26 23:59)
    """
    if mode != "custom":
        return Range("all", None, None, "", "")
    start, end = _day(raw_from), _day(raw_to)
    since = None if start is None else int(datetime.combine(start, time(), UTC).timestamp() * 1000)
    until = (
        None if end is None
        else int(datetime.combine(end, time(), UTC).timestamp() * 1000) + DAY_MS - 1
    )
    return Range("custom", since, until, raw_from, raw_to)


def _in_range(trade: ClosedTrade, rng: Range) -> bool:
    ts = trade.close_bar_close_ts
    return (rng.since_ts is None or ts >= rng.since_ts) and (
        rng.until_ts is None or ts <= rng.until_ts
    )


# ── จัดรูป ───────────────────────────────────────────────────────────────────


def _utc(ts: int, fmt: str = "%m-%d") -> str:
    """วันที่แบบ UTC · ด้วยเหตุผลเดียวกับ `log._bar()`"""
    return f"{datetime.fromtimestamp(ts / 1000, tz=UTC):{fmt}}"


def _pct(value: float | None, digits: int = 1) -> str:
    return UNKNOWN if value is None else f"{value:+.{digits}f}%"


def _pts(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:+.1f} จุด"


def _quote(value: Decimal, *, signed: bool = False) -> str:
    return f"{value:+.2f}" if signed else f"{value:.2f}"


def _px(value: float) -> str:
    """ทศนิยมสองตำแหน่งเสมอ ยกเว้นเหรียญราคาต่ำกว่า 1 ที่สองตำแหน่งจะกลืนตัวเลขทั้งหมด"""
    return f"{value:,.2f}" if value >= 1 else f"{value:.6g}"


def _tone(value: float | Decimal | None) -> str:
    if value is None or value == 0:
        return ""
    return "good" if value > 0 else "bad"


# ── การ์ด "บอททำตามกฎหรือไม่" ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Check:
    label: str
    value: str
    #: `good` / `warn` / `bad` / `""` — สีของตัวเลข
    tone: str = ""


def _overlaps(trades: tuple[ClosedTrade, ...]) -> int:
    """คู่ไม้เหรียญเดียวกันคนละฝั่งที่ถือซ้อนเวลากัน — ต้องเป็นศูนย์เสมอ (โหมดทางเดียว)

    ขอบชนกันไม่นับ: ขาปิดกับขาเปิดของ flip อยู่ที่แท่งเดียวกัน และขาปิดมาก่อน
    """
    count = 0
    for i, a in enumerate(trades):
        for b in trades[i + 1:]:
            if a.symbol == b.symbol and a.market == b.market and a.side != b.side:
                if a.entry_ts < b.exit_ts and b.entry_ts < a.exit_ts:
                    count += 1
    return count


def _checks(
    summary: Summary,
    origins: dict[str, report_repo.Origin],
    counts: dict[str, int],
    flips: report_repo.FlipCount,
) -> tuple[tuple[Check, ...], tuple[Check, ...], bool]:
    """สองกลุ่มของการ์ด + ธงว่าทุกข้อที่ต้องเป็นศูนย์เป็นศูนย์จริงไหม"""
    trades = summary.trades
    total = len(trades)
    known = [origins[t.trade_id] for t in trades if t.trade_id in origins]
    on_signal = sum(o.on_signal for o in known)
    cold = sum(not o.on_signal and o.cold_start is not None for o in known)
    stray = sum(not o.on_signal and o.cold_start is None for o in known)
    orphan = total - len(known)
    reasons = {r: sum(t.exit_reason == r for t in trades) for r in EXIT_TEXT}
    overlap = _overlaps(trades)

    rules = [
        Check("เข้าไม้จากแท่งสัญญาณจริง", f"{on_signal} / {total}",
              "good" if on_signal + cold == total and total else ""),
    ]
    if cold:
        rules.append(Check("เข้าทาง cold start", str(cold)))
    if stray:
        rules.append(Check("เปิดนอกแท่งสัญญาณ", str(stray), "bad"))
    if orphan:
        rules.append(Check("ไม่พบแถวตัดสินที่เปิดไม้", str(orphan), "warn"))
    rules += [
        Check("ปิดด้วยสัญญาณฝั่งตรงข้าม", f"{reasons['signal']} / {total}"),
        Check("ปิดด้วย stop ที่ exchange", str(reasons["stop"])),
        Check("ถูก liquidation", str(reasons["liquidation"]),
              "bad" if reasons["liquidation"] else ""),
        Check("ปิดไม้ฉุกเฉิน", str(reasons["manual"]), "warn" if reasons["manual"] else ""),
        Check("กลับข้างครบสองขาในแท่งเดียว", f"{flips.completed} / {flips.total}",
              "warn" if flips.completed < flips.total else ""),
        Check("เปิดไม้ทับฝั่งตรงข้ามที่ยังค้าง", str(overlap), "bad" if overlap else "good"),
    ]
    gates = (
        Check("ถูก risk ปฏิเสธ ไม่เกิดไม้", str(counts["risk"])),
        Check("ถูกเพดานตัดขนาดไม้", str(counts["capped"])),
        Check("ตกไปที่ไม้พื้นฐาน · LLM ไม่ตอบ", str(counts["llm"])),
    )
    clean = stray == 0 and overlap == 0
    return tuple(rules), gates, clean


# ── equity curve ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Chart:
    net: str
    gross: str
    area: str
    zero_y: float
    grid: tuple[tuple[float, str], ...]
    ticks: tuple[tuple[float, str], ...]
    end: tuple[float, float]
    trough: tuple[float, float] | None


def _step(span: float) -> float:
    """ระยะของเส้นกริด · 4% ตามไฟล์ design แล้วขยายเป็นเท่าตัวจนเหลือไม่เกินหกช่อง"""
    step = 4.0
    while span / step > 6:
        step *= 2
    return step


def _chart(summary: Summary) -> Chart | None:
    """polyline ของผลสะสมสุทธิกับก่อนหักต้นทุน · ไม่มีทุนตัวเดียว = ไม่มีกราฟ"""
    points = summary.equity
    if not points:
        return None
    start = min(t.entry_ts for t in summary.trades)
    values = [0.0, *(p.net_pct for p in points), *(p.gross_pct for p in points)]
    step = _step(max(values) - min(values) or 1.0)
    hi = max(step, -(-max(values) // step) * step)
    lo = min(-step, (min(values) // step) * step)
    span_ts = max(points[-1].ts - start, 1)

    def x(ts: int) -> float:
        return round(_X0 + (ts - start) / span_ts * (_X1 - _X0), 1)

    def y(v: float) -> float:
        return round(_Y0 + (hi - v) / (hi - lo) * (_Y1 - _Y0), 1)

    net = [(x(start), y(0.0)), *((x(p.ts), y(p.net_pct)) for p in points)]
    gross = [(x(start), y(0.0)), *((x(p.ts), y(p.gross_pct)) for p in points)]

    def line(pts: list[tuple[float, float]]) -> str:
        return " ".join(f"{a},{b}" for a, b in pts)

    grid = []
    v = hi
    while v >= lo - 1e-9:
        grid.append((y(v), "0" if v == 0 else f"{v:+g}%"))
        v -= step
    # ห้าขีดบนแกน X: ต้น ปลาย และสามจุดระหว่างทาง ตามจำนวนในไฟล์ design
    ticks = tuple(
        (x(ts), _utc(ts))
        for ts in sorted({start + span_ts * k // 4 for k in range(5)})
    )
    trough = None
    if summary.drawdown_ts is not None:
        at = next(p for p in points if p.ts == summary.drawdown_ts)
        trough = (x(at.ts), y(at.net_pct))
    return Chart(
        net=line(net),
        gross=line(gross),
        area=line([*net, (net[-1][0], y(0.0))]),
        zero_y=y(0.0),
        grid=tuple(grid),
        ticks=ticks,
        end=net[-1],
        trough=trough,
    )


# ── แถวของตาราง ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SymbolLine:
    pair: str
    trades: int
    wins: str
    ls: str
    contrib: str
    tone: str
    #: ความกว้างของแถบเป็น % ของครึ่งราง และฝั่งที่มันโผล่
    bar_width: float
    bar_side: str
    fee: str
    slip: str
    holding: str


@dataclass(frozen=True, slots=True)
class TradeLine:
    in_bar: str
    out_bar: str
    pair: str
    size: str
    side: str
    entry: str
    exit: str
    gross: str
    gross_tone: str
    net: str
    net_tone: str
    reason: str
    complete: bool


def _win_rate(wins: int, total: int) -> str:
    return f"{wins} · {wins / total * 100:.1f}%" if total else "0"


def _reason(trade: ClosedTrade) -> str:
    text = EXIT_TEXT.get(trade.exit_reason, trade.exit_reason)
    text = text.format(opposite=_OPPOSITE.get(trade.side, UNKNOWN))
    return f"{text} — {trade.exit_detail}" if trade.exit_detail else text


def _trade_line(trade: ClosedTrade, origin: report_repo.Origin | None) -> TradeLine:
    size = UNKNOWN if origin is None or origin.size_pct is None else f"{origin.size_pct:g}%"
    return TradeLine(
        in_bar=_utc(trade.open_bar_close_ts),
        out_bar=_utc(trade.close_bar_close_ts),
        pair=trade.symbol,
        size=size,
        side=trade.side.upper(),
        entry=_px(trade.entry_px),
        exit=_px(trade.exit_px),
        gross=_pct(trade.gross_pct),
        gross_tone=_tone(trade.gross_pct),
        net=_pct(trade.net_pct),
        net_tone=_tone(trade.net_pct),
        reason=_reason(trade),
        complete=trade.cost_complete,
    )


def _held_text(held: list[report_repo.OpenTrade]) -> str:
    if not held:
        return "ไม่มี"
    sides = sorted({t.side for t in held})
    return f"{len(held)} ไม้ " + " / ".join(sides)


def page_context(
    conn: Connection,
    *,
    profile: str,
    range_mode: str = "all",
    raw_from: str = "",
    raw_to: str = "",
) -> dict[str, object]:
    """ทุกอย่างที่ `partials/report_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `rp_`"""
    rng = parse_range(range_mode, raw_from, raw_to)
    trades = [t for t in ledger_repo.closed_trades(conn, profile) if _in_range(t, rng)]
    origins = report_repo.origins(conn, profile)
    versions = {origins[t.trade_id].config_version_id for t in trades if t.trade_id in origins}
    capitals = report_repo.capital_of(conn, versions)
    summary = summarize(trades, origins, capitals)
    held = report_repo.open_trades(conn, profile)
    counts = decisions_repo.journal_counts(
        conn, profile, since_ts=rng.since_ts, until_ts=rng.until_ts
    )
    flips = report_repo.flips(conn, profile, since_ts=rng.since_ts, until_ts=rng.until_ts)
    rules, gates, clean = _checks(summary, origins, counts, flips)

    held_by_symbol: dict[str, list[report_repo.OpenTrade]] = {}
    for t in held:
        held_by_symbol.setdefault(t.symbol, []).append(t)
    held_margin = [t.margin for t in held]

    first = min((t.entry_ts for t in summary.trades), default=None)
    last = max((t.exit_ts for t in summary.trades), default=None)
    total = len(summary.trades)

    symbols = tuple(
        SymbolLine(
            pair=row.symbol,
            trades=row.trades,
            wins=_win_rate(row.wins, row.trades),
            ls=f"{row.longs} / {row.shorts}",
            contrib=_pct(row.net_pct),
            tone=_tone(row.net_quote),
            bar_width=(
                0.0 if row.net_pct is None
                else round(min(abs(row.net_pct), BAR_CLAMP_PCT) / BAR_CLAMP_PCT * 50, 2)
            ),
            bar_side="right" if row.net_quote >= 0 else "left",
            fee=_quote(row.fee_quote),
            slip=_quote(row.slippage_quote),
            holding=_held_text(held_by_symbol.get(row.symbol, [])),
        )
        for row in summary.per_symbol
    )

    return {
        "rp_profile": profile,
        "rp_range": rng,
        "rp_empty": total == 0,
        "rp_span": (
            UNKNOWN if first is None
            else f"{_utc(first, '%Y-%m-%d')} → {_utc(last, '%Y-%m-%d')}"
        ),
        "rp_days": 0 if first is None else (last - first) // DAY_MS + 1,
        "rp_capital": None if summary.capital is None else _quote(summary.capital),
        "rp_segments": tuple(
            (UNKNOWN if s.capital is None else _quote(s.capital), s.trades, _pct(s.net_pct))
            for s in summary.segments
        ),
        "rp_net": _pct(summary.net_pct),
        "rp_net_tone": _tone(summary.net_quote),
        "rp_net_abs": f"{_quote(summary.net_quote, signed=True)} USDT",
        "rp_split": f"ฝั่ง long {_pts(summary.long_pts)} · ฝั่ง short {_pts(summary.short_pts)}",
        "rp_trades": total,
        "rp_wins": f"ชนะ {_win_rate(summary.wins, total)}",
        "rp_trade_note": (
            f"long {summary.longs} ไม้ · short {summary.shorts} ไม้ · "
            f"กำไรเฉลี่ย {_pct(summary.avg_win_pct)} ขาดทุนเฉลี่ย {_pct(summary.avg_loss_pct)}"
        ),
        "rp_dd": _pct(summary.drawdown_pct),
        "rp_dd_note": (
            "ยังไม่เคยต่ำกว่ายอดสูงสุด" if summary.drawdown_ts is None
            else _utc(summary.drawdown_ts, "%Y-%m-%d")
        ),
        "rp_cost": _quote(summary.fee_quote + summary.slippage_quote + summary.funding_quote),
        "rp_cost_note": (
            f"fee {_quote(summary.fee_quote)} + slippage {_quote(summary.slippage_quote)} + "
            f"funding {_quote(summary.funding_quote)} · กินไป "
            + (UNKNOWN if summary.cost_pts is None else f"{summary.cost_pts:.2f} จุด")
        ),
        "rp_incomplete": summary.incomplete,
        "rp_held": UNKNOWN,
        "rp_held_count": _held_text(held),
        "rp_held_note": (
            "margin ที่ใช้ "
            + (UNKNOWN if None in held_margin else _quote(sum(held_margin, Decimal(0))))
            + " · ผลที่ยังไม่ปิดต้องใช้ mark price สด ซึ่งยังไม่มีแหล่ง"
        ),
        "rp_chart": _chart(summary),
        "rp_rules": rules,
        "rp_gates": gates,
        "rp_clean": clean,
        "rp_symbols": symbols,
        "rp_total": {
            "trades": total,
            "wins": _win_rate(summary.wins, total),
            "ls": f"{summary.longs} / {summary.shorts}",
            "net": _pct(summary.net_pct),
            "tone": _tone(summary.net_quote),
            "fee": _quote(summary.fee_quote),
            "slip": _quote(summary.slippage_quote),
            "held": _held_text(held),
        },
        "rp_lines": tuple(_trade_line(t, origins.get(t.trade_id)) for t in summary.trades),
        "rp_funding": _quote(summary.funding_quote, signed=True),
        "rp_held_total": len(held),
    }


@router.get("/partials/report", response_class=HTMLResponse)
def body(
    request: Request,
    range_mode: str = Query("all", alias="range"),
    raw_from: str = Query("", alias="from"),
    raw_to: str = Query("", alias="to"),
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("view_overview")),
    mode: str = Depends(current_mode),
) -> HTMLResponse:
    """เนื้อของหน้ารายงาน — ตอนสลับโหมดและตอนเปลี่ยนช่วง"""
    with db.connect() as conn:
        ctx = page_context(
            conn, profile=mode, range_mode=range_mode, raw_from=raw_from, raw_to=raw_to
        )
    return templates.TemplateResponse(request, "partials/report_body.html", ctx)


def csv_rows(conn: Connection, *, profile: str, rng: Range) -> str:
    """ไม้ที่ปิดแล้วในช่วงนั้นเป็น CSV · ตัวเลขดิบ ไม่ผ่านการจัดรูปของหน้าจอ

    `cost_complete` ต่อท้ายหัวของไฟล์ design — ไฟล์ที่ส่งออกไปแล้วไม่มีธงนี้ คนที่เปิดใน
    spreadsheet จะไม่มีทางรู้ว่า net ของแถวไหนยังขาดต้นทุน
    """
    origins = report_repo.origins(conn, profile)
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for t in ledger_repo.closed_trades(conn, profile):
        if not _in_range(t, rng):
            continue
        origin = origins.get(t.trade_id)
        writer.writerow((
            _utc(t.open_bar_close_ts, "%Y-%m-%dT%H:%MZ"),
            _utc(t.close_bar_close_ts, "%Y-%m-%dT%H:%MZ"),
            t.symbol,
            t.side,
            "" if origin is None or origin.size_pct is None else f"{origin.size_pct:g}",
            f"{t.entry_px:g}",
            f"{t.exit_px:g}",
            f"{t.gross_pct:.4f}",
            f"{t.net_pct:.4f}",
            t.exit_reason,
            "true" if t.cost_complete else "false",
        ))
    return out.getvalue()


@router.get("/api/{profile}/report/export")
def export(
    profile: str,
    range_mode: str = Query("all", alias="range"),
    raw_from: str = Query("", alias="from"),
    raw_to: str = Query("", alias="to"),
    db: Engine = Depends(get_db),
    _: User = Depends(require_cap("export_records")),
) -> Response:
    """`{profile}` ที่ไม่มีอยู่คือ 404 ไม่ใช่ 400 (spec/10 §6. สัญญาของ API)"""
    if profile not in PROFILES:
        raise HTTPException(status_code=404, detail=f"ไม่มีโปรไฟล์ {profile!r}")
    with db.connect() as conn:
        body = csv_rows(conn, profile=profile, rng=parse_range(range_mode, raw_from, raw_to))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{CSV_NAME}"'},
    )
