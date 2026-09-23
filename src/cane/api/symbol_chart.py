"""แท็บ กราฟและสัญญาณ ของหน้าเหรียญ — candlestick + EMA12/26 + แถบโซน (handoff §9.2a · §11)

วาดเป็น SVG ฝั่ง server (ADR 20) ไม่ใช้ chart lib · ข้อมูลคือแท่งในตาราง `bars` ที่ engine
เก็บไว้ และ Action Zone คำนวณด้วย `action_zones()` ตัวเดียวกับไปป์ไลน์

## โซนบนกราฟคำนวณใหม่ แต่ "ระบบตัดสินอะไร" อ่านจากบันทึก

เส้นกับแถบสีต้องมีค่าทุกแท่ง ซึ่งตารางไม่ได้เก็บ จึงคำนวณจากแท่ง · ส่วนกล่องผลของแท่ง
ล่าสุดอ่านจากแถว `decisions` ถ้ามี · ถ้าสองทางได้โซนไม่ตรงกันที่แท่งเดียวกัน หน้าจอบอก —
แปลว่าแท่งในตารางถูกเขียนทับหลังตัดสิน หรือไปป์ไลน์เห็นประวัติคนละชุด ซึ่งคนต้องรู้

## ที่ไฟล์ design ยังไม่มี (§11) และทำแล้ว / ยังทำไม่ได้

- จุดเปิด/ปิดของทั้งสองฝั่ง — วาดจาก `fills` ของเหรียญนี้ในช่วงที่แสดง
- เส้นราคา liquidation ของสถานะที่ถือ — **ยังไม่วาด** ราคานั้นไม่ได้เก็บ (แถวเก็บระยะเป็น %
  ตอนเปิดเท่านั้น) และคำนวณใหม่ต้องใช้ maintenance margin ของ venue (ADR 33)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Connection

from cane.config.settings import Settings, SymbolConfig
from cane.data.ohlcv import Bar
from cane.db.repo import bars as bars_repo
from cane.db.repo import ledger as ledger_repo
from cane.db.repo.decisions import DecisionRecord
from cane.db.types import now_ms
from cane.indicators.action_zone import FAST_PERIOD, SLOW_PERIOD, SMOOTH_PERIOD, ActionZone, action_zones

UNKNOWN = "—"

#: แท่งที่โหลดมาคำนวณ · **ชุดเดียวกับไปป์ไลน์** — ทั้ง live (`data/ohlcv.py` อ่านทั้งตาราง
#: แล้ว merge กับที่ดึงใหม่) และ replay อ่านประวัติทั้งหมดไม่จำกัด · หน้าต่างที่สั้นกว่าให้ EMA
#: seed ต่างไปเล็กน้อย แล้วแถบเตือน "โซนไม่ตรงกับที่บันทึก" จะดังทั้งที่ไม่มีอะไรผิด
LOAD_BARS: int | None = None
#: แท่งที่วาด · handoff §11 "85 แท่ง"
SHOW_BARS = 85

#: กล่องของกราฟราคา · ความสูงของแถบโซนอยู่ที่ CSS (18px)
W, H, PAD_TOP, PAD_BOTTOM = 1000.0, 300.0, 12.0, 12.0

ZONE_TEXT = (
    ("GREEN", "เปิด long"), ("BLUE", "pre-long 2"), ("LBLUE", "pre-long 1"),
    ("RED", "เปิด short"), ("ORANGE", "pre-short 2"), ("YELLOW", "pre-short 1"), ("BLACK", ""),
)


@dataclass(frozen=True, slots=True)
class Candle:
    x: float
    w: float
    wick_top: float
    wick_bottom: float
    body_top: float
    body_h: float
    up: bool


@dataclass(frozen=True, slots=True)
class Marker:
    x: float
    y: float
    #: `open-long` / `open-short` / `close`
    kind: str
    title: str


@dataclass(frozen=True, slots=True)
class Chart:
    candles: tuple[Candle, ...]
    fast: str
    slow: str
    ribbon: tuple[tuple[float, float, str], ...]
    markers: tuple[Marker, ...]
    hi: str
    lo: str
    first: str
    last: str


def _line(points: list[tuple[float, float | None]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points if y is not None)


def build_chart(bars: list[Bar], zones: list[ActionZone], marks: list[ledger_repo.Mark]) -> Chart:
    """แท่งท้าย `SHOW_BARS` แท่ง · แกนราคาครอบทั้ง high/low และเส้น EMA ที่มีค่า"""
    bars, zones = bars[-SHOW_BARS:], zones[-SHOW_BARS:]
    values = [b.high for b in bars] + [b.low for b in bars]
    values += [z.fast_ma for z in zones if z.fast_ma is not None]
    values += [z.slow_ma for z in zones if z.slow_ma is not None]
    top, bottom = max(values), min(values)
    span = (top - bottom) or 1.0
    step = W / len(bars)

    def y(px: float) -> float:
        return PAD_TOP + (top - px) / span * (H - PAD_TOP - PAD_BOTTOM)

    def x(i: int) -> float:
        return step * i + step / 2

    candles = []
    for i, b in enumerate(bars):
        hi_body, lo_body = max(b.open, b.close), min(b.open, b.close)
        candles.append(Candle(
            x=round(x(i) - step * 0.3, 1), w=round(step * 0.6, 1),
            wick_top=round(y(b.high), 1), wick_bottom=round(y(b.low), 1),
            body_top=round(y(hi_body), 1), body_h=round(max(y(lo_body) - y(hi_body), 1.0), 1),
            up=b.close >= b.open,
        ))
    index = {b.close_ts: i for i, b in enumerate(bars)}
    markers = []
    for m in marks:
        i = index.get(m.bar_close_ts)
        if i is None:
            continue
        b = bars[i]
        if m.leg == "open":
            kind = f"open-{m.side}"
            at = y(b.low) + 12 if m.side == "long" else y(b.high) - 12
            title = f"เปิด {m.side} @ {m.px:g}"
        else:
            kind = "close"
            at = y(b.high) - 12 if m.side == "long" else y(b.low) + 12
            title = f"ปิด {m.side} @ {m.px:g} ({m.leg})"
        markers.append(Marker(x=round(x(i), 1), y=round(at, 1), kind=kind, title=title))

    def day(ts: int) -> str:
        return f"{datetime.fromtimestamp(ts / 1000, tz=UTC):%m-%d}"

    return Chart(
        candles=tuple(candles),
        fast=_line([(x(i), None if z.fast_ma is None else y(z.fast_ma)) for i, z in enumerate(zones)]),
        slow=_line([(x(i), None if z.slow_ma is None else y(z.slow_ma)) for i, z in enumerate(zones)]),
        ribbon=tuple((round(step * i, 2), round(step, 2), z.zone) for i, z in enumerate(zones)),
        markers=tuple(markers),
        hi=f"{top:,.2f}",
        lo=f"{bottom:,.2f}",
        first=day(bars[0].close_ts),
        last=day(bars[-1].close_ts),
    )


@dataclass(frozen=True, slots=True)
class Verdict:
    #: `long` / `short` / `short-off` / `none`
    kind: str
    title: str
    body: str
    button: str
    tab: str


def _verdict(
    long_signal: bool, short_signal: bool, prev: ActionZone | None, held_side: str,
    short_allowed: bool, held_label: str, *, spot: bool = False,
) -> Verdict:
    """กล่องท้ายการ์ด สี่แบบตาม handoff §9.2a · ข้อความหลักเป็นข้อความสุดท้ายของ design"""
    came_from = prev.zone if prev else UNKNOWN
    if long_signal:
        pending = ("มีสถานะ short ค้าง — ปิดก่อนแล้วจึงเปิด long" if held_side == "short"
                   else "ไม่มีสถานะ short ค้างที่ต้องปิดก่อน")
        return Verdict(
            "long", "แท่งนี้เป็นจุดสัญญาณฝั่ง long — เปิดไม้ที่แท่งถัดไป",
            f"เข้าโซนเขียวจาก {came_from} ขณะสถานะก่อนหน้าเป็น bearish จึงเป็น long signal จริง "
            f"ไม่ใช่แค่ longcond · {pending}",
            "ดูการตัดสินใจ", "decision",
        )
    if short_signal and short_allowed:
        body = (f"โหมดทางเดียวบังคับปิด {held_label} ให้เสร็จก่อน ถ้าขาปิดไม่สำเร็จจะไม่เปิด short"
                if held_side == "long" else "ไม่มีสถานะ long ค้างที่ต้องปิดก่อน")
        return Verdict(
            "short",
            "แท่งนี้เป็นจุดสัญญาณฝั่ง short — "
            + ("ปิด long แล้วเปิด short ที่แท่งถัดไป" if held_side == "long" else "เปิดไม้ที่แท่งถัดไป"),
            body, "ดูการตัดสินใจ", "decision",
        )
    if short_signal:
        return Verdict(
            "short-off",
            "มี short signal — spot ไม่มีฝั่ง short" if spot
            else "มี short signal แต่ฝั่ง short ปิดอยู่ในโปรไฟล์",
            (f"ระบบยังปิด {held_label} ตามสัญญาณ แต่จะไม่เปิดไม้ฝั่ง short ต่อ" if held_side == "long"
             else "ไม่มีสถานะ long ให้ปิด และจะไม่เปิดไม้ฝั่ง short"),
            "ดูการตัดสินใจ", "decision",
        )
    return Verdict(
        "none", "แท่งล่าสุดไม่ใช่จุดสัญญาณทั้งสองฝั่ง — ไม่ทำอะไร",
        "กฎไม้เรียวปฏิเสธการเข้าที่ไม่ใช่แท่งสัญญาณ ทั้งฝั่ง long และ short · "
        "ระบบยังเขียน DecisionRecord ของแท่งนี้ตามปกติ",
        "ดูบันทึก", "",
    )


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _since(value: int | None) -> str:
    return "na" if value is None else str(value)


def chart_context(
    conn: Connection,
    *,
    profile: str,
    settings: Settings,
    sym: SymbolConfig,
    record: DecisionRecord | None,
    held_side: str,
    held_label: str,
) -> dict[str, object]:
    """ทุกอย่างของแท็บกราฟ · คีย์ขึ้นต้นด้วย `ch_` · ไม่มีแท่งเลย = `ch_ready` เป็นเท็จ"""
    bars = bars_repo.closed_bars(
        conn, sym.market, sym.symbol, settings.timeframe, as_of=now_ms(), limit=LOAD_BARS
    )
    if not bars:
        return {"ch_ready": False}
    zones = action_zones(bars)
    last, prev = zones[-1], (zones[-2] if len(zones) > 1 else None)
    shown_from = bars[-SHOW_BARS:][0].close_ts
    marks = ledger_repo.marks_since(conn, profile, sym.market, sym.symbol, shown_from)
    short_allowed = settings.allow_short and sym.allow_short and sym.market == "usdtm_perp"

    mismatch = ""
    if record is not None and record.bar_close_ts == last.bar_close_ts and record.zone != last.zone:
        mismatch = (
            f"โซนที่คำนวณจากแท่งในตาราง ({last.zone}) ไม่ตรงกับที่บันทึกตอนตัดสิน ({record.zone}) "
            "— แท่งในตารางอาจถูกเขียนทับหลังตัดสิน"
        )
    elif record is not None and record.bar_close_ts != last.bar_close_ts:
        mismatch = "แท่งล่าสุดในตารางยังไม่มีบันทึกการตัดสินใจ — engine อาจยังไม่เดินแท่งนี้"

    # แถวที่ตัดสินแท่งเดียวกันเป็นตัวจริงของ "สัญญาณหรือเปล่า" · ไม่มีแถว = ใช้ค่าที่คำนวณ
    # (และ `mismatch` บอกไว้แล้วว่ายังไม่มีบันทึกของแท่งนี้)
    same_bar = record is not None and record.bar_close_ts == last.bar_close_ts
    signals = (
        (record.long_signal, record.short_signal) if same_bar
        else (last.long_signal, last.short_signal)
    )
    perp = sym.market == "usdtm_perp"
    funding = (
        UNKNOWN if record is None or record.funding_rate is None
        else f"{record.funding_rate * 100:+.3f}%"
    )
    return {
        "ch_ready": True,
        "ch_chart": build_chart(bars, zones, marks),
        "ch_zones": ZONE_TEXT,
        "ch_close": f"{last.close_px:,.2f}",
        "ch_fast": UNKNOWN if last.fast_ma is None else f"{last.fast_ma:,.2f}",
        "ch_slow": UNKNOWN if last.slow_ma is None else f"{last.slow_ma:,.2f}",
        "ch_trend": (
            UNKNOWN if last.fast_ma is None or last.slow_ma is None
            else "Bull" if last.fast_ma > last.slow_ma else "Bear"
        ),
        "ch_state": (
            ("zone", last.zone),
            ("longcond", _bool(last.longcond)),
            ("shortcond", _bool(last.shortcond)),
            ("state", last.state.lower()),
            ("barssince longcond", _since(last.bars_since_long)),
            ("barssince shortcond", _since(last.bars_since_short)),
        ),
        "ch_signals": (("long signal", _bool(last.long_signal)), ("short signal", _bool(last.short_signal))),
        "ch_held": held_label,
        "ch_params": (
            ("xsrc", "close"),
            ("xprd1 / xprd2", f"{FAST_PERIOD} / {SLOW_PERIOD}"),
            ("xsmooth", str(SMOOTH_PERIOD)),
        ),
        "ch_contract": (
            ("contract", "USDT-M perp" if perp else "spot"),
            ("margin / mode", f"{settings.broker.margin_mode} · one-way" if perp else "— · long-only"),
            ("leverage", f"{sym.leverage:g}x"),
            ("funding 8h", funding if perp else "ไม่มีบน spot"),
        ),
        "ch_verdict": _verdict(
            *signals, prev, held_side, short_allowed, held_label, spot=not perp
        ),
        "ch_mismatch": mismatch,
    }
