# 02 · CDC Action Zone

ถอดจาก `reference/cdc_action_zone.pine` — *CDC ActionZone V3 2020* โดย piriya33 (MPL 2.0)
ทุกอย่างในหน้านี้มาจากไฟล์นั้นโดยตรง ไม่ได้มาจากการเดา

## พารามิเตอร์

| ชื่อ | ค่าตั้งต้น | หมายเหตุ |
| --- | --- | --- |
| `xsrc` | `close` | ราคาที่ใช้ |
| `xprd1` | 12 | คาบ EMA เร็ว |
| `xprd2` | 26 | คาบ EMA ช้า |
| `xsmooth` | 1 | คาบ smoothing ก่อนเข้า EMA |

`xsmooth = 1` ทำให้ `xPrice = ema(close, 1) = close` (alpha = 2/(1+1) = 1) — ที่ค่าตั้งต้นจึงใช้ `close` ตรงๆ ได้ แต่ **ต้องคง parameter ไว้** เพราะผู้ใช้เปลี่ยนได้

## การคำนวณ

```
xPrice = ema(xsrc, xsmooth)
FastMA = ema(xPrice, xprd1)
SlowMA = ema(xPrice, xprd2)

Bull = FastMA > SlowMA
Bear = FastMA < SlowMA
```

## นิยามโซนทั้ง 6 สี

| Zone | เงื่อนไข | ความหมายเดิมใน Pine |
| --- | --- | --- |
| `GREEN` | `Bull and xPrice > FastMA` | Buy |
| `BLUE` | `Bear and xPrice > FastMA and xPrice > SlowMA` | Pre Buy 2 |
| `LBLUE` | `Bear and xPrice > FastMA and xPrice < SlowMA` | Pre Buy 1 |
| `RED` | `Bear and xPrice < FastMA` | Sell |
| `ORANGE` | `Bull and xPrice < FastMA and xPrice < SlowMA` | Pre Sell 2 |
| `YELLOW` | `Bull and xPrice < FastMA and xPrice > SlowMA` | Pre Sell 1 |
| `BLACK` | ไม่เข้าเงื่อนไขใดเลย | เช่น `FastMA == SlowMA` หรือ `xPrice == FastMA` พอดี |

ระบบใช้จริงแค่ `GREEN` กับ `RED` แต่ต้องคำนวณครบทั้ง 6 เพราะ golden test เทียบสีทีละแท่งกับ TradingView

## สัญญาณ

```
buycond  = Green and not Green[1]
sellcond = Red   and not Red[1]

bullish = barssince(buycond) < barssince(sellcond)
bearish = barssince(sellcond) < barssince(buycond)

buy  = bearish[1] and buycond
sell = bullish[1] and sellcond
```

### ชื่อใน Pine ↔ ชื่อในโค้ด

Pine เขียนไว้สำหรับระบบ long-only ชื่อของมันจึงสมมติว่า "แดง = ขายออก" ระบบนี้เทรดสองฝั่ง
แดงจึงเป็น**สัญญาณเปิด short** ไม่ใช่แค่สัญญาณออก — สูตรเหมือนกันทุกบรรทัด เปลี่ยนแต่ชื่อ

| Pine | โค้ด | ความหมายในระบบนี้ |
| --- | --- | --- |
| `buycond` | `longcond` | เขียวแรก |
| `sellcond` | `shortcond` | แดงแรก |
| `bullish` / `bearish` | `state` = `BULLISH` / `BEARISH` / `UNSET` | `UNSET` คือช่วงที่ยังเกิดไม่ครบทั้งสองอย่าง (ดูข้อ 2 ด้านล่าง) |
| `buy` | `long_signal` | เปิด long (หรือ flip จาก short) |
| `sell` | `short_signal` | ปิด long **และเปิด short** ในแท่งเดียว ([03](03-trading-rules.md)) |

ตามกฎของ [01](01-glossary.md): ถ้าเอกสารกับโค้ดเรียกไม่ตรงกัน ถือเป็นบั๊กของเอกสาร
ชื่อฝั่งขวาคือชื่อจริงในโค้ด ชื่อฝั่งซ้ายมีไว้ให้เทียบกับไฟล์ Pine ต้นทางได้เท่านั้น

