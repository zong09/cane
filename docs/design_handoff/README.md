# Handoff: cane Console — ชุดเต็ม (light theme)

เอกสารฉบับเดียวจบ ครอบคลุมงานออกแบบคอนโซล cane ทั้งหมด: ทุกหน้าจอ, การแยกโหมด live/paper และการแจ้งเตือน
ไม่มีภาคผนวก ไม่มีธีมมืด — ทุกค่าสีในเอกสารนี้คือธีมสว่างที่ใช้จริง

---

## 1. Overview

คอนโซลควบคุมบอทเทรด `cane` — บอทตัดสินใจซื้อขายจาก **CDC Action Zone บนแท่งรายวันที่ปิดแล้ว** โดยมี Confluence Judge (LLM) เป็นตัวปรับ *ขนาดไม้* ไม่ใช่ตัวตัดสินใจเข้า-ออก

ตลาดเป็น **USDT-M perpetual เปิดได้ทั้ง long และ short** ในโหมดทางเดียวต่อเหรียญ (one-way, isolated margin) — สัญญาณชุดเดียวอ่านสองฝั่ง: GREEN = จุดเปิด long, RED = จุดเปิด short การกลับข้างเกิดในแท่งเดียวเป็นสองขา (ปิดฝั่งเดิม → เปิดฝั่งใหม่)

คอนโซลทำ 4 อย่าง

1. **แสดงว่าบอทกำลังคิดอะไรอยู่** — โซน สัญญาณทั้งสองฝั่ง สถานะที่ถือ เลเวอเรจ ราคา liquidation
2. **อธิบายย้อนหลังได้ว่าทำไมลงไม้ขนาดนั้นฝั่งไหน** — DecisionRecord ทุกแท่งทุก symbol รวมกรณี "ไม่ทำอะไร" และกรณีกลับข้างที่มีสองขาในเรคคอร์ดเดียว
3. **ให้คนหยุดบอทได้ทันที** — kill switch, dry_run, ปิดฝั่ง short, risk limit, สิทธิ์ต่อ role, start/stop engine แยกตามโหมด
4. **เตือนออกนอกคอนโซล** — LINE / Telegram เปิดปิดแยกช่องทางและแยกโหมด

ผู้ใช้: เจ้าของพอร์ต (Owner) + ทีมที่ดูแลบอทวันต่อวัน (Admin/Trader) + คนอ่านอย่างเดียว (Viewer/Auditor)
ภาษาในอินเทอร์เฟซเป็น **ภาษาไทย** ยกเว้นศัพท์ที่ตรงกับสเปกและไฟล์ config (`longcond`, `shortcond`, `zone`, `dry_run`, `allow_short`, `bucket_quote_long`, `leverage`) ซึ่งคงภาษาอังกฤษโดยเจตนา

---

## 2. About the design files

`design/cane Console Light.dc.html` คือ **design reference ที่เขียนเป็น HTML** — prototype ที่แสดงหน้าตาและพฤติกรรมที่ต้องการ **ไม่ใช่ production code ที่จะ copy ไปใช้ตรงๆ**

งานคือ **สร้างหน้าตานี้ขึ้นใหม่ในโปรเจกต์จริง** ตาม pattern และ library ที่โปรเจกต์นั้นใช้อยู่ `support.js` เป็น runtime ของ prototype เท่านั้น — ไม่ต้อง port เปิดไฟล์ `.dc.html` ในเบราว์เซอร์ตรงๆ ได้ (ต้องมี `support.js` และโฟลเดอร์ `assets/` อยู่ข้างๆ)

สถานะโค้ดเบสตอนนี้ (โฟลเดอร์ `cane`): เป็น **Python package** (`src/cane/` มีแค่ `config/settings.py` และ `log.py`) — **ยังไม่มี frontend และยังไม่มี HTTP/API layer** ผู้พัฒนาต้อง

- frontend stack ที่ทีมเลือกแล้วคือ **FastAPI + Jinja2 + HTMX** — คอนโซลเกือบทั้งหมดเป็น read-only view ของ state ที่ engine เขียนไว้ การเพิ่ม build step กับภาษาที่สองจึงไม่คุ้ม ส่วน fidelity ยังทำตาม pixel ได้เท่าเดิมเพราะเป็นเรื่อง CSS
- interaction ที่ต้นแบบทำด้วย component state (permission matrix แบบ draft, step-up modal, แผงแจ้งเตือน, accordion ฟอร์มคีย์) ในของจริงจะเป็น server-rendered partial ที่ swap ด้วย HTMX
- สร้าง read API ฝั่ง Python ด้วย **FastAPI** — คอนโซลเกือบทั้งหมดเป็น read-only view ของ state ที่ engine เขียนไว้ (`decisions.jsonl`, `state/killswitch.json`, ไฟล์ profile TOML, position จาก exchange)
- เขียน/แก้จริงมีเฉพาะ: kill switch, dry_run, allow_short, start/stop engine, สลับโหมด, จัดการคู่เหรียญ, จัดการผู้ใช้/สิทธิ์/session, ตั้งค่าการแจ้งเตือน — ทุกอันที่กระทบ live ต้องผ่าน 2FA step-up
- ฝั่ง exchange ต้องตั้ง `defaultType = future`, `marginMode = isolated`, `positionMode = one-way` ให้ตรงกับที่ UI สมมติไว้ทุกหน้า

### ไฟล์ในชุดนี้

| ไฟล์ | สถานะ | ใช้ทำอะไร |
|---|---|---|
| `design/cane Console Light.dc.html` | **สเปกที่ต้องทำ** | คอนโซลเต็มทุกหน้าจอ · live/paper · การแจ้งเตือน |
| `design/cane Logo 1b.dc.html` | **logo kit ที่เลือก** | mark, lockup, ขนาด 32/24/16 (แบบ Crook) |
| `design/cane-mark.svg` · `cane-mark-mono.svg` | asset | โลโก้ mark |
| `design/assets/*.svg` | mock | กราฟ sparkline ribbon ที่ไฟล์สเปกอ้างถึง — **ไม่ใช่ production asset** |
| `design/support.js` | runtime | ของ prototype เท่านั้น ไม่ต้อง port |

---

## 3. Fidelity

**High-fidelity.** สี ตัวอักษร ระยะ และ copy เป็นค่าสุดท้าย ให้ทำตาม pixel ให้ใกล้ที่สุดด้วย component library ที่โปรเจกต์เลือก

ข้อยกเว้นที่เป็น lofi และต้องคุยก่อนทำ

- กราฟราคาทั้งหมดเป็น **static SVG** วาดจากข้อมูลตัวอย่าง — ของจริงต้องเป็น chart component ที่รับข้อมูลจริง (หัวข้อ 11)
- ตัวเลขทุกตัวเป็น mock data
- หน้า `ตั้งค่า` แสดง TOML แบบ read-only + validation error list; **ยังไม่มี design ของ inline editor**
- สูตรขนาดไม้ฝั่ง short ใช้ additive ชุดเดียวกับ long (base 5 + 20 ต่อปัจจัย) เปลี่ยนแค่ชุดปัจจัย
- การแจ้งเตือนยังไม่มี loading/error state และยังไม่มี validation ของฟิลด์คีย์ (หัวข้อ 8.5)

---

## 4. โครงหน้าจอ

Canvas กว้าง **1440px คงที่** ไม่มี responsive breakpoint (ออกแบบสำหรับ desktop) ถ้าต้องรองรับจอเล็ก ให้คุยเรื่อง breakpoint แยก

```
┌──────────────────────────────────────────────────────────┐
│ [ rail 236px ] │ [ top bar + กระดิ่ง ]                    │
│                ├───────────────────────────────────────  │
│                │ [ main · padding 28px 34px 40px ]        │
├──────────────────────────────────────────────────────────┤
│ [ footer · เวอร์ชัน · build · env · deploy ]              │
└──────────────────────────────────────────────────────────┘
```

- **body** bg `#eaf1f7` + `linear-gradient(155deg,#c3d8e8 0%,#dbe8f2 34%,#eff5f9 68%,#ffffff 100%)` `background-attachment:fixed`
- **rail** `width 236px`, bg `linear-gradient(160deg,#e6eff7 0%,#f4f8fb 58%,#ffffff 100%)`, `border-right 1px solid #d5e1ea`, `padding 26px 18px 22px`, flex column
- **top bar** `padding 10px 34px`, bg `linear-gradient(180deg,#eef4f9,#ffffff)`, `border-bottom 1px solid #dde7ee`
- **main** แต่ละหน้าจอ `padding 28px 34px 40px`, `border-top 2px solid #e4eff8`
- **footer** แถบบางท้ายหน้า: ซ้าย `cane <appVersion>` · `build <buildNo>`; ขวา `<envLabel>` · `deploy <deployedAt>`
- หน้า Login ไม่มี rail / top bar / footer

| # | screen id | ชื่อในเมนู | กลุ่ม |
|---|---|---|---|
| 0 | `login` | เข้าสู่ระบบ | — |
| 1 | `overview` | ภาพรวม | MODE |
| 2 | `symbols` | คู่เหรียญ | MODE |
| 3 | `risk` | ความเสี่ยง | MODE |
| 4 | `log` | บันทึก | MODE |
| 5 | `report` | รายงาน | MODE |
| 6 | `config` | ตั้งค่า | MODE |
| 7 | `users` | ผู้ใช้ | ทั้งระบบ |
| 8 | `symbol` | หน้ารายเหรียญ (3 แท็บ) | เข้าจากตาราง/rail |

---

## 5. Rail

จากบนลงล่าง

1. **โลโก้ lockup** `padding 0 8px 24px` — mark `30×30` bg `linear-gradient(135deg,#0f6fb5 0%,#30AFFF 55%,#7fd6ff 100%)` ไอคอนขาว `21×21` + wordmark `cane` (Mono 600 15px, letter-spacing .22em, uppercase) + sub `CDC ACTION ZONE` (Mono 400 10px, `#5f7b8a`, letter-spacing .06em)
2. **แถบสีโซน** `height 3px`, `margin 0 8px 20px` — 7 ช่อง flex `2:1:1:1:1:2:1` สี `#17a866 #2f7fe0 #92c8f2 #dfbb2a #ef8b3c #e05252 #c3d4e0`
3. **กลุ่ม MODE** — หัวกลุ่ม `MODE` (Mono 500 10px `#5f7b8a` letter-spacing .08em) + subtitle `ขอบเขต live.toml` / `ขอบเขต paper.toml` (`400 10.5px` `#8ea3af`)
   รายการเลข `01`–`06`: ภาพรวม · คู่เหรียญ · ความเสี่ยง · บันทึก · รายงาน · ตั้งค่า
   - active: bg `linear-gradient(90deg,#cfe3f4 0%,#eaf4fd 100%)`, text `#1478c4` 600, แถบซ้าย `3px × 15px` ไล่สีน้ำเงิน, เลข `#0f6fb5`
   - inactive: transparent, `#3d5a6b` 400, เลข `#6f8896`, hover bg `#e8eef3`
   - `padding 10px 12px`, `gap 10px`, radius 0
4. **กลุ่ม ทั้งระบบ** — คั่น `border-top 1px solid #dde7ee`, subtitle `ไม่ผูกกับโหมด`, ใช้ `·` แทนเลข; รายการเดียว: ผู้ใช้
   บัญชี สิทธิ์ และ session ใช้ร่วมทั้งสองโหมด ไม่ขึ้นกับโหมดหรือสถานะ engine
5. **Symbols section** — คั่น `border-top 1px solid #dde7ee`, `padding-top 18px`, `margin-top 22px`
   - หัว: `SYMBOLS` + จำนวน `N คู่` + ปุ่ม `จัดการ` (border `1px solid #c9d8e3`, `400 10px`, hover border `#0f6fb5` text `#1478c4`)
   - ช่องค้นหา `ค้นหาคู่เหรียญ` — filter แบบ substring, ไม่พบ → `ไม่พบคู่ที่ค้นหา`
   - แต่ละแถว `padding 9px 12px` gap 9px: จุดสีโซน `8×8` · ชื่อคู่ · ป้ายฝั่งที่ถือ `L` (Mono 600 9.5px `#0f7a4c`) / `S` (`#c0392f`) · สามเหลี่ยมสัญญาณ (ขึ้นเขียว `#17a866` = long, ลงแดง `#e05252` = short) · ราคาปิด (Mono 11px)
   - active row: bg `linear-gradient(90deg,#cfe3f4,#eaf4fd)`, text `#1478c4` 600 · inactive hover `#e8eef3`
   - คู่ที่เพิ่มใหม่ยังไม่มีข้อมูล: ราคา `รอข้อมูล`, zone BLACK
   - สามเหลี่ยม short ไม่แสดงเมื่อ `allowShort = false`
6. **Spacer** `flex:1`
7. **PROFILE card** (หัวข้อ 6) — สลับโหมด, สถานะ engine, แถวผู้ใช้
   - แถวผู้ใช้: avatar `26×26` bg `linear-gradient(150deg,#cfe3f4,#eaf4fd)` text `#1478c4` initials + ชื่อ + role (`OWNER`, Mono 500 9.5px `#1478c4`) + ปุ่ม `ออก` (hover border/text `#c0392f`)

---

## 6. โหมด live / paper

### 6.1 ตัวสลับโหมด (PROFILE card)
- ตัวควบคุมสองช่อง เต็มความกว้างการ์ด, border 1px, radius 0 ช่อง active ถูกเติมสี ช่องที่ไม่ active เป็นปุ่มพื้นโปร่ง
  - LIVE active: `linear-gradient(135deg,#b5352b 0%,#e8776c 100%)` ตัวอักษรขาว; ขอบการ์ด `#f3d2ce`
  - PAPER active: `linear-gradient(135deg,#0f7a4c 0%,#48bd88 100%)` ตัวอักษรขาว; ขอบการ์ด `#b9dfca`
  - ตัวอักษร `600 11px 'IBM Plex Mono'` letter-spacing .13em padding `7px 0`; ช่อง inactive `500` `#6f8896`, hover bg `#e8eef3` + text สีของโหมดนั้น
- แถวป้ายใต้ตัวสลับ: ชื่อไฟล์โปรไฟล์ (`live.toml` / `paper.toml`, Mono 400 10.5px `#6f8896`) + ชิปสถานะ (`padding 2px 8px`, border 1px)
  - `dry-run` — bg `linear-gradient(160deg,#cbead9,#edf8f2)`, text `#0f7a4c`, border `#b9dfca`
  - `ยิงจริง` — bg `linear-gradient(160deg,#f8d6d3,#fdeeec)`, text `#c0392f`, border `#f3d2ce`
  - paper แสดง `จำลองทั้งหมด` (ชิปเขียว) เสมอ
