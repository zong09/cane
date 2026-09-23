# ADR 30 · Judge คุยกับ typesafe.ai ได้ผ่าน adapter ที่สอง — รูปของปลายทางไม่ใช่ข้อบังคับของ Judge

**สถานะ:** accepted · **แก้ข้อสมมติของใบ 31** ที่ว่า "ปลายทางมีรูปเดียว (OpenAI-compatible)"

`confluence/openai_client.py` (ใบ 31) เขียนไว้ว่าปลายทางเป็น endpoint แบบ OpenAI-compatible เท่านั้น
ตั้งจาก `.env` สามตัว · replay ของใบ 12 ต้องยิง Judge เป็นร้อยครั้งต่อรัน และผู้ใช้เลือก
**typesafe.ai** เป็นปลายทางนี้ (`POST https://api.typesafe.ai/v1/systemone`)

typesafe **ไม่ใช่** OpenAI-compatible: ส่ง `{state, model, questions}` ได้ `{answers, usage}` กลับมา โดยแต่ละคำถามเป็น
หนึ่งในสามชนิดที่ตอบเป็นค่าที่มีชนิด ไม่ใช่ข้อความ — `noul` (ความน่าจะเป็นของ yes/no) · `choice` (เลือกหนึ่งจากชุด
พร้อมความน่าจะเป็นทุกตัวเลือก) · `score` · ไม่มี `temperature` หรือ `response_format` ให้ตั้ง

## ตัดสิน

เพิ่ม `confluence/typesafe_client.py` เป็น adapter ตัวที่สองของ `LlmClient` · **ตรรกะของ Judge ไม่ถูกแก้** (ลายเซ็นของ `LlmClient.ask` เพิ่มพารามิเตอร์ — ดูผลตามมา) —
[spec/04 §ข้อบังคับเรื่องความคงเส้นคงวา](../spec/04-confluence-judge.md) เขียนไว้อยู่แล้วว่าผู้ให้บริการที่รูปคำขอต่างออกไปคือ
"การเปลี่ยน adapter ไม่ใช่การสลับ flag" · ข้อที่แก้คือถ้อยคำของสัญญาที่ผูกกับ OpenAI เกินจำเป็น

### การแปลง verdict → คำถามของ typesafe

`VERDICT_JSON_SCHEMA` ต้องการ `present`, `confidence`, `evidence_bars`, `rationale` · แปลงดังนี้ (หนึ่ง POST ต่อ factor
สองคำถามใน `questions` เดียวกัน)

| ช่องของ verdict | มาจาก |
|---|---|
| `present` | `noul >= 0.5` ของคำถาม noul ที่ถามว่ามีปัจจัยนั้นหรือไม่ |
| `confidence` | `max(p, 1 - p)` ปัดสี่ตำแหน่ง — **ค่าที่ประกอบขึ้น ไม่ใช่ค่าที่โมเดลรายงาน** (`noul` ไม่มีช่อง confidence แยก) |
| `evidence_bars` | `[int(choice)]` เมื่อ `present` เท่านั้น — คำถาม choice ที่ตัวเลือกคือ `bar_indices` ที่ Judge ส่งมา (ดัชนีแท่งที่ตารางใน prompt แสดงจริง ซึ่งยืดเกิน `CONTEXT_BARS` ได้) |
| `rationale` | ข้อความสังเคราะห์จากชื่อ factor และแท่งที่เลือก — **ไม่ใช่เหตุผลของโมเดล** |

ปัดสี่ตำแหน่งเพราะ `_scaled(PCT_SCALE)` ของ `insert_decision` ปฏิเสธค่าที่ละเอียดกว่านั้น

### สิ่งที่คงไว้ทุกข้อ

- **`model_id` รวม host** (`{base_url}|{model}`) เหมือน adapter แรก — โมเดลเดียวกันคนละปลายทางคือคนละ quantization ได้
  จึงต้องอยู่ใน `prompt_hash` ([ข้อ 5](0005-llm-verdict-stable-per-bar.md))
- **ล้มเหลว = ยก exception** (`transport` / `bad_schema` / `bad_verdict`) ไม่มีค่าคืนแปลว่าพัง → fallback ที่ `base_pct` ตาม
  [ข้อ 6](0006-llm-failure-falls-back-to-base-pct.md) · ผลที่พังไม่ถูกเขียนลง cache
- **`schema.validate()` ยังเป็นด่านที่ยืนเองได้** — choice ที่ตอบดัชนีนอกช่วงหรือไม่ใช่เลขจะตกที่ `bad_verdict`
- คีย์ตั้งจาก `.env` เท่านั้น ไม่มีค่าตั้งต้น ([ข้อ 25](0025-secrets-stay-out-of-the-database.md)) · 429/529 → exponential backoff

## ผลตามมา และสิ่งที่ยังไม่รู้

- `LlmClient.ask()` ต้องรู้ว่าถามอะไร — `factor`, `side` และดัชนีแท่งที่เลือกได้ไม่อยู่ใน `system`/`user`/`schema` ที่มีอยู่
  จึงเพิ่มพารามิเตอร์ keyword-only สามตัว (`factor`, `side`, `bar_indices`) · `OpenAICompatJudgeClient` เมินมันได้ทั้งหมด ·
  `bar_indices` มาจาก `judge.context_start()` ตัวเดียวกับที่ `render_context()` ใช้ ไม่ให้สองที่คำนวณช่วงคนละแบบ
- **`evidence_bars` เหลือแท่งเดียว** (choice เลือกได้ทีละหนึ่ง) แคบกว่าที่ OpenAI-compatible ตอบได้ และ **`rationale`
  ไม่มีเนื้อหาจากโมเดล** — บันทึกย้อนหลังของ replay จึงอธิบายว่า *ทำไม* น้อยกว่าของ live ผ่าน gateway อื่น ·
  ยอมรับเพราะ Judge ตัดสินตัวเลขเท่านั้น ([ข้อ 4](0004-llm-judges-numbers-only.md)) และ `present` กับแท่งอ้างอิงคือส่วนที่เข้าสูตรขนาดไม้
- **ยังไม่เคยยิงด้วยคีย์จริง** — วัดเมื่อ 2026-09-20 ว่า Python เข้าถึง `api.typesafe.ai` ได้จากเน็ตนี้ (ได้ 401 จาก
  คีย์ปลอม ไม่ถูก Zscaler ดัก) แต่ความนิ่งของ choice-ตามดัชนีแท่งยังไม่ได้วัด · เทสต์พิสูจน์ได้แค่รูปคำขอและการแปลงคำตอบ
- cache ยังเป็นตัวรับประกันว่าแท่งเดียวได้คำตอบเดียว ไม่ใช่ความนิ่งของโมเดล
