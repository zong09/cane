"""adapter ที่คุยกับ endpoint แบบ OpenAI-compatible — **ยังไม่เคยถูกรันกับปลายทางจริง**

## สถานะของไฟล์นี้ อ่านก่อนเชื่ออะไรในนี้

รูปคำขอเขียนตามเอกสารของ OpenAI chat-completions ที่ vLLM / Ollama / LM Studio /
OpenRouter ประกาศว่ารองรับ **แต่ยังไม่ได้ยิงใส่ตัวจริงสักครั้ง** — วัดเมื่อ 2026-09-14
ว่าจากเน็ตนี้ `openrouter.ai` ถูก Zscaler ดัก TLS (`api.anthropic.com` ไม่ถูกดัก) และยัง
ไม่มี gateway ที่ localhost ให้ต่อ · ถือเป็นของที่ต้องทดสอบด้วยมือครั้งแรกที่มีปลายทาง
ไม่ใช่ของที่ผ่านแล้ว — ท่าเดียวกับ `anthropic_client.py`

เทสต์ของไฟล์นี้พิสูจน์ **รูปของคำขอและการแปลงคำตอบ** ซึ่งทำได้โดยไม่ต้องมีเน็ต
ส่วน "ปลายทางยอมรับคำขอรูปนี้ไหม" พิสูจน์ไม่ได้จนกว่าจะมีปลายทาง

## ไม่มี dependency ใหม่ — ใช้ `urllib` ของ stdlib

ไม่ใช้ `requests` (อยู่ใน `.venv` แบบ transitive ผ่าน ccxt เท่านั้น **ไม่ได้ประกาศใน
`pyproject.toml`** การ import ของที่ไม่ได้ประกาศคือการรอให้พังวันที่ ccxt เลิกใช้มัน)
และไม่ใช้ `openai` SDK เพราะทั้งไฟล์นี้ต้องการแค่ POST เดียวกับ JSON เดียว

ผลที่ต้องการคือ: ย้ายไปเครื่องอื่นแล้ว **ตั้งสามตัวแปรใน `.env` ก็รันได้เลย** ไม่ต้อง
`uv sync --extra` อะไรก่อน · ต่างจาก `anthropic_client.py` ที่ต้องมี extra `llm`

## `model_id` รวม `base_url` เข้าไปด้วย ไม่ใช่ชื่อโมเดลเปล่า

`prompt_hash()` เอาค่านี้ไปเป็นส่วนหนึ่งของคีย์ cache เพื่อให้การเปลี่ยนโมเดลล้าง
คำตัดสินเก่าโดยอัตโนมัติ · **ชื่อโมเดลอย่างเดียวตรึงน้ำหนักไม่อยู่** — `qwen3:32b` บน
gateway คนละตัวเป็นคนละ quantization ได้ (gateway ที่ route ต่อไปหลายผู้ให้บริการยิ่ง
ชัด) ซึ่งให้คำตอบคนละอย่างบนแท่งเดียวกัน · ถ้าไม่รวม base_url ไว้ การย้ายเครื่องจะ
อ่านคำตัดสินของโมเดลที่ไม่ใช่ตัวเดิมมาใช้ต่อเงียบๆ ซึ่งเป็นการหลอกแบบเดียวกับที่
`0007_verdict_cache.py` ตั้งใจกัน

## สามจุดที่คำขอต่างจากฝั่ง Anthropic โดยเจตนา

1. **ส่ง `temperature: 0` ได้ และส่ง** — ต่างจาก Anthropic ที่ถอดพารามิเตอร์นี้ออกแล้ว
   (ส่งไปได้ 400) · ที่นี่ตั้งได้จึงตั้ง ตามหลัก "ต่ำสุดเท่าที่ API รองรับ" · **แต่มัน
   ไม่ได้รับประกัน determinism** — endpoint ที่ทำ batching รวมผลบวกทศนิยมคนละลำดับ
   ตามองค์ประกอบของ batch พอ logit ขยับ greedy ก็เลือกคนละ token ได้ · ตัวที่รับประกัน
   จริงยังเป็น `verdict_cache` เหมือนเดิม
2. **structured output อยู่ที่ `response_format.json_schema`** ไม่ใช่ `output_config.format`
   ของ Anthropic · `VERDICT_JSON_SCHEMA` มี `additionalProperties: false` กับ `required`
   ครบอยู่แล้ว จึงส่งผ่านเข้า `strict: true` ได้ตรงๆ ไม่ต้องแปลง
3. **ไม่มี `effort`** — เป็นพารามิเตอร์ของ Anthropic ไม่มีในฝั่งนี้

ใช้ `max_tokens` ไม่ใช่ `max_completion_tokens` เพราะ vLLM / Ollama / llama.cpp /
OpenRouter รับตัวแรกกันหมด ส่วนตัวหลังเป็นของใหม่ฝั่ง OpenAI เองที่ยังไม่ทั่วถึง

## `strict: true` ไม่ใช่หลักประกัน — `validate()` ต่างหากที่เป็น

gateway แต่ละเจ้ารองรับ `response_format` ไม่เท่ากัน บางตัวลดชั้นเป็น `json_object`
(JSON ถูกรูปแต่ผิดช่อง) บางตัวเมินทิ้งแล้วตอบร้อยแก้ว **และไม่มีตัวไหนฟ้อง** · ด่านที่
ยืนได้เองโดยไม่พึ่งปลายทางคือ `schema.validate()` ซึ่งตรวจว่า factor/side ตรงกับคำถาม
ที่ถาม, `present` ที่ไม่มี `evidence_bars` ถูกปฏิเสธ, และดัชนีแท่งอยู่ในช่วงจริง ·
คำตอบร้อยแก้วตกที่ `bad_schema` คำตอบผิดช่องตกที่ `bad_verdict` ทั้งคู่กลายเป็น
fallback ที่ **ไม่ถูกเขียนลง cache** (`judge.py` คืนก่อนถึง `cache.put()`)

## ไม่ดัก exception เพื่อกลบ — ดักเพื่อ **เอาเหตุผลจริงออกมา**

`LlmClient` บอกว่าคุยไม่สำเร็จให้ยก exception และ `judge.py` แปลเป็น `transport` เอง
ที่นี่จึงไม่มีค่าคืนที่แปลว่าพัง · ที่ดัก `HTTPError` ไว้ตัวเดียวเพราะ urllib ทิ้ง body
ของคำตอบไปเมื่อ status ไม่ 2xx แล้วเหลือแค่ `"HTTP Error 400: Bad Request"` — ส่วนที่
บอกว่า *ทำไม* (เช่น "this model does not support response_format") อยู่ใน body พอดี
กับที่หายไป · ดักแล้วยกใหม่พร้อม body ไม่ได้เปลี่ยนสัญญา แค่ทำให้ครั้งแรกที่ต่อจริง
อ่านรู้เรื่อง
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

#: URL ฐานของ gateway พร้อม path เวอร์ชัน เช่น `http://localhost:11434/v1`
#: **มีค่านี้ = ใช้ตัวนี้ ไม่มี = ใช้ Anthropic** (ดู `provider.py`)
BASE_URL_ENV = "CANE_LLM_BASE_URL"

#: ชื่อโมเดลตามที่ gateway เรียก เช่น `qwen3:32b` · ไม่มีค่าตั้งต้นโดยเจตนา
#: ชื่อโมเดลที่เดาให้แปลว่าคำตัดสินมาจากโมเดลที่ไม่มีใครเลือก
MODEL_ENV = "CANE_LLM_MODEL"

#: ADR 25 — ความลับอยู่ใน `.env` ไม่ลงฐานข้อมูล · **เว้นว่างได้** เพราะ gateway ที่
#: localhost (Ollama, vLLM, LM Studio) ไม่ต้องใช้คีย์ · เว้นว่าง = ไม่ส่ง header
API_KEY_ENV = "CANE_LLM_API_KEY"

#: เท่ากับฝั่ง Anthropic ด้วยเหตุผลเดียวกัน — คำตัดสินสั้นแต่การถูกตัดกลางประโยค
#: ทำให้ JSON ไม่ครบรูป แล้วทั้งฝั่งตกไป `bad_schema`
MAX_TOKENS = 4096

#: โมเดล open-weight ที่รันบน CPU ตอบช้ากว่า API ที่โฮสต์ไว้มาก และ replay ของใบ 12
#: ยิงติดกันเป็นพัน ๆ ครั้ง · ตั้งสั้นกว่านี้จะได้ `transport` ที่มาจากความช้า
#: ไม่ใช่จากความผิด ซึ่งลง `decisions.llm_fallback_reason` ปนกับของจริงแยกไม่ออก
TIMEOUT_S = 120.0

#: ชื่อ schema ที่ส่งไปกับคำขอ · ผู้ให้บริการบางเจ้าเอาไปใส่ในข้อความ error
SCHEMA_NAME = "confluence_verdict"


class OpenAICompatJudgeClient:
    """`LlmClient` ที่คุยกับ endpoint แบบ OpenAI-compatible — ดูสถานะที่หัวไฟล์"""

    def __init__(
        self, *, base_url: str, model: str, api_key: str | None = None
    ) -> None:
        if not base_url:
            raise RuntimeError(f"ต้องมี base_url — ตั้ง {BASE_URL_ENV} ใน .env")
        if not model:
            raise RuntimeError(f"ต้องมีชื่อโมเดล — ตั้ง {MODEL_ENV} ใน .env")
        # ตัด `/` ท้ายทิ้งเพื่อให้ `.../v1` กับ `.../v1/` ให้ `model_id` ค่าเดียวกัน
        # ไม่งั้นการพิมพ์ `.env` ต่างกันหนึ่งตัวอักษรจะล้าง cache ทั้งก้อนโดยไม่มี
        # อะไรเปลี่ยนจริง — ท่าเดียวกับ `sort_keys=True` ใน `prompt_hash()`
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key or None

    @classmethod
    def from_env(cls) -> OpenAICompatJudgeClient:
        """ประกอบจาก `.env` · ขาดค่าที่จำเป็น = `RuntimeError` ไม่ใช่ค่าตั้งต้น"""
        return cls(
            base_url=os.environ.get(BASE_URL_ENV, ""),
            model=os.environ.get(MODEL_ENV, ""),
            api_key=os.environ.get(API_KEY_ENV),
        )

    @property
    def model_id(self) -> str:
        """ตัวตนของ "โมเดลที่ตัดสิน" ที่จะเข้า `prompt_hash` — ดูหัวไฟล์ว่าทำไมมี host"""
        return f"{self.base_url}|{self.model}"

    def ask(
        self, *, system: str, user: str, schema: dict[str, object]
    ) -> dict[str, object]:
        """ยิงหนึ่งคำถาม คืน dict ที่แกะจาก JSON แล้ว · พังแล้วยก exception"""
        body = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": SCHEMA_NAME,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        payload = self._post("/chat/completions", body)
        content = payload["choices"][0]["message"]["content"]
        return json.loads(content)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            # ดูหัวไฟล์ — ยกใหม่พร้อม body เพราะเหตุผลจริงอยู่ในนั้น
            detail = error.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(
                f"{self.base_url}{path} ตอบ {error.code}: {detail}"
            ) from error
