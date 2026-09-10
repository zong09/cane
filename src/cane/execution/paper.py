"""`PaperBroker` — ปลายทางจำลองที่เขียน ledger จริง

**ตัวมันเองไม่รู้จัก `dry_run`** และนั่นตั้งใจ · profile `paper` ถูก CHECK บังคับ
`dry_run = true` ตายตัว (spec/07) ถ้า broker ตัวนี้เคารพธงนั้น มันจะไม่เขียน ledger
เลยสักครั้ง ซึ่งขัดกับเหตุผลที่มันมีอยู่ · ประตูของ `dry_run` อยู่ที่ engine ว่าจะ
เรียก `place()` หรือไม่ (spec/08 ขั้น 13) และมีหลักฐานบังคับอยู่แล้วที่
`repo/decisions.py:_check_dry_run()` ซึ่งห้ามแท่ง dry run มีออเดอร์ที่ `sent = True`

## เวลาเดินตอนไหน — `positions()` และ `open_orders()` เป็นตัวเดินให้

ผู้เรียกไม่ต้องบอก broker ว่าแท่งใหม่มาแล้ว มันไปอ่านจาก `BarSource` เอง แล้วเดิน
จำลองข้ามแท่งที่ยังไม่ได้คิด · ทางเลือกอีกทางคือให้ engine เรียก `settle(bar)` เอง
ซึ่งตรงไปตรงมากว่า แต่แปลว่า engine ต้องมี `if` แยก paper กับ live ซึ่งขัด ADR 9
ที่ทั้งระบบยืนอยู่บนมัน · ท่านี้ใช้กับ `ReplayBarSource` ของใบ 12 ได้โดยไม่แก้อะไร

**ผลข้างเคียงที่ต้องรู้:** การ*อ่าน*สถานะเขียน ledger ได้ (stop ที่ทำงานไปแล้ว,
funding ที่ถูกหัก) — จึงรับ `conn` เข้ามาและเขียนในทรานแซกชันของผู้เรียก ตามท่าของ
`repo/` ทั้งชุด ไม่เปิดทรานแซกชันของตัวเอง

## สิ่งที่จำลองไม่ได้ และเลือกไว้อย่างไร

มีแต่ OHLC ไม่มีเส้นทางราคาภายในแท่ง ทุกข้อข้างล่างจึงเป็น**ข้อตกลง** ไม่ใช่ความจริง:

- **market order fill ที่ราคาปิดของแท่งล่าสุดที่ปิดแล้ว** — ตรงกับจังหวะที่ engine
  ตัดสินใจ (spec/08 ทำงานเมื่อแท่งปิด) ไม่ใส่ slippage เพราะไม่มีอะไรให้อ้างอิง
  `ref_px` จึงเท่ากับ `px` เสมอในโหมดนี้ และรายงาน slippage ของ paper จะเป็นศูนย์
  อย่างซื่อสัตย์ ไม่ใช่เป็นตัวเลขที่แต่งขึ้น
- **stop ที่ราคากระโดดข้าม fill ที่ราคาเปิด ไม่ใช่ที่ราคา stop** — แท่งที่เปิดมาต่ำ
  กว่า stop ของ long อยู่แล้วแปลว่าไม่มีใครได้ราคานั้น การ fill ที่ `stop_px` จะทำให้
  paper รายงานผลดีกว่าความจริงตรงจุดที่เจ็บที่สุด
- **ถ้าทั้ง stop และ liquidation อยู่ในช่วงของแท่งเดียวกัน ตัวที่ใกล้ราคาเปิดกว่า
  ทำงานก่อน** — ไม่ใช่การเดา แต่มาจากลำดับของราคาเอง: ของ long นั้น `stop_px` สูงกว่า
  `liq_px` เสมอเมื่อ `min_liq_buffer_pct` ทำงานถูกต้อง ราคาที่ไหลลงจึงผ่าน stop ก่อน
- **funding ไม่มีค่าตั้งต้น** ต้องฉีด `funding_source` เข้ามาเมื่อเป็นตลาด perp
  ไม่งั้นสร้าง broker ไม่ขึ้น — การเดารอบและอัตราเองจะทำให้ P&L ของ paper ต่างจาก
  ของจริงด้วยตัวเลขที่ไม่มีใครกรอก

## สูตร liquidation

isolated margin หนึ่งไม้ต่อหนึ่งเหรียญ (`position_mode = one_way`):

    long : liq = entry × (1 − 1/leverage + mmr)
    short: liq = entry × (1 + 1/leverage − mmr)

`mmr` = `maintenance_margin_pct / 100` จาก config · **spot ไม่มี liquidation อยู่จริง**
ไม่ใช่มีแล้วไกลมาก (ADR 26) `liquidation_px` ของมันจึงเป็น `None`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

from sqlalchemy import Connection

from cane.data.ohlcv import Bar, BarSource
from cane.db.repo import ledger as ledger_repo
from cane.db.repo.ledger import Fill, FundingCharge, dedupe_key_of, trade_id_of
from cane.db.types import price_to_db, store_symbol
from cane.execution.broker import (
    Balance,
    OpenOrder,
    Order,
    OrderResult,
    Position,
    check_market_supports,
)

#: รอบ funding ที่ตกในช่วง `(after_ts, through_ts]` พร้อมอัตราของรอบนั้น
#: อัตราเป็น `None` = รู้ว่ามีรอบ แต่ไม่รู้อัตรา — ต่างจากไม่มีรอบเลย
FundingSource = Callable[[str, int, int], Sequence[tuple[int, float | None]]]

PERP = "usdtm_perp"
SPOT = "spot"


class PaperError(RuntimeError):
    """สิ่งที่ปลายทางจริงจะปฏิเสธ — จำลองแล้วต้องปฏิเสธเหมือนกัน ไม่ใช่ปล่อยผ่าน"""


@dataclass
class _Sim:
    """สถานะจำลองของหนึ่งเหรียญ · `qty = 0` แปลว่าไม่มีไม้"""

    symbol: str
    side: str = "long"
    qty: float = 0.0
    entry_px: float = 0.0
    trade_id: str = ""
    leverage: float = 1.0
    margin: Decimal = Decimal("0")
    settled_through_ts: int = 0
    #: ลำดับของ fill **ภายในออเดอร์ใบเดียวกัน** ต่อ `client_order_id` — ไม่ใช่ตัวนับ
    #: รวมของเหรียญ · ถ้านับรวม คีย์ของ fill จะขึ้นกับว่ามีออเดอร์อื่นมาก่อนกี่ใบ
    #: ซึ่งเปลี่ยนไปเมื่อ process รีสตาร์ท แล้วการกันเขียนซ้ำจะไม่ทำงานเลย
    fill_seq: dict[str, int] = field(default_factory=dict)
    stop: OpenOrder | None = None
    stop_seq: int = 0


@dataclass
class PaperBroker:
    """หนึ่งตัวต่อหนึ่งตลาด — ท่าเดียวกับ `make_client(exchange, market)` ของชั้นข้อมูล"""

    conn: Connection
    market: str
    profile: str
    bars: BarSource
    timeframe: str
    seed_quote: Decimal | None
    taker_fee_pct: float | None
    maintenance_margin_pct: float | None
    funding_source: FundingSource | None = None
    quote: str = "USDT"

    _cash: Decimal = field(init=False)
    _sims: dict[str, _Sim] = field(init=False, default_factory=dict)
    _leverage: dict[str, float] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        if self.market not in (PERP, SPOT):
            raise ValueError(f"market ต้องเป็น {PERP} หรือ {SPOT} ไม่ใช่ {self.market!r}")
        # ประตูที่ migration 0005 ตั้งใจให้อยู่ตรงนี้ ไม่ใช่ที่ NOT NULL ของคอลัมน์ —
        # เวอร์ชัน config ที่ seed ไว้ก่อนสองค่านี้จะมีอยู่ต้องอ่านกลับได้ ส่วนการ
        # *ใช้* มันต้องมีค่าครบ
        missing = [
            name
            for name, value in (
                ("seed_quote", self.seed_quote),
                ("taker_fee_pct", self.taker_fee_pct),
                ("maintenance_margin_pct", self.maintenance_margin_pct),
            )
            if value is None
        ]
        if missing:
            raise PaperError(
                "PaperBroker ต้องมี " + ", ".join(missing) + " จาก config — "
                "ไม่มีค่าเหล่านี้แล้วคิด P&L และหาราคา liquidation ไม่ได้ "
                "(migration 0005 · spec/07)"
            )
        if self.market == PERP and self.funding_source is None:
            raise PaperError(
                "ตลาด usdtm_perp ต้องมี funding_source — การเดารอบและอัตราเองจะทำให้ "
                "P&L ของ paper ต่างจากของจริงด้วยตัวเลขที่ไม่มีใครกรอก"
            )
        # ผ่าน `str` เสมอ ไม่ใช่ `Decimal(float)` — `db/types.py` อธิบายไว้ว่าทำไม
        self._cash = _money(self.seed_quote)

    # ── สิ่งที่ผู้เรียกใช้จริง ────────────────────────────────────────────────

    def place(self, order: Order) -> OrderResult:
        check_market_supports(order, self.market)
        symbol = store_symbol(order.symbol)
        self._settle(symbol)
        sim = self._sim(symbol)

        if order.type == "stop_market":
            return self._arm_stop(sim, order)
        return self._fill_market(sim, order)

    def cancel(self, order_id: str) -> None:
        for sim in self._sims.values():
            if sim.stop is not None and sim.stop.venue_order_id == order_id:
                sim.stop = None
                return
        raise PaperError(f"ไม่มีออเดอร์ค้างชื่อ {order_id!r} ให้ยกเลิก")

    def replace(self, order_id: str, stop_px: float) -> OrderResult:
        """ขยับจุดป้องกัน ไม่ใช่เปิดความเสี่ยงใหม่ — ทำได้แม้ kill switch latch (spec/06)"""
        for sim in self._sims.values():
            if sim.stop is not None and sim.stop.venue_order_id == order_id:
                moved = OpenOrder(
                    venue_order_id=sim.stop.venue_order_id,
                    client_order_id=sim.stop.client_order_id,
                    symbol=sim.stop.symbol,
                    side=sim.stop.side,
                    type=sim.stop.type,
                    qty=sim.stop.qty,
                    stop_px=stop_px,
                    reduce_only=sim.stop.reduce_only,
                )
                sim.stop = moved
                return OrderResult(
                    client_order_id=moved.client_order_id or "",
                    venue_order_id=moved.venue_order_id,
                    status="open",
                    filled_qty=0.0,
                )
        raise PaperError(f"ไม่มีออเดอร์ค้างชื่อ {order_id!r} ให้ขยับ")

    def open_orders(self, symbol: str) -> list[OpenOrder]:
        key = store_symbol(symbol)
        self._settle(key)
        sim = self._sims.get(key)
        return [] if sim is None or sim.stop is None else [sim.stop]

    def positions(self) -> list[Position]:
        """เดินจำลองให้ทันแท่งล่าสุดก่อนตอบเสมอ — ห้ามตอบจากความจำที่ค้างอยู่"""
        out: list[Position] = []
        for symbol in list(self._sims):
            self._settle(symbol)
            sim = self._sims[symbol]
            if sim.qty <= 0:
                continue
            mark = self._mark(symbol)
            out.append(
                Position(
                    symbol=sim.symbol,
                    side=sim.side,
                    qty=sim.qty,
                    entry_px=sim.entry_px,
                    mark_px=mark,
                    unrealized_pnl=_money(_pnl(sim.side, sim.entry_px, mark, sim.qty)),
                    leverage=sim.leverage,
                    liquidation_px=self._liq_px(sim),
                )
            )
        return out

    def balance(self) -> Balance:
        for symbol in list(self._sims):
            self._settle(symbol)
        used = sum((sim.margin for sim in self._sims.values()), Decimal("0"))
        return Balance(quote=self.quote, free=self._cash, used=used, total=self._cash + used)

    def set_leverage(self, symbol: str, leverage: float) -> None:
        if self.market == SPOT:
            raise PaperError("spot ไม่มี leverage — บังคับเป็น 1 ตั้งแต่ config (ADR 26)")
        if leverage <= 0:
            raise ValueError(f"leverage ต้องมากกว่าศูนย์ ไม่ใช่ {leverage}")
        self._leverage[store_symbol(symbol)] = leverage

    def set_margin_mode(self, symbol: str, mode: str, position_mode: str) -> None:
        if self.market == SPOT:
            raise PaperError("spot ไม่มี margin mode")
        if mode != "isolated":
            raise PaperError(f"จำลองได้เฉพาะ isolated ไม่ใช่ {mode!r} — สูตร liq ของไฟล์นี้เป็นของ isolated")
        if position_mode != "one_way":
            raise PaperError("hedge ไม่ใช่ตัวเลือกที่ปิดไว้ แต่เป็นค่าที่ระบบไม่รองรับ (spec/07)")

    # ── การเดินเวลา ──────────────────────────────────────────────────────────

    def _settle(self, symbol: str) -> None:
        """เดินจำลองข้ามทุกแท่งที่ปิดแล้วแต่ยังไม่ได้คิด"""
        sim = self._sims.get(symbol)
        if sim is None:
            return
        for bar in self.bars.bars(symbol, self.timeframe):
            if bar.close_ts <= sim.settled_through_ts:
                continue
            if sim.qty > 0:
                self._charge_funding(sim, bar)
                self._maybe_exit(sim, bar)
            sim.settled_through_ts = bar.close_ts

    def _charge_funding(self, sim: _Sim, bar: Bar) -> None:
        if self.market != PERP or self.funding_source is None:
            return
        for cycle_ts, rate in self.funding_source(sim.symbol, sim.settled_through_ts, bar.close_ts):
            if rate is None:
                charge = FundingCharge(
                    profile=self.profile,
                    symbol=sim.symbol,
                    trade_id=sim.trade_id,
                    cycle_ts=cycle_ts,
                    position_qty=sim.qty,
                    unavailable_reason="ไม่มีอัตราของรอบนี้ให้จำลอง",
                )
            else:
                # ฝั่ง long จ่ายเมื่ออัตราเป็นบวก ฝั่ง short รับ — เครื่องหมายกลับกัน
                amount = _money(rate * bar.close * sim.qty * (1 if sim.side == "long" else -1))
                charge = FundingCharge(
                    profile=self.profile,
                    symbol=sim.symbol,
                    trade_id=sim.trade_id,
                    cycle_ts=cycle_ts,
                    position_qty=sim.qty,
                    rate=rate,
                    amount_quote=amount,
                    mark_px=bar.close,
                )
                self._cash -= amount
            ledger_repo.record_funding_charge(self.conn, charge)

    def _maybe_exit(self, sim: _Sim, bar: Bar) -> None:
        """stop และ liquidation ในแท่งเดียวกัน — ตัวที่ใกล้ราคาเปิดกว่าทำงานก่อน"""
        levels: list[tuple[float, str]] = []
        if sim.stop is not None and sim.stop.stop_px is not None:
            if _crossed(sim.side, sim.stop.stop_px, bar):
                levels.append((sim.stop.stop_px, "stop"))
        liq = self._liq_px(sim)
        if liq is not None and _crossed(sim.side, liq, bar):
            levels.append((liq, "liquidation"))
        if not levels:
            return

        # ราคาไหลลงสำหรับ long จึงผ่านระดับที่สูงกว่าก่อน · short กลับกัน
        levels.sort(key=lambda item: item[0], reverse=(sim.side == "long"))
        level, reason = levels[0]
        # แท่งที่เปิดมาเลยระดับไปแล้วแปลว่าไม่มีใครได้ราคานั้น — fill ที่ราคาเปิด
        px = bar.open if _crossed(sim.side, level, bar, at_open=True) else level
        self._close(sim, px, bar, reason, leg="stop" if reason == "stop" else "close")

    # ── การเขียน fill ────────────────────────────────────────────────────────

    def _fill_market(self, sim: _Sim, order: Order) -> OrderResult:
        bar = self._last_bar(sim.symbol)
        px = bar.close
        if order.reduce_only or (self.market == SPOT and order.side == "sell"):
            if sim.qty <= 0:
                raise PaperError(f"{sim.symbol} ไม่มีไม้ให้ปิด")
            if order.qty > sim.qty + 1e-12:
                # spot ขายเกินที่ถือไม่ได้จริง และ reduceOnly ของ perp ก็ปิดเกินไม่ได้
                raise PaperError(
                    f"ปิดเกินที่ถืออยู่: สั่ง {order.qty} แต่ถือ {sim.qty}"
                )
            return self._close(sim, px, bar, "signal", leg="close", qty=order.qty,
                               order=order)
        return self._open(sim, px, bar, order)

    def _open(self, sim: _Sim, px: float, bar: Bar, order: Order) -> OrderResult:
        if sim.qty > 0:
            raise PaperError(
                f"{sim.symbol} ถือไม้อยู่แล้ว — ไม่มี pyramiding และห้ามเปิดสวน (spec/03)"
            )
        # บน spot ไปไม่ถึงค่า `short` — คำสั่ง `sell` ถูกส่งเข้าทางปิดเสมอที่
        # `_fill_market()` เพราะการขายบน spot คือการขายของที่ถืออยู่ ไม่ใช่การเปิด
        # ฝั่งใหม่ (spec/03:22) · ตัวที่ปฏิเสธจริงจึงเป็นเส้นทาง ไม่ใช่ `if` ตรงนี้
        side = "long" if order.side == "buy" else "short"

        leverage = 1.0 if self.market == SPOT else self._leverage.get(sim.symbol, 0.0)
        if leverage <= 0:
            # spec/08 สั่งให้ตั้ง leverage ที่ปลายทางก่อนเปิดไม้ทุกครั้ง เพราะค่าที่
            # exchange ถูกเปลี่ยนจากนอกระบบได้ · จำลองก็ต้องบังคับเหมือนกัน
            raise PaperError(
                f"ยังไม่ได้เรียก set_leverage สำหรับ {sim.symbol} — spec/08 บังคับให้ตั้ง"
                " leverage ที่ปลายทางก่อนเปิดไม้ทุกครั้ง"
            )

        notional = px * order.qty
        margin = _money(notional / leverage)
        fee = _fee(notional, self.taker_fee_pct)
        if self._cash < margin + fee:
            raise PaperError(f"เงินไม่พอ: ต้องใช้ {margin + fee} แต่มี {self._cash}")

        self._cash -= margin + fee
        sim.side = side
        sim.qty = order.qty
        sim.entry_px = px
        sim.leverage = leverage
        sim.margin = margin
        sim.trade_id = trade_id_of(self.market, sim.symbol, side, bar.close_ts)
        sim.settled_through_ts = bar.close_ts

        return self._write(sim, order.client_order_id, "open", px, order.qty, bar,
                           fee, order.type, order.reduce_only, sim.qty, None, None)

    def _close(
        self,
        sim: _Sim,
        px: float,
        bar: Bar,
        reason: str,
        *,
        leg: str,
        qty: float | None = None,
        order: Order | None = None,
    ) -> OrderResult:
        closing = sim.qty if qty is None else qty
        share = Decimal(str(closing / sim.qty))
        released = _money(sim.margin * share)
        realized = _money(_pnl(sim.side, sim.entry_px, px, closing))
        fee = _fee(px * closing, self.taker_fee_pct)

        self._cash += released + realized - fee
        sim.margin -= released
        sim.qty -= closing
        if sim.qty <= 1e-12:
            sim.qty = 0.0
            sim.margin = Decimal("0")
            sim.stop = None

        coid = (
            order.client_order_id
            if order is not None
            else f"{sim.trade_id}-{reason}-{bar.close_ts}"
        )
        return self._write(
            sim, coid, leg, px, closing, bar, fee,
            order.type if order is not None else "stop_market" if leg == "stop" else "market",
            True if order is None else order.reduce_only,
            sim.qty, reason, None,
        )

    def _write(
        self, sim: _Sim, coid: str, leg: str, px: float, qty: float, bar: Bar,
        fee: Decimal, order_type: str, reduce_only: bool, qty_after: float,
        exit_reason: str | None, exit_detail: str | None,
    ) -> OrderResult:
        seq = sim.fill_seq.get(coid, 0)
        sim.fill_seq[coid] = seq + 1
        key = dedupe_key_of(coid, seq)
        ledger_repo.record_fill(
            self.conn,
            Fill(
                profile=self.profile,
                market=self.market,
                symbol=sim.symbol,
                trade_id=sim.trade_id,
                leg=leg,
                fill_ts=bar.close_ts,
                px=px,
                qty=qty,
                client_order_id=coid,
                order_type=order_type,
                # spot ไม่มีธงนี้อยู่จริง — CHECK ของ `fills` ปฏิเสธด้วย
                reduce_only=False if self.market == SPOT else reduce_only,
                position_qty_after=qty_after,
                bar_close_ts=bar.close_ts,
                dedupe_key=key,
                # ไม่มี slippage ให้อ้างอิงในโหมดนี้ ราคาที่ตัดสินใจคือราคาที่ได้
                ref_px=px,
                fee_quote=fee,
                fee_ccy=self.quote,
                leverage=None if self.market == SPOT else sim.leverage,
                exit_reason=exit_reason,
                exit_detail=exit_detail,
            ),
        )
        return OrderResult(
            client_order_id=coid,
            venue_order_id=key,
            status="closed",
            filled_qty=qty,
            avg_px=px,
            fee_quote=fee,
            fee_ccy=self.quote,
        )

    def _arm_stop(self, sim: _Sim, order: Order) -> OrderResult:
        if sim.qty <= 0:
            raise PaperError(f"{sim.symbol} ไม่มีไม้ให้วาง stop คุ้ม")
        sim.stop_seq += 1
        venue_id = f"{sim.trade_id}-stop-{sim.stop_seq}"
        sim.stop = OpenOrder(
            venue_order_id=venue_id,
            client_order_id=order.client_order_id,
            symbol=sim.symbol,
            side=order.side,
            type="stop_market",
            qty=order.qty,
            stop_px=order.stop_px,
            reduce_only=order.reduce_only,
        )
        return OrderResult(
            client_order_id=order.client_order_id,
            venue_order_id=venue_id,
            status="open",
            filled_qty=0.0,
        )

    # ── ตัวช่วยเล็กๆ ─────────────────────────────────────────────────────────

    def _sim(self, symbol: str) -> _Sim:
        return self._sims.setdefault(symbol, _Sim(symbol=symbol))

    def _last_bar(self, symbol: str) -> Bar:
        bars = self.bars.bars(symbol, self.timeframe)
        if not bars:
            raise PaperError(f"ไม่มีแท่งที่ปิดแล้วของ {symbol} ให้อ้างราคา")
        return bars[-1]

    def _mark(self, symbol: str) -> float:
        return self._last_bar(symbol).close

    def _liq_px(self, sim: _Sim) -> float | None:
        """`None` บน spot — ตลาดนั้นไม่มี liquidation อยู่จริง (ADR 26)"""
        if self.market == SPOT or sim.qty <= 0:
            return None
        mmr = (self.maintenance_margin_pct or 0.0) / 100
        edge = 1 / sim.leverage - mmr
        return sim.entry_px * (1 - edge) if sim.side == "long" else sim.entry_px * (1 + edge)


def _pnl(side: str, entry_px: float, px: float, qty: float) -> float:
    return (px - entry_px) * qty if side == "long" else (entry_px - px) * qty


def _fee(notional: float, taker_fee_pct: float | None) -> Decimal:
    return _money(notional * (taker_fee_pct or 0.0) / 100)


def _money(value: float | Decimal) -> Decimal:
    """`float` → `Decimal` ผ่าน `str` เสมอ ตามกฎของ `db/types.py`"""
    return price_to_db(value)


def _crossed(side: str, level: float, bar: Bar, *, at_open: bool = False) -> bool:
    """ราคาของแท่งนี้ไปถึงระดับนั้นหรือยัง — long ดูขาลง short ดูขาขึ้น"""
    if side == "long":
        return (bar.open if at_open else bar.low) <= level
    return (bar.open if at_open else bar.high) >= level
