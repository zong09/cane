# ADR 29 · replay รันบน scratch DB แยก มี cursor ต่อ profile ไว้กันรันซ้ำ

**สถานะ:** accepted

replay ย้อนหลังใช้โค้ดเส้นเดียวกับ live ([ADR 9](0009-paper-and-live-are-profiles.md)) แต่มีสองข้อเท็จจริงที่ทำให้
มันรันบน DB เดียวกับ paper/live จริงไม่ได้

1. **`PaperBroker` เก็บเงินสดและสถานะไม้ไว้ในหน่วยความจำ** ไม่ rebuild จาก ledger · replay ที่ตายกลางทางจึง
   resume ไม่ได้ — broker ที่สร้างใหม่ไม่รู้ว่าเดิมถือไม้อะไรอยู่
2. **`insert_decision` ไม่มี `ON CONFLICT`** โดยตั้งใจ ([ข้อ 27](0027-shape-of-the-decision-record.md) §27.1: แถวที่สองคือหลักฐานของ
   restart) · replay ซ้ำบน DB เดิมจึงไม่ล้ม แต่ทำให้ทุกแท่งมีสองแถวเงียบๆ

และตารางข้อเท็จจริงเป็น append-only ([ข้อ 23](0023-append-only-enforced-by-grants.md)) ล้างทิ้งไม่ได้ ถ้า replay ปนเข้าไปหน้าบันทึกกับหน้า
ภาพรวมของ paper จริงจะปนข้อมูลจำลองถาวร

## ตัดสิน

- replay รันบน **database แยก** (ชื่อ `cane_replay` เป็นธรรมเนียม) ที่ `alembic upgrade head` ด้วย `CANE_DB_DSN` ของมันเอง
  — schema เดียวกับ DB จริงทุกประการ · ล้างด้วยการ drop database ไม่ใช่ DELETE
- ตาราง **`replay_cursor`** (migration 0011) หนึ่งแถวต่อ profile เก็บ `as_of_ms`, `end_ts`, `updated_ts` ·
  ไดรเวอร์ `start()` ตอนเริ่ม (ล้มถ้ามีแถวอยู่แล้ว) และ `advance()` ทุก `as_of` ในทรานแซกชันเดียวกับที่ commit
  บันทึกของแท่งนั้น
- **cursor เป็นตัวหมายความคืบหน้าและตัวกันรันซ้ำ ไม่ใช่จุด resume** — จะ resume ได้ก็ต่อเมื่อ `PaperBroker`
  rebuild จาก ledger ได้ ซึ่งเป็นใบแยก

## ทำไมต่อ profile ไม่ใช่ต่อ (symbol, market)

replay มีนาฬิกาเดียวเหมือน live (`as_of` ตัวเดียวเดินไปข้างหน้า แล้วทุก `(symbol, market)` ที่ enabled ถูกเรียกที่
`as_of` นั้น) · cursor ต่อ symbol จะเปิดช่องให้สองตัวเดินไม่พร้อมกัน ซึ่งไม่ใช่การจำลองของสิ่งที่ live ทำ และทำให้
[ADR 9](0009-paper-and-live-are-profiles.md) ไม่จริง

## ผลตามมา

- นับ "กี่แท่งที่ replay ตัดสินใจ" ด้วย `count(DISTINCT bar_close_ts)` ต่อ `(symbol, market)` ตาม [ข้อ 27](0027-shape-of-the-decision-record.md) §27.1
- P&L ของ paper replay **ไม่รวม funding** — ไม่มีแหล่ง funding ย้อนหลังในระบบ ต้องติดป้ายตอนแสดงผล
- role `cane_engine` ได้ `SELECT`/`INSERT` และ `UPDATE (as_of_ms, updated_ts)` เท่านั้น · ไม่มีใครมี `DELETE`
