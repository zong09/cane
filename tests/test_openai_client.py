"""adapter ฝั่ง OpenAI-compatible — พิสูจน์รูปคำขอ เพราะยังไม่มีปลายทางให้ยิง

ไฟล์ที่ทดสอบนี้ **ยังไม่เคยคุยกับ gateway จริงสักครั้ง** สิ่งที่พิสูจน์ได้ตอนนี้จึงมี
สองอย่าง: คำขอที่ประกอบออกมามีรูปตามที่เอกสารของปลายทางบอก และคำตอบที่ปลายทางคืน
ถูกแปลงกลับถูก · "ปลายทางยอมรับคำขอรูปนี้ไหม" พิสูจน์ไม่ได้จนกว่าจะมี gateway

ไม่มีเทสต์ไหนแตะเน็ต — `urlopen` ถูกสลับเป็นของปลอมที่ **เก็บคำขอไว้ให้ตรวจ** ซึ่งเป็น
เหตุผลที่เทสต์ชุดนี้มีค่าทั้งที่ยังต่อจริงไม่ได้: ตัวที่พังตอนต่อครั้งแรกมักไม่ใช่เน็ต
แต่เป็นคำขอที่ประกอบผิดช่อง

**เทสต์ที่สำคัญที่สุดในไฟล์นี้คือ `model_id` มี host อยู่ด้วย** — ไม่ใช่เพราะมันซับซ้อน
แต่เพราะมันเงียบ · ถ้ามันหายไป ระบบจะอ่านคำตัดสินของโมเดลที่ไม่ใช่ตัวเดิมมาใช้ต่อ
โดยไม่มีอะไรฟ้อง แล้วอาการจะโผล่เป็น "กลยุทธ์เปลี่ยนพฤติกรรมหลังย้ายเครื่อง"
ซึ่งไล่หาต้นตอยากมาก
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from cane.confluence import VERDICT_JSON_SCHEMA, prompt_hash
from cane.confluence.anthropic_client import API_KEY_ENV as ANTHROPIC_API_KEY_ENV
from cane.confluence.openai_client import (
    API_KEY_ENV,
    BASE_URL_ENV,
    MODEL_ENV,
    OpenAICompatJudgeClient,
)
from cane.confluence.provider import judge_client_from_env

GATEWAY = "http://localhost:11434/v1"
MODEL = "qwen3:32b"

_VERDICT = {
    "factor": "CHANNEL_BREAKOUT",
    "side": "long",
    "present": True,
    "confidence": 0.8,
    "evidence_bars": [37, 38],
    "rationale": "ทะลุกรอบบนที่แท่ง 37 แล้วยืนได้",
}


class _FakeUrlopen:
    """สลับแทน `urllib.request.urlopen` · เก็บ Request ไว้ให้เทสต์ตรวจ"""

    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.requests: list = []
        self._payload = payload
        self._error = error

    def __call__(self, request, timeout=None):  # noqa: ANN001
        self.requests.append(request)
        self.timeout = timeout
        if self._error is not None:
            raise self._error
        body = json.dumps(self._payload).encode("utf-8")
        return _FakeResponse(body)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_payload(verdict: dict = _VERDICT) -> dict:
    """คำตอบรูปเดียวกับที่ chat-completions คืน — คำตัดสินเป็น **สตริง** ใน content"""
    return {"choices": [{"message": {"content": json.dumps(verdict)}}]}


def _ask(monkeypatch, client: OpenAICompatJudgeClient, payload: dict | None = None):
    fake = _FakeUrlopen(payload if payload is not None else _ok_payload())
    monkeypatch.setattr("urllib.request.urlopen", fake)
    result = client.ask(system="SYS", user="USR", schema=VERDICT_JSON_SCHEMA)
    return result, fake.requests[0]


def _body_of(request) -> dict:  # noqa: ANN001
    return json.loads(request.data.decode("utf-8"))


def test_the_model_id_carries_the_gateway_not_just_the_model_name():
    """ชื่อโมเดลเดียวกันคนละ gateway ต้องเป็นคนละตัวตน

    `qwen3:32b` บน gateway คนละตัวเป็นคนละ quantization ได้ ซึ่งให้คำตอบคนละอย่าง
    บนแท่งเดียวกัน · ถ้าตัวตนเป็นชื่อโมเดลเปล่า การย้ายเครื่องจะอ่านคำตัดสินเก่า
    ของโมเดลที่ไม่ใช่ตัวเดิมมาใช้ต่อเงียบๆ
    """
    here = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL)
    there = OpenAICompatJudgeClient(base_url="http://10.0.0.9:8000/v1", model=MODEL)

    assert here.model_id != there.model_id
    assert GATEWAY in here.model_id
    assert MODEL in here.model_id


def test_a_different_gateway_gives_a_different_prompt_hash():
    """ด่านจริงคือ `prompt_hash` — ตัวที่ลงไปอยู่ในคีย์ของ `verdict_cache`

    เทสต์ข้างบนพิสูจน์ว่า `model_id` ต่างกัน ตัวนี้พิสูจน์ว่าความต่างนั้น**เดินทาง
    ไปถึงคีย์จริง** ไม่ได้หยุดอยู่แค่ property ที่ไม่มีใครเอาไปใช้
    """
    here = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL)
    there = OpenAICompatJudgeClient(base_url="http://10.0.0.9:8000/v1", model=MODEL)

    assert prompt_hash("long", here.model_id) != prompt_hash("long", there.model_id)


def test_a_trailing_slash_does_not_change_the_identity():
    """`.env` ที่พิมพ์ `/v1/` กับ `/v1` ต้องไม่ล้าง cache ทั้งก้อน

    ความต่างหนึ่งตัวอักษรที่ไม่มีความหมายกับปลายทาง ไม่ควรมีความหมายกับคีย์ —
    ท่าเดียวกับ `sort_keys=True` ตอน dump schema ใน `prompt_hash()`
    """
    assert (
        OpenAICompatJudgeClient(base_url=GATEWAY + "/", model=MODEL).model_id
        == OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL).model_id
    )


def test_the_request_asks_for_the_schema_through_response_format(monkeypatch):
    """schema ต้องไปอยู่ใน `response_format` **ไม่ใช่ถูกแปะลง prompt**

    ความต่างคือคำตอบที่ผิดรูปเป็นไปไม่ได้ กับคำตอบที่ผิดรูปได้แต่เราขอไว้ว่าอย่า
    (ข้อบังคับที่ docstring ของ `LlmClient` เขียนไว้)
    """
    client = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL)
    _, request = _ask(monkeypatch, client)
    body = _body_of(request)

    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    # ส่งผ่านตรงๆ ไม่แปลง — `VERDICT_JSON_SCHEMA` มี additionalProperties/required
    # ครบตามที่ strict ต้องการอยู่แล้ว การแปลงระหว่างทางคือที่ที่ตกหล่นได้
    assert body["response_format"]["json_schema"]["schema"] == VERDICT_JSON_SCHEMA
    assert "SYS" not in json.dumps(body["response_format"])


def test_the_request_sets_temperature_zero_and_omits_anthropic_only_fields(monkeypatch):
    """`temperature` ตั้งได้ที่ฝั่งนี้จึงตั้ง · `effort` เป็นของ Anthropic ห้ามหลุดมา

    **และห้ามมี `max_tokens`** — เอกสารของ DashScope/QwenCloud สั่งให้เว้นไว้เมื่อเปิด
    structured output เพราะเพดานที่ตัดกลาง JSON ให้คำตอบที่ parse ไม่ได้ ไม่ใช่คำตอบ
    ที่สั้นลง · มันจะกลายเป็น `bad_schema` ที่ดูเหมือนโมเดลตอบไม่เป็น ทั้งที่เป็น
    เพดานที่เราตั้งเอง
    """
    client = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL)
    _, request = _ask(monkeypatch, client)
    body = _body_of(request)

    assert body["temperature"] == 0
    assert body["model"] == MODEL
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert "max_tokens" not in body
    assert "max_completion_tokens" not in body
    assert "effort" not in body
    assert "output_config" not in body
    assert request.full_url == f"{GATEWAY}/chat/completions"


def test_the_key_is_sent_only_when_there_is_one(monkeypatch):
    """gateway ที่ localhost ไม่ต้องใช้คีย์ — ส่ง header เปล่าไปคือทางที่บางตัวปฏิเสธ"""
    with_key = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL, api_key="sk-x")
    _, request = _ask(monkeypatch, with_key)
    assert request.get_header("Authorization") == "Bearer sk-x"

    without = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL, api_key="")
    _, request = _ask(monkeypatch, without)
    assert request.get_header("Authorization") is None


def test_the_verdict_comes_back_as_a_dict_with_the_six_fields(monkeypatch):
    """คำตัดสินมาเป็น **สตริง JSON ใน `content`** ไม่ใช่ dict — ต้องแกะอีกชั้น

    ชั้นที่ลืมแกะจะคืนสตริงให้ `judge.py` แล้วมันตกเป็น `bad_schema` ทุกครั้ง
    ซึ่งดูเหมือนโมเดลตอบไม่เป็น ทั้งที่ปลายทางตอบถูกมาตลอด
    """
    client = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL)
    result, _ = _ask(monkeypatch, client)

    assert result == _VERDICT
    assert set(result) == set(VERDICT_JSON_SCHEMA["required"])


def test_an_http_error_keeps_the_body_that_says_why(monkeypatch):
    """urllib ทิ้ง body ทิ้งเมื่อ status ไม่ 2xx — ส่วนที่บอกว่าทำไมอยู่ในนั้นพอดี

    ครั้งแรกที่ต่อ gateway จริง ข้อความที่ได้ต้องพาไปถึงสาเหตุ ไม่ใช่
    `HTTP Error 400: Bad Request` ที่บอกแค่ว่าพัง
    """
    detail = '{"error":"this model does not support response_format"}'
    error = urllib.error.HTTPError(
        f"{GATEWAY}/chat/completions", 400, "Bad Request", {}, io.BytesIO(detail.encode())
    )
    fake = _FakeUrlopen(error=error)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    client = OpenAICompatJudgeClient(base_url=GATEWAY, model=MODEL)

    with pytest.raises(RuntimeError, match="response_format"):
        client.ask(system="SYS", user="USR", schema=VERDICT_JSON_SCHEMA)


def test_missing_config_fails_loudly_instead_of_guessing(monkeypatch):
    """ขาด base_url หรือชื่อโมเดล = ล้ม ไม่ใช่เดาค่าตั้งต้นให้

    ชื่อโมเดลที่เดาให้แปลว่าคำตัดสินมาจากโมเดลที่ไม่มีใครเลือก ซึ่งเป็นทิศตรงข้าม
    กับ fail-closed ที่ `config/settings.py` ยึดไว้กับ risk limit อยู่แล้ว
    """
    monkeypatch.delenv(BASE_URL_ENV, raising=False)
    monkeypatch.delenv(MODEL_ENV, raising=False)
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    with pytest.raises(RuntimeError, match=BASE_URL_ENV):
        OpenAICompatJudgeClient.from_env()

    monkeypatch.setenv(BASE_URL_ENV, GATEWAY)
    with pytest.raises(RuntimeError, match=MODEL_ENV):
        OpenAICompatJudgeClient.from_env()

    monkeypatch.setenv(MODEL_ENV, MODEL)
    assert OpenAICompatJudgeClient.from_env().model_id == f"{GATEWAY}|{MODEL}"


def test_the_presence_of_a_base_url_is_what_picks_the_adapter(monkeypatch):
    """กฎการเลือก: มี `CANE_LLM_BASE_URL` = ตัวนี้ · ไม่มี = Anthropic

    **ต้องตรวจทั้งสองขา** — ขาเดียวพิสูจน์ไม่ได้ว่ามีการเลือกเกิดขึ้นจริง โค้ดที่
    คืนตัวนี้เสมอก็ผ่านขาแรกได้เหมือนกัน

    ขา Anthropic ตรวจได้ในสภาพแวดล้อมนี้ทั้งที่ไม่มี SDK ติดตั้ง เพราะ
    `AnthropicJudgeClient.__init__` ล้มเรื่องคีย์**ก่อน**จะ import SDK (มันเป็น
    lazy import ด้วยเหตุผลนี้พอดี) · ตัวที่แยกสองขาออกจากกันคือ **ชื่อตัวแปรใน
    ข้อความ error** ไม่ใช่แค่ว่ามี exception — ถ้าเลือกผิดตัว มันจะบ่นถึง
    `CANE_LLM_BASE_URL` แทน
    """
    monkeypatch.setenv(BASE_URL_ENV, GATEWAY)
    monkeypatch.setenv(MODEL_ENV, MODEL)
    monkeypatch.delenv(API_KEY_ENV, raising=False)

    client = judge_client_from_env()
    assert isinstance(client, OpenAICompatJudgeClient)
    assert client.model_id == f"{GATEWAY}|{MODEL}"

    monkeypatch.delenv(BASE_URL_ENV)
    monkeypatch.delenv(ANTHROPIC_API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match=ANTHROPIC_API_KEY_ENV):
        judge_client_from_env()
