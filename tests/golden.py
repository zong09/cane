"""อ่านไฟล์ export จาก TradingView — ใช้ร่วมกันระหว่าง golden test สองชุด

ไฟล์เดียวกันถือทั้ง CDC Action Zone (`Fast EMA` / `Slow EMA` / `Buy Signal` /
`Sell Signal`) และ CDC ATR Trailing Stop (`Fast Trail` / `Slow Trail`) เพราะ
export ออกมาจากชาร์ตเดียวที่ใส่สองอินดิเคเตอร์ · โมดูลนี้จึงไม่ได้อยู่ใน
`test_action_zone.py` อีกต่อไป ทั้งที่โฟลเดอร์ fixture ยังชื่อ `action_zone/`

**ไม่ใช่ไฟล์เทสต์** (ชื่อไม่ขึ้นต้นด้วย `test_`) pytest จึงไม่เก็บไปรัน
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import pytest

from cane.data import Bar

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "action_zone"

DAY = 86_400_000

#: ช่วงหัวชุดข้อมูลที่ตัดทิ้งตอนเทียบโซน (`5 × slow`) — **ไม่ใช่** `MIN_CLOSED_BARS`
#: ดูหัวไฟล์ของ `action_zone.py` ว่าทำไมสองเลขนี้คนละเรื่องกัน
WARMUP_BARS = 130


class GoldenRow(NamedTuple):
    """หนึ่งแถวของไฟล์ export — `None` คือช่องว่างจริงในไฟล์ (`na` ของ Pine)"""

    bar: Bar
    fast_ema: float | None
    slow_ema: float | None
    fast_trail: float | None
    slow_trail: float | None
    buy: bool
    sell: bool


def load(path: Path) -> list[GoldenRow]:
    """อ่านหนึ่งไฟล์ — ดู `fixtures/action_zone/README.md` ว่าต้อง export อะไรมา

    ใช้ `csv.reader` ไม่ใช่ `DictReader` เพราะไฟล์จริง**มีชื่อคอลัมน์ซ้ำ**
    (`Buy/Sell Ribbon` โผล่สองครั้ง จาก `plot()` สองเส้นที่ตั้งชื่อเหมือนกัน) และ
    `DictReader` จะยุบคู่ที่ซ้ำให้เหลือตัวท้ายเงียบๆ · จับคู่ชื่อ→ดัชนีเองโดยเอา
    **ตัวแรก** ที่เจอ แล้วอ่านด้วยดัชนี ชื่อซ้ำจึงไม่ทำให้อ่านผิดคอลัมน์

    เวลาในไฟล์เป็น `YYYY-MM-DD` ของแท่ง **เปิด** ตามธรรมเนียมของ TradingView ·
    `Bar.close_ts` จึงเป็นเวลาเปิด + หนึ่งวัน ซึ่งคือสิ่งที่ `closed_as_of()` คืน

    `Buy Signal` / `Sell Signal` เป็น `0`/`1` **ไม่ใช่ช่องว่าง** ต่างจากเส้น EMA
    และเส้น Trail ที่ช่องว่างคือ `na` จริงๆ — `flag()` จึงยืนยันรูปแบบทุกแถวแทน
    ที่จะแปลงเงียบๆ

    คอลัมน์ที่จงใจไม่โหลด: `Buy` / `Sell` (ของสคริปต์ trailing stop คนละคู่กับ
    `Buy Signal` / `Sell Signal`) เป็น `0` ทั้งคอลัมน์ในไฟล์นี้ · `Buy more
    signals` / `Sell more signals` ว่างทั้งคอลัมน์ · `Buy/Sell Ribbon` เป็นแถบสี
    ไม่ใช่ค่าที่โค้ดเราคำนวณ
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))

    header, body = rows[0], rows[1:]
    at: dict[str, int] = {}
    for index, name in enumerate(header):
        at.setdefault(name, index)

    def number(row: list[str], name: str) -> float | None:
        text = row[at[name]].strip()
        return float(text) if text else None

    def flag(row: list[str], name: str) -> bool:
        text = row[at[name]].strip()
        assert text in {"0", "1"}, f"{name} มีค่า {text!r} ที่ไม่ใช่ 0/1"
        return text == "1"

    out: list[GoldenRow] = []
    for row in body:
        opened = datetime.strptime(row[at["time"]], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        open_ts = int(opened.timestamp() * 1000)
        out.append(
            GoldenRow(
                bar=Bar(
                    open_ts=open_ts,
                    close_ts=open_ts + DAY,
                    open=float(row[at["open"]]),
                    high=float(row[at["high"]]),
                    low=float(row[at["low"]]),
                    close=float(row[at["close"]]),
                    volume=float(row[at["Volume"]]),
                ),
                fast_ema=number(row, "Fast EMA"),
                slow_ema=number(row, "Slow EMA"),
                fast_trail=number(row, "Fast Trail"),
                slow_trail=number(row, "Slow Trail"),
                buy=flag(row, "Buy Signal"),
                sell=flag(row, "Sell Signal"),
            )
        )
    return out


def rows() -> list[GoldenRow]:
    """ไฟล์เดียวในโฟลเดอร์ · `skip` ถ้ายังไม่มี ซึ่งแปลว่าใบ 04 ยังปิดไม่ได้"""
    found = sorted(GOLDEN_DIR.glob("*.csv"))
    if not found:
        pytest.skip("ยังไม่มีไฟล์ export จาก TradingView — ใบ #04 ปิดไม่ได้")
    assert len(found) == 1, f"มีไฟล์ golden มากกว่าหนึ่ง: {[p.name for p in found]}"
    return load(found[0])
