"""ตัว render เทมเพลตตัวเดียวของทั้งคอนโซล

แยกออกมาจาก `app.py` เพราะ router ทุกตัวต้องใช้มัน ถ้าอยู่ใน `app.py` จะกลายเป็น
import วน (app → router → app)
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

WEB = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = WEB / "templates"
STATIC = WEB / "static"

templates = Jinja2Templates(directory=str(TEMPLATES))
