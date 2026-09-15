"""ตะเข็บของคอนโซล — จุดที่ใบ 20 (auth) จะมาแทนโดยไม่ต้องแตะเทมเพลตสักไฟล์

ทุกอย่างที่ยังไม่มีของจริงในใบ 19 ถูกมัดไว้เป็น **ฟังก์ชันเดียวต่อหนึ่งเรื่อง**
ไม่กระจายไปตาม handler เพราะของที่กระจายคือของที่ใบ 20 ต้องไล่เก็บทีละจุดแล้วลืมบางจุด

`get_db()` คืน `Engine` **ไม่ใช่ `Connection`** โดยเจตนา · dependency ที่ yield จาก
`engine.begin()` จะห่อ body ของ handler ไว้ในทรานแซกชันทั้งก้อน ซึ่งพา `launch()`
เข้าไปอยู่ข้างในทรานแซกชันด้วย — กับดักที่ `engine/supervisor.py` เขียนเตือนไว้ว่า
ได้ process ลูกที่อ่าน `should_run = false` แล้วออกทันทีโดยไม่มี error ที่ไหนเลย
handler จึงเป็นเจ้าของ `with db.begin()` ของตัวเองเสมอ
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, Response
from sqlalchemy import Engine

from cane.engine.state import PROFILES
from cane.engine.supervisor import Supervisor

#: โหมดที่ session กำลังดู · spec/10 §3. สลับโหมดไม่ใช่การควบคุม บอกว่าโหมดเป็นของ
#: session ไม่ใช่ของ engine · ใบ 19 ยังไม่มี auth เลย จึงเก็บใน cookie ธรรมดา
#: ไม่เซ็น — การเซ็นค่าที่ใครก็ตั้งได้อยู่แล้วไม่ได้ซื้ออะไร · ใบ 20 ย้ายไปอยู่บนแถว
#: `sessions` ตาม spec/09 §6. session ซึ่งบอกว่าต้องตรวจกับตารางทุก request
MODE_COOKIE = "cane_mode"

#: ค่าตั้งต้นคือ `paper` ไม่ใช่ `live` — คนที่เปิดคอนโซลมาโดยไม่เคยเลือกอะไร
#: ควรได้หน้าจอที่กดอะไรผิดแล้วไม่มีเงินจริงหาย
DEFAULT_MODE = "paper"


@dataclass(frozen=True, slots=True)
class ConsoleUser:
    """คนที่กำลังดูอยู่ · ใบ 20 จะแทนด้วยแถวจริงจากตาราง `users`"""

    initials: str
    name: str
    role: str


#: ค่าที่ไฟล์ design hardcode ไว้ · อยู่ตรงนี้จุดเดียว ไม่ใช่ในเทมเพลต เพื่อให้ใบ 20
#: เปลี่ยนที่มาของมันได้โดยที่ `partials/rail.html` ไม่ต้องแก้
STUB_USER = ConsoleUser(initials="NP", name="นภัส พ.", role="OWNER")


def get_db(request: Request) -> Engine:
    return request.app.state.db


def get_sup(request: Request) -> Supervisor:
    return request.app.state.sup


def current_user(request: Request) -> ConsoleUser:
    """ใบ 20 แทนด้วยการอ่าน session แล้ว join `users` · ใบ 19 คืนคนเดิมเสมอ"""
    return STUB_USER


def current_mode(request: Request) -> str:
    """ค่าที่อ่านไม่ออกถือเป็น `paper` ไม่ใช่ error — cookie มาจากฝั่งผู้ใช้"""
    mode = request.cookies.get(MODE_COOKIE)
    return mode if mode in PROFILES else DEFAULT_MODE


def set_mode(response: Response, mode: str) -> None:
    response.set_cookie(MODE_COOKIE, mode, httponly=True, samesite="strict")


def require_profile(profile: str) -> str:
    """profile ที่ไม่มีอยู่คือ **404 ไม่ใช่ 400** (spec/10 §6. สัญญาของ API)

    "โปรไฟล์ที่ไม่มีอยู่ไม่ใช่คำขอที่ผิดรูป" — คำขอรูปถูกทุกประการ มันแค่ชี้ไปที่
    ของที่ไม่มี
    """
    if profile not in PROFILES:
        raise HTTPException(status_code=404, detail=f"ไม่มี profile {profile!r}")
    return profile


def require_step_up() -> None:
    """step-up TOTP · ใบ 19 **ปฏิเสธเสมอ** เพราะยังไม่มีอะไรให้ตรวจ

    ปฏิเสธไว้ก่อนไม่ใช่ปล่อยผ่านไว้ก่อน · spec/09 §4. endpoint → สิทธิ์ที่ต้องมี
    ถือว่า endpoint ที่ยังไม่มีในตารางสิทธิ์เป็น 403 · ถ้าใบ 19 ปล่อยผ่าน แล้วใบ 20 ลืมจุดนี้
    ไปจุดหนึ่ง ผลคือทางเข้า `live` ที่ไม่มีใครกั้น ซึ่งเป็นความผิดพลาดที่มองไม่เห็น
    """
    raise HTTPException(status_code=403, detail="ต้องยืนยันด้วย TOTP ก่อน — ใบ 20")
