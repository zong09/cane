"""แท็บ Cold start ของหน้าเหรียญ — พรีวิวของ run ถัดไป กับปุ่มเลือกเจตนา (ใบ 25 · ADR 35 · handoff §9.2c)

## ปุ่มสองปุ่มเลือกเจตนาของ run ถัดไป ไม่ใช่สั่งเข้าไม้

`เข้าไม้พร้อมตั้ง SL` กับ `ข้ามรอบนี้` ยิง `POST /api/{profile}/coldstart/{symbol}` (ADR 35) ·
ผลคือแถวใน `cold_start_intent` ที่ engine อ่านแล้วลบที่แท่งแรกของ run ถัดไป **ไม่มีออเดอร์ออกไป
ตอนกด** · เจตนาชนะ `cold_start` ของ config · เลือกตอน engine เดินอยู่ = มีผลเมื่อ start ครั้งถัดไป
และหน้าจอบอกตรงปุ่ม · ประตูของ `late_entry()` ยังครบ — เลือก trailing ไว้แต่ RR ไม่ถึงตอนนั้น
engine ก็ไม่เข้า

## พรีวิวใช้ฟังก์ชันตัวเดียวกับ engine

`decide()` → `late_entry()` · Trail2 จาก `cdc_trailing_stop()` ปัดแบบเดียวกับ `pipeline._stop_px()` ·
เป้าจาก `features()` · หน้าจอจึงบอกได้ว่า engine จะทำอะไรถ้ากด start ตอนนี้ โดยไม่มีสูตรที่สอง
ที่ต้องคอยทำให้ตรงกัน · สิ่งเดียวที่ต่าง: **สถานะที่ถืออ่านจาก ledger** ไม่ใช่จาก exchange ·
engine อ่านจาก exchange ตอนเดินจริง (spec/08 ขั้น 3) หน้าจอจึงติดป้ายไว้

## สิ่งที่ไฟล์ design เขียนไว้แต่ยังไม่มี

| ในไฟล์ design | สถานะ |
| --- | --- |
| กราฟ 1h ของทางที่ 1 + ปุ่ม `เฝ้า 1h รอสัญญาณรอบถัดไป` | engine ยังไม่สร้าง `wait_1h` (pipeline บันทึกเป็น `cane_rule`) และไม่มีแท่ง 1h ในตาราง |
| ปุ่ม `เฝ้า 1h รอสัญญาณรอบถัดไป` | ไม่มีปุ่มเลย ไม่ใช่ปุ่มที่กดไม่ได้ — endpoint ปฏิเสธ `wait_1h` (ADR 35) |
| กราฟ trailing stop ของทางที่ 2 | ยังไม่วาด · ตัวเลขทุกตัวของเส้นอยู่ในกล่อง metric |
| `TP ขั้นต่ำ 3,092.80` กับ `R : R 2.00` (= entry + 2 × risk ซึ่งคือ**เกณฑ์**) | แสดง**เป้าจริง** = จุดเหวี่ยงล่าสุดตามที่เจ้าของตัดสินไว้ใน `rules/late_entry.py` (2026-09-14) และ RR จริงของมัน · เกณฑ์ 2 × risk ย้ายไปอยู่ในแถบผ่าน/ไม่ผ่าน |
| `ไม่มีสถานะเปิด … ที่ exchange` | อ่านจาก ledger จึงเขียนว่า `ใน ledger` · live ที่ `dry_run = true` ไม่มี fill เลย แถบนี้จึงขึ้นว่าไม่มีสถานะเสมอ ไม่ว่า exchange จะถืออะไร |
| ปริมาณในหมายเหตุขนาดไม้ `ปัดลงตาม lot size` | ไม่ได้ปัด — lot size มาจาก venue ตอนส่งจริง · ตัวเลขในหน้านี้คือก่อนปัด จึงตัดคำนั้นออก |

เจ้าของยืนยันข้อความที่เปลี่ยนจาก design ในตารางนี้แล้ว (handoff §15 ข้อ 9 · 2026-09-23)
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection

from cane.config.settings import Settings, SymbolConfig
from cane.data.ohlcv import MIN_CLOSED_BARS
from cane.db.repo import bars as bars_repo
from cane.db.types import PRICE_SCALE, now_ms
from cane.indicators.action_zone import action_zones
from cane.indicators.features import features
from cane.indicators.trailing import (
    FAST_ATR_FACTOR,
    FAST_ATR_PERIOD,
    SIGNAL_PERIOD,
    SLOW_ATR_FACTOR,
    SLOW_ATR_PERIOD,
    cdc_trailing_stop,
)
from cane.rules.cane import decide
from cane.rules.late_entry import MIN_REWARD_TO_RISK, ColdStartPlan, late_entry

UNKNOWN = "—"

#: เหตุที่ไม่เข้าเงื่อนไข → ประโยคในกล่องเหตุผล · ชุดเดียวกับ skip_reason ที่ `late_entry()` คืนได้
NOT_ELIGIBLE = {
    "no_signal": "ยังไม่มีเทรนด์ให้ตกรถ — state ยังไม่เดินไปข้างใดข้างหนึ่ง",
    "already_positioned": "มีสถานะเปิดของคู่นี้อยู่แล้ว — กลับโหมดปกติคือเฝ้ารอสัญญาณฝั่งตรงข้าม ไม่เข้าไม้ซ้ำ",
    "short_disabled": "ตกรถฝั่ง short แต่ฝั่ง short ปิดอยู่ — ไม่มีเส้นทางให้เข้า",
    "signal_bar": "แท่งล่าสุดเป็นจุดสัญญาณ — เส้นทางปกติจัดการเอง ไม่ใช่การตกรถ",
}

ROUTE_TEXT = {
    None: "ไม่ได้ตั้ง = ไม่เข้าเส้นทาง cold start",
    "skip": "skip — ไม่เข้าเส้นทาง cold start",
    "wait_1h": "wait_1h — ลง timeframe เล็ก",
    "trailing": "trailing — CDC ATR Trailing Stop",
}


@dataclass(frozen=True, slots=True)
class Trailing:
    entry: str
    stop: str
    target: str
    rr: str
    passed: bool
    risk_note: str
    hst: str
    sig: str
    color: str
    size_note: str


def _state_run(zones) -> int:  # noqa: ANN001
    """state เดียวกันมากี่แท่งแล้ว นับจากแท่งล่าสุดย้อนกลับ"""
    last = zones[-1].state
    run = 0
    for z in reversed(zones):
        if z.state != last:
            break
        run += 1
    return run


def _trailing(
    preview: ColdStartPlan, side: str, entry: float, trail, settings: Settings, sym: SymbolConfig  # noqa: ANN001
) -> Trailing:
    risk, reward = preview.risk, preview.reward
    stop = trail.slow
    if side == "long":
        target = None if reward is None else entry + reward
    else:
        target = None if reward is None else entry - reward
    rr = None if not risk or reward is None or risk <= 0 else reward / risk
    bucket = sym.bucket_quote_long if side == "long" else (sym.bucket_quote_short or 0.0)
    margin = bucket * settings.base_pct / 100
    notional = margin * sym.leverage
    base = sym.symbol.split("/")[0]
    return Trailing(
        entry=f"{entry:,.2f}",
        stop=UNKNOWN if stop is None else f"{round(stop, PRICE_SCALE):,.2f}",
        target=UNKNOWN if target is None else f"{target:,.2f}",
        rr=UNKNOWN if rr is None else f"{rr:.2f}",
        passed=preview.side is not None,
        risk_note=(
            UNKNOWN if risk is None
            else f"ความเสี่ยงต่อไม้ {risk:,.2f} ({risk / entry * 100:.2f}%)"
            + f" · เป้าต้องห่างจากราคาเข้าอย่างน้อย {MIN_REWARD_TO_RISK * risk:,.2f} "
            + f"({MIN_REWARD_TO_RISK:g} × ความเสี่ยง)"
        ),
        hst=UNKNOWN if trail.hst is None else f"{trail.hst:+.1f}",
        sig=UNKNOWN if trail.sig is None else f"{trail.sig:+.1f}",
        color=(
            UNKNOWN if trail.hst is None or trail.sig is None
            else "Green" if trail.hst > trail.sig else "Red"
        ),
        size_note=(
            f"ไม้พื้นฐาน {settings.base_pct:g}% (margin {margin:.2f} · notional {notional:.2f} "
            f"ที่ {sym.leverage:g}x · {notional / entry:.6g} {base}) เพราะไม่ใช่จุดสัญญาณ "
            "จึงไม่มีปัจจัยสนับสนุนให้ตัดสิน"
        ),
    )


def coldstart_context(
    conn: Connection, *, settings: Settings, sym: SymbolConfig, held_side: str,
    intent_route: str | None = None,
) -> dict[str, object]:
    """ทุกอย่างของแท็บ Cold start · คีย์ขึ้นต้นด้วย `cs_`

    `intent_route` คือเจตนาที่คนเลือกไว้ให้ run ถัดไป (ADR 35) · มี = ชนะ config ในบรรทัด
    "ถ้ากด start engine ตอนนี้" แบบเดียวกับที่ engine ทำจริง
    """
    bars = bars_repo.closed_bars(conn, sym.market, sym.symbol, settings.timeframe, as_of=now_ms())
    base = {
        "cs_route": ROUTE_TEXT.get(settings.cold_start, settings.cold_start),
        "cs_route_key": settings.cold_start or "ไม่ได้ตั้ง",
    }
    if len(bars) < MIN_CLOSED_BARS:
        return base | {"cs_ready": False, "cs_missing": MIN_CLOSED_BARS - len(bars)}

    zones = action_zones(bars)
    last = zones[-1]
    position = None if held_side == "flat" else held_side
    allow_short = settings.allow_short and sym.allow_short
    plan = decide(
        long_signal=last.long_signal, short_signal=last.short_signal, state=last.state,
        position_side=position, market=sym.market, allow_short=allow_short,
    )
    trail = cdc_trailing_stop(bars)[-1]
    trail_slow = None if trail.slow is None else round(trail.slow, PRICE_SCALE)
    try:
        feat = features(bars)
    except ValueError:
        feat = None

    side = {"BULLISH": "long", "BEARISH": "short"}.get(last.state)
    if feat is None:
        return base | {"cs_ready": False, "cs_missing": 0}

    # ประตูทั้งห้าเป็นของ `late_entry()` · ถามด้วย `route="trailing"` เสมอเพื่อให้ได้พรีวิวของ
    # ทางที่ 2 ไม่ว่า config เลือกทางไหน · `rr_too_low` แปลว่าผ่านทุกประตูแล้ว ตกที่ RR เท่านั้น
    preview = late_entry(plan, route="trailing", state=last.state, position_side=position,
                         allow_short=allow_short, feat=feat, trail_slow=trail_slow)
    if last.long_signal or last.short_signal:
        # แท่งสัญญาณไม่ใช่การตกรถ · `late_entry()` คืน `no_signal` ให้กรณีนี้ ซึ่งอ่านผิดได้
        reason = "signal_bar"
    elif preview.skip_reason in (None, "rr_too_low"):
        reason = None
    else:
        reason = preview.skip_reason

    ctx: dict[str, object] = base | {
        "cs_ready": True,
        "cs_eligible": reason is None,
        "cs_reason": NOT_ELIGIBLE.get(reason or "", reason),
        "cs_side": side,
        "cs_run": _state_run(zones),
        "cs_state": last.state.lower(),
        "cs_position": "flat" if position is None else position,
        "cs_pair": sym.symbol,
    }
    if reason is not None:
        return ctx

    route = intent_route if intent_route is not None else settings.cold_start
    actual = late_entry(plan, route=route, state=last.state, position_side=position,
                        allow_short=allow_short, feat=feat, trail_slow=trail_slow)
    if actual.side is not None and actual.route == "trailing":
        outcome = f"เข้าไม้ {actual.side} ที่ราคาปิด พร้อมตั้ง stop ที่ {actual.stop_px:,.2f}"
    elif actual.side is not None:
        outcome = "config เลือก wait_1h แต่ engine ยังไม่สร้างทางนี้ — บันทึกว่าตั้งใจให้รอ ไม่เข้าไม้"
    elif actual.skip_reason == "rr_too_low":
        outcome = f"ไม่เข้า — RR ไม่ถึง {MIN_REWARD_TO_RISK:g}:1"
    elif intent_route is not None:
        outcome = "ไม่เข้า — เจตนาที่เลือกไว้คือข้ามรอบนี้"
    else:
        outcome = "ไม่เข้า — config ไม่ได้เปิดเส้นทาง cold start ไว้"
    return ctx | {
        "cs_trailing": _trailing(preview, side, feat.close_px, trail, settings, sym),
        "cs_outcome": outcome,
        "cs_params": (
            ("AP1 / AF1", f"{FAST_ATR_PERIOD} / {FAST_ATR_FACTOR:g}"),
            ("AP2 / AF2", f"{SLOW_ATR_PERIOD} / {SLOW_ATR_FACTOR:g}"),
            ("Sig", f"ema(Hst, {SIGNAL_PERIOD})"),
        ),
    }
