"""คอนโซล (ใบ 19) — ส่วนที่ต้องยิง SQL จริง

เทสต์ใน `test_console_web.py` แทน `Supervisor` กับ repo ไว้หมด ไฟล์นี้จึงมีหน้าที่
เดียว: ยืนยันว่าเส้นทางจาก handler ลงไปถึงตารางจริงต่อกันติด — `engine_state` ที่ยัง
ไม่มีแถว และ `config_versions` ที่ active อยู่

แอปได้ **connection ของเทสต์เอง** ผ่าน `BoundDb` ไม่ใช่ Engine ของตัวเอง ไม่งั้นมัน
จะเปิดทรานแซกชันคนละตัวแล้วมองไม่เห็นข้อมูลที่เทสต์เพิ่งเขียน (fixture `db` ไม่ commit)
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select

from cane.api import log as log_routes
from cane.api.app import create_app
from cane.api.deps import signed_in
from cane.auth import secrets as auth_secrets
from cane.auth import totp
from cane.auth.matrix import DEFAULT_MATRIX
from cane.config import load_profile
from cane.db.repo import config as config_repo
from cane.db.repo.bars import insert_bars
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import killswitch as killswitch_repo
from cane.db.repo import ledger
from cane.db.repo.ledger import Fill, dedupe_key_of, trade_id_of
from cane.db.repo import permissions as perms
from cane.db.repo import sessions as sessions_repo
from cane.db.repo import users as users_repo
from cane.db.schema import (
    AUTH_TABLES,
    CONFIG_TABLES,
    engine_state,
    fills,
    funding_charges,
    user_audit_log,
)
from cane.db.schema import bars as bars_table
from cane.db.schema import decisions as decisions_table
from cane.db.types import now_ms
from cane.engine.state import PROFILES, STOPPED

sys.path.insert(0, str(Path(__file__).parent))
from golden import GOLDEN_DIR, load  # noqa: E402

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


def right_now_code(secret: str = "JBSWY3DPEHPK3PXP") -> dict[str, str]:
    """รหัสจริงของวินาทีนี้ — `owner` ผูก secret ตัวนี้ไว้"""
    return {"step_up_code": totp.code(secret, totp.counter_at(now_ms()))}


def test_activating_moves_the_pointer_and_closes_the_previous_version(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """`is_active` เป็นตัวชี้ · active ได้ profile ละหนึ่งเวอร์ชัน (partial unique index)"""
    first = seeded(db, "paper")
    draft = config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="console"
    )

    with client:
        response = client.post(
            f"/api/paper/config/{draft.id}/activate", data=right_now_code()
        )

    assert response.status_code == 200
    assert config_repo.active_version(db, "paper").id == draft.id
    assert [v.is_active for v in config_repo.versions(db, "paper")] == [True, False]
    assert first.id != draft.id


def test_a_wrong_step_up_code_leaves_the_pointer_where_it_was(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """เกณฑ์เสร็จข้อสามของใบ 21"""
    active = seeded(db, "paper")
    draft = config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="console"
    )

    with client:
        response = client.post(
            f"/api/paper/config/{draft.id}/activate", data={"step_up_code": "000000"}
        )

    assert response.status_code == 403
    assert config_repo.active_version(db, "paper").id == active.id


def test_activating_leaves_an_audit_row_marked_step_up_verified(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """spec/09 §step-up TOTP — สิ่งที่ทำต้องบันทึก ไม่ใช่แค่ด่านที่ผ่าน"""
    seeded(db, "paper")
    draft = config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="console"
    )

    with client:
        client.post(f"/api/paper/config/{draft.id}/activate", data=right_now_code())

    row = db.execute(
        select(user_audit_log).where(user_audit_log.c.action == "config.activate")
    ).one()
    assert row.step_up_verified is True
    assert row.target == f"paper v{draft.version}"


# ── จุดสีของ symbol ใน rail · ใบ 22 ───────────────────────────────────────────


def a_decision(version_id: int, *, profile: str, zone: str, symbol: str = "BTC/USDT"):
    """แท่งที่จบด้วย "ไม่ทำอะไร" — สั้นที่สุดที่ยังถูกกฎทุกข้อของ `validate_record()`"""
    return decisions_repo.DecisionRecord(
        profile=profile,
        market="usdtm_perp",
        symbol=symbol,
        timeframe="1d",
        bar_close_ts=1_787_961_600_000,
        decided_ts=1_787_961_600_500,
        config_version_id=version_id,
        close_px=77_500.0,
        zone=zone,
        state="BULLISH",
        long_signal=False,
        short_signal=False,
        dry_run=True,
        skip_reason="no_signal",
    )


def test_the_rail_dot_takes_its_colour_from_the_latest_decision(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ใบ 19 ทิ้งจุดสีไว้เป็นสีเทาคงที่ · ใบ 22 ทำให้มันอ่านโซนจริง"""
    head = seeded(db, "paper")
    db.execute(decisions_table.delete())
    decisions_repo.insert_decision(
        db, a_decision(head.id, profile="paper", zone="GREEN")
    )

    with client:
        page = client.get("/overview").text

    assert "var(--zone-green)" in page