- คำอธิบาย `400 11px/1.6` `#5f7b8a`: `USDT-M perp · เปิดได้ทั้ง long และ short` / `แท่งล่าสุด 2026-08-26 · รอบถัดไป 22h 11m`
- **paper → live ต้อง step-up** — modal กว้าง 452px, แถบบน `3px` ไล่สีแดง, ชิป `PAPER` → `LIVE`, หัวข้อ `สลับไปโปรไฟล์ live` (600 17px), คำอธิบาย `คอนโซลจะอ่านค่าจาก live.toml และผูกกับบัญชี binance จริง · ตำแหน่ง ตัวเลขความเสี่ยง และรายงานของ paper จะไม่แสดงหลังจากนี้`, กล่องเตือนแดง `live.toml ยังโหลดไม่ผ่าน 4 ข้อ — สลับได้แต่ระบบจะยังไม่เทรดจนกว่าจะแก้ครบ`, ช่อง TOTP 6 หลัก (`500 17px Mono`, letter-spacing .34em), ปุ่ม `สลับไป live` disabled จนครบ 6 หลัก, ปุ่มรอง `อยู่ paper ต่อ`
- **live → paper ทันที ไม่ต้องยืนยัน**

### 6.2 Engine เปิด/ปิด — คนละตัวต่อโหมด
ใต้คำอธิบายโปรไฟล์ คั่นด้วย `1px solid #dde7ee`
- บรรทัดสถานะ: จุดสี่เหลี่ยม 7px + ป้าย — running: แดง `#c0392f` (live) / เขียว `#0f7a4c` (paper) ข้อความ `engine live · running`; stopped: จุด `#c3d4e0`, ข้อความ `#6f8896` `engine live · stopped`
- ปุ่มเต็มความกว้าง `padding 9px 0` `600 12px`: running → เติมสีไล่ตามโหมด ป้าย `stop engine`; stopped → พื้นขาว ขอบ 1px และตัวอักษรสีของโหมด ป้าย `start engine`
- บรรทัดเล็กใต้ปุ่ม (`400 10.5px Mono` `#8ea3af`) บอก engine ของอีกโหมด: `paper: running` / `live: stopped`
- **engine สองตัวเป็น state อิสระและเดินต่อเมื่อสลับโหมด** — การเปลี่ยนโหมดที่ดูอยู่ต้องไม่ start/stop engine ใดๆ

### 6.3 ข้อมูลที่ผูกกับโหมด
หน้าจอในกลุ่ม MODE อ่านจากโปรไฟล์ที่ active ค่าที่ต่างกันใน prototype

| ที่ | live | paper |
|---|---|---|
| ภาพรวม · ขาดทุนวันนี้ | `0.8 / 3.0%` (bar 27%) | `1.6 / 5.0%` (bar 32%) |
| ภาพรวม · breaker | `แพ้ติดกัน 1 จาก 2` | `0 จาก 4` |
| ภาพรวม · มาร์จิ้นที่ใช้ | `17 / 300 USDT` · เฉลี่ย `1.9x` | `17 / 1000 USDT` · เฉลี่ย `1.4x` |
| รายงาน · บรรทัดท้าย | `… USDT-M perp 1–2x · โปรไฟล์ live` | `… 1x · โปรไฟล์ paper · seed 10000.00` |

เพดานและตัวนับ breaker ต้องมาจากไฟล์โปรไฟล์ **ห้าม hardcode ต่อหน้าจอ** ในของจริงทุกตัวเลขบนหน้าจอกลุ่มนี้เป็นค่าต่อโปรไฟล์

---

## 7. Login

`min-height:100vh`, `display:grid; place-items:center`, `padding 70px 0`; การ์ดกว้าง `472px`
โลโก้ lockup เหนือการ์ด · การ์ด bg `linear-gradient(160deg,#e6eff7 0%,#f4f8fb 58%,#ffffff 100%)` border `1px solid #d5e1ea` radius 0 · แถบสีโซน 3px บนสุด · เนื้อ `padding 34px 38px 32px`

| state | เนื้อหา |
|---|---|
| `password` | `ขั้นที่ 1 จาก 2` (Mono 500 10px `#5f7b8a`) · H1 `เข้าสู่ระบบ` (600 23px) · `คอนโซลนี้ควบคุมบอทที่ส่งออเดอร์จริง ทุกการเข้าใช้และทุกคำสั่งถูกบันทึกพร้อมชื่อผู้ใช้` · ช่องอีเมล + รหัสผ่าน · ปุ่ม `ดำเนินการต่อ` + hint |
| `totp` | `ขั้นที่ 2 จาก 2` · H1 `ยืนยันด้วยรหัส 6 หลัก` · อีเมล + ปุ่ม `เปลี่ยน` · ช่องรหัส (Mono 500 26px, letter-spacing .34em, center) · `รหัสจากแอป Authenticator ที่ผูกกับบัญชีนี้ · เปลี่ยนทุก 30 วินาที` · ปุ่ม `ยืนยันและเข้าสู่ระบบ` · footer `เข้าแอปไม่ได้` / ลิงก์ `ใช้รหัสสำรอง` / `เหลือ 2 ครั้ง` |
| `locked` | banner bg `linear-gradient(160deg,#f8d6d3,#fdeeec)` border `#f3d2ce` + ไอคอน `!` 26×26 ไล่สีแดง: `บัญชีถูกล็อกชั่วคราว` (`#c0392f`) + `ใส่รหัสผิด 5 ครั้งติดกัน · ลองใหม่ได้ใน 14:32` (`#b04a4a`) · `การล็อกเกิดที่ระดับบัญชี ไม่ใช่ที่อุปกรณ์ — เปลี่ยนเครื่องหรือล้างคุกกี้แล้วก็ยังล็อกอยู่ ถ้าต้องเข้าใช้ก่อนหมดเวลา ให้ Owner หรือ Admin ปลดล็อกจากหน้าผู้ใช้` · `ความพยายามล่าสุด 2026-08-28 09:41 · 203.0.113.44 · Bangkok` · ปุ่ม `กลับไปหน้าเข้าสู่ระบบ` |

**Input style** `padding 12px 14px`, border `1px solid #d5e1ea`, radius 1px, bg `linear-gradient(180deg,#eef4f9,#ffffff)`; focus → border `#0f6fb5`, bg `linear-gradient(160deg,#e6eff7,#f4f8fb,#ffffff)`
**ปุ่มหลัก** bg `linear-gradient(135deg,#0f6fb5 0%,#30AFFF 55%,#7fd6ff 100%)` ขาว `600 13.5px`; disabled → border `1px solid #d5e1ea`, bg `linear-gradient(180deg,#eef4f9,#ffffff)`, text `#6f8896`

**Validation** ขั้น 1 เปิดเมื่ออีเมลมี `@` และรหัสผ่านไม่ว่าง; ขั้น 2 เปิดเมื่อรหัสยาว 6 หลัก (กรองเฉพาะตัวเลข, maxLength 6)
**Hint** ไม่มี `@` → `ใส่อีเมลที่ Admin เชิญไว้` · รหัสผ่านว่าง → `ใส่รหัสผ่าน แล้วจะขอรหัส 6 หลักในขั้นถัดไป` · ครบ → `ขั้นถัดไปจะขอรหัส 6 หลักจากแอป Authenticator`
Footer นอกการ์ด: `cane 0.4.2 · โปรไฟล์ live` + ลิงก์ dev `ดูหน้าจอเมื่อบัญชีถูกล็อก`


---

## 8. การแจ้งเตือน

### 8.1 Top bar + กระดิ่ง
element แรกของคอลัมน์เนื้อหาหลัก อยู่เหนือทุกหน้าจอในแอป ไม่แสดงในหน้า login
`display:flex; align-items:center; gap:12px; padding:10px 34px`, bg `linear-gradient(180deg,#eef4f9,#ffffff)`, `border-bottom 1px solid #dde7ee`

| ตำแหน่ง | เนื้อหา | สไตล์ |
|---|---|---|
| ซ้าย | `โหมด LIVE` / `โหมด PAPER` | `500 10px Mono`, letter-spacing .08em, `#5f7b8a` |
| ถัดมา | `live 2/2 ช่องทาง · paper 1/2 ช่องทาง` | `400 11px Mono`, `#8ea3af` |
| ขวาสุด | ปุ่มกระดิ่ง | 34×34, border `1px solid #c9d8e3`, radius 1px, bg `linear-gradient(160deg,#e6eff7 0%,#f4f8fb 58%,#ffffff 100%)`, ไอคอน `#3d5a6b` |

ปุ่มกระดิ่ง: ไอคอน SVG 17×17 stroke 1.7 (ระฆัง + เส้นโค้งลูกกระดิ่ง) · hover border `#0f6fb5` ไอคอน `#1478c4` · badge absolute `top:-6px right:-6px`, min-width 17px height 17px padding `0 4px`, bg `linear-gradient(135deg,#b5352b,#e8776c)`, `600 10px Mono` ขาว มุมฉาก — ซ่อนเมื่อ = 0

### 8.2 แผงรายการ (dropdown)
`position:absolute; top:calc(100% + 9px); right:0; width:398px; z-index:60`
bg `linear-gradient(160deg,#e6eff7 0%,#f4f8fb 58%,#ffffff 100%)`, border `1px solid #c9d8e3`, shadow `0 18px 40px rgba(14,30,41,.16)`, มุมฉาก

- **แถบสีบนสุด** 3px แบ่ง flex 2/1/1/2 สี `#17a866 #2f7fe0 #dfbb2a #e05252`
- **หัวแผง** `padding 14px 18px`, `border-bottom 1px solid #dde7ee` — "การแจ้งเตือน" `600 13px` + ป้ายโหมด `400 11px Mono` `#6f8896` + ปุ่มลิงก์ "อ่านทั้งหมด" `#1478c4` 11.5px underline
- **รายการ** `max-height:370px; overflow-y:auto` แต่ละแถวเป็น `<button>` เต็มความกว้าง `padding 13px 18px`, `border-bottom 1px solid #eef2f6`
  - แถบ accent ซ้าย 3px: `#e05252` เสี่ยง/ยิงจริง · `#dfbb2a` เตือน · `#2f7fe0` ข้อมูล · `#17a866` สำเร็จ/สรุป
  - แถวยังไม่อ่าน bg `#f3f8fc`, อ่านแล้ว transparent, hover `#e8eef3`
  - บรรทัดบน: ช่องทาง (`500 9.5px Mono` `#5f7b8a` letter-spacing .09em) · เวลา (`400 10.5px Mono` `#8ea3af`) · จุดแดง 6×6 `#e05252` เมื่อยังไม่อ่าน
  - หัวข้อ `500 13px/1.45` `#0e1e29` · รายละเอียด `400 11.5px/1.6 Mono/Thai` `#5f7b8a`
  - ว่าง → "ยังไม่มีการแจ้งเตือนในโหมดนี้" กลางแผง `padding 26px 18px` `#6f8896`
- **ท้ายแผง** `padding 12px 18px`, `border-top 1px solid #dde7ee`, bg `linear-gradient(180deg,#eef4f9,#ffffff)` — ซ้าย "อีก N รายการในโหมด paper/live" หรือ "ไม่มีค้างในอีกโหมด"; ขวา ปุ่ม "ตั้งค่าการแจ้งเตือน" → หน้าตั้งค่า + ปิดแผง

**ข้อมูลตัวอย่างในต้นแบบ** (โหมด live 5 รายการ / paper 2 รายการ) — ส่งคำสั่ง long BTC/USDT 45% · live.toml โหลดไม่ผ่าน 4 ข้อ · SOL/USDT ขาดทุน -11.0% รอกลับข้าง · ผู้ใช้เข้าสู่ระบบ · ปิดแท่งรายวันประมวลผลครบ

### 8.3 การ์ดตั้งค่าการแจ้งเตือน (ท้ายหน้า `ตั้งค่า`)
อยู่ **นอกแท็บ live.toml / paper.toml** เพราะคุมทั้งสองโหมดพร้อมกัน (`margin-top:28px`, id `notif-settings`)
border `1px solid #d5e1ea`, bg `linear-gradient(160deg,#e6eff7 0%,#f4f8fb 58%,#ffffff 100%)`

- **หัวการ์ด** `padding 15px 22px`, bg `linear-gradient(180deg,#eef4f9,#ffffff)`, `border-bottom 1px solid #e3ebf1` — "การแจ้งเตือน" `600 13.5px` + "เปิด/ปิดแยกรายช่องทาง และแยกโหมด live กับ paper · ไม่ผูกกับแท็บไฟล์ด้านบน" `400 12px` `#5f7b8a` + สรุปช่องทางฝั่งขวา
- **ตาราง** grid `minmax(0,1fr) 200px 96px 96px 182px`, gap 14px
  หัวตาราง `padding 12px 22px` `500 10.5px Mono` `#5f7b8a`: ช่องทาง / ปลายทาง / **LIVE** (`#c0392f` กลาง) / **PAPER** (`#0f7a4c` กลาง) / (ว่าง)
- **แถวช่องทาง** `padding 17px 22px`, `border-bottom 1px solid #dde7ee`
  - ป้ายสี่เหลี่ยม 28×28 อักษรย่อขาว `600 11px Mono` — LINE `LN` bg `linear-gradient(135deg,#0f7a4c,#48bd88)`; Telegram `TG` bg `linear-gradient(135deg,#0f6fb5,#30AFFF,#7fd6ff)`
  - ชื่อ `600 13.5px` + คำอธิบายย่อย `400 11.5px` `#5f7b8a` — LINE: "Messaging API · กลุ่ม cane-alerts" / Telegram: "Bot API · @cane_desk_bot"
  - ปลายทาง `400 12px Mono` `#3d5a6b` ellipsis — `group · Uf3a…c81` / `chat_id -100…4472`
  - toggle LIVE / PAPER + ปุ่ม "คีย์" (สลับเป็น "ปิด" เมื่อกาง) และ "ทดสอบส่ง" — `padding 7px 11px`, border `1px solid #c9d8e3`, `400 11.5px` `#3d5a6b`, hover border `#0f6fb5` text `#1478c4`; สถานะกาง = border `#0f6fb5` bg `#e6eff7` text `#1478c4` weight 500
- **Toggle switch** 44×24, padding 2.5px, radius 13px, ปุ่มกลม 19×19 ขาว
  - เปิด LIVE: bg `linear-gradient(135deg,#b5352b,#e8776c)` ปุ่มชิดขวา · เปิด PAPER: bg `linear-gradient(135deg,#0f7a4c,#48bd88)` ปุ่มชิดขวา
  - ปิด: bg `#e3ebf1` border `1px solid #d5e1ea` ปุ่มชิดซ้าย; hover border `#c0392f` (live) / `#0f7a4c` (paper)
  - `title` บอกผลของการกด เช่น "ปิดแจ้งเตือน live"
  - ค่าตั้งต้น — LINE: live เปิด / paper ปิด · Telegram: live เปิด / paper เปิด
- **ท้ายการ์ด** `padding 16px 22px` `400 12.5px/1.75` `#5f7b8a`: "ปิดช่องทางในโหมดใดโหมดหนึ่งไม่กระทบอีกโหมด — เช่น ปิด paper ไว้เพื่อไม่ให้เสียงเตือนจากการทดลองปนกับไม้จริง · ค่าที่กรอกถูกเขียนลง .env ฝั่งเซิร์ฟเวอร์ · คอนโซลอ่านกลับมาแสดงเป็นค่าปิดบัง ต้องกดแสดงค่าถึงจะเห็นเต็ม และการกดถูกบันทึกใน audit log"
  ขวา: ชิปผลทดสอบ (bg `linear-gradient(160deg,#cbead9,#edf8f2)`, border `#b9dfca`, text `#0f7a4c`) — "ส่งข้อความทดสอบไปที่ <ช่องทาง> แล้ว"

