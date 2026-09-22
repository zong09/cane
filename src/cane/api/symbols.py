"""หน้า คู่เหรียญ — เพิ่ม แก้ และลบบล็อก `[[symbols]]` (ใบ 26)

ใบ 21 เว้นช่อง `symbols` ไว้ทั้งชุด (`api/config.py` เขียนเหตุผลไว้สามจุด) เพราะ
บล็อกนี้เป็น **ลิสต์** ไม่ใช่ค่าเดี่ยว — `patched()` เดินด้วย path แบบจุดจึงเอื้อมไม่ถึง
`symbols[0].leverage` · หน้านี้จึงประกอบลิสต์ใหม่ทั้งชุดเอง แล้วส่งเข้าทางเขียนเดิม
(`validate_settings()` → `insert_version()`) ไม่ใช่ทางเขียน config เส้นที่สอง

## บันทึกแล้วได้ **ร่าง** ไม่ใช่ของที่เปิดใช้ทันที

spec/10 §เขียน ให้สองแถวของสวิตช์ (`dry_run` · `allow_short`) เลื่อนตัวชี้ให้เอง และ
เขียนกำกับว่า "ต่างจากทุกแถวที่เหลือ" · แถว `POST /api/{profile}/symbols` ไม่มีประโยค
เลื่อนตัวชี้ เวอร์ชันที่ได้จึงเป็นร่างที่ยังไม่มีใครอ่าน จนกว่าจะมีคนไปกดเปิดใช้ที่หน้า
ตั้งค่า · ส่วน step-up ยังต้องมีเพราะ spec/09 §4. endpoint → สิทธิ์ที่ต้องมี เขียนว่า
**ต้อง** ที่แถวนี้ตรงๆ

## ลำดับด่าน: ทุกด่านที่ไม่ต้องใช้รหัสอยู่ก่อนด่านรหัส

เหตุผลเดียวกับใบ 23 — `verify_step_up()` กิน counter ของ TOTP ที่ใช้ร่วมกับ login
คนที่กรอก `leverage` เกินเพดานต้องได้รายการที่ต้องแก้กลับมาโดย **ไม่เสียรหัสไปหนึ่งรอบ**
ผลคือ `validate_settings()` ถูกเรียกก่อน `verify_step_up()` ที่นี่ ตรงข้ามกับใบ 23 ที่
ตรวจรหัสก่อน เพราะที่นั่นค่าที่รับมามีสองค่าและถูกกันไว้หมดแล้วตั้งแต่ก่อนถึงรหัส

## ชื่อช่องในฟอร์มเป็นชื่อเปล่า ไม่ใช่ path

`render_loc()` ผลิต `symbols[3].leverage` ซึ่งผูกกับ **ดัชนีในลิสต์** ที่ฟอร์มไม่ควรรู้
(เหรียญใหม่ยังไม่มีดัชนีจนกว่าจะรู้ว่าไปต่อท้ายหรือทับของเดิม) · ฟอร์มจึงส่ง `leverage`
เปล่าๆ แล้ว `_placed()` แปลง `("symbols", i, "leverage")` ของแถวที่กำลังแก้กลับเป็น
`leverage` · ปัญหาของเหรียญ **แถวอื่น** ไม่ถูกแปลง มันไปอยู่ในรายการ `unplaced`
เพราะฟอร์มนี้ไม่มีช่องให้แก้แถวนั้น
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection, Engine

from cane.api import config as config_routes
from cane.api.config import FormField
from cane.api.deps import client_ip, get_db, require_cap, require_profile
from cane.api.templating import templates
from cane.auth import service
from cane.config.settings import Loc, Settings
from cane.config.validate import ConfigError, Problem, validate_settings
from cane.db.repo import audit
from cane.db.repo import config as config_repo
from cane.db.repo import permissions as perms
from cane.db.repo.users import User
from cane.db.types import now_ms

router = APIRouter()

MARKETS = (("usdtm_perp", "usdtm_perp — มี leverage และ liquidation"), ("spot", "spot — long อย่างเดียว"))

#: `true`/`false` เป็น **select ไม่ใช่ checkbox** โดยเจตนา — checkbox ที่ไม่ถูกติ๊ก
#: ไม่ส่งชื่อช่องมาเลย ซึ่งแยกไม่ออกจาก "ฟอร์มนี้ไม่มีช่องนั้น" · กับ `allow_short`
#: ความต่างนั้นคือความต่างระหว่างเปิดฝั่ง short กับไม่เปิด
BOOLS = (("false", "false"), ("true", "true"))

#: ช่องของบล็อก `[[symbols]]` หนึ่งบล็อก → วิธีอ่านค่าจากสตริงที่ browser ส่งมา
#:
#: **ต้องแปลงเองก่อนส่งเข้า `validate_settings()`** ด้วยเหตุผลที่ `api/config.py`
#: เขียนไว้ที่ `EDITABLE`: `cross_checks()` ทำงานกับ dict ดิบและ `_is_number()` คืน
#: `False` ให้สตริง `"2.0"` · กฎ `leverage` ของเหรียญเทียบ `max_leverage` จะเงียบไป
#: ทั้งข้อ และนั่นเป็น **กฎเดียวที่ฐานเขียนเป็น CHECK ไม่ได้** (spec/07 §กฎการตรวจ config)
FIELDS: dict[str, str] = {
    "symbol": "text",
    "market": "text",
    "bucket_quote_long": "number",
    "bucket_quote_short": "number?",
    "leverage": "number",
    "allow_short": "bool",
    "enabled": "bool",
}


@dataclass(frozen=True, slots=True)
class SymbolRow:
    """หนึ่งบรรทัดในตาราง · ค่าเป็นสตริงพร้อมแสดง เทมเพลตไม่ต้องรู้จัก `None`"""

    symbol: str
    market: str
    bucket_long: str
    bucket_short: str
    leverage: str
    allow_short: bool
    enabled: bool


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _rows(settings: Settings | None) -> tuple[SymbolRow, ...]:
    if settings is None:
        return ()
    return tuple(
        SymbolRow(
            symbol=sym.symbol,
            market=sym.market,
            bucket_long=_text(sym.bucket_quote_long),
            bucket_short=_text(sym.bucket_quote_short) or "—",
            leverage=_text(sym.leverage),
            allow_short=sym.allow_short,
            enabled=sym.enabled,
        )
        for sym in settings.symbols
    )


def _blank() -> dict[str, str]:
    """ค่าตั้งต้นของฟอร์มเพิ่มเหรียญ · `market` ไม่มีค่าตั้งต้น (decisions #26)

    ที่เหลือเป็นช่องว่าง ไม่ใช่เลขที่เดาให้ — เงินของ bucket กับอัตราทดเป็นค่าที่
    คนต้องตั้งใจพิมพ์ทุกครั้ง
    """
    return {
        "symbol": "",
        "market": "usdtm_perp",
        "bucket_quote_long": "",
        "bucket_quote_short": "",
        "leverage": "",
        "allow_short": "false",
        "enabled": "true",
    }


def _values_of(settings: Settings | None, symbol: str) -> dict[str, str]:
    """ค่าของเหรียญที่มีอยู่แล้ว → ค่าตั้งต้นของฟอร์ม · ไม่เจอ = ฟอร์มเปล่า"""
    for sym in settings.symbols if settings else ():
        if sym.symbol == symbol:
            return {
                "symbol": sym.symbol,
                "market": sym.market,
                "bucket_quote_long": _text(sym.bucket_quote_long),
                "bucket_quote_short": _text(sym.bucket_quote_short),
                "leverage": _text(sym.leverage),
                "allow_short": str(sym.allow_short).lower(),
                "enabled": str(sym.enabled).lower(),
            }
    return _blank()


def _form(values: Mapping[str, str], errors: Mapping[str, Problem]) -> tuple[FormField, ...]:
    """ช่องกรอกของบล็อกเดียว · `path` เป็นชื่อเปล่า ดูเหตุผลที่หัวไฟล์"""
    fields = (
        FormField("symbol", "symbol", values["symbol"], hint="รูปเดียวกับที่ venue ใช้ เช่น BTC/USDT"),
        FormField("market", "market", values["market"], "select", MARKETS),
        FormField("bucket_quote_long", "bucket_quote_long", values["bucket_quote_long"], "number"),
        FormField(
            "bucket_quote_short",
            "bucket_quote_short",
            values["bucket_quote_short"],
            "number",
            hint="เว้นว่าง = เทรดฝั่ง long อย่างเดียว",
        ),
        FormField(
            "leverage",
            "leverage",
            values["leverage"],
            "number",
            hint="ห้ามเกิน max_leverage ของโปรไฟล์ · spot ต้องเป็น 1",
        ),
        FormField("allow_short", "allow_short", values["allow_short"], "select", BOOLS),
        FormField(
            "enabled",
            "enabled",
            values["enabled"],
            "select",
            BOOLS,
            hint="false = พักไว้ คงบล็อกไว้แต่ข้ามในรอบคำนวณ",
        ),
    )
    return tuple(replace(f, error=errors.get(f.path)) for f in fields)


def _block(form: Mapping[str, str]) -> tuple[dict, list[Problem]]:
    """ค่าที่ browser ส่งมา → บล็อก `[[symbols]]` หนึ่งบล็อกที่ validator อ่านรู้เรื่อง

    ใช้ `_coerce()` ตัวเดียวกับใบ 21 ไม่ได้เขียนกฎการแปลงใหม่ — กฎที่ว่า `bool`
    รับแค่ `"true"`/`"false"` ตรงตัวต้องมีที่เดียว ไม่งั้นทางเข้าที่สองจะยอมรับ
    ค่าที่ทางเข้าแรกปฏิเสธ
    """
    block: dict[str, object] = {}
    problems: list[Problem] = []
    for name, kind in FIELDS.items():
        raw = form.get(name, "").strip()
        try:
            value = config_routes._coerce(raw, kind)
        except ValueError:
            problems.append(
                Problem((name,), f"{name} = {raw!r} ไม่ใช่ค่าที่ช่องนี้รับ", "ค่าที่รับได้อยู่ในช่องเลือกของฟอร์ม")
            )
            continue
        if value is not config_routes._ABSENT:
            block[name] = value
    return block, problems


def _upserted(
    symbols: list[dict], block: dict, *, original: str
) -> tuple[list[dict], int]:
    """ลิสต์ใหม่ + ดัชนีของบล็อกที่เพิ่งแก้ · `original` ว่าง = เพิ่มเหรียญใหม่

    ชื่อเหรียญที่ถูกเปลี่ยนตอนแก้ต้อง **ทับแถวเดิม** ไม่ใช่เพิ่มแถวที่สอง — จึงหา
    ตำแหน่งจากชื่อเดิมที่ฟอร์มพามาด้วย ไม่ใช่จากชื่อที่เพิ่งพิมพ์
    """
    key = original or str(block.get("symbol", ""))
    for i, sym in enumerate(symbols):
        if sym.get("symbol") == key:
            return symbols[:i] + [block] + symbols[i + 1 :], i
    return symbols + [block], len(symbols)


def _placed(
    problems: list[Problem], *, index: int
) -> tuple[dict[str, Problem], tuple[Problem, ...]]:
    """ปัญหาของแถวที่กำลังแก้ → ชื่อช่องในฟอร์ม · ที่เหลือไปอยู่ในรายการล่าง"""
    placed: dict[str, Problem] = {}
    unplaced: list[Problem] = []
    for problem in problems:
        loc: Loc = problem.loc
        if len(loc) == 1 and str(loc[0]) in FIELDS:
            placed[str(loc[0])] = problem
        elif len(loc) == 3 and loc[0] == "symbols" and loc[1] == index:
            placed[str(loc[2])] = problem
        else:
            unplaced.append(problem)
    return placed, tuple(unplaced)


def page_context(
    conn: Connection,
    *,
    profile: str,
    user: User,
    values: Mapping[str, str] | None = None,
    original: str = "",
    errors: Mapping[str, Problem] | None = None,
    unplaced: tuple[Problem, ...] = (),
    notice: str = "",
    oob: bool = False,
) -> dict[str, object]:
    """ทุกอย่างที่ `partials/symbols_body.html` ต้องใช้ · คีย์ขึ้นต้นด้วย `sym_`

    prefix ไม่ใช่เรื่องความสวย — `context.build()` ใส่คีย์ `symbols` (รายชื่อในแถบ
    ข้าง) ไว้ใน context ของหน้าเต็มอยู่แล้ว ชื่อชนกันเมื่อไหร่ตารางจะอ่านของอีกที่
    """
    problems: tuple[Problem, ...] = ()
    try:
        settings = config_repo.active_settings(conn, profile)
    except ConfigError as exc:
        settings = None
        problems = tuple(exc.problems)

    form_values = dict(values) if values is not None else _blank()
    return {
        "sym_profile": profile,
        "sym_settings": settings,
        "sym_problems": len(problems),
        "sym_no_version": settings is None and not problems,
        "sym_rows": _rows(settings),
        "sym_form": _form(form_values, dict(errors or {})),
        "sym_original": original,
        "sym_unplaced": unplaced,
        "sym_notice": notice,
        "sym_oob": oob,
        "sym_can_edit": perms.allowed(conn, role=user.role, cap="edit_symbols"),
    }


def _body(
    request: Request,
    db: Engine,
    *,
    profile: str,
    user: User,
    values: Mapping[str, str] | None = None,
    original: str = "",
    errors: Mapping[str, Problem] | None = None,
    unplaced: tuple[Problem, ...] = (),
    notice: str = "",
    oob: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    with db.connect() as conn:
        ctx = page_context(
            conn,
            profile=profile,
            user=user,
            values=values,
            original=original,
            errors=errors,
            unplaced=unplaced,
            notice=notice,
            oob=oob,
        )
    return templates.TemplateResponse(
        request, "partials/symbols_body.html", ctx, status_code=status_code
    )


@router.get("/partials/symbols/{profile}", response_class=HTMLResponse)
def body(
    profile: str,
    request: Request,
    symbol: str = "",
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("view_overview")),
) -> HTMLResponse:
    """เนื้อของหน้า · `?symbol=` ลอกค่าของเหรียญนั้นลงฟอร์มเพื่อแก้

    ปุ่ม `แก้` ไม่มี endpoint ของตัวเองเพราะการแก้ไม่ใช่สถานะที่เซิร์ฟเวอร์จำ — มันคือ
    การขอเนื้อหน้าเดิมที่ฟอร์มมีค่าตั้งต้นต่างออกไป (แบบเดียวกับปุ่ม `ตรวจอีกครั้ง`
    ของใบ 21) · อยู่ใต้ `/partials/` ไม่ใช่ `/api/` เพราะ `/api/` เป็นรายการที่ผูกกับ
    ตารางสิทธิ์ของ spec/09 หนึ่งแถวต่อหนึ่ง endpoint
    """
    target = require_profile(profile)
    with db.connect() as conn:
        try:
            settings = config_repo.active_settings(conn, target)
        except ConfigError:
            settings = None
    values = _values_of(settings, symbol) if symbol else None
    return _body(
        request, db, profile=target, user=user, values=values, original=symbol if symbol else ""
    )


@router.get("/partials/symbols/{profile}/delete", response_class=HTMLResponse)
def delete_modal(
    profile: str,
    symbol: str,
    request: Request,
    _: User = Depends(require_cap("edit_symbols")),
) -> HTMLResponse:
    target = require_profile(profile)
    return templates.TemplateResponse(
        request,
        "partials/stepup_modal.html",
        {
            "title": f"ลบ {symbol} · {target}",
            "detail": "บันทึกเป็นเวอร์ชันใหม่ที่ไม่มีเหรียญนี้ — ยังไม่เปิดใช้จนกว่าจะกดเปิดใช้ที่หน้า ตั้งค่า",
            "action": f"/api/{target}/symbols/{symbol}",
            "method": "delete",
        },
    )


def _written(
    request: Request,
    db: Engine,
    *,
    target: str,
    symbols: list[dict],
    base: Settings,
    index: int,
    problems: list[Problem],
    code: str,
    user: User,
    values: Mapping[str, str] | None,
    original: str,
    note: str,
    action: str,
    audit_target: str,
    done: str,
    oob: bool,
) -> HTMLResponse:
    """ตรวจ → ขอรหัส → เขียนเวอร์ชันใหม่ · เส้นทางร่วมของทั้งบันทึกและลบ

    **ตรวจก่อนรหัส** ด้วยเหตุผลที่หัวไฟล์เขียนไว้ · ที่เหลือเป็นคู่เดิมของใบ 21
    (`insert_version` ไม่มี `activate` ตามมา — ร่าง ไม่ใช่ของที่เปิดใช้)
    """
    raw = deepcopy(base.model_dump())
    raw["symbols"] = symbols
    settings: Settings | None = None
    try:
        settings = validate_settings(raw, source="console")
    except ConfigError as exc:
        problems.extend(exc.problems)

    if problems:
        errors, unplaced = _placed(problems, index=index)
        return _body(
            request, db, profile=target, user=user, values=values, original=original,
            errors=errors, unplaced=unplaced, oob=oob,
            notice="ยังบันทึกไม่ได้ — แก้รายการข้างล่างก่อน · รหัสยังไม่ถูกใช้",
        )

    assert settings is not None
    now = now_ms()
    with db.begin() as conn:
        ok = service.verify_step_up(conn, user, code.strip(), now=now)
        if not ok:
            audit.record(
                conn,
                action=f"{action}_refused",
                ts=now,
                actor_user_id=user.id,
                target=audit_target,
                ip=client_ip(request),
            )
    if not ok:
        # ตอบ 200 ไม่ใช่ 403 · 403 เป็นรหัสที่ `base.html` จองไว้ให้ modal ของ step-up
        # ซึ่ง swap ที่ `#modal-slot` · ฟอร์มของหน้านี้ swap ที่ตัวเนื้อหน้า คำตอบ 403
        # จากตรงนี้จะไปลงผิดช่องแล้วหน้าจอจะดูเหมือนปุ่มไม่ทำงาน (เหตุผลเดียวกับใบ 21)
        return _body(
            request, db, profile=target, user=user, values=values, original=original,
            oob=oob, notice="รหัส 6 หลักไม่ถูกต้อง — ยังไม่ได้บันทึกอะไร",
        )

    with db.begin() as conn:
        head = config_repo.insert_version(
            conn,
            settings,
            source="console",
            note=note,
            created_by_user_id=user.id,
            created_ts=now,
        )
        audit.record(
            conn,
            action=action,
            ts=now,
            actor_user_id=user.id,
            target=f"{audit_target} → v{head.version}",
            ip=client_ip(request),
            step_up_verified=True,
        )
    return _body(
        request, db, profile=target, user=user, oob=oob,
        notice=f"{done} · บันทึกเป็นร่าง v{head.version} — เปิดใช้ได้ที่หน้า ตั้งค่า",
    )


def _base_of(db: Engine, target: str) -> Settings | None:
    with db.connect() as conn:
        try:
            return config_repo.active_settings(conn, target)
        except ConfigError:
            return None


@router.post("/api/{profile}/symbols", response_class=HTMLResponse)
def save(
    profile: str,
    request: Request,
    form: Mapping[str, str] = Depends(config_routes.form_values),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("edit_symbols")),
) -> HTMLResponse:
    """เพิ่มหรือแก้เหรียญหนึ่งตัว = **เวอร์ชันใหม่ที่บล็อก `[[symbols]]` ต่างไป**

    ลอกของเดิมมาทั้งชุดแล้วทับแถวเดียว ไม่ใช่ประกอบลิสต์ใหม่จากตารางบนหน้าจอ —
    ตารางนั้นเป็นของเวอร์ชันที่ active ตอน render ถ้ามีคนอื่นบันทึกเวอร์ชันคั่นระหว่าง
    ที่ฟอร์มเปิดอยู่ การส่งทั้งตารางกลับมาจะย้อนงานของเขาเงียบๆ
    """
    target = require_profile(profile)
    base = _base_of(db, target)
    if base is None:
        return _body(
            request, db, profile=target, user=user,
            notice="ยังไม่มีเวอร์ชันที่เปิดใช้ให้ลอก — ทางเข้าครั้งแรกคือ cane db seed",
        )

    block, problems = _block(form)
    original = form.get("original", "").strip()
    was = deepcopy(base.model_dump()["symbols"])
    symbols, index = _upserted(was, block, original=original)
    name = str(block.get("symbol", "")) or original or "เหรียญใหม่"
    # พิมพ์ชื่อที่มีอยู่แล้วลงฟอร์ม "เพิ่มเหรียญ" = **ทับแถวเดิม** ไม่ใช่ได้แถวที่สอง
    # (ชื่อเป็นกุญแจของ `config_symbols`) · คำที่ตอบกลับต้องพูดความจริงข้อนั้น ไม่ใช่
    # บอกว่า "เพิ่มแล้ว" ทั้งที่ค่าเดิมของเหรียญนั้นถูกแทนไปทั้งแถว
    verb = "แก้" if original else ("ทับ" if len(symbols) == len(was) else "เพิ่ม")
    return _written(
        request, db, target=target, symbols=symbols, base=base, index=index,
        problems=problems, code=form.get("step_up_code", ""), user=user,
        values=form, original=original,
        note=f"{verb} {name} จากหน้าคู่เหรียญ",
        action="config.symbols", audit_target=f"{target} {name}",
        done=f"{verb} {name} แล้ว", oob=False,
    )


@router.delete("/api/{profile}/symbols/{symbol:path}", response_class=HTMLResponse)
def remove(
    profile: str,
    symbol: str,
    request: Request,
    step_up_code: str = Form(""),
    from_url: str = Query("", alias="step_up_code"),
    db: Engine = Depends(get_db),
    user: User = Depends(require_cap("edit_symbols")),
) -> HTMLResponse:
    """ลบเหรียญ = เวอร์ชันใหม่ที่ไม่มีบล็อกนั้น

    **รหัสรับได้ทั้งสองที่** เพราะ htmx ส่ง `DELETE` ไม่เหมือน `POST` — ค่าตั้งต้นของ
    `methodsThatUseUrlParams` ใน htmx 2.0.4 คือ `["get","delete"]` รหัสจากหน้าจอจริง
    จึงมาทาง query string ส่วนคำขอที่ประกอบเองส่งมาทาง body ได้ · ถ้ารับทางเดียว
    ทางที่ไม่ได้รับจะเงียบกลายเป็น "รหัสผิด" ซึ่งเป็นคำตอบที่ชี้ผิดที่

    `{symbol:path}` เพราะชื่อเหรียญมี `/` อยู่กลางคำ (`BTC/USDT`) · ลบตัวสุดท้ายไม่ได้
    และตัวที่ปฏิเสธคือ `Settings.symbols` ที่ประกาศ `min_length=1` ไม่ใช่ด่านที่นี่ —
    "ไม่มี symbol เลย" เป็นข้อหนึ่งใน spec/07 §กฎการตรวจ config อยู่แล้ว
    """
    target = require_profile(profile)
    base = _base_of(db, target)
    if base is None:
        return _body(
            request, db, profile=target, user=user, oob=True,
            notice="ยังไม่มีเวอร์ชันที่เปิดใช้ให้ลอก — ทางเข้าครั้งแรกคือ cane db seed",
        )

    symbols = [sym for sym in deepcopy(base.model_dump()["symbols"]) if sym["symbol"] != symbol]
    if len(symbols) == len(base.symbols):
        return _body(
            request, db, profile=target, user=user, oob=True,
            notice=f"ไม่มี {symbol} ในเวอร์ชันที่เปิดใช้อยู่ — ไม่มีอะไรให้ลบ",
        )

    return _written(
        request, db, target=target, symbols=symbols, base=base, index=-1,
        problems=[], code=step_up_code or from_url, user=user, values=None, original="",
        note=f"ลบ {symbol} จากหน้าคู่เหรียญ",
        action="config.symbols_removed", audit_target=f"{target} {symbol}",
        done=f"ลบ {symbol} แล้ว", oob=True,
    )
