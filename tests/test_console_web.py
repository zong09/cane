"""คอนโซล (ใบ 19) — โครง layout, สลับโหมด, ปุ่ม engine

ไม่แตะ Postgres และไม่แตะเน็ต · `Supervisor` ถูกแทนด้วยตัวที่จดว่าถูกเรียกอะไรบ้าง
เพราะสิ่งที่เทสต์ชุดนี้ต้องพิสูจน์คือ **ใครถูกเรียกและเมื่อไหร่** ไม่ใช่ SQL วิ่งถูกไหม
(อันนั้นเป็นของ `test_console_web_db.py`)

เกณฑ์เสร็จข้อที่สองของใบ 19 — "กดสลับโหมดแล้ว engine ทั้งสองยังทำงานเหมือนเดิม" —
อยู่ที่ `test_switching_mode_never_touches_either_engine` ซึ่งผูกกับ
spec/10 §3. สลับโหมดไม่ใช่การควบคุม
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cane.api import symbols as sym_routes
from cane.api.app import create_app
from cane.api.deps import get_sup, signed_in
from cane.auth import service as auth_service
from cane.config.validate import ConfigError, Problem
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import killswitch as killswitch_repo
from cane.db.repo import ledger as ledger_repo
from cane.db.repo import permissions as perms
from cane.db.repo import report as report_repo
from cane.db.repo import users as users_repo
from cane.db.repo.sessions import Session
from cane.db.repo.users import User
from cane.engine.state import CRASHED, PROFILES, RUNNING, STOPPED
from cane.engine.supervisor import EngineView, Supervisor

NOW = 1_700_000_000_000


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


class FakeSpawn:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, profile: str) -> FakeProcess:
        self.calls.append(profile)
        return FakeProcess(pid=1000 + len(self.calls))


class FakeConn:
    """รับ `execute()` แล้วจดไว้เฉยๆ

    route เขียน `user_audit_log` จริงตั้งแต่ใบ 20 · ไฟล์นี้ไม่มี Postgres จึงรับ
    คำสั่งไว้แล้วทิ้ง — **สิ่งที่ไฟล์นี้ทดสอบคือการต่อสาย ไม่ใช่ SQL** ส่วนที่ว่า
    audit ลงจริงและถูกกรองจริงอยู่ที่ `test_auth_db.py`
    """

    def __init__(self) -> None:
        self.statements: list[object] = []

    def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return None

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


class FakeDb:
    """`connect()` กับ `begin()` คืนอะไรก็ได้ที่เป็น context manager

    handler ไม่ได้ยิง SQL เองเลย มันส่ง `conn` ต่อให้ repo กับ supervisor ซึ่งถูก
    แทนไว้หมดแล้วในเทสต์ชุดนี้
    """

    def __init__(self) -> None:
        self.conn = FakeConn()

    def connect(self) -> FakeConn:
        return self.conn

    def begin(self) -> FakeConn:
        return self.conn


class RecordingSupervisor(Supervisor):
    """จดทุกคำสั่งที่เข้ามา · `launch()`/`signal_stop()` ของจริงยังทำงานตามเดิม

    เหตุที่ไม่ปล่อยให้ `status()` ของจริงทำงาน: มันอ่านตาราง `engine_state` จริง
    ซึ่งเป็นสิ่งที่ชุด `not db` ไม่มี
    """

    def __init__(self, states: dict[str, str] | None = None) -> None:
        self.spawn_calls = FakeSpawn()
        super().__init__(spawn=self.spawn_calls)
        self.states = dict(states or {p: STOPPED for p in PROFILES})
        self.should_run = {p: self.states[p] == RUNNING for p in PROFILES}
        self.commands: list[tuple[str, str]] = []

    def _snapshot(self, profile: str) -> EngineView:
        return EngineView(
            profile=profile,
            should_run=self.should_run[profile],
            last_heartbeat_ts=NOW if self.states[profile] == RUNNING else None,
            blocked_reason=None,
            status=self.states[profile],
            pid=None,
        )

    def status(self, conn: object, *, now: int | None = None) -> tuple[EngineView, ...]:
        return tuple(self._snapshot(profile) for profile in PROFILES)

    def start(self, conn: object, profile: str, *, now: int | None = None) -> EngineView:
        self.commands.append(("start", profile))
        before = self._snapshot(profile)
        # เลียนแบบของจริง: หลัง commit เจตนาเป็น true และ engine ขึ้นมาเต้นแล้ว
        self.should_run[profile] = True
        self.states[profile] = RUNNING
        return before

    def stop(self, conn: object, profile: str, *, now: int | None = None) -> EngineView:
        self.commands.append(("stop", profile))
        self.should_run[profile] = False
        self.states[profile] = STOPPED
        return self._snapshot(profile)


def fake_user(role: str = "OWNER") -> User:
    return User(
        id=7,
        email="someone@example.com",
        name="นภัส พ.",
        role=role,
        status="active",
        password_hash="x",
        totp_secret_enc="x",
        totp_enrolled_ts=NOW,
        totp_last_counter=None,
        last_login_ts=NOW,
    )


def fake_session(mode: str = "paper") -> Session:
    return Session(
        id=3,
        user_id=7,
        ip=None,
        user_agent=None,
        mode=mode,
        created_ts=NOW,
        last_seen_ts=NOW,
        expires_ts=NOW + 1,
        revoked_ts=None,
    )


@pytest.fixture(autouse=True)
def no_db_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """ตัดทุกอย่างที่จะยิง SQL จริงออก — ไฟล์นี้ทดสอบการต่อสาย ไม่ใช่ข้อมูล

    สิทธิ์จริงกับ login จริงถูกทดสอบกับ Postgres ที่ `test_auth_db.py`,
    `test_auth_service_db.py` และ `test_console_auth_db.py`
    """
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, profile: None)
    # หน้าตั้งค่า (ใบ 21) อ่านประวัติเวอร์ชันกับชื่อคนแก้ด้วย · `FakeConn.execute()`
    # คืน `None` การปล่อยให้ repo ตัวจริงวิ่งจึงระเบิดเป็น AttributeError ก่อนถึง assert
    monkeypatch.setattr(config_repo, "versions", lambda conn, profile: [])
    # rail อ่านโซนล่าสุดต่อเหรียญตั้งแต่ใบ 22 · ทุกหน้ามี rail จึงโดนทุกเทสต์
    monkeypatch.setattr(decisions_repo, "latest_per_symbol", lambda conn, profile, tf: {})
    # หน้าภาพรวมอ่าน kill switch จริง · ไม่มีแถว = ไม่ latched (repo คืนค่าตั้งต้นให้)
    monkeypatch.setattr(
        killswitch_repo,
        "read",
        lambda conn, profile: killswitch_repo.KillSwitch(profile=profile, latched=False),
    )
    # หน้าบันทึก (ใบ 24) อ่านหัวของ `decisions` ทั้งโปรไฟล์ทีละหน้า · ตัวนับของชิป
    # เป็น `count(*) FILTER` ที่ `FakeConn` ตอบไม่ได้ ของจริงอยู่ที่ชุด db
    monkeypatch.setattr(decisions_repo, "journal", lambda conn, profile, **kw: [])
    monkeypatch.setattr(
        decisions_repo,
        "journal_counts",
        lambda conn, profile, **kw: dict.fromkeys(decisions_repo.CHIPS, 0),
    )
    # หน้ารายงาน (ใบ 25) อ่าน VIEW `closed_trades` กับตัวอ่านของ `repo/report` · ของจริงอยู่ที่ชุด db
    monkeypatch.setattr(ledger_repo, "closed_trades", lambda conn, profile: [])
    monkeypatch.setattr(report_repo, "origins", lambda conn, profile: {})
    monkeypatch.setattr(report_repo, "capital_of", lambda conn, versions: {})
    monkeypatch.setattr(report_repo, "open_trades", lambda conn, profile: [])
    monkeypatch.setattr(
        report_repo, "flips", lambda conn, profile, **kw: report_repo.FlipCount(0, 0)
    )
    monkeypatch.setattr(users_repo, "everyone", lambda conn: [])
    monkeypatch.setattr(perms, "allowed", lambda conn, *, role, cap: role != "VIEWER")
    monkeypatch.setattr(
        auth_service, "verify_step_up", lambda conn, user, code, *, now: code == "111111"
    )


#: รหัส step-up ที่ `no_db_reads` ตั้งให้ผ่าน · ของจริงมาจากแอป Authenticator
GOOD_CODE = {"step_up_code": "111111"}


def build(
    states: dict[str, str] | None = None,
    *,
    role: str = "OWNER",
    mode: str = "paper",
    anonymous: bool = False,
) -> tuple[TestClient, RecordingSupervisor]:
    sup = RecordingSupervisor(states)
    app = create_app(db=FakeDb())
    app.dependency_overrides[get_sup] = lambda: sup
    if not anonymous:
        app.dependency_overrides[signed_in] = lambda: (fake_session(mode), fake_user(role))
    return TestClient(app), sup


# ── เกณฑ์เสร็จข้อ 2 ของใบ · spec/10 §3. สลับโหมดไม่ใช่การควบคุม ────────────────


def test_switching_mode_never_touches_either_engine() -> None:
    """"โหมดคือมุมมอง" — การสลับต้องไม่ start ไม่ stop ไม่ spawn อะไรทั้งสิ้น

    ถ้าข้อนี้พัง คนที่กดดู paper สักครู่แล้วกลับมาจะเจอ `live` ที่ถูกสั่งหยุดไป
    โดยไม่มีใครตั้งใจ ซึ่งเป็นความเสียหายที่ไม่มีอะไรบนหน้าจอบอก
    """
    client, sup = build({"live": RUNNING, "paper": RUNNING})
    with client:
        before = dict(sup.should_run)
        for _ in range(3):
            assert client.post("/api/session/mode", data={"mode": "paper"}).status_code == 200

    assert sup.commands == []
    assert sup.spawn_calls.calls == []
    assert sup.should_run == before


def test_switching_to_live_without_a_step_up_code_is_refused() -> None:
    """spec/09 §step-up TOTP — รหัสต้องมากับ request นั้นเอง ไม่ใช่กับ session"""
    client, _ = build()
    with client:
        response = client.post("/api/session/mode", data={"mode": "live"})

    assert response.status_code == 403
    # ต้องเป็น partial ของกล่องเตือนใน modal ไม่ใช่ JSON `{"detail": ...}` ของ FastAPI
    # — htmx เป็นคนรับ ไม่ใช่โค้ดที่อ่าน JSON เป็น
    assert 'class="modal__warn"' in response.text


def test_switching_to_live_with_a_valid_step_up_code_goes_through() -> None:
    client, sup = build()
    with client:
        response = client.post("/api/session/mode", data={"mode": "live", **GOOD_CODE})

    assert response.status_code == 200
    assert sup.commands == []  # ยังไม่แตะ engine ตัวใดเลย


def test_switching_back_to_paper_needs_no_code_at_all() -> None:
    """live → paper ทันที ไม่ต้องยืนยัน (handoff §6.1) — การลดความเสี่ยงไม่ต้องขออนุญาต"""
    client, _ = build(mode="live")
    with client:
        response = client.post("/api/session/mode", data={"mode": "paper"})

    assert response.status_code == 200


# ── spec/10 §6. สัญญาของ API ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/staging/engine/start"),
        ("post", "/api/staging/engine/stop"),
        ("post", "/api/session/mode"),
    ],
)
def test_a_profile_that_does_not_exist_is_not_found_rather_than_a_bad_request(
    method: str, path: str
) -> None:
    """"โปรไฟล์ที่ไม่มีอยู่ไม่ใช่คำขอที่ผิดรูป" — คำขอรูปถูก มันแค่ชี้ไปที่ของที่ไม่มี"""
    client, _ = build()
    with client:
        response = getattr(client, method)(path, data={"mode": "staging", **GOOD_CODE})

    assert response.status_code == 404


def test_starting_an_engine_twice_in_a_row_answers_twice_without_spawning_twice() -> None:
    """สตาร์ทซ้ำตอน heartbeat ยังสด = no-op คืน 200 ไม่ใช่ error"""
    client, sup = build()
    with client:
        first = client.post("/api/paper/engine/start", data=GOOD_CODE)
        second = client.post("/api/paper/engine/start", data=GOOD_CODE)

    assert (first.status_code, second.status_code) == (200, 200)
    assert sup.spawn_calls.calls == ["paper"]


def test_the_card_returned_after_start_shows_the_state_after_the_command() -> None:
    """`Supervisor.start()` คืนภาพ *ก่อน* คำสั่ง — route ต้องอ่านใหม่ก่อน render

    ถ้า route ลืมอ่านใหม่ หน้าจอจะขึ้น `stopped` ทันทีหลังคนกดสตาร์ท ซึ่งอ่านได้ว่า
    ปุ่มไม่ทำงาน แล้วคนจะกดซ้ำ
    """
    client, _ = build()
    with client:
        response = client.post("/api/paper/engine/start", data=GOOD_CODE)

    assert "engine paper · running" in response.text
    assert "stop engine" in response.text


def test_stopping_an_engine_with_no_handle_is_not_an_error() -> None:
    """คอนโซลที่เพิ่งรีสตาร์ทไม่มี handle — `should_run` ในตารางทำงานแทนอยู่แล้ว"""
    client, sup = build({"live": RUNNING, "paper": RUNNING})
    with client:
        response = client.post("/api/paper/engine/stop", data=GOOD_CODE)

    assert response.status_code == 200
    assert sup.commands == [("stop", "paper")]


# ── layout ───────────────────────────────────────────────────────────────────


def test_the_rail_renders_both_groups_and_every_menu_item() -> None:
    client, _ = build()
    with client:
        page = client.get("/overview").text

    for label in ("ภาพรวม", "คู่เหรียญ", "ความเสี่ยง", "บันทึก", "รายงาน", "ตั้งค่า"):
        assert label in page
    assert "MODE" in page and "ทั้งระบบ" in page and "ไม่ผูกกับโหมด" in page
    assert "ผู้ใช้" in page
    # user chip อ่านจาก current_user() ไม่ใช่จากข้อความที่ฝังในเทมเพลต
    assert "OWNER" in page and "นพ" in page  # ตัวย่อของ "นภัส พ."


def test_a_page_that_does_not_exist_is_not_found() -> None:
    client, _ = build()
    with client:
        assert client.get("/nowhere").status_code == 404


def test_the_engine_card_polls_so_the_first_heartbeat_can_clear_a_false_crash() -> None:
    """หลัง `launch()` ลูกยังไม่ได้เต้นครั้งแรก สถานะที่คำนวณได้จึงเป็น `crashed`

    นั่นถูกตามตารางของ `derive_status()` แต่มันเป็นภาพชั่วคราว · ถ้าการ์ดไม่ poll
    หน้าจอจะค้างที่ crashed ตลอดไปทั้งที่ engine เดินอยู่
    """
    client, _ = build({"live": CRASHED, "paper": CRASHED})
    with client:
        card = client.get("/partials/engine").text

    assert "crashed" in card
    assert 'hx-get="/partials/engine"' in card
    assert "every 5s" in card
    # การ์ดที่ poll มาเองไม่ต้องมีนัดพิเศษ — นัดนั้นมีเฉพาะการ์ดที่ตอบหลังกดปุ่ม
    assert "load delay" not in card


def test_the_status_endpoint_answers_for_both_profiles_in_one_request() -> None:
    """spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 8 · การ์ด PROFILE แสดงอีกโหมดด้วย"""
    client, _ = build({"live": RUNNING, "paper": STOPPED})
    with client:
        body = client.get("/api/engine/status").json()

    assert [row["profile"] for row in body["engines"]] == list(PROFILES)
    assert {row["profile"]: row["status"] for row in body["engines"]} == {
        "live": RUNNING,
        "paper": STOPPED,
    }


def test_the_card_answered_right_after_a_command_asks_for_one_extra_recheck() -> None:
    """หลัง `launch()` ลูกยังไม่ทันเต้นครั้งแรก การ์ดนัดนี้จึงย่นเวลาที่ป้ายผิดค้างอยู่

    ไม่เดาว่า "เพิ่งสั่งไป = กำลังขึ้น" เพราะ engine ที่ตายตั้งแต่ยังไม่ทันเต้น
    ครั้งแรกหน้าตาเหมือนกันเป๊ะ · เลือกให้ป้ายผิดทางฝั่งที่ทำให้คนไปกดสตาร์ท
    """
    client, _ = build({"live": CRASHED, "paper": CRASHED})
    with client:
        after_start = client.post("/api/paper/engine/start", data=GOOD_CODE).text
        after_stop = client.post("/api/paper/engine/stop", data=GOOD_CODE).text

    assert "load delay:1200ms" in after_start
    assert "load delay:1200ms" in after_stop


def test_a_crashed_engine_still_shows_stop_until_the_supervisor_can_tell_it_apart() -> None:
    """ปุ่มเลือกจาก `should_run` ไม่ใช่ `status` — ตรึงไว้เพราะยังเปลี่ยนไม่ปลอดภัย

    spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile บอกว่า engine ที่พังให้คนกดสตาร์ทเอง
    และ `launch()` ก็เปิดให้ `crashed` อยู่แล้ว · แต่ช่วงสองสามวินาทีแรกหลัง
    `launch()` สถานะก็อ่านได้เป็น `crashed` เหมือนกัน ปุ่ม start ตรงนั้นจะพาไป
    `launch()` รอบสองแล้วได้ process ตัวที่สอง · ปลดตรงนี้ได้เมื่อ `Supervisor`
    แยก "เพิ่ง spawn" ออกจาก "ตายแล้ว" ได้ — เป็นของที่ต้องแก้ที่ engine/ ไม่ใช่ที่ route
    """
    client, sup = build({"live": CRASHED, "paper": CRASHED})
    sup.should_run["paper"] = True
    with client:
        card = client.get("/partials/engine").text

    assert "stop engine" in card
    assert "start engine" not in card


# ── ด่านของใบ 20 ที่ต่อเข้ามา ─────────────────────────────────────────────────


def test_a_browser_with_no_session_is_sent_to_the_login_page() -> None:
    """คนที่พิมพ์ URL ตรงๆ ต้องเจอหน้า login ไม่ใช่ JSON 401"""
    client, _ = build(anonymous=True)
    with client:
        response = client.get("/overview", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_an_htmx_request_with_no_session_gets_a_redirect_header_instead() -> None:
    """redirect ของ `hx-post` จะ swap ทั้งหน้า login เข้าไปในการ์ดใบเล็ก"""
    client, _ = build(anonymous=True)
    with client:
        response = client.post(
            "/api/paper/engine/start", data=GOOD_CODE, headers={"HX-Request": "true"}
        )

    assert response.status_code == 401
    assert response.headers["HX-Redirect"] == "/login"


def test_a_viewer_cannot_control_the_engine() -> None:
    """spec/09 §10. เกณฑ์ยืนยันความถูกต้อง ข้อ 3 — ที่ชั้น HTTP"""
    client, sup = build(role="VIEWER")
    with client:
        response = client.post("/api/paper/engine/start", data=GOOD_CODE)

    assert response.status_code == 403
    assert sup.spawn_calls.calls == []


def test_a_viewer_cannot_open_the_users_page(no_db_reads: None) -> None:
    client, _ = build(role="VIEWER")
    with client:
        assert client.get("/users").status_code == 403


def test_controlling_the_engine_without_a_code_is_refused(no_db_reads: None) -> None:
    """spec/09 §step-up TOTP — "ขอทุกครั้งที่ลงมือ ไม่มีช่วงผ่อนผัน\""""
    client, sup = build()
    with client:
        response = client.post("/api/paper/engine/start")

    assert response.status_code == 403
    assert sup.spawn_calls.calls == []


