"""adapter ตัวจริงที่คุยกับ Anthropic API — **ยังไม่เคยถูกรันแม้แต่ครั้งเดียว**

## สถานะของไฟล์นี้ อ่านก่อนเชื่ออะไรในนี้

ไม่มีคีย์ใน `.env` · แพ็กเกจ `anthropic` ยังไม่ได้ติดตั้งในสภาพแวดล้อมนี้ · และยังไม่รู้
ว่า Zscaler ที่บล็อก TLS ฝั่ง Python ไปยัง Binance บล็อกทางไป Anthropic ด้วยหรือไม่
(`curl` ผ่านแต่ Python ไม่ผ่าน — ปัญหาเดิมที่ขวางใบ 12/13 อยู่)

**เทสต์ของใบ 06 ไม่แตะไฟล์นี้เลย** มันทดสอบ `judge.py` ผ่าน `LlmClient` ปลอม ซึ่งเป็น
เหตุผลที่ ADR 3 แยก interface ออกจาก adapter ตั้งแต่แรก · ตรรกะ "คำตอบนี้ใช้ได้ไหม"
พิสูจน์แล้ว ส่วน "คุยกับ Anthropic ถูกวิธีไหม" **ยังไม่พิสูจน์** ถือเป็นของที่ต้อง
ทดสอบด้วยมือครั้งแรกที่มีคีย์ ไม่ใช่ของที่ผ่านแล้ว

## สามข้อที่เขียนตามเอกสารของ Anthropic ไม่ใช่ตามความจำ

1. **ไม่ส่ง `temperature`** — โมเดลรุ่นปัจจุบัน (Opus 5, Sonnet 5, ตระกูล 4.6 ขึ้นไป)
   ถอดพารามิเตอร์นี้ออกแล้ว ส่งไปได้ 400 · spec/04:64 ที่สั่ง `temperature 0` เขียนไว้
   ก่อนหน้านั้น ตัวที่รับประกันความคงเส้นคงวาจริงคือ cache ต่อแท่ง ไม่ใช่ค่านี้
2. **structured output อยู่ที่ `output_config.format`** ไม่ใช่ `output_format` ที่
   เลิกใช้แล้ว และไม่ใช่การแปะ schema ลงใน prompt แล้วขอร้อง
3. **`effort` อยู่ใน `output_config` ไม่ใช่ชั้นบนสุด** · ใช้ `medium` เพราะงานนี้คือ
   การอ่านตัวเลขชุดเล็กแล้วตอบใช่/ไม่ใช่พร้อมเหตุผลสั้น ไม่ใช่งานที่ต้องคิดยาว

## คีย์อยู่ใน `.env` ตาม ADR 25 ไม่ลง DB

ชื่อตัวแปรตามแบบเดียวกับคีย์ exchange ที่มีอยู่แล้ว (`CANE_BINANCE_API_KEY`) ·
**ยังไม่มีใน `.env.example` เพราะยังไม่ได้ตกลงกับเจ้าของว่าจะใช้ชื่อนี้**
"""

from __future__ import annotations

import os
from typing import Any

#: โมเดลที่ใช้ตัดสิน · เข้าไปอยู่ใน `prompt_hash` ด้วย การเปลี่ยนค่านี้จึงล้าง cache
#: ของฝั่งนั้นโดยอัตโนมัติ ไม่ใช่อ่านคำตัดสินของโมเดลเก่ามาใช้ต่อเงียบๆ
DEFAULT_MODEL = "claude-opus-5"

#: ADR 25 — ความลับอยู่ใน `.env` ไม่ลงฐานข้อมูล
API_KEY_ENV = "CANE_ANTHROPIC_API_KEY"

#: คำตัดสินหนึ่งตัวสั้นมาก แต่ไม่ตั้งต่ำกว่านี้เพราะการถูกตัดกลางประโยคทำให้ JSON
#: ไม่ครบรูป แล้วมันจะกลายเป็น `bad_schema` ที่ทำให้ทั้งฝั่งตกไป fallback
MAX_TOKENS = 4096


class AnthropicJudgeClient:
    """`LlmClient` ที่คุยกับ Anthropic จริง — ดูสถานะ "ยังไม่เคยถูกรัน" ที่หัวไฟล์

    นำเข้า SDK แบบ lazy เพื่อให้ `import cane.confluence` ทำงานได้บนเครื่องที่ยังไม่ได้
    ติดตั้ง `anthropic` · ถ้านำเข้าที่หัวไฟล์ เทสต์ทั้งชุดของใบนี้จะพังทันทีทั้งที่
    ไม่มีเทสต์ไหนต้องใช้ SDK เลย — ท่าเดียวกับที่ `conftest.py` ทำ Engine แบบ lazy
    """

    def __init__(self, *, model: str = DEFAULT_MODEL, api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self._api_key:
            raise RuntimeError(
                f"ไม่พบ {API_KEY_ENV} — ใส่ใน .env แล้วสั่งงานด้วย `uv run --env-file .env ...`"
            )
        self._client: Any | None = None

    def ask(
        self, *, system: str, user: str, schema: dict[str, object]
    ) -> dict[str, object]:
        """ยิงหนึ่งคำถาม คืน dict ที่แกะจาก JSON แล้ว · พังแล้วยก exception

        ไม่ดัก exception ใดๆ ที่นี่ **โดยเจตนา** — `judge.py` แปลทุกความล้มเหลวเป็น
        `fallback` ตาม ADR 6 อยู่แล้ว การดักซ้ำที่นี่จะได้แค่ทำให้เหตุผลจริงหายไป
        """
        import json

        client = self._ensure_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        text = next(
            block.text for block in response.content if block.type == "text"
        )
        return json.loads(text)

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client
