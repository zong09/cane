"""`cane` — ทางเข้าบรรทัดคำสั่งของระบบ

ตอนนี้มีคำสั่งเดียว: `cane db seed` ที่พาไฟล์ TOML เดิมเข้า DB ครั้งแรก
**ยังไม่ใช่ทางเข้าของตัวบอท** (ลูปต่อการปิดแท่งเป็นของใบ 12) ไฟล์นี้จึงตั้งใจเล็ก
และไม่มีอะไรที่เป็นตรรกะของระบบอยู่ข้างใน — ตรรกะอยู่ใน repository กับ validator

seed ใช้ role **console** ไม่ใช่ engine เพราะการเขียน config เป็นสิทธิ์ของคน
ไม่ใช่ของบอท (`decisions.md` ข้อ 23) เส้นทางนี้จึงเป็นตัวยืนยันด้วยว่า grant
ของ console ถูกจริง ไม่ใช่แค่ถูกในเทสต์
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from cane.config.validate import ConfigError, load_profile
from cane.db.engine import make_engine
from cane.db.repo import config as config_repo

#: config ไม่ผ่าน — แยกจาก 1 (ล้มเพราะอย่างอื่น) เพื่อให้สคริปต์ที่เรียกแยกได้ว่า
#: "ค่าผิด" กับ "ต่อ DB ไม่ได้" ไม่ใช่เรื่องเดียวกัน
EXIT_INVALID_CONFIG = 2


def _seed(args: argparse.Namespace) -> int:
    try:
        settings = load_profile(args.source)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_INVALID_CONFIG

    if settings.profile != args.profile:
        print(
            f"ไฟล์ {args.source} เป็น profile {settings.profile!r} "
            f"แต่สั่ง --profile {args.profile!r}",
            file=sys.stderr,
        )
        return EXIT_INVALID_CONFIG

    engine = make_engine(role="console")
    try:
        with engine.begin() as conn:
            # ค่าเดิมเป๊ะ = ไม่สร้างเวอร์ชันใหม่ · การรัน seed ซ้ำเป็นเรื่องปกติ
            # (อยู่ในขั้นตอนตรวจงาน) ถ้าปล่อยให้สร้างเวอร์ชันทุกครั้ง ประวัติจะเต็ม
            # ไปด้วยเวอร์ชันที่ไม่มีอะไรต่างกัน แล้วประวัติจริงจะอ่านไม่ออก
            current = config_repo.active_settings(conn, settings.profile)
            if current == settings:
                active = config_repo.active_version(conn, settings.profile)
                assert active is not None
                print(
                    f"{settings.profile}: ไม่มีอะไรเปลี่ยน "
                    f"ยังใช้ v{active.version} อยู่"
                )
                return 0

            head = config_repo.insert_version(
                conn, settings, source="toml_seed", note=args.note
            )
            if args.activate:
                head = config_repo.activate(conn, head.id)
    finally:
        engine.dispose()

    state = "เปิดใช้แล้ว" if head.is_active else "บันทึกไว้ ยังไม่เปิดใช้"
    print(f"{head.profile}: v{head.version} {state} (จาก {args.source})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cane", description="เครื่องมือของบอท cane")
    commands = parser.add_subparsers(dest="group", required=True)

    db = commands.add_parser("db", help="งานฐานข้อมูล").add_subparsers(
        dest="command", required=True
    )

    seed = db.add_parser(
        "seed",
        help="พาไฟล์ TOML เข้า DB เป็นเวอร์ชันใหม่",
        description=(
            "อ่านไฟล์ profile หนึ่งไฟล์ ตรวจให้ผ่านทุกกฎ แล้วบันทึกเป็น config "
            "เวอร์ชันใหม่ · ค่าที่ไม่ต่างจากเวอร์ชันที่ใช้อยู่จะไม่สร้างเวอร์ชันใหม่"
        ),
    )
    seed.add_argument("--profile", required=True, choices=["live", "paper"])
    seed.add_argument(
        "--from",
        dest="source",
        required=True,
        metavar="PATH",
        help="ไฟล์ TOML ต้นทาง เช่น config/paper.toml",
    )
    seed.add_argument("--note", default=None, help="บันทึกกำกับเวอร์ชันนี้")
    seed.add_argument(
        "--no-activate",
        dest="activate",
        action="store_false",
        help="บันทึกไว้แต่ยังไม่เปิดใช้ (engine ยังเดินด้วยเวอร์ชันเดิม)",
    )
    seed.set_defaults(activate=True, run=_seed)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