def test_a_wrong_step_up_code_reopens_the_modal_rather_than_returning_json() -> None:
    """htmx เป็นคนรับคำตอบ · JSON `{"detail": ...}` จะกลายเป็นหน้าจอที่เงียบสนิท"""
    client, _ = build()
    with client:
        response = client.post("/api/paper/engine/start", data={"step_up_code": "999999"})

    assert response.status_code == 403
    assert 'name="step_up_code"' in response.text
    assert "modal__warn" in response.text


def test_the_engine_buttons_open_the_step_up_modal_instead_of_firing_directly() -> None:
    """ถ้าปุ่มยิง endpoint ตรงๆ มันจะได้ 403 ทุกครั้งเพราะไม่มีรหัสไปด้วย"""
    client, _ = build()
    with client:
        card = client.get("/partials/engine").text

    assert "/partials/stepup/paper/start" in card
    assert 'hx-post="/api/paper/engine/start"' not in card


def test_an_api_endpoint_answers_401_rather_than_redirecting_to_a_html_page() -> None:
    """client ที่อ่าน JSON เป็นจะ parse หน้า login ไม่ออกแล้วรายงานผิดเรื่อง"""
    client, _ = build(anonymous=True)
    with client:
        response = client.get("/api/engine/status", follow_redirects=False)

    assert response.status_code == 401


