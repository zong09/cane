"""config loader — fail-closed และเลขบรรทัดต้องตรงกับที่ design แสดง"""

from __future__ import annotations

from pathlib import Path

import pytest

from cane.config import ConfigError, load_profile

# ไฟล์ live.toml ที่พังสี่จุดตาม design handoff (หน้าตั้งค่า แท็บ live.toml)
# เลขบรรทัดในคอมเมนต์คือ badge ที่ design แสดงจริง — เป็นเกณฑ์ตรวจของเทสต์ชุดนี้
BROKEN_LIVE = """\
profile    = "live"
timeframe  = "1d"
market     = "usdtm_perp"
base_pct   = 32.0
dry_run    = true

[[symbols]]
symbol             = "BTC/USDT"
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


# ── โหลดผ่าน ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("profile", ["live", "paper"])
def test_ships_with_loadable_profiles(profile):
    settings = load_profile(f"config/{profile}.toml")
    assert settings.profile == profile
    assert settings.market == "usdtm_perp"
    assert settings.dry_run is True


def test_paper_profile_matches_design_values():
    paper = load_profile("config/paper.toml")
    live = load_profile("config/live.toml")
    assert (paper.base_pct, live.base_pct) == (10.0, 5.0)
    assert (paper.symbols[0].leverage, live.symbols[0].leverage) == (1.0, 2.0)
    assert (paper.risk.max_leverage, live.risk.max_leverage) == (2.0, 3.0)
    assert (paper.risk.min_liq_buffer_pct, live.risk.min_liq_buffer_pct) == (35.0, 25.0)
    assert (paper.broker.kind, live.broker.kind) == ("paper", "ccxt")


# ── สี่เคสของ design พร้อมเลขบรรทัด ─────────────────────────────────────────


def test_reports_every_design_problem_in_one_pass(tmp_path):
    """คนแก้ไฟล์ต้องเห็นทุกข้อในรอบเดียว ไม่ใช่แก้ทีละข้อแล้วโหลดใหม่"""
    found = {(p.line, p.message) for p in problems_of(write(tmp_path, BROKEN_LIVE))}

    assert (4, "base_pct = 32.0 อยู่นอกช่วง 5–20") in found
    assert (19, "คีย์ที่ไม่รู้จัก max_daily_loss_pcnt") in found
    assert (20, "ขาด consecutive_loss_breaker") in found
    assert (22, 'broker.kind = "ccxt" แต่ไม่ระบุ exchange') in found

    # การพิมพ์คีย์ผิดหนึ่งครั้งให้สองข้อเสมอ: คีย์ที่ไม่รู้จัก + คีย์จริงที่ขาดไป
    # ทั้งคู่จริงทั้งคู่ และ detail ของข้อแรกโยงไปหาข้อที่สองให้แล้ว
    assert (20, "ขาด max_daily_loss_pct") in found
    assert len(found) == 5


def test_unknown_key_suggests_the_intended_one(tmp_path):
    problems = problems_of(write(tmp_path, BROKEN_LIVE))
    typo = next(p for p in problems if "max_daily_loss_pcnt" in p.message)
    assert "max_daily_loss_pct" in typo.detail


def test_missing_risk_limit_points_at_the_insertion_point(tmp_path):
    """คีย์ที่หายไม่มีบรรทัดของตัวเอง จึงชี้บรรทัดถัดจากคีย์สุดท้ายของตาราง"""
    missing = next(
        p for p in problems_of(write(tmp_path, BROKEN_LIVE)) if p.message.startswith("ขาด")
    )
    assert missing.line == 20
    assert missing.detail == "risk limit ไม่มีค่าตั้งต้นให้"


def test_cross_key_rule_points_at_the_key_that_triggered_it(tmp_path):
    """`exchange` ที่หายไม่มีบรรทัด จึงชี้ที่ `kind` ซึ่งเป็นตัวจุดชนวนกฎ

    design ติด badge `L23` ให้ข้อนี้ แต่ mockup นับแถวคำอธิบาย "— ขาด …" เป็น
    หนึ่งบรรทัดด้วย ไฟล์จริงบนดิสก์ที่ขาดคีย์นั้นไปเลย `kind` จึงอยู่ที่ L22
    ไฟล์เดียวกันจะมีทั้ง "ขาดที่ L20" และ "kind ที่ L23" พร้อมกันไม่ได้
    """
    problem = next(
        p for p in problems_of(write(tmp_path, BROKEN_LIVE)) if "exchange" in p.message
    )
    assert problem.line == 22


# ── fail-closed ข้ออื่น ─────────────────────────────────────────────────────


def test_leverage_above_max_leverage_is_rejected(tmp_path):
    text = BROKEN_LIVE.replace("leverage           = 2.0", "leverage           = 9.0")
    problem = next(
        p for p in problems_of(write(tmp_path, text)) if "max_leverage" in p.message
    )
    assert problem.line == 11


def test_allow_short_without_its_own_bucket_is_rejected(tmp_path):
    text = BROKEN_LIVE.replace("bucket_quote_short = 60.0\n", "")
    problem = next(
        p for p in problems_of(write(tmp_path, text)) if "bucket_quote_short" in p.message
    )
    assert problem.line == 11  # ชี้ที่ allow_short ซึ่งเป็นตัวจุดชนวน


def test_paper_profile_cannot_turn_off_dry_run(tmp_path):
    source = Path("config/paper.toml").read_text(encoding="utf-8")
    text = source.replace("dry_run    = true", "dry_run    = false")
    problem = next(p for p in problems_of(write(tmp_path, text)) if "dry_run" in p.message)
    assert problem.line == 6


def test_seed_quote_is_rejected_on_a_real_broker(tmp_path):
    text = BROKEN_LIVE.replace('kind          = "ccxt"', 'kind          = "ccxt"\nseed_quote    = 10000.0')
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


def test_malformed_toml_reports_its_line(tmp_path):
    text = BROKEN_LIVE.replace("dry_run    = true", "dry_run    = ")
    problems = problems_of(write(tmp_path, text))
    assert problems[0].line == 5
    assert "TOML" in problems[0].message


def test_error_message_lists_every_line(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_profile(write(tmp_path, BROKEN_LIVE))
    rendered = str(caught.value)
    assert "L4" in rendered and "L19" in rendered
    assert "L20" in rendered and "L22" in rendered


def test_line_index_tracks_each_symbols_block_separately(tmp_path):
    """design จริงมีสี่เหรียญ — ปัญหาในบล็อกที่สองต้องชี้บรรทัดของบล็อกที่สอง"""
    second = """