### 8.4 ฟอร์มคีย์ (กางใต้แถว ทีละช่องทาง)
`padding 20px 22px 22px`, bg `linear-gradient(180deg,#eef4f9,#ffffff)`, `border-bottom 1px solid #dde7ee`

- **หัวฟอร์ม** "คีย์และปลายทางของ <ชื่อ>" `500 10px Mono` uppercase letter-spacing .09em `#5f7b8a`
  ขวา: ปุ่ม "แสดงค่า" (border `#c9d8e3`, `#5f7b8a`) ↔ "ซ่อนค่า" (bg `linear-gradient(160deg,#f8d6d3,#fdeeec)`, border `#e2b4ae`, text `#c0392f`) — คุม `type` ของ input ที่เป็น secret ทั้งฟอร์มของช่องทางนั้น
- **Webhook URL (เฉพาะ LINE, แถวบนสุด)** grid `230px minmax(0,1fr)` gap 16px, `margin-bottom 16px; padding-bottom 16px; border-bottom 1px solid #dde7ee`
  - ซ้าย: "Webhook URL" `500 12.5px` + "วางค่านี้ใน LINE Developers Console" `400 10.5px` `#8ea3af`
  - ขวา: กล่องอ่านอย่างเดียว (ไม่ใช่ input) `padding 10px 13px`, border `1px solid #d5e1ea`, bg `#fff`, `400 12.5px Mono` `#3d5a6b`, ellipsis — ค่าตัวอย่าง `https://api.cane.company.co.th/hooks/line/6f21c9`
  - ปุ่ม "คัดลอก" → กดแล้วเป็น "คัดลอกแล้ว" (bg `linear-gradient(160deg,#cbead9,#edf8f2)`, border `#b9dfca`, text `#0f7a4c`) กลับเป็นปกติใน 1800ms
- **ช่องกรอก** grid `230px minmax(0,1fr)` gap 16px, ระยะแนวตั้ง 14px, กว้างสุด 720px
  - label `500 12.5px` `#0e1e29` + ชื่อ env var ใต้ label `400 10.5px Mono` `#8ea3af`
  - input `padding 10px 13px`, border `1px solid #d5e1ea`, radius 1px, bg `#fff`, `400 12.5px Mono/Thai`; focus → border `#0f6fb5`, bg `linear-gradient(160deg,#e6eff7,#f4f8fb,#ffffff)`

| ช่องทาง | ฟิลด์ | env var | secret | placeholder |
|---|---|---|---|---|
| LINE | Channel access token | `CANE_LINE_TOKEN` | ✓ | วางค่าจาก LINE Developers Console |
| LINE | Channel secret | `CANE_LINE_SECRET` | ✓ | 32 ตัวอักษร |
| LINE | ปลายทาง (group / room / user id) | `CANE_LINE_TO` | — | Uf3a…c81 |
| Telegram | Bot token | `CANE_TELEGRAM_TOKEN` | ✓ | 123456789:AA… |
| Telegram | chat_id | `CANE_TELEGRAM_CHAT_ID` | — | -1001234564472 |
| Telegram | message_thread_id (ถ้าใช้ topic) | `CANE_TELEGRAM_THREAD_ID` | — | เว้นว่างได้ |

- **ท้ายฟอร์ม** `border-top 1px solid #dde7ee`, `padding-top 16px`
  - ซ้าย ข้อความช่วยเหลือ `400 12px/1.7` `#5f7b8a` — LINE: "สร้าง channel access token ที่ LINE Developers Console แล้วเชิญบอทเข้ากลุ่มก่อนจึงจะส่งข้อความเข้ากลุ่มได้" / Telegram: "ขอ token จาก @BotFather แล้วเพิ่มบอทเข้ากลุ่ม จึงจะอ่าน chat_id ออกมาได้"
  - ขวา ปุ่มหลัก "บันทึกและทดสอบส่ง" `padding 9px 16px`, ไม่มี border, bg `linear-gradient(135deg,#0f6fb5,#30AFFF,#7fd6ff)`, ขาว `500 12.5px`, hover `#1478c4`

### 8.5 สิ่งที่ต้องเพิ่มตอน implement
- ปิดแผงเมื่อคลิกนอกพื้นที่ / กด Esc; focus trap และ `aria-expanded` บนปุ่มกระดิ่ง; `aria-live` เมื่อจำนวนที่ยังไม่ได้อ่านเปลี่ยน
- loading / error state ของการทดสอบส่ง (ต้นแบบมีแต่ success)
- validation: Telegram token `<digits>:<35+ chars>`, chat_id เป็นจำนวนเต็ม, LINE secret 32 ตัวอักษร
- **ยืนยันด้วย 2FA (step-up) ก่อนบันทึกคีย์ที่กระทบโหมด live** — ใช้ pattern step-up เดิมของคอนโซล
- คีย์เป็นชุดเดียวต่อช่องทาง ใช้ร่วมทั้ง live และ paper แยกกันเฉพาะสวิตช์เปิด/ปิด ถ้าธุรกิจต้องการบอทคนละตัวต่อโหมด ต้องขยายเป็น `{ [channel]: { [mode]: {...} } }` และเพิ่มแท็บโหมดในฟอร์มคีย์


---

## 9. หน้าจอทีละหน้า

### 9.1 ภาพรวม

1. **Header** — H1 `ภาพรวม` (600 25px, letter-spacing -.01em) + sub `N symbol · USDT-M perpetual · โหมดทางเดียวต่อเหรียญ · ตัดสินใจบนแท่งรายวันที่ปิดแล้วเท่านั้น` + ปุ่ม `เปิดบันทึกการตัดสินใจ` (border `1px solid #c9d8e3`, bg `linear-gradient(160deg,#e6eff7,#f4f8fb,#ffffff)`, hover border `#0f6fb5` text `#1478c4`)
2. **Cold start banner** (เมื่อ `showColdStart`) — bg `linear-gradient(160deg,#f6e7bd,#fdf8ea)` border `#f4e6bd` `padding 18px 22px`; ไอคอน `!` 34×34 `linear-gradient(135deg,#c8981a,#e9c247)` text `#4a3708`
   หัวข้อ `Cold start — บอทเพิ่งเปิดขึ้นมาขณะที่เทรนด์เดินไปแล้ว` (`#8a6a15`) · รายละเอียด `ETH/USDT เป็น bullish ตั้งแต่ 18 ส.ค. แต่แท่งล่าสุดไม่ใช่จุดสัญญาณฝั่ง long · ต้องเลือกเส้นทางก่อนจึงจะเข้าไม้ได้` (`#8f6c13`) · ปุ่ม `เลือกเส้นทาง` (bg `linear-gradient(135deg,#0e5b83,#3f9ec4)`) → ETH + แท็บ Cold start
3. **KPI strip 5 ช่อง** grid `repeat(5,1fr)` gap 0, border `1px solid #d5e1ea`, bg การ์ด, cell `padding 20px 22px` คั่น `border-right 1px solid #d5e1ea`, ตัวเลข Mono 600 30px/1

   | ช่อง | ค่า | บรรทัดล่าง |
   |---|---|---|
   | สัญญาณรอดำเนินการ | `2` + `รอเปิดไม้แท่งถัดไป` | `▲ long · BTC/USDT` (`#0f7a4c`) · `▼ short · SOL/USDT` (`#c0392f`) |
   | ถือสถานะอยู่ | `2` + `SOL · DOGE` | `long 2` (`#0f7a4c`) · `short 0` (`#5f7b8a`) |
   | ขาดทุนวันนี้ | `0.8 / 3.0%` | bar 6px track `#e3ebf1` fill ไล่สีน้ำเงิน 27% |
   | Kill switch | จุด 9×9 `#17a866` + `ปกติ` | `แพ้ติดกัน 1 จาก 2` |
   | มาร์จิ้นที่ใช้ | `17 / 300 USDT` | `notional 32.00 USDT` · `เลเวอเรจเฉลี่ย 1.9x` |

   เมื่อ `allowShort = false` ช่องแรกเหลือ `1` + `ฝั่ง short ปิดอยู่` (`#6f8896`)
4. **ตาราง symbol** — การ์ด bg `linear-gradient(160deg,#e6eff7,#f4f8fb,#ffffff)` border `1px solid #d5e1ea`
   grid `158px 88px 84px 96px 180px 168px 1fr 40px` gap 14px
   header `padding 15px 24px` bg `linear-gradient(180deg,#eef4f9,#ffffff)` `border-bottom 1px solid #e3ebf1` Mono 500 10.5px `#5f7b8a` letter-spacing .07em: `SYMBOL` `ZONE` `STATE` `ฝั่งที่ถือ` `85 แท่ง` `สัญญาณ` `สถานะไม้` (คอลัมน์ไทยใช้ Sans, letter-spacing 0)
   แถว `padding 19px 24px` `border-bottom 1px solid #dde7ee`; row tint: long signal `#eaf7f0` · short/กลับข้าง `#fdf0ee`
   เซลล์: ชื่อคู่ (600 15px) + `bucket 100.00` / zone chip (แถบซ้าย 2px + Mono 600 11px) / state `bullish|bearish` / chip ฝั่งที่ถือ `LONG`/`SHORT`/`FLAT` + `lev 2x` / sparkline `180×46` / สัญญาณ (สามเหลี่ยม + `เปิด long` / `เปิด short` + บรรทัดอธิบาย เช่น `ปิด long ก่อน แล้วกลับข้าง`) / สถานะไม้ (`รอส่ง long 45%` + `margin 45.00 · notional 90.00`) / ปุ่ม `→` 32×32
   4 แถวตัวอย่างครอบทุกกรณี — BTC long signal ยังไม่ถือ · ETH ตกรถ cold start · SOL ถือ long แล้วมี short signal (กลับข้าง) · DOGE ถือ long ไม่มีสัญญาณ (`long เท่านั้น`)
   เมื่อ `allowShort = false` แถว SOL เปลี่ยนเป็น `ปิด long เท่านั้น` (`#8f6c13`) + `ฝั่ง short ปิดอยู่ในโปรไฟล์`

### 9.2 เหรียญ (symbol detail)

**Header** ชื่อคู่ + zone chip + chip ฝั่งที่ถือ (`LONG 25%` bg เขียวอ่อน / `SHORT 25%` bg แดงอ่อน / `FLAT` bg `linear-gradient(180deg,#eef4f9,#ffffff)`) + sub `1d · binance USDT-M perp · isolated 2x · bucket long 100.00 / short 60.00 USDT · แท่ง 2026-08-26 00:00 UTC` + chip ขวา `golden test 500/500`

**Tabs** `กราฟและสัญญาณ` · `การตัดสินใจ` · `Cold start` — underline active `2.5px #30AFFF` weight 600 `#0e1e29`; inactive `#5f7b8a` 400 hover `#0e1e29`
จุดต่อท้าย label 7×7: เขียว `#17a866` = มี long signal · แดง `#e05252` = มี short signal · เขียว = เข้าเงื่อนไข cold start

#### 9.2a แท็บ กราฟและสัญญาณ
grid `1fr 300px` gap 20px, align-items start

**ซ้าย** (การ์ด `padding 24px 26px 20px`)
- แถบสถิติ 4 ช่อง gap 38px: `CLOSE` (Mono 600 32px) / `FASTMA · EMA 12` (swatch `16×3` ไล่สีน้ำเงิน + Mono 500 17px) / `SLOWMA · EMA 26` (swatch `#8ea3af`) / `FAST vs SLOW` (`Bull`/`Bear`)
- **กราฟราคา** SVG เต็มความกว้าง (ของจริง: candlestick รายวัน + EMA12 + EMA26)
- **Zone ribbon** แถบสูง 18px ใต้กราฟ `margin-top 12px`
- **Legend 7 โซน** คั่น `border-top 1px solid #dde7ee`, chip 12×12 + label: `GREEN · เปิด long`, `BLUE · pre-long 2`, `LBLUE · pre-long 1`, `RED · เปิด short`, `ORANGE · pre-short 2`, `YELLOW · pre-short 1`, `BLACK`
- **Verdict box** ท้ายการ์ด 4 แบบ

  | กรณี | สี | ข้อความ |
  |---|---|---|
  | long signal | bg `linear-gradient(160deg,#cbead9,#edf8f2)` border `#b9dfca` สามเหลี่ยมเขียวขึ้น | `แท่งนี้เป็นจุดสัญญาณฝั่ง long — เปิดไม้ที่แท่งถัดไป` + อธิบายว่าเข้าโซนเขียวจาก BLUE ขณะสถานะก่อนหน้าเป็น bearish จึงเป็น long signal จริง ไม่ใช่แค่ longcond · ไม่มีสถานะ short ค้างที่ต้องปิดก่อน + ปุ่ม `ดูการตัดสินใจ` (bg ไล่สีเขียว) |
  | short signal | bg `linear-gradient(160deg,#f8d6d3,#fdeeec)` border `#f3d2ce` สามเหลี่ยมแดงลง | `แท่งนี้เป็นจุดสัญญาณฝั่ง short — ปิด long แล้วเปิด short ที่แท่งถัดไป` + `โหมดทางเดียวบังคับปิด long 25% ให้เสร็จก่อน ถ้าขาปิดไม่สำเร็จจะไม่เปิด short` + ปุ่ม (bg ไล่สีแดง) |
  | short แต่ปิดฝั่ง short ไว้ | bg `linear-gradient(160deg,#f6e7bd,#fdf8ea)` border `#f4e6bd` | `มี short signal แต่ฝั่ง short ปิดอยู่ในโปรไฟล์` + `ระบบยังปิด long 25% ตามสัญญาณ แต่จะไม่เปิดไม้ฝั่ง short ต่อ` + ปุ่ม outline เหลือง |
  | ไม่มีสัญญาณ | bg `linear-gradient(180deg,#eef4f9,#ffffff)` border `#e3ebf1` | `แท่งล่าสุดไม่ใช่จุดสัญญาณทั้งสองฝั่ง — ไม่ทำอะไร` + `กฎไม้เรียวปฏิเสธการเข้าที่ไม่ใช่แท่งสัญญาณ ทั้งฝั่ง long และ short · ระบบยังเขียน DecisionRecord ของแท่งนี้ตามปกติ` + ปุ่ม `ดูบันทึก` |