# ── หน้าตั้งค่า · ใบ 21 ───────────────────────────────────────────────────────


def a_config(profile: str = "paper"):
    """config ที่ผ่านทุกกฎ · อ่านจากไฟล์ seed จริงเพื่อไม่ต้องประกอบด้วยมือ

    ไฟล์ TOML เหลือหน้าที่เดียวคือทางเข้าของ `cane db seed` (spec/07 §Config profile)
    ที่นี่ใช้มันเป็น **ตัวอย่างค่าที่ถูกต้อง** ไม่ใช่เป็นแหล่งที่หน้าจออ่าน
    """
    from cane.config import load_profile

    return load_profile(f"config/{profile}.toml")


def a_broken_config() -> ConfigError:
    """เวอร์ชันที่บันทึกไว้ตอนกฎยังไม่เข้ม แล้ว `settings_of()` ปฏิเสธตอนอ่าน"""
    return ConfigError(
        [
            Problem(("base_pct",), "base_pct = 32.0 อยู่นอกช่วง 5–20", "ตรวจตอนบันทึกเวอร์ชัน"),
            Problem(("risk", "max_leverage"), "ขาด max_leverage", "risk limit ไม่มีค่าตั้งต้นให้"),
        ],
        source="config version paper v1",
    )


def test_the_config_page_has_a_body_of_its_own_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """ใบ 21 เป็นหน้าแรกของชุด 21–26 ที่เลิกใช้ placeholder ของใบ 19"""
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, profile: a_config())
    client, _ = build()
    with client:
        page = client.get("/config").text

    assert "ใบ 19 ทำแค่โครง" not in page
    assert "ประวัติเวอร์ชัน" in page
    assert 'name="base_pct"' in page


def test_a_profile_with_no_active_version_says_so_and_offers_nothing_to_edit() -> None:
    """ไม่มีเวอร์ชัน active = ไม่เทรด (spec/07 §Config profile) — ไม่ใช่ฟอร์มเปล่าให้กรอก

    ฟอร์มเปล่าที่กรอกได้จะกลายเป็นการสร้าง config จากศูนย์ ซึ่งไม่ใช่สิ่งที่หน้านี้ทำ
    """
    client, _ = build()
    with client:
        page = client.get("/config").text

    assert "ยังไม่มีเวอร์ชัน config ที่เปิดใช้" in page
    assert 'name="base_pct"' not in page


def test_a_config_that_no_longer_validates_lists_every_field_that_must_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`context.build()` กลืน `ConfigError` ทิ้ง หน้านี้จึงต้องเรียก repo เอง

    และต้องเป็น 200 ไม่ใช่ 500 — หน้าที่เปิดไม่ขึ้นคือหน้าที่แก้ config ไม่ได้
    """

    def explode(conn: object, profile: str):
        raise a_broken_config()

    monkeypatch.setattr(config_repo, "active_settings", explode)
    client, _ = build()
    with client:
        response = client.get("/config")

    assert response.status_code == 200
    assert "โหลดไม่ผ่าน — พบ 2 ข้อ" in response.text
    assert "ไม่มีโหมดเตือนแล้วไปต่อ" in response.text


def test_the_problem_list_points_at_field_paths_not_line_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """badge เป็น `risk.max_leverage` ไม่ใช่ `L20` — spec/07 §path ของฟิลด์ที่ผิด

    mockup ของ design ติด badge เลขบรรทัดไว้ ซึ่งใช้อ้างอิงไม่ได้แล้วตั้งแต่ config
    ย้ายลง DB · ไม่มีไฟล์ก็ไม่มีบรรทัด
    """

    def explode(conn: object, profile: str):
        raise a_broken_config()

    monkeypatch.setattr(config_repo, "active_settings", explode)
    client, _ = build()
    with client:
        page = client.get("/config").text

    assert '<span class="cfg__path">risk.max_leverage</span>' in page
    assert '<span class="cfg__path">base_pct</span>' in page


def test_someone_without_edit_profile_sees_the_page_but_cannot_edit_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`view_overview` เปิดหน้าได้ · การแก้เป็น `edit_profile` ซึ่ง OWNER คนเดียวมี

    `no_db_reads` ปล่อยทุก role ที่ไม่ใช่ VIEWER จึงแยกสองสิทธิ์นี้ไม่ได้ —
    เทสต์นี้ต้องวางของปลอมของตัวเอง
    """
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, profile: a_config())
    monkeypatch.setattr(perms, "allowed", lambda conn, *, role, cap: cap != "edit_profile")
    client, _ = build(role="TRADER")
    with client:
        page = client.get("/config").text

    assert "ดูได้อย่างเดียว — การแก้ต้องมีสิทธิ์ edit_profile" in page


