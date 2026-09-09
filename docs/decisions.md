# การตัดสินใจเชิงสถาปัตยกรรม

แต่ละข้อบันทึกไว้เพื่อให้คนที่มาทีหลังรู้ว่า **อะไรที่ถ้าเปลี่ยนแล้วต้องคิดใหม่ทั้งระบบ** ไม่ใช่แค่แก้บรรทัดเดียว

**เนื้อของแต่ละข้ออยู่ที่ [`docs/adr/`](adr/) ไฟล์ละข้อ** หน้านี้เหลือหน้าที่เป็นสารบัญ
เลขข้อไม่เปลี่ยนและไม่ถูกใช้ซ้ำ ข้อที่ถูกล้มไปแล้วยังอยู่ในรายการพร้อมบอกว่าอะไรมาแทน
เพราะโค้ดกับสเปกอ้างเลขเหล่านี้อยู่

| # | หัวข้อ | สถานะ |
| --- | --- | --- |
| 1 | [ใช้ Python](adr/0001-python-as-the-language.md) | accepted |
| 2 | [ข้อมูลราคาจาก ccxt (Binance)](adr/0002-price-data-from-ccxt.md) | accepted |
| 3 | [ส่งคำสั่งผ่าน Broker interface ไม่ผูก venue](adr/0003-broker-interface-not-a-venue.md) | accepted |
| 4 | [LLM ตัดสินปัจจัยสนับสนุน โดยเห็นเฉพาะตัวเลข](adr/0004-llm-judges-numbers-only.md) | accepted |
| 5 | [คำตัดสินของ LLM ต้องคงที่ต่อแท่ง](adr/0005-llm-verdict-stable-per-bar.md) | accepted · [errata 2026-09-09](adr/0005-llm-verdict-stable-per-bar.md) — กุญแจ cache ขาด `side` |
| 6 | [LLM ล้มเหลว → ลงไม้ `base_pct`](adr/0006-llm-failure-falls-back-to-base-pct.md) | accepted |
| 7 | [ยิงออเดอร์อัตโนมัติภายใต้ risk limit ไม่มีคนกดยืนยันต่อไม้](adr/0007-automatic-orders-under-risk-limits.md) | accepted |
| 8 | [ขนาดไม้เป็น % ของ bucket ต่อ symbol **ต่อฝั่ง**](adr/0008-size-as-pct-of-a-per-side-bucket.md) | accepted |
| 9 | [paper กับ live เป็นคนละ profile โค้ดเส้นทางเดียวกัน](adr/0009-paper-and-live-are-profiles.md) | accepted · แก้ถ้อยคำ 2026-09-02 |
| 10 | [ตัดสินใจบนแท่งที่ปิดแล้วเท่านั้น](adr/0010-closed-bars-only.md) | accepted |
| 11 | [เฟสแรกยังไม่มี backtest engine แต่บังคับบันทึกให้ครบ](adr/0011-no-backtest-engine-but-a-full-record.md) | accepted · สื่อกลางเปลี่ยนเป็นตารางโดย [ข้อ 22](adr/0022-everything-in-postgresql.md) |
| 12 | [`base_pct` เป็นค่าคงที่ ไม่ผูกกับ confidence ของ LLM](adr/0012-base-pct-is-a-constant.md) | accepted |
| 13 | [ไม่เอาเป้าราคาและส่วนจิตวิทยาจากเอกสารต้นทางเข้าโค้ด](adr/0013-no-price-targets-or-psychology.md) | accepted |
| 14 | [ตลาดคือ USDT-M perpetual futures มี leverage](adr/0014-usdtm-perp-with-leverage.md) | **ถูกแทนบางส่วนโดย [ข้อ 26](adr/0026-market-is-per-symbol.md)** |
| 15 | [เทรดสองฝั่ง long + short เต็มตัว](adr/0015-both-sides-long-and-short.md) | accepted |
| 16 | [คงการยิงอัตโนมัติเต็มรูปแบบ ไม่มี human gate ต่อไม้](adr/0016-no-human-gate-per-trade.md) | accepted |
| 17 | [stop loss ของ cold start ทางที่ 2 วางไว้ที่ exchange ไม่ใช่ประเมินฝั่ง engine](adr/0017-cold-start-stop-lives-at-the-exchange.md) | accepted |
| 18 | [config อยู่ใน DB เป็นเวอร์ชันที่แก้ไม่ได้ · TOML เหลือหน้าที่ seed (เขียนใหม่ 2026-09-02)](adr/0018-config-in-the-database.md) | accepted · **เขียนใหม่ทั้งข้อ 2026-09-02** |
| 19 | [ของที่ค้างจาก `flip_aborted` ระบบไม่ปิดเอง คนปิดด้วยมือ](adr/0019-flip-aborted-leftovers-closed-by-hand.md) | accepted |
| 20 | [คอนโซลเป็น FastAPI + Jinja2 + HTMX ไม่ใช่ SPA](adr/0020-console-is-server-rendered.md) | accepted |
| 21 | [การแจ้งเตือนอยู่ในเฟสบอท ไม่ใช่เฟสคอนโซล](adr/0021-notifications-ship-with-the-bot.md) | accepted |
| 22 | [ข้อมูลทุกอย่างลง PostgreSQL · SQLAlchemy Core + Alembic ไม่ใช้ ORM](adr/0022-everything-in-postgresql.md) | accepted |
| 23 | [append-only บังคับด้วยสิทธิ์ของ DB ไม่ใช่ด้วยข้อตกลง](adr/0023-append-only-enforced-by-grants.md) | accepted |
| 24 | [สิ่งที่คำนวณย้อนได้เป็น VIEW ไม่ใช่ตาราง](adr/0024-derived-values-are-views.md) | accepted |
| 25 | [ความลับไม่ลง DB แม้จะสั่งว่า "ทุกอย่างลง DB"](adr/0025-secrets-stay-out-of-the-database.md) | accepted |
| 26 | [ตลาดเป็นค่าต่อ symbol ไม่ใช่ค่าของทั้งระบบ](adr/0026-market-is-per-symbol.md) | accepted |
| 27 | [รูปของบันทึกการตัดสินใจ — กุญแจ เกณฑ์ "เข้าไม้แล้ว" และ `signal`](adr/0027-shape-of-the-decision-record.md) | accepted |

---

## การเพิ่มข้อใหม่

สร้างไฟล์ `docs/adr/NNNN-english-slug.md` ด้วยเลขถัดไป ขึ้นต้นด้วยหัวเรื่องกับบรรทัด
`**สถานะ:**` แล้วเพิ่มแถวในตารางข้างบน · ข้อที่ถูกล้มไม่ถูกลบ ให้แก้สถานะของมันเป็น
ถูกแทนโดยข้อใหม่ แล้วใส่ลิงก์ทั้งสองทาง
