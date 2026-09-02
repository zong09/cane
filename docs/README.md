# cane — เอกสารระบบเทรด CDC Action Zone

สเปกทั้งหมดอยู่ใน `spec/` อ่านตามลำดับได้ แต่ละไฟล์จบในตัว

| ไฟล์ | เนื้อหา |
| --- | --- |
| [spec/00-overview.md](spec/00-overview.md) | ระบบนี้คืออะไร ทำอะไร ไม่ทำอะไร |
| [spec/01-glossary.md](spec/01-glossary.md) | คำศัพท์กลาง — ใช้ชื่อเดียวกันทั้งเอกสารและโค้ด |
| [spec/02-action-zone.md](spec/02-action-zone.md) | สูตร CDC Action Zone ถอดจาก Pine Script จริง |
| [spec/03-trading-rules.md](spec/03-trading-rules.md) | เข้า/ออก, กฎไม้เรียว, cold start |
| [spec/04-confluence-judge.md](spec/04-confluence-judge.md) | สัญญา (contract) ของ LLM ที่ตัดสินปัจจัยสนับสนุน |
| [spec/05-position-sizing.md](spec/05-position-sizing.md) | ตารางขนาดหน้าตัก |
| [spec/06-risk-and-execution.md](spec/06-risk-and-execution.md) | risk limit, kill switch, broker, reconciliation |
| [spec/07-data-and-config.md](spec/07-data-and-config.md) | แหล่งข้อมูล, config profile, ความปลอดภัยของ credential |
| [spec/08-runtime-pipeline.md](spec/08-runtime-pipeline.md) | ลำดับการทำงานต่อการปิดแท่ง |
| [spec/11-notifications.md](spec/11-notifications.md) | การแจ้งเตือน — event ที่ engine ปล่อย, ช่องทาง LINE/Telegram, credential |
| [decisions.md](decisions.md) | การตัดสินใจเชิงสถาปัตยกรรมและเหตุผล |

**ที่เก็บข้อมูล** ยังไม่มีไฟล์สเปกของตัวเอง — ข้อตกลงของ schema, กฎ append-only ด้วยสิทธิ์ของ DB,
และนโยบายความลับอยู่ใน [decisions.md](decisions.md) ข้อ 22–25 ส่วนรูปร่างตารางจริงอ่านได้จาก
`../src/cane/db/schema.py` (ตัวประกาศ) และ `../alembic/versions/` (ลำดับการเปลี่ยน)

**ที่มา:** `../reference/uncle-chaloke-trading-skill.md` (หลักการเทรด), `../reference/cdc_action_zone.pine` (สูตรอินดิเคเตอร์) และ `../reference/cdc_trailing_stop.pine` (เส้น trailing stop ของ cold start ทางที่ 2)
