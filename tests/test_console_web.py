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
from cane.api.deps import MODE_COOKIE, get_sup
from cane.db.repo import config as config_repo
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
    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


class FakeDb:
    """`connect()` กับ `begin()` คืนอะไรก็ได้ที่เป็น context manager

    handler ไม่ได้ยิง SQL เองเลย มันส่ง `conn` ต่อให้ repo กับ supervisor ซึ่งถูก
    แทนไว้หมดแล้วในเทสต์ชุดนี้
    """

    def connect(self) -> FakeConn:
        return FakeConn()

    def begin(self) -> FakeConn:
        return FakeConn()


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


@pytest.fixture
def no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """ยังไม่มี config เวอร์ชันไหน active — เป็นสภาพของเครื่องที่เพิ่ง migrate เสร็จ"""
    monkeypatch.setattr(config_repo, "active_settings", lambda conn, profile: None)


def build(states: dict[str, str] | None = None) -> tuple[TestClient, RecordingSupervisor]:
    sup = RecordingSupervisor(states)
    app = create_app(db=FakeDb())
    app.dependency_overrides[get_sup] = lambda: sup
    return TestClient(app), sup


# ── เกณฑ์เสร็จข้อ 2 ของใบ · spec/10 §3. สลับโหมดไม่ใช่การควบคุม ────────────────


def test_switching_mode_never_touches_either_engine(no_config: None) -> None:
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


def test_switching_to_live_is_refused_until_step_up_exists(no_config: None) -> None:
    """ปิดไว้ก่อนดีกว่าเปิดไว้ก่อน — step-up TOTP เป็นของใบ 20 ยังไม่มีอะไรให้ตรวจ"""
    client, _ = build()
    with client:
        response = client.post("/api/session/mode", data={"mode": "live"})

    assert response.status_code == 403
    assert "ใบ 20" in response.text
    # ต้องเป็น partial ของกล่องเตือนใน modal ไม่ใช่ JSON `{"detail": ...}` ของ FastAPI
    # — htmx เป็นคนรับ ไม่ใช่โค้ดที่อ่าน JSON เป็น
    assert 'class="modal__warn"' in response.text


def test_switching_back_to_paper_takes_effect_immediately(no_config: None) -> None:
    client, _ = build()
    with client:
        client.cookies.set(MODE_COOKIE, "live")
        response = client.post("/api/session/mode", data={"mode": "paper"})

    assert response.status_code == 200
    assert response.cookies[MODE_COOKIE] == "paper"


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
    no_config: None, method: str, path: str
) -> None:
    """"โปรไฟล์ที่ไม่มีอยู่ไม่ใช่คำขอที่ผิดรูป" — คำขอรูปถูก มันแค่ชี้ไปที่ของที่ไม่มี"""
    client, _ = build()
    with client:
        response = getattr(client, method)(path, data={"mode": "staging"})

    assert response.status_code == 404


def test_starting_an_engine_twice_in_a_row_answers_twice_without_spawning_twice(
    no_config: None,
) -> None:
    """สตาร์ทซ้ำตอน heartbeat ยังสด = no-op คืน 200 ไม่ใช่ error"""
    client, sup = build()
    with client:
        first = client.post("/api/paper/engine/start")
        second = client.post("/api/paper/engine/start")

    assert (first.status_code, second.status_code) == (200, 200)
    assert sup.spawn_calls.calls == ["paper"]


def test_the_card_returned_after_start_shows_the_state_after_the_command(
    no_config: None,
) -> None:
    """`Supervisor.start()` คืนภาพ *ก่อน* คำสั่ง — route ต้องอ่านใหม่ก่อน render

    ถ้า route ลืมอ่านใหม่ หน้าจอจะขึ้น `stopped` ทันทีหลังคนกดสตาร์ท ซึ่งอ่านได้ว่า
    ปุ่มไม่ทำงาน แล้วคนจะกดซ้ำ
    """
    client, _ = build()
    with client:
        response = client.post("/api/paper/engine/start")

    assert "engine paper · running" in response.text
    assert "stop engine" in response.text


def test_stopping_an_engine_with_no_handle_is_not_an_error(no_config: None) -> None:
    """คอนโซลที่เพิ่งรีสตาร์ทไม่มี handle — `should_run` ในตารางทำงานแทนอยู่แล้ว"""
    client, sup = build({"live": RUNNING, "paper": RUNNING})
    with client:
        response = client.post("/api/paper/engine/stop")

    assert response.status_code == 200
    assert sup.commands == [("stop", "paper")]


# ── layout ───────────────────────────────────────────────────────────────────


def test_the_rail_renders_both_groups_and_every_menu_item(no_config: None) -> None:
    client, _ = build()
    with client:
        page = client.get("/overview").text

    for label in ("ภาพรวม", "คู่เหรียญ", "ความเสี่ยง", "บันทึก", "รายงาน", "ตั้งค่า"):
        assert label in page
    assert "MODE" in page and "ทั้งระบบ" in page and "ไม่ผูกกับโหมด" in page
    assert "ผู้ใช้" in page
    # user chip อ่านจาก current_user() ไม่ใช่จากข้อความที่ฝังในเทมเพลต
    assert "OWNER" in page and "NP" in page


@pytest.mark.parametrize("slug", ["overview", "symbols", "risk", "log", "report", "config", "users"])
def test_every_menu_item_opens_even_though_its_body_belongs_to_a_later_ticket(
    no_config: None, slug: str
) -> None:
    client, _ = build()
    with client:
        response = client.get(f"/{slug}")

    assert response.status_code == 200
    assert "ใบ 19 ทำแค่โครง" in response.text


def test_a_page_that_does_not_exist_is_not_found(no_config: None) -> None:
    client, _ = build()
    with client:
        assert client.get("/nowhere").status_code == 404


def test_the_engine_card_polls_so_the_first_heartbeat_can_clear_a_false_crash(
    no_config: None,
) -> None:
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


def test_the_status_endpoint_answers_for_both_profiles_in_one_request(
    no_config: None,
) -> None:
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
        after_start = client.post("/api/paper/engine/start").text
        after_stop = client.post("/api/paper/engine/stop").text

    assert "load delay:1200ms" in after_start
    assert "load delay:1200ms" in after_stop


def test_a_crashed_engine_still_shows_stop_until_the_supervisor_can_tell_it_apart(
    no_config: None,
) -> None:
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
