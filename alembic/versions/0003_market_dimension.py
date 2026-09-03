"""market เป็นมิติของ symbol — spot อยู่ร่วมกับ perp ได้ (decisions #26)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-03

`market` ย้ายจากค่าระดับ profile (`config_settings.market` ที่ CHECK บังคับ `usdtm_perp`)
ลงไปเป็น **ค่าต่อเหรียญ** — profile เดียวจึงถือ BTC บน perp และ ETH บน spot พร้อมกันได้

**ไฟล์นี้แก้ตารางที่มีข้อมูลจริงอยู่แล้ว** เครื่อง dev seed `paper` v1 / `live` v1 ไว้ และ
`bars` อาจมีแท่งค้าง · คอลัมน์ใหม่จึงต้อง `DEFAULT` → backfill → `DROP DEFAULT` ไม่ใช่
`ADD COLUMN NOT NULL` เปล่าๆ ที่จะล้มทันทีบนตารางที่ไม่ว่าง · ค่า backfill เป็น
`usdtm_perp` ได้อย่างปลอดภัยเพราะ `ck_config_settings_market` เดิมบังคับไว้ตรงๆ ว่า
ทุกแถวที่มีอยู่คือ perp ไม่ใช่การเดา

**`bars` เปลี่ยน primary key** จาก `(symbol, timeframe, open_ts)` เป็น
`(market, symbol, timeframe, open_ts)` — `store_symbol()` ตัด `:USDT` ทิ้งเพื่อให้ตาราง
เก็บรูปเดียวกับที่ config เขียน (spec/07) ผลคือ `BTC/USDT` ของ spot กับของ perp มีชื่อ
เดียวกัน ถ้าไม่แยกด้วยคอลัมน์ สองตลาดจะทับกันเงียบๆ แล้ว indicator จะคำนวณบนแท่งที่
ปนกันสองตลาด · เก็บเป็นคอลัมน์ ไม่ใช่เข้ารหัสไว้ในสตริง เพราะ `WHERE market = 'spot'`
ต้องเขียนได้ ไม่ใช่ต้องไล่ `LIKE '%:USDT'`

**ไม่มี GRANT ในไฟล์นี้** — grant ระดับตารางของ `0001`/`0002` ครอบคอลัมน์ใหม่ให้เอง
ไม่มีตารางใหม่เกิดขึ้นเลย · `tests/test_db_grants.py` ยิงให้เห็นว่ายังปฏิเสธจริง
ไม่ใช่เชื่อว่า Postgres ทำให้
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: ตลาดที่รองรับ — เขียนเป็นค่าคงที่ในไฟล์ migration เอง ไม่ import จาก `schema.py`
#: migration เป็น snapshot ของวันที่เขียน ไม่ใช่ของ schema วันนี้
MARKETS = "'usdtm_perp', 'spot'"

#: ค่าของแถวที่มีอยู่ก่อน migration นี้ — CHECK เดิมบังคับไว้ว่าเป็น perp ทั้งหมด
LEGACY_MARKET = "usdtm_perp"


def upgrade() -> None:
    # ── config_symbols ────────────────────────────────────────────────────
    op.add_column(
        "config_symbols",
        sa.Column("market", sa.Text(), nullable=False, server_default=LEGACY_MARKET),
    )
    op.alter_column("config_symbols", "market", server_default=None)
    op.create_check_constraint(
        "ck_config_symbols_market", "config_symbols", f"market IN ({MARKETS})"
    )
    # สามข้อนี้ไม่ใช่กฎที่ตั้งใหม่ แต่เป็นการเขียนสิ่งที่ตลาด spot **ไม่มี** ลงไป
    # ให้ฐานปฏิเสธ แทนที่จะให้โค้ดชั้นบนคอยจำ
    op.create_check_constraint(
        "ck_config_symbols_spot_no_short", "config_symbols", "market <> 'spot' OR NOT allow_short"
    )
    op.create_check_constraint(
        "ck_config_symbols_spot_no_leverage", "config_symbols", "market <> 'spot' OR leverage = 1"
    )
    op.create_check_constraint(
        "ck_config_symbols_spot_no_short_bucket",
        "config_symbols",
        "market <> 'spot' OR bucket_quote_short IS NULL",
    )

    # ── config_settings ───────────────────────────────────────────────────
    # แหล่งความจริงของตลาดอยู่ที่ `config_symbols` แล้ว การเก็บไว้สองที่คือการเปิดทาง
    # ให้มันขัดกันเอง · CHECK ของคอลัมน์หายไปพร้อมคอลัมน์
    op.drop_column("config_settings", "market")

    # ── bars ──────────────────────────────────────────────────────────────
    op.add_column(
        "bars", sa.Column("market", sa.Text(), nullable=False, server_default=LEGACY_MARKET)
    )
    op.alter_column("bars", "market", server_default=None)
    op.create_check_constraint("ck_bars_market", "bars", f"market IN ({MARKETS})")
    op.drop_constraint("bars_pkey", "bars", type_="primary")
    op.create_primary_key("bars_pkey", "bars", ["market", "symbol", "timeframe", "open_ts"])


def downgrade() -> None:
    # `bars` ล้มเองอยู่แล้วตอนยุบ `market` ทิ้ง (สองตลาดชนกันที่ PK) แต่ `config_symbols`
    # **ไม่ล้ม** — การ drop คอลัมน์จะสำเร็จเงียบๆ แล้วเหรียญ spot ทุกตัวถูกติดป้ายใหม่
    # เป็น perp ซึ่งคือการเปลี่ยนความหมายของข้อมูลโดยไม่มีใครเห็น จึงต้องกันไว้เอง
    spot = op.get_bind().execute(
        sa.text("SELECT count(*) FROM config_symbols WHERE market = 'spot'")
    ).scalar_one()
    if spot:
        raise RuntimeError(
            f"downgrade ไม่ได้: มีเหรียญ spot อยู่ {spot} แถวใน config_symbols — "
            "schema เก่ารองรับแต่ usdtm_perp การถอยจะทำให้ของพวกนี้กลายเป็น perp เงียบๆ "
            "ต้องลบหรือย้ายเหรียญ spot ออกก่อน"
        )

    op.drop_constraint("bars_pkey", "bars", type_="primary")
    op.create_primary_key("bars_pkey", "bars", ["symbol", "timeframe", "open_ts"])
    op.drop_constraint("ck_bars_market", "bars", type_="check")
    op.drop_column("bars", "market")

    # คืนคอลัมน์ด้วยท่าเดียวกับขา upgrade — ตารางไม่ว่าง `ADD COLUMN NOT NULL` เปล่าๆ ล้ม
    op.add_column(
        "config_settings",
        sa.Column("market", sa.Text(), nullable=False, server_default=LEGACY_MARKET),
    )
    op.alter_column("config_settings", "market", server_default=None)
    op.create_check_constraint(
        "ck_config_settings_market", "config_settings", "market = 'usdtm_perp'"
    )

    for name in (
        "ck_config_symbols_spot_no_short_bucket",
        "ck_config_symbols_spot_no_leverage",
        "ck_config_symbols_spot_no_short",
        "ck_config_symbols_market",
    ):
        op.drop_constraint(name, "config_symbols", type_="check")
    op.drop_column("config_symbols", "market")
