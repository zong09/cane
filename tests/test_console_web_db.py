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
from cane.api.deps import signed_in
from cane.auth import secrets as auth_secrets
from cane.auth.matrix import DEFAULT_MATRIX
from cane.config import load_profile
from cane.db.repo import config as config_repo
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import AUTH_TABLES, CONFIG_TABLES, engine_state
from cane.db.types import now_ms
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
def owner(db: Connection):
    """OWNER ที่ผูก 2FA แล้ว พร้อม session ที่ยังมีชีวิต

    ใบ 19 เคยตั้งโหมดด้วยคุกกี้ · ใบ 20 ย้ายโหมดไปอยู่บนแถวของ session ไฟล์นี้จึง
    ต้องมีผู้ใช้จริงกับ session จริง ไม่ใช่แค่ตั้งคุกกี้แล้วยิง
    """
    for table in AUTH_TABLES:
        db.execute(table.delete())
    perms.activate(db, perms.insert_version(db, DEFAULT_MATRIX, created_ts=now_ms()))

    user_id = users_repo.create(
        db,
        email="owner@example.com",
        name="เจ้าของ",
        role="OWNER",
        created_ts=now_ms(),
        password_hash=auth_secrets.hash_password("รหัสผ่านที่ยาวพอ"),
    )
    users_repo.enrol_totp(
        db,
        user_id,
        secret_enc=auth_secrets.encrypt_secret("JBSWY3DPEHPK3PXP"),
        enrolled_ts=now_ms(),
    )
    token = auth_secrets.new_token()
    sessions_repo.create(db, user_id=user_id, token=token, now=now_ms())
    return sessions_repo.lookup(db, token, now=now_ms())


@pytest.fixture
def client(db: Connection, owner) -> TestClient:
    app = create_app(db=BoundDb(db), spawn=lambda profile: FakeProcess(1))
    app.dependency_overrides[signed_in] = lambda: owner
    return TestClient(app)


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
        page = client.get("/overview").text

    assert "จำลองทั้งหมด" in page
    assert "BTC/USDT" in page  # SYMBOLS ใน rail มาจาก config เวอร์ชันเดียวกัน


def test_a_profile_with_no_active_version_says_so_instead_of_pretending_to_be_fine(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """"ไม่มีเวอร์ชัน active = ไม่เทรด" (spec/07) — ต้องเห็นบนหน้าจอ ไม่ใช่เงียบ"""
    with client:
        page = client.get("/overview").text

    assert "ไม่มีเวอร์ชัน active" in page


# ── หน้าตั้งค่า · ใบ 21 ───────────────────────────────────────────────────────


def test_the_config_page_reads_the_active_version_from_the_database(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ค่าที่อยู่ในช่องกรอกมาจากแถวใน `config_*` ไม่ใช่จากไฟล์ `paper.toml`"""
    head = config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="toml_seed"
    )
    config_repo.activate(db, head.id)

    with client:
        page = client.get("/config").text

    assert 'name="base_pct"' in page
    assert "โหลดผ่าน — ไม่พบข้อผิดพลาด" in page
    assert f"v{head.version}" in page


def test_the_version_history_lists_every_version_newest_first(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ประวัติคือเหตุผลทั้งหมดของการเก็บเป็นเวอร์ชัน — เวอร์ชันที่ไม่ active ต้องเห็นด้วย"""
    settings = load_profile("config/paper.toml")
    first = config_repo.insert_version(db, settings, source="toml_seed")
    config_repo.activate(db, first.id)
    second = config_repo.insert_version(db, settings, source="console", note="ลองแก้")

    with client:
        page = client.get("/config").text

    assert page.index(f"v{second.version}") < page.index(f"v{first.version}")
    assert "ลองแก้" in page
    assert "เปิดใช้อยู่" in page


def seeded(db: Connection, profile: str):
    head = config_repo.insert_version(
        db, load_profile(f"config/{profile}.toml"), source="toml_seed"
    )
    return config_repo.activate(db, head.id)


def test_saving_writes_a_new_version_that_is_not_active_and_leaves_the_pointer_alone(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """เกณฑ์ของมติ draft+activate — บันทึกแล้ว engine ต้องยังเดินด้วยค่าเดิม"""
    active = seeded(db, "paper")

    with client:
        response = client.post(
            "/api/paper/config", data={"base_pct": "12.0", "note": "ลดขนาดไม้"}
        )

    assert response.status_code == 200
    heads = config_repo.versions(db, "paper")
    assert len(heads) == 2
    assert heads[0].source == "console"
    assert heads[0].is_active is False
    assert config_repo.active_version(db, "paper").id == active.id


def test_the_saved_version_records_who_pressed_it(
    db: Connection, client: TestClient, clean_config: None, owner
) -> None:
    """ประวัติที่ไม่มีชื่อคนแก้ตอบคำถามว่า "ใครเปลี่ยน" ไม่ได้ ซึ่งเป็นครึ่งหนึ่งของเหตุผลที่เก็บ"""
    seeded(db, "paper")
    _, user = owner

    with client:
        client.post("/api/paper/config", data={"base_pct": "12.0"})

    draft = config_repo.versions(db, "paper")[0]
    assert draft.created_by_user_id == user.id


def test_a_form_that_breaks_three_rules_writes_no_row_at_all(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """เกณฑ์เสร็จของใบ 21 · ค่าที่ไม่ผ่านต้อง **ไม่ลง DB เลย** ไม่ใช่ลงแล้วไม่ activate

    (spec/07 §กฎการตรวจ config) · badge เป็น path ของฟิลด์ ไม่ใช่เลขบรรทัดของไฟล์
    ที่ไม่มีอยู่แล้ว
    """
    seeded(db, "live")
    before = len(config_repo.versions(db, "live"))

    with client:
        page = client.post(
            "/api/live/config",
            data={
                "base_pct": "32.0",
                "risk.consecutive_loss_breaker": "",
                "broker.exchange": "",
            },
        ).text

    assert "อยู่นอกช่วง 5–20" in page
    assert "ขาด consecutive_loss_breaker" in page
    assert "ไม่ระบุ exchange" in page
    assert len(config_repo.versions(db, "live")) == before
