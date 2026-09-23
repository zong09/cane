"""ตัวเลขของหน้ารายงาน — pure function บนไม้ที่ปิดแล้ว ไม่แตะฐาน

แยกจาก `api/report.py` เพราะคำถาม "ผลตอบแทนรวมเท่าไร" ต้องตอบให้ถูกก่อนจะมีใครถามว่า
แสดงยังไง · เทสต์ของไฟล์นี้จึงเทียบกับเลขที่คิดด้วยมือ ไม่ต้องมี HTML หรือ Postgres

## % รวมหารด้วยทุนของเวอร์ชันที่ตัดสินไม้นั้น ไม่ใช่ของวันนี้

ทุน = ผลรวม bucket ทั้งสองฝั่งของเหรียญที่เปิดใช้ในเวอร์ชันนั้น (ADR 8 · `repo/report.capital_of`) ·
ถ้าไม้ในช่วงที่เลือกมาจากเวอร์ชันที่ทุนไม่เท่ากัน **ไม่มีตัวหารตัวเดียวที่ถูก** — การเฉลี่ย
หรือเลือกตัวใดตัวหนึ่งคือการแต่งตัวเลข · `Summary.capital` จึงเป็น `None` และผลแยกเป็น
`segments` ตามทุนแต่ละก้อนแทน · ตัวเลขที่เป็น "จุด" ทุกตัว (ฝั่ง long/short, ต้นทุน,
drawdown, equity curve) ขึ้นกับตัวหารนี้ จึงเป็น `None` ตามไปด้วย ส่วนยอด USDT ยังตอบได้เสมอ

**ห้ามรวม % ด้วยการบวก `net_pct` ของแต่ละไม้** — `net_pct` คิดบน notional ของไม้นั้น
ไม้ 10% บน notional 20 กับไม้ 10% บน notional 2,000 ไม่ใช่ผลเท่ากัน
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from statistics import fmean

from cane.db.repo.ledger import ClosedTrade
from cane.db.repo.report import Origin

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Segment:
    """ไม้ที่ใช้ทุนก้อนเดียวกัน · `capital is None` = หาเวอร์ชันหรือทุนของไม้กลุ่มนี้ไม่เจอ"""

    capital: Decimal | None
    trades: int
    net_quote: Decimal
    net_pct: float | None


@dataclass(frozen=True, slots=True)
class Point:
    """หนึ่งจุดบน equity curve — หลังไม้ที่ออก ณ `ts` · % สะสมของทุน"""

    ts: int
    net_pct: float
    gross_pct: float


@dataclass(frozen=True, slots=True)
class SymbolRow:
    #: เหรียญชื่อเดียวกันบนสองตลาดเป็นคนละแถว (ADR 26)
    market: str
    symbol: str
    trades: int
    wins: int
    longs: int
    shorts: int
    net_quote: Decimal
    #: ส่วนของผลรวมทั้งหน้า — แถวทุกแถวบวกกันได้ `Summary.net_pct` พอดี
    net_pct: float | None
    fee_quote: Decimal
    slippage_quote: Decimal


@dataclass(frozen=True, slots=True)
class Summary:
    trades: tuple[ClosedTrade, ...]
    capital: Decimal | None
    segments: tuple[Segment, ...]
    net_quote: Decimal
    gross_quote: Decimal
    fee_quote: Decimal
    slippage_quote: Decimal
    funding_quote: Decimal
    net_pct: float | None
    long_pts: float | None
    short_pts: float | None
    cost_pts: float | None
    wins: int
    longs: int
    shorts: int
    avg_win_pct: float | None
    avg_loss_pct: float | None
    drawdown_pct: float | None
    drawdown_ts: int | None
    #: ไม้ที่ `cost_complete = false` — net ของไม้เหล่านี้อาจไม่ใช่ตัวจริง
    incomplete: int
    equity: tuple[Point, ...]
    per_symbol: tuple[SymbolRow, ...]


def _sum(values) -> Decimal:  # noqa: ANN001
    return sum(values, ZERO)


def _segments(
    trades: Sequence[ClosedTrade],
    origins: Mapping[str, Origin],
    capitals: Mapping[int, Decimal],
) -> tuple[Segment, ...]:
    """จัดกลุ่มตามทุน ไม่ใช่ตามเวอร์ชัน — สองเวอร์ชันที่ทุนเท่ากันหารด้วยตัวเดียวกันได้

    เรียงตามไม้แรกที่ออกของแต่ละกลุ่ม เพื่อให้อ่านเป็นลำดับเวลาได้
    """
    groups: dict[Decimal | None, list[ClosedTrade]] = {}
    for trade in sorted(trades, key=lambda t: t.exit_ts):
        origin = origins.get(trade.trade_id)
        capital = None if origin is None else capitals.get(origin.config_version_id)
        groups.setdefault(capital, []).append(trade)
    return tuple(
        Segment(
            capital=capital,
            trades=len(members),
            net_quote=(net := _sum(t.net_quote for t in members)),
            net_pct=None if not capital else float(net / capital * 100),
        )
        for capital, members in groups.items()
    )


def summarize(
    trades: Sequence[ClosedTrade],
    origins: Mapping[str, Origin],
    capitals: Mapping[int, Decimal],
) -> Summary:
    """ตัวเลขทั้งหน้าจากไม้ที่ปิดแล้วในช่วงที่เลือก · `trades` เรียงแบบไหนมาก็ได้"""
    segments = _segments(trades, origins, capitals)
    capital = segments[0].capital if len(segments) == 1 else None

    def pts(amount: Decimal) -> float | None:
        return None if not capital else float(amount / capital * 100)

    net = _sum(t.net_quote for t in trades)
    fee = _sum(t.fee_quote for t in trades)
    slippage = _sum(t.slippage_quote for t in trades)
    funding = _sum(t.funding_quote for t in trades)
    wins = [t for t in trades if t.net_quote > 0]
    losses = [t for t in trades if t.net_quote < 0]

    # equity สะสมตามเวลาที่ออก · drawdown วัดจากยอดสูงสุดที่เคยถึง โดยยอดตั้งต้นคือศูนย์
    equity: list[Point] = []
    cum_net = cum_gross = peak = ZERO
    worst, worst_ts = ZERO, None
    for trade in sorted(trades, key=lambda t: (t.exit_ts, t.trade_id)):
        cum_net += trade.net_quote
        cum_gross += trade.gross_quote
        peak = max(peak, cum_net)
        if cum_net - peak < worst:
            worst, worst_ts = cum_net - peak, trade.exit_ts
        if capital:
            equity.append(Point(ts=trade.exit_ts, net_pct=pts(cum_net), gross_pct=pts(cum_gross)))

    rows: dict[tuple[str, str], list[ClosedTrade]] = {}
    for trade in trades:
        rows.setdefault((trade.symbol, trade.market), []).append(trade)

    return Summary(
        trades=tuple(sorted(trades, key=lambda t: (t.exit_ts, t.trade_id), reverse=True)),
        capital=capital,
        segments=segments,
        net_quote=net,
        gross_quote=_sum(t.gross_quote for t in trades),
        fee_quote=fee,
        slippage_quote=slippage,
        funding_quote=funding,
        net_pct=pts(net),
        long_pts=pts(_sum(t.net_quote for t in trades if t.side == "long")),
        short_pts=pts(_sum(t.net_quote for t in trades if t.side == "short")),
        cost_pts=pts(fee + slippage + funding),
        wins=len(wins),
        longs=sum(t.side == "long" for t in trades),
        shorts=sum(t.side == "short" for t in trades),
        avg_win_pct=fmean(t.net_pct for t in wins) if wins else None,
        avg_loss_pct=fmean(t.net_pct for t in losses) if losses else None,
        drawdown_pct=pts(worst),
        drawdown_ts=worst_ts,
        incomplete=sum(not t.cost_complete for t in trades),
        equity=tuple(equity),
        per_symbol=tuple(
            SymbolRow(
                market=market,
                symbol=symbol,
                trades=len(members),
                wins=sum(t.net_quote > 0 for t in members),
                longs=sum(t.side == "long" for t in members),
                shorts=sum(t.side == "short" for t in members),
                net_quote=(sym_net := _sum(t.net_quote for t in members)),
                net_pct=pts(sym_net),
                fee_quote=_sum(t.fee_quote for t in members),
                slippage_quote=_sum(t.slippage_quote for t in members),
            )
            for (symbol, market), members in sorted(rows.items())
        ),
    )
