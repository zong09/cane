"""คอนโซล (ใบ 19) — ส่วนที่ต้องยิง SQL จริง

เทสต์ใน `test_console_web.py` แทน `Supervisor` กับ repo ไว้หมด ไฟล์นี้จึงมีหน้าที่
เดียว: ยืนยันว่าเส้นทางจาก handler ลงไปถึงตารางจริงต่อกันติด — `engine_state` ที่ยัง
ไม่มีแถว และ `config_versions` ที่ active อยู่

แอปได้ **connection ของเทสต์เอง** ผ่าน `BoundDb` ไม่ใช่ Engine ของตัวเอง ไม่งั้นมัน
จะเปิดทรานแซกชันคนละตัวแล้วมองไม่เห็นข้อมูลที่เทสต์เพิ่งเขียน (fixture `db` ไม่ commit)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection

from cane.api.app import create_app
from cane.api.deps import MODE_COOKIE
from cane.config import load_profile
from cane.db.repo import config as config_repo
from cane.db.schema import CONFIG_TABLES, engine_state
from cane.engine.state import PROFILES, STOPPED

pytestmark = pytest.mark.db


class _Borrowed:
    """context manager ที่ยืม connection มาแล้วไม่ปิด — เจ้าของคือ fixture `db`"""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def __enter__(self) -> Connection:
        return self._conn

    def __exit__(self, *_: object) -> bool:
        return False


class BoundDb:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def connect(self) -> _Borrowed:
        return _Borrowed(self._conn)

    def begin(self) -> _Borrowed:
        return _Borrowed(self._conn)


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def terminate(self) -> None:
        pass


@pytest.fixture
def client(db: Connection) -> TestClient:
    return TestClient(create_app(db=BoundDb(db), spawn=lambda profile: FakeProcess(1)))


@pytest.fixture
def clean_config(db: Connection) -> None:
    """ตาราง config ของเครื่อง dev มีของที่ `cane db seed` ทิ้งไว้ — ล้างในทรานแซกชัน"""
    for table in reversed(CONFIG_TABLES):
        db.execute(table.delete())


def test_the_status_endpoint_answers_for_both_profiles_even_with_no_rows_yet(
    db: Connection, client: TestClient
) -> None:
    """spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 8 — profile ที่ยังไม่เคยเดินก็ต้องมี

    ถ้ามันหายไป การ์ด PROFILE จะว่างแทนที่จะบอกว่า "ยังไม่เคยเดิน" ซึ่งเป็นคำตอบ
    คนละอันกัน
    """
    db.execute(engine_state.delete())

    with client:
        body = client.get("/api/engine/status").json()

    assert [row["profile"] for row in body["engines"]] == list(PROFILES)
    assert all(row["status"] == STOPPED for row in body["engines"])


def test_the_profile_chip_reads_the_active_config_version_not_the_toml_file(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ชิป `จำลองทั้งหมด` มาจากแถวใน DB · ไฟล์ TOML เป็นแค่ทางเข้าของ `cane db seed`"""
    head = config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="toml_seed"
    )
    config_repo.activate(db, head.id)

    with client:
        client.cookies.set(MODE_COOKIE, "paper")
        page = client.get("/overview").text

    assert "จำลองทั้งหมด" in page
    assert "BTC/USDT" in page  # SYMBOLS ใน rail มาจาก config เวอร์ชันเดียวกัน


def test_a_profile_with_no_active_version_says_so_instead_of_pretending_to_be_fine(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """"ไม่มีเวอร์ชัน active = ไม่เทรด" (spec/07) — ต้องเห็นบนหน้าจอ ไม่ใช่เงียบ"""
    with client:
        client.cookies.set(MODE_COOKIE, "paper")
        page = client.get("/overview").text

    assert "ไม่มีเวอร์ชัน active" in page
