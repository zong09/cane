"""ตัวตั้งค่า Alembic — **DSN มาจาก environment ไม่ใช่จาก alembic.ini**

`alembic.ini` ถูก commit ลง git การเก็บ DSN ไว้ที่นั้นคือการ commit รหัสผ่านของฐาน
จึงอ่านผ่าน `cane.db.engine.dsn_from_env()` ตัวเดียวกับที่แอปใช้ — ถ้าตัวแปรหาย
migration กับแอปจะล้มด้วยข้อความเดียวกัน ไม่ใช่ล้มต่างกันคนละแบบ

migration รันด้วย **สิทธิ์ของ login user ตรงๆ ไม่ SET ROLE** เพราะทั้ง `cane_engine`
และ `cane_console` ตั้งใจไม่ให้แก้โครงสร้างได้เลย
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from cane.db.engine import dsn_from_env, safe_dsn
from cane.db.schema import metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: ให้ `alembic revision --autogenerate` เทียบกับ schema.py ได้
#: (ตัว migration ที่ generate ออกมาต้องอ่านทวนเองทุกครั้ง ไม่เชื่อผลดิบ)
target_metadata = metadata


def run_migrations_offline() -> None:
    """สร้าง SQL ออกมาเป็นข้อความ ไม่ต่อฐาน — ใช้ตอนต้องส่ง SQL ให้ DBA รีวิว"""
    context.configure(
        url=dsn_from_env(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    dsn = dsn_from_env()
    # ไม่เรียก make_engine() เพราะไม่ต้องการ pool_pre_ping/role ของแอป
    # และไม่ต้องการให้ migration พึ่งพฤติกรรมของ Engine ฝั่งแอปที่เปลี่ยนได้
    engine = create_engine(dsn, poolclass=None)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
    # พิมพ์แบบตัด user/password ออกแล้ว — DSN ดิบห้ามโผล่ใน output ของ CI
    print(f"alembic: {safe_dsn(dsn)}")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