**ขวา** (2 บล็อก `padding 20px 22px`)
- `สถานะที่คำนวณได้` — key/value Mono 12.5px: `zone` (chip) · `longcond` · `shortcond` · `state` · `barssince longcond` · `barssince shortcond` · เส้นคั่น · `long signal` / `short signal` (weight 600) · เส้นคั่น · `สถานะที่ถือ` · `ราคา liquidation`
- `พารามิเตอร์` — `xsrc = close` · `xprd1 / xprd2 = 12 / 26` · `xsmooth = 1` · เส้นคั่น · `contract = USDT-M perp` · `margin / mode = isolated · one-way` · `leverage = 2x` · `funding 8h = +0.010%`
  + กล่องหมายเหตุ: `โซนเดียวกันใช้ได้ทั้งสองฝั่ง — GREEN คือจุดเปิด long, RED คือจุดเปิด short สีของโซนเปลี่ยนได้จนแท่งปิด ระบบจึงไม่รองรับโหมด fixed timeframe ของ Pine Script`

#### 9.2b แท็บ การตัดสินใจ — 4 กรณี

**กรณี A · long signal (BTC)** grid `minmax(0,1fr) 340px`

การ์ดซ้าย 4 บล็อกซ้อน
1. **Hero** bg `linear-gradient(160deg,#cbead9,#edf8f2)`, `border-bottom 1px solid #b9dfca`, `padding 28px 30px`
   label `ผลการตัดสินใจ` + chip ขวา `โหมดทดลอง · ไม่ส่งคำสั่งจริง` (พื้นเหลืองอ่อน text `#8a6a15`) หรือ `ส่งออเดอร์จริง` (พื้นแดงอ่อน text `#c0392f`)
   `LONG 0.00153 BTC` (600 34px `#0f7a4c`) + `45% ของ bucket long` (Mono 500 20px) + chip `isolated 2x` (bg `#e6eff7` text `#1478c4` border `#c9d8e3`)
   sub `long signal พร้อมปัจจัยสนับสนุน 2 จาก 3 · ไม่มีสถานะ short ค้างจึงไม่ต้องปิดก่อน · ผ่านกฎไม้เรียวและ risk ทุกข้อ · เปิดไม้ที่แท่งถัดไป`
2. **3-up meta** คั่น `border-right 1px solid #dde7ee`: `สัญญาณ` = `เปิด long · GREEN` / `ราคาอ้างอิง` = `58,465 close` / `รหัสออเดอร์ · กันสั่งซ้ำ` = `cane-BTCUSDT-1787788800-long`
3. **ปัจจัยสนับสนุน** + chip `temperature 0 · จาก cache` + นับ `2 / 3` ขวา — การ์ดละ `padding 16px 18px`
   - ผ่าน: bg `linear-gradient(160deg,#cbead9,#edf8f2)` border `#b9dfca`, ติ๊ก 17×17 ไล่สีเขียว, ชื่อ `#0f7a4c` 600 13.5px, คำอธิบาย `#4b7d64` 13px/1.75 `padding-left 28px`, บรรทัด `แท่งอ้างอิง …` Mono 11px `#6f8896`
   - ไม่ผ่าน: bg `linear-gradient(180deg,#eef4f9,#ffffff)` border `#e3ebf1`, กล่องเปล่า border `1.5px #c9d8e3`, ชื่อ+คำอธิบาย `#5f7b8a`
   - 3 ปัจจัย long: `เบรคเส้นแนวโน้มกด` (0.78 ผ่าน) · `รายย่อยโยนของทิ้ง` (0.85 ผ่าน) · `จุดต่ำสุดใหม่สูงกว่าเดิม` (0.62 ไม่ผ่าน)
   - footnote: `ความมั่นใจไม่เข้าสูตรขนาดไม้ — เก็บไว้ให้คนตรวจย้อนหลังเท่านั้น`
4. **ด่านที่ผ่านมาแล้ว** grid 2 คอลัมน์ ติ๊กเขียว 6 ข้อ: `กฎไม้เรียว` · `kill switch — clear` · `daily loss 0.8 / 3.0%` · `สถานะจริงที่ exchange · ไม่มีฝั่งตรงข้าม` · `เลเวอเรจ 2x ไม่เกินเพดาน 3x` · `ห่าง liquidation 49.5% ≥ 25%`

การ์ดขวา
- **ที่มาของ 45%** ledger Mono 13px แถวละ `padding 10px 0` + `border-bottom 1px solid #dde7ee`:
  `ไม้พื้นฐาน 5` · `เบรคเส้นแนวโน้มกด +20` · `รายย่อยโยนของทิ้ง +20` · `จุดต่ำสุดใหม่สูงกว่าเดิม +0` (จาง) · `ครบ 3 → 100 / ไม่เข้าเงื่อนไข` (จาง) · `สูตรให้ 45%` (`border-bottom 2px solid #8ea3af`) · `เพดานต่อไม้ฝั่ง long 50% — ไม่ถูกตัด`
  ปิดท้ายด้วยกล่อง `ใช้จริง` bg `#e6eff7` border `#c9d8e3`: `45% · margin 45.00 USDT` + `notional 90.00 USDT ที่ 2x · 0.00153 BTC · ปัดลงตาม lot size` + แถวคั่น `ราคา liquidation | 29,525 · ห่าง 49.5%`
- **DecisionRecord** pre block bg `linear-gradient(180deg,#eef4f9,#ffffff)` border `#e3ebf1`, Mono 11px/1.85 `white-space: pre-wrap`; JSON ย่อมี key `"side"`, `"leverage"`, `"margin_mode"` + ปุ่ม `ดูทั้งหมด` + footnote `เขียนทุกแท่งทุก symbol รวมกรณีที่ผลลัพธ์คือไม่ทำอะไร`

**กรณี B · short signal ที่ต้องกลับข้าง (SOL)** — โครงเดียวกับ A แต่ย้อมแดง (hero bg `linear-gradient(160deg,#f8d6d3,#fdeeec)`, ตัวเลข `#c0392f`, body `#b04a4a`) และแทรกบล็อกเพิ่ม
- **แผนกลับข้าง — สองขาในแท่งเดียว** + chip `one-way mode`; grid `minmax(0,1fr) 26px minmax(0,1fr)` กลางเป็นลูกศร `→` (`#8ea3af`)
  - **ขา 1** พื้นแดงอ่อน: `ปิด long 25%` + `ปริมาณ 0.181 SOL · reduceOnly` / `ราคาเข้าเดิม 165.40` / `ผลของไม้ -11.0% · -3.31 USDT` / `ผลต่อมาร์จิ้น -22.1%`
  - **ขา 2** พื้นแดงอ่อนเข้มกว่า: `เปิด short 25%` + `margin 15.00 USDT` / `notional ที่ 2x 30.00 USDT` / `ปริมาณ 0.203 SOL` / `liquidation 219.85 · ห่าง 49.4%`
  - แถบเตือนเหลือง + ไอคอน `!` 18×18: `ถ้าขา 1 ไม่ fill ครบ ระบบยกเลิกขา 2 ทั้งหมด ไม่เปิด short ทับสถานะ long ที่ยังค้าง และเขียน DecisionRecord ว่า flip_aborted เพื่อให้รอบถัดไปตัดสินใจใหม่จากสถานะจริงที่ exchange`
- **ปัจจัยสนับสนุนฝั่ง short** — ติ๊กที่ผ่านใช้ไล่สีแดง, การ์ดที่ผ่านพื้นแดงอ่อน, ชื่อ `#c0392f`; 3 ปัจจัย: `หลุดเส้นแนวโน้มรับ` (0.81 ผ่าน) · `แรงซื้อหมดแรงที่ยอด` (0.61 ไม่ผ่าน) · `จุดสูงสุดใหม่ต่ำกว่าเดิม` (0.58 ไม่ผ่าน) + footnote `ชุดปัจจัยฝั่ง short เป็นภาพกลับด้านของฝั่ง long คนละพรอมป์ แต่ให้น้ำหนักเท่ากัน`
- ledger ขวา: `ที่มาของ 25%` · `เพดานต่อไม้ฝั่ง short 40% — ไม่ถูกตัด` · กล่อง `ใช้จริง` พื้นแดงอ่อน + footnote `bucket ฝั่ง short แยกจากฝั่ง long — การเปิด short ไม่กินโควตาของไม้ long ที่รออยู่คู่อื่น`
- DecisionRecord มี `"flip":{"close":"long","qty":0.181}` + footnote `ขาปิดและขาเปิดอยู่ในเรคคอร์ดเดียวกัน เพื่อให้ตรวจย้อนหลังได้ว่ากลับข้างครบทั้งสองขาหรือไม่`

**กรณี C · short signal แต่ `allow_short = false`** — การ์ดเดียว `max-width 720px`: hero พื้นเหลืองอ่อน, `ปิด long 25% · ไม่เปิด short` (600 30px `#8a6a15`) + อธิบายว่าปิดตามสัญญาณแต่ไม่เรียก Confluence Judge และไม่คำนวณขนาดไม้ฝั่ง short
key/value: `ขาที่ยิง` = `ปิด long 0.181 SOL · reduceOnly` / `ผลของไม้` = `-11.0% · -3.31 USDT` / `ขาที่ข้าม` = `เปิด short — allow_short = false` (`#8f6c13`)
footer bar: `ปิดฝั่ง short ไว้ทำให้ระบบกลับไปเป็นลอง-ออนลี่แบบเดิม โดยยังใช้สัญญาณชุดเดียวกัน · เปิดฝั่ง short ได้ที่หน้าความเสี่ยง`

**กรณี D · ไม่มีสัญญาณ** — การ์ดเดียว `max-width 720px`: hero `ไม่ทำอะไร` (600 30px) + `แท่งล่าสุดไม่มี long signal และไม่มี short signal — ไม่มีอะไรต้องเปิด และไม่มีสัญญาณฝั่งตรงข้ามที่ต้องปิดสถานะที่ถืออยู่ · ไม่เรียก Confluence Judge ไม่คำนวณขนาดไม้ แต่ยังเขียน DecisionRecord ตามปกติ`
3-up meta (`— · zone X` / ราคาปิด / `ไม่คำนวณ`) · บล็อก `ทำไมไม่ลงไม้` (`longcond` / `shortcond` / `state` / `สถานะที่ถือ` / `กฎไม้เรียว → ปฏิเสธทั้งสองฝั่ง — ไม่ใช่แท่งสัญญาณ` สีแดง) · footer bar + ปุ่ม `ดูบันทึก`

#### 9.2c แท็บ Cold start

**เข้าเงื่อนไข (ETH — ตกรถฝั่ง long)**
- **Precondition bar** `เงื่อนไขบังคับ` + ติ๊กเขียว `ไม่มีสถานะเปิดของ ETH/USDT ที่ exchange ทั้งฝั่ง long และ short` + `ถ้ามีอยู่แล้ว จะกลับโหมดปกติคือเฝ้ารอสัญญาณฝั่งตรงข้าม ไม่เข้าไม้ซ้ำและไม่เปิดสวนสถานะเดิม`
- **แถบระบุฝั่ง** chip `ฝั่ง LONG` + `ETH/USDT ตกรถฝั่ง long — state เป็น bullish มา 8 แท่งก่อนบอทเปิด · เส้นทางนี้ทำงานสมมาตรทั้งสองฝั่ง ถ้า state เป็น bearish จะเป็นการตกรถฝั่ง short และใช้ตรรกะเดียวกันโดยตั้ง SL เหนือราคาเข้าแทน`
- Grid `400px minmax(0,1fr)` — สองเส้นทางเทียบกัน

**ทางที่ 1 · ลง timeframe เล็ก** (การ์ดซ้าย แคบ = ไม่แนะนำ)
กราฟ 1h ในกรอบ + header `1h · 72 แท่ง` / `2,800` · key/value: `zone 1h GREEN` · `FastMA / SlowMA 2,762 / 2,741` · `long signal ล่าสุด 9 แท่งก่อน` · `แท่งล่าสุดเป็นจุดสัญญาณ false`
กล่องเหลือง: `1h ก็ตกรถแล้วเหมือนกัน` + `สัญญาณ 1h เกิดไปแล้ว 9 ชั่วโมงและราคาวิ่งไป 2.1% … ต้องรอ short signal ของ 1h ก่อน แล้วค่อยรอ long รอบถัดไป` · ปุ่ม outline `เฝ้า 1h รอสัญญาณรอบถัดไป`

**ทางที่ 2 · CDC ATR Trailing Stop** (การ์ดขวา กว้าง = ทางที่ใช้ได้) — badge `V2.1 · 2013`
- กราฟ trailing stop + legend: `Slow Trail · 2 × ATR(10)` (เส้น `#e05252`) / `Fast Trail · 0.5 × ATR(5)` (จุดไล่สีน้ำเงิน) / `โซนกำไรที่ต้องถึง` (แถบเขียวอ่อน)
- **4-up metric cards** `repeat(4,1fr)` gap 12px, Mono 600 19px: `ENTRY 2,800.00` · `SL · SLOW TRAIL 2,653.60` (พื้นแดงอ่อน text `#c0392f`) · `TP ขั้นต่ำ 3,092.80` (พื้นเขียวอ่อน text `#0f7a4c`) · `R : R 2.00` (พื้นฟ้าอ่อน)
- แถบผ่านเกณฑ์เขียว: `ผ่านเกณฑ์ RR ≥ 2:1` + `ความเสี่ยงต่อไม้ 146.40 (5.23%) · เป้าขั้นต่ำ 292.80 เหนือราคาเข้า`
- 2 คอลัมน์พารามิเตอร์: `พารามิเตอร์ · จาก .pine` (`AP1 / AF1 = 5 / 0.5`, `AP2 / AF2 = 10 / 2.0`, `Sig = ema(Hst, 9)`) และ `ATRCD · แท่งล่าสุด` (`Hst +104.8`, `Sig +89.4`, `สี Green`)
- ปุ่ม `เข้าไม้พร้อมตั้ง SL ที่ 2,653.60` (bg ไล่สีเขียว, flex:1) + `ข้ามรอบนี้` (outline)
- footnote ป้าย `ตามสเปก`: ไม้พื้นฐาน 5% (`margin 5.00 · notional 10.00 ที่ 2x · 0.00357 ETH`) เพราะไม่ใช่จุดสัญญาณจึงไม่มีปัจจัยสนับสนุนให้ตัดสิน

**ไม่เข้าเงื่อนไข** — การ์ด `max-width 640px`: `ไม่เข้าเงื่อนไข cold start` + `เส้นทางนี้ทำงานเฉพาะรอบแรกหลังบอทเปิด เมื่อ state เดินไปข้างหนึ่งอยู่แล้ว (bullish = ตกรถฝั่ง long, bearish = ตกรถฝั่ง short) แต่แท่งล่าสุดไม่ใช่จุดสัญญาณ และต้องไม่มีสถานะเปิดของคู่นี้ที่ exchange ทั้งสองฝั่ง` + key/value 3 ข้อ + กล่องเหตุผลเฉพาะคู่นั้น (`coldReason`)


### 9.3 ความเสี่ยง

Sub: `USDT-M perpetual · isolated margin · โหมดทางเดียวต่อเหรียญ · ทุกเพดานตรวจก่อนยิงทุกออเดอร์ ไม่ใช่ตอนเริ่มรอบ · ตั้งไม่ครบ = โหลดไม่ผ่าน = ไม่เทรด`
Grid `400px minmax(0,1fr)`

