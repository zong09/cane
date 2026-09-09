# ADR 17 · stop loss ของ cold start ทางที่ 2 วางไว้ที่ exchange ไม่ใช่ประเมินฝั่ง engine

**สถานะ:** accepted

Broker interface ฉบับแรกมีแค่ `place / cancel / positions / balance` ไม่มีแนวคิดของ order type จึงตั้ง stop ไม่ได้ ทั้งที่ cold start ทางที่ 2 บังคับให้ตั้ง stop ที่เส้น Slow Trail ทันที

ทางเลือกที่ปฏิเสธ: ประเมิน stop ฝั่ง engine ตอนแท่งรายวันปิด — บน perp ที่มี leverage คือการป้องกันที่ตรวจวันละครั้ง ราคากระโดดข้ามคืนเดียวก็ถึง liquidation ได้ก่อน engine ตื่น

**ผลตามมา:** interface โตขึ้น — เพิ่ม order type (`stop_market`), `replace` (เส้น Slow Trail ขยับทุกแท่ง จึงต้องขยับ stop ตามโดยไม่เปิดหน้าต่างที่ไม้ไม่มี stop คุ้ม) และ `open_orders` (reconcile ต้องเห็น stop ที่ค้างอยู่ ไม่ใช่ดูแค่ position) · `PaperBroker` ต้องจำลอง stop fill ด้วย ไม่งั้นเส้นทางนี้ทดสอบใน paper ไม่ได้เลย ซึ่งขัดกับ #9 · `clientOrderId` ต้องมีชิ้น `leg` ไม่งั้นการกันสั่งซ้ำไม่นิยามสำหรับ stop order

**สิ่งที่เลือกไว้ให้ชัด:** kill switch latch แล้ว **บล็อกออเดอร์เปิดใหม่ แต่ไม่ยกเลิก stop ที่ป้องกันไม้เดิมอยู่** — fail-closed คือหยุดทำสิ่งใหม่ ไม่ใช่ปลดสิ่งที่ป้องกันอยู่

---

**เกี่ยวข้อง:** [ADR 3](0003-broker-interface-not-a-venue.md) · [ADR 7](0007-automatic-orders-under-risk-limits.md) · [ADR 9](0009-paper-and-live-are-profiles.md) · [spec/06](../spec/06-risk-and-execution.md)

---

[← สารบัญ ADR ทั้งหมด](../decisions.md)
