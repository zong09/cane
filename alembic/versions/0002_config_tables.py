"""config ลง DB — versioned, immutable (แทน ADR 18)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02

DB เป็นแหล่งจริงของ config · ไฟล์ TOML เหลือหน้าที่ seed ครั้งแรกผ่าน `cane db seed`

**สิทธิ์ต่างจากตารางข้อเท็จจริงของ 0001** — config ไม่ใช่ข้อเท็จจริงที่เกิดแล้วจบ
มันคือของที่คนแก้: `cane_console` จึง `INSERT` ได้ (สร้างเวอร์ชันใหม่) ส่วน `cane_engine`
**อ่านได้เท่านั้น** engine ไม่ใช่คนแก้ config ของตัวเอง

`UPDATE` ให้เฉพาะคอลัมน์ `config_versions.is_active` คอลัมน์เดียว (grant ระดับคอลัมน์)
เพราะเนื้อของเวอร์ชันต้อง immutable ส่วนที่ขยับได้คือ **ตัวชี้** ว่าเวอร์ชันไหนใช้อยู่ —
ถ้าให้ UPDATE ทั้งตาราง คำว่า immutable จะกลับไปเป็นข้อตกลงที่โค้ดผิดพลาดทับได้อีก
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONFIG_TABLES = (
    "config_versions",
    "config_settings",
    "config_symbols",
    "config_risk",
    "config_broker",
    "config_data",
)


def upgrade() -> None:
    # สร้าง type เองแบบเปิดเผย แล้วให้ทุกคอลัมน์อ้างด้วย `create_type=False` —
    # ถ้าปล่อยให้ `create_table` สร้าง type ให้ ลำดับการสร้างจะขึ้นกับว่าตารางไหน
    # อ้าง type ก่อน ซึ่งเป็นรายละเอียดที่เปลี่ยนได้เองเวลาเพิ่มตารางใหม่
    op.execute("CREATE TYPE margin_mode_t AS ENUM ('isolated', 'cross')")

    op.create_table('config_versions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('profile', postgresql.ENUM('live', 'paper', name='profile_t', create_type=False), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_ts', sa.BigInteger(), nullable=False),
    sa.Column('created_by_user_id', sa.BigInteger(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("source IN ('toml_seed', 'console', 'migration')", name='ck_config_versions_source'),
    sa.CheckConstraint('version > 0', name='ck_config_versions_version_positive'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'profile', name='uq_config_versions_id_profile'),
    sa.UniqueConstraint('profile', 'version', name='uq_config_versions_profile_version')
    )
    op.create_index('uq_config_versions_active', 'config_versions', ['profile'], unique=True, postgresql_where=sa.text('is_active'))
    op.create_table('config_broker',
    sa.Column('config_version_id', sa.BigInteger(), nullable=False),
    sa.Column('profile', postgresql.ENUM('live', 'paper', name='profile_t', create_type=False), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('exchange', sa.Text(), nullable=True),
    sa.Column('margin_mode', postgresql.ENUM('isolated', 'cross', name='margin_mode_t', create_type=False), nullable=False),
    sa.Column('position_mode', sa.Text(), nullable=False),
    sa.Column('seed_quote', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('created_ts', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("kind <> 'ccxt' OR exchange IS NOT NULL", name='ck_config_broker_exchange'),
    sa.CheckConstraint("kind = 'paper' OR seed_quote IS NULL", name='ck_config_broker_seed_quote'),
    sa.CheckConstraint("kind IN ('ccxt', 'paper')", name='ck_config_broker_kind'),
    sa.CheckConstraint("position_mode = 'one_way'", name='ck_config_broker_position_mode'),
    sa.CheckConstraint('seed_quote IS NULL OR seed_quote > 0', name='ck_config_broker_seed_positive'),
    sa.ForeignKeyConstraint(['config_version_id', 'profile'], ['config_versions.id', 'config_versions.profile'], name='fk_config_broker_version'),
    sa.PrimaryKeyConstraint('config_version_id')
    )
    op.create_table('config_data',
    sa.Column('config_version_id', sa.BigInteger(), nullable=False),
    sa.Column('profile', postgresql.ENUM('live', 'paper', name='profile_t', create_type=False), nullable=False),
    sa.Column('exchange', sa.Text(), nullable=False),
    sa.Column('created_ts', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['config_version_id', 'profile'], ['config_versions.id', 'config_versions.profile'], name='fk_config_data_version'),
    sa.PrimaryKeyConstraint('config_version_id')
    )
    op.create_table('config_risk',
    sa.Column('config_version_id', sa.BigInteger(), nullable=False),
    sa.Column('profile', postgresql.ENUM('live', 'paper', name='profile_t', create_type=False), nullable=False),
    sa.Column('max_position_pct_long', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('max_position_pct_short', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('max_leverage', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('min_liq_buffer_pct', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('max_daily_loss_pct', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('consecutive_loss_breaker', sa.Integer(), nullable=False),
    sa.Column('created_ts', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('consecutive_loss_breaker > 0', name='ck_config_risk_loss_breaker'),
    sa.CheckConstraint('max_daily_loss_pct > 0 AND max_daily_loss_pct <= 100', name='ck_config_risk_daily_loss'),
    sa.CheckConstraint('max_leverage > 0', name='ck_config_risk_max_leverage'),
    sa.CheckConstraint('max_position_pct_long > 0 AND max_position_pct_long <= 100', name='ck_config_risk_pct_long'),
    sa.CheckConstraint('max_position_pct_short > 0 AND max_position_pct_short <= 100', name='ck_config_risk_pct_short'),
    sa.CheckConstraint('min_liq_buffer_pct > 0 AND min_liq_buffer_pct < 100', name='ck_config_risk_liq_buffer'),
    sa.ForeignKeyConstraint(['config_version_id', 'profile'], ['config_versions.id', 'config_versions.profile'], name='fk_config_risk_version'),
    sa.PrimaryKeyConstraint('config_version_id')
    )
    op.create_table('config_settings',
    sa.Column('config_version_id', sa.BigInteger(), nullable=False),
    sa.Column('profile', postgresql.ENUM('live', 'paper', name='profile_t', create_type=False), nullable=False),
    sa.Column('timeframe', sa.Text(), nullable=False),
    sa.Column('market', sa.Text(), nullable=False),
    sa.Column('cold_start', sa.Text(), nullable=True),
    sa.Column('base_pct', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('dry_run', sa.Boolean(), nullable=False),
    sa.Column('allow_short', sa.Boolean(), nullable=False),
    sa.Column('created_ts', sa.BigInteger(), nullable=False),
    sa.CheckConstraint("cold_start IS NULL OR cold_start IN ('wait_1h', 'trailing', 'skip')", name='ck_config_settings_cold_start'),
    sa.CheckConstraint("market = 'usdtm_perp'", name='ck_config_settings_market'),
    sa.CheckConstraint("profile <> 'paper' OR dry_run", name='ck_config_settings_paper_dry_run'),
    sa.CheckConstraint("timeframe IN ('1h', '1d')", name='ck_config_settings_timeframe'),
    sa.CheckConstraint('base_pct BETWEEN 5 AND 20', name='ck_config_settings_base_pct'),
    sa.ForeignKeyConstraint(['config_version_id', 'profile'], ['config_versions.id', 'config_versions.profile'], name='fk_config_settings_version'),
    sa.PrimaryKeyConstraint('config_version_id')
    )
    op.create_table('config_symbols',
    sa.Column('config_version_id', sa.BigInteger(), nullable=False),
    sa.Column('symbol', sa.Text(), nullable=False),
    sa.Column('profile', postgresql.ENUM('live', 'paper', name='profile_t', create_type=False), nullable=False),
    sa.Column('bucket_quote_long', sa.Numeric(precision=24, scale=8), nullable=False),
    sa.Column('bucket_quote_short', sa.Numeric(precision=24, scale=8), nullable=True),
    sa.Column('leverage', sa.Numeric(precision=9, scale=4), nullable=False),
    sa.Column('allow_short', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_ts', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('NOT allow_short OR bucket_quote_short IS NOT NULL', name='ck_config_symbols_short_needs_bucket'),
    sa.CheckConstraint('bucket_quote_long > 0', name='ck_config_symbols_bucket_long'),
    sa.CheckConstraint('bucket_quote_short IS NULL OR bucket_quote_short > 0', name='ck_config_symbols_bucket_short'),
    sa.CheckConstraint('leverage > 0', name='ck_config_symbols_leverage'),
    sa.ForeignKeyConstraint(['config_version_id', 'profile'], ['config_versions.id', 'config_versions.profile'], name='fk_config_symbols_version'),
    sa.PrimaryKeyConstraint('config_version_id', 'symbol')
    )

    for table in CONFIG_TABLES:
        # engine อ่าน config ที่ active แล้วเอาไปตัดสิน — ไม่ใช่คนแก้
        op.execute(f"GRANT SELECT ON TABLE {table} TO cane_engine")
        # console สร้างเวอร์ชันใหม่ได้ แต่ทับเนื้อของเวอร์ชันเก่าไม่ได้
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO cane_console")

    # ตัวชี้เวอร์ชันที่ใช้อยู่ ขยับได้คอลัมน์เดียวเท่านั้น
    op.execute("GRANT UPDATE (is_active) ON TABLE config_versions TO cane_console")


def downgrade() -> None:
    op.drop_table('config_symbols')
    op.drop_table('config_settings')
    op.drop_table('config_risk')
    op.drop_table('config_data')
    op.drop_table('config_broker')
    op.drop_index('uq_config_versions_active', table_name='config_versions', postgresql_where=sa.text('is_active'))
    op.drop_table('config_versions')
    # `profile_t` ไม่ถูกลบที่นี่ — 0001 เป็นคนสร้าง จึงเป็นคนลบ
    op.execute("DROP TYPE margin_mode_t")
