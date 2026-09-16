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

from cane.api.app import create_app
from cane.api.deps import get_sup, signed_in
from cane.auth import service as auth_service
from cane.config.validate import ConfigError, Problem
from cane.db.repo import config as config_repo
from cane.db.repo import decisions as decisions_repo
from cane.db.repo import permissions as perms
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


#: `config` ไม่อยู่ในรายการนี้แล้ว — ใบ 21 เติมเนื้อของมันไปแล้ว ที่เหลือยังเป็นโครง
@pytest.mark.parametrize("slug", ["overview", "symbols", "risk", "log", "report", "users"])
def test_every_menu_item_opens_even_though_its_body_belongs_to_a_later_ticket(
    slug: str
) -> None:
    client, _ = build()
    with client:
        response = client.get(f"/{slug}")

    assert response.status_code == 200
    assert "ใบ 19 ทำแค่โครง" in response.text


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