### สามจุดที่พลาดง่ายตอน port

**1. `buy` ไม่เท่ากับ "แท่งเขียวแรก"**
`buy` ต้องการให้แท่งก่อนหน้าอยู่สถานะ `bearish` ด้วย แท่งเขียวแรกที่โผล่ขึ้นระหว่างที่ระบบยัง bullish อยู่ (เช่น ราคาย่อลงไปโซนเหลืองแล้วเด้งกลับ) **ไม่ใช่สัญญาณซื้อ** ถ้าอ่านเอกสารต้นทางอย่างเดียวแล้ว implement ตามคำว่า "เขียวแรก" ตรงตัว จะได้สัญญาณเกินจำนวนจริง

**2. `barssince` ที่ยังไม่เคยเกิดคือ `na` ไม่ใช่อนันต์**
ใน Pine การเปรียบเทียบใดๆ กับ `na` ให้ผลเป็น false เสมอ ดังนั้น:

- ถ้า `buycond` ยังไม่เคยเกิด → `bullish` เป็น false **และ** `bearish` ก็เป็น false
- `bullish` และ `bearish` จะเริ่มมีค่าจริงก็ต่อเมื่อ **เกิดทั้ง `buycond` และ `sellcond` มาแล้วอย่างละครั้ง**

ผลตามมา: **สัญญาณ `buy` ตัวแรกสุดของชุดข้อมูลจะไม่เกิด** เว้นแต่มี `sellcond` มาก่อนหน้า อย่าแปลง `na` เป็น `+inf` — จะได้ `bearish` เป็น true ก่อนเวลาอันควรและสร้างสัญญาณผีขึ้นมา

**3. ไม่ต้อง implement โหมด fixed timeframe**
`xfixtf` (ค่าตั้งต้น false) ใช้ `request.security` กับ `lookahead_on` ระบบเราตัดสินใจบนแท่งที่ปิดแล้วอยู่แล้ว จึงไม่รองรับโหมดนี้ และไม่ควรรองรับ

## จังหวะการเข้าไม้

label ในสคริปต์เขียนว่า **"BUY next bar"** — สัญญาณยืนยันตอนแท่งปิด แล้วเข้าไม้ที่แท่งถัดไป ตรงกับหลักการที่ระบบตัดสินใจบนแท่งที่ปิดแล้วเท่านั้น

## เกณฑ์ยืนยันความถูกต้อง

golden test เทียบ `Zone` ทีละแท่งกับไฟล์ export จาก TradingView ของ symbol และ timeframe เดียวกัน **ต้องตรง 100% อย่างน้อย 500 แท่ง**

ข้อควรระวัง: การ seed ค่าเริ่มต้นของ EMA ต่างกันได้ระหว่าง Pine กับ `pandas.ewm(adjust=False)` และนั่นคือจุดที่แท่งช่วงต้นจะไม่ตรงกันมากที่สุด **ต้องยืนยันด้วยข้อมูลจริง ไม่ใช่ด้วยการอ่านโค้ด** — ตัดช่วง warm-up ออกก่อนเทียบ และถ้ายังไม่ตรง ให้แก้ที่วิธี seed จนตรง ไม่ใช่ขยายช่วงที่ตัดทิ้ง

### warm-up สองตัวเลข — คนละเรื่องกัน

สับสนสองตัวนี้เมื่อไหร่จะได้ทั้งสัญญาณผีและ golden test ที่หลอกว่าผ่าน

| ตัวเลข | ใช้ตอนไหน |
| --- | --- |
| **130 แท่ง** (`5 × xprd2`) | ตัดทิ้งหัวชุดข้อมูล **ตอนเทียบ golden test** เท่านั้น เพราะ EMA ช่วงต้นขึ้นกับวิธี seed |
| **85 แท่ง** | เกณฑ์ **"พร้อมเทรด"** ของ symbol — มีแท่งปิดน้อยกว่านี้ = ข้าม symbol นั้นทั้งรอบ ไม่คำนวณ ไม่ตัดสิน ([08](08-runtime-pipeline.md) ขั้น 1) |
