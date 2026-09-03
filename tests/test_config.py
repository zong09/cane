"""ตรวจ config — fail-closed และชี้ **ฟิลด์** ที่ต้องแก้

เดิมชุดนี้ตรวจเลขบรรทัดของไฟล์ TOML ให้ตรงกับ badge `L4 / L19` ของหน้าตั้งค่า
ตอนที่ config ย้ายไปเป็นตารางใน DB (`decisions.md` ข้อ 22) เกณฑ์นั้นหายไปพร้อมไฟล์
สิ่งที่แทนและตรงกับหน้าตั้งค่าแบบฟอร์มคือ **path ของฟิลด์** — `symbols[0].leverage`
ชี้ช่องกรอกได้ตรงตัว ซึ่งเลขบรรทัดทำไม่ได้อยู่แล้วเมื่อไม่มีไฟล์

**กฎที่ตรวจยังเป็นชุดเดิมทั้งหมด** เปลี่ยนแค่สิ่งที่ยืนยัน — ไฟล์นี้จึงยังเป็น
หลักฐานว่าการย้ายลง DB ไม่ได้ทำให้กฎข้อไหนหลุดหายไป
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cane.config import ConfigError, load_profile, render_loc, validate_settings

# live.toml ที่พังสี่จุดตาม design handoff (หน้าตั้งค่า แท็บ live.toml)
BROKEN_LIVE = """\
profile    = "live"
timeframe  = "1d"
base_pct   = 32.0
dry_run    = true

[[symbols]]
symbol             = "BTC/USDT"
market             = "usdtm_perp"
bucket_quote_long  = 100.0
bucket_quote_short = 60.0
leverage           = 2.0
allow_short        = true

[risk]
max_position_pct_long    = 50.0
max_position_pct_short   = 40.0
max_leverage             = 3.0
min_liq_buffer_pct       = 25.0
max_daily_loss_pcnt      = 3.0

[broker]
kind          = "ccxt"

