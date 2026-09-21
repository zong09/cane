"""adapter ของ typesafe.ai System One — รูปคำขอกับการแปลงคำตอบเป็น verdict (ADR 30)

ไม่แตะเครือข่ายและไม่ต้องมีคีย์: `urlopen` ถูกแทนด้วยตัวปลอมที่จดคำขอแล้วคืน/ยกผลตามที่เขียนไว้ และ `sleep`
เป็นตัวปลอมที่จดเวลาหน่วง · **นี่พิสูจน์ได้แค่รูปคำขอกับการแปลง** — ปลายทางยอมรับคำขอรูปนี้ไหม และ choice
ตามดัชนีแท่งนิ่งแค่ไหน พิสูจน์ไม่ได้จนกว่าจะยิงด้วยคีย์จริง
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from cane.confluence import VERDICT_JSON_SCHEMA
from cane.confluence.typesafe_client import (
    API_KEY_ENV,
    BASE_URL_ENV,
    MAX_ATTEMPTS,
    MODEL_ENV,
    Q_EVIDENCE,
    Q_PRESENT,
    TypesafeJudgeClient,
)

BASE_URL = "https://api.typesafe.ai"
MODEL = "jev-latest"
KEY = "sk-secret-123"
FACTOR = "CHANNEL_BREAKOUT"
SIDE = "long"
BARS = [35, 36, 37, 38, 39]


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeUrlopen:
    """แทน `urllib.request.urlopen` — จดคำขอแล้วเล่นผลที่กำหนดไว้ทีละครั้ง

    ผลแต่ละครั้งเป็นอย่างใดอย่างหนึ่ง: dict (body JSON ที่สำเร็จ) · bytes (body ดิบ) · หรือ Exception ที่ให้ยก
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list = []
        self.timeouts: list = []

    def __call__(self, request, timeout=None):  # noqa: ANN001
        self.requests.append(request)
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, (bytes, bytearray)):
            return _FakeResponse(bytes(outcome))
        return _FakeResponse(json.dumps(outcome).encode("utf-8"))


def _ok(noul: float, choice: int | None = None) -> dict:
    """คำตอบของ System One ที่สำเร็จ สำหรับสองคำถาม"""
    answers = {Q_PRESENT: {"type": "noul", "noul": noul}}
    if choice is not None:
        answers[Q_EVIDENCE] = {
            "type": "choice",
            "choice": str(choice),
            "confidence": 0.9,
            "probabilities": {str(choice): 0.9},
        }
    return {
        "model": MODEL,
        "answers": answers,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        f"{BASE_URL}/v1/systemone", code, "Error", {}, io.BytesIO(body.encode("utf-8"))
    )


def _client(fake: _FakeUrlopen, sleeps: list | None = None) -> TypesafeJudgeClient:
    return TypesafeJudgeClient(
        base_url=BASE_URL,
        model=MODEL,
        api_key=KEY,
        sleep=sleeps.append if sleeps is not None else lambda _s: None,
        urlopen=fake,
    )


def _ask(client: TypesafeJudgeClient, bar_indices=BARS) -> dict:
    return client.ask(
        system="SYS",
        user="USR",
        schema=VERDICT_JSON_SCHEMA,
        factor=FACTOR,
        side=SIDE,
        bar_indices=bar_indices,
    )


def _body_of(request) -> dict:
    return json.loads(request.data.decode("utf-8"))


def test_the_request_shape_matches_the_api():
    fake = _FakeUrlopen(_ok(0.83, choice=37))
    client = _client(fake)
    _ask(client)

    request = fake.requests[0]
    assert request.full_url == f"{BASE_URL}/v1/systemone"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert request.get_header("Content-type") == "application/json"

    body = _body_of(request)
    assert body["state"] == "USR"
    assert body["model"] == MODEL
    assert set(body["questions"]) == {Q_PRESENT, Q_EVIDENCE}

    noul_q = body["questions"][Q_PRESENT]
    assert noul_q["type"] == "noul"
    assert set(noul_q["criteria"]) == {"true", "false"}

    choice_q = body["questions"][Q_EVIDENCE]
    assert choice_q["type"] == "choice"
    assert set(choice_q["criteria"]) == {str(i) for i in BARS}


def test_present_answer_maps_to_the_verdict_shape():
    fake = _FakeUrlopen(_ok(0.83, choice=37))
    client = _client(fake)
    result = _ask(client)

    assert set(result) == set(VERDICT_JSON_SCHEMA["required"])
    assert result["factor"] == FACTOR
    assert result["side"] == SIDE
    assert result["present"] is True
    assert result["confidence"] == 0.83
    assert result["evidence_bars"] == [37]
    assert "0.8300" in result["rationale"]


