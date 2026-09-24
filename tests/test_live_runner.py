"""`LiveRunner` — ธง cold start ที่ถือข้ามแท่ง (ไม่แตะเน็ตหรือฐาน: ของที่ฉีดเข้า `run_bar` ถูกแทนหมด)"""

from __future__ import annotations

from types import SimpleNamespace

from cane.config import load_profile
from cane.engine import live


def test_cold_start_stays_pending_while_run_bar_skips_for_too_few_bars(monkeypatch):
    """spec/08 §cold start — แท่งที่ `run_bar` คืน `None` (แท่งปิดแล้วไม่ถึง `MIN_CLOSED_BARS`)
    ยังไม่ได้ประเมิน cold start · ถ้าปิดธงตรงนั้น run นี้จะเสียโอกาส cold start ไปเงียบๆ"""
    settings = load_profile("config/live.toml")
    assert len([s for s in settings.symbols if s.enabled]) == 1
    seen: list[bool] = []
    results = iter([None, object(), object()])

    def fake_run_bar(conn, ctx, sym):
        seen.append(sym.cold_start_pending)
        record = next(results)
        if record is not None:
            sym.cold_start_pending = False  # ทำแบบเดียวกับ `run_bar` จริงเมื่อเขียนบันทึก
        return record

    monkeypatch.setattr(live, "run_bar", fake_run_bar)
    monkeypatch.setattr(live, "LiveBarSource", lambda *a, **k: object())
    monkeypatch.setattr(live, "CcxtBroker", lambda **k: object())
    monkeypatch.setattr(live, "CcxtLotSource", lambda trading: object())
    monkeypatch.setattr(live.config_repo, "active_version", lambda conn, profile: SimpleNamespace(id=1))

    runner = live.LiveRunner(profile="live", judge_factory=lambda: (object(), "stub"))
    for s in settings.symbols:  # ข้าม `_ensure_market` — ไม่สร้าง ccxt client
        runner._trading[s.market] = runner._data[s.market] = object()

    for close_ts in (1, 2, 3):
        runner(None, settings, close_ts)

    # แท่ง 1 ถูกข้าม → แท่ง 2 ยังเป็นรอบ cold start · แท่ง 2 เขียนบันทึกแล้ว → แท่ง 3 ไม่ใช่
    assert seen == [True, True, False]
