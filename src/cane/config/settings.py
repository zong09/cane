"""Schema ของ config profile

ทุก model เป็น `extra="forbid"` โดยเจตนา — คีย์ที่พิมพ์ผิดต้องทำให้โหลดไม่ผ่าน
ไม่ใช่หายไปเงียบๆ แล้วปล่อยให้ระบบเดินด้วยค่าตั้งต้นที่ไม่มีใครตั้งใจ

risk limit ทุกตัว **ไม่มีค่าตั้งต้น** — ตั้งไม่ครบ = ไม่เทรด (spec/06 fail-closed)
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

STRICT = ConfigDict(extra="forbid")

Loc = tuple[str | int, ...]

#: ทศนิยมที่เก็บได้จริงของแต่ละชนิดค่า — ตรงกับ `NUMERIC(9,4)` และ `NUMERIC(24,8)`
#: ในตาราง config (ดู `db/schema.py`)
PCT_PLACES = 4
MONEY_PLACES = 8


def require_scale(value: float, places: int, field: str = "") -> float:
    """ปฏิเสธค่าที่ละเอียดกว่าที่เก็บได้ **ไม่ใช่ปัดให้เงียบๆ**

    ที่เก็บถือ `base_pct` ไว้ 4 ตำแหน่ง ถ้ารับ `10.00001` เข้ามาแล้วปัดเป็น `10.0000`
    ระบบจะเทรดด้วยค่าที่ไม่ใช่ค่าที่คนกรอก ซึ่งเป็นความล้มเหลวแบบเดียวกับที่
    [decisions #18](../../../docs/decisions.md) ปิดประตูไว้ ("ค่าที่ระบบใช้ต้องไม่ต่าง
    จากค่าที่คนเห็น") · การปัดที่ไม่มีใครเห็นแย่กว่าการปฏิเสธที่คนเห็น

    ผลพลอยได้ที่ต้องการด้วย: `cane db seed` เทียบค่าเดิมกับค่าใหม่เพื่อไม่สร้าง
    เวอร์ชันซ้ำ ค่าที่ถูกปัดตอนเก็บจะเทียบไม่เท่ากับที่อ่านกลับมาเสมอ แล้วการรัน
    seed ทุกครั้งจะสร้างเวอร์ชันใหม่ที่ไม่มีอะไรต่างกัน

    อยู่เป็นฟังก์ชันธรรมดา **ไม่ใช่แค่ pydantic validator** เพราะชั้นบันทึกการตัดสินใจ
    (`db/repo/decisions.py`) เป็น frozen dataclass ที่เรียก validator ของ pydantic ไม่ได้
    แต่ต้องเคารพกฎเดียวกัน — `price_to_db()`/`pct_to_db()` ใน `db/types.py` **ปัด**
    (ROUND_HALF_EVEN) ตัวปฏิเสธจึงต้องมาก่อน · `field` ใส่ไว้ให้ผู้เรียกที่ไม่มี
    location ของ pydantic บอกได้ว่าคอลัมน์ไหนผิด

    **ห้ามย้ายฟังก์ชันนี้ไป `db/types.py`** — `cane/db/__init__.py` import `repo.*` และ
    `repo/config.py` import โมดูลนี้อยู่แล้ว ทิศที่ปลอดภัยมีทางเดียวคือ `db` → `config`
    """
    where = f"{field}: " if field else ""
    try:
        exponent = Decimal(str(value)).as_tuple().exponent
    except InvalidOperation:  # pragma: no cover - pydantic กรอง nan/inf ไปก่อน
        raise ValueError(f"{where}ไม่ใช่ตัวเลขที่เก็บได้") from None
    if isinstance(exponent, int) and -exponent > places:
        raise ValueError(f"{where}ทศนิยมได้ไม่เกิน {places} ตำแหน่ง (ที่เก็บถือได้เท่านี้)")
    return value


def _no_more_places_than(places: int):
    """ห่อ `require_scale()` ให้เป็น `AfterValidator` ของ pydantic"""

    def check(value: float) -> float:
        return require_scale(value, places)

    return check


#: เปอร์เซ็นต์และตัวคูณ — 4 ทศนิยม
Pct = Annotated[float, AfterValidator(_no_more_places_than(PCT_PLACES))]
#: จำนวนเงิน — 8 ทศนิยม
Money = Annotated[float, AfterValidator(_no_more_places_than(MONEY_PLACES))]


class SymbolConfig(BaseModel):
    model_config = STRICT

    symbol: str
    #: ตลาดของเหรียญนี้ — **บังคับกรอก ไม่มีค่าตั้งต้นโดยเจตนา** (decisions #26)
    #:
    #: ค่าตั้งต้นที่เป็นไปได้มีแต่ `usdtm_perp` ซึ่งเป็นตลาดที่มี leverage และมี
    #: liquidation · การเดาให้แปลว่าคนที่ลืมพิมพ์บรรทัดนี้จะได้ความเสี่ยงที่ไม่ได้ขอ
    #: ซึ่งเป็นทิศตรงข้ามกับ fail-closed ที่ไฟล์นี้ยึดกับ risk limit อยู่แล้ว
    market: Literal["usdtm_perp", "spot"]
    bucket_quote_long: Money = Field(gt=0)
    # เว้นได้ = เทรดฝั่ง long อย่างเดียว
    bucket_quote_short: Money | None = Field(default=None, gt=0)
    leverage: Pct = Field(gt=0)
    allow_short: bool = False
    # false = พักไว้ คงบล็อกไว้แต่ข้ามในรอบคำนวณ
    enabled: bool = True


class RiskConfig(BaseModel):
    model_config = STRICT

    max_position_pct_long: Pct = Field(gt=0, le=100)
    max_position_pct_short: Pct = Field(gt=0, le=100)
    max_leverage: Pct = Field(gt=0)
    min_liq_buffer_pct: Pct = Field(gt=0, lt=100)
    max_daily_loss_pct: Pct = Field(gt=0, le=100)
    consecutive_loss_breaker: int = Field(gt=0)


class BrokerConfig(BaseModel):
    model_config = STRICT

    kind: Literal["ccxt", "paper"]
    exchange: str | None = None
    margin_mode: Literal["isolated", "cross"] = "isolated"
    # one-way เป็นข้อบังคับของระบบ ไม่ใช่ตัวเลือก — hedge ทำให้ถือสวนกันได้
    # ซึ่งละเมิดหลัก "หนึ่งฝั่งต่อเหรียญเสมอ" (spec/03)
    position_mode: Literal["one_way"] = "one_way"
    seed_quote: Money | None = Field(default=None, gt=0)


class DataConfig(BaseModel):
    model_config = STRICT

    exchange: str


class Settings(BaseModel):
    model_config = STRICT

    profile: Literal["live", "paper"]
    timeframe: str
    # ไม่ระบุ = ไม่เข้าเส้นทาง cold start เลย (fail-closed, spec/03)
    cold_start: Literal["wait_1h", "trailing", "skip"] | None = None
    base_pct: Pct = Field(ge=5, le=20)
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
            # สามข้อของ spot — `schema.py` เขียนเป็น CHECK ไว้แล้วทั้งสามข้อ ที่ทำซ้ำ
            # ที่นี่เพราะคนที่กรอกฟอร์มต้องเห็น **ชื่อฟิลด์ที่ผิด** ไม่ใช่ข้อความของ
            # Postgres ที่บอกแค่ชื่อ constraint · ไม่ใช่ข้อบังคับที่ตั้งใหม่
            if sym.get("market") == "spot":
                if sym.get("allow_short") is True:
                    out.append((
                        ("symbols", i, "allow_short"),
                        f"{name} อยู่บน spot จึงเปิด allow_short ไม่ได้",
                        "spot ขายชอร์ตไม่ได้ — แดงคือขายออกให้แบน ไม่ใช่เปิดฝั่งตรงข้าม",
                    ))
                leverage = sym.get("leverage")
                if _is_number(leverage) and leverage != 1:
                    out.append((
                        ("symbols", i, "leverage"),
                        f"{name} อยู่บน spot จึงต้องมี leverage = 1 (ได้ {leverage})",
                        "spot ไม่มีอัตราทด — ใส่ 1 เพื่อให้สูตร notional = margin × leverage เดินเส้นทางเดียว",
                    ))
                if sym.get("bucket_quote_short") is not None:
                    out.append((
                        ("symbols", i, "bucket_quote_short"),
                        f"{name} อยู่บน spot จึงไม่มีกระเป๋าฝั่ง short",
                        "ฝั่ง short ไม่มีอยู่จริงบน spot ไม่ใช่มีแล้วเป็นศูนย์",
                    ))

    return out


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
