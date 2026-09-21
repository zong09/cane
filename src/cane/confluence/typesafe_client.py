"""adapter ที่คุยกับ typesafe.ai "System One" — ตัวที่สองของ `LlmClient` (ADR 30) · **ยังไม่เคยยิงด้วยคีย์จริง**

โครงเดียวกับ `openai_client.py`: หนึ่ง POST ได้ dict กลับมา ใช้ `urllib` ของ stdlib ไม่มี dependency ใหม่ ·
ต่างกันสองข้อ: **คีย์เป็นของบังคับ** (ไม่มีทางเว้นว่าง) และ **ปลายทางตอบเป็นค่ามีชนิด ไม่ใช่ JSON ตาม schema**

ปลายทางไม่รับ `response_format` — เราถามสองคำถามใน POST เดียว: `noul` (ความน่าจะเป็นที่ปัจจัยนี้ปรากฏ) กับ
`choice` (แท่งไหนคือหลักฐาน) แล้ว `ask()` แปลงคำตอบกลับเป็นรูปของ `VERDICT_JSON_SCHEMA` · ตารางแปลงกับข้อจำกัด
(confidence เป็นค่าประกอบ, หลักฐานเหลือแท่งเดียว, rationale สังเคราะห์) อยู่ที่ ADR 30

สิ่งที่ยังไม่รู้: ความนิ่งของ choice ที่เลือกแท่งจากดัชนี และความสัมพันธ์ของ noul กับ prompt ภาษาไทยของแต่ละ factor —
เทสต์พิสูจน์ได้แค่รูปคำขอกับการแปลงคำตอบ ต้องยิงจริงครั้งแรกด้วยมือก่อนเชื่อผล replay
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

#: ที่อยู่ของ endpoint เช่น `https://api.typesafe.ai` · ไม่มีค่าตั้งต้น (ตั้งใน `.env`)
BASE_URL_ENV = "CANE_TYPESAFE_BASE_URL"

#: ชื่อโมเดลตามที่ API เรียก เช่น `jev-latest` · ไม่มีค่าตั้งต้น
MODEL_ENV = "CANE_TYPESAFE_MODEL"

#: Bearer token — ของบังคับ ต่างจาก adapter แรกที่เว้นว่างได้เมื่อ gateway อยู่ที่ localhost
API_KEY_ENV = "CANE_TYPESAFE_API_KEY"

#: path ที่ต่อท้ายที่อยู่ข้างบน
ENDPOINT_PATH = "/v1/systemone"

#: เพดานเวลาต่อคำขอ (วินาที)
TIMEOUT_S = 60.0

#: จำนวนครั้งที่ยิงทั้งหมดรวมครั้งแรก
MAX_ATTEMPTS = 5

#: หน่วงครั้งแรก (วินาที) แล้วเท่าตัวทุกครั้ง แต่ไม่เกิน `BACKOFF_CAP_S`
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 30.0

#: status ที่แปลว่า "ลองใหม่" — rate limit กับปลายทางล้น · ตัวอื่นทุกตัว (รวม 401) ไม่ลองซ้ำ
RETRY_STATUSES = (429, 529)

#: choice หนึ่งข้อมีตัวเลือกได้ไม่เกินนี้
MAX_CHOICE_OPTIONS = 255

#: ปัจจัย "ปรากฏ" เมื่อความน่าจะเป็นของ noul ไม่ต่ำกว่าค่านี้
PRESENT_THRESHOLD = 0.5

#: ชื่อคำถามที่ส่งไปและอ่านกลับจากคำตอบ
Q_PRESENT = "present"
Q_EVIDENCE = "evidence"

# ── ข้อความของคำถาม ──────────────────────────────────────────────────────────
# ต่อท้าย prompt ของ factor (`system`) — prompt เดิมเขียนเป็นคำสั่งให้โมเดลแชต ส่วนที่นี่เพิ่มคำถามที่ต้องตอบจริง
# ของแต่ละข้อ · **ข้อความเหล่านี้ไม่อยู่ใน `prompt_hash`** — แก้ถ้อยคำแล้วคำตัดสินที่ cache ไว้ยังเป็นของถ้อยคำเก่า
# ต้องล้าง cache เอง (หรือเพิ่มเข้า `prompt_hash` เมื่อยิงจริงแล้วพบว่าถ้อยคำมีผลต่อคำตอบ)
PRESENT_SUFFIX = "## คำถามที่ต้องตอบ\n\nจากตัวเลขในสถานะข้างต้น ปัจจัยนี้ปรากฏอยู่บนแท่งที่ตัดสินหรือไม่"
EVIDENCE_SUFFIX = (
    "## คำถามที่ต้องตอบ\n\nแท่งไหนในตารางคือหลักฐานที่ชัดที่สุดของปัจจัยนี้ "
    "(ตอบเป็นดัชนีแท่งตามคอลัมน์ i ของตาราง)"
)
CRITERIA_TRUE = "ปัจจัยนี้ปรากฏชัดเจนตามเกณฑ์ที่เขียนไว้ข้างต้น"
CRITERIA_FALSE = "ปัจจัยนี้ไม่ปรากฏ หรือยังไม่ชัดพอจะเรียกว่ามี"
OPTION_TEXT = "แท่งที่ {index}"  # {index} คือดัชนีแท่งในตารางของ prompt
# ─────────────────────────────────────────────────────────────────────────────


class TypesafeJudgeClient:
    """`LlmClient` ที่คุยกับ typesafe.ai System One — ดูสถานะที่หัวไฟล์"""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        sleep=time.sleep,
        urlopen=urllib.request.urlopen,
    ) -> None:
        if not base_url:
            raise RuntimeError(f"ต้องมี base_url — ตั้ง {BASE_URL_ENV} ใน .env")
        if not model:
            raise RuntimeError(f"ต้องมีชื่อโมเดล — ตั้ง {MODEL_ENV} ใน .env")
        if not api_key:
            raise RuntimeError(f"ต้องมีคีย์ — ตั้ง {API_KEY_ENV} ใน .env (ปลายทางนี้ไม่มีโหมดไม่ใช้คีย์)")
        # ตัด `/` ท้ายทิ้ง — พิมพ์ต่างกันหนึ่งตัวอักษรต้องไม่ล้าง cache ทั้งก้อน (เหตุผลเดียวกับ adapter แรก)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key  # ห้ามปรากฏใน exception หรือ repr
        self._sleep = sleep
        self._urlopen = urlopen

    @classmethod
    def from_env(cls) -> TypesafeJudgeClient:
        """ประกอบจาก `.env` · ขาดค่าใดค่าหนึ่ง = `RuntimeError` ไม่ใช่ค่าตั้งต้น"""
        return cls(
            base_url=os.environ.get(BASE_URL_ENV, ""),
            model=os.environ.get(MODEL_ENV, ""),
            api_key=os.environ.get(API_KEY_ENV, ""),
        )

    @property
    def model_id(self) -> str:
        """ตัวตนของ "โมเดลที่ตัดสิน" ที่เข้า `prompt_hash` — host บวกชื่อโมเดล เหมือน adapter แรก"""
        return f"{self.base_url}|{self.model}"

    def __repr__(self) -> str:
        return f"TypesafeJudgeClient(base_url={self.base_url!r}, model={self.model!r})"

    def ask(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, object],
        factor: str,
        side: str,
        bar_indices: Sequence[int],
    ) -> dict[str, object]:
        """ถามหนึ่ง factor แล้วคืน dict ที่มีคีย์ของ `VERDICT_JSON_SCHEMA` ครบ

        `schema` รับไว้แต่ไม่ใช้ — ชนิดของคำถามกำหนดรูปคำตอบอยู่แล้ว · พังทุกกรณี = ยกข้อผิดพลาด
        ให้ `judge.py` แปลเป็น fallback (ADR 6) ไม่มีค่าคืนที่แปลว่าพัง
        """
        options = list(bar_indices)
        if not options:
            raise ValueError("bar_indices ว่าง — ไม่มีแท่งให้เลือกเป็นหลักฐาน")
        if len(options) > MAX_CHOICE_OPTIONS:
            # choice รับได้ไม่เกิน 255 ตัวเลือก — เก็บแท่งท้ายสุด (หน้าต่างของ prompt ยืดย้อนหลังได้ไกลกว่านี้)
            options = options[-MAX_CHOICE_OPTIONS:]

        body = {
            "state": user,
            "model": self.model,
            "questions": {
                Q_PRESENT: {
                    "type": "noul",
                    "instructions": system + "\n\n" + PRESENT_SUFFIX,
                    "criteria": {"true": CRITERIA_TRUE, "false": CRITERIA_FALSE},
                },
                Q_EVIDENCE: {
                    "type": "choice",
                    "instructions": system + "\n\n" + EVIDENCE_SUFFIX,
                    "criteria": {str(i): OPTION_TEXT.format(index=i) for i in options},
                },
            },
        }
        answers = self._post(body)["answers"]

        p = float(answers[Q_PRESENT]["noul"])
        if not 0 <= p <= 1:
            raise ValueError(f"ความน่าจะเป็นของ noul อยู่นอก 0..1: {p}")
        present = p >= PRESENT_THRESHOLD
        # ค่าที่ **ประกอบจากความน่าจะเป็น** ไม่ใช่ค่าที่โมเดลรายงาน (noul ไม่มีช่อง confidence) · ปัด 4 ตำแหน่งเพราะฐานปฏิเสธที่ละเอียดกว่า
        confidence = round(max(p, 1 - p), 4)

        if present:
            n = int(answers[Q_EVIDENCE]["choice"])
            if n not in options:
                raise ValueError(f"แท่งหลักฐาน {n} ไม่อยู่ในตัวเลือกที่เสนอ")
            evidence_bars = [n]
            rationale = f"typesafe p(yes)={p:.4f}; evidence bar {n}"
        else:
            evidence_bars = []
            rationale = f"typesafe p(yes)={p:.4f}"

        return {
            "factor": factor,
            "side": side,
            "present": present,
            "confidence": confidence,
            "evidence_bars": evidence_bars,
            "rationale": rationale,  # สังเคราะห์จากตัวเลข ไม่ใช่เหตุผลของโมเดล
        }

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """ยิงหนึ่งคำขอ ลองใหม่เมื่อ rate limit/ปลายทางล้น · ล้มแบบอื่นยกทันที ไม่มีคีย์อยู่ในข้อความ"""
        request = urllib.request.Request(
            f"{self.base_url}{ENDPOINT_PATH}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        raw: str | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                with self._urlopen(request, timeout=TIMEOUT_S) as response:
                    raw = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as error:
                if error.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
                    self._sleep(min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2**attempt)))
                    continue
                detail = error.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(
                    f"{self.base_url}{ENDPOINT_PATH} ตอบ {error.code}: {detail}"
                ) from error
            except urllib.error.URLError as error:
                raise RuntimeError(
                    f"ต่อ {self.base_url}{ENDPOINT_PATH} ไม่ได้: {error.reason}"
                ) from error
            break
        if raw is None:
            raise RuntimeError(
                f"{self.base_url}{ENDPOINT_PATH} ล้มเหลวหลังลอง {MAX_ATTEMPTS} ครั้ง"
            )
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(f"คำตอบไม่ใช่ JSON: {raw[:200]}") from error
