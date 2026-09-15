"""ตัวสตาร์ท/หยุด/ดูสถานะ engine ทั้งสอง profile — อยู่ในกระบวนการของคอนโซล

## subprocess ไม่ใช่ thread

spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile ให้เหตุผลทั้งหมดไว้: **engine พังต้องไม่ลาก
คอนโซลตาย** เพราะคอนโซลคือที่เดียวที่คนกด kill switch ได้ ถ้ามันตายไปพร้อม engine
เครื่องมือหยุดฉุกเฉินก็หายไปตอนที่ต้องใช้มันที่สุด

## เขียน DB กับ spawn process แยกกันเป็นสองเมท็อด ไม่ใช่เมท็อดเดียว

`start()` เขียนเจตนาลงทรานแซกชันของผู้เรียก · `launch()` ถึงจะ spawn · ต้องเรียก
**หลัง commit** เท่านั้น และนี่ไม่ใช่ความเรียบร้อยทางสไตล์: process ลูกต่อ DB ด้วย
connection ของตัวเอง มันมองไม่เห็นทรานแซกชันที่ยังไม่ commit ของแม่ · spawn ข้างใน
ทรานแซกชันจึงได้ลูกที่อ่าน `should_run = false` แล้วออกทันที แล้วคอนโซลจะเห็น process
ที่ "สตาร์ทแล้วตายเลย" โดยไม่มี error ที่ไหนเลย

    with db.begin() as conn:
        view = sup.start(conn, "paper")   # เขียนเจตนา ยังไม่ spawn
    sup.launch(view)                      # นอกทรานแซกชัน · fork() ถอนคืนไม่ได้

`stop()` กับ `signal_stop()` แยกด้วยเหตุผลเดียวกัน แม้ผลของการ spawn ผิดจังหวะจะเบากว่า

## สถานะมาจาก heartbeat เสมอ ไม่ใช่จาก handle ที่ถืออยู่

สองกับดักที่เป็นภาพสะท้อนของกันและกัน:

- **คอนโซลรีสตาร์ท** — handle หายหมดทั้งที่ engine ทั้งสองยังเต้นอยู่สบายดี · ถ้าตอบว่า
  `stopped` จาก `self._handles` คนจะกดสตาร์ท แล้วได้ engine ตัวที่สองยิงออเดอร์ซ้อน
- **engine ค้าง** — `Popen` ยังมีชีวิตแต่ลูปไม่เดินแล้ว · ถ้าตอบว่า `running` จาก handle
  ก็คือการโกหกแบบเดียวกับที่ spec/10 §`engine.running` ไม่ใช่ค่าเดียว มันเป็นสองค่า เขียนไว้

`handle` จึงมีหน้าที่เดียวคือ **ส่ง SIGTERM** ไม่ใช่ตอบว่ายังเดินอยู่ไหม

## ไม่มี `restart()` และไม่มี `reconcile()`

spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile ใช้สามประโยคบอกว่า engine ที่พัง
**จะไม่ถูกสตาร์ทใหม่อัตโนมัติ** เพราะสิ่งที่ทำให้มันตายรอบแรกยังอยู่ที่เดิม การปลุกคืนอัตโนมัติจึงเปลี่ยนความล้มเหลวที่มองเห็นให้
เป็นลูปที่ไม่มีใครเห็น · เมท็อดที่หายไปคือตัวบังคับข้อนั้น — `status()` ไม่มีทาง spawn
อะไรได้เลยเพราะมันไม่มีช่องทางไปถึง `launch()`

## ที่นี่ไม่รู้จัก kill switch

`engine.should_run` กับ `kill_switch.latched` เป็นคนละ state คนละ lifecycle และ
**ไม่มีทางที่ตัวหนึ่งไปเปลี่ยนอีกตัว** (spec/10 §`engine.should_run` ≠ `kill_switch.latched`) ·
trigger ของ migration 0008 กันได้แค่ทางเดียว (engine ปลดไม่ได้) แต่คอนโซลปลดได้ตามสิทธิ์
และคอนโซลคือที่ที่ไฟล์นี้อยู่ · ด่านเดียวที่เหลือจึงคือการไม่มี import นั้นอยู่เลย
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Connection

from cane.db.repo import enginestate
from cane.db.repo.enginestate import EngineState
from cane.db.types import now_ms
from cane.engine.state import (
    BLOCKED,
    PROFILES,
    RUNNING,
    EngineStatus,
    derive_status,
)


class Process(Protocol):
    """สามอย่างที่ supervisor ต้องการจาก process หนึ่งตัว — ไม่ใช่ทั้ง `Popen`

    **ไม่มี `kill()` อยู่ในนี้โดยเจตนา** · `SIGKILL` ตัดกลางการส่งออเดอร์ ซึ่งขัดกับ
    spec/10 §4. รอบชีวิตของ engine ที่บังคับให้จบรอบปัจจุบันก่อน · โปรโตคอลที่ไม่มีเมท็อดนั้น
    ทำให้เขียนโค้ดที่เรียกมันไม่ได้ตั้งแต่แรก
    """

    @property
    def pid(self) -> int: ...

    def terminate(self) -> None: ...


Spawn = Callable[[str], Process]


def spawn_subprocess(profile: str) -> Process:
    """`python -m cane.cli engine run --profile X` เป็น process ลูก

    ใช้ `sys.executable -m` ไม่ใช่ชื่อ `cane` เพราะ console script อาจไม่อยู่บน `PATH`
    ของกระบวนการที่เรียก (เช่นคอนโซลที่ถูกสตาร์ทโดย systemd) แต่ interpreter ตัวที่
    กำลังรันอยู่หาเจอเสมอ และเป็นตัวเดียวกับที่มี dependency ครบ

    ลูกรับ `CANE_DB_DSN` ทาง environment ที่สืบทอดมา — ไม่ส่งผ่าน argv เพราะ DSN
    มีรหัสผ่านอยู่ข้างใน และ argv ของ process อ่านได้จากทั้งเครื่อง
    """
    return subprocess.Popen(  # noqa: S603 - argv คงที่ ไม่มีอะไรมาจากผู้ใช้
        [sys.executable, "-m", "cane.cli", "engine", "run", "--profile", profile]
    )


@dataclass(frozen=True, slots=True)
class EngineView:
    """สิ่งที่คอนโซลแสดงต่อหนึ่ง profile · `pid` มาจากหน่วยความจำ ไม่ได้มาจากตาราง"""

    profile: str
    should_run: bool
    last_heartbeat_ts: int | None
    blocked_reason: str | None
    status: EngineStatus
    pid: int | None


class Supervisor:
    """หนึ่งตัวต่อหนึ่งกระบวนการคอนโซล · ถือ handle ของลูกไว้ในหน่วยความจำเท่านั้น

    ไม่เก็บ pid ลงตารางเพราะ spec/10 §5. state ที่อยู่ในตาราง ไม่มีคอลัมน์นั้น และ
    `engine.observed` ถูกระบุว่าคิดใหม่ทุกครั้งที่ถาม · pid ที่ยังอยู่ไม่ได้แปลว่าลูป
    ยังเดิน heartbeat ต่างหากที่แปล
    """

    __slots__ = ("_handles", "_spawn")

    def __init__(self, *, spawn: Spawn = spawn_subprocess) -> None:
        self._spawn = spawn
        self._handles: dict[str, Process] = {}

    # ── ฝั่ง DB · ผู้เรียกเป็นเจ้าของทรานแซกชัน ────────────────────────────────

    def status(
        self, conn: Connection, *, now: int | None = None
    ) -> tuple[EngineView, ...]:
        """ทั้งสอง profile จาก **query เดียว** (spec/10 §6. สัญญาของ API)

        profile ที่ยังไม่มีแถวก็อยู่ในผลด้วย เป็น `stopped` — การ์ด PROFILE แสดงสถานะ
        ของอีกโหมดเสมอ ถ้าโหมดที่ยังไม่เคยเดินหายไปจากคำตอบ หน้าจอจะว่างแทนที่จะบอกว่า
        มันยังไม่เคยเดิน ซึ่งเป็นคำตอบคนละอันกัน
        """
        at = now_ms() if now is None else now
        rows = enginestate.read_all(conn)
        return tuple(
            self._view(rows.get(profile) or EngineState(profile, should_run=False), at)
            for profile in PROFILES
        )

    def start(
        self, conn: Connection, profile: str, *, now: int | None = None
    ) -> EngineView:
        """ตั้งเจตนาเป็น "รัน" แล้วคืนภาพ **ก่อน** spawn — ดูหัวไฟล์ว่าทำไมแยก

        สถานะใน view ที่คืนคือสถานะ *ก่อน* คำสั่งนี้ ซึ่งคือสิ่งที่ `launch()` ต้องใช้
        ตัดสินว่ามี process เดิมอยู่แล้วหรือยัง
        """
        at = now_ms() if now is None else now
        before = enginestate.read(conn, profile)
        enginestate.set_should_run(conn, profile, should_run=True)
        return self._view(before, at)

    def stop(
        self, conn: Connection, profile: str, *, now: int | None = None
    ) -> EngineView:
        """ตั้งเจตนาเป็น "ไม่รัน" — **นี่คือกลไกหลัก** SIGTERM เป็นแค่ตัวเร่ง

        เจตนาอยู่ในตาราง จึงทนการรีสตาร์ทคอนโซลที่ทำให้ handle หาย · engine อ่านมัน
        ที่ต้นรอบถัดไปแล้วจบเอง ต่อให้ไม่มีใครส่งสัญญาณอะไรให้เลย
        """
        at = now_ms() if now is None else now
        enginestate.set_should_run(conn, profile, should_run=False)
        return self._view(enginestate.read(conn, profile), at)

    # ── ฝั่ง process · เรียกหลัง commit เท่านั้น ───────────────────────────────

    def launch(self, view: EngineView) -> int | None:
        """spawn ถ้ายังไม่มีตัวที่เต้นอยู่ · คืน pid หรือ `None` เมื่อไม่ได้ทำอะไร

        สตาร์ทซ้ำตอนที่ heartbeat ยังสดคือ **no-op ไม่ใช่ error** (spec/10 §6. สัญญาของ API) ·
        ตัวตัดสินคือความสดของ heartbeat ไม่ใช่การมี handle อยู่ในมือ — คอนโซลที่เพิ่ง
        รีสตาร์ทไม่มี handle แต่ engine ยังเดินอยู่ ถ้าตัดสินจาก handle จะได้ตัวที่สอง
        """
        if view.status in (RUNNING, BLOCKED):
            return None
        process = self._spawn(view.profile)
        self._handles[view.profile] = process
        return process.pid

    def signal_stop(self, profile: str) -> bool:
        """สะกิดให้จบรอบเร็วขึ้นด้วย SIGTERM · คืน `False` เมื่อไม่มี handle แล้ว

        ไม่มี handle **ไม่ใช่ความผิดพลาด** — คอนโซลรีสตาร์ทไปแล้วเป็นเรื่องปกติ และ
        `should_run` ที่ `stop()` เขียนไว้ทำงานแทนอยู่แล้ว · ที่ SIGTERM ซื้อมาคือ
        เวลา: บนแท่ง 1D การรอให้ลูปวนมาถึงต้นรอบถัดไปเองคือรอถึงพรุ่งนี้
        """
        process = self._handles.pop(profile, None)
        if process is None:
            return False
        process.terminate()
        return True

    def forget(self, profile: str) -> None:
        """ทิ้ง handle โดยไม่ส่งสัญญาณ — สำหรับตอนที่รู้แล้วว่า process ไม่อยู่"""
        self._handles.pop(profile, None)

    def _view(self, state: EngineState, now: int) -> EngineView:
        process = self._handles.get(state.profile)
        return EngineView(
            profile=state.profile,
            should_run=state.should_run,
            last_heartbeat_ts=state.last_heartbeat_ts,
            blocked_reason=state.blocked_reason,
            status=derive_status(state, now=now),
            pid=None if process is None else process.pid,
        )