**ซ้าย 4 การ์ดซ้อน**
1. **Kill switch**
   - clear: กล่องพื้นเขียวอ่อน border `#b9dfca`, จุด 11×11 `#17a866`, `Kill switch — ปกติ` + `สถานะ latched เก็บที่ state/killswitch.json — restart แล้วไม่หาย`; ปุ่ม `หยุดยิงออเดอร์ทันที` outline `1px solid #e2b4ae` text `#c0392f` hover พื้นแดงอ่อน border `#c0392f`, `padding 15px` เต็มความกว้าง
   - latched: กล่องพื้นแดงอ่อน border `#f3d2ce`, จุด `#c0392f`, `Kill switch — latched` + `หยุดยิงออเดอร์ทั้งหมดแล้ว · ติดจากเงื่อนไขแพ้ติดกัน`; ปุ่ม `ปลดล็อก — ต้องพิมพ์ชื่อ profile ยืนยัน`
   - footnote: `ปลดล็อกด้วยมือเท่านั้น ไม่หายเองเมื่อเวลาผ่านไปหรือ restart`
2. **สถานะรวมตามฝั่ง**
   - `LONG` (chip แถบซ้ายเขียว) + `2 ไม้ · margin 17.00` + bar 7px track `#e3ebf1` fill `#17a866` 5.7% + `notional 32.00 · 5.7% ของ bucket long 300.00 · รอเปิด long 45.00 ที่ BTC`
   - `SHORT` (chip แถบซ้ายแดง) + `0 ไม้ · margin 0.00` (`#5f7b8a`) + bar fill แดงอ่อน 8.3% + `รอเปิด short 15.00 ที่ SOL · 8.3% ของ bucket short 180.00`
   - กล่องหมายเหตุ: `โหมดทางเดียวทำให้ net exposure เท่ากับฝั่งที่ถืออยู่เสมอ ไม่มีสถานะสวนกันในคู่เดียวกัน`
3. **ฝั่ง short** — `ปิดได้ทั้งระบบเมื่อไม่อยากถือฝั่งลง — ระบบจะยังปิด long ตามสัญญาณ short แต่ไม่เปิดไม้ใหม่` + toggle `50×28` เหลี่ยม (on = track `#17a866` knob ขวา, off = track `#d5e1ea` knob ซ้าย, knob `22×22` bg `#fff` offset 3px)
4. **โหมดทดลอง** — `ค่าตั้งต้นเป็นเปิดแม้ในโปรไฟล์จริง การส่งคำสั่งจริงต้องจงใจเสมอ` + toggle แบบเดียวกัน

**ขวา — เพดานความเสี่ยง · จาก config profile** (`padding 26px 28px`, gap 26px) 6 รายการ ตัวเลข Mono 600 16px

| เพดาน | ค่า | ตัวชี้วัด | บรรทัดล่าง |
|---|---|---|---|
| เพดานต่อไม้ฝั่ง long · % ของ bucket long | 50.0 | bar fill 90% ฟ้า | `ไม้ long ที่รอส่งอยู่ 45% — ยังไม่ชนเพดาน` |
| เพดานต่อไม้ฝั่ง short · % ของ bucket short | 40.0 | bar fill 62.5% `#e05252` | `ไม้ short ที่รอส่งอยู่ 25% — ยังไม่ชนเพดาน · ตั้งต่ำกว่าฝั่ง long โดยเจตนา` |
| เพดานขาดทุนต่อวัน | 3.0 | bar fill 27% ไล่สีน้ำเงิน | `วันนี้ 0.8% · นับกำไรขาดทุนที่ยังไม่ปิดด้วย (mark-to-market) · รีเซ็ตเที่ยงคืน UTC` |
| ตัดวงจรเมื่อแพ้ติดกัน | 2 | pip 2 ช่อง `46×11` (แรก `#e05252`, สอง `#e3ebf1`) | `แพ้ติดกัน 1 ไม้ — อีก 1 ไม้จะสั่งหยุดยิงออเดอร์ · นับรวมทั้งสองฝั่ง` |
| เพดานเลเวอเรจต่อเหรียญ | 3.0x | 3 ช่องเท่ากัน สูง 11px (2 ช่องฟ้า, 1 ช่อง `#e3ebf1`) | `ตั้งไว้สูงสุดในโปรไฟล์ตอนนี้ 2.0x ที่ BTC ETH SOL · DOGE 1.0x` |
| บัฟเฟอร์ห่างราคา liquidation ขั้นต่ำ | 25.0% | bar fill 50% `#dfbb2a` | `ไม้ที่คำนวณแล้วห่างน้อยกว่านี้ถูกปฏิเสธก่อนยิง · ไม้ที่รออยู่ห่าง 49.4–49.5%` |

ท้ายการ์ด คั่น `border-top 1px solid #dde7ee`, 2 คอลัมน์
- `BROKER`: `kind = ccxt` · `exchange = binance` · `defaultType = future` · `marginMode = isolated` · `positionMode = one-way`
- `กันสั่งซ้ำ`: `รหัสออเดอร์ คำนวณจาก symbol + เวลาปิดแท่ง + ฝั่งสถานะ (long/short) + ขาที่ยิง (open/close) — ถ้าโปรเซสตายกลางคันแล้วกลับมาจะได้ผลเดิม ไม่เปิดสถานะซ้อนและไม่กลับข้างซ้ำ`

**ตารางท้ายหน้า — เลเวอเรจและ bucket ต่อเหรียญ** (`margin-top 20px`)
grid `150px 74px 116px 116px 150px 1fr` gap 16px, header `padding 14px 24px`: `SYMBOL` `LEV` `BUCKET LONG` `BUCKET SHORT` `ฝั่งที่อนุญาต` `ห่าง liquidation ของไม้ปัจจุบัน`
แถว `padding 15px 24px` Mono 12.5px; `LEV` เป็น chip (bg `#e6eff7` text `#1478c4` border `#c9d8e3`); `ฝั่งที่อนุญาต` = `long + short` หรือ `long เท่านั้น` (`#8f6c13`); DOGE bucket short = `—`
footnote: `bucket ของสองฝั่งแยกกัน — เปิด short ไม่กินโควตาไม้ long ที่รออยู่ · เหรียญที่ตั้ง 1.0x ไม่มีการยืม จึงไม่มีราคา liquidation แต่ยังเปิด short ได้ถ้าอนุญาต`

### 9.4 บันทึก

H1 `บันทึกการตัดสินใจ` + sub `decisions.jsonl · append-only · หนึ่งบรรทัดต่อหนึ่งแท่งต่อหนึ่ง symbol · ขาปิดและขาเปิดของการกลับข้างอยู่ในบรรทัดเดียวกัน · ตารางนี้แสดง 8 บรรทัดที่มีผลต่อสถานะ`

**Filter chips** (static ใน prototype — ของจริงควรกดกรองได้): `ทั้งหมด 464` (active) · `มีออเดอร์ 22` · `ฝั่ง long 18` (พื้นเขียวอ่อน) · `ฝั่ง short 4` (พื้นแดงอ่อน) · `กลับข้าง 3` · `risk ปฏิเสธ 6` · `LLM ตอบไม่ได้ 3` (พื้นเหลืองอ่อน) · `ถูกเพดานตัด 4` (พื้นฟ้าอ่อน)

**ตาราง** grid `82px 118px 86px 80px 84px 118px 168px minmax(0,1fr)` gap 13px, แถว `padding 17px 24px`, Mono 12.5px
header: `BAR` `SYMBOL` `ZONE` `SIDE` `SIGNAL` `FACTORS` `SIZE` `ผลลัพธ์`
- `SIDE` = chip แถบซ้าย 2px: `LONG` เขียว / `SHORT` แดง / `FLAT` `#c9d8e3`
- `SIGNAL` = `เปิด long` (`#0f7a4c`) / `เปิด short` (`#c0392f`) / `—`
- `FACTORS` = ช่อง 17×17 สามช่อง — ผ่านฝั่ง long `#17a866`, ผ่านฝั่ง short `#c0392f`, ไม่ผ่าน = พื้นขาว border `1.5px #c9d8e3`; ไม่เรียก LLM → ข้อความ `ไม่เรียก`; fallback → chip `ไม้พื้นฐาน` (พื้นเหลืองอ่อน)
- `SIZE` = `45 → 45`; ถูกตัด → `45` ขีดฆ่า + `→ 40` + chip `เพดาน`
- Row tint: เปิด long `#eaf7f0` · เปิด short / กลับข้าง `#fdf0ee` · ปกติ transparent
- `ผลลัพธ์` บอกทั้งสองขาเมื่อกลับข้าง เช่น `ปิด long 0.00170 BTC +18.2% · เปิด short 0.00069 BTC`; แถวที่มีใบสรุปมีปุ่มเล็ก `ใบสรุป` (หรือ `cold start`)
- 8 แถวตัวอย่างครอบทุกผลลัพธ์: dry-run long · dry-run กลับข้างเป็น short · LLM fallback ไม้พื้นฐาน · ไม่ทำอะไร + cold start · กลับข้างจาก short เป็น long · กลับข้างพร้อมถูกเพดานตัด · risk ปฏิเสธ · เปิด long ที่ถูกเพดานตัดจาก 100 → 50

### 9.5 รายงาน

Header + sub `คิดจากไม้ที่ปิดแล้วเท่านั้น ทั้งฝั่ง long และ short · หัก fee, slippage และ funding จริงจาก fill · % คิดบน notional ไม่ใช่มาร์จิ้น · ไม้ที่ยังถืออยู่แยกไว้ด้านล่าง` + range switcher (`ตั้งแต่เริ่มรัน` / `กำหนดช่วงเอง`, segmented ใน border 1px, active bg `#e6eff7` text `#1478c4`) + ปุ่ม `ส่งออก CSV`

**Range bar** — โหมด all: `2026-05-02 → 2026-08-26` + `116 วัน · แท่งรายวันที่ปิดแล้ว 116 แท่ง`; โหมด custom: input `จาก` / `ถึง` (กว้าง 130px, Mono 12.5px); ขวาเสมอ: `bucket long 300.00 · short 180.00 USDT · USDT-M perp 1–2x · โปรไฟล์ live`

**KPI strip 5 ช่อง** ตัวเลข Mono 600 28px
- `ผลตอบแทนสุทธิ · ปิดแล้ว` = `+6.2%` / `+18.55 USDT` / `ฝั่ง long +6.9 จุด · ฝั่ง short -0.7 จุด`
- `ไม้ที่ปิดแล้ว` = `10` / `ชนะ 4 · 40.0%` / `long 8 ไม้ · short 2 ไม้ · กำไรเฉลี่ย +11.1% ขาดทุนเฉลี่ย -4.3%`
- `ย่อลึกสุดจากยอดสูงสุด` = `-2.6%` / `16 มิ.ย. · วันที่ kill switch ติด`
- `ค่าธรรมเนียม + slippage` = `1.80 USDT` / `fee 0.95 + slippage 0.43 + funding 0.42 · กินไป 0.60 จุด`
- `ที่ยังถืออยู่ · ไม่ได้นับ` = `-1.0%` / `2 ไม้ long` / `SOL -11.0% · DOGE +12.1% · margin ที่ใช้ 17.00`

**Equity curve** grid `minmax(0,1fr) 330px`
- ซ้าย: inline SVG `viewBox 0 0 1000 268` — gridline `#e3ebf1`, zero line `#c9d8e3`, แกน Y `+12% … -8%` (Mono 11px `#8ea3af`), เส้นสุทธิไล่สีน้ำเงิน `stroke-width 2.4` + area fill opacity .1, เส้นก่อนหักต้นทุน dashed `#8ea3af` `5 4`, จุดแดง `#e05252` ที่ drawdown, จุดฟ้าปลายเส้น, tick แกน X `05-02 06-02 06-16 07-30 08-26`
  ใต้กราฟ: `เกือบทั้งเส้นมาจากไม้ long ของ BTC ที่ถือ 19 วัน (+5.97 USDT) — ไม้อื่นรวมกันแทบไม่ขยับเส้น · ฝั่ง short 2 ไม้ติดลบรวม -2.10 USDT`
- ขวา: การ์ด `บอททำตามกฎหรือไม่` — `เข้าไม้จากแท่งสัญญาณจริง 10 / 10` (เขียว) · `ปิดด้วยสัญญาณฝั่งตรงข้าม 8 / 10` · `ปิดด้วย kill switch 1` (เหลือง) · `ปิดเพราะ state กลับข้างแต่ไม่มีสัญญาณ 1` · `กลับข้างครบสองขาในแท่งเดียว 3 / 3` · `ปิดแล้วไม่เปิดฝั่งตรงข้าม 5` · `เปิดไม้ทับฝั่งตรงข้ามที่ยังค้าง 0` (เขียว) · เส้นคั่น · `ถูก risk ปฏิเสธ ไม่เกิดไม้ 6` · `ถูกเพดานตัดขนาดไม้ 4` · `ตกไปที่ไม้พื้นฐาน · LLM ไม่ตอบ 3` + กล่องยืนยัน + ปุ่ม `เปิดบันทึกการตัดสินใจ`

**ตารางต่อ symbol** grid `140px 88px 96px 84px 1fr 104px 104px 150px`: `SYMBOL` `ไม้ที่ปิด` `ชนะ` `L / S` `ผลรวมต่อ bucket รวม` `FEE` `SLIPPAGE` `ที่ยังถืออยู่`
คอลัมน์กลางเป็น **diverging bar**: track สูง 8px bg `#e3ebf1` + เส้นกลาง 1px `#c9d8e3` ที่ 50% + bar โผล่จากกลางไปซ้าย/ขวา (สเกลตัด ±12%, ครึ่งละ 50% ของความกว้าง), เขียว `#17a866` / แดง `#e05252` + ตัวเลขขวา `width 70px` ชิดขวา
แถวรวมท้ายตาราง bg `linear-gradient(180deg,#eef4f9,#ffffff)`: `รวม 10 · 4 · 40.0% · 8 / 2 · +6.2% · 0.95 · 0.43 · 2 ไม้ · -1.0%`

**ตารางไม้ที่ปิดแล้ว** grid `80px 80px 118px 64px 68px 1fr 1fr 84px 84px 164px`: `เข้า` `ออก` `SYMBOL` `SIZE` `ฝั่ง` `ENTRY` `EXIT` `GROSS` `NET` `สาเหตุที่ออก`
- `ฝั่ง` = `LONG` (`#0f7a4c`) / `SHORT` (`#c0392f`) Mono 600 11.5px · `NET` weight 600 สีตามผล · แถวที่ออกด้วย kill switch ได้ row tint เหลืองอ่อน + สาเหตุสี `#8f6c13`
- สาเหตุที่ออกในข้อมูลตัวอย่าง: `สัญญาณ long — กลับข้างในแท่งเดียว` · `สัญญาณ short — กลับข้างในแท่งเดียว` · `state กลับ bullish — ปิด short ไม่เปิด long` · `สัญญาณ short — DOGE ตั้ง long เท่านั้น` · `สัญญาณ short — ก่อนเปิดฝั่ง short` · `kill switch — แพ้ติดกัน 2 ไม้`
- header อธิบาย: `เรียงจากใหม่ไปเก่า · ราคาเข้า-ออกคือ fill จริง ไม่ใช่ราคาปิดแท่ง · ฝั่ง short กำไรเมื่อราคาออกต่ำกว่าราคาเข้า`
- footer: `ค่าธรรมเนียม taker 0.10% ต่อขา` · `slippage เฉลี่ย 0.045% ต่อขา` · `slippage แย่สุด 0.18% · DOGE/USDT 07-28` · `funding รวม -0.42 USDT · เก็บทุก 8 ชม. ทั้งสองฝั่ง` · `ยังไม่รวมไม้ที่ถืออยู่ 2 ไม้`

