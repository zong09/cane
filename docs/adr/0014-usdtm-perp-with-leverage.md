# ADR 14 · ตลาดคือ USDT-M perpetual futures มี leverage

**สถานะ:** **ถูกแทนบางส่วนโดย [ADR 26](0026-market-is-per-symbol.md)** — ยังจริงสำหรับเหรียญบน `usdtm_perp` เท่านั้น

> **ถูกแทนบางส่วนแล้วโดย [ADR 26](0026-market-is-per-symbol.md)** — ข้อนี้ยังจริงสำหรับ
> เหรียญที่อยู่บน `usdtm_perp` แต่ไม่จริงในฐานะคำอธิบายของทั้งระบบอีกแล้ว ระบบเทรด `spot` ได้ด้วย

design handoff ยืนอยู่บนตลาดนี้ทั้งชุด (`market`, `leverage`, `max_leverage`, `min_liq_buffer_pct`, isolated + one-way, funding รายเหรียญ) และเจ้าของระบบยืนยันเลือกทางนี้ สเปกฉบับแรกเขียนว่า "ไม่ใช้ leverage" ซึ่งเป็นคนละระบบกัน

**ผลตามมา:** ไม้ถูกปิดโดย exchange ได้เองที่ราคา liquidation **แม้ยังไม่เกิดสัญญาณฝั่งตรงข้าม** — ขัดกับหลัก "ออกที่สัญญาณฝั่งตรงข้ามอย่างเดียว" ของเอกสารต้นทางโดยตรง ระบบกันด้วยตรรกะสัญญาณไม่ได้ กันได้ทางเดียวคือ `min_liq_buffer_pct` ซึ่งต้อง fail-closed อย่างเคร่งครัด: คำนวณระยะไม่ได้ = ไม่เปิด

---

**เกี่ยวข้อง:** [ADR 15](0015-both-sides-long-and-short.md) · [ADR 26](0026-market-is-per-symbol.md) · [spec/06](../spec/06-risk-and-execution.md)

---

[← สารบัญ ADR ทั้งหมด](../decisions.md)
