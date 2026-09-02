"""ตาราง config — เวอร์ชันเป็นของที่เพิ่มได้ ไม่ใช่ของที่แก้

หนึ่งเวอร์ชัน = หัวใน `config_versions` + ลูกห้าตารางที่เขียนพร้อมกันในทรานแซกชันเดียว
เวอร์ชันครึ่งใบเกิดไม่ได้ เพราะผู้เรียกเป็นคนถือทรานแซกชัน (ดู `repo/__init__.py`)

**`is_active` เป็นตัวชี้ ไม่ใช่ค่าของเวอร์ชัน** — `activate()` ปิดตัวเก่าแล้วเปิดตัวใหม่
สองคำสั่งในทรานแซกชันเดียว ไม่ใช่คำสั่งเดียว เพราะ partial unique index ตรวจทีละแถว
ตอนเขียน การสลับด้วยคำสั่งเดียวจึงชนกับตัวเองได้แม้สภาพปลายทางจะถูก
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection, func, select

from cane.config.settings import Settings
from cane.db.schema import (
    config_broker,
    config_data,
    config_risk,
    config_settings,
    config_symbols,
    config_versions,
)
from cane.db.types import (
    now_ms,
    pct_from_db,
    pct_to_db,
    price_from_db,
    price_to_db,
    store_symbol,
)

#: ที่มาของเวอร์ชันที่ schema ยอมรับ (`ck_config_versions_source`)
SOURCES = ("toml_seed", "console", "migration")


@dataclass(frozen=True, slots=True)
class ConfigVersion:
    """หัวของเวอร์ชันหนึ่ง — ข้อมูลที่คอนโซลใช้แสดงประวัติการแก้ config

    `created_by_user_id` เป็น `None` เมื่อไม่ใช่คนกด (seed จากไฟล์ หรือ migration)
    FK ไปตาราง `users` มาพร้อมใบ 20 ที่เป็นคนสร้างตารางนั้น
    """

    id: int
    profile: str
    version: int
    source: str
    note: str | None
    created_ts: int
    created_by_user_id: int | None
    is_active: bool


def _row_to_version(row) -> ConfigVersion:  # noqa: ANN001
    return ConfigVersion(
        id=row.id,
        profile=row.profile,
        version=row.version,
        source=row.source,
        note=row.note,
        created_ts=row.created_ts,
        created_by_user_id=row.created_by_user_id,
        is_active=row.is_active,
    )


def insert_version(
    conn: Connection,
    settings: Settings,
    *,
    source: str,
    note: str | None = None,
    created_by_user_id: int | None = None,
    created_ts: int | None = None,
) -> ConfigVersion:
    """บันทึกเวอร์ชันใหม่ **ไม่ activate ให้** — คืนหัวของเวอร์ชันที่เพิ่งเขียน

    แยกการบันทึกออกจากการเปิดใช้โดยเจตนา: คอนโซลต้องบันทึกร่างไว้ได้โดยที่ engine
    ยังเดินด้วยค่าเดิม และการเปิดใช้เป็นการกระทำที่ต้องมีคนตัดสินใจอีกครั้งหนึ่ง

    เลขเวอร์ชันนับต่อ profile จากค่าสูงสุดที่มี — สองคนกดพร้อมกันจะได้เลขเดียวกัน
    แล้ว `UNIQUE (profile, version)` ปฏิเสธคนที่สอง ซึ่งเป็นผลที่ต้องการ:
    เวอร์ชันที่หายไปเพราะถูกทับเงียบๆ แย่กว่าการต้องกดใหม่

    **ตรวจซ้ำก่อนเขียน** ไม่ใช่เชื่อผู้เรียก — `Settings(...)` ที่ประกอบด้วยมือใน
    โค้ดผ่านแค่การตรวจระดับฟิลด์ ไม่ผ่าน `cross_checks` (เช่น `leverage` เกิน
    `max_leverage` ซึ่ง DB เขียนเป็น CHECK ไม่ได้) เวอร์ชันแบบนั้นเขียนลงได้แต่
    `settings_of()` จะปฏิเสธตอนอ่าน — กลายเป็นเวอร์ชันที่เก็บแล้วอ่านไม่ได้
    ซึ่งแย่กว่าการเขียนไม่สำเร็จตั้งแต่แรก
    """
    from cane.config.validate import validate_settings

    validate_settings(settings.model_dump(), source=f"config version ของ {settings.profile}")

    if source not in SOURCES:
        known = ", ".join(SOURCES)
        raise ValueError(f"source {source!r} ไม่รู้จัก — มีแต่ {known}")

    stamp = now_ms() if created_ts is None else created_ts
    profile = settings.profile

    next_version = conn.execute(
        select(func.coalesce(func.max(config_versions.c.version), 0) + 1).where(
            config_versions.c.profile == profile
        )
    ).scalar_one()

    head = conn.execute(
        config_versions.insert()
        .values(
            profile=profile,
            version=next_version,
            source=source,
            note=note,
            created_ts=stamp,
            created_by_user_id=created_by_user_id,
            is_active=False,
        )
        .returning(*config_versions.c)
    ).one()
    version_id = head.id

    conn.execute(
        config_settings.insert().values(
            config_version_id=version_id,
            profile=profile,
            timeframe=settings.timeframe,
            market=settings.market,
            cold_start=settings.cold_start,
            base_pct=pct_to_db(settings.base_pct),
            dry_run=settings.dry_run,
            allow_short=settings.allow_short,
            created_ts=stamp,
        )
    )

    conn.execute(
        config_symbols.insert(),
        [
            {
                "config_version_id": version_id,
                "profile": profile,
                "symbol": store_symbol(sym.symbol),
                "bucket_quote_long": price_to_db(sym.bucket_quote_long),
                "bucket_quote_short": (
                    None
                    if sym.bucket_quote_short is None
                    else price_to_db(sym.bucket_quote_short)
                ),
                "leverage": pct_to_db(sym.leverage),
                "allow_short": sym.allow_short,
                "enabled": sym.enabled,
                "created_ts": stamp,
            }
            for sym in settings.symbols
        ],
    )

    risk = settings.risk
    conn.execute(
        config_risk.insert().values(
            config_version_id=version_id,
            profile=profile,
            max_position_pct_long=pct_to_db(risk.max_position_pct_long),
            max_position_pct_short=pct_to_db(risk.max_position_pct_short),
            max_leverage=pct_to_db(risk.max_leverage),
            min_liq_buffer_pct=pct_to_db(risk.min_liq_buffer_pct),
            max_daily_loss_pct=pct_to_db(risk.max_daily_loss_pct),
            consecutive_loss_breaker=risk.consecutive_loss_breaker,
            created_ts=stamp,
        )
    )

    broker = settings.broker
    conn.execute(
        config_broker.insert().values(
            config_version_id=version_id,
            profile=profile,
            kind=broker.kind,
            exchange=broker.exchange,
            margin_mode=broker.margin_mode,
            position_mode=broker.position_mode,
            seed_quote=(
                None if broker.seed_quote is None else price_to_db(broker.seed_quote)
            ),
            created_ts=stamp,
        )
    )

    conn.execute(
        config_data.insert().values(
            config_version_id=version_id,
            profile=profile,
            exchange=settings.data.exchange,
            created_ts=stamp,
        )
    )

    return _row_to_version(head)


def activate(conn: Connection, version_id: int) -> ConfigVersion:
    """เปิดใช้เวอร์ชันหนึ่ง ปิดเวอร์ชันเดิมของ profile เดียวกันไปพร้อมกัน

    **ปิดก่อนเปิดสองคำสั่ง** ไม่ใช่คำสั่งเดียวที่ตั้งค่าตามเงื่อนไข — unique index
    แบบ partial ตรวจทีละแถวขณะเขียน คำสั่งเดียวที่สลับสองแถวจึงชนตัวเองได้แม้
    สภาพปลายทางจะมี active แค่หนึ่งแถว
    """
    row = conn.execute(
        select(config_versions).where(config_versions.c.id == version_id)
    ).first()
    if row is None:
        raise LookupError(f"ไม่มี config version id {version_id}")

    conn.execute(
        config_versions.update()
        .where(
            config_versions.c.profile == row.profile,
            config_versions.c.is_active,
        )
        .values(is_active=False)
    )
    return _row_to_version(
        conn.execute(
            config_versions.update()
            .where(config_versions.c.id == version_id)
            .values(is_active=True)
            .returning(*config_versions.c)
        ).one()
    )


def active_version(conn: Connection, profile: str) -> ConfigVersion | None:
    row = conn.execute(
        select(config_versions).where(
            config_versions.c.profile == profile,
            config_versions.c.is_active,
        )
    ).first()
    return None if row is None else _row_to_version(row)


def versions(conn: Connection, profile: str) -> list[ConfigVersion]:
    """ประวัติทุกเวอร์ชันของ profile เรียงใหม่ → เก่า (ลำดับที่คอนโซลแสดง)"""
    rows = conn.execute(
        select(config_versions)
        .where(config_versions.c.profile == profile)
        .order_by(config_versions.c.version.desc())
    ).all()
    return [_row_to_version(row) for row in rows]


def settings_of(conn: Connection, version_id: int) -> Settings | None:
    """ประกอบ `Settings` ของเวอร์ชันหนึ่งกลับมา · ไม่มีเวอร์ชันนั้น = `None`

    เวอร์ชันเก่าอ่านได้เสมอแม้จะไม่ active — นั่นคือเหตุผลทั้งหมดของการเก็บเป็น
    เวอร์ชัน: `decisions.config_version_id` (ใบ 03) ต้องพาไปอ่านค่าที่ใช้ตัดสิน
    แท่งนั้นได้จริง ไม่ใช่ค่าที่ใช้อยู่ ณ ตอนที่เปิดรายงาน

    **ตรวจซ้ำด้วย pydantic ตอนอ่าน** ไม่ใช่เชื่อแถวใน DB ตรงๆ — แถวที่ผ่าน CHECK
    ทุกข้อยังขัดกฎข้ามตารางได้ (เช่น `leverage` เกิน `max_leverage`) ซึ่งเป็นกฎที่
    DB เขียนไม่ได้ ถ้าโหลดขึ้นมาใช้เงียบๆ ระบบจะเทรดด้วย config ที่ validator
    ไม่เคยยอมให้ผ่าน
    """
    head = conn.execute(
        select(config_versions).where(config_versions.c.id == version_id)
    ).first()
    if head is None:
        return None

    core = conn.execute(
        select(config_settings).where(config_settings.c.config_version_id == version_id)
    ).one()
    risk = conn.execute(
        select(config_risk).where(config_risk.c.config_version_id == version_id)
    ).one()
    broker = conn.execute(
        select(config_broker).where(config_broker.c.config_version_id == version_id)
    ).one()
    data = conn.execute(
        select(config_data).where(config_data.c.config_version_id == version_id)
    ).one()
    symbols = conn.execute(
        select(config_symbols)
        .where(config_symbols.c.config_version_id == version_id)
        # เรียงตามชื่อเพื่อให้เวอร์ชันเดียวกันประกอบกลับมาได้เหมือนกันทุกครั้ง
        # ลำดับที่ไม่นิ่งจะทำให้การเทียบสองเวอร์ชันเห็นความต่างที่ไม่มีอยู่จริง
        .order_by(config_symbols.c.symbol)
    ).all()

    # import ที่นี่ ไม่ใช่หัวไฟล์ — `validate` นำเข้า `settings` และ repo นำเข้าทั้งคู่
    # การนำเข้าที่หัวไฟล์ทำให้ผูกวงกันตอน `cane.config` ถูกโหลดก่อน `cane.db`
    from cane.config.validate import validate_settings

    return validate_settings(
        {
            "profile": head.profile,
            "timeframe": core.timeframe,
            "market": core.market,
            "cold_start": core.cold_start,
            "base_pct": pct_from_db(core.base_pct),
            "dry_run": core.dry_run,
            "allow_short": core.allow_short,
            "symbols": [
                {
                    "symbol": row.symbol,
                    "bucket_quote_long": price_from_db(row.bucket_quote_long),
                    "bucket_quote_short": (
                        None
                        if row.bucket_quote_short is None
                        else price_from_db(row.bucket_quote_short)
                    ),
                    "leverage": pct_from_db(row.leverage),
                    "allow_short": row.allow_short,
                    "enabled": row.enabled,
                }
                for row in symbols
            ],
            "risk": {
                "max_position_pct_long": pct_from_db(risk.max_position_pct_long),
                "max_position_pct_short": pct_from_db(risk.max_position_pct_short),
                "max_leverage": pct_from_db(risk.max_leverage),
                "min_liq_buffer_pct": pct_from_db(risk.min_liq_buffer_pct),
                "max_daily_loss_pct": pct_from_db(risk.max_daily_loss_pct),
                "consecutive_loss_breaker": risk.consecutive_loss_breaker,
            },
            "broker": {
                "kind": broker.kind,
                "exchange": broker.exchange,
                "margin_mode": broker.margin_mode,
                "position_mode": broker.position_mode,
                "seed_quote": (
                    None
                    if broker.seed_quote is None
                    else price_from_db(broker.seed_quote)
                ),
            },
            "data": {"exchange": data.exchange},
        },
        source=f"config version {head.profile} v{head.version}",
    )


def active_settings(conn: Connection, profile: str) -> Settings | None:
    """config ที่ระบบใช้อยู่จริงของ profile หนึ่ง — ประตูที่ engine เข้ามาอ่าน

    ไม่มีเวอร์ชัน active = `None` ซึ่งแปลว่า **ยังไม่เทรด** ไม่ใช่ให้ใช้ค่าตั้งต้น
    (fail-closed ตาม spec/06) ผู้เรียกต้องจัดการกรณีนี้ ไม่ใช่ได้ค่าปลอมไปเดินต่อ
    """
    head = active_version(conn, profile)
    return None if head is None else settings_of(conn, head.id)
