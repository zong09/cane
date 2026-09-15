"""สถานะที่คอนโซลแสดง — คิดใหม่ทุกครั้งที่ถาม ไม่มีคอลัมน์ไหนเก็บมันไว้

## ทำไมไฟล์นี้ไม่รู้จัก `Connection`

spec/10 §2. สาม state ที่คนละเรื่องกัน แยก `engine.observed` ออกมาเป็น state ที่
"คิดใหม่ทุกครั้งที่ถาม" และไม่อยู่รอดข้ามอะไรเลย · ที่นี่จึงเป็นฟังก์ชันบริสุทธิ์ที่รับ
ตัวเลขเข้ามาแล้วคืนคำหนึ่งคำ — ทดสอบได้ครบทุกช่องโดยไม่ต้องมี Postgres และไม่ต้องรอเวลาจริง

`now` เป็นพารามิเตอร์บังคับ **ไม่มีค่าตั้งต้นเป็น `now_ms()`** เพราะเทสต์ที่ต้องพึ่ง
นาฬิกาของเครื่องคือเทสต์ที่จะแดงเองในวันที่เครื่องช้า

## `running` ไม่ใช่ค่าเดียว มันเป็นสองค่า

spec/10 §`engine.running` ไม่ใช่ค่าเดียว มันเป็นสองค่า อธิบายไว้ว่าเจตนาของคน
(`should_run`) กับสภาพจริงของ process แยกกันได้ และตอนที่มันแยกกันคือตอนที่สำคัญที่สุด:
engine ตายไปเมื่อสิบนาทีก่อนแต่หน้าจอยังเขียนว่า `running` คือการโกหกที่ทำให้ไม่มีใคร
ไปกดสตาร์ท · `derive_status()` จึงเขียนเป็นบันไดตามลำดับแถวของตารางในสเปกตรงตัว
ไม่ยุบเป็นนิพจน์เดียว เพื่อให้ตรวจทานเทียบทีละแถวกับต้นฉบับได้

## `blocked` มาจากคนละหัวข้อ และมาทีหลัง `crashed`

สี่แถวในตารางของ §2 ไม่มี `blocked` · มันอยู่ที่ spec/10 §4. รอบชีวิตของ engine ซึ่ง
บอกว่า config ที่ validate ไม่ผ่านทำให้ engine เข้าสถานะ `blocked` แล้ววนต่อ **ไม่ exit**

ลำดับจึงสำคัญ: `blocked` เป็นชั้นทับบน `running` เท่านั้น · engine ที่ติดบล็อกแล้ว
**ตาย** ต้องรายงาน `crashed` ไม่ใช่ `blocked` ที่ค้างอยู่จากตอนที่มันยังมีชีวิต —
ไม่งั้นคนอ่านหน้าจอจะไปแก้ config ให้กับ process ที่ไม่มีอยู่แล้ว
"""

from __future__ import annotations

from typing import Literal

from cane.db.repo.enginestate import EngineState

#: ทุกกี่วินาที engine เขียน heartbeat หนึ่งครั้ง
#:
#: เป็นค่าคงที่ในโค้ด **ไม่ใช่ฟิลด์ใน `Settings`** โดยเจตนา — `Settings` เป็น pydantic
#: `extra="forbid"` การเพิ่มฟิลด์เดียวลากไปถึงไฟล์ TOML ทั้งสอง ตาราง config migration
#: และ `repo/config.py` เพื่อค่าที่ไม่มีเหตุผลให้ต่างกันระหว่าง live กับ paper
#:
#: ถี่กว่ารอบการตัดสินใจมาก และนั่นคือประเด็น — spec/10 บอกว่า engine เขียน heartbeat
#: "ทุกครั้งที่วนรอบ" แต่รอบคือการรอแท่งปิด ซึ่งบนแท่ง 1D คือหนึ่งวัน · heartbeat ที่
#: เต้นวันละครั้งทำให้ "ตายไปแล้ว" กับ "กำลังรอแท่งถัดไป" แยกกันไม่ออกจนถึงพรุ่งนี้
HEARTBEAT_PERIOD_S = 5

#: heartbeat ที่เก่ากว่านี้ = process ไม่อยู่แล้ว (spec/10 §7. เกณฑ์ยืนยันความถูกต้อง ข้อ 4)
#:
#: **สองรอบ ไม่ใช่รอบเดียว** — GC pause หรือ query ที่ช้าผิดปกติครั้งเดียวไม่ควรทำให้
#: engine ที่ยังเดินอยู่ถูกประกาศว่าตาย เพราะคนที่เห็น `crashed` จะไปกดสตาร์ทซ้ำ
STALE_AFTER_MS = 2 * HEARTBEAT_PERIOD_S * 1000

#: ลำดับที่คอนโซลแสดงเสมอ · `live` ก่อนเพราะเป็นตัวที่คนต้องเห็นก่อนถ้าเห็นได้แค่ตัวเดียว
PROFILES: tuple[str, ...] = ("live", "paper")

STOPPED = "stopped"
RUNNING = "running"
CRASHED = "crashed"
STOPPING = "stopping"
BLOCKED = "blocked"

EngineStatus = Literal["stopped", "running", "crashed", "stopping", "blocked"]


def is_fresh(last_heartbeat_ts: int | None, *, now: int) -> bool:
    """heartbeat ยังสดอยู่ไหม · ไม่เคยเต้นเลย = ไม่สด ไม่ใช่กรณีพิเศษ"""
    if last_heartbeat_ts is None:
        return False
    return now - last_heartbeat_ts <= STALE_AFTER_MS


def derive_status(state: EngineState, *, now: int) -> EngineStatus:
    """สถานะที่แสดง จากเจตนา + heartbeat (spec/10 §2. สาม state ที่คนละเรื่องกัน)

    | `should_run` | heartbeat | ผล |
    | --- | --- | --- |
    | `false` | เก่า/ไม่มี | `stopped` |
    | `true` | สดกว่าสองรอบ | `running` (หรือ `blocked` ถ้ามีเหตุ — ดูหัวไฟล์) |
    | `true` | เก่ากว่านั้น | `crashed` |
    | `false` | สด | `stopping` |

    เขียนเรียงตามแถวของสเปกโดยเจตนา · `crashed` มาก่อนการตรวจ `blocked_reason` เพราะ
    process ที่ตายแล้วไม่ได้ติดบล็อก มันไม่อยู่
    """
    fresh = is_fresh(state.last_heartbeat_ts, now=now)

    if not state.should_run:
        return STOPPING if fresh else STOPPED
    if not fresh:
        return CRASHED
    if state.blocked_reason is not None:
        return BLOCKED
    return RUNNING
