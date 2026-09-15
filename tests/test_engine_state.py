"""สถานะของ engine กับ supervisor — ทุกข้อในไฟล์นี้พิสูจน์ได้โดยไม่ต้องมี Postgres

**ไฟล์นี้ไม่แตะฐานข้อมูลและไม่ spawn process** ต้องรันได้ใต้ `-m "not db"` เสมอ
(ธรรมเนียมเดียวกับ `test_action_zone.py`) · ทำได้เพราะสองอย่าง:

- `derive_status()` รับ `now` เป็นพารามิเตอร์ ไม่ไปอ่านนาฬิกาเอง — ความเก่าของ
  heartbeat จึงจำลองด้วยเลข ไม่ใช่ด้วยการ `sleep`
- `Supervisor` รับ `spawn` เข้ามา และ `loop.run()` รับ `sleep`/`now` เข้ามา ตาม
  ธรรมเนียมของรีโปที่ exchange client ถูก inject ทุกที่ ไม่เคยถูกสร้างข้างในโค้ดที่ทดสอบ

สิ่งที่ไฟล์นี้ตรวจไม่ได้และต้องมี process จริง อยู่ที่ `test_engine_process.py`:
"ฆ่า process แล้ว heartbeat หยุดจริงไหม" เป็นสมบัติของระบบปฏิบัติการ ไม่ใช่ของโค้ดเรา
"""

from __future__ import annotations

import pytest

from cane.db.repo.enginestate import EngineState
from cane.engine import loop
from cane.engine.state import (
    BLOCKED,
    CRASHED,
    HEARTBEAT_PERIOD_S,
    PROFILES,
    RUNNING,
    STALE_AFTER_MS,
    STOPPED,
    STOPPING,
    derive_status,
    is_fresh,
)
from cane.engine.supervisor import Supervisor

NOW = 1_789_000_000_000


def state(profile="paper", **overrides) -> EngineState:
    base = {"should_run": False, "last_heartbeat_ts": None, "blocked_reason": None}
    return EngineState(profile=profile, **(base | overrides))


# ── สถานะที่แสดง — สี่แถวของ spec/10 §2. สาม state ที่คนละเรื่องกัน ──────────────


def test_an_intent_to_run_with_a_fresh_heartbeat_reads_as_running():
    got = state(should_run=True, last_heartbeat_ts=NOW - 1000)

    assert derive_status(got, now=NOW) == RUNNING


def test_an_intent_to_run_with_a_heartbeat_older_than_two_periods_reads_as_crashed():
    """เกณฑ์ข้อ 4 · **นี่คือข้อที่ทั้งใบมีไว้เพื่อ**

    engine ตายไปเมื่อสิบนาทีก่อนแต่หน้าจอยังเขียนว่า `running` คือการโกหกที่ทำให้
    ไม่มีใครไปกดสตาร์ท (spec/10 §`engine.running` ไม่ใช่ค่าเดียว มันเป็นสองค่า)
    ถ้าข้อนี้พัง คอนโซลจะแสดง `running` ให้กับ process ที่ไม่มีอยู่
    """
    got = state(should_run=True, last_heartbeat_ts=NOW - STALE_AFTER_MS - 1)

    assert derive_status(got, now=NOW) == CRASHED


def test_a_heartbeat_exactly_two_periods_old_is_not_yet_crashed():
    """ขอบเขตอยู่ที่ "เก่ากว่าสองรอบ" ไม่ใช่ "สองรอบขึ้นไป"

    สองรอบเป๊ะคือกรณีที่เกิดจริงตลอดเวลาเมื่อ engine เต้นตรงเวลา · ถ้านับว่าตาย
    engine ที่สุขภาพดีจะกะพริบเป็น `crashed` เป็นระยะ แล้วคนจะเลิกเชื่อหน้าจอ
    """
    got = state(should_run=True, last_heartbeat_ts=NOW - STALE_AFTER_MS)

    assert derive_status(got, now=NOW) == RUNNING


def test_no_intent_and_no_heartbeat_at_all_reads_as_stopped():
    assert derive_status(state(), now=NOW) == STOPPED


