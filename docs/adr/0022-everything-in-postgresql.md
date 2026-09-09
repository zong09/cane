# ADR 22 · ข้อมูลทุกอย่างลง PostgreSQL · SQLAlchemy Core + Alembic ไม่ใช้ ORM

**สถานะ:** accepted

เดิมของแต่ละชนิดอยู่คนละที่: config เป็น TOML, แท่งราคาเป็น JSON ต่อ (symbol, timeframe),
DecisionRecord เป็น JSONL, kill switch กับสวิตช์แจ้งเตือนเป็น JSON ใน `var/state/`
เจ้าของระบบสั่งให้ยกเครื่องใหม่ให้ **ทุกอย่างลง DB อย่างเป็นระบบ**

กำไรที่หนักที่สุดสองข้อ ซึ่งเป็นสิ่งที่ไฟล์ทำไม่ได้ ไม่ใช่แค่ทำได้ยากกว่า:

1. **append-only บังคับได้จริง** ด้วยสิทธิ์ของ DB (#23)
2. **บันทึกผูกกับ config ที่ใช้ตัดสินแท่งนั้นได้** — `decisions.config_version_id` ชี้ไปที่
   เวอร์ชันที่ใช้จริง ไม่ต้อง snapshot `base_pct` / `bucket_quote` ลงทุกแถว และไม่ต้องหวังว่า
   เวลาของ git history จะเรียงตรงกับ `bar_close_ts` (ปัญหาที่ #18 สร้างไว้โดยไม่ได้ตั้งใจ)

**ใช้ SQLAlchemy Core + Alembic ไม่ใช่ ORM** — ระบบนี้ส่งข้อมูลไปมาด้วย frozen dataclass และ
ฉีด dependency เข้าทุกชั้นอยู่แล้ว session/identity-map ของ ORM ชนกับสไตล์นั้นตรงๆ และซ่อน SQL
ที่รันจริงไว้หลัง lazy-load ซึ่งเป็นสิ่งที่ระบบที่ยิงเงินจริงไม่ควรมี · repository คืน dataclass
เดิม (`Bar`, `FundingRate`, …) ชั้นบนจึงไม่รู้เลยว่ามี SQL อยู่

**ชนิดข้อมูล:** เงินและราคาเป็น `NUMERIC` · เวลาเป็น `BIGINT` epoch ms คงกฎ "ไม่มี `datetime`
ใน `src/`" ไว้ · จุดแปลง `NUMERIC` ↔ `float` อยู่ที่ repository **ที่เดียว** เพราะราคาที่เข้าสูตร
indicator ต้องเป็น `float` (golden test เทียบกับ TradingView) ขณะที่เงินใน ledger ต้องเป็น `Decimal`

**ผลตามมา — ราคาที่ต้องจ่ายและยอมรับแล้ว:**

- **ชุดเทสต์ไม่ hermetic อีกต่อไป** เทสต์ที่แตะ persistence ต้องมี Postgres จริง จึงแยกด้วย
  marker `db` และชุดที่ไม่แตะ DB ต้องยังรันได้โดยไม่มี service (`pytest -m "not db"`)
- ต้องมี migration เป็นชั้นงานใหม่ที่ต้องดูแล และการแก้ schema กลายเป็นเรื่องที่ต้องคิดถึง
  การถอยกลับด้วย ไม่ใช่แก้ dataclass แล้วจบ
- ของที่เคยอ่านได้ด้วย `cat` ต้องอ่านผ่าน SQL — คอนโซลได้ประโยชน์ แต่การไล่ปัญหาด้วยมือ
  ต้องมี `docker compose exec db psql` เป็นเครื่องมือประจำ

---

[← สารบัญ ADR ทั้งหมด](../decisions.md)
