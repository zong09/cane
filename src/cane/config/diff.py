"""เทียบ config ของสองโปรไฟล์ทีละฟิลด์ — panel `ต่างจาก {อีกโหมด} N ค่า` (ใบ 21)

คิดใน Python ไม่ใช่ SQL join เพราะสองเวอร์ชันที่เอามาเทียบถูกประกอบกลับเป็น
`Settings` แล้วทั้งคู่ (`settings_of()` validate ตอนอ่าน) — การไป join แถวดิบอีกรอบ
แปลว่ามีสองที่ที่รู้ว่า config หน้าตาอย่างไร แล้วมันจะค่อยๆ ไม่ตรงกัน

**คนละแกนกับ `GET /api/config/diff` ของ spec/10 §6. สัญญาของ API** ซึ่งเทียบสอง
**เวอร์ชันของ profile เดียวกัน** · ที่นี่เทียบสอง profile ณ เวอร์ชันที่ active อยู่
"""

from __future__ import annotations

from dataclasses import dataclass

from cane.config.settings import Settings

#: ค่าที่ไม่มีอยู่ฝั่งหนึ่ง — คนละเรื่องกับ `None` ที่แปลว่า "มีฟิลด์นี้แต่เว้นว่าง"
MISSING = "ขาด"

#: `profile` ต่างกันเสมอโดยนิยาม การนับเป็นผลต่างทำให้ตัวเลขบนหัว panel โกหก
_SKIP = ("profile",)


@dataclass(frozen=True, slots=True)
class Row:
    """หนึ่งบรรทัดของตารางเทียบ · `mine`/`theirs` เป็นข้อความพร้อมแสดงแล้ว"""

    key: str
    mine: str
    theirs: str

    @property
    def mine_missing(self) -> bool:
        return self.mine == MISSING

    @property
    def theirs_missing(self) -> bool:
        return self.theirs == MISSING


def _leaf(value: object) -> str:
    return "" if value is None else str(value)


def flatten(settings: Settings) -> dict[str, str]:
    """`Settings` → `{path: ค่าเป็นข้อความ}` ที่เทียบกันตรงๆ ได้

    path ของค่าระดับ profile **ตรงกับชื่อ `name` ของช่องกรอกในฟอร์ม** (`base_pct`,
    `risk.max_leverage`) เพื่อให้คนที่เห็นผลต่างกดไปแก้ช่องนั้นได้ทันที

    ส่วนเหรียญใช้ `symbols[BTC/USDT@usdtm_perp].leverage` **ไม่ใช่เลขลำดับ** อย่างที่
    `render_loc()` ทำ — ลำดับในลิสต์ไม่มีความหมายข้าม profile · `BTC/USDT` ที่เป็น
    spot ใน paper กับที่เป็น perp ใน live เป็นคนละของกัน ถ้าจับคู่ด้วยเลขลำดับ
    (หรือด้วยชื่อเหรียญเฉยๆ) ตารางจะบอกว่า "leverage ต่างกัน" ทั้งที่ของจริงคือ
    คนละตลาด ซึ่งชี้ให้แก้ผิดที่
    """
    dump = settings.model_dump()
    out: dict[str, str] = {}

    for key, value in dump.items():
        if key in _SKIP or key in ("symbols", "risk", "broker", "data"):
            continue
        out[key] = _leaf(value)

    for section in ("risk", "broker", "data"):
        for key, value in dump[section].items():
            out[f"{section}.{key}"] = _leaf(value)

    for sym in dump["symbols"]:
        head = f"symbols[{sym['symbol']}@{sym['market']}]"
        for key, value in sym.items():
            if key in ("symbol", "market"):
                continue
            out[f"{head}.{key}"] = _leaf(value)

    return out


def diff(mine: Settings, theirs: Settings) -> list[Row]:
    """ฟิลด์ที่ไม่เท่ากันของสอง profile · เรียงตาม path

    ฝั่งที่ไม่มีฟิลด์นั้นเลยได้ `MISSING` ไม่ใช่ค่าว่าง — เหรียญที่มีอยู่โปรไฟล์เดียว
    ต้องอ่านออกว่า "ไม่มี" ไม่ใช่ "มีแล้วเว้นไว้"
    """
    left, right = flatten(mine), flatten(theirs)
    rows = [
        Row(key, left.get(key, MISSING), right.get(key, MISSING))
        for key in sorted(left | right)
    ]
    return [row for row in rows if row.mine != row.theirs]