def test_the_other_profile_opens_in_its_own_tab_with_a_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ดูโปรไฟล์ที่ไม่ได้รันอยู่ก็ได้ แต่ต้องบอกว่ากำลังดูของที่ไม่ได้เดิน"""
    monkeypatch.setattr(
        config_repo, "active_settings", lambda conn, profile: a_config("live")
    )
    client, _ = build(mode="paper")
    with client:
        body = client.get("/partials/config/live").text

    assert "กำลังดูโปรไฟล์ที่ไม่ได้ทำงานอยู่" in body


def test_a_profile_that_does_not_exist_is_not_found() -> None:
    """spec/10 §6. สัญญาของ API — profile ที่ไม่มีคือ 404 ไม่ใช่ 400"""
    client, _ = build()
    with client:
        assert client.get("/partials/config/nowhere").status_code == 404


def test_the_diff_card_counts_the_fields_that_differ_from_the_other_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ต่างจาก live N ค่า` — N มาจากการเทียบจริง ไม่ใช่เลขในภาพ mockup"""
    monkeypatch.setattr(
        config_repo, "active_settings", lambda conn, profile: a_config(profile)
    )
    client, _ = build(mode="paper")
    with client:
        page = client.get("/config").text

    assert "ต่างจาก live" in page
    assert "คีย์ที่ไม่อยู่ในรายการนี้มีค่าเท่ากันทั้งสองโปรไฟล์" in page
    # base_pct ของสองไฟล์ seed ต่างกันจริง (10.0 กับ 5.0)
    assert "base_pct" in page


def test_the_other_profile_with_no_active_version_says_it_cannot_be_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_repo,
        "active_settings",
        lambda conn, profile: a_config("paper") if profile == "paper" else None,
    )
    client, _ = build(mode="paper")
    with client:
        page = client.get("/config").text

    assert "live ยังไม่มีเวอร์ชัน active — เทียบไม่ได้" in page


def test_the_other_profile_that_no_longer_validates_says_it_cannot_be_compared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """เทียบกับของที่ประกอบกลับไม่ได้ไม่ได้ · และเป็นคนละเรื่องกับ "ยังไม่มีเวอร์ชัน" """

    def by_profile(conn: object, profile: str):
        if profile == "paper":
            return a_config("paper")
        raise a_broken_config()

    monkeypatch.setattr(config_repo, "active_settings", by_profile)
    client, _ = build(mode="paper")
    with client:
        page = client.get("/config").text

    assert "live โหลดไม่ผ่าน 2 ข้อ — เทียบไม่ได้" in page


# ── บันทึกเป็นเวอร์ชันใหม่ · ใบ 21 ────────────────────────────────────────────


class RecordingInsert:
    """จดว่า `insert_version()` ถูกเรียกด้วยอะไร · คืนหัวเวอร์ชันปลอมกลับไป"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, conn, settings, **kwargs):  # noqa: ANN001
        self.calls.append({"settings": settings} | kwargs)
        return config_repo.ConfigVersion(
            id=99,
            profile=settings.profile,
            version=7,
            source=kwargs["source"],
            note=kwargs.get("note"),
            created_ts=NOW,
            created_by_user_id=kwargs.get("created_by_user_id"),
            is_active=False,
        )


def saving(monkeypatch: pytest.MonkeyPatch):
    """แทน `active_settings` ด้วย config จริง และดัก `insert_version`/`activate`"""
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, p: a_config(p))
    inserted = RecordingInsert()
    activated: list[int] = []
    monkeypatch.setattr(config_repo, "insert_version", inserted)
    monkeypatch.setattr(config_repo, "activate", lambda conn, version_id: activated.append(version_id))
    return inserted, activated


def test_saving_creates_a_draft_and_never_moves_the_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ฐานให้คอนโซลแค่ INSERT — บันทึกจึงเป็นเวอร์ชันใหม่เสมอ และยังไม่เปิดใช้"""
    inserted, activated = saving(monkeypatch)
    client, _ = build()
    with client:
        response = client.post(
            "/api/paper/config", data={"base_pct": "12.0", "note": "ลดขนาดไม้"}
        )

    assert response.status_code == 200
    assert len(inserted.calls) == 1
    assert inserted.calls[0]["source"] == "console"
    assert inserted.calls[0]["note"] == "ลดขนาดไม้"
    assert inserted.calls[0]["created_by_user_id"] == 7
    assert inserted.calls[0]["settings"].base_pct == 12.0
    assert activated == []
    assert "ยังไม่เปิดใช้" in response.text