[data]
exchange = "binance"
"""


def write(tmp_path, text, name="live.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def problems_of(path):
    with pytest.raises(ConfigError) as caught:
        load_profile(path)
    return caught.value.problems


def fields_of(path):
    return {(p.field_path, p.message) for p in problems_of(path)}


# ── โหลดผ่าน ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("profile", ["live", "paper"])
def test_ships_with_loadable_profiles(profile):
    settings = load_profile(f"config/{profile}.toml")
    assert settings.profile == profile
    assert settings.symbols[0].market == "usdtm_perp"
    assert settings.dry_run is True


def test_paper_profile_matches_design_values():
    paper = load_profile("config/paper.toml")
    live = load_profile("config/live.toml")
    assert (paper.base_pct, live.base_pct) == (10.0, 5.0)
    assert (paper.symbols[0].leverage, live.symbols[0].leverage) == (1.0, 2.0)
    assert (paper.risk.max_leverage, live.risk.max_leverage) == (2.0, 3.0)
    assert (paper.risk.min_liq_buffer_pct, live.risk.min_liq_buffer_pct) == (35.0, 25.0)
    assert (paper.broker.kind, live.broker.kind) == ("paper", "ccxt")


# ── สี่เคสของ design ชี้เป็นฟิลด์ ───────────────────────────────────────────


def test_reports_every_design_problem_in_one_pass(tmp_path):
    """คนแก้ต้องเห็นทุกข้อในรอบเดียว ไม่ใช่แก้ทีละข้อแล้วกดบันทึกใหม่ทุกครั้ง

    ข้อนี้เป็นเหตุผลที่ validator ยังต้องมีอยู่แม้ DB จะมี CHECK ครบ — CHECK
    ล้มที่ข้อแรกที่เจอเสมอ มันบอกได้ทีละข้อ
    """
    found = fields_of(write(tmp_path, BROKEN_LIVE))

    assert ("base_pct", "base_pct = 32.0 อยู่นอกช่วง 5–20") in found
    assert ("risk.max_daily_loss_pcnt", "คีย์ที่ไม่รู้จัก max_daily_loss_pcnt") in found
    assert ("risk.consecutive_loss_breaker", "ขาด consecutive_loss_breaker") in found
    assert ("broker.kind", 'broker.kind = "ccxt" แต่ไม่ระบุ exchange') in found

    # การพิมพ์คีย์ผิดหนึ่งครั้งให้สองข้อเสมอ: คีย์ที่ไม่รู้จัก + คีย์จริงที่ขาดไป
    # ทั้งคู่จริงทั้งคู่ และ detail ของข้อแรกโยงไปหาข้อที่สองให้แล้ว
    assert ("risk.max_daily_loss_pct", "ขาด max_daily_loss_pct") in found
    assert len(found) == 5


def test_unknown_key_suggests_the_intended_one(tmp_path):
    problems = problems_of(write(tmp_path, BROKEN_LIVE))
    typo = next(p for p in problems if "max_daily_loss_pcnt" in p.message)
    assert "max_daily_loss_pct" in typo.detail


def test_a_missing_risk_limit_says_there_is_no_default(tmp_path):
    """risk limit ไม่มีค่าตั้งต้น — ตั้งไม่ครบ = ไม่เทรด (spec/06)"""
    missing = next(
        p for p in problems_of(write(tmp_path, BROKEN_LIVE)) if p.message.startswith("ขาด")
    )
    assert missing.detail == "risk limit ไม่มีค่าตั้งต้นให้"
    assert missing.field_path.startswith("risk.")


def test_a_cross_key_rule_points_at_the_field_that_triggered_it(tmp_path):
    """`exchange` ที่หายไม่มีช่องของตัวเองให้ชี้ จึงชี้ที่ `kind` ตัวจุดชนวนกฎ"""
    problem = next(
        p for p in problems_of(write(tmp_path, BROKEN_LIVE)) if "exchange" in p.message
    )
    assert problem.field_path == "broker.kind"


# ── fail-closed ข้ออื่น ─────────────────────────────────────────────────────


def test_leverage_above_max_leverage_is_rejected(tmp_path):
    """กฎข้ามตาราง — DB เขียนเป็น CHECK ไม่ได้ จึงต้องอยู่ที่ validator เท่านั้น"""
    text = BROKEN_LIVE.replace("leverage           = 2.0", "leverage           = 9.0")
    problem = next(
        p for p in problems_of(write(tmp_path, text)) if "max_leverage" in p.message
    )
    assert problem.field_path == "symbols[0].leverage"


def test_allow_short_without_its_own_bucket_is_rejected(tmp_path):
    text = BROKEN_LIVE.replace("bucket_quote_short = 60.0\n", "")
    problem = next(
        p for p in problems_of(write(tmp_path, text)) if "bucket_quote_short" in p.message
    )
    assert problem.field_path == "symbols[0].allow_short"


def test_paper_profile_cannot_turn_off_dry_run(tmp_path):
    source = Path("config/paper.toml").read_text(encoding="utf-8")
    text = source.replace("dry_run    = true", "dry_run    = false")
    problem = next(p for p in problems_of(write(tmp_path, text)) if "dry_run" in p.message)
    assert problem.field_path == "dry_run"


def test_seed_quote_is_rejected_on_a_real_broker(tmp_path):
    text = BROKEN_LIVE.replace(
        'kind          = "ccxt"', 'kind          = "ccxt"\nseed_quote    = 10000.0'
    )
    assert any("seed_quote" in p.message for p in problems_of(write(tmp_path, text)))


def test_hedge_position_mode_is_not_a_supported_value(tmp_path):
    text = BROKEN_LIVE.replace(
        'kind          = "ccxt"', 'kind          = "ccxt"\nposition_mode = "hedge"'
    )
    assert any("position_mode" in p.message for p in problems_of(write(tmp_path, text)))


def test_config_without_symbols_does_not_load(tmp_path):
    lines = BROKEN_LIVE.splitlines(keepends=True)
    text = "".join(lines[:6] + lines[13:])
    assert any("symbol" in p.message for p in problems_of(write(tmp_path, text)))


def test_each_symbols_block_is_reported_separately(tmp_path):
    """design จริงมีสี่เหรียญ — ปัญหาของเหรียญที่สองต้องชี้ที่เหรียญที่สอง"""
    second = """
