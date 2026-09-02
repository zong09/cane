"""ตรวจ config แบบ fail-closed พร้อมชี้ **ฟิลด์** ที่ต้องแก้

เดิมไฟล์นี้ทำแผนที่ path → เลขบรรทัดจากตัวอักษรดิบของไฟล์ TOML เพื่อให้คอนโซล
แสดง badge `L4 / L19` ข้างรายการที่ต้องแก้ · ตอนที่ config ย้ายไปเป็นตารางใน DB
(`decisions.md` ข้อ 22) **ไม่มีไฟล์ ก็ไม่มีบรรทัดให้ชี้** สิ่งที่แทนได้และตรงกว่าคือ
**path ของฟิลด์** เพราะหน้าตั้งค่ากลายเป็นฟอร์มต่อฟิลด์ ไม่ใช่ตัวแก้ไฟล์ TOML —
`symbols[0].leverage` ชี้ได้ตรงกับช่องกรอกช่องนั้น ซึ่งเลขบรรทัดทำไม่ได้อยู่แล้ว

ยังต้องอ่าน TOML อยู่ เพราะ `cane db seed` เอาไฟล์เดิมเข้า DB ครั้งแรก แต่ TOML
เป็น **ทางเข้า** ไม่ใช่แหล่งความจริงอีกแล้ว

`validate_settings()` เป็นประตูเดียวที่ค่าจากภายนอกผ่าน ไม่ว่าจะมาจากไฟล์ seed
หรือจากฟอร์มคอนโซล — ฟอร์มก็คือข้อมูลจากภายนอกเหมือนกัน จึงต้องผ่านตัวเดียวกัน
"""

from __future__ import annotations

import difflib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cane.config.settings import Loc, Settings, cross_checks, model_at


def render_loc(loc: Loc) -> str:
    """`("symbols", 0, "leverage")` → `symbols[0].leverage`

    รูปนี้ตรงกับ path ที่คอนโซลใช้ตั้งชื่อ input ในฟอร์ม จึงชี้ช่องที่ต้องแก้ได้ตรงตัว
    """
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out


@dataclass(frozen=True)
class Problem:
    """ข้อผิดพลาดหนึ่งข้อ พร้อม path ของฟิลด์ที่คนต้องไปแก้

    `loc = ()` คือปัญหาที่ไม่ผูกกับฟิลด์ใดฟิลด์หนึ่ง เช่นไฟล์ TOML ผิดรูปทั้งไฟล์
    ซึ่งยังต้องรายงานได้ ไม่ใช่ยัดให้ฟิลด์ใดฟิลด์หนึ่งรับผิดไป
    """

    loc: Loc = field(default=())
    message: str = ""
    detail: str = ""

    @property
    def field_path(self) -> str:
        return render_loc(self.loc)

    def __str__(self) -> str:
        head = f"{self.field_path}: " if self.loc else ""
        tail = f" — {self.detail}" if self.detail else ""
        return f"{head}{self.message}{tail}"


class ConfigError(Exception):
    """config ไม่ผ่าน — ระบบต้องไม่เทรดต่อ และเวอร์ชันนี้ต้องไม่ถูกบันทึกลง DB

    `source` เป็นแค่คำอธิบายว่าค่ามาจากไหน (พาธไฟล์ seed หรือ `"console"`)
    ไม่ใช่สิ่งที่โค้ดเอาไปตัดสินใจต่อ — ตัวที่เอาไปใช้จริงคือ `problems`
    """

    def __init__(self, problems: list[Problem], *, source: str | None = None) -> None:
        self.source = source
        self.problems = sorted(problems, key=lambda p: (p.field_path, p.message))
        where = f"{source} " if source else ""
        body = "\n".join(f"  {p}" for p in self.problems)
        super().__init__(f"{where}ไม่ผ่าน {len(self.problems)} ข้อ:\n{body}")


