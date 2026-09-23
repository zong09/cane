# ADR 28 · ccxt สอง client ต่อ market ไม่ใช่ `params={'type': ...}` ต่อคำสั่ง

**สถานะ:** accepted

ccxt `Exchange` หนึ่งตัวมี `defaultType` เดียว · profile เดียวที่เทรดทั้ง `usdtm_perp` และ `spot` บน venue เดียว
([ข้อ 26](0026-market-is-per-symbol.md)) จึงต้องเลือกว่าจะจัดการความต่างนี้อย่างไร — ใบ 12 ถูกสั่งให้ตัดสิน
ไม่ใช่ปล่อยให้ไปค้นพบตอนรันจริง

## ตัดสิน

**หนึ่ง client ต่อหนึ่ง market** สร้างด้วย `make_client(exchange, market)` (`data/exchange.py`) ที่มีอยู่แล้ว
และลูปต่อแท่งวนตาม `(symbol, market)` ไม่ใช่ `symbol` อย่างเดียว

## ทำไม

- **ความผิดพลาดของทางเลือกอื่นเงียบ** — ทางที่ส่ง `params={'type': ...}` ต่อคำสั่งต้องไม่ลืมใส่ในทุก call
  (ส่งคำสั่ง, ยกเลิก, อ่านสถานะ, ตั้ง leverage) ลืมครั้งเดียวคือส่งผิดตลาด ซึ่งบน perp กับ spot คือ
  คนละความเสี่ยงทั้งหมด · ทางที่แยก client ทำให้ "ผิดตลาด" ต้องเป็นการกระทำที่จงใจ ไม่ใช่การลืม
- **state ของแต่ละตลาดแยกกันอยู่แล้ว** — `bars` มี PK รวม `market` ([ข้อ 26](0026-market-is-per-symbol.md)),
  `Broker.market` เป็นค่าประจำ instance, `ReplayBarSource` กับ `LiveBarSource` ผูก market ต่อ instance
  ตั้งแต่ใบ 02b · การมี client ต่อ market ตรงกับรูปนั้น ไม่ต้องมีชั้นแปลเพิ่ม

## ผลตามมา

- runtime ของ engine ถือ `dict[market, client]` ไม่ใช่ client เดียว · ค่าใช้จ่ายคือ connection เพิ่มหนึ่งชุดต่อ market
- paper replay **ไม่แตะ ccxt สั่งซื้อขายเลย** (`PaperBroker` จำลองทั้งหมด) จึงไม่ได้รับผลจากข้อนี้ ·
  ผลจริงอยู่ที่ live ของ `CcxtBroker` (ใบ 13) ซึ่งต้องสร้างต่อ market ตามข้อนี้
