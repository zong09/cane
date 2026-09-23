# ADR 33 · อัตรา maintenance margin ของ live มาจากปลายทาง ไม่ใช่จาก config

**สถานะ:** accepted · **เพิ่มเมธอดใน [spec/06 §Broker interface](../spec/06-risk-and-execution.md)**

ขั้น 12 ของ [spec/08 §สิบสี่ขั้นของหนึ่งรอบ](../spec/08-runtime-pipeline.md) ตรวจ
`min_liq_buffer_pct` กับไม้ที่ **ยังไม่เปิด** จึงถามราคา liquidation จาก `positions()` ไม่ได้
ต้องคำนวณเอง และสูตรต้องการอัตรา maintenance margin

`engine/pipeline.py` อ่านค่านั้นจาก `settings.broker.maintenance_margin_pct` · แต่
`config/settings.py:cross_checks()` **ปฏิเสธคีย์นั้นใน profile ที่ `broker.kind = "ccxt"`**
ด้วยเหตุผลที่เขียนไว้ตรงๆ ว่า "ราคา liquidation มาจาก exchange"

สองข้อนี้รวมกันแปลว่า **live ได้ `None` ทุกครั้ง แล้วด่าน fail-closed ปฏิเสธไม้ perp ทุกไม้
ตลอดไป** — ระบบที่ผ่านเทสต์ทั้งหมดแต่เปิดไม้จริงไม่ได้เลยสักไม้ · เจอตอนต่อ live ของใบ 13

## ตัดสิน

`Broker` มีเมธอด `maintenance_margin(symbol, notional) -> pct | None` และไปป์ไลน์ถาม
**ปลายทาง** ไม่ใช่ config

- `CcxtBroker` อ่านจากตารางชั้นของ venue (`fetch_market_leverage_tiers`) แล้วเลือก**ชั้นที่
  notional นั้นตกอยู่** ไม่ใช่ชั้นแรกเสมอ — อัตราของ Binance โตตามขนาดไม้ ชั้นแรกต่ำสุด
  การใช้มันกับไม้ใหญ่คือการรายงานว่าไม้ห่าง liquidation มากกว่าความจริง ซึ่งทำให้ด่านที่มีไว้
  กัน liquidation ปล่อยผ่านไม้ที่ควรถูกปฏิเสธ
- `PaperBroker` คืนค่าที่คนกรอกไว้จำลอง (`maintenance_margin_pct` ของ config paper)
- venue ที่ไม่บอกชั้นมาเลย = `None` แล้วด่านปฏิเสธไม้นั้น (fail-closed ตาม spec/06) ไม่ใช่
  เดาอัตรามาตรฐานให้

ทั้งสองทางเดินโค้ดเส้นเดียวกัน ไปป์ไลน์ไม่มี `if` แยก paper กับ live (ADR 9)

## ทำไมไม่ทำทางอื่น

- **ยอมให้ `maintenance_margin_pct` อยู่ใน config ของ live** — ล้ม `cross_checks()` ที่ตั้งใจ
  ไว้ และทำให้คนอ่าน config เข้าใจว่าระบบคิดราคา liquidation เองแทนที่จะใช้ของ venue ·
  ที่แย่กว่านั้นคือค่าที่คนกรอกจะเก่าเงียบๆ เมื่อ venue ปรับตาราง
- **อ่าน `liquidationPrice` จาก `positions()`** — ตอบคำถามผิดข้อ ขั้น 12 ถามถึงไม้ที่ยังไม่เปิด
  ซึ่งยังไม่มีแถวใน `positions()`
- **ข้ามด่านนี้บน live** — `min_liq_buffer_pct` เป็นสิ่งเดียวที่กัน liquidation ได้
  ([spec/06 §`min_liq_buffer_pct` คือสิ่งเดียวที่กัน liquidation](../spec/06-risk-and-execution.md))

## ผลตามมา

- `Broker` โตขึ้นหนึ่งเมธอด · `PaperBroker` ต้องมีด้วย ไม่งั้นสัญญาไม่ครบ
- อัตราถูกจำไว้ต่อเหรียญใน `CcxtBroker` — ตารางชั้นของ venue ไม่เปลี่ยนรายวัน