def test_a_symbol_with_no_decision_yet_stays_black_rather_than_guessing(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """`BLACK` แปลว่า "ไม่เข้าเงื่อนไขสีใดเลย" อยู่แล้ว (spec/02 §นิยามโซนทั้ง 6 สี)"""
    seeded(db, "paper")
    db.execute(decisions_table.delete())

    with client:
        page = client.get("/overview").text

    assert "var(--zone-black)" in page
    assert "var(--zone-green)" not in page


# ── หน้าภาพรวมผูกกับโหมดจริง · เกณฑ์เสร็จของใบ 22 ─────────────────────────────


def client_in(db: Connection, owner, mode: str) -> TestClient:
    """client ที่ session มองโหมดที่ระบุ · `current_mode` อ่านจากแถว session"""
    session, user = owner
    app = create_app(db=BoundDb(db), spawn=lambda profile: FakeProcess(1))
    app.dependency_overrides[signed_in] = lambda: (replace(session, mode=mode), user)
    return TestClient(app)


def test_every_number_on_the_overview_follows_the_mode_being_viewed(
    db: Connection, owner, clean_config: None
) -> None:
    """เกณฑ์เสร็จของใบ 22 — สลับ live/paper แล้วไม่มีช่องไหนค้างที่โปรไฟล์เดิม

    เพดานมาจาก bucket ของ config คนละชุด (paper 100+80 · live 100) และโซนมาจาก
    บันทึกคนละแถว — ถ้าหน้าลืมส่ง profile ลงไปชั้นใดชั้นหนึ่ง สองค่านี้จะเท่ากัน
    """
    db.execute(decisions_table.delete())
    paper = seeded(db, "paper")
    live = seeded(db, "live")
    decisions_repo.insert_decision(db, a_decision(paper.id, profile="paper", zone="GREEN"))
    decisions_repo.insert_decision(db, a_decision(live.id, profile="live", zone="RED"))

    with client_in(db, owner, "paper") as client:
        as_paper = client.get("/overview").text
    with client_in(db, owner, "live") as client:
        as_live = client.get("/overview").text

    assert "เพดาน long 180.00" in as_paper
    assert "เพดาน long 100.00" in as_live
    assert ">GREEN<" in as_paper and ">RED<" not in as_paper
    assert ">RED<" in as_live and ">GREEN<" not in as_live
    # เพดานขาดทุนก็เป็นของคนละโปรไฟล์ (paper 5.0 · live 3.0)
    assert "/ 5.0%" in as_paper
    assert "/ 3.0%" in as_live


# ── หน้าความเสี่ยงผูกกับโหมดจริง · ใบ 23 ──────────────────────────────────────


def test_every_ceiling_on_the_risk_page_follows_the_mode_being_viewed(
    db: Connection, owner, clean_config: None
) -> None:
    """เพดานทุกตัวมาจาก config ของโปรไฟล์ที่ session กำลังดู ไม่ใช่ของตัวที่โหลดล่าสุด

    ตัวแยกคือค่าที่ seed ไว้คนละชุด: ขาดทุนต่อวัน (paper 5.0 · live 3.0) กับ bucket
    รวมของฝั่ง long (paper 100+80 · live 100) — ถ้าหน้าลืมส่ง profile ลงไปชั้นใด
    ชั้นหนึ่ง สองค่านี้จะเท่ากันทั้งที่ฐานเก็บไว้คนละแถว
    """
    seeded(db, "paper")
    seeded(db, "live")

    with client_in(db, owner, "paper") as client:
        as_paper = client.get("/risk").text
    with client_in(db, owner, "live") as client:
        as_live = client.get("/risk").text

    assert '<span class="rk__limitval">5</span>' in as_paper
    assert '<span class="rk__limitval">3</span>' in as_live
    assert "bucket long รวม 180.00 USDT" in as_paper
    assert "bucket long รวม 100.00 USDT" in as_live
    # paper มีเหรียญ spot อยู่ด้วย live ไม่มี — ตารางท้ายหน้าจึงต้องต่างกัน
    assert "ไม่มี (spot)" in as_paper
    assert "ไม่มี (spot)" not in as_live


# ── สวิตช์หยุดฉุกเฉินผ่านเส้นทาง HTTP · ใบ 23 ─────────────────────────────────


def _counter_of(db: Connection, user_id: int) -> int | None:
    return users_repo.by_id(db, user_id).totp_last_counter


def test_the_console_role_gets_past_the_trigger_that_guards_unlatching(
    db: Connection, owner, clean_config: None
) -> None:
    """เกณฑ์เสร็จของใบ — ปลดได้จริงจากหน้าจอ ไม่ใช่แค่จากชั้น repo

    migration 0008 มี trigger ที่ปฏิเสธการปลดของทุก role ยกเว้น `cane_console` ·
    `tests/test_risk.py` พิสูจน์ชั้น repo ไว้แล้ว ที่นี่พิสูจน์ว่า **แอปจริงถือ role
    ที่ผ่านด่านนั้น** ตลอดเส้นทาง route → repo → ตาราง
    """
    seeded(db, "paper")
    killswitch_repo.latch(db, "paper", reason="เทสต์", by="engine")

    with client_in(db, owner, "paper") as client:
        response = client.post(
            "/api/paper/killswitch/unlatch",
            data={"profile_name": "paper", **right_now_code()},
        )

    assert response.status_code == 200
    assert killswitch_repo.is_latched(db, "paper") is False


def test_pressing_stop_twice_through_the_route_keeps_the_first_story(
    db: Connection, owner, clean_config: None
) -> None:
    """spec/10 §เขียน — กดซ้ำคืน 200 และ **ไม่เขียนทับเหตุผลกับเวลาของครั้งแรก**"""
    seeded(db, "paper")

    with client_in(db, owner, "paper") as client:
        first = client.post("/api/paper/killswitch/latch")
        state = killswitch_repo.read(db, "paper")
        second = client.post("/api/paper/killswitch/latch")

    again = killswitch_repo.read(db, "paper")
    assert first.status_code == 200 and second.status_code == 200
    assert again.latched is True
    assert again.reason == state.reason
    assert again.latched_ts == state.latched_ts


def test_a_wrong_profile_name_leaves_the_switch_latched_and_the_code_unspent(
    db: Connection, owner, clean_config: None
) -> None:
    """ด่านชื่อโปรไฟล์ต้องมาก่อนด่านรหัส — counter ของ TOTP ใช้ร่วมกับ login

    ถ้าสลับลำดับ คนที่พิมพ์ชื่อผิดจะเสียรหัสรอบนั้นไปทั้งที่ยังไม่ได้ปลดอะไรเลย ·
    เทสต์นี้เป็นที่เดียวที่พิสูจน์ลำดับได้ เพราะต้องอ่าน `totp_last_counter` ของจริง
    """
    session, user = owner
    seeded(db, "paper")
    killswitch_repo.latch(db, "paper", reason="เทสต์", by="engine")
    before = _counter_of(db, user.id)

    with client_in(db, owner, "paper") as client:
        response = client.post(
            "/api/paper/killswitch/unlatch",
            data={"profile_name": "live", **right_now_code()},
        )

    assert response.status_code == 200
    assert killswitch_repo.is_latched(db, "paper") is True
    assert _counter_of(db, user.id) == before


def test_unlatching_leaves_an_audit_row_marked_step_up_verified(
    db: Connection, owner, clean_config: None
) -> None:
    """spec/09 §step-up TOTP — สภาพอ่านจากตารางเดียว ประวัติอ่านจากตารางที่ลบไม่ได้"""
    seeded(db, "paper")
    killswitch_repo.latch(db, "paper", reason="เทสต์", by="engine")

    with client_in(db, owner, "paper") as client:
        client.post(
            "/api/paper/killswitch/unlatch",
            data={"profile_name": "paper", **right_now_code()},
        )

    row = db.execute(
        select(user_audit_log).where(user_audit_log.c.action == "killswitch.unlatch")
    ).one()
    assert row.step_up_verified is True
    assert row.target == "paper"


# ── สวิตช์ dry_run / allow_short ผ่านเส้นทาง HTTP · ใบ 23 ─────────────────────


def test_flipping_dry_run_makes_the_new_version_the_active_one(
    db: Connection, owner, clean_config: None
) -> None:
    """เกณฑ์ของสวิตช์: กดครั้งเดียวแล้ว **ค่าที่ engine จะอ่าน** เปลี่ยนจริง

    ไม่ใช่แค่มีเวอร์ชันใหม่นอนรออยู่ในประวัติ · ตัวชี้ `is_active` ต้องขยับตามในคำขอ
    เดียวกัน (spec/10 §เขียน)
    """
    before = seeded(db, "live")

    with client_in(db, owner, "live") as client:
        response = client.post(
            "/api/live/config/dry_run", data={"value": "false", **right_now_code()}
        )

    active = config_repo.active_version(db, "live")
    assert response.status_code == 200
    assert active.id != before.id
    assert config_repo.active_settings(db, "live").dry_run is False
    assert [v.is_active for v in config_repo.versions(db, "live")] == [True, False]


def test_paper_keeps_dry_run_true_and_writes_no_version_at_all(
    db: Connection, owner, clean_config: None
) -> None:
    """`ck_config_settings_paper_dry_run` ปฏิเสธที่ฐาน — หน้าจอต้องบอกก่อนถึงตรงนั้น"""
    seeded(db, "paper")
    before = len(config_repo.versions(db, "paper"))

    with client_in(db, owner, "paper") as client:
        response = client.post(
            "/api/paper/config/dry_run", data={"value": "false", **right_now_code()}
        )

    assert response.status_code == 200
    assert config_repo.active_settings(db, "paper").dry_run is True
    assert len(config_repo.versions(db, "paper")) == before


def test_flipping_the_short_side_leaves_an_audit_row_marked_step_up_verified(
    db: Connection, owner, clean_config: None
) -> None:
    """spec/09 §step-up TOTP — การกระทำที่ผ่านด่านต้องบันทึกว่าผ่านด่านอะไรมา"""
    seeded(db, "live")

    with client_in(db, owner, "live") as client:
        client.post(
            "/api/live/config/allow_short", data={"value": "false", **right_now_code()}
        )

    row = db.execute(
        select(user_audit_log).where(user_audit_log.c.action == "config.allow_short")
    ).one()
    assert row.step_up_verified is True
    assert "allow_short=false" in row.target
    assert config_repo.active_settings(db, "live").allow_short is False


# ── หน้าบันทึก · ใบ 24 ────────────────────────────────────────────────────────

_DAY_MS = 86_400_000


def _accepted_open(**overrides):
    base = {
        "leg": "open",
        "order_side": "buy",
        "order_type": "market",
        "reduce_only": False,
        "qty": 0.001,
        "client_order_id": "cane-log",
        "sent": True,
        "accepted": True,
    }
    return decisions_repo.OrderAttempt(**{**base, **overrides})


def _journal_bars(version_id: int, *, profile: str):
    """ห้าแท่งที่กระจายตัวให้ทุกชิปมีตัวนับที่ไม่เท่ากัน

    ตัวนับที่เท่ากันหมดทำให้เทสต์ผ่านได้แม้เงื่อนไขของชิปจะสลับกันทั้งชุด
    """
    base = a_decision(version_id, profile=profile, zone="GREEN")
    start = base.bar_close_ts
    return [
        base,
        replace(
            base,
            bar_close_ts=start + _DAY_MS,
            long_signal=True,
            side="long",
            skip_reason="risk_rejected",
        ),
        replace(
            base,
            bar_close_ts=start + 2 * _DAY_MS,
            symbol="ETH/USDT",
            zone="RED",
            short_signal=True,
            side="short",
            skip_reason="short_disabled",
        ),
        replace(
            base,
            bar_close_ts=start + 3 * _DAY_MS,
            long_signal=True,
            side="long",
            skip_reason="dry_run",
            judge_called=True,
            factors_present=3,
            size_rule="confluence",
            size_pct_formula=100.0,
            size_pct_final=50.0,
            capped=True,
        ),
        replace(
            base,
            bar_close_ts=start + 4 * _DAY_MS,
            dry_run=False,
            long_signal=True,
            side="long",
            skip_reason=None,
            judge_called=True,
            factors_present=2,
            size_rule="confluence",
            size_pct_formula=45.0,
            size_pct_final=45.0,
            capped=False,
            margin=45.0,
            notional=90.0,
            qty=0.001,
            ref_px=90_000.0,
            orders=(_accepted_open(),),
        ),
    ]


@pytest.fixture
def journal(db: Connection, clean_config: None):
    db.execute(decisions_table.delete())
    head = seeded(db, "paper")
    for record in _journal_bars(head.id, profile="paper"):
        decisions_repo.insert_decision(db, record)
    return head


def test_the_journal_page_shows_every_chip_with_the_count_from_the_table(
    client: TestClient, journal
) -> None:
    with client:
        page = client.get("/log").text

    assert 'ทั้งหมด <span class="lg__count">5</span>' in page
    assert 'มีออเดอร์ <span class="lg__count">1</span>' in page
    assert 'ฝั่ง long <span class="lg__count">3</span>' in page
    assert 'ฝั่ง short <span class="lg__count">1</span>' in page
    assert 'risk ปฏิเสธ <span class="lg__count">1</span>' in page
    assert 'ถูกเพดานตัด <span class="lg__count">1</span>' in page
    assert 'กลับข้าง <span class="lg__count">0</span>' in page


def test_the_journal_renders_one_row_per_bar_with_the_columns_the_ticket_asks_for(
    client: TestClient, journal
) -> None:
    with client:
        page = client.get("/log").text

    assert page.count('class="lgtable__row"') + page.count(
        'class="lgtable__row lgtable__row--long"'
    ) == 5
    assert "BTC/USDT" in page and "ETH/USDT" in page
    assert "var(--zone-green)" in page and "var(--zone-red)" in page
    # SIZE ที่ถูกเพดานตัดต้องเห็นทั้งสองตัวเลขพร้อม badge ไม่ใช่ตัวเดียว
    assert ">100</span>" in page and "→ 50" in page and "เพดาน" in page
    assert "risk ปฏิเสธ — ไม่เกิดไม้" in page
    # สีของแถวตอบว่า "ลงไม้ฝั่งไหน" — แท่ง dry_run นับด้วย (paper ถูกบังคับ dry_run
    # ตายตัว ถ้าไม่นับ บันทึกของ paper จะไม่มีสีเลยสักแถว) ส่วนแท่งที่ risk ปฏิเสธ
    # กับแท่งที่ฝั่ง short ปิดอยู่ไม่ได้ลงไม้ จึงไม่ย้อม
    assert page.count("lgtable__row--long") == 2
    assert "lgtable__row--short" not in page


def test_a_chip_filters_the_table_without_touching_the_counts(
    client: TestClient, journal
) -> None:
    with client:
        short_only = client.get("/partials/log?chip=short").text

    assert "ETH/USDT" in short_only
    assert "BTC/USDT" not in short_only
    # ตัวนับมาจาก SQL บนทั้งโปรไฟล์ จึงไม่ยุบตามตัวกรอง
    assert 'ทั้งหมด <span class="lg__count">5</span>' in short_only


def test_a_chip_that_does_not_exist_falls_back_to_everything(
    client: TestClient, journal
) -> None:
    """query string เป็นของที่คนแก้เองได้ · 404 ตรงนี้คือหน้าที่อ่านบันทึกไม่ได้"""
    with client:
        page = client.get("/partials/log?chip=ไม่มีชิปนี้").text

    assert "BTC/USDT" in page and "ETH/USDT" in page


def test_an_empty_journal_says_so_instead_of_showing_a_bare_table(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    seeded(db, "paper")
    db.execute(decisions_table.delete())

    with client:
        page = client.get("/log").text

    assert "ยังไม่มีบันทึกในโปรไฟล์นี้" in page
    assert 'ทั้งหมด <span class="lg__count">0</span>' in page


def test_show_more_carries_on_after_the_last_row_instead_of_starting_over(
    db: Connection, client: TestClient, clean_config: None, monkeypatch
) -> None:
    """เกณฑ์ของ paginate — หน้าถัดไปต้องเป็นแถวที่ **ยังไม่เคยเห็น**

    ย่อหน้าละสองแถวแทนห้าสิบ เพื่อไม่ต้องเขียนบันทึกห้าสิบแถวเพื่อพิสูจน์เรื่องเดียว
    """
    monkeypatch.setattr(log_routes, "PAGE_SIZE", 2)
    db.execute(decisions_table.delete())
    head = seeded(db, "paper")
    for record in _journal_bars(head.id, profile="paper"):
        decisions_repo.insert_decision(db, record)

    with client:
        first = client.get("/partials/log").text
        cursor = first.split('hx-get="/partials/log/rows?')[1].split('"')[0]
        second = client.get(f"/partials/log/rows?{cursor}").text

    # หน้าแรกคือสองแท่งใหม่สุด (ที่ 5 กับที่ 4) หน้าสองคือที่ 3 กับที่ 2
    assert "เปิด long 0.001 BTC · margin 45.00" in first
    assert "เปิด long 0.001 BTC · margin 45.00" not in second
    assert "ฝั่ง short ปิดอยู่" in second
    assert "risk ปฏิเสธ — ไม่เกิดไม้" in second


def test_the_journal_follows_the_mode_being_viewed(
    db: Connection, owner, clean_config: None
) -> None:
    db.execute(decisions_table.delete())
    paper = seeded(db, "paper")
    live = seeded(db, "live")
    decisions_repo.insert_decision(
        db, a_decision(paper.id, profile="paper", zone="GREEN", symbol="BTC/USDT")
    )
    decisions_repo.insert_decision(
        db, a_decision(live.id, profile="live", zone="RED", symbol="BTC/USDT")
    )

    with client_in(db, owner, "paper") as client:
        as_paper = client.get("/log").text
    with client_in(db, owner, "live") as client:
        as_live = client.get("/log").text

    assert "var(--zone-green)" in as_paper and "var(--zone-red)" not in as_paper
    assert "var(--zone-red)" in as_live and "var(--zone-green)" not in as_live


def test_the_journal_opens_even_when_the_profile_has_no_active_config(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """หน้าอื่นขึ้นแบนเนอร์ "ไม่มีเวอร์ชัน active" แล้วหยุด · หน้านี้ต้องอ่านได้ต่อ

    ไม่มี config ที่เปิดใช้ = ไม่เทรด ซึ่งเป็นตอนที่คนอยากรู้ที่สุดว่าเมื่อวานเกิดอะไร
    ถ้าหน้านี้ผูกกับ `active_settings()` แบบหน้าภาพรวม บันทึกจะอ่านไม่ได้พอดีตอนนั้น
    """
    db.execute(decisions_table.delete())
    # เวอร์ชันที่ยังไม่ได้เปิดใช้ — มีแถวให้ FK ของบันทึกชี้ แต่ `active_settings()` คืน None
    draft = config_repo.insert_version(
        db, load_profile("config/paper.toml"), source="toml_seed"
    )
    decisions_repo.insert_decision(
        db, a_decision(draft.id, profile="paper", zone="GREEN")
    )

    with client:
        response = client.get("/log")

    assert response.status_code == 200
    assert "var(--zone-green)" in response.text
    assert "ยังไม่มีบันทึก" not in response.text


# ── คู่เหรียญ · ใบ 26 ─────────────────────────────────────────────────────────


def a_pair(**over: str) -> dict[str, str]:
    """ฟอร์มของบล็อก `[[symbols]]` หนึ่งบล็อก ที่ผ่านทุกกฎ — เทสต์แต่ละใบทับทีละช่อง"""
    data = {
        "original": "",
        "symbol": "SOL/USDT",
        "market": "usdtm_perp",
        "bucket_quote_long": "50.0",
        "bucket_quote_short": "",
        "leverage": "2.0",
        "allow_short": "false",
        "enabled": "true",
    }
    return data | over


def latest(db: Connection, profile: str = "paper"):
    """เวอร์ชันล่าสุดของโปรไฟล์ · `versions()` เรียงใหม่ก่อนเก่า"""
    return config_repo.versions(db, profile)[0]


def test_the_symbols_page_lists_the_pairs_of_the_active_version(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """เกณฑ์ข้อแรกของใบ 26 — หน้านี้ต้องมีเนื้อ ไม่ใช่ placeholder ของใบ 19"""
    seeded(db, "paper")

    with client:
        response = client.get("/symbols")

    assert response.status_code == 200
    assert "เนื้อหน้านี้เป็นของใบ" not in response.text
    assert "BTC/USDT" in response.text
    assert "ETH/USDT" in response.text


def test_adding_a_pair_writes_a_draft_that_carries_it_and_leaves_the_pointer_alone(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """spec/10 §เขียน ให้แถวนี้ "เวอร์ชันใหม่" เฉยๆ — ประโยคเลื่อนตัวชี้มีเฉพาะสองแถวสวิตช์"""
    active = seeded(db, "paper")

    with client:
        response = client.post("/api/paper/symbols", data=a_pair() | right_now_code())

    assert response.status_code == 200
    draft = latest(db)
    assert draft.id != active.id
    assert draft.is_active is False
    assert config_repo.active_version(db, "paper").id == active.id
    names = [s.symbol for s in config_repo.settings_of(db, draft.id).symbols]
    assert names == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_a_leverage_over_the_ceiling_is_refused_before_the_code_is_spent(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """กฎเดียวที่ฐานเขียนเป็น CHECK ไม่ได้ (spec/07) — และเป็นกฎที่เงียบถ้าไม่แปลงค่าก่อน

    รหัสที่ส่งมาผิด แต่คำตอบต้องพูดถึง `leverage` ไม่ใช่พูดถึงรหัส — ด่านที่ไม่ต้องใช้
    รหัสอยู่ก่อนด่านรหัสเสมอ
    """
    seeded(db, "paper")
    before = len(config_repo.versions(db, "paper"))

    with client:
        page = client.post(
            "/api/paper/symbols",
            data=a_pair(leverage="9.0") | {"step_up_code": "000000"},
        ).text

    assert "เกิน max_leverage" in page
    assert "รหัสยังไม่ถูกใช้" in page
    assert len(config_repo.versions(db, "paper")) == before


def test_a_spot_pair_cannot_carry_the_short_side(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """สามข้อของ spot เป็น CHECK ที่ฐานอยู่แล้ว — ฟอร์มต้องปฏิเสธก่อนถึง INSERT

    ที่ต้องมีเทสต์เพราะคำตอบของ Postgres บอกแค่ชื่อ constraint คนกรอกฟอร์มจะไม่รู้ว่า
    ช่องไหนผิด และคำขอนั้นจะกลายเป็น 500 แทนที่จะเป็นรายการที่ต้องแก้
    """
    seeded(db, "paper")
    before = len(config_repo.versions(db, "paper"))

    with client:
        page = client.post(
            "/api/paper/symbols",
            data=a_pair(market="spot", allow_short="true", bucket_quote_short="10.0", leverage="1.0")
            | right_now_code(),
        ).text

    assert "เปิด allow_short ไม่ได้" in page
    assert len(config_repo.versions(db, "paper")) == before


def test_editing_a_pair_replaces_its_row_instead_of_adding_a_second_one(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    seeded(db, "paper")

    with client:
        client.post(
            "/api/paper/symbols",
            data=a_pair(original="BTC/USDT", symbol="BTC/USDT", bucket_quote_long="500.0",
                        bucket_quote_short="60.0", allow_short="true", leverage="2.0")
            | right_now_code(),
        )

    symbols = config_repo.settings_of(db, latest(db).id).symbols
    assert [s.symbol for s in symbols] == ["BTC/USDT", "ETH/USDT"]
    assert float(symbols[0].bucket_quote_long) == 500.0


def test_renaming_a_pair_replaces_the_row_it_came_from(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ชื่อใหม่ต้องทับแถวเดิม ไม่ใช่เพิ่มแถวที่สองแล้วทิ้งของเก่าไว้เดินต่อ"""
    seeded(db, "paper")

    with client:
        client.post(
            "/api/paper/symbols",
            data=a_pair(original="ETH/USDT", symbol="XRP/USDT", market="spot",
                        bucket_quote_long="80.0", leverage="1.0")
            | right_now_code(),
        )

    names = [s.symbol for s in config_repo.settings_of(db, latest(db).id).symbols]
    assert names == ["BTC/USDT", "XRP/USDT"]


def test_typing_a_name_that_already_exists_replaces_that_row_and_says_so(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ชื่อเหรียญเป็นกุญแจของ `config_symbols` — แถวที่สองของชื่อเดิมมีไม่ได้อยู่แล้ว

    ที่ต้องตรึงคือ**คำที่ตอบกลับ**: การทับค่าเดิมทั้งแถวต้องไม่ถูกเรียกว่า "เพิ่ม"
    """
    seeded(db, "paper")

    with client:
        page = client.post(
            "/api/paper/symbols",
            data=a_pair(symbol="BTC/USDT", bucket_quote_long="999.0", leverage="1.0")
            | right_now_code(),
        ).text

    assert "ทับ BTC/USDT แล้ว" in page
    symbols = config_repo.settings_of(db, latest(db).id).symbols
    assert [s.symbol for s in symbols] == ["BTC/USDT", "ETH/USDT"]
    assert float(symbols[0].bucket_quote_long) == 999.0


def test_deleting_a_pair_writes_a_draft_without_it(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    active = seeded(db, "paper")

    with client:
        response = client.request(
            "DELETE", "/api/paper/symbols/ETH/USDT", data=right_now_code()
        )

    assert response.status_code == 200
    names = [s.symbol for s in config_repo.settings_of(db, latest(db).id).symbols]
    assert names == ["BTC/USDT"]
    assert config_repo.active_version(db, "paper").id == active.id


def test_a_code_that_arrives_in_the_query_string_is_not_accepted(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ความลับใน query string ไปนอนอยู่ใน access log ของทุกชั้นที่คำขอผ่าน

    ค่าตั้งต้นของ htmx ส่งค่าของ `DELETE` ไปทาง URL · ทางแก้อยู่ที่ `base.html`
    (`methodsThatUseUrlParams` เหลือแค่ `get`) และปลายทาง **ต้องไม่รับทางนั้นด้วย**
    ไม่งั้นการตั้งค่าที่ต้นทางจะเป็นแค่ข้อตกลงที่ใครก็ข้ามได้
    """
    seeded(db, "paper")
    before = len(config_repo.versions(db, "paper"))

    with client:
        response = client.request(
            "DELETE", "/api/paper/symbols/ETH/USDT", params=right_now_code()
        )

    assert "รหัส 6 หลักไม่ถูกต้อง" in response.text
    assert len(config_repo.versions(db, "paper")) == before


def test_the_console_tells_htmx_to_keep_delete_parameters_out_of_the_url(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """ถ้าบรรทัดนี้หาย ปุ่มลบจะกลับไปส่งรหัสทาง query string เงียบๆ"""
    seeded(db, "paper")

    with client:
        page = client.get("/symbols").text

    assert '"methodsThatUseUrlParams":["get"]' in page


def test_the_last_pair_cannot_be_deleted(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """"ไม่มี symbol เลย" เป็นข้อหนึ่งของ spec/07 §กฎการตรวจ config

    ตัวที่ปฏิเสธคือ `Settings.symbols` (`min_length=1`) ไม่ใช่ด่านที่หน้านี้ตั้งเอง —
    เทสต์นี้ตรึงว่าเส้นทางของหน้านี้เดินผ่านตัวนั้นจริง
    """
    only_one = load_profile("config/paper.toml")
    only_one = only_one.model_copy(update={"symbols": only_one.symbols[:1]})
    config_repo.activate(db, config_repo.insert_version(db, only_one, source="toml_seed").id)
    before = len(config_repo.versions(db, "paper"))

    with client:
        page = client.request(
            "DELETE", "/api/paper/symbols/BTC/USDT", data=right_now_code()
        ).text

    assert "ไม่มี symbol ให้เทรดเลย" in page
    assert len(config_repo.versions(db, "paper")) == before


def test_a_wrong_code_writes_no_version_and_leaves_a_refused_row(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    """spec/09 §step-up TOTP — ด่านที่ไม่ผ่านต้องมีร่องรอย ไม่ใช่เงียบ"""
    seeded(db, "paper")
    before = len(config_repo.versions(db, "paper"))

    with client:
        page = client.post(
            "/api/paper/symbols", data=a_pair() | {"step_up_code": "000000"}
        ).text

    assert "รหัส 6 หลักไม่ถูกต้อง" in page
    assert len(config_repo.versions(db, "paper")) == before
    row = db.execute(
        select(user_audit_log).where(user_audit_log.c.action == "config.symbols_refused")
    ).one()
    assert row.step_up_verified is False


def test_a_saved_pair_leaves_an_audit_row_marked_step_up_verified(
    db: Connection, client: TestClient, clean_config: None
) -> None:
    seeded(db, "paper")

    with client:
        client.post("/api/paper/symbols", data=a_pair() | right_now_code())

    row = db.execute(
        select(user_audit_log).where(user_audit_log.c.action == "config.symbols")
    ).one()
    assert row.step_up_verified is True
    assert "SOL/USDT" in row.target


# ── รายงาน · ใบ 25 ────────────────────────────────────────────────────────────

_REPORT_T0 = 1_787_961_600_000


def _spot_trade(db: Connection, version_id: int, n: int, exit_px: float) -> None:
    """ไม้ long ETH spot หนึ่งไม้: แถวตัดสินที่แท่งเปิด + fill ขาเปิดและขาปิด

    spot ไม่มี funding จึงไม่ต้องสร้างรอบ funding ให้ต้นทุนครบ
    """
    bar = _REPORT_T0 + n * 2 * _DAY_MS
    decisions_repo.insert_decision(db, decisions_repo.DecisionRecord(
        profile="paper", market="spot", symbol="ETH/USDT", timeframe="1d",
        bar_close_ts=bar, decided_ts=bar + 500, config_version_id=version_id,
        close_px=100.0, zone="GREEN", state="BULLISH", long_signal=True, short_signal=False,
        dry_run=True, side="long", skip_reason="dry_run", size_pct_final=25.0,
        orders=(_accepted_open(client_order_id=f"rp-{n}", sent=False, accepted=False),),
    ))
    trade = trade_id_of("spot", "ETH/USDT", "long", bar)
    common = dict(profile="paper", market="spot", symbol="ETH/USDT", trade_id=trade,
                  order_type="market", reduce_only=False, qty=1.0,
                  fee_quote=Decimal("0"), fee_ccy="USDT")
    ledger.record_fill(db, Fill(**common, leg="open", fill_ts=bar, px=100.0, ref_px=100.0,
                                client_order_id=f"o{n}", position_qty_after=1.0,
                                bar_close_ts=bar, dedupe_key=dedupe_key_of(f"o{n}")))
    ledger.record_fill(db, Fill(**common, leg="close", fill_ts=bar + _DAY_MS, px=exit_px,
                                ref_px=exit_px, client_order_id=f"c{n}",
                                position_qty_after=0.0, bar_close_ts=bar + _DAY_MS,
                                dedupe_key=dedupe_key_of(f"c{n}"), exit_reason="signal"))


@pytest.fixture
def two_trades(db: Connection, clean_config: None):
    db.execute(funding_charges.delete())
    db.execute(fills.delete())
    db.execute(decisions_table.delete())
    head = seeded(db, "paper")
    _spot_trade(db, head.id, 0, 112.0)
    _spot_trade(db, head.id, 1, 96.4)
    return head


def test_the_report_divides_by_the_capital_of_the_version_that_decided(
    client: TestClient, two_trades
) -> None:
    """ทุนของ paper = 100 + 60 (BTC) + 80 (ETH) = 240 · net +12 − 3.6 = +8.4 → +3.5%"""
    with client:
        page = client.get("/report").text

    assert "+3.5%" in page
    assert "+8.40 USDT" in page
    assert "ทุน 240.00 USDT" in page
    assert "ชนะ 1 · 50.0%" in page
    # ไม้ที่สองขาดทุนหลังไม้แรก → ย่อจากยอดสูงสุด 3.6 / 240 = -1.5%
    assert "-1.5%" in page
    assert "<polyline" in page
    assert page.count('class="rptr"') == 2
    assert "เข้าไม้จากแท่งสัญญาณจริง" in page and "2 / 2" in page


def test_a_custom_range_counts_only_trades_that_exited_inside_it(
    client: TestClient, two_trades
) -> None:
    # ไม้แรกออกแท่ง T0+1d · ไม้ที่สองออกแท่ง T0+3d
    first_exit = datetime.fromtimestamp((_REPORT_T0 + _DAY_MS) / 1000, tz=UTC).date()
    with client:
        page = client.get(
            f"/partials/report?range=custom&from={first_exit}&to={first_exit}"
        ).text

    assert page.count('class="rptr"') == 1
    assert "+5.0%" in page  # +12 / 240


def test_the_csv_carries_one_row_per_closed_trade_with_its_cost_flag(
    client: TestClient, two_trades
) -> None:
    with client:
        response = client.get("/api/paper/report/export")

    assert response.status_code == 200
    assert "cane-report-closed-trades.csv" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0] == (
        "entry_bar,exit_bar,symbol,side,size_pct,entry_fill,exit_fill,"
        "gross_pct,net_pct,exit_reason,cost_complete"
    )
    assert len(lines) == 3
    assert lines[1].split(",")[2:5] == ["ETH/USDT", "long", "25"]
    assert lines[1].endswith(",signal,true")


# ── หน้าเหรียญ · ใบ 25 ────────────────────────────────────────────────────────


def _bar_of(db: Connection, version_id: int, **overrides) -> None:
    base = dict(
        profile="paper", market="usdtm_perp", symbol="BTC/USDT", timeframe="1d",
        bar_close_ts=_REPORT_T0, decided_ts=_REPORT_T0 + 500, config_version_id=version_id,
        close_px=100.0, zone="BLUE", state="BEARISH", long_signal=False, short_signal=False,
        dry_run=True, skip_reason="no_signal",
    )
    decisions_repo.insert_decision(db, decisions_repo.DecisionRecord(**{**base, **overrides}))


@pytest.fixture
def paper_head(db: Connection, clean_config: None):
    db.execute(funding_charges.delete())
    db.execute(fills.delete())
    db.execute(decisions_table.delete())
    return seeded(db, "paper")


def _long_entry(**overrides):
    base = dict(
        zone="GREEN", state="BULLISH", long_signal=True, side="long", skip_reason="dry_run",
        leverage=1.0, margin_mode="isolated", judge_called=True, llm_fallback=False,
        factors_present=2, size_rule="confluence", size_pct_formula=50.0, size_pct_final=50.0,
        capped=False, margin=50.0, notional=50.0, qty=0.5, ref_px=100.0,
        verdicts=(
            decisions_repo.Verdict(factor="CHANNEL_BREAKOUT", side="long", present=True,
                                   cached=True, confidence=0.78, rationale="ทะลุเส้นกด"),
            decisions_repo.Verdict(factor="RETAIL_CAPITULATION", side="long", present=True,
                                   cached=False, confidence=0.85),
            decisions_repo.Verdict(factor="HIGHER_LOW", side="long", present=False,
                                   cached=False, confidence=0.62),
        ),
        risk_checks=(
            decisions_repo.RiskCheck(seq=1, layer="kill_switch", passed=True),
            decisions_repo.RiskCheck(seq=2, layer="daily_loss", passed=True, value=0.8, limit_value=3.0),
            decisions_repo.RiskCheck(seq=3, layer="liq_buffer", passed=True, value=49.5, limit_value=25.0),
        ),
        orders=(_accepted_open(client_order_id="cane-BTCUSDT-1-long", sent=False, accepted=False),),
    )
    return {**base, **overrides}


def test_a_long_entry_shows_where_its_size_came_from(
    db: Connection, client: TestClient, paper_head
) -> None:
    _bar_of(db, paper_head.id, **_long_entry())

    with client:
        page = client.get("/symbols/BTC/USDT?tab=decision").text

    assert "LONG 0.5 BTC" in page and "50% ของ bucket long" in page
    assert "โหมดทดลอง · ไม่ส่งคำสั่งจริง" in page
    assert "cane-BTCUSDT-1-long" in page
    # ที่มาของขนาดไม้: base_pct ของเวอร์ชันที่ตัดสิน (paper = 10) + 20 ต่อปัจจัยที่ผ่าน
    assert "ไม้พื้นฐาน</span><span>10</span>" in page
    assert page.count("<span>+20</span>") == 2 and "<span>+0</span>" in page
    assert "สูตรให้ 50%" in page and "ไม่ถูกตัด" in page
    assert "ครบ 3 → 100" not in page
    assert "เบรคเส้นแนวโน้มกด" in page and "ทะลุเส้นกด" in page
    assert "จาก cache 1 / 3" in page
    assert "kill switch — clear" in page and "daily loss 0.8 / 3.0%" in page
    # 100 × (1 − 0.495) = 50.50
    assert "50.50 · ห่าง 49.5% (คำนวณจาก ref_px)" in page


def test_a_flip_shows_both_legs_and_the_result_of_the_closed_long(
    db: Connection, client: TestClient, paper_head
) -> None:
    open_bar = _REPORT_T0 - _DAY_MS
    trade = trade_id_of("usdtm_perp", "BTC/USDT", "long", open_bar)
    common = dict(profile="paper", market="usdtm_perp", symbol="BTC/USDT", trade_id=trade,
                  order_type="market", qty=0.5, fee_quote=Decimal("0"), fee_ccy="USDT",
                  leverage=1.0)
    ledger.record_fill(db, Fill(**common, leg="open", fill_ts=open_bar, px=110.0, ref_px=110.0,
                                reduce_only=False, client_order_id="fo", position_qty_after=0.5,
                                bar_close_ts=open_bar, dedupe_key=dedupe_key_of("fo")))
    ledger.record_fill(db, Fill(**common, leg="close", fill_ts=_REPORT_T0, px=100.0, ref_px=100.0,
                                reduce_only=True, client_order_id="fc", position_qty_after=0.0,
                                bar_close_ts=_REPORT_T0, dedupe_key=dedupe_key_of("fc"),
                                exit_reason="signal"))
    _bar_of(db, paper_head.id, **_long_entry(
        zone="RED", state="BEARISH", long_signal=False, short_signal=True, side="short",
        verdicts=(), factors_present=0, size_rule="confluence", size_pct_formula=10.0,
        size_pct_final=10.0,
        orders=(
            _accepted_open(leg="close", order_side="buy", reduce_only=True,
                           client_order_id="close-leg", qty=0.5, sent=False, accepted=False),
            _accepted_open(order_side="sell", client_order_id="open-leg",
                           sent=False, accepted=False),
        ),
        flip=decisions_repo.Flip(close_qty_intended=0.5, close_qty_filled=0.5,
                                 residual_qty=0.0, aborted=False),
    ))

    with client:
        page = client.get("/symbols/BTC/USDT?tab=decision").text

    assert "แผนกลับข้าง — สองขาในแท่งเดียว" in page
    assert "ขา 1 · ปิด long" in page and "ขา 2 · เปิด short" in page
    assert "110.00" in page  # ราคาเข้าเดิมของไม้ long ที่ปิด
    assert "-9.1% · -5.00 USDT" in page
    assert "flip_aborted" in page  # แถบเตือน


def test_a_short_signal_with_short_disabled_says_it_closed_but_did_not_open(
    db: Connection, client: TestClient, paper_head
) -> None:
    _bar_of(db, paper_head.id, zone="RED", short_signal=True, skip_reason="short_disabled",
            orders=(_accepted_open(leg="close", order_side="sell", reduce_only=True, qty=0.45),))

    with client:
        page = client.get("/symbols/BTC/USDT?tab=decision").text

    assert "ปิด long 0.45 · ไม่เปิด short" in page
    assert "เปิด short — allow_short = false" in page


def test_a_rejected_signal_names_the_layer_that_refused_it(
    db: Connection, client: TestClient, paper_head
) -> None:
    """เหตุผลของ risk รายชั้น — หน้าบันทึกฝากมาไว้ที่นี่ (api/log.py)"""
    _bar_of(db, paper_head.id, zone="GREEN", long_signal=True, side="long",
            skip_reason="risk_rejected",
            risk_checks=(
                decisions_repo.RiskCheck(seq=1, layer="kill_switch", passed=True),
                decisions_repo.RiskCheck(seq=2, layer="daily_loss", passed=False,
                                         value=4.2, limit_value=3.0),
            ))

    with client:
        page = client.get("/symbols/BTC/USDT?tab=decision").text

    assert "risk ปฏิเสธ — ไม่เกิดไม้" in page
    assert "ชั้นที่ปฏิเสธ: daily_loss (4.20 เทียบเพดาน 3)" in page
    assert "sd__gate sd__gate--fail" in page


def test_a_bar_with_no_signal_says_it_did_nothing(
    db: Connection, client: TestClient, paper_head
) -> None:
    _bar_of(db, paper_head.id)

    with client:
        page = client.get("/symbols/BTC/USDT?tab=decision").text

    assert "ไม่ทำอะไร" in page and "ทำไมไม่ลงไม้" in page
    assert "ปฏิเสธทั้งสองฝั่ง — ไม่ใช่แท่งสัญญาณ" in page


def test_the_header_shows_the_size_the_opening_decision_chose_with_its_margin(
    db: Connection, client: TestClient, paper_head
) -> None:
    """handoff §9.2 `LONG 25%` · % มาจากแถวที่เปิดไม้ ไม่ใช่ margin หาร bucket ของวันนี้"""
    _bar_of(db, paper_head.id, **_long_entry())
    trade = trade_id_of("usdtm_perp", "BTC/USDT", "long", _REPORT_T0)
    ledger.record_fill(db, Fill(
        profile="paper", market="usdtm_perp", symbol="BTC/USDT", trade_id=trade, leg="open",
        fill_ts=_REPORT_T0, px=100.0, qty=0.5, client_order_id="hd", order_type="market",
        reduce_only=False, position_qty_after=0.5, bar_close_ts=_REPORT_T0,
        dedupe_key=dedupe_key_of("hd"), ref_px=100.0, fee_quote=Decimal("0"), fee_ccy="USDT",
        leverage=1.0,
    ))

    with client:
        page = client.get("/symbols/BTC/USDT?tab=decision").text

    assert "LONG 50% · margin 50.00" in page


def test_a_pair_that_is_not_in_the_profile_is_not_found(
    client: TestClient, paper_head
) -> None:
    with client:
        response = client.get("/symbols/DOGE/USDT")

    assert response.status_code == 404


def test_the_rail_links_each_pair_to_its_own_page(client: TestClient, paper_head) -> None:
    with client:
        page = client.get("/overview").text

    assert 'href="/symbols/BTC/USDT?market=usdtm_perp"' in page
    assert 'href="/symbols/ETH/USDT?market=spot"' in page


def _golden_bars():
    return [row.bar for row in load(GOLDEN_DIR / "BINANCE_BTCUSDT.P, 1D.csv")]


#: แท่งที่เป็น long signal ตัวแรกของ fixture (ตัวเดียวกับ `BUY_1` ของ test_pipeline_db)
_BUY_1 = 213


@pytest.fixture
def chart_bars(db: Connection, paper_head):
    db.execute(bars_table.delete())
    history = _golden_bars()[: _BUY_1 + 1]
    insert_bars(db, "usdtm_perp", "BTC/USDT", "1d", history)
    return history


def test_the_chart_tab_draws_the_last_85_bars_with_their_zones(
    db: Connection, client: TestClient, paper_head, chart_bars
) -> None:
    last = chart_bars[-1]
    _bar_of(db, paper_head.id, bar_close_ts=last.close_ts, close_px=last.close,
            zone="GREEN", state="BULLISH", long_signal=True, side="long",
            skip_reason="risk_rejected")

    with client:
        page = client.get("/symbols/BTC/USDT").text

    assert page.count('class="ch__body ') == 85
    assert page.count("fill: var(--zone-") == 85
    assert 'class="ch__fast"' in page and 'class="ch__slow"' in page
    assert "แท่งนี้เป็นจุดสัญญาณฝั่ง long — เปิดไม้ที่แท่งถัดไป" in page
    assert "ไม่ตรงกับที่บันทึกตอนตัดสิน" not in page
    assert f"{last.close:,.2f}" in page


def test_a_zone_that_disagrees_with_the_record_is_called_out(
    db: Connection, client: TestClient, paper_head, chart_bars
) -> None:
    last = chart_bars[-1]
    _bar_of(db, paper_head.id, bar_close_ts=last.close_ts, zone="RED")

    with client:
        page = client.get("/symbols/BTC/USDT?tab=chart").text

    assert "ไม่ตรงกับที่บันทึกตอนตัดสิน (RED)" in page
    # กล่องผลอ่านสัญญาณจากแถว ไม่ใช่จากที่คำนวณใหม่
    assert "แท่งล่าสุดไม่ใช่จุดสัญญาณทั้งสองฝั่ง" in page


def test_the_chart_marks_where_trades_opened(
    db: Connection, client: TestClient, paper_head, chart_bars
) -> None:
    bar = chart_bars[-10]
    trade = trade_id_of("usdtm_perp", "BTC/USDT", "long", bar.close_ts)
    ledger.record_fill(db, Fill(
        profile="paper", market="usdtm_perp", symbol="BTC/USDT", trade_id=trade, leg="open",
        fill_ts=bar.close_ts, px=bar.close, qty=0.01, client_order_id="mk",
        order_type="market", reduce_only=False, position_qty_after=0.01,
        bar_close_ts=bar.close_ts, dedupe_key=dedupe_key_of("mk"), ref_px=bar.close,
        fee_quote=Decimal("0"), fee_ccy="USDT", leverage=1.0,
    ))

    with client:
        page = client.get("/symbols/BTC/USDT").text

    assert 'class="ch__mark ch__mark--open-long"' in page
    # ไม้ที่ยังถือทำให้ header ไม่ใช่ FLAT · ไม่มีแถวที่เปิด = ขึ้นปริมาณแทน % · margin = 0.01 × close
    assert f"LONG 0.01 · margin {0.01 * bar.close:.2f}" in page
