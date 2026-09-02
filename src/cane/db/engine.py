"""สร้าง Engine ของ SQLAlchemy จาก DSN ใน environment — ที่เดียวที่รู้จัก DSN

ทุกชั้นที่เหลือรับ `Connection` เข้ามาเป็นอาร์กิวเมนต์ตัวแรก ไม่มีใครสร้าง Engine
เองข้างใน (สไตล์เดียวกับ `ExchangeClient` ที่ถูกฉีดเข้าทุกจุดในชั้นข้อมูล) ผลคือ
เทสต์เปลี่ยนปลายทางได้ด้วยการส่ง connection คนละตัว ไม่ต้องแก้ตัวแปรสภาพแวดล้อม

**role ถูกเลือกตอนสร้าง Engine ไม่ใช่ตอนยิงคำสั่ง** — `cane_engine` เขียนตารางข้อเท็จจริง
ได้แต่ `UPDATE`/`DELETE` ไม่ได้เลย ส่วน `cane_console` อ่านได้หมดแต่เขียนได้เฉพาะตาราง
config/state · append-only จึงเป็นข้อบังคับของ DB ไม่ใช่ข้อตกลงที่โค้ดผิดพลาดทับได้
(ตอนเป็นไฟล์ JSONL มันเป็นแค่ข้อตกลง)
"""

from __future__ import annotations

import os
from typing import Literal

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL, make_url

#: ชื่อตัวแปรสภาพแวดล้อมที่ถือ DSN — `.env.example` อธิบายรูปแบบไว้
DSN_ENV = "CANE_DB_DSN"

Role = Literal["engine", "console"]

#: role ในแอป → role จริงใน Postgres · เป็น NOLOGIN group role ทั้งคู่
#: (สร้างใน migration แรก) ผู้ใช้ที่ล็อกอินเป็นสมาชิกแล้ว `SET ROLE` ลงมาสวมสิทธิ์
DB_ROLES: dict[str, str] = {
    "engine": "cane_engine",
    "console": "cane_console",
}


def safe_dsn(dsn: str | URL) -> str:
    """DSN ที่ตัด user/password ออกแล้ว — ใช้ในข้อความ error และ log เท่านั้น

    ตัวกรอง log ของโปรเจกต์ (`cane.log.redact`) จับที่ชื่อคีย์แบบ `password = ...`
    แต่รหัสผ่านใน DSN อยู่ในรูป `//user:pass@host` ซึ่งไม่มีชื่อคีย์ให้จับ
    จึงต้องตัดที่ต้นทางเอง ห้ามเอา DSN ดิบไปประกอบข้อความใดๆ
    """
    url = make_url(dsn) if isinstance(dsn, str) else dsn
    return url.render_as_string(hide_password=True)


def dsn_from_env(env: dict[str, str] | None = None) -> str:
    """อ่าน DSN จาก environment — **ไม่มีค่าตั้งต้น** ขาดแล้วต้องล้ม

    ถ้าใส่ค่าตั้งต้นเป็น localhost ไว้ การลืมตั้งตัวแปรใน prod จะกลายเป็นการต่อ
    ฐานผิดตัวแบบเงียบๆ แล้วรายงานว่า "ไม่มีข้อมูล" ซึ่งเป็นความล้มเหลวชนิดที่
    ตรวจจับยากที่สุด — fail closed ตามหลักเดียวกับ config ของ spec/06
    """
    source = os.environ if env is None else env
    dsn = source.get(DSN_ENV, "").strip()
    if not dsn:
        raise RuntimeError(
            f"ไม่พบ {DSN_ENV} — คัดลอก .env.example เป็น .env แล้วสั่งงานด้วย "
            "`uv run --env-file .env ...`"
        )
    return dsn


def make_engine(dsn: str | None = None, *, role: Role | None = None) -> Engine:
    """สร้าง Engine · `role` คือสิทธิ์ที่ทุก connection ของ Engine ตัวนี้จะสวม

    `SET ROLE` ยิงตอน **connect** ไม่ใช่ตอน begin เพราะ pool เอา connection กลับมา
    ใช้ซ้ำ — ตั้งครั้งเดียวต่อ connection แล้วอยู่ยาวตลอดอายุของมัน ตรงกับที่ Engine
    หนึ่งตัวมี role เดียวตายตัว ถ้าจะเปลี่ยน role ให้สร้าง Engine ใหม่

    `role=None` = ใช้สิทธิ์ของ login user ตรงๆ — สำหรับ migration และงาน admin
    ที่ต้องสร้าง/แก้โครงสร้าง ซึ่งทั้งสอง role ข้างบนตั้งใจไม่ให้ทำได้
    """
    resolved = dsn or dsn_from_env()
    if role is not None and role not in DB_ROLES:
        known = ", ".join(sorted(DB_ROLES))
        raise ValueError(f"role {role!r} ไม่รู้จัก — มีแต่ {known}")

    # pool_pre_ping: DB ที่ยกด้วย docker compose ถูกรีสตาร์ทระหว่าง dev ได้ตลอด
    # connection ที่ตายค้างใน pool จะกลายเป็น error ที่ดูเหมือนบั๊กของ query
    engine = create_engine(resolved, pool_pre_ping=True)

    if role is not None:
        db_role = DB_ROLES[role]

        @event.listens_for(engine, "connect")
        def _set_role(dbapi_conn, _record) -> None:  # noqa: ANN001
            """สวม role ให้ connection ทางกายภาพ **นอกทรานแซกชัน**

            `SET ROLE` เป็นคำสั่งที่ transactional — ถ้ายิงในทรานแซกชัน มันจะถูกถอน
            พร้อม `ROLLBACK` · psycopg3 ไม่ใช่ autocommit โดยตั้งต้น การยิงตรงๆ
            จึงเปิดทรานแซกชันขึ้นมาเงียบๆ แล้วรอบแรกที่ผู้เรียกออกจาก
            `with engine.connect()` โดยไม่ commit (SQLAlchemy สั่ง ROLLBACK ให้)
            role จะหลุด · connection กลับเข้า pool ในสภาพ **เจ้าของตาราง** และการ
            checkout ครั้งต่อไปได้สิทธิ์ UPDATE/DELETE เต็มมือแบบไม่มีสัญญาณอะไรเลย
            ซึ่งคือความล้มเหลวชนิดที่ทั้งใบนี้มีอยู่เพื่อป้องกัน (ทดสอบไว้แล้วใน
            `tests/test_db_grants.py`)

            เปิด autocommit ชั่วคราวจึงเป็นส่วนที่จำเป็น ไม่ใช่การจัดระเบียบ
            """
            previous = dbapi_conn.autocommit
            dbapi_conn.autocommit = True
            try:
                # ชื่อ role มาจาก DB_ROLES ที่เป็นค่าคงที่ในไฟล์นี้ ไม่ใช่ค่าจากผู้ใช้
                # จึงต่อสตริงได้ปลอดภัย — และ SET ROLE ผูกพารามิเตอร์ไม่ได้อยู่แล้ว
                with dbapi_conn.cursor() as cur:
                    cur.execute(f'SET ROLE "{db_role}"')
            finally:
                dbapi_conn.autocommit = previous

    return engine
