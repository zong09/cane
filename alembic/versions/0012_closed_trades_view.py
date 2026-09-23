"""closed_trades — ไม้ที่ปิดแล้ว derive จาก `fills` + `funding_charges` (ADR 24)

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-23

ADR 24 บอกตรงตัวว่ากำไรขาดทุนของไม้ที่ปิดแล้วเป็น **VIEW ห้ามเก็บเป็นตาราง** — ตารางสรุปคือ
แหล่งความจริงที่สองที่จะขัดกับ ledger ในวันที่มีบั๊ก · ไฟล์นี้จึงไม่มี `CREATE TABLE` เลย

## ไม้หนึ่งไม้ = `trade_id` หนึ่งค่า และ "ปิดแล้ว" อ่านจาก fill ใบสุดท้าย

ไม่มีตาราง `trades` (ดู `trade_id_of()`) · ไม้ปิดแล้วเมื่อ fill ใบสุดท้ายของ `trade_id` นั้น
เป็นขาปิดหรือขา stop และ `position_qty_after = 0` · ไม้ที่ปิดไม่ครบ (ของค้างจาก `flip_aborted`)
จึงไม่โผล่ที่นี่ ซึ่งถูก — มันยังไม่ใช่ไม้ที่ปิดแล้ว

## gross กับ net ต่างกันด้วยต้นทุนสามก้อน และทั้งสามก้อนเป็นบวก = เสีย

- `pnl_px_quote` — ผลของราคาที่ fill จริง (slippage อยู่ในนี้แล้วเพราะเป็นราคาที่ได้จริง)
- `slippage_quote` — `px` เทียบ `ref_px` ต่อ fill · ซื้อแพงกว่าหรือขายถูกกว่าราคาอ้างอิง = บวก
- `fee_quote` — เฉพาะค่าธรรมเนียมที่เป็น**สกุล quote ของเหรียญ** ค่าธรรมเนียมสกุลอื่น (BNB,
  หรือ base ของ spot) บวกเข้ายอด USDT ไม่ได้ จึงนับเป็น "ไม่รู้" ไม่ใช่ศูนย์
- `funding_quote` — ตามที่ ledger เก็บ: บวก = ถูกหัก (`PaperBroker` กับ reconcile ตกลงกันไว้แล้ว)

`gross_quote = pnl_px_quote + slippage_quote` คือผลที่ราคาอ้างอิง ก่อนหักอะไรเลย ·
`net_quote = pnl_px_quote - fee_quote - funding_quote` คือเงินที่ได้จริง ·
% ทั้งสองตัวคิดบน **notional ขาเข้า** (`qty × px` ของขาเปิด) ไม่ใช่มาร์จิ้น — ตรงกับ
handoff §9.5 และไม่ขัด spec/05: `size_pct` กำหนดมาร์จิ้น ส่วนผลตอบแทนเป็นอีกคำถามหนึ่ง

## ต้นทุนที่หายต้องหายเสียงดัง (ADR 24 §ผลตามมา)

`cost_complete` เป็น `false` เมื่อ net ที่คืนไปอาจไม่ใช่ตัวจริง:

- มี fill ที่ไม่รู้ค่าธรรมเนียม หรือค่าธรรมเนียมเป็นสกุลอื่น (`fee_missing_fills`)
- มีรอบ funding ที่บันทึกไว้แต่ไม่รู้ยอด (`funding_cycles_unavailable`)
- รอบ funding ที่ควรมีตามกริด 8 ชม. มากกว่าที่บันทึกไว้ (`funding_cycles_missing`) ·
  กริดคือ `entry_ts < cycle <= exit_ts` แบบเดียวกับ `engine/funding_cycles.py`

**slippage ไม่อยู่ใน `cost_complete`** เพราะ net ไม่ได้พึ่งมัน — ราคาที่ fill จริงรวม slippage
ไว้แล้ว · fill ที่ไม่มี `ref_px` ทำให้ `gross_quote` ขาดไป ไม่ใช่ net · จึงมีตัวนับของมันเอง
(`slippage_missing_fills`) ให้หน้าที่แสดง gross เอาไปติดธง

## สิทธิ์

VIEW ไม่ได้เพิ่มสิทธิ์ให้ใคร — ได้แค่ `SELECT` และตารางใต้มันยังเป็น append-only (ADR 23) ·
`cane_engine` ต้องอ่านได้ด้วย เพราะตัวนับ "แพ้ติดกัน" ของ breaker จะมาวางบน VIEW นี้
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 8 ชั่วโมงเป็นมิลลิวินาที — เขียนซ้ำจาก `engine/funding_cycles.py` โดยเจตนา
#: migration เป็น snapshot ของวันที่เขียน ไม่ import โค้ดที่เปลี่ยนได้
FUNDING_INTERVAL_MS = 8 * 60 * 60 * 1000

CLOSED_TRADES = f"""
CREATE VIEW closed_trades AS
WITH f AS (
    SELECT
        fills.*,
        split_part(trade_id, ':', 3) AS side,
        split_part(symbol, '/', 2) AS quote_ccy,
        -- +1 = ออเดอร์ซื้อ · ขาเปิดของ long กับขาปิดของ short เป็นการซื้อ
        CASE WHEN (leg = 'open') = (split_part(trade_id, ':', 3) = 'long')
             THEN 1 ELSE -1 END AS buy_sign
    FROM fills
),
last_fill AS (
    SELECT DISTINCT ON (profile, trade_id)
        profile, trade_id, leg, fill_ts, bar_close_ts,
        position_qty_after, exit_reason, exit_detail
    FROM fills
    ORDER BY profile, trade_id, fill_ts DESC, id DESC
),
legs AS (
    SELECT
        profile,
        trade_id,
        min(market) AS market,
        min(symbol) AS symbol,
        min(side) AS side,
        min(fill_ts) FILTER (WHERE leg = 'open') AS entry_ts,
        min(bar_close_ts) FILTER (WHERE leg = 'open') AS open_bar_close_ts,
        sum(qty) FILTER (WHERE leg = 'open') AS qty,
        sum(qty * px) FILTER (WHERE leg = 'open') AS entry_notional,
        sum(qty) FILTER (WHERE leg <> 'open') AS exit_qty,
        sum(qty * px) FILTER (WHERE leg <> 'open') AS exit_value,
        max(leverage) FILTER (WHERE leg = 'open') AS leverage,
        coalesce(sum(fee_quote) FILTER (WHERE fee_ccy = quote_ccy), 0) AS fee_quote,
        count(*) FILTER (
            WHERE fee_quote IS NULL OR fee_ccy IS DISTINCT FROM quote_ccy
        ) AS fee_missing_fills,
        coalesce(sum(buy_sign * qty * (px - ref_px)) FILTER (WHERE ref_px IS NOT NULL), 0)
            AS slippage_quote,
        count(*) FILTER (WHERE ref_px IS NULL) AS slippage_missing_fills
    FROM f
    GROUP BY profile, trade_id
),
funding AS (
    SELECT
        profile,
        trade_id,
        coalesce(sum(amount_quote), 0) AS funding_quote,
        count(*) AS recorded,
        count(*) FILTER (WHERE amount_quote IS NULL) AS unavailable
    FROM funding_charges
    GROUP BY profile, trade_id
),
priced AS (
    SELECT
        l.*,
        lf.fill_ts AS exit_ts,
        lf.bar_close_ts AS close_bar_close_ts,
        lf.exit_reason,
        lf.exit_detail,
        l.entry_notional / l.qty AS entry_px,
        l.exit_value / l.exit_qty AS exit_px,
        CASE WHEN l.side = 'long' THEN 1 ELSE -1 END
            * (l.exit_value - l.entry_notional / l.qty * l.exit_qty) AS pnl_px_quote,
        coalesce(fu.funding_quote, 0) AS funding_quote,
        CASE WHEN l.market = 'usdtm_perp'
             THEN lf.fill_ts / {FUNDING_INTERVAL_MS} - l.entry_ts / {FUNDING_INTERVAL_MS}
             ELSE 0 END AS funding_cycles_expected,
        coalesce(fu.recorded, 0) AS funding_cycles_recorded,
        coalesce(fu.unavailable, 0) AS funding_cycles_unavailable
    FROM legs l
    JOIN last_fill lf USING (profile, trade_id)
    LEFT JOIN funding fu USING (profile, trade_id)
    WHERE lf.leg <> 'open' AND lf.position_qty_after = 0 AND l.qty IS NOT NULL
)
SELECT
    profile,
    market,
    symbol,
    trade_id,
    side,
    open_bar_close_ts,
    close_bar_close_ts,
    entry_ts,
    exit_ts,
    qty,
    entry_px,
    exit_px,
    entry_notional,
    leverage,
    exit_reason,
    exit_detail,
    pnl_px_quote,
    slippage_quote,
    slippage_missing_fills,
    fee_quote,
    fee_missing_fills,
    funding_quote,
    funding_cycles_expected,
    funding_cycles_recorded,
    funding_cycles_unavailable,
    greatest(funding_cycles_expected - funding_cycles_recorded, 0) AS funding_cycles_missing,
    pnl_px_quote + slippage_quote AS gross_quote,
    pnl_px_quote - fee_quote - funding_quote AS net_quote,
    (pnl_px_quote + slippage_quote) / entry_notional * 100 AS gross_pct,
    (pnl_px_quote - fee_quote - funding_quote) / entry_notional * 100 AS net_pct,
    fee_missing_fills = 0
        AND funding_cycles_unavailable = 0
        AND funding_cycles_recorded >= funding_cycles_expected AS cost_complete
FROM priced
"""


def upgrade() -> None:
    op.execute(CLOSED_TRADES)
    op.execute("GRANT SELECT ON TABLE closed_trades TO cane_engine")
    op.execute("GRANT SELECT ON TABLE closed_trades TO cane_console")


def downgrade() -> None:
    # GRANT หายไปเองพร้อม DROP VIEW
    op.execute("DROP VIEW closed_trades")
