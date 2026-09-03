"""decisions tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-03

บันทึกการตัดสินใจย้ายจาก JSONL มาเป็นตารางใน PostgreSQL (decisions #22) — หัวหนึ่งแถว
ต่อหนึ่งแท่งต่อหนึ่ง symbol พร้อมลูกหกตัวที่เก็บของที่เป็นหลายแถวจริง (verdict ต่อ factor,
risk ต่อชั้น, ออเดอร์ต่อขา, flip, stop, ของค้างที่ระบบไม่ได้ตั้งใจถือ)

**ตารางทั้งชุดเป็นตารางข้อเท็จจริง** (decisions #23) → `cane_engine` ได้ `SELECT, INSERT`
`cane_console` ได้ `SELECT` อย่างเดียว **ไม่มี `UPDATE`/`DELETE` ให้ใครเลย** ·
ชื่อตารางเขียนเป็นค่าคงที่ในไฟล์นี้เอง ไม่ import จาก `schema.py` เพราะ migration เป็น
snapshot ของวันที่เขียน ไม่ใช่ของ schema วันนี้

ENUM ใหม่ห้าตัวสร้างด้วย `CREATE TYPE` เองที่หัว `upgrade()` แล้วให้ทุกคอลัมน์อ้างด้วย
`create_type=False` — ถ้าปล่อยให้ `create_table` สร้างให้ ลำดับการสร้างจะขึ้นกับว่าตารางไหน
อ้าง type ก่อน ซึ่งเป็นรายละเอียดที่เปลี่ยนได้เองเวลาเพิ่มตารางใหม่
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: ตารางบันทึกทั้งชุด เรียงตามลำดับที่ต้องสร้าง (หัวก่อนลูก) — ค่าคงที่ของไฟล์นี้
DECISION_TABLES = (
    "decisions",
    "decision_verdicts",
    "decision_risk_checks",
    "decision_orders",
    "decision_flip",
    "decision_stop",
    "decision_unmanaged",
)

#: ENUM ที่ไฟล์นี้เป็นคนสร้าง จึงเป็นคนลบตอน downgrade — `profile_t` (0001) และ
#: `margin_mode_t` (0002) ไม่แตะ
NEW_ENUMS = (
    ("zone_t", "'GREEN', 'BLUE', 'LBLUE', 'RED', 'ORANGE', 'YELLOW', 'BLACK'"),
    ("state_t", "'BULLISH', 'BEARISH', 'UNSET'"),
    ("side_t", "'long', 'short'"),
    ("order_side_t", "'buy', 'sell'"),
    ("leg_t", "'open', 'close', 'stop'"),
)

_PROFILE_T = postgresql.ENUM("live", "paper", name="profile_t", create_type=False)
_MARGIN_MODE_T = postgresql.ENUM(
    "isolated", "cross", name="margin_mode_t", create_type=False
)
_ZONE_T = postgresql.ENUM(
    "GREEN",
    "BLUE",
    "LBLUE",
    "RED",
    "ORANGE",
    "YELLOW",
    "BLACK",
    name="zone_t",
    create_type=False,
)
_STATE_T = postgresql.ENUM(
    "BULLISH", "BEARISH", "UNSET", name="state_t", create_type=False
)
_SIDE_T = postgresql.ENUM("long", "short", name="side_t", create_type=False)
_ORDER_SIDE_T = postgresql.ENUM("buy", "sell", name="order_side_t", create_type=False)
_LEG_T = postgresql.ENUM("open", "close", "stop", name="leg_t", create_type=False)


def upgrade() -> None:
    for name, values in NEW_ENUMS:
        op.execute(f"CREATE TYPE {name} AS ENUM ({values})")

    op.create_table(
        "decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("bar_close_ts", sa.BigInteger(), nullable=False),
        sa.Column(
            "utc_day",
            sa.Integer(),
            sa.Computed("bar_close_ts / 86400000", persisted=True),
            nullable=True,
        ),
        sa.Column("decided_ts", sa.BigInteger(), nullable=False),
        sa.Column("config_version_id", sa.BigInteger(), nullable=False),
        sa.Column("close_px", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("zone", _ZONE_T, nullable=False),
        sa.Column("state", _STATE_T, nullable=False),
        sa.Column("long_signal", sa.Boolean(), nullable=False),
        sa.Column("short_signal", sa.Boolean(), nullable=False),
        sa.Column("side", _SIDE_T, nullable=True),
        sa.Column("cold_start", sa.Text(), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("leverage", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("margin_mode", _MARGIN_MODE_T, nullable=True),
        sa.Column("judge_called", sa.Boolean(), nullable=True),
        sa.Column("llm_fallback", sa.Boolean(), nullable=True),
        sa.Column("llm_fallback_reason", sa.Text(), nullable=True),
        sa.Column("prompt_hash", sa.Text(), nullable=True),
        sa.Column("factors_present", sa.Integer(), nullable=True),
        sa.Column("size_rule", sa.Text(), nullable=True),
        sa.Column("size_pct_formula", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("size_pct_final", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("capped", sa.Boolean(), nullable=True),
        sa.Column("margin", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("notional", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("qty", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("ref_px", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("funding_rate", sa.Numeric(precision=12, scale=10), nullable=True),
        sa.Column("funding_next_ts", sa.BigInteger(), nullable=True),
        sa.Column("funding_unavailable_reason", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "cold_start IS NULL OR cold_start IN ('wait_1h', 'trailing', 'skip')",
            name="ck_decisions_cold_start",
        ),
        sa.CheckConstraint(
            "market <> 'spot' OR (funding_rate IS NULL AND funding_next_ts IS NULL "
            "AND funding_unavailable_reason IS NULL)",
            name="ck_decisions_spot_no_funding",
        ),
        sa.CheckConstraint(
            "market <> 'spot' OR (leverage = 1 AND margin_mode IS NULL)",
            name="ck_decisions_spot_no_leverage",
        ),
        sa.CheckConstraint(
            "market <> 'spot' OR side IS NULL OR side = 'long'",
            name="ck_decisions_spot_long_only",
        ),
        sa.CheckConstraint(
            "market IN ('usdtm_perp', 'spot')", name="ck_decisions_market"
        ),
        sa.CheckConstraint(
            "size_rule IS NULL OR size_rule IN ('confluence', 'cold_start', 'none')",
            name="ck_decisions_size_rule",
        ),
        sa.CheckConstraint(
            "skip_reason IS NULL OR skip_reason IN ("
            "'flip_aborted', 'no_signal', 'already_positioned', 'short_disabled', "
            "'cane_rule', 'rr_too_low', 'risk_rejected', 'order_error', 'dry_run')",
            name="ck_decisions_skip_reason",
        ),
        sa.CheckConstraint("timeframe IN ('1h', '1d')", name="ck_decisions_timeframe"),
        sa.CheckConstraint(
            "NOT (long_signal AND short_signal)", name="ck_decisions_signal_exclusive"
        ),
        sa.CheckConstraint(
            "factors_present IS NULL OR factors_present BETWEEN 0 AND 3",
            name="ck_decisions_factors_present",
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id", "profile"],
            ["config_versions.id", "config_versions.profile"],
            name="fk_decisions_config_version",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "profile", name="uq_decisions_id_profile"),
    )
    # กุญแจธรรมชาติ **ไม่ unique** โดยเจตนา — แถวที่สองของกุญแจเดียวกันคือหลักฐานของ
    # restart กลางแท่ง ไม่ใช่ข้อเท็จจริงซ้ำ (spec/06:127)
    op.create_index(
        "ix_decisions_natural",
        "decisions",
        ["profile", "market", "symbol", "timeframe", "bar_close_ts"],
        unique=False,
    )

    op.create_table(
        "decision_verdicts",
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("factor", sa.Text(), nullable=False),
        sa.Column("side", _SIDE_T, nullable=False),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("evidence_bars", postgresql.ARRAY(sa.BigInteger()), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "factor IN ('CHANNEL_BREAKOUT', 'RETAIL_CAPITULATION', 'HIGHER_LOW', "
            "'CHANNEL_BREAKDOWN', 'BUYING_EXHAUSTION', 'LOWER_HIGH')",
            name="ck_decision_verdicts_factor",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "profile"],
            ["decisions.id", "decisions.profile"],
            name="fk_decision_verdicts_decision",
        ),
        sa.PrimaryKeyConstraint("decision_id", "factor"),
    )

    op.create_table(
        "decision_risk_checks",
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("limit_value", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "layer IN ('kill_switch', 'daily_loss', 'liq_buffer')",
            name="ck_decision_risk_checks_layer",
        ),
        sa.CheckConstraint("seq > 0", name="ck_decision_risk_checks_seq"),
        sa.ForeignKeyConstraint(
            ["decision_id", "profile"],
            ["decisions.id", "decisions.profile"],
            name="fk_decision_risk_checks_decision",
        ),
        sa.PrimaryKeyConstraint("decision_id", "seq"),
    )

    op.create_table(
        "decision_orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("leg", _LEG_T, nullable=False),
        sa.Column("order_side", _ORDER_SIDE_T, nullable=False),
        sa.Column("order_type", sa.Text(), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("stop_px", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("client_order_id", sa.Text(), nullable=False),
        sa.Column("sent", sa.Boolean(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("venue_order_id", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "order_type IN ('market', 'stop_market')",
            name="ck_decision_orders_order_type",
        ),
        sa.CheckConstraint(
            "NOT (sent AND NOT accepted) OR error IS NOT NULL",
            name="ck_decision_orders_rejected_needs_error",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "profile"],
            ["decisions.id", "decisions.profile"],
            name="fk_decision_orders_decision",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_decision_orders_decision", "decision_orders", ["decision_id"], unique=False
    )

    op.create_table(
        "decision_flip",
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("close_qty_intended", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("close_qty_filled", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("residual_qty", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("residual_side", _SIDE_T, nullable=True),
        sa.Column("aborted", sa.Boolean(), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "residual_qty = 0 OR residual_side IS NOT NULL",
            name="ck_decision_flip_residual_needs_side",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "profile"],
            ["decisions.id", "decisions.profile"],
            name="fk_decision_flip_decision",
        ),
        sa.PrimaryKeyConstraint("decision_id"),
    )

    op.create_table(
        "decision_stop",
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("px", sa.Numeric(precision=24, scale=8), nullable=True),
        sa.Column("stop_order_id", sa.Text(), nullable=True),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "action IN ('placed', 'replaced', 'unchanged', 'missing')",
            name="ck_decision_stop_action",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "profile"],
            ["decisions.id", "decisions.profile"],
            name="fk_decision_stop_decision",
        ),
        sa.PrimaryKeyConstraint("decision_id"),
    )

    op.create_table(
        "decision_unmanaged",
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("profile", _PROFILE_T, nullable=False),
        sa.Column("side", _SIDE_T, nullable=False),
        sa.Column("qty", sa.Numeric(precision=24, scale=8), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("first_seen_bar_close_ts", sa.BigInteger(), nullable=False),
        sa.Column("created_ts", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id", "profile"],
            ["decisions.id", "decisions.profile"],
            name="fk_decision_unmanaged_decision",
        ),
        sa.PrimaryKeyConstraint("decision_id", "side"),
    )

    for table in DECISION_TABLES:
        # ตารางข้อเท็จจริง — เขียนได้ครั้งเดียว แก้ไม่ได้ ลบไม่ได้ (decisions #23)
        # คอลัมน์ id เป็น IDENTITY ซึ่ง sequence เป็นของตาราง สิทธิ์ INSERT บนตารางพอแล้ว
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO cane_engine")
        op.execute(f"GRANT SELECT ON TABLE {table} TO cane_console")


def downgrade() -> None:
    # **ไม่มี guard แบบ 0003** — ไฟล์นี้ลบแต่ตารางที่ตัวเองสร้าง ไม่ได้เปลี่ยนความหมาย
    # ของข้อมูลที่มีอยู่ก่อน · GRANT หายไปเองพร้อม DROP TABLE
    op.drop_table("decision_unmanaged")
    op.drop_table("decision_stop")
    op.drop_table("decision_flip")
    op.drop_index("ix_decision_orders_decision", table_name="decision_orders")
    op.drop_table("decision_orders")
    op.drop_table("decision_risk_checks")
    op.drop_table("decision_verdicts")
    op.drop_index("ix_decisions_natural", table_name="decisions")
    op.drop_table("decisions")

    for name, _ in NEW_ENUMS:
        op.execute(f"DROP TYPE {name}")
