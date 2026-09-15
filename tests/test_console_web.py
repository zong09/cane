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
from cane.db.repo import config as config_repo
from cane.db.repo import permissions as perms
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


@pytest.mark.parametrize("slug", ["overview", "symbols", "risk", "log", "report", "config", "users"])
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
