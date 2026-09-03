# 01 · คำศัพท์กลาง

ชื่อในตารางนี้คือชื่อที่ใช้ในโค้ด ถ้าเอกสารกับโค้ดเรียกไม่ตรงกัน ให้ถือว่าเป็นบั๊กของเอกสาร

## ตลาดและสถานะ

| คำ | นิยาม |
| --- | --- |
| **market** | ตลาดของ **แต่ละเหรียญ** ไม่ใช่ของทั้งระบบ — `usdtm_perp` (USDT-M perpetual futures, มี leverage และ liquidation) หรือ `spot` (long-only ไม่มี leverage ไม่มี funding) |
| **Bar** | แท่งเทียนที่ **ปิดแล้ว** ของ timeframe หนึ่ง มี OHLCV และ `close_ts` แท่งที่ยังวิ่งอยู่ไม่ใช่ Bar และไม่เข้าระบบ |
| **Zone** | สีของแท่งตาม CDC Action Zone หนึ่งใน `GREEN`, `BLUE`, `LBLUE`, `RED`, `ORANGE`, `YELLOW`, `BLACK` |
| **Bull / Bear** | `FastMA > SlowMA` / `FastMA < SlowMA` — ความสัมพันธ์ของเส้นค่าเฉลี่ย ไม่ใช่สถานะเทรนด์ของระบบ |
| **state** | สถานะเทรนด์ของระบบ — `BULLISH`, `BEARISH` หรือ `UNSET` นับจากว่า `longcond` หรือ `shortcond` เกิดล่าสุดเมื่อไหร่ `UNSET` คือช่วงที่ยังเกิดไม่ครบทั้งสองอย่าง (ดู [02](02-action-zone.md)) |
| **side** | ฝั่งของไม้ — `long` หรือ `short` ระบบเป็น **one-way** หนึ่ง symbol มีได้ฝั่งเดียวเสมอ |

## สัญญาณ

ชื่อใน Pine กับชื่อในโค้ดไม่ตรงกัน เพราะ Pine เขียนไว้สำหรับระบบ long-only ส่วนระบบนี้เทรดสองฝั่ง — ตารางเทียบชื่ออยู่ที่ [02](02-action-zone.md)