[[symbols]]
symbol             = "ETH/USDT"
bucket_quote_long  = 80.0
bucket_quote_short = 40.0
leverage           = 9.0
allow_short        = true
"""
    text = BROKEN_LIVE.replace("\n[risk]", f"{second}\n[risk]")
    lines = text.splitlines()

    problem = next(
        p for p in problems_of(write(tmp_path, text)) if "max_leverage" in p.message
    )
    assert "ETH/USDT" in problem.message
    assert lines[problem.line - 1].strip().startswith("leverage")
    assert problem.line == 18  # บล็อกที่สอง ไม่ใช่ leverage ของ BTC ที่ L11


def test_global_allow_short_defaults_true_and_is_settable(tmp_path):
    """สวิตช์ระดับระบบ — ผลจริงต่อเหรียญคือ AND กับของเหรียญ (ใช้จริงที่ rules ใบ 08)"""
    assert load_profile("config/live.toml").allow_short is True

    text = BROKEN_LIVE.replace("dry_run    = true", "dry_run    = true\nallow_short = false")
    with pytest.raises(ConfigError):
        load_profile(write(tmp_path, text))  # ยังพังด้วยสี่ข้อเดิม ไม่ใช่เพราะคีย์นี้

    good = Path("config/paper.toml").read_text(encoding="utf-8").replace(
        "allow_short = true", "allow_short = false"
    )
    assert load_profile(write(tmp_path, good, "paper.toml")).allow_short is False