def test_a_leverage_ceiling_below_a_symbol_leverage_is_caught_before_the_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """กฎเดียวที่ฐานเขียนเป็น CHECK ไม่ได้ — และมันเงียบถ้าส่งค่าเป็นสตริง

    `cross_checks()` ใช้ `_is_number()` ซึ่งคืน False ให้ `"0.5"` · ถ้า route โยนค่า
    จากฟอร์มเข้า `validate_settings()` ดิบๆ เวอร์ชันที่ leverage เกินเพดานจะลงฐานได้
    """
    inserted, _ = saving(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/config", data={"risk.max_leverage": "0.5"})

    assert response.status_code == 200
    assert "symbols[0].leverage" in response.text
    assert inserted.calls == []


def test_a_value_out_of_range_comes_back_200_with_the_error_on_that_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """htmx ทิ้ง 4xx ทุกตัวยกเว้น 403 — รายการที่ต้องแก้ต้องมาเป็น 200"""
    inserted, _ = saving(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/config", data={"base_pct": "32.0"})

    assert response.status_code == 200
    assert "อยู่นอกช่วง 5–20" in response.text
    assert inserted.calls == []


def test_a_field_that_is_not_a_number_at_all_is_a_field_error_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted, _ = saving(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/config", data={"base_pct": "สิบ"})

    assert response.status_code == 200
    assert "ไม่ใช่ตัวเลข" in response.text
    assert inserted.calls == []


def test_a_blank_required_field_reads_as_missing_not_as_a_bad_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"ขาด consecutive_loss_breaker" บอกสิ่งที่ต้องทำ ส่วน "ต้องเป็นตัวเลข" ไม่บอก"""
    inserted, _ = saving(monkeypatch)
    client, _ = build()
    with client:
        response = client.post(
            "/api/paper/config", data={"risk.consecutive_loss_breaker": ""}
        )

    assert response.status_code == 200
    assert "ขาด consecutive_loss_breaker" in response.text
    assert inserted.calls == []


def test_an_empty_optional_field_is_saved_as_none_rather_than_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`taker_fee_pct` เว้นว่างได้โดยเจตนา — ศูนย์แปลว่า "ไม่มีค่าธรรมเนียม" ซึ่งคนละเรื่อง"""
    inserted, _ = saving(monkeypatch)
    client, _ = build()
    with client:
        client.post("/api/paper/config", data={"broker.taker_fee_pct": ""})

    assert inserted.calls[0]["settings"].broker.taker_fee_pct is None


def test_the_fields_the_form_never_shows_come_from_the_active_version_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`symbols` (ใบ 26) กับ `dry_run`/`allow_short` (ใบ 23) ไม่ได้อยู่ในฟอร์ม

    ลอกมาทั้งชุดแล้วทับเฉพาะช่องที่ส่งมา ไม่ใช่ประกอบใหม่จากฟอร์ม ไม่งั้นเวอร์ชันใหม่
    จะไม่มีเหรียญเลย
    """
    inserted, _ = saving(monkeypatch)
    client, _ = build()
    with client:
        client.post("/api/paper/config", data={"base_pct": "12.0", "dry_run": "false"})

    saved = inserted.calls[0]["settings"]
    assert [s.symbol for s in saved.symbols] == ["BTC/USDT", "ETH/USDT"]
    assert saved.dry_run is True


def test_saving_needs_edit_profile_even_though_the_page_opens_for_everyone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted, _ = saving(monkeypatch)
    client, _ = build(role="VIEWER")
    with client:
        response = client.post("/api/paper/config", data={"base_pct": "12.0"})

    assert response.status_code == 403
    assert inserted.calls == []


def test_saving_with_no_active_version_is_refused_rather_than_inventing_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ไม่มีของให้ลอก · ทางเข้าครั้งแรกคือ `cane db seed` ไม่ใช่ฟอร์มเปล่า"""
    inserted = RecordingInsert()
    monkeypatch.setattr(config_repo, "insert_version", inserted)
    client, _ = build()
    with client:
        response = client.post("/api/paper/config", data={"base_pct": "12.0"})

    assert response.status_code == 200
    assert "cane db seed" in response.text
    assert inserted.calls == []


# ── เปิดใช้เวอร์ชัน · ใบ 21 ───────────────────────────────────────────────────


def a_version(version_id: int, version: int, *, is_active: bool = False):
    return config_repo.ConfigVersion(
        id=version_id,
        profile="paper",
        version=version,
        source="console",
        note=None,
        created_ts=NOW,
        created_by_user_id=7,
        is_active=is_active,
    )


def with_history(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """ประวัติสองเวอร์ชันของ paper · คืนลิสต์ที่ `activate()` จะเขียนลงไป"""
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, p: a_config(p))
    # live มีเวอร์ชันของตัวเองด้วย — ถ้าปล่อยว่าง เทสต์ข้ามโปรไฟล์จะผ่านเพราะ
    # "ไม่มีเวอร์ชันเลย" ไม่ใช่เพราะด่านความเป็นเจ้าของทำงาน
    monkeypatch.setattr(
        config_repo,
        "versions",
        lambda conn, profile: [a_version(2, 2), a_version(1, 1, is_active=True)]
        if profile == "paper"
        else [a_version(5, 1, is_active=True)],
    )
    activated: list[int] = []
    monkeypatch.setattr(
        config_repo, "activate", lambda conn, version_id: activated.append(version_id)
    )
    return activated


def test_the_activate_button_opens_the_step_up_modal_instead_of_firing_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/09 §step-up TOTP — รหัสต้องมากับคำขอที่ลงมือ ปุ่มจึงเปิด modal ก่อน"""
    with_history(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/config").text

    assert 'hx-get="/partials/config/paper/activate/2"' in page
    # เวอร์ชันที่เปิดใช้อยู่แล้วไม่มีปุ่มให้กดซ้ำ
    assert 'hx-get="/partials/config/paper/activate/1"' not in page


def test_activating_without_the_right_code_is_refused_with_the_modal_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 อยู่ในลิสต์ `responseHandling` ของ `base.html` จึง swap ขึ้นจอได้จริง"""
    activated = with_history(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/config/2/activate", data={"step_up_code": "000000"})

    assert response.status_code == 403
    assert 'class="modal__warn"' in response.text
    assert 'name="step_up_code"' in response.text
    assert 'hx-post="/api/paper/config/2/activate"' in response.text
    assert activated == []


def test_activating_moves_the_pointer_and_closes_the_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activated = with_history(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/config/2/activate", data=GOOD_CODE)

    assert response.status_code == 200
    assert activated == [2]
    # การ์ดกลับไปแบบ out-of-band แล้วเหลือความว่างมาแทน modal — modal จึงปิดเอง
    assert 'hx-swap-oob="true"' in response.text
    assert "เปิดใช้เวอร์ชัน v2 แล้ว" in response.text


def test_activating_a_version_that_belongs_to_the_other_profile_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`activate()` เลื่อนตัวชี้ของ profile ที่อยู่ในแถว ไม่ใช่ของที่อยู่ใน URL

    live มีเวอร์ชันของตัวเองอยู่ (id 5) · id 2 เป็นของ paper — ถ้าด่านนี้หายไป
    คำขอนี้จะไปเปิดใช้เวอร์ชันของ paper โดยที่ URL บอกว่ากำลังทำอะไรกับ live
    """
    activated = with_history(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/live/config/2/activate", data=GOOD_CODE)

    assert response.status_code == 404
    assert activated == []


def test_a_version_that_does_not_exist_cannot_be_activated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_history(monkeypatch)
    client, _ = build()
    with client:
        assert client.get("/partials/config/paper/activate/404").status_code == 404


def test_someone_without_edit_profile_cannot_activate_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activated = with_history(monkeypatch)
    client, _ = build(role="VIEWER")
    with client:
        response = client.post("/api/paper/config/2/activate", data=GOOD_CODE)

    assert response.status_code == 403
    assert activated == []


# ── หน้าภาพรวม · ใบ 22 ────────────────────────────────────────────────────────


def a_record(**overrides):
    """บันทึกของแท่งหนึ่ง — ค่าตั้งต้นคือแท่งที่ไม่ทำอะไร"""
    base = {
        "profile": "paper",
        "market": "usdtm_perp",
        "symbol": "BTC/USDT",
        "timeframe": "1d",
        "bar_close_ts": NOW,
        "decided_ts": NOW,
        "config_version_id": 1,
        "close_px": 77_500.0,
        "zone": "GREEN",
        "state": "BULLISH",
        "long_signal": False,
        "short_signal": False,
        "dry_run": True,
        "skip_reason": "no_signal",
    }
    return decisions_repo.DecisionRecord(**{**base, **overrides})


def with_overview(monkeypatch: pytest.MonkeyPatch, latest=None, profile: str = "paper"):
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, p: a_config(p))
    monkeypatch.setattr(
        decisions_repo, "latest_per_symbol", lambda conn, p, tf: dict(latest or {})
    )


def test_the_overview_page_has_a_body_of_its_own_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_overview(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "ใบ 19 ทำแค่โครง" not in page
    assert "สัญญาณรอดำเนินการ" in page
    assert "Kill switch" in page


def test_a_number_with_no_source_yet_shows_a_dash_never_a_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ศูนย์เป็นคำตอบ ("วันนี้ไม่ขาดทุนเลย") ส่วนขีดแปลว่ายังตอบไม่ได้ — คนละเรื่อง

    repo นี้ยึดเส้นนี้อยู่แล้วที่ `day_pnl_pct=None` ใน `risk/limits.py` ซึ่งเขียนว่า
    `None` แปลว่า **คำนวณไม่ได้**
    """
    with_overview(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "ต้องอ่านสถานะไม้จาก venue" in page
    assert "—" in page
    # เพดานมาจาก config จริง ไม่ใช่ขีด
    assert "/ 5.0%" in page


def test_no_decision_rows_at_all_means_the_count_cannot_be_answered_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ฐานที่ engine ยังไม่เคยเดิน — `สัญญาณรอดำเนินการ = 0` จะเป็นคำตอบที่ผิด"""
    with_overview(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "ยังไม่มีบันทึก" in page


def test_a_long_signal_on_the_latest_bar_is_counted_and_shown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_overview(
        monkeypatch,
        {("usdtm_perp", "BTC/USDT"): a_record(long_signal=True, skip_reason=None)},
    )
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "เปิด long" in page
    assert "var(--zone-green)" in page


def test_a_short_signal_is_hidden_when_the_profile_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/07 §`allow_short` มีสองชั้น — ผลจริงคือ AND ของสองชั้น

    ปิดข้างบนแล้วเหรียญที่เปิดไว้เองก็ยังปิด · สัญญาณที่กดไม่ได้ต้องไม่โผล่บนจอ
    """
    closed = a_config("paper").model_copy(update={"allow_short": False})
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, p: closed)
    monkeypatch.setattr(
        decisions_repo,
        "latest_per_symbol",
        lambda conn, p, tf: {
            ("usdtm_perp", "BTC/USDT"): a_record(short_signal=True, skip_reason=None)
        },
    )
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "เปิด short" not in page
    assert "ฝั่ง short ปิดอยู่" in page


def test_a_bar_the_cane_rule_rejected_raises_the_cold_start_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cane_rule` เป็นประตูเดียวที่เข้าเส้นทาง cold start ได้ (`rules/late_entry.py`)

    ใช้มันเป็นเงื่อนไขได้ตรงๆ โดยไม่ต้องมีธงใหม่ในฐาน ซึ่ง spec/10 ห้ามไว้อยู่แล้ว
    """
    with_overview(
        monkeypatch,
        {("usdtm_perp", "BTC/USDT"): a_record(skip_reason="cane_rule")},
    )
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "Cold start" in page
    assert "แท่งล่าสุดไม่ใช่จุดสัญญาณ" in page


def test_a_latched_kill_switch_is_visible_on_the_overview_not_only_on_the_risk_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_overview(monkeypatch)
    monkeypatch.setattr(
        killswitch_repo,
        "read",
        lambda conn, profile: killswitch_repo.KillSwitch(
            profile=profile, latched=True, reason="แพ้ติดกันครบ"
        ),
    )
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "latched" in page
    assert "แพ้ติดกันครบ" in page


def test_the_overview_says_so_when_the_profile_has_no_active_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = build()
    with client:
        page = client.get("/overview").text

    assert "ยังไม่มีเวอร์ชัน config ที่เปิดใช้" in page


def test_switching_mode_tells_the_page_to_reload_its_own_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """สลับโหมด swap แค่การ์ดสองใบ · เนื้อหน้าที่ผูกกับโหมดต้องรู้ตัวเองว่าต้องดึงใหม่

    ไม่งั้นตัวเลขทั้งหน้าค้างอยู่ที่โหมดเดิมทั้งที่แถบบนเปลี่ยนไปแล้ว
    """
    with_overview(monkeypatch)
    client, _ = build(mode="live")
    with client:
        switched = client.post("/api/session/mode", data={"mode": "paper"})
        page = client.get("/overview").text

    assert switched.headers["HX-Trigger"] == "cane:mode"
    assert 'hx-trigger="cane:mode from:body"' in page


# ── หน้าความเสี่ยง · ใบ 23 ─────────────────────────────────────────────────────


def with_risk(monkeypatch: pytest.MonkeyPatch, latest=None, profile: str = "paper"):
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, p: a_config(p))
    monkeypatch.setattr(
        decisions_repo, "latest_per_symbol", lambda conn, p, tf: dict(latest or {})
    )


def test_the_risk_page_has_a_body_of_its_own_now(monkeypatch: pytest.MonkeyPatch) -> None:
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "ใบ 19 ทำแค่โครง" not in page
    assert "เพดานความเสี่ยง" in page
    assert "Kill switch" in page


def test_a_number_that_needs_open_positions_shows_a_dash_never_a_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ไม่มีตารางเก็บสถานะไม้เลย (ใบ 13) · "0 ไม้ · margin 0.00" จึงเป็นคำตอบที่ผิด"""
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "— ไม้ · margin —" in page
    assert "notional — ·" in page
    # เพดานมาจาก config เวอร์ชันที่ active จึงเป็นของจริงตั้งแต่วันนี้ ไม่ใช่ขีด
    assert '<span class="rk__limitval">35%</span>' in page
    assert '<span class="rk__limitval">50</span>' in page
    assert "bucket long รวม 180.00 USDT" in page


def test_the_breaker_shows_its_ceiling_as_empty_pips_rather_than_a_count_of_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ตัวนับต้องมาจาก VIEW ที่ยังไม่มี — ศูนย์ช่องที่เต็มแปลว่า "ยังไม่แพ้เลย" ซึ่งยังตอบไม่ได้"""
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    # paper seed ตั้ง consecutive_loss_breaker = 4
    assert page.count('<span class="rk__pip"></span>') == 4
    assert "แพ้ติดกัน — ไม้" in page


def test_the_kill_switch_card_tells_its_story_from_the_reason_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """breaker กับคนกดให้เหตุคนละอย่าง · ประโยคตายตัวบนการ์ดจะโกหกกรณีหนึ่งเสมอ"""
    with_risk(monkeypatch)
    monkeypatch.setattr(
        killswitch_repo,
        "read",
        lambda conn, profile: killswitch_repo.KillSwitch(
            profile=profile, latched=True, reason="คนกดตอนข่าวออก", latched_by="zong"
        ),
    )
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "Kill switch — latched" in page
    assert "คนกดตอนข่าวออก" in page
    assert "zong" in page


def test_the_kill_switch_card_never_mentions_a_json_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ไฟล์ design เขียนว่าสถานะอยู่ที่ `state/killswitch.json` — errata ของ spec/10 ยกเลิกไปแล้ว"""
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "killswitch.json" not in page
    assert "แถวในฐาน" in page


def test_the_order_id_note_says_the_side_is_the_order_side_not_the_position_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/06 §กันสั่งซ้ำ (reconciliation) เขียน errata เองว่า mock ในดีไซน์พิมพ์ผิด"""
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "ฝั่งของออเดอร์ (buy/sell)" in page
    assert "ฝั่งสถานะ (long/short)" not in page


def test_the_broker_panel_derives_default_type_instead_of_reading_it_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`defaultType` ไม่มีในฐาน · perp เป็น `swap` ไม่ใช่ `future` อย่างที่ดีไซน์เขียน"""
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    # paper seed มีทั้ง perp และ spot — ต้องขึ้นทั้งสองค่า
    assert "defaultType = spot · swap" in page
    assert "future" not in page


def test_a_spot_symbol_says_it_has_no_liquidation_rather_than_an_unknown_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spot ไม่เรียกชั้น liq_buffer เลย ไม่ใช่เรียกแล้วผ่านเสมอ (spec/06)"""
    with_risk(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "ไม่มี (spot)" in page


def test_a_row_reads_long_only_when_the_profile_switch_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ผลจริงของแต่ละเหรียญคือ AND สองชั้น — ปิดข้างบนแล้วเหรียญที่เปิดเองก็ยังปิด"""
    closed = a_config("paper").model_copy(update={"allow_short": False})
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, p: closed)
    monkeypatch.setattr(decisions_repo, "latest_per_symbol", lambda conn, p, tf: {})
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "long + short" not in page
    assert "long เท่านั้น" in page


def test_leftovers_from_an_aborted_flip_are_listed_apart_from_the_positions_we_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ไม้แบบนี้มีเลเวอเรจและไม่มี stop จนกว่าคนจะเห็น (decisions #19)

    ถ้าคอนโซลกลืนมันไปกับไม้ปกติ การตัดสินให้คนปิดด้วยมือก็กลายเป็นการเงียบใส่ความเสี่ยง
    """
    stuck = decisions_repo.Unmanaged(
        side="short", qty=0.004, source="flip_aborted", first_seen_bar_close_ts=NOW
    )
    with_risk(
        monkeypatch,
        {("usdtm_perp", "BTC/USDT"): a_record(skip_reason="flip_aborted", unmanaged=(stuck,))},
    )
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "ไม้ที่ระบบไม่ได้ตั้งใจถือ" in page
    assert "flip_aborted" in page
    assert "0.004" in page


def test_the_risk_page_says_so_when_the_profile_has_no_active_config() -> None:
    """เพดานทุกตัวมาจากเวอร์ชันที่เปิดใช้ · ไม่มีเวอร์ชัน = ไม่มีเพดานให้แสดง"""
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "ยังไม่มีเวอร์ชัน config ที่เปิดใช้" in page
    # การ์ด kill switch อ่านจากตาราง จึงยังต้องแสดงได้แม้ config จะไม่มี
    assert "Kill switch" in page


def test_the_risk_page_reloads_its_own_body_when_the_mode_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_risk(monkeypatch)
    client, _ = build(mode="live")
    with client:
        client.post("/api/session/mode", data={"mode": "paper"})
        page = client.get("/risk").text

    assert 'hx-trigger="cane:mode from:body"' in page


# ── สวิตช์หยุดฉุกเฉิน · ใบ 23 ──────────────────────────────────────────────────


class RecordingSwitch:
    """จดว่ามีการกด latch/unlatch อะไรบ้าง · ความ idempotent ของจริงอยู่ที่ repo

    `latch()` เป็น `ON CONFLICT DO UPDATE … WHERE latched IS false` และมีเทสต์กับ
    Postgres จริงอยู่แล้วที่ `tests/test_risk.py` — ที่นี่ตรวจว่า route ไม่เพิ่มด่าน
    ของตัวเองมาทับความ idempotent นั้น
    """

    def __init__(self, latched: bool = False) -> None:
        self.state = killswitch_repo.KillSwitch(profile="paper", latched=latched)
        self.latched: list[tuple[str, str]] = []
        self.unlatched: list[str] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(killswitch_repo, "read", lambda conn, profile: self.state)
        monkeypatch.setattr(killswitch_repo, "latch", self._latch)
        monkeypatch.setattr(killswitch_repo, "unlatch", self._unlatch)

    def _latch(self, conn, profile, *, reason, by=None):
        self.latched.append((profile, reason))
        if not self.state.latched:
            self.state = killswitch_repo.KillSwitch(
                profile=profile, latched=True, reason=reason, latched_by=by
            )
        return self.state

    def _unlatch(self, conn, profile):
        self.unlatched.append(profile)
        self.state = killswitch_repo.KillSwitch(profile=profile, latched=False)
        return self.state


def test_pressing_the_stop_button_latches_without_asking_for_a_code_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/09 §3. ตารางสิทธิ์ — 13 สิทธิ์ × 5 role ให้ latch กว้างและไม่ต้องยืนยันซ้ำ

    ความช้าตอนฉุกเฉินแพงกว่าการกดเกิน · modal ที่ขวางปุ่มหยุดคือความช้าแบบนั้น
    """
    with_risk(monkeypatch)
    switch = RecordingSwitch()
    switch.install(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/killswitch/latch")

    assert response.status_code == 200
    assert len(switch.latched) == 1
    assert "Kill switch — latched" in response.text


def test_pressing_the_stop_button_twice_answers_both_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/10 §เขียน — "การกดหยุดฉุกเฉินซ้ำต้องไม่เคยล้มเหลว"

    คนที่กดแล้วเห็น error เพราะมันถูก latch อยู่แล้ว จะไม่รู้ว่าตัวเองหยุดสำเร็จหรือยัง
    แล้วจะไปกดอย่างอื่น · route จึงต้องไม่มีด่าน "latched อยู่แล้ว" ของตัวเอง
    """
    with_risk(monkeypatch)
    switch = RecordingSwitch()
    switch.install(monkeypatch)
    client, _ = build()
    with client:
        first = client.post("/api/paper/killswitch/latch")
        second = client.post("/api/paper/killswitch/latch")

    assert first.status_code == 200 and second.status_code == 200
    assert len(switch.latched) == 2


def test_latching_never_touches_either_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec/10 §`engine.should_run` ≠ `kill_switch.latched` — คนละ state คนละ lifecycle

    latch แล้ว engine ยังเดินอยู่ ยังบันทึกการตัดสินใจที่ลงท้ายว่าถูกกั้น ·
    ประวัติที่ขาดหายไปตอนฉุกเฉินคือประวัติที่ขาดหายไปตรงที่อยากอ่านที่สุด
    """
    with_risk(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, sup = build({"paper": RUNNING, "live": STOPPED})
    with client:
        client.post("/api/paper/killswitch/latch")

    assert sup.commands == []


def test_the_stop_button_is_not_rendered_for_someone_who_cannot_press_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ปุ่มที่ render แล้วได้ 403 ตอนกด = htmx เอา JSON ของ FastAPI มาแปะหน้าจอ"""
    with_risk(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    monkeypatch.setattr(
        perms, "allowed", lambda conn, *, role, cap: cap == "view_overview"
    )
    client, _ = build(role="VIEWER")
    with client:
        page = client.get("/risk").text

    assert "หยุดยิงออเดอร์ทันที" not in page


def test_the_unlock_button_asks_for_the_profile_name_as_well_as_a_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """สามด่านตอบคำถามคนละข้อ จึงไม่มีข้อไหนแทนกันได้ (spec/10 §2. สาม state ที่คนละเรื่องกัน)"""
    with_risk(monkeypatch)
    RecordingSwitch(latched=True).install(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text
        modal = client.get("/partials/risk/paper/unlatch").text

    assert "ปลดล็อก — ต้องพิมพ์ชื่อ profile ยืนยัน" in page
    assert 'name="profile_name"' in modal
    assert 'name="step_up_code"' in modal
    assert "/api/paper/killswitch/unlatch" in modal


def test_typing_the_wrong_profile_name_never_spends_the_totp_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`verify_step_up()` เขียน counter เมื่อผ่าน และ counter ใช้ร่วมกับ login

    ตรวจรหัสก่อนชื่อเมื่อไหร่ คนที่พิมพ์ชื่อผิดจะเสียรหัสรอบนั้นไปโดยยังไม่ได้ปลดอะไรเลย
    """
    with_risk(monkeypatch)
    switch = RecordingSwitch(latched=True)
    switch.install(monkeypatch)

    def never(*args, **kwargs):
        raise AssertionError("ต้องไม่เรียก verify_step_up เมื่อชื่อโปรไฟล์ยังไม่ตรง")

    monkeypatch.setattr(auth_service, "verify_step_up", never)
    client, _ = build()
    with client:
        response = client.post(
            "/api/paper/killswitch/unlatch", data={"profile_name": "live", **GOOD_CODE}
        )

    assert response.status_code == 200
    assert "ชื่อโปรไฟล์ไม่ตรง" in response.text
    assert switch.unlatched == []


def test_a_wrong_step_up_code_reopens_the_unlock_modal_rather_than_returning_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 อยู่ในลิสต์ `responseHandling` ของ `base.html` จึง swap ได้ · 4xx อื่นหายเงียบ"""
    with_risk(monkeypatch)
    switch = RecordingSwitch(latched=True)
    switch.install(monkeypatch)
    client, _ = build()
    with client:
        response = client.post(
            "/api/paper/killswitch/unlatch",
            data={"profile_name": "paper", "step_up_code": "000000"},
        )

    assert response.status_code == 403
    assert "รหัส 6 หลักไม่ถูกต้อง" in response.text
    assert 'name="profile_name"' in response.text
    assert switch.unlatched == []


def test_unlocking_past_all_three_gates_clears_the_switch_and_closes_the_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """สำเร็จแล้วคืนเนื้อหน้าแบบ out-of-band — htmx เอาไปวางที่ `#risk-body` แล้ว
    เหลือความว่างมาแทน modal ซึ่งคือการปิด modal โดยไม่ต้องมี JS
    """
    with_risk(monkeypatch)
    switch = RecordingSwitch(latched=True)
    switch.install(monkeypatch)
    client, _ = build()
    with client:
        response = client.post(
            "/api/paper/killswitch/unlatch", data={"profile_name": "paper", **GOOD_CODE}
        )

    assert response.status_code == 200
    assert switch.unlatched == ["paper"]
    assert 'hx-swap-oob="true"' in response.text
    assert "Kill switch — ปกติ" in response.text


def test_the_latch_answer_is_not_out_of_band_because_the_button_targets_the_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ปุ่มหยุดยิงตรงไปที่ `#risk-body` · ติดธง oob ด้วยจะได้ความว่างทับทั้งหน้า"""
    with_risk(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build()
    with client:
        response = client.post("/api/paper/killswitch/latch")

    assert 'hx-swap-oob="true"' not in response.text


# ── สวิตช์ dry_run / allow_short · ใบ 23 ──────────────────────────────────────


def test_flipping_dry_run_writes_a_new_version_and_activates_it_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """สวิตช์ความปลอดภัยที่ยังไม่มีผลจนกว่าใครจะไปกด activate คือสวิตช์ที่โกหก

    ตัวเขียนยังเป็นคู่เดิมของใบ 21 (`insert_version` + `activate`) — ใบนี้ไม่เพิ่ม
    ทางเขียน config เส้นที่สอง
    """
    inserted, activated = saving(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build(mode="live")
    with client:
        response = client.post(
            "/api/live/config/dry_run", data={"value": "false", **GOOD_CODE}
        )

    assert response.status_code == 200
    assert len(inserted.calls) == 1
    assert inserted.calls[0]["settings"].dry_run is False
    assert activated == [99]
    assert 'hx-swap-oob="true"' in response.text


def test_the_switch_posts_the_value_it_wants_so_pressing_twice_lands_in_one_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ให้เซิร์ฟเวอร์กลับด้านเองเมื่อไหร่ สองคำขอที่ซ้อนกันจะสลับกันไปมา"""
    inserted, _ = saving(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build(mode="live")
    with client:
        client.post("/api/live/config/allow_short", data={"value": "false", **GOOD_CODE})
        client.post("/api/live/config/allow_short", data={"value": "false", **GOOD_CODE})

    assert [call["settings"].allow_short for call in inserted.calls] == [False, False]


def test_paper_cannot_leave_dry_run_and_says_why_before_spending_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ฐานปฏิเสธด้วย `ck_config_settings_paper_dry_run` อยู่แล้ว (spec/06 §dry_run)

    ด่านที่ไม่ต้องใช้รหัสต้องอยู่ก่อนด่านรหัส — คนที่กดสวิตช์ที่ฐานปฏิเสธอยู่แล้ว
    ต้องไม่เสียรหัสของรอบนั้นไปด้วย
    """
    inserted, activated = saving(monkeypatch)
    RecordingSwitch().install(monkeypatch)

    def never(*args, **kwargs):
        raise AssertionError("ต้องไม่เรียก verify_step_up เมื่อฐานปฏิเสธอยู่แล้ว")

    monkeypatch.setattr(auth_service, "verify_step_up", never)
    client, _ = build()
    with client:
        response = client.post(
            "/api/paper/config/dry_run", data={"value": "false", **GOOD_CODE}
        )

    assert response.status_code == 200
    assert "paper บังคับ dry_run = true" in response.text
    assert inserted.calls == [] and activated == []


def test_flipping_a_switch_with_a_wrong_code_writes_nothing_and_moves_no_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted, activated = saving(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build(mode="live")
    with client:
        response = client.post(
            "/api/live/config/dry_run", data={"value": "false", "step_up_code": "000000"}
        )

    assert response.status_code == 403
    assert "รหัส 6 หลักไม่ถูกต้อง" in response.text
    assert inserted.calls == [] and activated == []


def test_turning_the_short_side_off_carries_every_other_field_along(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ลอกทั้งชุดแล้วทับช่องเดียว — `symbols` กับ risk limit ต้องไปครบ ไม่ใช่หายไป"""
    inserted, _ = saving(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build(mode="live")
    with client:
        client.post("/api/live/config/allow_short", data={"value": "false", **GOOD_CODE})

    written = inserted.calls[0]["settings"]
    original = a_config("live")
    assert written.allow_short is False
    assert written.symbols == original.symbols
    assert written.risk == original.risk
    assert written.dry_run == original.dry_run


def test_a_switch_value_that_is_neither_true_nor_false_is_a_message_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted, _ = saving(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build(mode="live")
    with client:
        response = client.post(
            "/api/live/config/dry_run", data={"value": "ปิด", **GOOD_CODE}
        )

    assert response.status_code == 200
    assert "ต้องเป็น true หรือ false" in response.text
    assert inserted.calls == []


def test_the_switches_are_not_rendered_for_someone_who_cannot_flip_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_risk(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    monkeypatch.setattr(
        perms, "allowed", lambda conn, *, role, cap: cap == "view_overview"
    )
    client, _ = build(role="TRADER", mode="live")
    with client:
        page = client.get("/risk").text

    assert "ปิดฝั่ง short" not in page
    assert "ปิดโหมดทดลอง" not in page
    # แต่ค่าที่ใช้อยู่ยังต้องอ่านได้
    assert "ฝั่ง short" in page and "โหมดทดลอง" in page


def test_paper_shows_no_button_for_a_switch_the_database_pins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_risk(monkeypatch)
    RecordingSwitch().install(monkeypatch)
    client, _ = build()
    with client:
        page = client.get("/risk").text

    assert "ปิดโหมดทดลอง" not in page
    assert "paper บังคับเปิดที่ฐาน" in page


# ── หน้าบันทึก · ใบ 24 ────────────────────────────────────────────────────────


def test_the_journal_asks_for_read_decisions_at_both_doors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/09 ผูก `records` ไว้กับ `read_decisions` — เปลือกกับเนื้อต้องขอตัวเดียวกัน

    ถ้าหน้า HTML ขอแค่ `view_overview` แล้ว partial ขอ `read_decisions` role ที่มี
    สิทธิ์แรกอย่างเดียวจะเปิดหน้าได้ แล้วตารางกลับ 403 เงียบๆ เพราะ htmx กลืน 4xx
    """
    asked: list[str] = []

    def record(conn, *, role: str, cap: str) -> bool:
        asked.append(cap)
        return True

    monkeypatch.setattr(perms, "allowed", record)
    client, _ = build()
    with client:
        client.get("/log")
        client.get("/partials/log")
        client.get("/partials/log/rows?before_ts=1&before_id=1")

    assert asked == ["read_decisions", "read_decisions", "read_decisions"]


def test_the_report_page_has_a_body_of_its_own_now() -> None:
    client, _ = build()
    with client:
        page = client.get("/report").text

    assert "ใบ 19 ทำแค่โครง" not in page
    assert "ตั้งแต่เริ่มรัน" in page and "กำหนดช่วงเอง" in page
    assert "บอททำตามกฎหรือไม่" in page
    assert "ยังไม่มีไม้ที่ปิดแล้วในช่วงนี้" in page
    # ปุ่มส่งออกชี้ไปที่ endpoint ของโปรไฟล์ที่ดูอยู่
    assert 'href="/api/paper/report/export?range=all' in page


def test_the_report_export_of_a_profile_that_does_not_exist_is_not_found() -> None:
    """spec/10 §6. สัญญาของ API — โปรไฟล์ที่ไม่มีอยู่ไม่ใช่คำขอที่ผิดรูป"""
    client, _ = build()
    with client:
        response = client.get("/api/staging/report/export")

    assert response.status_code == 404


def test_exporting_the_report_needs_export_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """spec/09 ผูก CSV ของรายงานไว้กับ `export_records` เหมือนการส่งออกบันทึก"""
    monkeypatch.setattr(perms, "allowed", lambda conn, *, role, cap: cap != "export_records")
    client, _ = build(role="TRADER")
    with client:
        response = client.get("/api/paper/report/export")
        page = client.get("/report")

    assert response.status_code == 403
    # หน้ายังเปิดได้ แต่ปุ่มกดไม่ได้ — ไม่ใช่ลิงก์ที่พาไปเจอ 403
    assert page.status_code == 200
    assert "/report/export" not in page.text
    assert "rp__export--off" in page.text


def test_a_viewer_without_read_decisions_cannot_open_a_symbol_page() -> None:
    client, _ = build(role="VIEWER")
    with client:
        response = client.get("/symbols/BTC/USDT")

    assert response.status_code == 403


def test_choosing_a_cold_start_route_needs_its_own_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spec/09 §4. endpoint → สิทธิ์ที่ต้องมี — `choose_cold_start_route` ไม่ใช่ `read_decisions`"""
    monkeypatch.setattr(perms, "allowed", lambda conn, *, role, cap: cap != "choose_cold_start_route")
    client, _ = build(role="VIEWER")
    with client:
        response = client.post("/api/paper/coldstart/BTC/USDT", data={"route": "skip"})

    assert response.status_code == 403


def test_every_chip_of_the_journal_is_on_the_page_even_with_nothing_to_count() -> None:
    client, _ = build()
    with client:
        page = client.get("/log").text

    for label in (
        "ทั้งหมด",
        "มีออเดอร์",
        "ฝั่ง long",
        "ฝั่ง short",
        "กลับข้าง",
        "risk ปฏิเสธ",
        "LLM ตอบไม่ได้",
        "ถูกเพดานตัด",
    ):
        assert label in page
    assert "ยังไม่มีบันทึกในโปรไฟล์นี้" in page


# ── คู่เหรียญ · ใบ 26 — ส่วนที่คิดได้โดยไม่ต้องมีฐาน ────────────────────────


def test_the_form_refuses_a_boolean_it_does_not_recognise() -> None:
    """`allow_short = "on"` ของ checkbox ต้องดัง ไม่ใช่เงียบแล้วกลายเป็น false

    ค่าที่กลายเป็น `False` เงียบๆ กับช่องนี้แปลว่า "ไม่เปิดฝั่ง short" ซึ่งเป็นคำตอบ
    ที่คนกรอกไม่ได้สั่ง
    """
    block, problems = sym_routes._block(
        {"symbol": "SOL/USDT", "market": "usdtm_perp", "bucket_quote_long": "50",
         "leverage": "2", "allow_short": "on", "enabled": "true"}
    )

    assert [p.field_path for p in problems] == ["allow_short"]
    assert "allow_short" not in block


def test_the_numbers_reach_the_validator_as_numbers_not_strings() -> None:
    """`cross_checks()` ใช้ `_is_number()` ซึ่งคืน False ให้สตริง — กฎ leverage จะเงียบ"""
    block, problems = sym_routes._block(
        {"symbol": "SOL/USDT", "market": "usdtm_perp", "bucket_quote_long": "50.5",
         "bucket_quote_short": "", "leverage": "2", "allow_short": "false", "enabled": "true"}
    )

    assert problems == []
    assert block["bucket_quote_long"] == 50.5
    assert block["leverage"] == 2.0
    assert block["allow_short"] is False
    # ช่องที่เว้นว่างได้และถูกเว้น = `None` ไม่ใช่หายไปจาก dict
    assert block["bucket_quote_short"] is None


def test_a_missing_required_field_is_dropped_so_pydantic_reports_it_as_missing() -> None:
    """ช่องว่างของค่าที่บังคับต้องกลายเป็น "ขาด" ไม่ใช่ "ไม่ใช่ตัวเลข" (ใบ 21 §_coerce)"""
    block, problems = sym_routes._block(
        {"symbol": "", "market": "usdtm_perp", "bucket_quote_long": "",
         "leverage": "2", "allow_short": "false", "enabled": "true"}
    )

    assert problems == []
    assert "symbol" not in block and "bucket_quote_long" not in block


def test_editing_replaces_the_row_and_adding_appends_one() -> None:
    rows = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]

    edited, index = sym_routes._upserted(rows, {"symbol": "BTC/USDT", "leverage": 2.0}, original="BTC/USDT")
    assert index == 0
    assert [r["symbol"] for r in edited] == ["BTC/USDT", "ETH/USDT"]

    renamed, index = sym_routes._upserted(rows, {"symbol": "XRP/USDT"}, original="ETH/USDT")
    assert index == 1
    assert [r["symbol"] for r in renamed] == ["BTC/USDT", "XRP/USDT"]

    added, index = sym_routes._upserted(rows, {"symbol": "SOL/USDT"}, original="")
    assert index == 2
    assert [r["symbol"] for r in added] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_only_the_row_being_edited_gets_its_problems_placed_on_the_form() -> None:
    """ฟอร์มนี้มีช่องของเหรียญเดียว · ปัญหาของแถวอื่นไม่มีช่องให้แขวน"""
    mine = Problem(("symbols", 1, "leverage"), "เกิน max_leverage", "")
    someone_else = Problem(("symbols", 0, "leverage"), "เกิน max_leverage", "")
    plain = Problem(("market",), "ค่าที่ช่องนี้ไม่รับ", "")

    placed, unplaced = sym_routes._placed([mine, someone_else, plain], index=1)

    assert set(placed) == {"leverage", "market"}
    assert placed["leverage"] is mine
    assert unplaced == (someone_else,)


def test_no_modal_class_is_defined_twice_in_the_stylesheet() -> None:
    """ใบ 23 เคยนิยาม `.modal__text` ซ้ำเป็นช่อง input แล้วทับย่อหน้าคำอธิบายของทุก modal
    (detail ขึ้นเป็นกล่องขอบตัว mono) · กฎที่มาทีหลังชนะเงียบๆ จึงต้องมีเทสต์ ไม่ใช่ตาคนดู"""
    import re
    from collections import Counter

    from cane.api.templating import STATIC

    css = (STATIC / "console.css").read_text()
    selectors = Counter(re.findall(r"^(\.modal__[\w-]+) \{", css, flags=re.M))
    assert [s for s, n in selectors.items() if n > 1] == []