def _describe(err: dict) -> Problem:
    loc: Loc = tuple(err["loc"])
    kind = err["type"]
    name = str(loc[-1]) if loc else "(root)"
    parent = loc[:-1]

    if kind == "extra_forbidden":
        return Problem(loc, f"คีย์ที่ไม่รู้จัก {name}", _did_you_mean(name, parent))

    if kind == "missing":
        detail = "risk limit ไม่มีค่าตั้งต้นให้" if parent == ("risk",) else "ไม่มีค่าตั้งต้นให้"
        return Problem(loc, f"ขาด {name}", detail)

    if loc == ("base_pct",) and kind in {"greater_than_equal", "less_than_equal"}:
        return Problem(
            loc,
            f"base_pct = {err['input']} อยู่นอกช่วง 5–20",
            "ตรวจตอนบันทึกเวอร์ชัน ไม่ใช่ตอนคำนวณ",
        )

    if kind == "literal_error":
        allowed = str(err.get("ctx", {}).get("expected", ""))
        return Problem(loc, f"{name} = {err['input']!r} ไม่ใช่ค่าที่รองรับ", f"ต้องเป็น {allowed}")

    if kind == "too_short" and loc == ("symbols",):
        return Problem(loc, "ไม่มี symbol ให้เทรดเลย", "ต้องมีอย่างน้อยหนึ่งคู่เหรียญ")

    if kind == "value_error":
        # pydantic เติม "Value error, " หน้าข้อความของ validator เอง — ตัดออกเพราะ
        # ข้อความที่คนอ่านคือของเรา ไม่ใช่ชื่อชนิดข้อผิดพลาดของไลบรารี
        return Problem(loc, f"{name} = {err['input']}: {err['msg'].removeprefix('Value error, ')}")

    return Problem(loc, err["msg"])


def _did_you_mean(name: str, parent: Loc) -> str:
    model = model_at(parent)
    if model is None:
        return "ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ"
    near = difflib.get_close_matches(name, list(model.model_fields), n=1, cutoff=0.7)
    if near:
        return f"ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ — หมายถึง {near[0]} หรือเปล่า"
    return "ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ"


def validate_settings(raw: Mapping[str, Any], *, source: str | None = None) -> Settings:
    """ตรวจค่าดิบหนึ่งชุด ผ่านครบทุกกฎเท่านั้นจึงคืน `Settings`

    ตรวจ **ทั้งสองชั้นก่อนแล้วค่อยโยน** (กฎข้ามคีย์ + schema) เพื่อให้คนแก้เห็นทุกข้อ
    ในรอบเดียว ไม่ใช่แก้ทีละข้อแล้วกดบันทึกใหม่ทุกครั้ง — ข้อบังคับเดิมของ spec/07
    ที่ยังใช้ได้เหมือนกันทั้งกับไฟล์และกับฟอร์ม

    กฎบางข้อที่นี่ **ซ้ำกับ CHECK ใน DB** โดยเจตนา (paper บังคับ dry_run, ช่วงของ
    base_pct, short ต้องมี bucket ของตัวเอง) — DB เป็นด่านสุดท้ายที่ทับไม่ได้
    ส่วนด่านนี้มีไว้เพื่อ **บอกให้ครบทุกข้อพร้อมกัน** ซึ่ง CHECK ทำไม่ได้เพราะมัน
    ล้มที่ข้อแรกที่เจอ · กฎที่ DB เขียนไม่ได้เลยคือ `leverage` ต่อเหรียญเทียบกับ
    `max_leverage` ของ profile เพราะเป็นกฎข้ามตาราง (อยู่ใน `cross_checks`)
    """
    problems = [
        Problem(loc, message, detail) for loc, message, detail in cross_checks(raw)
    ]

    settings: Settings | None = None
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        problems.extend(_describe(err) for err in exc.errors())

    if problems:
        raise ConfigError(problems, source=source)

    assert settings is not None
    return settings


def load_toml(path: str | Path) -> dict[str, Any]:
    """อ่านไฟล์ TOML หนึ่งไฟล์ · ผิดรูป = `ConfigError` ไม่ใช่ traceback ของ tomllib

    เก็บเลขบรรทัดที่ `tomllib` บอกไว้ในข้อความ เพราะไฟล์ที่ **ผิดไวยากรณ์** ยังเป็น
    ปัญหาของตัวไฟล์จริงๆ (ต่างจากค่าที่ผิดกฎ ซึ่งเป็นปัญหาของฟิลด์)
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        # ไฟล์ที่ไม่มีหรืออ่านไม่ได้เป็นความผิดพลาดของคนสั่ง ไม่ใช่บั๊ก — ต้องได้
        # ข้อความเดียวกับกรณีค่าผิดและรหัสออกเดียวกัน ไม่ใช่ traceback
        raise ConfigError(
            [Problem(message=f"อ่านไฟล์ไม่ได้: {exc.strerror or exc}")], source=str(path)
        ) from exc

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            [Problem(message=f"ไฟล์ TOML ผิดรูป: {exc}")], source=str(path)
        ) from exc


def load_profile(path: str | Path) -> Settings:
    """อ่าน + ตรวจไฟล์ profile หนึ่งไฟล์ — ทางเข้าของ `cane db seed`

    **ไม่ใช่เส้นทางที่ engine ใช้ตอนรันแล้ว** engine อ่าน config ที่ active จาก DB
    (`db.repo.config.active_settings`) ฟังก์ชันนี้เหลือหน้าที่เดียวคือพา TOML เข้า DB
    """
    return validate_settings(load_toml(path), source=str(path))
