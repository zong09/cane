"""เลือก adapter จาก `.env` — จุดเดียวที่รู้ว่ามี adapter กี่ตัว

## กฎการเลือกคือ "มี `CANE_LLM_BASE_URL` ไหม" ไม่ใช่สวิตช์แยก

สวิตช์ `CANE_LLM_PROVIDER=anthropic|openai_compat` อ่านง่ายกว่าตอนมอง `.env` แต่มัน
ขัดกันเองได้ — `provider=anthropic` ที่ยังมี `base_url` ค้างอยู่จากเครื่องก่อนหน้าคือ
สภาพที่ต้องมีด่านคอยตรวจ และเป็นด่านที่ลืมเขียนได้ · การให้ **การมีอยู่ของค่าเป็นตัว
เลือกเอง** ทำให้สภาพที่ขัดกันเขียนลงไปไม่ได้ตั้งแต่แรก ท่าเดียวกับที่ ADR 6 เลือก
exception แทนค่าคืนที่แปลว่าพัง: สิ่งที่ถูกลืมได้ ไม่ควรเป็นสิ่งที่ความถูกต้องพึ่งพา

## ทั้งสอง adapter มี `model_id` แต่ `LlmClient` ไม่มี — และนั่นถูกแล้ว

`judge_side()` รับ `model_id` เป็นพารามิเตอร์แยก มันจึงไม่เคยอ่านจาก client เลย ·
`LlmClient` อธิบาย "สิ่งที่ `judge_side` ต้องการ" ซึ่งมีแค่ `ask()` จริง ๆ — การเติม
`model_id` เข้าไปจะบังคับให้ client ปลอมในเทสต์ต้องมีช่องที่ไม่มีใครอ่าน

แต่ **ผู้เรียกที่ประกอบคีย์ cache ต้องการทั้งคู่** และต้องถามด้วยท่าเดียวกันไม่ว่าจะได้
adapter ตัวไหนมา ไม่ใช่ `client.model` กับ `client.base_url + client.model` แล้วแต่ตัว —
ที่แบบนั้นคือที่ที่ประกอบคีย์ผิดแล้วไม่มีอะไรฟ้อง · ค่าคืนของฟังก์ชันนี้จึงระบุเป็น
adapter จริงสองตัว ไม่ใช่ `LlmClient` ที่แคบกว่าของที่คืนไปจริง
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from cane.confluence.openai_client import BASE_URL_ENV, OpenAICompatJudgeClient

if TYPE_CHECKING:
    from cane.confluence.anthropic_client import AnthropicJudgeClient


def judge_client_from_env() -> OpenAICompatJudgeClient | AnthropicJudgeClient:
    """คืน adapter ที่ `.env` ชี้ไว้ · ค่าไม่ครบ = `RuntimeError` ไม่ใช่ค่าตั้งต้น

    นำเข้า `anthropic_client` **แบบ lazy** เพราะโมดูลนั้นเป็นของ extra `llm` ตามความ
    ตั้งใจเดิมของมัน · เครื่องที่ตั้ง `CANE_LLM_BASE_URL` ไว้ไม่ควรต้องติดตั้ง SDK ของ
    Anthropic เพื่อให้ import ผ่าน ซึ่งคือเหตุผลทั้งหมดที่ `openai_client` ไม่มี dep
    """
    if os.environ.get(BASE_URL_ENV):
        return OpenAICompatJudgeClient.from_env()

    from cane.confluence.anthropic_client import AnthropicJudgeClient

    return AnthropicJudgeClient()