[[symbols]]
symbol             = "ETH/USDT"
bucket_quote_long  = 80.0
bucket_quote_short = 40.0
leverage           = 9.0
allow_short        = true
"""
    text = BROKEN_LIVE.replace("\n[risk]", f"{second}\n[risk]")

    problem = next(
        p for p in problems_of(write(tmp_path, text)) if "max_leverage" in p.message
    )
    assert "ETH/USDT" in problem.message
    assert problem.field_path == "symbols[1].leverage"


def test_global_allow_short_defaults_true_and_is_settable(tmp_path):
    """สวิตช์ระดับระบบ — ผลจริงต่อเหรียญคือ AND กับของเหรียญ (ใช้จริงที่ rules ใบ 08)"""
    assert load_profile("config/live.toml").allow_short is True

    good = (
        Path("config/paper.toml")
        .read_text(encoding="utf-8")
        .replace("allow_short = true", "allow_short = false")
    )
    assert load_profile(write(tmp_path, good, "paper.toml")).allow_short is False


# ── ค่าที่ละเอียดกว่าที่เก็บได้ ต้องถูกปฏิเสธ ไม่ใช่ปัดเงียบๆ ─────────────────


@pytest.mark.parametrize(
    ("line", "field_path"),
    [
        ("base_pct   = 10.00001", "base_pct"),
        ("leverage           = 1.234567", "symbols[0].leverage"),
        ("bucket_quote_long  = 100.123456789", "symbols[0].bucket_quote_long"),
        ("max_leverage             = 3.00001", "risk.max_leverage"),
    ],
)
def test_a_value_finer_than_the_store_is_refused(tmp_path, line, field_path):
    """ที่เก็บถือ % ไว้ 4 ตำแหน่ง เงิน 8 ตำแหน่ง — ค่าที่ละเอียดกว่านั้นต้องดัง

    ถ้าปัดให้เงียบๆ ระบบจะเทรดด้วยค่าที่ไม่ใช่ค่าที่คนกรอก ซึ่งเป็นความล้มเหลว
    แบบเดียวกับที่ ADR 18 ปิดประตูไว้ ("ค่าที่ระบบใช้ต้องไม่ต่างจากค่าที่คนเห็น")
    """
    key = line.split("=")[0].strip()
    source = Path("config/paper.toml").read_text(encoding="utf-8")
    replaced = "\n".join(
        line if raw.split("=")[0].strip() == key else raw
        for raw in source.splitlines()
    )
    assert replaced != source

    problem = next(
        p
        for p in problems_of(write(tmp_path, replaced, "paper.toml"))
        if "ทศนิยม" in p.message
    )
    assert problem.field_path == field_path


def test_the_number_of_places_the_store_holds_is_still_accepted(tmp_path):
    """ขอบเขตพอดีต้องผ่าน — ไม่ใช่ปฏิเสธเผื่อไว้"""
    source = Path("config/paper.toml").read_text(encoding="utf-8")
    text = source.replace("base_pct   = 10.0", "base_pct   = 10.1234")

    assert load_profile(write(tmp_path, text, "paper.toml")).base_pct == 10.1234


# ── ไฟล์ผิดรูป ยังเป็นปัญหาของไฟล์ ไม่ใช่ของฟิลด์ ────────────────────────────


def test_malformed_toml_is_a_file_level_problem(tmp_path):
    """ไวยากรณ์ TOML พังไม่มีฟิลด์ให้ชี้ — `loc` ว่างและเก็บเลขบรรทัดของ tomllib ไว้"""
    text = BROKEN_LIVE.replace("dry_run    = true", "dry_run    = ")
    problems = problems_of(write(tmp_path, text))

    assert problems[0].loc == ()
    assert problems[0].field_path == ""
    assert "TOML" in problems[0].message


def test_a_missing_file_is_a_config_problem_not_a_traceback(tmp_path):
    """สั่ง seed ผิดพาธเป็นความผิดพลาดของคนสั่ง — ต้องได้ข้อความ ไม่ใช่ traceback"""
    with pytest.raises(ConfigError) as caught:
        load_profile(tmp_path / "ไม่มีอยู่.toml")

    assert caught.value.problems[0].loc == ()
    assert "อ่านไฟล์ไม่ได้" in caught.value.problems[0].message


def test_the_error_message_names_every_field(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_profile(write(tmp_path, BROKEN_LIVE))
    rendered = str(caught.value)

    for wanted in ("base_pct", "risk.consecutive_loss_breaker", "broker.kind"):
        assert wanted in rendered
    assert "live.toml" in rendered  # บอกด้วยว่าค่าชุดนี้มาจากไหน


# ── ฟอร์มคอนโซลเดินประตูเดียวกับไฟล์ seed ───────────────────────────────────


def test_values_from_a_form_go_through_the_same_gate():
    """ฟอร์มคอนโซลก็คือข้อมูลจากภายนอก — ไม่มีทางเข้าที่ตรวจน้อยกว่ากัน"""
    raw = {
        "profile": "live",
        "timeframe": "1d",
        "base_pct": 7.5,
        "dry_run": True,
        "allow_short": True,
        "symbols": [
            {
                "symbol": "BTC/USDT",
                "market": "usdtm_perp",
                "bucket_quote_long": 100.0,
                "bucket_quote_short": 60.0,
                "leverage": 2.0,
                "allow_short": True,
                "enabled": True,
            }
        ],
        "risk": {
            "max_position_pct_long": 50.0,
            "max_position_pct_short": 40.0,
            "max_leverage": 3.0,
            "min_liq_buffer_pct": 25.0,
            "max_daily_loss_pct": 3.0,
            "consecutive_loss_breaker": 2,
        },
        "broker": {"kind": "ccxt", "exchange": "binance"},
        "data": {"exchange": "binance"},
    }

    settings = validate_settings(raw, source="console")

    assert settings.base_pct == 7.5
    assert settings.broker.margin_mode == "isolated"  # ค่าตั้งต้นของ schema


def test_a_bad_form_says_where_the_values_came_from():
    with pytest.raises(ConfigError) as caught:
        validate_settings({"profile": "paper", "dry_run": False}, source="console")

    assert caught.value.source == "console"
    assert any(p.field_path == "dry_run" for p in caught.value.problems)


@pytest.mark.parametrize(
    ("loc", "expected"),
    [
        ((), ""),
        (("base_pct",), "base_pct"),
        (("risk", "max_leverage"), "risk.max_leverage"),
        (("symbols", 0, "leverage"), "symbols[0].leverage"),
        (("symbols", 12), "symbols[12]"),
    ],
)
def test_field_paths_render_the_way_a_form_names_its_inputs(loc, expected):
    assert render_loc(loc) == expected
