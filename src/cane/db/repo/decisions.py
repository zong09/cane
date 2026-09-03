"""ตาราง `decisions` — บันทึกการตัดสินใจหนึ่งแถวต่อหนึ่งแท่งต่อหนึ่ง symbol

แทน `DecisionRecord` แบบ JSONL ที่ spec/07:139 เขียนไว้ (decisions #22 ย้ายลงฐาน)
หัวใจไม่เปลี่ยน: **เขียนทุกกรณี รวมถึงตอนที่ผลคือ "ไม่ทำอะไร"** (decisions #11, spec/08:67)

หนึ่งรอบการตัดสินใจ = หัว + ลูกทั้งหมด ลงพร้อมกันหรือไม่ลงเลย · โมดูลนี้ **ไม่ commit**
ผู้เรียกเป็นคนถือทรานแซกชัน (ดู `repo/__init__.py`)

`validate_record()` แยกเป็นฟังก์ชันบริสุทธิ์ที่ไม่รับ `Connection` เพราะกฎที่มันบังคับ
เป็นกฎข้ามตาราง/ข้ามแถวที่ CHECK เขียนไม่ได้ และต้องทดสอบได้โดยไม่ต้องมี Postgres
(`tests/test_decision_record.py` รันใน `-m "not db"`)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import Connection, select

from cane.config.settings import require_scale
from cane.db.schema import (
    decision_flip,
    decision_orders,
    decision_risk_checks,
    decision_stop,
    decision_unmanaged,
    decision_verdicts,
    decisions,
)
from cane.db.types import (
    PCT_SCALE,
    PRICE_SCALE,
    RATE_SCALE,
    now_ms,
    pct_from_db,
    pct_to_db,
    price_from_db,
    price_to_db,
    rate_from_db,
    rate_to_db,
    store_symbol,
)

#: ชุดปิดของ `skip_reason` (`ck_decisions_skip_reason`) — ตอบคำถามเดียวว่า
#: "ทำไมไม่มีออเดอร์เปิด (`leg = open`) ถูกส่ง" หนึ่งค่าต่อแถว เลือกจากประตูแรกที่ปิด
SKIP_REASONS = (
    "flip_aborted",
    "no_signal",
    "already_positioned",
    "short_disabled",
    "cane_rule",
    "rr_too_low",
    "risk_rejected",
    "order_error",
    "dry_run",
)


@dataclass(frozen=True, slots=True)
class Verdict:
    """คำตัดสินของ Judge ต่อ factor หนึ่งตัว (spec/04:50-58)

    `confidence` ใช้สำหรับให้คนอ่านย้อนหลังเท่านั้น **ห้ามผูกกับขนาดไม้**
    (decisions #12, spec/05:52) · `cached` แยก "มาจาก cache" ออกจาก "เรียกจริง"
    """

    factor: str
    side: str
    present: bool
    cached: bool
    confidence: float | None = None
    evidence_bars: tuple[int, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class RiskCheck:
    """ผลการตรวจ risk หนึ่งชั้น · `seq` คือลำดับที่ชั้นนั้นถูกเรียกจริง (spec/08:39)"""

    seq: int
    layer: str
    passed: bool
    value: float | None = None
    limit_value: float | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class OrderAttempt:
    """ออเดอร์หนึ่งขาที่พยายามส่ง — รวมขาที่พังก่อนส่ง (`sent = False`, `error` มีค่า)"""

    leg: str
    order_side: str
    order_type: str
    reduce_only: bool
    qty: float
    client_order_id: str
    sent: bool
    accepted: bool
    stop_px: float | None = None
    venue_order_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Flip:
    """ผลของการกลับข้างสองขาในแท่งเดียว (spec/03:53-83) — ไม่มีบน spot"""

    close_qty_intended: float
    close_qty_filled: float
    residual_qty: float
    aborted: bool
    residual_side: str | None = None


@dataclass(frozen=True, slots=True)
class Stop:
    """stop order ของแท่งนี้ · `action = missing` คือหา stop ที่ควรมีไม่เจอ (spec/08:80)"""

    action: str
    px: float | None = None
    stop_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Unmanaged:
    """ของค้างที่ระบบไม่ได้ตั้งใจถือ — เขียนซ้ำทุกแท่งจนกว่าคนจะปิด (decisions #19)"""

    side: str
    qty: float
    source: str
    first_seen_bar_close_ts: int


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """บันทึกครบชุดของหนึ่งแท่ง

    ฟิลด์ที่ไม่มีค่าตั้งต้นคือฟิลด์ที่เกิดบนทุกเส้นทาง · ที่เหลือเว้นได้ เพราะแท่งที่จบ
    ด้วย `no_signal` มีแค่หัวกับ `zone`/`state`/`close_px` (spec/08:67)

    `id` `created_ts` `utc_day` เป็น `None` ตอนประกอบเพื่อเขียน และมีค่าตอนอ่านกลับ
    """

    profile: str
    market: str
    symbol: str
    timeframe: str
    bar_close_ts: int
    decided_ts: int
    config_version_id: int
    close_px: float
    zone: str
    state: str
    long_signal: bool
    short_signal: bool
    dry_run: bool
    side: str | None = None
    cold_start: str | None = None
    leverage: float | None = None
    margin_mode: str | None = None
    judge_called: bool | None = None
    llm_fallback: bool | None = None
    llm_fallback_reason: str | None = None
    prompt_hash: str | None = None
    factors_present: int | None = None
    size_rule: str | None = None
    size_pct_formula: float | None = None
    size_pct_final: float | None = None
    capped: bool | None = None
    margin: float | None = None
    notional: float | None = None
    qty: float | None = None
    ref_px: float | None = None
    skip_reason: str | None = None
    funding_rate: float | None = None
    funding_next_ts: int | None = None
    funding_unavailable_reason: str | None = None
    verdicts: tuple[Verdict, ...] = ()
    risk_checks: tuple[RiskCheck, ...] = ()
    orders: tuple[OrderAttempt, ...] = ()
    unmanaged: tuple[Unmanaged, ...] = ()
    flip: Flip | None = None
    stop: Stop | None = None
    id: int | None = None
    created_ts: int | None = None
    utc_day: int | None = None


# ── กฎที่ DB เขียนเป็น CHECK ไม่ได้ ──────────────────────────────────────────


def validate_record(record: DecisionRecord) -> None:
    """ปฏิเสธบันทึกที่ผิดกฎ **ก่อน** เขียน ไม่ใช่เขียนลงแล้วอ่านกลับไม่ได้

    CHECK ของ Postgres เห็นได้แค่คอลัมน์ในแถวเดียวกัน กฎด้านล่างจึงเขียนที่นั่นไม่ได้:
    สามข้อแรกข้ามตาราง (`decisions` ↔ `decision_orders`) หรือข้ามแถว
    (`decision_risk_checks`) และสามข้อหลังต้องเห็น `market`/`dry_run` ของหัวพร้อมกับ
    แถวของลูก ซึ่งจะเขียนเป็น CHECK ได้ต้องพา `market` ลงลูกทั้งหกตารางแล้วขยาย
    composite FK ตามไปด้วย — ไม่คุ้มกับกฎสามข้อ

    ใบ 03b เจอมาแล้วว่าแถวที่ผ่าน CHECK ทุกข้อยังเป็นแถวที่อ่านกลับไม่ได้ ตัวตรวจนี้
    จึงถูกเรียกจาก `insert_decision()` ทุกครั้ง ไม่ใช่ของที่ผู้เรียกเลือกเรียกเอง
    """
    _check_scales(record)
    _check_skip_reason(record)
    _check_risk_sequence(record)
    _check_market_rules(record)
    _check_dry_run(record)


def _open_orders(record: DecisionRecord) -> tuple[OrderAttempt, ...]:
    return tuple(order for order in record.orders if order.leg == "open")


def _check_skip_reason(record: DecisionRecord) -> None:
    """`skip_reason IS NULL ⟺ มีออเดอร์เปิดที่ venue **รับแล้ว**`

    เกณฑ์คือ `accepted` ไม่ใช่ `sent` — ออเดอร์ที่ venue ปฏิเสธคือ `sent = True`
    แต่ `accepted = False` ถ้าใช้ `sent` เป็นเกณฑ์ แถวนั้นจะถูกบังคับให้
    `skip_reason` เป็น `None` ทั้งที่เหตุผลจริงคือ `order_error` ซึ่งขัดกันเอง

    และ `order_error` ผูกกับ `error IS NOT NULL` ไม่ใช่ `sent AND NOT accepted`
    เพราะเกณฑ์หลังทิ้งช่อง "พังก่อนส่ง" (`sent = False` แต่ `error` มีค่า) ไว้โดย
    ไม่มี `skip_reason` ค่าไหนรองรับ
    """
    if record.skip_reason is not None and record.skip_reason not in SKIP_REASONS:
        known = ", ".join(SKIP_REASONS)
        raise ValueError(f"skip_reason {record.skip_reason!r} ไม่รู้จัก — มีแต่ {known}")

    opens = _open_orders(record)
    accepted = any(order.accepted for order in opens)
    if accepted and record.skip_reason is not None:
        raise ValueError(
            f"มีออเดอร์เปิดที่ venue รับแล้ว แต่ skip_reason = {record.skip_reason!r} — "
            "ไม้ที่เข้าได้ไม่มีเหตุผลที่ไม่เข้า"
        )
    if not accepted and record.skip_reason is None:
        raise ValueError(
            "ไม่มีออเดอร์เปิดที่ venue รับ แต่ skip_reason เป็น None — "
            "แท่งที่ไม่ได้เข้าไม้ต้องบอกได้ว่าเพราะอะไร (spec/08:67)"
        )

    # `order_error` ถูกตรวจเฉพาะตอนที่ **ไม่มี** ออเดอร์เปิดที่ถูกรับ เพราะ `skip_reason`
    # ตอบคำถามเดียวว่า "ทำไมไม่มีออเดอร์เปิดถูกส่ง" โดยเลือกจาก **ประตูแรกที่ปิด** —
    # แท่งที่ retry แล้วสำเร็จ (ครั้งแรกมี error ครั้งที่สอง accepted) ไม่มีประตูไหนปิด
    # ถ้าเทียบแบบ biconditional ล้วน แถวนั้นจะถูกบังคับให้เป็นทั้ง `None` และ
    # `'order_error'` พร้อมกัน แล้วเขียนไม่ลงเลย ซึ่งเป็นสิ่งที่ PK ของ `decision_orders`
    # ตั้งใจรองรับอยู่แล้ว (ขาเดียวกันซ้ำได้ตอน retry)
    if not accepted:
        failed = any(order.error is not None for order in opens)
        if failed and record.skip_reason != "order_error":
            raise ValueError(
                "มีออเดอร์เปิดที่มี error แต่ skip_reason ไม่ใช่ 'order_error' "
                f"(เป็น {record.skip_reason!r})"
            )
        if record.skip_reason == "order_error" and not failed:
            raise ValueError(
                "skip_reason = 'order_error' แต่ไม่มีออเดอร์เปิดที่มี error — "
                "เหตุผลไม่มีหลักฐานรองรับ"
            )


def _check_risk_sequence(record: DecisionRecord) -> None:
    """`seq` เรียง 1..n ไม่มีช่อง · ไม่ผ่านได้ไม่เกินหนึ่งชั้น และต้องเป็นชั้นสุดท้าย

    spec/08:39 ตรวจทีละชั้นและชั้นแรกที่ไม่ผ่านปฏิเสธทั้งไม้ → ชั้นที่ตามหลังชั้นที่
    ไม่ผ่านจะไม่ถูกเรียกเลย · แถวที่ขัดกฎนี้ไม่ใช่ข้อมูลที่แปลก แต่เป็นบันทึกที่บอกว่า
    ระบบตรวจต่อหลังจากปฏิเสธไปแล้ว ซึ่งไม่ใช่สิ่งที่เกิดขึ้นจริง
    """
    checks = record.risk_checks
    if not checks:
        return
    seqs = [check.seq for check in checks]
    if seqs != list(range(1, len(seqs) + 1)):
        raise ValueError(
            f"seq ของ risk check ต้องเรียง 1..{len(seqs)} ไม่มีช่อง — ได้ {seqs}"
        )
    failed = [check.seq for check in checks if not check.passed]
    if len(failed) > 1:
        raise ValueError(
            f"risk check ที่ไม่ผ่านมีได้ไม่เกินหนึ่งชั้น — ได้ seq {failed}"
        )
    if failed and failed[0] != len(seqs):
        raise ValueError(
            f"ชั้นที่ไม่ผ่านต้องเป็นชั้นสุดท้ายที่บันทึก (seq {len(seqs)}) — "
            f"ได้ seq {failed[0]}"
        )


def _check_market_rules(record: DecisionRecord) -> None:
    """สิ่งที่ตลาด spot **ไม่มี** (decisions #26)

    spot ไม่มี flip เพราะสัญญาณแดงคือ "ขายออกให้แบน" จบ ไม่มีขาเปิดตาม (spec/03:20)
    และไม่มี `reduceOnly` ให้ใช้ — ทั้งสองข้อผูกกับ `market` ของหัว ซึ่งลูกมองไม่เห็น
    """
    if record.market != "spot":
        return
    if record.flip is not None:
        raise ValueError("ไม้ spot มี decision_flip ไม่ได้ — ไม่มี flip บน spot (spec/03:20)")
    for order in record.orders:
        if order.reduce_only:
            raise ValueError(
                f"ออเดอร์ขา {order.leg!r} ของไม้ spot ตั้ง reduce_only — "
                "spot ไม่มีพารามิเตอร์นี้"
            )
    for order in _open_orders(record):
        if order.order_side == "sell":
            raise ValueError(
                "ไม้ spot มีออเดอร์เปิดฝั่ง sell ไม่ได้ — สัญญาณแดงบน spot จบที่ leg=close"
            )


def _check_dry_run(record: DecisionRecord) -> None:
    """`dry_run` = คำนวณครบและเขียนบันทึกครบ แต่ **ไม่ส่งคำสั่งจริง** (spec/06:80-84)

    ขั้น 13 ของ spec/08:43 ข้ามการเปิดสถานะ → ออเดอร์เปิดที่ `sent = True` บนแท่ง
    dry run คือหลักฐานว่ามีคำสั่งหลุดออกไปจริง ซึ่งเป็นเรื่องที่ต้องดังตอนเขียน
    """
    if not record.dry_run:
        return
    for order in _open_orders(record):
        if order.sent:
            raise ValueError(
                "แท่ง dry_run มีออเดอร์เปิดที่ sent = True — โหมดนี้ห้ามส่งคำสั่งจริง"
            )


def _check_scales(record: DecisionRecord) -> None:
    """ค่าที่ละเอียดกว่าคอลัมน์ **ถูกปฏิเสธ ไม่ปัดให้** (กฎที่ใบ 03b ตั้งไว้)

    `price_to_db()`/`pct_to_db()`/`rate_to_db()` ปัดด้วย ROUND_HALF_EVEN ตัวปฏิเสธ
    จึงต้องมาก่อน ไม่งั้นบันทึกจะไม่ตรงกับค่าที่คำนวณจริงโดยไม่มีใครเห็น
    """
    for name in ("close_px", "margin", "notional", "qty", "ref_px"):
        _scaled(getattr(record, name), PRICE_SCALE, name)
    for name in ("leverage", "size_pct_formula", "size_pct_final"):
        _scaled(getattr(record, name), PCT_SCALE, name)
    _scaled(record.funding_rate, RATE_SCALE, "funding_rate")

    for verdict in record.verdicts:
        _scaled(verdict.confidence, PCT_SCALE, f"verdicts[{verdict.factor}].confidence")
    for check in record.risk_checks:
        _scaled(check.value, PRICE_SCALE, f"risk_checks[{check.seq}].value")
        _scaled(check.limit_value, PRICE_SCALE, f"risk_checks[{check.seq}].limit_value")
    for order in record.orders:
        _scaled(order.qty, PRICE_SCALE, f"orders[{order.client_order_id}].qty")
        _scaled(order.stop_px, PRICE_SCALE, f"orders[{order.client_order_id}].stop_px")
    if record.flip is not None:
        for name in ("close_qty_intended", "close_qty_filled", "residual_qty"):
            _scaled(getattr(record.flip, name), PRICE_SCALE, f"flip.{name}")
    if record.stop is not None:
        _scaled(record.stop.px, PRICE_SCALE, "stop.px")
    for held in record.unmanaged:
        _scaled(held.qty, PRICE_SCALE, f"unmanaged[{held.side}].qty")


def _scaled(value: float | None, places: int, name: str) -> None:
    if value is not None:
        require_scale(value, places, name)


# ── เขียน ───────────────────────────────────────────────────────────────────


def insert_decision(
    conn: Connection, record: DecisionRecord, *, created_ts: int | None = None
) -> int:
    """เขียนหัวและลูกทั้งหมดในทรานแซกชันของผู้เรียก — คืน `id` ของหัว

    **ไม่ commit** และ **ไม่มี `ON CONFLICT`** — กุญแจธรรมชาติไม่ unique โดยเจตนา
    แถวที่สองของแท่งเดิมคือหลักฐานของ restart ไม่ใช่แถวซ้ำที่ต้องกลืน (spec/06:127)
    """
    validate_record(record)
    stamp = now_ms() if created_ts is None else created_ts
    profile = record.profile

    decision_id = conn.execute(
        decisions.insert()
        .values(
            profile=profile,
            market=record.market,
            symbol=store_symbol(record.symbol),
            timeframe=record.timeframe,
            bar_close_ts=record.bar_close_ts,
            decided_ts=record.decided_ts,
            config_version_id=record.config_version_id,
            close_px=price_to_db(record.close_px),
            zone=record.zone,
            state=record.state,
            long_signal=record.long_signal,
            short_signal=record.short_signal,
            side=record.side,
            cold_start=record.cold_start,
            dry_run=record.dry_run,
            leverage=_pct(record.leverage),
            margin_mode=record.margin_mode,
            judge_called=record.judge_called,
            llm_fallback=record.llm_fallback,
            llm_fallback_reason=record.llm_fallback_reason,
            prompt_hash=record.prompt_hash,
            factors_present=record.factors_present,
            size_rule=record.size_rule,
            size_pct_formula=_pct(record.size_pct_formula),
            size_pct_final=_pct(record.size_pct_final),
            capped=record.capped,
            margin=_price(record.margin),
            notional=_price(record.notional),
            qty=_price(record.qty),
            ref_px=_price(record.ref_px),
            skip_reason=record.skip_reason,
            funding_rate=None if record.funding_rate is None else rate_to_db(record.funding_rate),
            funding_next_ts=record.funding_next_ts,
            funding_unavailable_reason=record.funding_unavailable_reason,
            created_ts=stamp,
        )
        .returning(decisions.c.id)
    ).scalar_one()

    if record.verdicts:
        conn.execute(
            decision_verdicts.insert(),
            [
                {
                    "decision_id": decision_id,
                    "profile": profile,
                    "factor": verdict.factor,
                    "side": verdict.side,
                    "present": verdict.present,
                    "confidence": _pct(verdict.confidence),
                    "evidence_bars": list(verdict.evidence_bars) or None,
                    "rationale": verdict.rationale,
                    "cached": verdict.cached,
                    "created_ts": stamp,
                }
                for verdict in record.verdicts
            ],
        )

    if record.risk_checks:
        conn.execute(
            decision_risk_checks.insert(),
            [
                {
                    "decision_id": decision_id,
                    "profile": profile,
                    "seq": check.seq,
                    "layer": check.layer,
                    "passed": check.passed,
                    "value": _price(check.value),
                    "limit_value": _price(check.limit_value),
                    "detail": check.detail,
                    "created_ts": stamp,
                }
                for check in record.risk_checks
            ],
        )

    if record.orders:
        conn.execute(
            decision_orders.insert(),
            [
                {
                    "decision_id": decision_id,
                    "profile": profile,
                    "leg": order.leg,
                    "order_side": order.order_side,
                    "order_type": order.order_type,
                    "reduce_only": order.reduce_only,
                    "qty": price_to_db(order.qty),
                    "stop_px": _price(order.stop_px),
                    "client_order_id": order.client_order_id,
                    "sent": order.sent,
                    "accepted": order.accepted,
                    "venue_order_id": order.venue_order_id,
                    "error": order.error,
                    "created_ts": stamp,
                }
                for order in record.orders
            ],
        )

    if record.flip is not None:
        conn.execute(
            decision_flip.insert().values(
                decision_id=decision_id,
                profile=profile,
                close_qty_intended=price_to_db(record.flip.close_qty_intended),
                close_qty_filled=price_to_db(record.flip.close_qty_filled),
                residual_qty=price_to_db(record.flip.residual_qty),
                residual_side=record.flip.residual_side,
                aborted=record.flip.aborted,
                created_ts=stamp,
            )
        )

    if record.stop is not None:
        conn.execute(
            decision_stop.insert().values(
                decision_id=decision_id,
                profile=profile,
                action=record.stop.action,
                px=_price(record.stop.px),
                stop_order_id=record.stop.stop_order_id,
                created_ts=stamp,
            )
        )

    if record.unmanaged:
        conn.execute(
            decision_unmanaged.insert(),
            [
                {
                    "decision_id": decision_id,
                    "profile": profile,
                    "side": held.side,
                    "qty": price_to_db(held.qty),
                    "source": held.source,
                    "first_seen_bar_close_ts": held.first_seen_bar_close_ts,
                    "created_ts": stamp,
                }
                for held in record.unmanaged
            ],
        )

    return decision_id


def _price(value: float | None):  # noqa: ANN202
    return None if value is None else price_to_db(value)


def _pct(value: float | None):  # noqa: ANN202
    return None if value is None else pct_to_db(value)


# ── อ่าน ────────────────────────────────────────────────────────────────────


def decisions_for(
    conn: Connection,
    profile: str,
    market: str,
    symbol: str,
    timeframe: str,
    *,
    since: int | None = None,
    limit: int | None = None,
) -> list[DecisionRecord]:
    """ลำดับการตัดสินใจของเหรียญหนึ่ง เรียงตามเวลาแท่ง แล้วตาม `id`

    เรียงด้วย `id` เป็นตัวที่สองเพราะแท่งเดียวมีได้หลายแถว (restart กลางแท่ง) —
    ลำดับที่ไม่แน่นอนจะทำให้ "แถวล่าสุดของแท่งนั้น" เปลี่ยนไปเรื่อยๆ
    """
    stmt = (
        select(decisions)
        .where(
            decisions.c.profile == profile,
            decisions.c.market == market,
            decisions.c.symbol == store_symbol(symbol),
            decisions.c.timeframe == timeframe,
        )
        .order_by(decisions.c.bar_close_ts, decisions.c.id)
    )
    if since is not None:
        stmt = stmt.where(decisions.c.bar_close_ts >= since)
    if limit is not None:
        stmt = stmt.limit(limit)
    return _load(conn, conn.execute(stmt).all())


def decision_at(
    conn: Connection,
    profile: str,
    market: str,
    symbol: str,
    timeframe: str,
    bar_close_ts: int,
) -> DecisionRecord | None:
    """บันทึก **แถวล่าสุด** ของแท่งนั้น หรือ `None` ถ้าไม่มี

    แท่งเดียวมีได้หลายแถวเพราะกุญแจธรรมชาติไม่ unique — แถวล่าสุดคือรอบที่เดินจบ
    ทีหลังสุด · ผู้เรียกที่ต้องเห็นทุกรอบให้ใช้ `decisions_for()` แล้วกรองเอง
    """
    row = conn.execute(
        select(decisions)
        .where(
            decisions.c.profile == profile,
            decisions.c.market == market,
            decisions.c.symbol == store_symbol(symbol),
            decisions.c.timeframe == timeframe,
            decisions.c.bar_close_ts == bar_close_ts,
        )
        .order_by(decisions.c.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return _load(conn, [row])[0]


def _load(conn: Connection, rows: Sequence) -> list[DecisionRecord]:  # noqa: ANN001
    """ประกอบหัวกับลูกเป็น dataclass — อ่านลูกครั้งเดียวต่อชุด ไม่ใช่ครั้งเดียวต่อแท่ง"""
    if not rows:
        return []
    ids = [row.id for row in rows]

    verdicts: dict[int, list[Verdict]] = {}
    for row in conn.execute(
        select(decision_verdicts)
        .where(decision_verdicts.c.decision_id.in_(ids))
        .order_by(decision_verdicts.c.factor)
    ).all():
        verdicts.setdefault(row.decision_id, []).append(
            Verdict(
                factor=row.factor,
                side=row.side,
                present=row.present,
                cached=row.cached,
                confidence=None if row.confidence is None else pct_from_db(row.confidence),
                evidence_bars=tuple(row.evidence_bars or ()),
                rationale=row.rationale,
            )
        )

    checks: dict[int, list[RiskCheck]] = {}
    for row in conn.execute(
        select(decision_risk_checks)
        .where(decision_risk_checks.c.decision_id.in_(ids))
        .order_by(decision_risk_checks.c.seq)
    ).all():
        checks.setdefault(row.decision_id, []).append(
            RiskCheck(
                seq=row.seq,
                layer=row.layer,
                passed=row.passed,
                value=None if row.value is None else price_from_db(row.value),
                limit_value=(
                    None if row.limit_value is None else price_from_db(row.limit_value)
                ),
                detail=row.detail,
            )
        )

    orders: dict[int, list[OrderAttempt]] = {}
    for row in conn.execute(
        select(decision_orders)
        .where(decision_orders.c.decision_id.in_(ids))
        .order_by(decision_orders.c.id)
    ).all():
        orders.setdefault(row.decision_id, []).append(
            OrderAttempt(
                leg=row.leg,
                order_side=row.order_side,
                order_type=row.order_type,
                reduce_only=row.reduce_only,
                qty=price_from_db(row.qty),
                client_order_id=row.client_order_id,
                sent=row.sent,
                accepted=row.accepted,
                stop_px=None if row.stop_px is None else price_from_db(row.stop_px),
                venue_order_id=row.venue_order_id,
                error=row.error,
            )
        )

    held: dict[int, list[Unmanaged]] = {}
    for row in conn.execute(
        select(decision_unmanaged)
        .where(decision_unmanaged.c.decision_id.in_(ids))
        .order_by(decision_unmanaged.c.side)
    ).all():
        held.setdefault(row.decision_id, []).append(
            Unmanaged(
                side=row.side,
                qty=price_from_db(row.qty),
                source=row.source,
                first_seen_bar_close_ts=row.first_seen_bar_close_ts,
            )
        )

    flips = {
        row.decision_id: Flip(
            close_qty_intended=price_from_db(row.close_qty_intended),
            close_qty_filled=price_from_db(row.close_qty_filled),
            residual_qty=price_from_db(row.residual_qty),
            aborted=row.aborted,
            residual_side=row.residual_side,
        )
        for row in conn.execute(
            select(decision_flip).where(decision_flip.c.decision_id.in_(ids))
        ).all()
    }

    stops = {
        row.decision_id: Stop(
            action=row.action,
            px=None if row.px is None else price_from_db(row.px),
            stop_order_id=row.stop_order_id,
        )
        for row in conn.execute(
            select(decision_stop).where(decision_stop.c.decision_id.in_(ids))
        ).all()
    }

    return [
        DecisionRecord(
            profile=row.profile,
            market=row.market,
            symbol=row.symbol,
            timeframe=row.timeframe,
            bar_close_ts=row.bar_close_ts,
            decided_ts=row.decided_ts,
            config_version_id=row.config_version_id,
            close_px=price_from_db(row.close_px),
            zone=row.zone,
            state=row.state,
            long_signal=row.long_signal,
            short_signal=row.short_signal,
            dry_run=row.dry_run,
            side=row.side,
            cold_start=row.cold_start,
            leverage=None if row.leverage is None else pct_from_db(row.leverage),
            margin_mode=row.margin_mode,
            judge_called=row.judge_called,
            llm_fallback=row.llm_fallback,
            llm_fallback_reason=row.llm_fallback_reason,
            prompt_hash=row.prompt_hash,
            factors_present=row.factors_present,
            size_rule=row.size_rule,
            size_pct_formula=(
                None if row.size_pct_formula is None else pct_from_db(row.size_pct_formula)
            ),
            size_pct_final=(
                None if row.size_pct_final is None else pct_from_db(row.size_pct_final)
            ),
            capped=row.capped,
            margin=None if row.margin is None else price_from_db(row.margin),
            notional=None if row.notional is None else price_from_db(row.notional),
            qty=None if row.qty is None else price_from_db(row.qty),
            ref_px=None if row.ref_px is None else price_from_db(row.ref_px),
            skip_reason=row.skip_reason,
            funding_rate=None if row.funding_rate is None else rate_from_db(row.funding_rate),
            funding_next_ts=row.funding_next_ts,
            funding_unavailable_reason=row.funding_unavailable_reason,
            verdicts=tuple(verdicts.get(row.id, ())),
            risk_checks=tuple(checks.get(row.id, ())),
            orders=tuple(orders.get(row.id, ())),
            unmanaged=tuple(held.get(row.id, ())),
            flip=flips.get(row.id),
            stop=stops.get(row.id),
            id=row.id,
            created_ts=row.created_ts,
            utc_day=row.utc_day,
        )
        for row in rows
    ]
