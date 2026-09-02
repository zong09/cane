"""Schema ของ config profile

ทุก model เป็น `extra="forbid"` โดยเจตนา — คีย์ที่พิมพ์ผิดต้องทำให้โหลดไม่ผ่าน
ไม่ใช่หายไปเงียบๆ แล้วปล่อยให้ระบบเดินด้วยค่าตั้งต้นที่ไม่มีใครตั้งใจ

risk limit ทุกตัว **ไม่มีค่าตั้งต้น** — ตั้งไม่ครบ = ไม่เทรด (spec/06 fail-closed)
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(extra="forbid")

Loc = tuple[str | int, ...]


class SymbolConfig(BaseModel):
    model_config = STRICT

    symbol: str
    bucket_quote_long: float = Field(gt=0)
    # เว้นได้ = เทรดฝั่ง long อย่างเดียว
    bucket_quote_short: float | None = Field(default=None, gt=0)
    leverage: float = Field(gt=0)
    allow_short: bool = False
    # false = พักไว้ คงบล็อกไว้แต่ข้ามในรอบคำนวณ
    enabled: bool = True


class RiskConfig(BaseModel):
    model_config = STRICT

    max_position_pct_long: float = Field(gt=0, le=100)
    max_position_pct_short: float = Field(gt=0, le=100)
    max_leverage: float = Field(gt=0)
    min_liq_buffer_pct: float = Field(gt=0, lt=100)
    max_daily_loss_pct: float = Field(gt=0, le=100)
    consecutive_loss_breaker: int = Field(gt=0)


class BrokerConfig(BaseModel):
    model_config = STRICT

    kind: Literal["ccxt", "paper"]
    exchange: str | None = None
    margin_mode: Literal["isolated", "cross"] = "isolated"
    # one-way เป็นข้อบังคับของระบบ ไม่ใช่ตัวเลือก — hedge ทำให้ถือสวนกันได้
    # ซึ่งละเมิดหลัก "หนึ่งฝั่งต่อเหรียญเสมอ" (spec/03)
    position_mode: Literal["one_way"] = "one_way"
    seed_quote: float | None = Field(default=None, gt=0)


class DataConfig(BaseModel):
    model_config = STRICT

    exchange: str


class Settings(BaseModel):
    model_config = STRICT

    profile: Literal["live", "paper"]
    timeframe: str
    market: Literal["usdtm_perp"]
    # ไม่ระบุ = ไม่เข้าเส้นทาง cold start เลย (fail-closed, spec/03)
    cold_start: Literal["wait_1h", "trailing", "skip"] | None = None
    base_pct: float = Field(ge=5, le=20)
    dry_run: bool = True
    # สวิตช์ระดับระบบ — ผลจริงของแต่ละเหรียญคือ AND กับ SymbolConfig.allow_short
    allow_short: bool = True
    symbols: list[SymbolConfig] = Field(min_length=1)
    risk: RiskConfig
    broker: BrokerConfig
    data: DataConfig


#: model ที่อยู่ ณ path หนึ่งของ config — ใช้หา "คีย์ที่รู้จัก" ตอนเดาคำที่พิมพ์ผิด
def model_at(path: Loc) -> type[BaseModel] | None:
    if path == ():
        return Settings
    if path == ("risk",):
        return RiskConfig
    if path == ("broker",):
        return BrokerConfig
    if path == ("data",):
        return DataConfig
    if len(path) == 2 and path[0] == "symbols" and isinstance(path[1], int):
        return SymbolConfig
    return None


def cross_checks(raw: Mapping[str, Any]) -> list[tuple[Loc, str, str]]:
    """ข้อบังคับที่ข้ามหลายคีย์ — pydantic ตรวจทีละ field จึงเห็นไม่ได้

    รับ **dict ดิบ** ไม่ใช่ `Settings` ที่ผ่านการตรวจแล้ว โดยตั้งใจ: คอนโซลแสดง
    รายการที่ต้องแก้ทั้งหมดในครั้งเดียว ถ้ารอให้ schema ผ่านก่อน คนแก้ไฟล์จะเห็น
    ปัญหาทีละข้อและต้องโหลดใหม่ทุกครั้ง ข้อไหนที่ข้อมูลไม่พอจะตรวจ ให้ข้ามไป
    เพราะ pydantic รายงานให้อยู่แล้ว

    คืน `(loc, message, detail)` โดย `loc` ชี้ไปที่คีย์ที่ **จุดชนวน** กฎ
    ไม่ใช่คีย์ที่หายไป เพราะคีย์ที่หายไม่มีบรรทัดให้ชี้
    """
    out: list[tuple[Loc, str, str]] = []

    broker = raw.get("broker")
    if isinstance(broker, Mapping):
        kind = broker.get("kind")
        if kind == "ccxt" and not broker.get("exchange"):
            out.append((
                ("broker", "kind"),
                'broker.kind = "ccxt" แต่ไม่ระบุ exchange',
                "ต้องรู้ venue ก่อนจึงจะต่อผ่าน ccxt ได้",
            ))
        if kind is not None and kind != "paper" and broker.get("seed_quote") is not None:
            out.append((
                ("broker", "seed_quote"),
                "seed_quote ใช้ได้เฉพาะ broker.kind = paper",
                "เงินตั้งต้นจำลองไม่มีความหมายกับ broker จริง",
            ))

    if raw.get("profile") == "paper" and raw.get("dry_run") is False:
        out.append((
            ("dry_run",),
            "profile = paper บังคับ dry_run = true",
            "paper ต้องไม่มีทางส่งคำสั่งจริงได้เลย",
        ))

    risk = raw.get("risk")
    max_leverage = risk.get("max_leverage") if isinstance(risk, Mapping) else None

    symbols = raw.get("symbols")
    if isinstance(symbols, list):
        for i, sym in enumerate(symbols):
            if not isinstance(sym, Mapping):
                continue
            name = sym.get("symbol") or f"symbols[{i}]"
            leverage = sym.get("leverage")
            if _is_number(leverage) and _is_number(max_leverage) and leverage > max_leverage:
                out.append((
                    ("symbols", i, "leverage"),
                    f"{name} leverage = {leverage} เกิน max_leverage = {max_leverage}",
                    "เพดานอัตราทดเป็นของ profile ไม่ใช่ของเหรียญ",
                ))
            if sym.get("allow_short") is True and sym.get("bucket_quote_short") is None:
                out.append((
                    ("symbols", i, "allow_short"),
                    f"{name} เปิด allow_short แต่ไม่มี bucket_quote_short",
                    "ฝั่ง short ต้องมีเงินของตัวเอง ไม่ยืมจากฝั่ง long",
                ))

    return out


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