def test_no_intent_with_a_fresh_heartbeat_reads_as_stopping_not_stopped():
    """สั่งหยุดแล้วแต่ยังปิดตัวไม่เสร็จ — คนละเรื่องกับหยุดแล้ว

    spec/10 §4. รอบชีวิตของ engine บอกว่าตอนหยุดต้องจบรอบที่กำลังทำอยู่ก่อน · ช่วงนั้น
    หน้าจอต้องบอกว่ากำลังปิด ไม่ใช่บอกว่าปิดแล้วทั้งที่ยังส่งออเดอร์อยู่
    """
    got = state(should_run=False, last_heartbeat_ts=NOW - 1000)

    assert derive_status(got, now=NOW) == STOPPING


def test_a_blocked_engine_that_is_still_beating_reads_as_blocked():
    """`blocked` มาจาก spec/10 §4. รอบชีวิตของ engine ไม่ใช่จากตารางสี่แถวของ §2"""
    got = state(should_run=True, last_heartbeat_ts=NOW - 1000, blocked_reason="ไม่มี config")

    assert derive_status(got, now=NOW) == BLOCKED


def test_a_blocked_engine_that_then_dies_reads_as_crashed_not_blocked():
    """ลำดับในบันไดสำคัญ — process ที่ตายแล้วไม่ได้ติดบล็อก มันไม่อยู่

    ถ้า `blocked` ชนะ `crashed` คนจะไปนั่งแก้ config ให้กับ process ที่ไม่มีอยู่แล้ว
    แล้วสงสัยว่าทำไมแก้แล้วไม่หาย
    """
    got = state(
        should_run=True,
        last_heartbeat_ts=NOW - STALE_AFTER_MS - 1,
        blocked_reason="ไม่มี config",
    )

    assert derive_status(got, now=NOW) == CRASHED


def test_a_heartbeat_that_never_happened_is_not_fresh_rather_than_an_error():
    assert not is_fresh(None, now=NOW)


# ── supervisor — สถานะมาจาก heartbeat ไม่ใช่จาก handle ────────────────────────


class FakeProcess:
    """`terminate()` อย่างเดียวพอ · ไม่มี `kill()` เหมือน Protocol ตัวจริง"""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


