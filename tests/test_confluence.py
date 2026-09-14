"""Judge — เกณฑ์ปิดใบสามข้อ บวกสองข้อที่ใบไม่ได้เขียนแต่จำเป็น

เกณฑ์ของใบ 06 ตรงๆ:
1. รัน judge สองครั้งบนแท่งเดิมได้ verdict เดิม ครั้งที่สองมาจาก cache
2. เคส schema violation
3. verdict ฝั่ง long กับ short บนแท่งเดียวกันไม่ทับกันใน cache

สองข้อที่ใบไม่ได้เขียนแต่ขาดไม่ได้ — **ทั้งคู่เกี่ยวกับสิ่งที่ห้ามลง cache**:
4. คำตอบ fallback ต้องไม่ถูกเขียนลง cache · ไม่งั้น timeout ครั้งเดียวกลายเป็น
   `present = false` ถาวรของแท่งนั้น แล้วความเสถียรของผู้ให้บริการจะกลายเป็นตัวแปร
   ของกลยุทธ์อย่างถาวร ซึ่งตรงข้ามกับเหตุผลที่ ADR 6 เลือก fallback แทนการข้ามสัญญาณ
5. คำตัดสินที่ `validate()` ไม่ผ่านก็ห้ามลง cache ด้วยเหตุผลเดียวกัน

`FakeJudge` **นับจำนวนครั้งที่ถูกเรียก** เพราะข้อ 1 พิสูจน์ไม่ได้ด้วยการเทียบค่าที่คืนมา
อย่างเดียว — คำตอบที่เท่ากันเกิดจาก "อ่าน cache" หรือจาก "ถามใหม่แล้วบังเอิญตอบเหมือนเดิม"
ก็ได้ ตัวที่แยกสองอย่างนี้คือจำนวนครั้งที่ปลายทางถูกแตะ ไม่ใช่ค่าที่ได้
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from cane.confluence import (
    FACTORS_BY_SIDE,
    SIDE_OF_FACTOR,
    ConfluenceVerdict,
    judge_side,
    prompt_hash,
    prompt_text,
    render_context,
    validate,
)
from cane.confluence import cache as verdict_cache_repo
from cane.data import Bar
from cane.db.schema import verdict_cache
from cane.indicators import features

DAY = 86_400_000
MODEL = "test-model-v1"


def bars(n: int = 30):
    """แท่งซิกแซกที่มีจุดเหวี่ยงจริง — `features()` ต้องคำนวณได้ครบทุกช่อง"""
    rows = []
    for i in range(n):
        mid = 100.0 + (i % 7) * 3.0 + i * 0.5
        rising = i % 2 == 0
        body = 0.4 if rising else -0.4
        rows.append((mid - body, mid + 1.0, mid - 1.0, mid + body))
    return [
        Bar(
            open_ts=1_600_000_000_000 + i * DAY,
            close_ts=1_600_000_000_000 + (i + 1) * DAY,
            open=o,
            high=h,
            low=lo,
            close=c,
            volume=1.0,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


def _slow_ramp(n: int):
    """แท่งที่เหวี่ยงแรงช่วงต้นแล้วไต่ขึ้นเรียบๆ ช่วงท้าย

    จุดเหวี่ยงที่ยืนยันได้จึงกระจุกอยู่ช่วงต้นของชุด ซึ่งเป็นรูปที่ทำให้หน้าต่างคงที่
    ตัดมันทิ้ง — ตรงกันข้ามกับ `bars()` ที่เหวี่ยงสม่ำเสมอตลอดชุด
    """
    rows = []
    for i in range(n):
        mid = 100.0 + (i % 5) * 6.0 if i < 25 else 120.0 + (i - 25) * 0.2
        rising = i % 2 == 0
        body = 0.3 if rising else -0.3
        rows.append((mid - body, mid + 1.0, mid - 1.0, mid + body))
    return [
        Bar(
            open_ts=1_600_000_000_000 + i * DAY,
            close_ts=1_600_000_000_000 + (i + 1) * DAY,
            open=o, high=h, low=lo, close=c, volume=1.0,
        )
        for i, (o, h, lo, c) in enumerate(rows)
    ]


class FakeJudge:
    """`LlmClient` ปลอมที่นับการเรียก · ไม่ต่อเน็ต ไม่ต้องมีคีย์

    `reply` รับ `(factor, side)` แล้วคืน dict ดิบแบบที่ผู้ให้บริการจะคืน — เทสต์ที่
    ต้องการคำตอบพังส่ง `reply` ที่คืนของพังหรือยก exception ได้ตรงๆ
    """

    def __init__(self, reply=None):
        self.calls: list[tuple[str, str]] = []
        self._reply = reply or _good_reply

    def ask(self, *, system: str, user: str, schema: dict):
        factor = _asked_factor(system)
        side = SIDE_OF_FACTOR[factor]
        self.calls.append((factor, side))
        return self._reply(factor, side)


#: prompt ของฝั่ง short **เอ่ยชื่อ factor ฝั่ง long ด้วย** ("ภาพสะท้อนของ
#: `CHANNEL_BREAKOUT`") ซึ่งถูกต้องสำหรับคนอ่าน แต่แปลว่าการเดา factor ด้วย
#: `"NAME" in system` จะหยิบผิดตัว · อ่านจากบรรทัดหัวข้อที่มีตัวเดียวแทน
_HEADING = "## ปัจจัยที่ต้องตัดสิน: `"


def _asked_factor(system: str) -> str:
    line = next(ln for ln in system.splitlines() if ln.startswith(_HEADING))
    return line[len(_HEADING) :].rstrip("`").strip()


def _good_reply(factor: str, side: str) -> dict:
    return {
        "factor": factor,
        "side": side,
        "present": True,
        "confidence": 0.75,
        "evidence_bars": [27, 28],
        "rationale": f"{factor} เห็นได้จากแท่ง 27 และ 28",
    }


def _run(db, client, side="long", *, symbol="BTC/USDT", market="usdtm_perp"):
    series = bars()
    return judge_side(
        db,
        client,
        market=market,
        symbol=symbol,
        timeframe="1d",
        bars=series,
        feat=features(series),
        side=side,
        model_id=MODEL,
    )


def _rows(db) -> int:
    return db.execute(select(func.count()).select_from(verdict_cache)).scalar_one()


# ── prompt + context — ส่วนที่ไม่แตะฐาน ────────────────────────────────────────


def test_the_two_sides_are_separate_prompt_sets_not_one_with_words_swapped():
    """spec/04:78-82 · `prompts/long/` กับ `prompts/short/` เป็นคนละไฟล์

    ถ้าเป็น template เดียวที่สลับคำ การแก้ถ้อยคำฝั่ง short จะล้าง cache ฝั่ง long
    ไปด้วยทุกครั้ง — `prompt_hash` ที่ต่างกันโดยธรรมชาติคือสิ่งที่กันข้อนั้น
    """
    assert prompt_hash("long", MODEL) != prompt_hash("short", MODEL)
    assert "long" in prompt_text("HIGHER_LOW")
    assert "short" in prompt_text("LOWER_HIGH")


def test_swapping_the_model_invalidates_the_cache_by_itself():
    """สเปกไม่ได้สั่งข้อนี้ · การเปลี่ยนโมเดลมีผลต่อคำตอบไม่น้อยกว่าการแก้ถ้อยคำ

    ถ้า `model_id` ไม่อยู่ใน hash การสลับโมเดลจะอ่านคำตัดสินของโมเดลเก่ามาใช้ต่อ
    เงียบๆ ซึ่งเป็นการ "หลอก" ข้อเดียวกับที่ spec/04:60 ตั้งใจกัน
    """
    assert prompt_hash("long", "model-a") != prompt_hash("long", "model-b")


def test_the_hash_is_stable_across_calls_so_the_cache_is_not_wiped_at_random():
    assert prompt_hash("long", MODEL) == prompt_hash("long", MODEL)


def test_the_bar_numbers_the_llm_sees_are_the_ones_features_talks_about():
    """`evidence_bars` จะชี้ผิดแท่งทั้งหมดถ้าตารางใน prompt เริ่มนับหนึ่งใหม่

    ข้อนี้คือข้อต่อระหว่าง `features()` กับ prompt ที่ไม่มี type ไหนบังคับให้ตรง —
    `swing_lows[i].index` ต้องปรากฏเป็นเลขแถวในตาราง OHLCV ที่ LLM อ่าน
    """
    series = bars()
    feat = features(series)
    text = render_context(series, feat)

    assert feat.swing_lows, "ชุดข้อมูลของเทสต์ต้องมีจุดเหวี่ยง ไม่งั้นข้อนี้ว่างเปล่า"
    for point in feat.swing_lows:
        assert f"| {point.index} |" in text
    assert f"แท่งที่ตัดสินคือ {feat.bar_index}" in text


def test_a_pivot_older_than_the_context_window_still_appears_in_the_table():
    """แท่งที่ feature อ้างถึงต้องอยู่ในตารางที่ LLM อ่านเสมอ แม้จะเก่ากว่าหน้าต่างปกติ

    จุดเหวี่ยงคือก้น/ยอดที่ยืนยันแล้ว มันอยู่ห่างจากปลายเท่าไหร่ก็ได้ ไม่ได้อยู่ใกล้
    ปลายเสมอ · ชุดข้อมูล 30 แท่งของเทสต์อื่นสั้นกว่า `CONTEXT_BARS` (40) จึงไม่มีทาง
    เจอข้อนี้เลย — ต้องมีชุดที่ยาวกว่าถึงจะพิสูจน์ได้

    ถ้าหน้าต่างคงที่ตัดจุดเหวี่ยงทิ้ง LLM จะเห็น `swing_lows` ชี้ไปที่แท่งที่ไม่มีอยู่
    ในตารางที่มันอ่าน แล้ว `evidence_bars` ที่ตอบกลับมาจะอ้างถึงแท่งที่มันไม่เคยเห็น
    """
    series = _slow_ramp(120)
    feat = features(series)
    text = render_context(series, feat)

    cited = [p.index for p in (*feat.swing_lows, *feat.swing_highs)]
    for line in (feat.resistance, feat.support):
        if line is not None:
            cited.extend(line.points)
    assert cited, "ชุดข้อมูลของเทสต์ต้องมีจุดเหวี่ยง"
    assert min(cited) < len(series) - 40, "ต้องมีจุดที่เก่ากว่าหน้าต่างปกติ ไม่งั้นข้อนี้ว่าง"

    for index in cited:
        assert f"| {index} |" in text, f"แท่ง {index} ถูกอ้างถึงแต่ไม่อยู่ในตาราง"


def test_an_invented_bar_index_is_refused_rather_than_cached_forever():
    """ดัชนีที่โมเดลแต่งขึ้นผ่านทุกด่านอื่นได้หมด ถ้าไม่ตรวจขอบบน

    แท่ง 999 ของชุดที่มี 30 แท่งจะถูกเขียนลง cache ถาวรแล้วชี้ไปที่ความว่างเปล่า
    ตอนอ่านย้อนหลัง ซึ่งแย่กว่าการไม่มี evidence เลย
    """
    verdict = ConfluenceVerdict(
        factor="HIGHER_LOW", side="long", present=True, evidence_bars=(999,)
    )
    validate(verdict, asked_factor="HIGHER_LOW", asked_side="long")  # ไม่บอกจำนวนแท่ง = ไม่ตรวจ
    with pytest.raises(ValueError, match="ไม่มีอยู่"):
        validate(
            verdict, asked_factor="HIGHER_LOW", asked_side="long", bar_count=30
        )


def test_the_context_is_byte_identical_for_the_same_bars():
    """cache คีย์ด้วย `bar_close_ts` ถ้าเนื้อที่ส่งไปไม่นิ่ง คีย์ตรงแต่คำถามไม่ตรง"""
    series = bars()
    assert render_context(series, features(series)) == render_context(
        series, features(series)
    )


# ── เกณฑ์ปิดใบข้อ 1 — ถามซ้ำได้คำตอบเดิม และครั้งที่สองมาจาก cache ─────────────


@pytest.mark.db
def test_the_second_run_on_the_same_bar_answers_from_cache_without_asking_again(db):
    """เกณฑ์ข้อ 1 · ตัวชี้ขาดคือ **จำนวนครั้งที่ถูกเรียก** ไม่ใช่ค่าที่คืนมา

    ค่าที่เท่ากันเกิดจาก "อ่าน cache" หรือ "ถามใหม่แล้วบังเอิญตอบเหมือนเดิม" ก็ได้
    `FakeJudge` ตอบเหมือนเดิมเสมอ เทสต์ที่เทียบแต่ค่าจึงผ่านทั้งที่ cache ไม่ทำงานเลย
    """
    client = FakeJudge()
    first = _run(db, client)
    second = _run(db, client)

    assert len(client.calls) == 3, "ถามครั้งเดียวสามตัว รอบสองต้องไม่แตะปลายทางเลย"
    assert first.verdicts == second.verdicts
    assert first.cached == (False, False, False)
    assert second.cached == (True, True, True)
    assert first.factors_present == second.factors_present == 3


@pytest.mark.db
def test_a_cached_verdict_keeps_every_field_not_just_the_yes_or_no(db):
    """`confidence` กับ `rationale` ต้องรอดข้ามรอบด้วย — มันคือของที่คนอ่านย้อนหลัง

    ถ้า cache เก็บแต่ `present` บันทึกของแท่งที่อ่านจาก cache จะอธิบายตัวเองไม่ได้
    ทั้งที่ตอนตัดสินจริงมีเหตุผลครบ (spec/04:58)
    """
    client = FakeJudge()
    _run(db, client)
    again = _run(db, client)

    verdict = again.verdicts[0]
    assert verdict.confidence == pytest.approx(0.75)
    assert verdict.evidence_bars == (27, 28)
    assert verdict.rationale is not None and verdict.factor in verdict.rationale


# ── เกณฑ์ปิดใบข้อ 2 — คำตอบผิดรูป ─────────────────────────────────────────────


@pytest.mark.db
@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        (lambda f, s: {"factor": f, "side": s}, "bad_schema"),
        (lambda f, s: {**_good_reply(f, s), "present": "อาจจะ"}, "bad_schema"),
        (lambda f, s: {**_good_reply(f, s), "factor": "LOWER_HIGH"}, "bad_verdict"),
        (lambda f, s: {**_good_reply(f, s), "side": "short"}, "bad_verdict"),
        (lambda f, s: {**_good_reply(f, s), "confidence": 1.4}, "bad_verdict"),
        (lambda f, s: {**_good_reply(f, s), "evidence_bars": []}, "bad_verdict"),
        (lambda f, s: {**_good_reply(f, s), "evidence_bars": [999]}, "bad_verdict"),
    ],
    ids=[
        "missing_fields",
        "present_is_not_a_bool",
        "answered_a_different_factor",
        "answered_a_different_side",
        "confidence_out_of_range",
        "present_without_evidence",
        "cited_a_bar_that_does_not_exist",
    ],
)
def test_a_malformed_answer_falls_back_for_the_whole_side_and_says_why(
    db, broken, reason
):
    """เกณฑ์ข้อ 2 · แยก "ผิดรูป" (`bad_schema`) ออกจาก "ตอบคนละคำถาม" (`bad_verdict`)

    สองอย่างนี้ต้องแยกกันตอนอ่านย้อนหลัง — อย่างแรกคือ SDK/โมเดลมีปัญหา อย่างหลังคือ
    โมเดลเข้าใจคำถามผิด ซึ่งแก้คนละทางกันคนละเรื่อง

    `answered_a_different_factor` คือเคสที่อันตรายที่สุดของทั้งชุด: คำตอบถูกต้องทุก
    ประการยกเว้นว่ามันตอบคำถามอื่น ถ้าไม่มีด่านนี้มันจะถูกเก็บลงช่องของ factor ที่ถาม
    แล้วดูสมเหตุสมผลตลอดไป

    `cited_a_bar_that_does_not_exist` เดินผ่าน `judge_side` จริงเพื่อพิสูจน์ว่า
    `bar_count` ถูกส่งต่อไปถึง `validate()` — การทดสอบ `validate()` ตรงๆ อย่างเดียว
    ผ่านได้แม้ `judge_side` จะลืมส่งค่านั้นไป
    """
    result = _run(db, FakeJudge(broken))

    assert result.fallback is True
    assert result.fallback_reason == reason
    assert result.factors_present == 0
    assert all(not v.present for v in result.verdicts)
    assert [v.factor for v in result.verdicts] == list(FACTORS_BY_SIDE["long"])


@pytest.mark.db
def test_a_verdict_that_failed_validation_is_never_written_to_the_cache(db):
    """ข้อ 5 · ขยะที่บังเอิญมาถึงต้องไม่กลายเป็นคำตัดสินถาวรของแท่งนั้น"""
    _run(db, FakeJudge(lambda f, s: {**_good_reply(f, s), "confidence": 9.9}))
    assert _rows(db) == 0


# ── ข้อ 4 — LLM ล่ม ────────────────────────────────────────────────────────────


@pytest.mark.db
def test_a_transport_failure_falls_back_and_leaves_the_cache_empty(db):
    """ข้อ 4 · **ข้อที่สำคัญที่สุดที่ใบไม่ได้เขียนไว้**

    ถ้า fallback ถูกเขียนลง cache timeout ครั้งเดียวจะกลายเป็น `present = false`
    ถาวรของแท่งนั้น · แท่งนั้นจะเข้าไม้ที่ `base_pct` ตลอดกาลแม้ LLM จะกลับมาแล้ว
    และไม่มีอะไรในระบบบอกได้ว่าทำไม — ความเสถียรของผู้ให้บริการกลายเป็นตัวแปรของ
    กลยุทธ์อย่างถาวร ซึ่งตรงข้ามกับเหตุผลที่ ADR 6 เลือก fallback แทนการข้ามสัญญาณ
    """

    def blow_up(factor, side):
        raise TimeoutError("ปลายทางไม่ตอบ")

    result = _run(db, FakeJudge(blow_up))

    assert result.fallback is True
    assert result.fallback_reason == "transport"
    assert _rows(db) == 0

    # LLM กลับมาแล้วต้องถามใหม่ได้จริง ไม่ใช่ติดคำตอบเดิมที่ไม่เคยมีใครตัดสิน
    healthy = FakeJudge()
    recovered = _run(db, healthy)
    assert recovered.fallback is False
    assert recovered.factors_present == 3
    assert len(healthy.calls) == 3


@pytest.mark.db
def test_one_bad_factor_takes_the_whole_side_down_not_just_itself(db):
    """`factors_present` เข้าสูตรขนาดไม้โดยตรง (spec/05)

    "2 ปัจจัย" ที่แปลว่า "ตอบได้ 2 จาก 3" กับที่แปลว่า "ตอบครบ 3 มี 2" เป็นคนละเรื่อง
    แต่ลงคอลัมน์เดียวกัน · ตัวที่ตอบไปแล้วยังอยู่ใน cache ไม่เสียเปล่า
    """
    failed_on = []

    def third_one_dies(factor, side):
        failed_on.append(factor)
        if len(failed_on) == 3:
            raise RuntimeError("ตัวที่สามพัง")
        return _good_reply(factor, side)

    result = _run(db, FakeJudge(third_one_dies))

    assert result.fallback is True
    assert result.factors_present == 0
    assert _rows(db) == 2, "สองตัวแรกที่ตอบสำเร็จยังอยู่ใน cache"


# ── เกณฑ์ปิดใบข้อ 3 — สองฝั่งบนแท่งเดียวกันไม่ทับกัน ──────────────────────────


@pytest.mark.db
def test_long_and_short_on_the_same_bar_are_six_separate_rows(db):
    """เกณฑ์ข้อ 3 · spec/04:72 — แท่งเดียวกันเป็นได้ทั้งจุดปิด long และจุดเปิด short

    ถ้า `side` ไม่อยู่ในคีย์ คำตัดสินของสองฝั่งจะทับกันเงียบๆ แล้วไม้ฝั่งหนึ่งจะถูก
    คิดขนาดจากปัจจัยของอีกฝั่ง โดยที่บันทึกดูปกติทุกประการ
    """
    client = FakeJudge()
    long_side = _run(db, client, "long")
    short_side = _run(db, client, "short")

    assert _rows(db) == 6
    assert len(client.calls) == 6
    assert [f for f, _ in client.calls[:3]] == list(FACTORS_BY_SIDE["long"])
    assert [f for f, _ in client.calls[3:]] == list(FACTORS_BY_SIDE["short"])
    assert {v.side for v in long_side.verdicts} == {"long"}
    assert {v.side for v in short_side.verdicts} == {"short"}


def test_the_factor_sets_of_the_two_sides_never_overlap():
    """ตัวที่กัน "คำตัดสินสองฝั่งทับกัน" จริงคือข้อนี้ ไม่ใช่ `side` ในคีย์

    spec/04:72 ให้เหตุผลว่า `side` ต้องอยู่ในคีย์เพราะไม่งั้นสองฝั่งจะทับกัน ·
    เหตุผลนั้นไม่จริงในรูปปัจจุบัน — ชื่อ factor ไม่ซ้ำกันเลยสักตัว คำถามของฝั่ง long
    จึงชนแถวของฝั่ง short ไม่ได้อยู่แล้ว **พิสูจน์ด้วยการกลายพันธุ์**: ถอด `side`
    ออกจาก `WHERE` ของ `cache._match()` แล้วเทสต์ทั้งชุดยังเขียว

    ถ้าวันหนึ่งมีใครใส่ชื่อซ้ำเข้าไปทั้งสองฝั่ง ข้อนี้จะดัง — และวันนั้น `side` ใน
    คีย์จะกลายเป็นสิ่งจำเป็นจริงๆ ไม่ใช่ของที่คงไว้เพราะสเปกเขียนไว้
    """
    long_side, short_side = FACTORS_BY_SIDE["long"], FACTORS_BY_SIDE["short"]
    assert not set(long_side) & set(short_side)
    assert len(set(long_side) | set(short_side)) == 6
    assert all(SIDE_OF_FACTOR[f] == "long" for f in long_side)
    assert all(SIDE_OF_FACTOR[f] == "short" for f in short_side)


@pytest.mark.db
def test_the_same_pair_on_two_markets_does_not_share_one_verdict(db):
    """`market` ไม่ได้อยู่ในคีย์ที่ spec/04:70 เขียนไว้ เพราะข้อนั้นเขียนก่อน ADR 26

    ตอนนี้ `BTC/USDT` บน spot กับบน perp เป็นคนละแท่งจริง (`bars` มี market ใน PK)
    ถ้าคีย์ไม่มี market ไม้ spot จะได้คำตัดสินที่คำนวณจากแท่ง perp มาใช้เงียบๆ
    """
    client = FakeJudge()
    _run(db, client, market="usdtm_perp")
    _run(db, client, market="spot")

    assert _rows(db) == 6
    assert len(client.calls) == 6


@pytest.mark.db
def test_the_long_and_short_spellings_the_ticket_disagrees_on(db):
    """ใบเขียน `SUPPORT_BREAKDOWN` · ฐานข้อมูลรับแต่ `CHANNEL_BREAKDOWN`

    ไม่ใช่เรื่องรสนิยม — CHECK `ck_verdict_cache_factor_matches_side` ปฏิเสธอีกชื่อจริง
    """
    _run(db, FakeJudge(), "short")
    stored = set(db.execute(select(verdict_cache.c.factor)).scalars())
    assert stored == set(FACTORS_BY_SIDE["short"])
    assert "SUPPORT_BREAKDOWN" not in stored


# ── ฐานข้อมูลกันไว้เองอีกชั้น ไม่ได้เชื่อว่า validate() ถูกเรียกเสมอ ──────────────


@pytest.mark.db
def test_the_table_refuses_a_present_verdict_with_no_evidence_on_its_own(db):
    """เขียนผ่าน repo ตรงๆ ข้าม `validate()` — ฐานต้องยังปฏิเสธ (spec/04:55)"""
    from sqlalchemy.exc import IntegrityError

    key = verdict_cache_repo.CacheKey(
        market="usdtm_perp",
        symbol="BTC/USDT",
        timeframe="1d",
        bar_close_ts=1,
        side="long",
        factor="HIGHER_LOW",
        prompt_hash="x",
    )
    bad = ConfluenceVerdict(
        factor="HIGHER_LOW", side="long", present=True, evidence_bars=()
    )
    with pytest.raises(IntegrityError, match="present_needs_evidence"):
        verdict_cache_repo.put(db, key, bad)


@pytest.mark.db
def test_the_table_refuses_a_factor_that_belongs_to_the_other_side(db):
    from sqlalchemy.exc import IntegrityError

    key = verdict_cache_repo.CacheKey(
        market="usdtm_perp",
        symbol="BTC/USDT",
        timeframe="1d",
        bar_close_ts=1,
        side="long",
        factor="LOWER_HIGH",
        prompt_hash="x",
    )
    with pytest.raises(IntegrityError, match="factor_matches_side"):
        verdict_cache_repo.put(
            db, key, ConfluenceVerdict(factor="LOWER_HIGH", side="long", present=False)
        )


@pytest.mark.db
def test_writing_the_same_key_twice_is_refused_rather_than_overwriting_the_past(db):
    """ไม่มี upsert โดยเจตนา · การเขียนทับคำตัดสินของแท่งเดิมคือการเปลี่ยนอดีต

    ซึ่งเป็นสิ่งเดียวที่ตารางนี้มีไว้เพื่อทำให้เป็นไปไม่ได้ · ยิงซ้ำแปลว่าผู้เรียกลืม
    เรียก `get()` ก่อน ซึ่งเป็นบั๊กที่ควรดัง
    """
    from sqlalchemy.exc import IntegrityError

    key = verdict_cache_repo.CacheKey(
        market="usdtm_perp",
        symbol="BTC/USDT",
        timeframe="1d",
        bar_close_ts=1,
        side="long",
        factor="HIGHER_LOW",
        prompt_hash="x",
    )
    verdict = ConfluenceVerdict(factor="HIGHER_LOW", side="long", present=False)
    verdict_cache_repo.put(db, key, verdict)
    with pytest.raises(IntegrityError):
        verdict_cache_repo.put(db, key, verdict)


@pytest.mark.db
def test_never_asked_reads_as_none_not_as_answered_no(db):
    """`get()` คืน `None` แปลว่ายังไม่เคยถาม ไม่ใช่ "ถามแล้วตอบว่าไม่มีปัจจัย"

    อย่างหลังเป็นแถวจริงที่มี `present = false` · ถ้าผู้เรียกแยกสองอย่างนี้ไม่ออก
    ทุกแท่งที่ LLM ตอบว่า "ไม่มีปัจจัย" จะถูกถามใหม่ทุกครั้งที่ process กลับมา
    """
    key = verdict_cache_repo.CacheKey(
        market="usdtm_perp",
        symbol="BTC/USDT",
        timeframe="1d",
        bar_close_ts=1,
        side="long",
        factor="HIGHER_LOW",
        prompt_hash="x",
    )
    assert verdict_cache_repo.get(db, key) is None

    verdict_cache_repo.put(
        db, key, ConfluenceVerdict(factor="HIGHER_LOW", side="long", present=False)
    )
    answered = verdict_cache_repo.get(db, key)
    assert answered is not None and answered.present is False


# ── validate() ตรงๆ ───────────────────────────────────────────────────────────


def test_validate_rejects_a_factor_that_is_not_of_the_side_being_asked():
    verdict = ConfluenceVerdict(factor="LOWER_HIGH", side="long", present=False)
    with pytest.raises(ValueError, match="ฝั่ง"):
        validate(verdict, asked_factor="LOWER_HIGH", asked_side="long")


def test_validate_rejects_an_unknown_factor_name():
    verdict = ConfluenceVerdict(factor="SUPPORT_BREAKDOWN", side="short", present=False)
    with pytest.raises(ValueError, match="ไม่รู้จัก factor"):
        validate(verdict, asked_factor="SUPPORT_BREAKDOWN", asked_side="short")


def test_validate_rejects_negative_bar_indices():
    verdict = ConfluenceVerdict(
        factor="HIGHER_LOW", side="long", present=True, evidence_bars=(-1,)
    )
    with pytest.raises(ValueError, match="ไม่ติดลบ"):
        validate(verdict, asked_factor="HIGHER_LOW", asked_side="long")
