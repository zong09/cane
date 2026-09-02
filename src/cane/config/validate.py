"""โหลด config profile แบบ fail-closed พร้อมเลขบรรทัดของทุกข้อผิดพลาด

เลขบรรทัดไม่ใช่ของแถม — คอนโซลเอาไปแสดงเป็น badge `L4 / L19` ข้างรายการที่ต้องแก้
`tomllib` ไม่คืนตำแหน่งของคีย์ให้ และ pydantic รู้แค่ path ของ field
โมดูลนี้จึงสแกนตัวอักษรดิบของไฟล์เองเพื่อทำแผนที่ path → บรรทัด แล้วเอามาต่อกับ
error ของ pydantic

กติกาการชี้บรรทัด (ยึดตาม design handoff):

- ค่าผิด / คีย์ที่ไม่รู้จัก → บรรทัดของคีย์นั้นเอง
- คีย์ที่หายไป          → บรรทัดถัดจากคีย์สุดท้ายของตารางนั้น (จุดที่ควรแทรก)
- กฎข้ามคีย์            → บรรทัดของคีย์ที่จุดชนวนกฎ ไม่ใช่คีย์ที่หาย
"""

from __future__ import annotations

import difflib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from cane.config.settings import Loc, Settings, cross_checks, model_at

# บรรทัดที่เป็นการกำหนดค่า: `key = ...` หรือ `"quoted key" = ...`
_KEY_LINE = re.compile(r'^\s*(?P<key>[A-Za-z0-9_-]+|"[^"]*")\s*=')
# หัวตาราง `[a.b]` หรือหัวสมาชิก array `[[a]]`
_TABLE_LINE = re.compile(r"^\s*(?P<brackets>\[\[?)\s*(?P<path>[^\]]+?)\s*\]\]?\s*$")


@dataclass(frozen=True)
class Problem:
    """ข้อผิดพลาดหนึ่งข้อ พร้อมบรรทัดที่คนต้องไปแก้"""

    line: int
    message: str
    detail: str = ""

    def __str__(self) -> str:
        tail = f" — {self.detail}" if self.detail else ""
        return f"L{self.line} {self.message}{tail}"


class ConfigError(Exception):
    """โหลด config ไม่ผ่าน — ระบบต้องไม่เทรดต่อ"""

    def __init__(self, path: Path, problems: list[Problem]) -> None:
        self.path = path
        self.problems = sorted(problems, key=lambda p: (p.line, p.message))
        body = "\n".join(f"  {p}" for p in self.problems)
        super().__init__(f"{path} โหลดไม่ผ่าน {len(self.problems)} ข้อ:\n{body}")


class LineIndex:
    """แผนที่จาก path ของคีย์ → เลขบรรทัด สร้างจากตัวอักษรดิบของไฟล์

    ตั้งใจสแกนบรรทัดต่อบรรทัด ไม่ได้ parse TOML เต็มรูปแบบ — schema ของโปรเจกต์นี้
    ไม่มีค่าที่กินหลายบรรทัด (ไม่มี array ยาว ไม่มี multi-line string) และไฟล์ที่
    รูปแบบเพี้ยนจะถูก `tomllib` ปฏิเสธไปก่อนที่แผนที่นี้จะถูกใช้
    """

    def __init__(self, text: str) -> None:
        self._key_line: dict[Loc, int] = {}
        self._table_header: dict[Loc, int] = {}
        self._table_last_key: dict[Loc, int] = {}
        self._total_lines = 0
        self._scan(text)

    def _scan(self, text: str) -> None:
        table: Loc = ()
        array_seen: dict[Loc, int] = {}

        for lineno, raw in enumerate(text.splitlines(), start=1):
            self._total_lines = lineno
            line = raw.split("#", 1)[0]
            if not line.strip():
                continue

            header = _TABLE_LINE.match(line)
            if header:
                path: Loc = tuple(
                    part.strip().strip('"') for part in header["path"].split(".")
                )
                if header["brackets"] == "[[":
                    idx = array_seen.get(path, 0)
                    array_seen[path] = idx + 1
                    path = (*path, idx)
                table = path
                self._table_header[table] = lineno
                continue

            key = _KEY_LINE.match(line)
            if key:
                name = key["key"].strip('"')
                self._key_line[(*table, name)] = lineno
                self._table_last_key[table] = lineno

    def line_of(self, path: Loc) -> int | None:
        return self._key_line.get(path)

    def insert_line_for(self, table: Loc) -> int:
        """บรรทัดที่คีย์ซึ่งหายไปของตารางนี้ควรไปอยู่"""
        anchor = self._table_last_key.get(table) or self._table_header.get(table)
        if anchor is not None:
            return anchor + 1
        return self._total_lines + 1

    def anchor(self, loc: Loc) -> int:
        """เลขบรรทัดที่ควรชี้สำหรับ path หนึ่ง มีคีย์อยู่จริงก็ชี้ที่คีย์ ไม่มีก็ชี้จุดแทรก"""
        return self.line_of(loc) or self.insert_line_for(loc[:-1])