**CSV export** header `entry_bar,exit_bar,symbol,side,size_pct,entry_fill,exit_fill,gross_pct,net_pct,exit_reason` ไฟล์ `cane-report-closed-trades.csv`

### 9.6 คู่เหรียญ

Header + sub `แต่ละคู่คือบล็อก [[symbols]] หนึ่งบล็อกในไฟล์โปรไฟล์ — เก็บเลเวอเรจและ bucket ของทั้งสองฝั่ง · แก้แล้วต้องผ่าน validate ทั้งไฟล์ก่อนระบบจะเทรดต่อ` + stat box คู่ (`คู่ที่ตั้งไว้` / `bucket long / short` = `300.00 / 180.00 USDT`)
Grid `minmax(0,1fr) 380px`

**ตาราง** grid `110px 68px 32px 38px 98px 152px 136px` gap 10px: `SYMBOL` `EXCHANGE` `TF` `LEV` `BUCKET L/S` `สถานะไม้ปัจจุบัน` + ปุ่ม
- แถบสถานะซ้ายชื่อคู่ `3px × 16px` — เปิดใช้ ไล่สีน้ำเงิน, พักไว้ `#c9d8e3`
- `LEV` สี `#1478c4`; `BUCKET L/S` = `100.00 / 60.00` (ฝั่งที่ไม่เปิดเป็น `—`)
- `สถานะไม้ปัจจุบัน` = chip `LONG 25%` / `SHORT 25%` / `FLAT` (แถบซ้าย 2px) + โน้ต 11px (`-11.0% · รอกลับข้างเป็น short`, `ตกรถฝั่ง long · รอเลือกเส้นทาง`, `คู่ใหม่ · รอครบ 85 แท่ง`)
- ปุ่ม idle: `พักไว้`/`เปิดใช้` + `ลบ` (hover border `#e05252`); confirming: `ยืนยันลบ` (พื้นแดงอ่อน border `#e05252` text `#c0392f`) + `ยกเลิก`
- **Inline warning ตอนยืนยันลบ** ขยายใต้แถว `padding-left 45px`
  - คู่ที่ถืออยู่: พื้นเหลืองอ่อน + ไอคอน `!` 18×18 — `ลบแล้วบอทจะเลิกเฝ้าสัญญาณฝั่งตรงข้ามของไม้นี้ สถานะ (long หรือ short) จะค้างอยู่ที่ exchange โดยไม่มีใครดูแล และไม่มีใครเฝ้าราคา liquidation ปิดไม้ก่อนลบจะปลอดภัยกว่า`
  - คู่ที่ไม่ถือ: พื้นกลาง — บล็อกจะถูกตัดออก แต่ประวัติ DecisionRecord ยังอยู่
- footnote: `พักไว้ = คงบล็อกในไฟล์แต่ข้ามในรอบคำนวณ … · สถานะไม้และโน้ตในคอลัมน์นี้เป็นค่าเดียวกับที่แสดงในหน้าภาพรวมและรายงาน`

**ฟอร์มเพิ่มคู่** (ขวา)
- `คู่เหรียญ` (placeholder `LINK/USDT`)
- grid `1fr 1fr`: `เงินทุนฝั่ง long · USDT` (`100.0`) / `เงินทุนฝั่ง short · USDT` (`60.0 · เว้นว่าง = ไม่เปิด`)
- grid `1fr 92px`: select `เลเวอเรจ · เพดาน 3.0x` (`1.0x · ไม่ยืม` / `2.0x` / `3.0x · ชนเพดาน`) และ select `กรอบเวลาแท่ง` (`1d`/`4h`/`1h`)
- ปุ่ม `เพิ่มลงโปรไฟล์` bg ไล่สีน้ำเงิน ตัวอักษรขาว; disabled = outline `#d5e1ea` text `#6f8896`
- Validation + hint แบบ progressive: ว่าง → `พิมพ์คู่เหรียญในรูปแบบ BASE/QUOTE เช่น LINK/USDT`; รูปแบบผิด (regex `^[A-Z0-9]{2,10}/[A-Z0-9]{2,6}$`) → `รูปแบบไม่ถูกต้อง — ต้องเป็น BASE/QUOTE ตัวพิมพ์ใหญ่`; ซ้ำ → `คู่นี้มีอยู่ในโปรไฟล์แล้ว`; bucket long ไม่ใช่เลข > 0 → `ต้องระบุ bucket_quote_long เป็นตัวเลขมากกว่า 0 · ฝั่ง short เว้นว่างได้ถ้าไม่ต้องการ`; ผ่าน → `จะเขียนเป็นบล็อก [[symbols]] ใหม่พร้อม leverage และ bucket ทั้งสองฝั่ง แล้ว validate ทั้งไฟล์อีกครั้ง · ค่าตั้งต้นคือ 1.0x คือไม่ยืม ปรับขึ้นได้ถึงเพดาน 3.0x`

**การ์ด ผลของการแก้รายการคู่** — 3 บรรทัดพร้อมแถบสีซ้าย 3px: ฟ้า (คู่ใหม่เริ่มนับ 85 แท่งย้อนหลังก่อน) · เหลือง (`bucket ฝั่ง long และ short ของคู่ใหม่แยกกัน และไม่แบ่งจากคู่เดิม — ต้องมีมาร์จิ้นว่างพอตามที่ตั้ง`) · แดง (validate ไม่ผ่าน = หยุดเทรดทั้งหมด ไม่ใช่แค่คู่ที่แก้)

### 9.7 ตั้งค่า

Sub: `paper กับ live เป็น profile คนละไฟล์ โครงเดียวกัน โค้ดเส้นทางเดียวกัน ต่างกันแค่ค่า · แก้ไฟล์ไหนมีผลกับเฉพาะโหมดนั้น`

**แท็บ** `live.toml` / `paper.toml` (Mono, active weight 600 + underline `2.5px #30AFFF`, inactive `#5f7b8a`) — แท็บที่ตรงกับโหมดที่เดินอยู่มีชิป `กำลังทำงาน` (bg `#e6eff7`, text `#1478c4`, border `#c9d8e3`)
เปิดหน้าจอครั้งแรกเลือกแท็บของโหมดที่ทำงานอยู่; แท็บที่ไม่ตรงแสดงแถบกลาง `กำลังดูโปรไฟล์ที่ไม่ได้ทำงานอยู่ — การแก้ที่นี่ไม่กระทบกับโหมดที่เดินอยู่ตอนนี้`

Grid `minmax(0,1fr) 400px`

**live.toml** — banner แดง `โหลดไม่ผ่าน — พบ 4 ข้อ · ระบบไม่เทรดจนกว่าจะแก้ครบ` + `ไม่มีโหมดเตือนแล้วไปต่อ` + ปุ่ม `ตรวจอีกครั้ง`
TOML viewer Mono 13px/2.1 `#3d5a6b`, ค่าเป็น `#1478c4`, ชื่อ section `#5f7b8a`; บรรทัดที่ error: พื้นแดงอ่อน + `border-left 3px solid #c0392f`, token ที่ผิด `#c0392f` weight 600; บรรทัดที่ขาดเขียนเป็น `— ขาด <key>` ทั้งบรรทัดสีแดง

```toml
profile   = "live"
timeframe = "1d"
market    = "usdtm_perp"
base_pct  = 32.0          # error: นอกช่วง 5–20
dry_run   = true

[[symbols]]
symbol             = "BTC/USDT"
bucket_quote_long  = 100.0
bucket_quote_short = 60.0
leverage           = 2.0
allow_short        = true

[risk]
max_position_pct_long  = 50.0
max_position_pct_short = 40.0
max_leverage           = 3.0
min_liq_buffer_pct     = 25.0
max_daily_loss_pcnt    = 3.0   # error: คีย์ที่ไม่รู้จัก
— ขาด consecutive_loss_breaker

[broker]
kind     = "ccxt"
— ขาด exchange
```

**รายการที่ต้องแก้** (ขวา) — badge บรรทัด (Mono 600 11px, พื้นแดงอ่อน, text `#c0392f`)
- `L4` `base_pct = 32.0` อยู่นอกช่วง 5–20 — ตรวจตอนโหลด ไม่ใช่ตอนคำนวณ
- `L19` คีย์ที่ไม่รู้จัก `max_daily_loss_pcnt` — ตั้งใจปฏิเสธ ไม่ปล่อยให้ค่าหายไปเงียบๆ — หมายถึง `max_daily_loss_pct` หรือเปล่า
- `L20` ขาด `consecutive_loss_breaker` — risk limit ไม่มีค่าตั้งต้นให้
- `L23` `broker.kind = "ccxt"` แต่ไม่ระบุ exchange
- footnote: API key อยู่ใน `.env` ที่คนกรอกเอง และอยู่ใน `.gitignore` ตั้งแต่ commit แรก · log ผ่าน filter ที่ลบค่าของคีย์ที่มีคำว่า key / secret / token / password ออกอัตโนมัติ

**paper.toml** — banner เขียว `โหลดผ่าน — ไม่พบข้อผิดพลาด · ตรวจล่าสุด 31 ส.ค. 2569 11:58` + `ใช้ schema ตัวเดียวกับ live.toml`
ขวา: `ต่างจาก live 6 ค่า` + `คีย์ที่ไม่อยู่ในรายการนี้มีค่าเท่ากันทั้งสองไฟล์` — ตาราง 3 คอลัมน์ (key / paper / live)

| key | paper | live |
|---|---|---|
| base_pct | 10.0 | 32.0 |
| leverage | 1.0 | 2.0 |
| max_leverage | 2.0 | 3.0 |
| min_liq_buffer_pct | 35.0 | 25.0 |
| max_daily_loss_pct | 5.0 | ขาด |
| broker.kind | paper | ccxt |

footnote: `โหมด paper ไม่ใช้ API key และไม่เรียก endpoint ที่ส่งคำสั่ง · ราคาอ้างอิงดึงจาก public feed เดียวกับ live เพื่อให้เทียบผลกันได้`
`dry_run` **บังคับ true** ในโหมด paper; เฉพาะ live เท่านั้นที่ตั้ง false ได้ (แล้วชิป `ยิงจริง` ติด)

**ท้ายหน้า** — การ์ดการแจ้งเตือน (หัวข้อ 8.3–8.4)


### 9.8 ผู้ใช้ — 4 แท็บ

Sub: `N บัญชี · สิทธิ์ผูกกับ role ไม่ผูกกับคน · ทุกการเปลี่ยนสิทธิ์ต้องยืนยันด้วย 2FA` + ปุ่ม `เชิญผู้ใช้` (bg ไล่สีน้ำเงิน)
**KPI 4 ช่อง**: `ใช้งานอยู่` · `ยิงจริงได้ (owner)` (`#1478c4`) · `รอรับคำเชิญ` (`#8f6c13`) · `ยังไม่ตั้ง 2FA` (`#c0392f` + `เข้าใช้ไม่ได้`)
**Tabs**: `บัญชีผู้ใช้` · `สิทธิ์ต่อ role` · `session ที่เปิดอยู่` · `บันทึกผู้ใช้`

#### 9.8a บัญชีผู้ใช้
- **Invite panel** (พับได้ เปิดจากปุ่ม `เชิญผู้ใช้`) grid `1fr 200px 140px`: อีเมล + role dropdown (custom ไม่ใช่ native select — ปุ่มสูง 41px + caret ในกล่อง 32px, เมนู `position absolute top 44px` border `#0f6fb5` เงาอ่อน, 5 role มีแถบซ้าย 3px เมื่อ active) + ปุ่ม `ส่งคำเชิญ`
  hint: ว่าง → `ผู้ถูกเชิญต้องตั้งรหัสผ่านและผูก 2FA ให้เสร็จก่อนจึงเข้าคอนโซลได้`; รูปแบบผิด → `รูปแบบอีเมลไม่ถูกต้อง`; ซ้ำ → `อีเมลนี้มีบัญชีอยู่แล้ว`; ผ่าน → `ลิงก์คำเชิญหมดอายุใน 72 ชั่วโมง · ส่งใหม่ได้จากแถวของผู้ใช้`
- **ตาราง** grid `minmax(0,1fr) 104px 128px 132px 106px 262px`: `ผู้ใช้` (avatar 30×30 + ชื่อ + อีเมล truncate) · `ROLE` (chip แถบซ้าย — OWNER ไล่สีน้ำเงิน/`#1478c4`, อื่น `#c9d8e3`/`#3d5a6b`) · `2FA` (`TOTP · ผูกแล้ว` เขียว / `ยังไม่ผูก` แดง) · `เข้าใช้ล่าสุด` · `สถานะ` (จุด 8×8 + `ใช้งาน`/`รอรับเชิญ`/`ระงับ`) · ปุ่ม `เปลี่ยน role` `reset 2FA` `ระงับ`/`ปลดระงับ`
  แถวของตัวเอง (`self: true`) ไม่มีปุ่มระงับ
  footnote: `การระงับมีผลทันทีกับ session ที่เปิดอยู่ — ไม่ต้องรอหมดอายุ token · ผู้ใช้ที่ยังไม่ผูก 2FA เข้าคอนโซลไม่ได้เลย ไม่ใช่แค่เตือน`

#### 9.8b สิทธิ์ต่อ role
Matrix grid `minmax(0,1fr) repeat(5,104px)` — คอลัมน์ `OWNER ADMIN TRADER VIEWER AUDITOR` (Mono 600 10.5px, OWNER สี `#1478c4`)
- โหมดอ่าน: `✓` `#0f7a4c` / `–` `#c9d8e3`; ปุ่ม `แก้ไขสิทธิ์` ที่หัวตาราง
- โหมดแก้: ทุกช่องเป็นปุ่ม `34×28` — เปิด: border `#b9dfca` พื้นเขียวอ่อน text `#0f7a4c`; ปิด: border `#d5e1ea` พื้นกลาง text `#8ea3af`; **เปลี่ยนแล้วยังไม่บันทึก**: border `#f4e6bd` พื้นเหลืองอ่อน text `#8a6a15`; คอลัมน์ OWNER ล็อก (tooltip `Owner มีสิทธิ์ทุกข้อเสมอ`)
- หัวตารางแสดง `เปลี่ยน N ช่อง` / `ยังไม่มีการเปลี่ยน` + `ยกเลิก` + `บันทึกและยืนยัน 2FA` (disabled เมื่อ diff = 0)

