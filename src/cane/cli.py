"""`cane` — ทางเข้าบรรทัดคำสั่งของระบบ

`cane db seed` พาไฟล์ TOML เดิมเข้า DB ครั้งแรก · `cane engine run` เป็นลูปต่อ profile
ที่ supervisor เรียก **ไม่ใช่คำสั่งที่คนพิมพ์เอง** · `cane serve` ยกคอนโซลขึ้น
ไฟล์นี้ตั้งใจเล็กและไม่มีตรรกะของระบบอยู่ข้างใน — ตรรกะอยู่ใน repository กับ validator

seed ใช้ role **console** ไม่ใช่ engine เพราะการเขียน config เป็นสิทธิ์ของคน
ไม่ใช่ของบอท (`decisions.md` ข้อ 23) เส้นทางนี้จึงเป็นตัวยืนยันด้วยว่า grant
ของ console ถูกจริง ไม่ใช่แค่ถูกในเทสต์
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence

from cane import log
from cane.config.validate import ConfigError, load_profile
from cane.db.engine import make_engine
from cane.db.repo import config as config_repo
from cane.engine import loop

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


def _engine_run(args: argparse.Namespace) -> int:
    """ลูปของ engine หนึ่ง profile — **ไม่ใช่คำสั่งที่คนพิมพ์เอง** supervisor เรียก

    สวม role `engine` เท่านั้น · role `console` จะทำให้ process นี้ปลด kill switch ได้
    ซึ่ง spec/06 บอกว่าเป็นการกระทำของคนผ่านคอนโซลอย่างเดียว

    signal handler ติดตั้งที่นี่ไม่ใช่ใน `loop.run()` เพราะ handler เป็นของทั้ง process
    ฟังก์ชันที่เทสต์เรียกได้จึงไม่ควรไปทับของ pytest
    """
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s · %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[log.install(handler)])

    stopping = loop.StopFlag()
    # SIGINT ด้วย เพราะคนที่รันด้วยมือแล้วกด Ctrl-C ควรได้การจบรอบแบบเดียวกัน
    # ไม่ใช่ KeyboardInterrupt กลางการส่งออเดอร์
    signal.signal(signal.SIGTERM, stopping.request_stop)
    signal.signal(signal.SIGINT, stopping.request_stop)

    db = make_engine(role="engine")
    try:
        return loop.run(args.profile, db=db, stopping=stopping)
    finally:
        db.dispose()


def _serve(args: argparse.Namespace) -> int:
    """ยกคอนโซลขึ้น — **worker เดียวเท่านั้น**

    `Supervisor` ถือ handle ของ process ลูกไว้ในหน่วยความจำของ process ตัวเอง
    (spec/10 §1. หนึ่ง engine ต่อหนึ่ง profile) worker ที่สองจะได้ supervisor ที่ว่างเปล่า
    แล้ว `signal_stop()` ของมันคืน `False` ทุกครั้งโดยไม่มีอะไรบอกว่าทำไม ·
    ด้วยเหตุผลเดียวกันจึงไม่มี `--reload`: การ reload คือการเปลี่ยน process

    import ของ web stack อยู่ **ข้างในฟังก์ชัน** เพราะ `spawn_subprocess()` เรียก
    `python -m cane.cli engine run` — import ระดับโมดูลจะทำให้ engine ลูกทุกตัว
    จ่ายค่า import fastapi/uvicorn ทิ้งไปฟรีๆ ทั้งที่ไม่ได้ใช้
    """
    import uvicorn

    from cane.api.app import create_app

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s · %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[log.install(handler)])

    # `log_config=None` คือส่วนที่ทำให้ log ของ uvicorn ผ่าน RedactingFilter ด้วย:
    # ถ้าปล่อยให้ uvicorn ตั้ง config ของตัวเอง มันจะติด handler ของมันเองแล้วปิด
    # propagate ซึ่งแปลว่า log ฝั่งเว็บทั้งชุดเลี่ยงตัวกรองไป
    uvicorn.run(create_app(), host=args.host, port=args.port, log_config=None)
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

    engine_cmds = commands.add_parser("engine", help="ลูปของบอทต่อ profile").add_subparsers(
        dest="command", required=True
    )

    engine_run = engine_cmds.add_parser(
        "run",
        help="เดินลูปของ profile หนึ่งจนกว่าจะถูกสั่งหยุด",
        description=(
            "**ปกติไม่ได้พิมพ์เอง** — supervisor ในคอนโซลเป็นคนเรียก · สั่งเองได้เพื่อ "
            "ตรวจงาน แต่ต้องมีคนกด start ก่อน ไม่งั้นมันอ่านเจตนาแล้วออกทันที"
        ),
    )
    engine_run.add_argument("--profile", required=True, choices=["live", "paper"])
    engine_run.set_defaults(run=_engine_run)

    # คำสั่งชั้นเดียวตัวแรกของไฟล์นี้ (ที่เหลือเป็น group→command) · `serve console`
    # จะอ่านว่ามีคอนโซลหลายแบบให้เลือก ซึ่งไม่จริงและจะไม่จริง
    serve = commands.add_parser(
        "serve",
        help="ยกคอนโซลขึ้น",
        description=(
            "FastAPI + Jinja2 + HTMX (ADR 20) · supervisor ของ engine อยู่ในกระบวนการ "
            "นี้ จึงรัน worker เดียวและไม่มี --reload"
        ),
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(run=_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