def _describe(err: dict, index: LineIndex) -> Problem:
    loc: Loc = tuple(err["loc"])
    kind = err["type"]
    name = str(loc[-1]) if loc else "(root)"
    parent = loc[:-1]

    if kind == "extra_forbidden":
        return Problem(index.anchor(loc), f"คีย์ที่ไม่รู้จัก {name}", _did_you_mean(name, parent))

    if kind == "missing":
        detail = "risk limit ไม่มีค่าตั้งต้นให้" if parent == ("risk",) else "ไม่มีค่าตั้งต้นให้"
        return Problem(index.insert_line_for(parent), f"ขาด {name}", detail)

    if loc == ("base_pct",) and kind in {"greater_than_equal", "less_than_equal"}:
        return Problem(
            index.anchor(loc),
            f"base_pct = {err['input']} อยู่นอกช่วง 5–20",
            "ตรวจตอนโหลด ไม่ใช่ตอนคำนวณ",
        )

    if kind == "literal_error":
        allowed = str(err.get("ctx", {}).get("expected", ""))
        return Problem(index.anchor(loc), f"{name} = {err['input']!r} ไม่ใช่ค่าที่รองรับ", f"ต้องเป็น {allowed}")

    if kind == "too_short" and loc == ("symbols",):
        return Problem(index.anchor(loc), "ไม่มี symbol ให้เทรดเลย", "ต้องมี [[symbols]] อย่างน้อยหนึ่งบล็อก")

    return Problem(index.anchor(loc), f"{'.'.join(str(p) for p in loc)}: {err['msg']}")


def _did_you_mean(name: str, parent: Loc) -> str:
    model = model_at(parent)
    if model is None:
        return "ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ"
    near = difflib.get_close_matches(name, list(model.model_fields), n=1, cutoff=0.7)
    if near:
        return f"ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ — หมายถึง {near[0]} หรือเปล่า"
    return "ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ"


def _toml_error_line(exc: tomllib.TOMLDecodeError) -> int:
    lineno = getattr(exc, "lineno", None)
    if isinstance(lineno, int):
        return lineno
    found = re.search(r"at line (\d+)", str(exc))
    return int(found[1]) if found else 1


def load_profile(path: str | Path) -> Settings:
    """อ่าน config profile หนึ่งไฟล์ ผ่านครบทุกกฎเท่านั้นจึงคืนค่า

    ผิดข้อใดข้อหนึ่ง → `ConfigError` ที่รวมทุกข้อไว้พร้อมเลขบรรทัด
    ไม่มีโหมดเตือนแล้วไปต่อ
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    index = LineIndex(text)

    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(path, [Problem(_toml_error_line(exc), f"ไฟล์ TOML ผิดรูป: {exc}")]) from exc

    # ตรวจทั้งสองชั้นก่อนแล้วค่อยโยน เพื่อให้คนแก้ไฟล์เห็นทุกข้อในรอบเดียว
    problems = [
        Problem(index.anchor(loc), message, detail)
        for loc, message, detail in cross_checks(raw)
    ]

    settings: Settings | None = None
    try:
        settings = Settings.model_validate(raw)
    except ValidationError as exc:
        problems.extend(_describe(err, index) for err in exc.errors())

    if problems:
        raise ConfigError(path, problems)

    assert settings is not None
    return settings