| ทำอะไรได้ | หมายเหตุ | O | A | T | V | Au |
|---|---|---|---|---|---|---|
| ดูภาพรวม กราฟ และโซน | | ✓ | ✓ | ✓ | ✓ | ✓ |
| อ่านบันทึกการตัดสินใจ | | ✓ | ✓ | ✓ | ✓ | ✓ |
| ปิดไม้ฉุกเฉิน | ยืนยันด้วย 2FA ทุกครั้ง | ✓ | – | ✓ | – | – |
| กด kill switch หยุดฉุกเฉิน | หยุดได้ ไม่ต้องยืนยันซ้ำ | ✓ | ✓ | ✓ | – | – |
| สลับ dry_run เป็นยิงจริง | Owner เท่านั้น | ✓ | – | – | – | – |
| แก้ไฟล์โปรไฟล์และ risk limit | Owner เท่านั้น | ✓ | – | – | – | – |
| เพิ่ม–ลบคู่เหรียญ | เขียนบล็อก [[symbols]] ในโปรไฟล์ | ✓ | – | – | – | – |
| จัดการผู้ใช้และย้าย role | | ✓ | ✓ | – | – | – |
| reset 2FA ของคนอื่น | | ✓ | ✓ | – | – | – |
| ส่งออกบันทึกทั้งหมด | | ✓ | ✓ | – | – | ✓ |

> การเปิด–ปิด `allow_short`, การสลับโหมด live/paper, การ start/stop engine และการตั้งค่าการแจ้งเตือน **ยังไม่มีแถวของตัวเอง** — prototype ถือว่าอยู่ใต้ `แก้ไฟล์โปรไฟล์และ risk limit` (Owner เท่านั้น) ถ้าต้องการให้ Admin ทำได้ ต้องเพิ่มแถวใหม่และคุยก่อน

#### 9.8c session ที่เปิดอยู่
grid `200px minmax(0,1fr) 190px 150px 124px`: `ผู้ใช้` (+ ป้าย `เครื่องนี้` สีเขียวถ้าเป็น session ปัจจุบัน) · `อุปกรณ์` · `ที่มา` (IP · เมือง) · `เริ่ม session` · ปุ่ม `ตัดออก` (ไม่มีในแถวของตัวเอง)
footnote: `session หมดอายุเองใน 12 ชั่วโมง · การตัดออกมีผลทันทีที่ request ถัดไป`

#### 9.8d บันทึกผู้ใช้
grid `170px 160px 1fr`: `เวลา (UTC)` · `ใคร` · `ทำอะไร` (action + detail บรรทัดล่าง); action ที่ danger สี `#c0392f`
footnote: `บันทึกนี้เขียนแล้วแก้ไม่ได้ · Auditor อ่านได้แต่ไม่เห็นค่า API key เพราะ log ผ่าน filter เดียวกับระบบเทรด`

#### 9.8e 2FA step-up modal (ใช้ร่วมทุก action)
- Overlay `position fixed; inset 0`, bg `rgba(3,10,15,.74)`, `place-items center`, `z-index 40`, `padding 40px`
- การ์ด `436px`, bg `linear-gradient(160deg,#e6eff7,#f4f8fb,#ffffff)`, border `1px solid #c9d8e3`, แถบบน `3px` ไล่สีน้ำเงิน, เนื้อ `padding 28px 30px 26px`
- เนื้อหา: label `ยืนยันตัวตนอีกครั้ง` → title (600 17px) → detail → [role dropdown ถ้าเป็น action เปลี่ยน role] → ช่องรหัส 6 หลัก (Mono 500 22px, letter-spacing .32em, center) → `รหัส 6 หลักจากแอป Authenticator ของคุณ` → ปุ่ม `ยกเลิก` (outline) + `ยืนยัน` (bg ไล่สีน้ำเงิน)
- ปุ่ม `ยืนยัน` เปิดเมื่อรหัสครบ 6 หลัก **และ** (ถ้าเป็น action เปลี่ยน role) role ที่เลือกต่างจากเดิม

| action | title | detail |
|---|---|---|
| ระงับ | `ระงับ <ชื่อ>` | `session ที่เปิดอยู่ของ <email> จะถูกตัดออกทันที และเข้าใช้ใหม่ไม่ได้จนกว่าจะปลดระงับ` |
| ปลดระงับ | `ปลดระงับ <ชื่อ>` | `ผู้ใช้จะเข้าคอนโซลได้อีกครั้งด้วยสิทธิ์ <role> เดิม` |
| reset 2FA | `reset 2FA ของ <ชื่อ>` | `ลบการผูก TOTP เดิม แล้วส่งลิงก์ตั้งค่าใหม่ไปที่ <email> · ระหว่างนี้ผู้ใช้เข้าคอนโซลไม่ได้` |
| เปลี่ยน role | `เปลี่ยน role ของ <ชื่อ>` | `สิทธิ์เปลี่ยนตามตารางทันที มีผลกับ session ที่เปิดอยู่ด้วย` + hint `จาก <A> เป็น <B>` (เลือก OWNER เพิ่ม ` · Owner แก้โปรไฟล์และสลับยิงจริงได้`) |
| ตัด session | `ตัด session ของ <ชื่อ>` | `<device> · <origin> · จะเด้งออกที่ request ถัดไป` |
| บันทึกสิทธิ์ | `บันทึกตารางสิทธิ์` | `เปลี่ยน N ช่อง · มีผลกับทุกคนใน role ที่แก้ รวม session ที่เปิดอยู่` |
| เชิญ | `ส่งคำเชิญผู้ใช้ใหม่` | `เชิญ <email> เป็น <role> · บันทึกในชื่อคุณ` |

---

## 10. Interactions & behavior

**Navigation** — rail = screen switch · symbol list → `symbol` (reset tab เป็น `chart`) · deep links: `ดูการตัดสินใจ` → symbol + tab `decision`, `เลือกเส้นทาง` / `cold start` → **ETH** + tab `coldstart`, `ใบสรุป` ในหน้าบันทึก → BTC หรือ SOL + tab `decision`, `ตั้งค่าการแจ้งเตือน` → `config`

**Auth flow** — `password` → `signIn` → `totp` → `verify` → `overview` · `เปลี่ยน` กลับ `password` (ล้างรหัส) · `ออก` → `login` ล้างรหัสผ่านและ TOTP · `locked` เข้าจากลิงก์ dev เท่านั้น (ของจริงมาจาก 401/423)

**สัญญาณสองฝั่ง (สำคัญ)** — ทุกที่ที่แสดงสัญญาณต้องอ่านทั้ง `long` และ `short`

| เงื่อนไข | ผลใน UI |
|---|---|
| `signal === 'long'` | verdict/decision ฝั่ง long (เขียว) |
| `signal === 'short'` และ `allowShort` | verdict/decision ฝั่ง short (แดง) + แผนกลับข้างถ้ามี long ค้าง |
| `signal === 'short'` และ `!allowShort` | `shortBlocked` (เหลือง) — ปิดฝั่งเดิมเท่านั้น |
| ไม่มีสัญญาณ | `noSignal` — ไม่ทำอะไร แต่ยังเขียน DecisionRecord |
| มีสัญญาณ + ถือฝั่งตรงข้าม | `isFlip` → สองขาในแท่งเดียว ขา 1 ต้อง fill ครบก่อนขา 2 |

**Step-up gate** — ทุก mutation ในหน้าผู้ใช้ + การสลับ paper → live + (ควรเพิ่ม) การบันทึกคีย์แจ้งเตือนของ live ต้องผ่าน modal 2FA · ยกเว้นเมื่อ `requireStepUp = false` (โหมดสาธิต) · **ของจริงต้องบังคับที่ endpoint ไม่ใช่ที่ UI**

**No optimistic destructive actions** — ลบคู่เหรียญใช้ 2-step inline confirm (ไม่ใช่ modal) พร้อมคำเตือนต่างกันตามว่าถืออยู่หรือไม่

**Permission matrix** — แก้แบบ draft (`permDraft` แยกจาก `perms`), ช่องที่ต่างจากเดิมย้อมเหลือง, `บันทึก` เท่านั้นที่ commit, `ยกเลิก` ทิ้ง draft

**Notifications** — mark read ทีละรายการ / ทั้งโหมด · toggle ต่อคู่ (ช่องทาง, โหมด) · accordion ฟอร์มคีย์ทีละช่องทาง · reveal ต่อช่องทาง · copy webhook (auto-reset 1800ms) · สลับโหมดแล้วรายการและ badge เปลี่ยนชุดทันที รายการที่อ่านแล้วในอีกโหมดคงสถานะเดิม

**Transitions** — prototype ไม่มี animation เลย state เปลี่ยนทันที มีแต่ hover ที่เปลี่ยนสี border/text ถ้าจะใส่ transition ให้ใช้สั้นๆ (`120ms ease`) กับ border-color/background เท่านั้น **อย่าใส่ slide/fade กับการเปลี่ยนหน้าจอ**

**Loading states** — ยังไม่มีใน prototype · คู่ที่เพิ่มใหม่แสดง `รอข้อมูล` + โน้ต `คู่ใหม่ · รอครบ 85 แท่ง` (zone BLACK) ไม่ใช่ skeleton

**Responsive** — ไม่รองรับใน prototype (1440px คงที่)

---

## 11. Charts

กราฟทุกตัวใน `design/assets/` เป็น **static SVG mock** ต้องแทนด้วยของจริง

| ไฟล์ | ใช้ที่ | ต้องกลายเป็น |
|---|---|---|
| `spark-{btc,eth,sol,doge}.svg` | ตารางภาพรวม (180×46) | sparkline 85 แท่ง ระบายสีตามโซน |
| `chart-{btc,eth,sol,doge}.svg` | แท็บกราฟ | candlestick รายวัน + EMA12 + EMA26 |
| `ribbon-{btc,eth,sol,doge}.svg` | ใต้ candlestick (สูง 18px) | แถบสีโซนหนึ่งช่องต่อหนึ่งแท่ง ใช้ zone palette |
| `h1.svg` | Cold start ทางที่ 1 | กราฟ 1h + mark จุดสัญญาณที่ผ่านไปแล้ว |
| `coldstart-eth.svg` | Cold start ทางที่ 2 | กราฟรายวัน + Slow Trail (`#e05252`) + Fast Trail (จุดฟ้า) + แถบโซนกำไร |
| equity curve | หน้ารายงาน | inline SVG อยู่ในไฟล์แล้ว — ใช้เป็น spec ของสไตล์ |

ของจริงต้องเพิ่มสิ่งที่ mock ยังไม่มี: ราคา liquidation ของสถานะที่ถืออยู่ และจุดเปิด/ปิดของทั้งสองฝั่ง
แนะนำ lightweight charting lib (เช่น `lightweight-charts`) หรือวาด SVG เอง — **อย่าใช้ default theme ของ lib** ต้อง override สี/แกน/ฟอนต์ให้ตรง token กราฟในดีไซน์ไม่มีกรอบ ไม่มี tooltip กล่องขาว ไม่มี gridline ตั้ง

---

## 12. Design Tokens

### 12.1 Fonts
Google Fonts: `IBM Plex Sans Thai` (400/500/600) + `IBM Plex Mono` (400/500/600) — โปรดักชันควร self-host

- ตัวเลข ค่าเทคนิค key ของ config, zone, side, timestamp → **Mono**
- ประโยคภาษาไทย หัวข้อ ปุ่ม → **Sans Thai**
- ข้อความผสมไทย+ตัวเลขใช้ stack `'IBM Plex Mono','IBM Plex Sans Thai',monospace` (Mono ไม่มี glyph ไทย → fallback อัตโนมัติ) — pattern นี้ใช้ทั่วไฟล์ ให้คงไว้

### 12.2 Type scale

| บทบาท | ค่า |
|---|---|
| H1 หน้า | 600 25px/1.25, letter-spacing -.01em |
| Hero number (decision) | 600 34px/1.15 Sans, -.02em |
| Hero number (close) | Mono 600 32px/1, -.02em |
| KPI number | Mono 600 28–30px/1 |
| Card title | 600 13.5–17px |
| Body | 400 12.5–13.5px, line-height 1.6–1.8 |
| Table cell | 400 12.5–13px (Mono สำหรับตัวเลข) |
| Table header | Mono 500 10.5px, letter-spacing .07em |
| Section label | Mono 500 10px, letter-spacing .09em, uppercase |
| Zone / side chip | Mono 600 11px, letter-spacing .07–.09em |
| Wordmark | Mono 600 15px, letter-spacing .22em, uppercase |
| TOTP input | Mono 500 26px, letter-spacing .34em (modal 17–22px / .32–.34em) |

### 12.3 Colors — surfaces & neutrals

| token | ค่า | ใช้ |
|---|---|---|
| body bg | `#eaf1f7` + `linear-gradient(155deg,#c3d8e8 0%,#dbe8f2 34%,#eff5f9 68%,#ffffff 100%)` fixed | พื้นหลังหน้า |
| surface (การ์ด, rail) | `linear-gradient(160deg,#e6eff7 0%,#f4f8fb 58%,#ffffff 100%)` | การ์ดหลัก |
| surface raised | `linear-gradient(180deg,#eef4f9 0%,#ffffff 100%)` | header ตาราง, input, top bar |
| surface hover | `#e8eef3` | hover ของ nav/row |
| selected row | `linear-gradient(90deg,#cfe3f4 0%,#eaf4fd 100%)` | nav/symbol ที่เลือก |
| unread row | `#f3f8fc` | แถวแจ้งเตือนที่ยังไม่อ่าน |
| border | `#d5e1ea` | ขอบการ์ด |
| border strong | `#c9d8e3` | ปุ่ม outline |
| divider | `#dde7ee` | เส้นคั่นในการ์ด |
| divider soft | `#e3ebf1` · `#eef2f6` | ใต้ header ตาราง, track |
| top rule | `#e4eff8` | `border-top 2px` ของแต่ละหน้าจอ |
| track | `#e3ebf1` | แถบ progress, toggle ปิด |

**Text** — `#0e1e29` primary · `#3d5a6b` secondary · `#5f7b8a` label · `#6f8896` muted · `#8ea3af` faint

### 12.4 Colors — accent & semantic

| ความหมาย | fill / gradient | text | tint bg | border |
|---|---|---|---|---|
| Primary blue | `linear-gradient(135deg,#0f6fb5 0%,#30AFFF 55%,#7fd6ff 100%)` | `#1478c4` | `#e6eff7` | `#c9d8e3` · focus `#0f6fb5` |
| live / อันตราย / short | `linear-gradient(135deg,#b5352b 0%,#e8776c 100%)` | `#c0392f` · `#b04a4a` | `linear-gradient(160deg,#f8d6d3,#fdeeec)` · row `#fdf0ee` | `#f3d2ce` · `#e2b4ae` |
| paper / สำเร็จ / long | `linear-gradient(135deg,#0f7a4c 0%,#48bd88 100%)` | `#0f7a4c` · `#4b7d64` | `linear-gradient(160deg,#cbead9,#edf8f2)` · row `#eaf7f0` | `#b9dfca` · `#a9cdb9` |
| เตือน / fallback | `linear-gradient(135deg,#c8981a 0%,#e9c247 100%)` | `#8a6a15` · `#8f6c13` | `linear-gradient(160deg,#f6e7bd,#fdf8ea)` | `#f4e6bd` |

