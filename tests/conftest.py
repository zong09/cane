"""fixture ของเทสต์ที่แตะ Postgres

**ทุกเทสต์อยู่ในทรานแซกชันที่ rollback ท้ายเทสต์** ไม่ใช่การล้างตารางทีหลัง —
เร็วกว่า ไม่มีเทสต์ไหนรั่วใส่ตัวถัดไป และไม่มีลำดับการรันที่ทำให้ผลต่างกัน

เทสต์ที่แตะ DB ติด marker `db` ทั้งหมด · ชุดที่ไม่แตะ (`test_log.py`, สูตร Action
Zone ในอนาคต) ต้องยังรันได้โดยไม่มี Postgres:

    uv run --extra dev pytest -q -m "not db"

fixture ที่นี่จึงต้อง **lazy** — ต่อฐานตอนถูกขอเท่านั้น ถ้าสร้าง Engine ตอน import
การรัน `-m "not db"` บนเครื่องที่ไม่มี DB จะล้มทั้งที่ไม่มีเทสต์ไหนต้องใช้
"""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, Engine, text

from cane.db.engine import make_engine


@pytest.fixture(scope="session")
def db_engine() -> Engine:
    """Engine เดียวต่อทั้งรอบเทสต์ · ใช้สิทธิ์ของ login user (ไม่ SET ROLE)

    เทสต์ที่ต้องการสิทธิ์แคบกว่านั้นสวม role เองด้วย `SET LOCAL ROLE` ในทรานแซกชัน
    ของตัวเอง — ไม่ต้องมี Engine ตัวที่สองและ DSN ชุดที่สองมาดูแลขนานกัน
    """
    engine = make_engine()
    try:
        with engine.connect() as conn:
            applied = conn.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar_one()
        if not applied:
            pytest.fail(
                "ฐานยังไม่ได้ migrate — สั่ง `docker compose up -d db` แล้ว "
                "`uv run --env-file .env alembic upgrade head` ก่อน",
                pytrace=False,
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db(db_engine: Engine) -> Connection:
    """connection ที่เปิดทรานแซกชันค้างไว้ แล้ว **rollback ทิ้งเสมอ**

    ไม่ commit เลยแม้เทสต์ผ่าน — ข้อมูลของเทสต์ไม่ควรมีอายุยืนกว่าตัวเทสต์
    """
    conn = db_engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()
