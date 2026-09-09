# ADR 3 · ส่งคำสั่งผ่าน Broker interface ไม่ผูก venue

**สถานะ:** accepted

ยังไม่ตัดสินว่าจะเทรดที่ Binance หรือ Maxbit จึงกันไว้ด้วย interface

ตลาดคือ **USDT-M perpetual futures** ไม่ใช่ spot — venue ที่เลือกต้องมีตลาดนี้

**ผลตามมา:** ถ้าเลือก venue ที่ต่างจากแหล่งข้อมูล จะมี basis risk และ slippage เพิ่มที่ต้องจัดการ

---

[← สารบัญ ADR ทั้งหมด](../decisions.md)