### 12.5 Zone & side palette

```
GREEN  #17a866   เปิด long        LONG   แถบ #17a866  text #0f7a4c
BLUE   #2f7fe0   pre-long 2       SHORT  แถบ #e05252  text #c0392f
LBLUE  #92c8f2   pre-long 1       FLAT   แถบ #c9d8e3  text #5f7b8a
YELLOW #dfbb2a   pre-short 1
ORANGE #ef8b3c   pre-short 2
RED    #e05252   เปิด short
BLACK  #c3d4e0   —
```

**accent แถบแจ้งเตือน** `#e05252` แดง · `#dfbb2a` เหลือง · `#2f7fe0` น้ำเงิน · `#17a866` เขียว

### 12.6 Radius · shadow · spacing
- **Radius** `0` เกือบทุกที่ (การ์ด ปุ่ม chip) · `1px` เฉพาะ input, ปุ่มไอคอนเล็ก, swatch · ข้อยกเว้นเดียวคือ toggle switch ของการแจ้งเตือน (`13px` + ปุ่มกลม 50%) — เป็นการตัดสินใจทางดีไซน์ที่ตั้งใจ: คอนโซลนี้เป็นเครื่องมือ ไม่ใช่แอปผู้บริโภค **อย่าเปลี่ยนการ์ด/ปุ่มเป็นมุมโค้ง**
- **Shadow** ใช้ที่เดียวคือแผงแจ้งเตือน `0 18px 40px rgba(14,30,41,.16)` (dropdown ของ role ใช้เงาอ่อนแบบเดียวกันได้)
- **Spacing ที่ใช้จริง** 2 · 3 · 6 · 7 · 8 · 9 · 10 · 11 · 12 · 13 · 14 · 16 · 18 · 20 · 22 · 24 · 26 · 28 · 30 · 34 · 38 · 40
- **Padding ที่ซ้ำบ่อย** page `28px 34px 40px` · การ์ดใหญ่ `24px 26px` / `26px 28px` · การ์ดเล็ก `22px 24px` · แถวตาราง `15px 24px` / `17px 24px` / `19px 24px` · header ตาราง `12px 22px` / `14px 24px` / `15px 24px` · ปุ่มหลัก `10px 16px` / `13px` (เต็มความกว้าง) · ปุ่มเล็ก `6px 12px` / `7px 11px`
- **Link** `a { color:#1478c4; text-decoration:none }` · `a:hover { color:#1478c4 }`


---

## 13. State & data

### 13.1 State ทั้งหมด

```ts
// navigation
screen: 'login'|'overview'|'symbol'|'symbols'|'risk'|'log'|'report'|'users'|'config'
sym: 'btc'|'eth'|'sol'|'doge'
tab: 'chart'|'decision'|'coldstart'
q: string                                    // คำค้นใน rail

// mode
mode: 'live'|'paper'                         // โปรไฟล์ที่ดูและที่ active
engine: { live: boolean, paper: boolean }    // อิสระ อยู่รอดข้ามการสลับโหมด
modeConfirm: boolean, modeCode: string       // step-up dialog paper → live
cfgTab: 'live'|'paper'                       // แท็บหน้าตั้งค่า ตั้งต้นตาม mode

// auth
authStep: 'password'|'totp'|'locked'
email, pw, code: string                      // code กรองเฉพาะตัวเลข ตัดที่ 6

// users
utab: 'people'|'perms'|'sessions'|'log'
users: {id,name,email,role,status,twofa,last,self}[]
sessions: {id,user,device,origin,since,current}[]
perms / permDraft: {cap,note,cells[5]}[]     // draft = null เมื่อไม่ได้แก้
inviteOpen, invite{email,role}, inviteMenuOpen
stepUp: {kind,id,title,detail,from?,danger?} | null
                                             // kind: suspend|role|reset|revoke|perms|invite|mode
stepUpCode, roleChoice, roleMenuOpen

// symbols
pairs: {pair,bucket,bucketS,lev,tf,ex,enabled,holding,side,posLabel,note}[]
form: {pair,bucket,bucketS,lev,tf}
pendingRemove: string | null

// report
rRange: 'all'|'custom', rFrom, rTo: ISO date

// notifications
notifOpen: boolean
notifs: {id,mode,ch,accent,time,unread,title,detail}[]
notif: { line:{live,paper}, telegram:{live,paper} }
notifCfg: { line:{token,secret,to}, telegram:{token,chat,thread} }
notifEdit: 'line'|'telegram'|null
notifReveal: 'line'|'telegram'|null
notifTest: string | null
notifCopied: boolean                         // auto-reset 1800ms
```

**Derived** — `unreadCount = notifs.filter(n => n.mode === mode && n.unread).length` · `otherUnread` = ค้างในอีกโหมด · `live N/2 ช่องทาง · paper M/2 ช่องทาง` นับจาก `notif`

**Props ที่เปิดให้สลับเพื่อสาธิต** (ของจริงมาจาก backend ไม่ใช่ prop): `dryRun` (true) · `allowShort` (true) · `killSwitch` (false) · `showColdStart` (true) · `requireStepUp` (true) · `appVersion` `buildNo` `envLabel` `deployedAt`

### 13.2 ข้อมูลต่อ symbol ที่ backend ต้องส่งให้ครบ
`zone, state, longcond, shortcond, long, short, sinceLongCond, sinceShortCond, signal ('long'|'short'|null), side ('long'|'short'|'flat'), posLabel, posLine, close, fast, slow, bucketL, bucketS, lev, liq, fund, holding, pairNote, hasCold, coldReason`

### 13.3 Data fetching

| view | ข้อมูล | ที่มาในโปรเจกต์ |
|---|---|---|
| ภาพรวม / rail | โซน สัญญาณสองฝั่ง สถานะที่ถือ เลเวอเรจ margin ต่อ symbol | engine state + `decisions.jsonl` + position จาก exchange |
| เหรียญ · กราฟ | OHLCV 85+ แท่ง + EMA12/26 + zone ต่อแท่ง + funding rate | `docs/spec/02-action-zone.md` |
| เหรียญ · การตัดสินใจ | DecisionRecord ของแท่งล่าสุด (verdicts, size ledger, risk gates, flip legs, liq price) | `docs/spec/04-confluence-judge.md`, `05-position-sizing.md` |
| เหรียญ · Cold start | สถานะ 1h + ค่า CDC ATR Trailing Stop + ฝั่งที่ตกรถ | `reference/cdc_trailing_stop.pine` |
| ความเสี่ยง | risk limit จาก profile + kill switch + daily loss (mark-to-market) + margin/notional ต่อฝั่ง + leverage & liq ต่อเหรียญ | `docs/spec/06-risk-and-execution.md`, `state/killswitch.json` |
| บันทึก | `decisions.jsonl` paginate + filter (ฝั่ง, กลับข้าง, risk, fallback, เพดาน) | `docs/spec/08-runtime-pipeline.md` |
| รายงาน | ไม้ที่ปิดแล้วสองฝั่ง + fee/slippage/funding จริงจาก fill + equity series | คำนวณจาก decisions + order fills |
| คู่เหรียญ | บล็อก `[[symbols]]` (สองฝั่ง + leverage) | `config/live.toml`, `docs/spec/07-data-and-config.md` |
| ตั้งค่า | ไฟล์ profile + ผล validate (บรรทัด + ข้อความ) | `src/cane/config/settings.py` |
| ผู้ใช้ | ยังไม่มีในโค้ดเบส — ต้องสร้าง auth/RBAC/audit log ใหม่ | — |
| การแจ้งเตือน | ยังไม่มีในโค้ดเบส — ต้องสร้าง notification layer ใหม่ | — |

### 13.4 Endpoint ที่ต้องสร้างใหม่สำหรับการแจ้งเตือน

```
GET    /api/notifications?mode=live|paper&limit=50    → รายการ + unread count
POST   /api/notifications/{id}/read
POST   /api/notifications/read-all      { mode }
GET    /api/notifications/settings      → { channels, config (masked), webhooks }
PUT    /api/notifications/settings      { channel, mode, enabled }
PUT    /api/notifications/credentials   { channel, fields }   // step-up 2FA เมื่อกระทบ live
POST   /api/notifications/test          { channel }           → { ok, error? }
POST   /hooks/line/{id}                                        // LINE webhook receiver
```

- ค่า secret **ไม่ส่งกลับเต็ม** — ส่ง masked; "แสดงค่า" ต้องเป็น endpoint แยกที่บันทึก audit log
- เก็บใน `.env` / secret store ฝั่งเซิร์ฟเวอร์ ตาม pattern เดิม (`.gitignore` ตั้งแต่ commit แรก, log ผ่าน filter ที่ลบค่าของคีย์ที่มีคำว่า key / secret / token / password)
- การส่งจริงต้องเช็ค `notif[channel][mode]` ก่อนทุกครั้ง — engine โหมด paper ที่ปิด LINE ไว้ต้องไม่ยิงเข้ากลุ่มจริง

---

## 14. Assets

- `design/cane-mark.svg`, `design/cane-mark-mono.svg` — โลโก้ mark ที่เลือก (**Crook**: เส้นโค้งเปิด + หางชี้ขึ้น, path `M28.8 19.6 A11.5 11.5 0 1 0 28.8 34.4 L41.7 19.1`, `stroke-linecap round`, viewBox `0 0 48 48`) ฝัง inline `21×21` (stroke-width 6.5) ในกล่อง `30×30`
  `design/cane Logo 1b.dc.html` มี kit เต็ม: ขนาด 32 / 24 / 16 พร้อม stroke-width ต่างกันต่อขนาด (5.5 / 6 / 7) — ใช้ค่าตามไฟล์นั้น ไม่ใช่ scale ตัวเดียว
- `design/assets/*.svg` — mock กราฟ (หัวข้อ 11) **ไม่ใช่ production asset**
- **ไอคอน** — ไม่มี icon font / icon library ทั้งคอนโซล ใช้ CSS shape ทั้งหมด (สามเหลี่ยมจาก border trick, สี่เหลี่ยม, `→ ▼ ▲ ✓ – !` เป็น text glyph) **ยกเว้นกระดิ่งแจ้งเตือน** ที่เป็น inline SVG stroke (24×24 viewBox, stroke 1.7) ถ้าจะเปลี่ยนไปใช้ icon set ให้เลือกแบบ geometric stroke 1.5px และคุยก่อน
- ป้ายช่องทาง LINE / Telegram ใช้ตัวอักษรย่อบนสี่เหลี่ยมไล่สี **ไม่ใช่โลโก้จริง** — ถ้าต้องการโลโก้แบรนด์ ให้ใช้ asset ทางการของแต่ละแพลตฟอร์มตามข้อกำหนดการใช้แบรนด์
- ฟอนต์โหลดจาก Google Fonts — โปรดักชันควร self-host

---

## 15. หมายเหตุสำคัญก่อนเริ่มเขียน

1. **ห้ามใส่ปุ่มที่ทำให้เปิดไม้นอกแท่งสัญญาณ** — ยกเว้นเส้นทาง cold start (ทางที่ 2) ที่บังคับตั้ง SL และ RR ≥ 2:1 นี่คือกฎของระบบ ไม่ใช่ข้อจำกัดของ UI
2. **โหมดทางเดียวต่อเหรียญ** — ห้ามมี UI ที่ทำให้เกิด long และ short พร้อมกันในคู่เดียว การกลับข้างต้องเป็นสองขาเรียงกัน และขา 2 ต้องยกเลิกถ้าขา 1 ไม่ fill ครบ (`flip_aborted`)
3. **`dry_run` default = true ทุกโปรไฟล์** รวม live — การส่งออเดอร์จริงต้องจงใจ และเห็นชัดในทุกหน้าที่เกี่ยวข้อง (ชิปแดง `ยิงจริง` / `ส่งออเดอร์จริง`)
4. **`allow_short` ปิดได้ทั้งระบบและรายเหรียญ** — เมื่อปิด ระบบยัง **ปิดสถานะ** ตามสัญญาณฝั่งตรงข้ามเสมอ แต่ไม่เปิดไม้ใหม่ UI ต้องแยกสองเรื่องนี้ให้ชัด อย่าย่อเป็น "ไม่ทำอะไร"
5. **DecisionRecord เขียนทุกแท่งทุก symbol** รวมกรณี "ไม่ทำอะไร" และกรณีกลับข้าง (สองขาในเรคคอร์ดเดียว) — UI ต้องแสดงเท่าเทียมกัน ไม่ซ่อน
6. **เลเวอเรจและ liquidation เป็นข้อมูลชั้นเดียวกับขนาดไม้** — ทุกที่ที่แสดง % ของ bucket ต้องแสดง margin, notional และระยะห่าง liquidation ควบไปด้วย อย่าตัดออกเพราะพื้นที่ไม่พอ
7. **engine live กับ engine paper เป็นคนละตัว** — การสลับโหมดที่ดูอยู่ต้องไม่ start/stop engine และการแจ้งเตือนต้องเช็ค toggle ของโหมดที่ยิง ไม่ใช่โหมดที่ผู้ใช้กำลังดู
8. **"ยังไม่มีในสเปก" เป็นป้ายที่ต้องคงไว้** — ตรงไหนที่ดีไซน์เดาค่าไปเอง ในของจริงต้องตัดสินใจก่อน ไม่ใช่ปล่อยผ่าน
9. **Copy ทั้งหมดในเอกสารนี้เป็นข้อความสุดท้าย** — คำอธิบายภาษาไทยเลือกคำมาเพื่อความแม่นยำ ("กฎไม้เรียว", "ตกไปที่ไม้พื้นฐาน", "โหมดทดลอง", "โหมดทางเดียว", "กลับข้าง") อย่าเปลี่ยนเป็นคำที่ทั่วไปกว่าโดยไม่ถาม

เอกสารสเปกของระบบอยู่ในโค้ดเบสเดิม (ไม่ได้ copy มา) — อ่านคู่กันเสมอ:
`docs/spec/00-overview.md` … `08-runtime-pipeline.md` · `docs/decisions.md` · `config/live.toml` · `config/paper.toml` · `reference/cdc_action_zone.pine` · `reference/cdc_trailing_stop.pine`

> สเปกในโค้ดเบสอาจยังเขียนแบบ spot/long-only อยู่ — ถ้าขัดกับดีไซน์นี้ ให้ถือว่า **สเปกเป็นตัวจริง** และกลับมาถามก่อนเขียน UI ตามดีไซน์

---

## 16. Files

```
design_handoff_cane/
├── README.md                          ← ไฟล์นี้ · สเปกฉบับเดียวจบ
└── design/
    ├── cane Console Light.dc.html     ← สเปกหลัก · ทุกหน้าจอ · live/paper · notifications
    ├── cane Logo 1b.dc.html           ← logo kit ที่เลือก (Crook)
    ├── support.js                     ← runtime ของ prototype (ไม่ต้อง port)
    ├── cane-mark.svg / cane-mark-mono.svg
    └── assets/                        ← mock กราฟ sparkline ribbon
```
