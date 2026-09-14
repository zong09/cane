"""การอ้างสเปกในโค้ดต้องชี้ไปที่ของที่มีอยู่จริง — ด่านที่ไม่เคยมีมาก่อน

## ทำไมต้องมีไฟล์นี้

ทั้ง repo อ้างสเปกด้วยเลขบรรทัด (`spec/04:73`) ซึ่ง **ไม่มีอะไรตรวจเลย** · วัดเมื่อ
2026-09-14: 181 จุด และอย่างน้อย 30 จุดชี้ไปที่บรรทัดว่างหรือเลยท้ายไฟล์ · แก้สเปก
หนึ่งบรรทัดแล้วทุกเลขที่ชี้เลยจุดนั้นเพี้ยนพร้อมกันโดยไม่มีเทสต์ไหนดัง

รูปใหม่อ้าง **ข้อความหัวข้อ** แทน:

    spec/04 §ข้อบังคับเรื่องความคงเส้นคงวา

หัวข้อไม่ขยับตามการแก้บรรทัด และถ้ามีใครเปลี่ยนถ้อยคำหัวข้อ เทสต์นี้จะดังทันที
ซึ่งถูกต้อง — การอ้างถึงหัวข้อที่ไม่มีแล้วคือการอ้างถึงของที่หายไป

## หาจุดจบของชื่อหัวข้อด้วยการเทียบกับของจริง ไม่ใช่ regex

ชื่อหัวข้อเป็นภาษาไทยมีเว้นวรรค และบางอันมี backtick กับ `**` อยู่ข้างใน
(`## `base_pct` ต้องเป็นค่าคงที่`) · regex ที่พยายามเดาว่าชื่อจบตรงไหนจะพังกับ
อันพวกนั้น และการบังคับให้มีตัวปิดคร่อมก็ทำให้คนเขียนต้อง escape backtick ซ้อน

ตัวจับคู่จึงอ่านหัวข้อจริงจากไฟล์สเปกก่อน แล้ว **เทียบว่าข้อความหลัง `§` ขึ้นต้นด้วย
หัวข้อไหน เลือกอันที่ยาวที่สุด** · ไม่มีหัวข้อซ้ำกันภายในไฟล์เดียว (ตรวจไว้ข้างล่าง)
ชื่อหัวข้อจึงเป็น anchor ที่ไม่กำกวม

## `SPEC_FILES_CONVERTED` คือรายการงานที่เหลือ ไม่ใช่ค่าตั้งค่า

ไฟล์สเปกที่อยู่ในชุดนี้ **ห้ามมีการอ้างแบบเลขบรรทัดเหลืออยู่เลย** · ไฟล์ที่ยังไม่อยู่
คือไฟล์ที่ยังแปลงไม่เสร็จ · เอาชื่อเข้าชุดนี้ได้ก็ต่อเมื่อแปลงครบแล้วจริง ซึ่งแปลว่า
ชุดนี้โตขึ้นทางเดียวและโตจนครบเมื่องานจบ
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SPEC_DIR = REPO / "docs" / "spec"

#: ที่ที่อาจมีการอ้างสเปก · `docs/design_handoff/` ไม่อยู่ในนี้โดยเจตนา — เป็นไฟล์
#: ส่งมอบงานออกแบบ (HTML ที่ export มา) ไม่ใช่โค้ดหรือเอกสารที่เราดูแลถ้อยคำ
SEARCH_ROOTS = ("src", "tests", "alembic", "docs/adr", "docs/spec")

#: ไฟล์สเปกที่แปลงเป็นรูป `§หัวข้อ` ครบแล้ว — ดูหัวไฟล์ว่าทำไมชุดนี้โตทางเดียว
SPEC_FILES_CONVERTED: frozenset[str] = frozenset({"00", "10"})

#: รูปเก่า: `spec/04:73` หรือ `spec/04:69-77`
OLD_STYLE = re.compile(r"spec/(\d+[a-z]?):(\d+(?:-\d+)?)")

#: รูปใหม่: `spec/04 §` แล้วตามด้วยชื่อหัวข้อ (หาจุดจบด้วยการเทียบ ดูหัวไฟล์)
NEW_STYLE = re.compile(r"spec/(\d+[a-z]?) §")


def _spec_path(number: str) -> Path | None:
    matches = sorted(SPEC_DIR.glob(f"{number}-*.md"))
    return matches[0] if matches else None


def _headings(path: Path) -> list[str]:
    """ชื่อหัวข้อทั้งหมดของไฟล์หนึ่ง เรียงจากยาวไปสั้นเพื่อให้จับคู่ตัวยาวก่อน"""
    found = [
        match.group(1).strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^#{2,3} (.+)$", line))
    ]
    return sorted(found, key=len, reverse=True)


def _sources() -> list[Path]:
    out: list[Path] = []
    for root in SEARCH_ROOTS:
        for path in sorted((REPO / root).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".md"}:
                out.append(path)
    return out


def _citations(pattern: re.Pattern[str]):
    """(ไฟล์, บรรทัด, เลขสเปก, ข้อความที่เหลือบนบรรทัดนั้น) ของทุกจุดที่ตรงรูป"""
    for path in _sources():
        if path.name == Path(__file__).name:
            continue  # ไฟล์นี้เขียนรูปทั้งสองแบบไว้เป็นตัวอย่าง ไม่ใช่การอ้างจริง
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in pattern.finditer(line):
                yield path, number, match.group(1), line[match.end() :]


def test_no_spec_file_repeats_a_heading():
    """ชื่อหัวข้อต้องไม่ซ้ำภายในไฟล์เดียว ไม่งั้นมันเป็น anchor ไม่ได้

    ข้อนี้คือสมมติฐานที่ตัวจับคู่ยืนอยู่ · ถ้าวันหนึ่งมีใครตั้งหัวข้อซ้ำ การอ้างถึง
    ชื่อนั้นจะกำกวมทันที และต้องรู้ตัวที่นี่ ไม่ใช่ตอนที่คนเปิดตามแล้วเจอผิดที่
    """
    for path in sorted(SPEC_DIR.glob("*.md")):
        headings = _headings(path)
        duplicates = {h for h in headings if headings.count(h) > 1}
        assert not duplicates, f"{path.name} มีหัวข้อซ้ำ: {duplicates}"


def test_every_heading_citation_points_at_a_heading_that_exists():
    """`spec/NN §หัวข้อ` ทุกจุดต้องชี้ไปที่หัวข้อที่มีอยู่จริงในไฟล์นั้น"""
    problems: list[str] = []
    for path, line_no, number, rest in _citations(NEW_STYLE):
        where = f"{path.relative_to(REPO)}:{line_no}"
        spec = _spec_path(number)
        if spec is None:
            problems.append(f"{where} อ้าง spec/{number} ซึ่งไม่มีไฟล์นี้")
            continue
        headings = _headings(spec)
        if not any(rest.startswith(h) for h in headings):
            got = rest[:40].rstrip()
            problems.append(
                f"{where} อ้าง spec/{number} §{got}… ซึ่งไม่ตรงหัวข้อไหนใน "
                f"{spec.name} · มีอยู่: {sorted(headings)}"
            )
    assert not problems, "การอ้างหัวข้อที่ไม่มีอยู่จริง:\n" + "\n".join(problems)


@pytest.mark.parametrize("number", sorted(SPEC_FILES_CONVERTED))
def test_a_converted_spec_has_no_line_number_citations_left(number: str):
    """ไฟล์ที่แปลงแล้วห้ามมีการอ้างเลขบรรทัดหลงเหลือ — ด่านที่กันรูปเก่าไม่ให้กลับมา

    ตัวเทสต์นี้ต่างหากที่ทำให้การแปลงมีความหมาย · ถ้ามีแต่เทสต์ข้างบน คนเขียนโค้ด
    ใหม่ยังหย่อนเลขบรรทัดกลับเข้ามาได้เรื่อยๆ แล้วปัญหาเดิมจะโตกลับมาเงียบๆ
    """
    stale = [
        f"{path.relative_to(REPO)}:{line_no}"
        for path, line_no, cited, _ in _citations(OLD_STYLE)
        if cited == number
    ]
    assert not stale, (
        f"spec/{number} แปลงเป็น §หัวข้อ แล้ว แต่ยังมีการอ้างเลขบรรทัดที่:\n  "
        + "\n  ".join(stale)
    )
