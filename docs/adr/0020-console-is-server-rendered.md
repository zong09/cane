# ADR 20 · คอนโซลเป็น FastAPI + Jinja2 + HTMX ไม่ใช่ SPA

**สถานะ:** accepted

design handoff ชุด v3 แนะนำ React + TypeScript + Vite โดยให้เหตุผลว่า prototype เขียนเป็น
React เชิงโครงสร้าง จึง port ตรงที่สุด เจ้าของระบบ **ยืนยันทางเดิม**

เหตุผล: คอนโซลนี้เกือบทั้งหมดเป็น read-only view ของ state ที่ engine เขียนไว้ (`decisions.jsonl`,
`state/*.json`, ไฟล์ profile) การเพิ่ม build step กับภาษาที่สองเข้ามาในระบบที่มี deployable เดียว
คือการเพิ่มพื้นผิวที่ต้องดูแลโดยไม่ได้ตอบโจทย์ของงาน ส่วน fidelity สูงเป็นเรื่องของ CSS
ซึ่งทำได้เท่ากันทั้งสองทาง

**ผลตามมา:** interaction ที่ prototype ทำด้วย component state ต้องออกแบบใหม่เป็น server-rendered
partial — permission matrix แบบ draft, step-up modal, แผงแจ้งเตือน, accordion ฟอร์มคีย์ ทั้งหมดนี้
เป็น HTMX swap ไม่ใช่ client state ตรงไหนที่ต้องมี JS จริงๆ ให้เขียนเป็นสคริปต์เล็กแยก
ไม่ใช่ดึง framework เข้ามา

---

[← สารบัญ ADR ทั้งหมด](../decisions.md)