def test_absent_answer_has_no_evidence():
    fake = _FakeUrlopen(_ok(0.2))
    client = _client(fake)
    result = _ask(client)

    assert result["present"] is False
    assert result["confidence"] == 0.8
    assert result["evidence_bars"] == []
    assert "0.2000" in result["rationale"]


def test_noul_exactly_at_threshold_is_present():
    fake = _FakeUrlopen(_ok(0.5, choice=38))
    client = _client(fake)
    result = _ask(client)

    assert result["present"] is True
    assert result["confidence"] == 0.5
    assert result["evidence_bars"] == [38]


def test_confidence_is_rounded_to_four_decimals():
    fake = _FakeUrlopen(_ok(0.123456789))
    client = _client(fake)
    result = _ask(client)

    assert result["confidence"] == 0.8765


def test_choice_outside_options_raises():
    fake = _FakeUrlopen(_ok(0.9, choice=999))
    client = _client(fake)
    with pytest.raises(ValueError):
        _ask(client)


def test_noul_outside_range_raises():
    fake = _FakeUrlopen(_ok(1.4))
    client = _client(fake)
    with pytest.raises(ValueError):
        _ask(client)


def test_429_then_success_sleeps_once():
    sleeps: list = []
    fake = _FakeUrlopen(_http_error(429), _ok(0.83, choice=37))
    client = _client(fake, sleeps)
    result = _ask(client)

    assert result["present"] is True
    assert sleeps == [1.0]
    assert len(fake.requests) == 2


def test_529_then_429_then_success_sleeps_exponentially():
    sleeps: list = []
    fake = _FakeUrlopen(_http_error(529), _http_error(429), _ok(0.83, choice=37))
    client = _client(fake, sleeps)
    _ask(client)

    assert sleeps == [1.0, 2.0]
    assert len(fake.requests) == 3


def test_five_429s_exhaust_retries():
    sleeps: list = []
    fake = _FakeUrlopen(*(_http_error(429) for _ in range(MAX_ATTEMPTS)))
    client = _client(fake, sleeps)
    with pytest.raises(RuntimeError):
        _ask(client)

    assert len(fake.requests) == MAX_ATTEMPTS


def test_401_fails_fast_and_never_leaks_the_key():
    sleeps: list = []
    fake = _FakeUrlopen(_http_error(401, "invalid api key"))
    client = _client(fake, sleeps)
    with pytest.raises(RuntimeError) as excinfo:
        _ask(client)

    assert len(fake.requests) == 1
    assert sleeps == []
    message = str(excinfo.value)
    assert "401" in message
    assert KEY not in message


def test_400_carries_the_error_body():
    fake = _FakeUrlopen(_http_error(400, '{"error":"bad model"}'))
    client = _client(fake)
    with pytest.raises(RuntimeError, match="bad model"):
        _ask(client)


def test_non_json_body_raises_value_error():
    fake = _FakeUrlopen(b"this is not json")
    client = _client(fake)
    with pytest.raises(ValueError):
        _ask(client)


def test_url_error_becomes_runtime_error():
    fake = _FakeUrlopen(urllib.error.URLError("connection refused"))
    client = _client(fake)
    with pytest.raises(RuntimeError):
        _ask(client)


def test_empty_bar_indices_raises():
    fake = _FakeUrlopen(_ok(0.83, choice=37))
    client = _client(fake)
    with pytest.raises(ValueError):
        _ask(client, bar_indices=[])
    assert fake.requests == []


def test_more_than_max_options_keeps_only_the_last_255():
    fake = _FakeUrlopen(_ok(0.83, choice=299))
    client = _client(fake)
    _ask(client, bar_indices=range(300))

    choice_q = _body_of(fake.requests[0])["questions"][Q_EVIDENCE]
    assert set(choice_q["criteria"]) == {str(i) for i in range(45, 300)}


def test_from_env_missing_key_names_the_var(monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV, BASE_URL)
    monkeypatch.setenv(MODEL_ENV, MODEL)
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with pytest.raises(RuntimeError, match=API_KEY_ENV):
        TypesafeJudgeClient.from_env()


def test_from_env_builds_a_client(monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV, BASE_URL + "/")  # trailing slash must not matter
    monkeypatch.setenv(MODEL_ENV, MODEL)
    monkeypatch.setenv(API_KEY_ENV, KEY)

    client = TypesafeJudgeClient.from_env()
    assert client.model_id == f"{BASE_URL}|{MODEL}"


def test_repr_does_not_leak_the_key():
    client = TypesafeJudgeClient(base_url=BASE_URL, model=MODEL, api_key=KEY)
    assert KEY not in repr(client)