class FakeSpawn:
    """นับจำนวนครั้งที่ถูกเรียก — เทสต์ "ไม่สตาร์ทใหม่เอง" วัดที่ตัวเลขนี้"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.made: list[FakeProcess] = []

    def __call__(self, profile: str) -> FakeProcess:
        self.calls.append(profile)
        process = FakeProcess(pid=1000 + len(self.calls))
        self.made.append(process)
        return process


def view(profile="paper", status=STOPPED, pid=None):
    from cane.engine.supervisor import EngineView

    return EngineView(
        profile=profile,
        should_run=True,
        last_heartbeat_ts=None,
        blocked_reason=None,
        status=status,
        pid=pid,
    )


def test_launching_twice_while_the_heartbeat_is_fresh_spawns_only_once():
    """spec/10 §6. สัญญาของ API — สตาร์ทซ้ำตอน heartbeat ยังสดคือ no-op ไม่ใช่ error

    สองตัวพร้อมกันบน `live` แปลว่าออเดอร์ซ้อน · `clientOrderId` กันซ้ำในแท่งเดียวกัน
    ไว้อีกชั้น แต่ชั้นนี้คือชั้นที่ไม่ควรให้ไปถึงตรงนั้นตั้งแต่แรก
    """
    spawn = FakeSpawn()
    supervisor = Supervisor(spawn=spawn)

    assert supervisor.launch(view(status=STOPPED)) is not None
    assert supervisor.launch(view(status=RUNNING)) is None
    assert supervisor.launch(view(status=BLOCKED)) is None

    assert spawn.calls == ["paper"]


def test_a_crashed_engine_is_relaunched_only_because_someone_asked_not_by_itself():
    """เกณฑ์ข้อ 4 ครึ่งหลัง — `crashed` สตาร์ทใหม่ได้ **เมื่อมีคนสั่ง**

    spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile ห้ามการปลุกคืนอัตโนมัติ ไม่ได้ห้ามการกดเอง ·
    ตัวบังคับข้อนั้นคือ `status()` ที่ไม่มีทางไปถึง `launch()` ได้เลย ไม่ใช่ flag
    """
    spawn = FakeSpawn()
    supervisor = Supervisor(spawn=spawn)

    assert supervisor.launch(view(status=CRASHED)) is not None
    assert spawn.calls == ["paper"]


def test_signalling_a_profile_with_no_handle_left_is_not_an_error():
    """คอนโซลรีสตาร์ทแล้ว handle หาย — เรื่องปกติ ไม่ใช่ความผิดพลาด

    `should_run` ในตารางคือกลไกหลัก SIGTERM เป็นแค่ตัวเร่ง · ถ้าข้อนี้โยน exception
    การกด stop หลังคอนโซลรีสตาร์ทจะกลายเป็นหน้าจอ error ทั้งที่คำสั่งได้ผลแล้ว
    """
    supervisor = Supervisor(spawn=FakeSpawn())

    assert supervisor.signal_stop("paper") is False


def test_stopping_sends_sigterm_and_then_forgets_the_handle():
    spawn = FakeSpawn()
    supervisor = Supervisor(spawn=spawn)
    supervisor.launch(view(status=STOPPED))

    assert supervisor.signal_stop("paper") is True
    assert spawn.made[0].terminated
    assert supervisor.signal_stop("paper") is False, "ส่งซ้ำไม่ได้ เพราะ handle ถูกทิ้งแล้ว"


def test_the_process_protocol_has_no_way_to_send_sigkill():
    """`SIGKILL` ตัดกลางการส่งออเดอร์ ซึ่ง spec/10 §4. รอบชีวิตของ engine ห้ามไว้

    กันด้วยการที่ `Process` ไม่มีเมท็อดนั้น — เขียนโค้ดที่เรียกมันไม่ได้ตั้งแต่แรก
    แข็งกว่าคอมเมนต์เตือน · `SIGKILL` ที่ชอบธรรมมีที่เดียวคือการเก็บกวาดในเทสต์
    """
    from cane.engine.supervisor import Process

    assert "kill" not in Process.__protocol_attrs__
    assert {"pid", "terminate"} <= Process.__protocol_attrs__


# ── ไม่มีอะไรใน engine/ รู้จัก kill switch ───────────────────────────────────


def test_nothing_under_cane_engine_imports_the_kill_switch_repository():
    """spec/10 §`engine.should_run` ≠ `kill_switch.latched` — สอง state ที่ห้ามแตะกัน

    trigger ของ migration 0008 กันได้ทางเดียว: engine ปลดสวิตช์ไม่ได้ · แต่ supervisor
    อยู่ในกระบวนการของ **คอนโซล** ซึ่งมีสิทธิ์ปลด และ `latch()` จาก start/stop ก็เป็น
    การ*ลด*ความเสี่ยงที่ฐานไม่ปฏิเสธ · ด่านที่เหลือจึงคือการไม่มี import นั้นอยู่เลย

    ถ้าวันหนึ่งมีคนเขียน "stop แล้ว latch ด้วยเพื่อความปลอดภัย" ข้อนี้จะดังก่อนที่
    ประวัติการตัดสินใจช่วงฉุกเฉินจะหายไป
    """
    from pathlib import Path

    engine_dir = Path(__file__).resolve().parent.parent / "src" / "cane" / "engine"
    offenders = [
        path.name
        for path in sorted(engine_dir.glob("*.py"))
        if "killswitch" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, f"โมดูลใน engine/ ที่อ้างถึง kill switch: {offenders}"


# ── ลูป — ลำดับในรอบคือตัวออกแบบ ──────────────────────────────────────────────


class FakeConn:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeDb:
    """Engine ปลอมที่จดว่าถูกเรียกอะไรบ้าง ตามลำดับ"""

    def __init__(self, should_run: list[bool]) -> None:
        self.should_run = list(should_run)
        self.log: list[str] = []

    def begin(self):
        return FakeConn(self)


class Clock:
    """นาฬิกาที่เดินเพราะ `sleep` เท่านั้น — ไม่ใช่เพราะเวลาจริงผ่านไป

    จำเป็น ไม่ใช่ความสวยงาม: `_wait_beating()` จบเพราะนาฬิกาเดินถึงเส้นตาย ถ้า
    `sleep` ปลอมไม่ขยับนาฬิกา ลูปจะวนเต้นไม่จบ ซึ่งเป็นสิ่งที่เกิดขึ้นจริงตอนเขียน
    เทสต์ชุดนี้ครั้งแรก
    """

    def __init__(self) -> None:
        self.ms = 0
        self.naps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.ms += int(seconds * 1000)

    def now(self) -> int:
        return self.ms


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def patched(monkeypatch):
    """แทน repo ด้วยตัวจดบันทึก · ลูปไม่ควรรู้จัก SQL อยู่แล้ว"""
    db = FakeDb(should_run=[])

    def fake_read(conn, profile):
        db.log.append("read")
        value = db.should_run.pop(0) if db.should_run else False
        return EngineState(profile=profile, should_run=value)

    def fake_beat(conn, profile, *, blocked_reason=None):
        db.log.append("beat")

    monkeypatch.setattr(loop.enginestate, "read", fake_read)
    monkeypatch.setattr(loop.enginestate, "beat", fake_beat)
    return db


def test_the_loop_reads_the_intent_before_it_ever_writes_a_heartbeat(patched, clock):
    """engine ที่ไม่มีใครสั่งให้รันต้องออกโดยไม่เคยทำท่าว่ามีชีวิต

    ถ้าเต้นก่อนอ่าน คอนโซลจะเห็น `stopping` ของ process ที่ไม่มีใครเรียกมา — สถานะ
    ที่อธิบายไม่ได้ว่ามาจากไหน
    """
    patched.should_run = [False]

    assert loop.run("paper", db=patched, stopping=loop.StopFlag(), sleep=clock.sleep, now=clock.now) == 0
    assert patched.log == ["read"]


def test_a_stop_requested_mid_cycle_is_only_honoured_at_the_top_of_the_next_one(patched, clock):
    """spec/10 §4. รอบชีวิตของ engine — จบรอบที่กำลังทำอยู่ก่อน ไม่ตัดกลางการส่งออเดอร์"""
    patched.should_run = [True, True]
    stopping = loop.StopFlag()

    def sleep(seconds):
        clock.sleep(seconds)
        stopping.request_stop()  # สัญญาณมาถึงระหว่างรอ

    assert loop.run("paper", db=patched, stopping=stopping, sleep=sleep, now=clock.now) == 0
    assert patched.log == ["read", "beat"], "ต้องไม่มีรอบที่สอง และรอบแรกต้องจบครบ"


def test_the_loop_stops_when_the_intent_flips_even_though_no_signal_ever_arrived(patched, clock):
    """หยุดได้แม้ SIGTERM ส่งไม่ถึง — กรณีที่คอนโซลรีสตาร์ทแล้ว handle หาย"""
    patched.should_run = [True, False]

    assert loop.run("paper", db=patched, stopping=loop.StopFlag(), sleep=clock.sleep, now=clock.now) == 0
    assert patched.log == ["read", "beat", "beat", "read"], "รอบแรกเต้นสองครั้ง: ต้นรอบกับระหว่างรอ"


def test_a_stop_flag_set_before_the_first_read_skips_the_cycle_entirely(patched, clock):
    patched.should_run = [True]
    stopping = loop.StopFlag()
    stopping.request_stop()

    assert loop.run("paper", db=patched, stopping=stopping, sleep=clock.sleep, now=clock.now) == 0
    assert patched.log == []


def test_the_wait_is_sliced_so_the_heartbeat_never_goes_stale_while_waiting(patched, clock):
    """ใบ 12 จะส่งเวลาปิดแท่งจริงเข้ามา — บน 1D คือรอหนึ่งวัน

    การรอที่ไม่แบ่งซอยจะทำให้ heartbeat เก่าเกินสองรอบระหว่างที่ engine สุขภาพดี
    กำลังรออยู่ แล้วคอนโซลจะขึ้น `crashed` ทั้งที่ไม่มีอะไรพัง
    """
    loop._wait_beating(
        "paper",
        db=patched,
        until_ms=HEARTBEAT_PERIOD_S * 1000 * 3,
        stopping=loop.StopFlag(),
        sleep=clock.sleep,
        now=clock.now,
    )

    assert clock.naps == [HEARTBEAT_PERIOD_S] * 3
    assert patched.log == ["beat"] * 3, "หนึ่งการเต้นต่อหนึ่งช่วง ไม่ใช่ครั้งเดียวตอนจบ"


def test_the_two_profiles_are_listed_live_first_so_the_console_never_reorders_them():
    assert PROFILES == ("live", "paper")