| คำ | นิยาม |
| --- | --- |
| **เขียวแรก / `longcond`** | แท่งที่เข้าสู่โซนเขียวจากโซนที่ไม่ใช่เขียว (`buycond` ใน Pine) |
| **แดงแรก / `shortcond`** | แท่งที่เข้าสู่โซนแดงจากโซนที่ไม่ใช่แดง (`sellcond` ใน Pine) |
| **`long_signal`** | `longcond` ที่เกิดขณะ state ก่อนหน้าเป็น `BEARISH` — **ไม่ใช่ทุก `longcond` จะเป็นสัญญาณ** |
| **`short_signal`** | `shortcond` ที่เกิดขณะ state ก่อนหน้าเป็น `BULLISH` |
| **flip** | แท่งที่เกิดสัญญาณฝั่งตรงข้ามกับที่ถืออยู่ → **ปิดฝั่งเดิมแล้วเปิดฝั่งใหม่ในแท่งเดียว** สองขา |
| **leg** | ขาของการยิงคำสั่งในหนึ่งแท่ง — `open`, `close` หรือ `stop` ใช้เป็นส่วนหนึ่งของ `clientOrderId` |
| **`flip_aborted`** | ขา 1 (ปิด) ไม่ fill ครบ → ยกเลิกขา 2 (เปิด) ทั้งหมด แล้วบันทึกด้วยชื่อนี้ ห้ามเปิดฝั่งใหม่ทับสถานะที่ยังปิดไม่ลง |
| **`residual_qty`** | ส่วนที่ปิดไม่ลงจาก `flip_aborted` — ยังเปิดค้างอยู่ที่ exchange |
| **`unmanaged`** | สถานะที่เปิดค้างอยู่ที่ปลายทางแต่ระบบไม่ได้ตั้งใจถือ ระบบไม่ปิดให้เอง คนต้องปิดด้วยมือ และต้องถูกบันทึก**ซ้ำทุกแท่ง**จนกว่าจะหมด ([decisions #19](../decisions.md)) |

## ปัจจัยสนับสนุน

| คำ | นิยาม |
| --- | --- |
| **ConfluenceFactor** | ปัจจัยสนับสนุนหนึ่งตัว **มีหกตัว แยกตามฝั่ง** — ฝั่ง long: `CHANNEL_BREAKOUT`, `RETAIL_CAPITULATION`, `HIGHER_LOW` · ฝั่ง short: `CHANNEL_BREAKDOWN`, `BUYING_EXHAUSTION`, `LOWER_HIGH` |
| **ConfluenceVerdict** | คำตัดสินของ LLM ต่อหนึ่ง factor — `{factor, side, present, confidence, evidence_bars, rationale}` |

## เงินและขนาดไม้

| คำ | นิยาม |
| --- | --- |
| **Bucket** | เงินทุนที่จัดสรรให้ symbol หนึ่ง **แยกตามฝั่ง** — `bucket_quote_long` และ `bucket_quote_short` หน่วยเป็นสกุลอ้างอิง (quote currency) ขนาดไม้คิดเป็น % ของก้อนฝั่งนั้น ไม่ใช่ของ equity รวม |
| **`base_pct`** | สัดส่วนไม้แรกเมื่อไม่มีปัจจัยสนับสนุนใดเลย เป็นค่าคงที่ใน config ช่วงที่เอกสารต้นทางให้คือ 5–20 |
| **`size_pct`** | `base_pct + 20 × (จำนวน factor ที่ present)` เพดาน 100 แล้วถูก `max_position_pct_<side>` ทับอีกชั้น |
| **margin** | `bucket_quote_<side> × size_pct / 100` — เงินที่วางเป็นหลักประกัน |
| **notional** | `margin × leverage` — มูลค่าสัญญาที่ถือจริง |
| **`leverage`** | อัตราทดต่อ symbol ตั้งใน config ต้องไม่เกิน `max_leverage` |
| **`max_leverage`** | เพดานอัตราทดระดับ profile — `leverage` ของ symbol ใดเกินค่านี้ = โหลด config ไม่ผ่าน |
| **`min_liq_buffer_pct`** | ระยะห่างขั้นต่ำจากราคาปัจจุบันถึงราคา liquidation ที่ยอมรับได้ ถ้าไม้ที่จะเปิดมีระยะน้อยกว่านี้ = ไม่เปิด |
| **`allow_short`** | ต่อ symbol — `false` แปลว่าสัญญาณ short ทำได้แค่ **ปิดฝั่ง long** ไม่เปิด short ใหม่ |

## การควบคุมและบันทึก

| คำ | นิยาม |
| --- | --- |
| **CaneRule (กฎไม้เรียว)** | ห้ามเข้าไม้ถ้าแท่งปัจจุบันไม่ใช่จุดสัญญาณ — บังคับ **ทั้งสองฝั่ง** ไล่ราคาคือละเมิดกฎ |
| **Cold start** | บอทเพิ่งเปิดขึ้นมาขณะที่เทรนด์เดินไปแล้ว — `BULLISH` = ตกรถฝั่ง long, `BEARISH` = ตกรถฝั่ง short **เกิดใหม่ทุกครั้งที่ start engine** ไม่ใช่ครั้งเดียวตอน boot |
| **KillSwitch** | สถานะ latched ที่หยุด**การยิงออเดอร์เปิดใหม่**ทั้งหมด ปลดด้วยการพิมพ์ชื่อ profile ยืนยันเท่านั้น (ไม่แตะ stop order ที่ป้องกันไม้เดิมอยู่ — ดู [06](06-risk-and-execution.md)) |
| **DecisionRecord** | บันทึกครบชุดต่อแท่งต่อ symbol: features → verdicts → sizing → risk check → order หรือ skip |
| **`dry_run`** | โหมดที่คำนวณทุกอย่างครบแต่ไม่ส่งคำสั่งจริง — paper บังคับ `true` เสมอ |
| **profile** | `live` หรือ `paper` — เป็นหน่วยของการแยกทุกอย่าง: config, state, record, cache, broker, engine |
