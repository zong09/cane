"""`cane` — ทางเข้าบรรทัดคำสั่งของระบบ

`cane db seed` พาไฟล์ TOML เดิมเข้า DB ครั้งแรก · `cane engine run` เป็นลูปต่อ profile
ที่ supervisor เรียก **ไม่ใช่คำสั่งที่คนพิมพ์เอง** · `cane serve` ยกคอนโซลขึ้น
`cane data import-bars` นำไฟล์ export รายวันของ TradingView เข้าตาราง `bars` · `cane replay run` เดินไปป์ไลน์ย้อนหลัง
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
from datetime import datetime, timezone
from pathlib import Path

from cane import log
from cane.config.validate import ConfigError, load_profile
from cane.data.csv_import import read_tradingview_csv
from cane.db.engine import make_engine
from cane.db.repo import config as config_repo
from cane.db.repo.bars import insert_bars
from cane.engine import loop, replay

#: config ไม่ผ่าน — แยกจาก 1 (ล้มเพราะอย่างอื่น) เพื่อให้สคริปต์ที่เรียกแยกได้ว่า
#: "ค่าผิด" กับ "ต่อ DB ไม่ได้" ไม่ใช่เรื่องเดียวกัน
EXIT_INVALID_CONFIG = 2

#: ยาวสำคัญกว่าความซับซ้อน · argon2 รับภาระที่เหลือ
MIN_PASSWORD_LEN = 12


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


def _import_bars(args: argparse.Namespace) -> int:
    """นำไฟล์ export รายวันของ TradingView เข้าตาราง `bars`

    ใช้ role **engine** เพราะแท่งราคาเป็นข้อมูลที่บอทเขียน (ดู GRANT ของ 0001) ไม่ใช่ของ console ·
    นำเข้าซ้ำได้ — แท่งที่มีอยู่แล้วถูกข้าม ไม่ถูกเขียนทับ (`insert_bars` เป็น `ON CONFLICT DO NOTHING`)
    """
    try:
        bars = read_tradingview_csv(Path(args.csv), args.timeframe)
    except (ValueError, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        return 1

    db = make_engine(role="engine")
    try:
        with db.begin() as conn:
            inserted = insert_bars(
                conn, args.market, args.symbol, args.timeframe, bars
            )
    finally:
        db.dispose()

    print(
        f"นำเข้า {inserted} จากทั้งหมด {len(bars)} แท่ง "
        f"({args.market} {args.symbol} {args.timeframe})"
    )
    return 0


def _utc_ms(text: str) -> int:
    """`YYYY-MM-DD` → epoch ms ของ 00:00 UTC วันนั้น"""
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _replay_judge(kind: str):
    """(client, model_id) ของตัวตัดสินที่เลือก — ตัวจริงขาดค่าใน `.env` แล้วล้มดังตรงนี้ ไม่ใช่กลางรัน"""
    if kind == "none":
        judge = replay.NoJudge()
        return judge, judge.model_id
    if kind == "typesafe":
        from cane.confluence.typesafe_client import TypesafeJudgeClient

        judge = TypesafeJudgeClient.from_env()
    else:
        from cane.confluence.openai_client import OpenAICompatJudgeClient

        judge = OpenAICompatJudgeClient.from_env()
    return judge, judge.model_id


def _replay_run(args: argparse.Namespace) -> int:
    """เดินไปป์ไลน์ย้อนหลังบน scratch database — ดู `engine/replay.py` ว่าทำไมต้องเป็น scratch

    สวม role `engine` เหมือนตัวจริง · `--from`/`--to` เป็นวันที่ (UTC) ของ **เวลาปิดแท่ง** รวมทั้งสองปลาย
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s · %(message)s")
    try:
        judge, model_id = _replay_judge(args.judge)
        db = make_engine(role="engine")
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        summary = replay.run_replay(
            db=db,
            profile=args.profile,
            from_close_ts=_utc_ms(args.date_from),
            to_close_ts=_utc_ms(args.date_to),
            judge=judge,
            model_id=model_id,
        )
    except replay.ReplayError as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        db.dispose()
    print(
        f"replay เสร็จ: {summary.bars} แท่ง · บันทึกการตัดสินใจ {summary.decisions} แถว "
        f"({', '.join(f'{m} {s}: {n}' for (m, s), n in sorted(summary.per_symbol.items()))})"
    )
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


def _seed_permissions(args: argparse.Namespace) -> int:
    """ใส่ตารางสิทธิ์ตั้งต้นเป็นเวอร์ชันแรกแล้วเปิดใช้ (spec/09)

    แยกจาก migration โดยเจตนา — สเปกบอกว่า matrix เป็น **ข้อมูล** ที่คอนโซลแก้ได้
    ไม่ใช่โครงสร้าง · ฐานที่ migrate แล้วแต่ยังไม่รันคำสั่งนี้ปฏิเสธทุก action
    เพราะไม่มีเวอร์ชัน active ให้เทียบ ซึ่งคือ fail-closed ที่ถูกต้อง
    """
    from cane.auth.matrix import DEFAULT_MATRIX
    from cane.db.repo import permissions as perms
    from cane.db.types import now_ms

    engine = make_engine(role="console")
    try:
        with engine.begin() as conn:
            current = perms.active_version(conn)
            if current is not None and perms.matrix_of(conn, current.id) == DEFAULT_MATRIX:
                print(f"ตารางสิทธิ์: ไม่มีอะไรเปลี่ยน ยังใช้เวอร์ชัน {current.id} อยู่")
                return 0
            version_id = perms.insert_version(conn, DEFAULT_MATRIX, created_ts=now_ms())
            perms.activate(conn, version_id)
    finally:
        engine.dispose()

    print(f"ตารางสิทธิ์: เปิดใช้เวอร์ชัน {version_id} (13 สิทธิ์ × 5 role)")
    return 0


def _create_owner(args: argparse.Namespace) -> int:
    """OWNER คนแรก — ไม่ได้มาจากคำเชิญเพราะยังไม่มีใคร login ได้ (spec/09)

    สร้างเป็น `pending` แล้วพิมพ์ลิงก์ผูก 2FA ออกมา · **ไม่มีเส้นทางไหนข้าม 2FA ได้**
    รวมทั้งเส้นนี้ — นั่นคือเหตุผลทั้งหมดที่มันไม่สร้างบัญชี `active` ให้เลย

    **รหัสผ่านไม่รับทาง argv** เพราะ argv ของ process อ่านได้จาก `ps` และค้างอยู่ใน
    ประวัติของ shell · ถามผ่าน getpass แทน
    """
    import getpass

    from cane.auth.secrets import hash_password, new_token
    from cane.db.repo import auth_tokens
    from cane.db.repo import users as users_repo
    from cane.db.types import now_ms

    password = getpass.getpass("รหัสผ่านตั้งต้น: ")
    if len(password) < MIN_PASSWORD_LEN:
        print(
            f"รหัสผ่านสั้นเกินไป ต้องอย่างน้อย {MIN_PASSWORD_LEN} อักษร", file=sys.stderr
        )
        return EXIT_INVALID_CONFIG
    if password != getpass.getpass("พิมพ์อีกครั้ง: "):
        print("รหัสผ่านสองครั้งไม่ตรงกัน", file=sys.stderr)
        return EXIT_INVALID_CONFIG

    now = now_ms()
    token = new_token()
    engine = make_engine(role="console")
    try:
        with engine.begin() as conn:
            if users_repo.by_email(conn, args.email) is not None:
                print(f"มีบัญชีอีเมลนี้อยู่แล้ว: {args.email}", file=sys.stderr)
                return EXIT_INVALID_CONFIG
            user_id = users_repo.create(
                conn,
                email=args.email,
                name=args.name,
                role="OWNER",
                created_ts=now,
                password_hash=hash_password(password),
            )
            auth_tokens.issue(
                conn, user_id=user_id, kind="invite", token=token, now=now
            )
    finally:
        engine.dispose()

    print(f"สร้างบัญชี OWNER แล้ว (ยังเป็น pending จนกว่าจะผูก 2FA)")
    print(f"เปิดลิงก์นี้เพื่อผูกแอป Authenticator — ใช้ได้ครั้งเดียว อายุ 72 ชั่วโมง:")
    print(f"  {args.base_url.rstrip('/')}/enrol/{token}")
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
    auth_cmds = commands.add_parser(
        "auth", help="ผู้ใช้และสิทธิ์ (ตั้งเครื่องครั้งแรก)"
    ).add_subparsers(dest="command", required=True)

    seed_perms = auth_cmds.add_parser(
        "seed-permissions",
        help="ใส่ตารางสิทธิ์ตั้งต้นเป็นเวอร์ชันแรกแล้วเปิดใช้",
        description=(
            "ฐานที่ยังไม่รันคำสั่งนี้ปฏิเสธทุก action เพราะไม่มีเวอร์ชัน active "
            "ให้เทียบ · รันซ้ำที่ค่าเดิมเป๊ะจะไม่สร้างเวอร์ชันใหม่"
        ),
    )
    seed_perms.set_defaults(run=_seed_permissions)

    create_owner = auth_cmds.add_parser(
        "create-owner",
        help="สร้าง OWNER คนแรก แล้วพิมพ์ลิงก์ผูก 2FA",
        description=(
            "บัญชีที่ได้เป็น `pending` · ยังเข้าคอนโซลไม่ได้จนกว่าจะผูก 2FA "
            "ผ่านลิงก์ที่พิมพ์ออกมา — ไม่มีเส้นทางไหนข้าม 2FA ได้ รวมทั้งเส้นนี้"
        ),
    )
    create_owner.add_argument("--email", required=True)
    create_owner.add_argument("--name", required=True)
    create_owner.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="ที่อยู่ของคอนโซล สำหรับประกอบลิงก์ผูก 2FA",
    )
    create_owner.set_defaults(run=_create_owner)

    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(run=_serve)

    data_cmds = commands.add_parser(
        "data", help="งานข้อมูลราคา"
    ).add_subparsers(dest="command", required=True)

    import_bars = data_cmds.add_parser(
        "import-bars",
        help="นำไฟล์ export รายวันของ TradingView เข้าตาราง `bars`",
        description=(
            "อ่านไฟล์ export รายวัน (คอลัมน์เวลามีแค่วัน) เป็นแท่งแล้วเขียนลงตาราง `bars` · "
            "นำเข้าซ้ำได้ แท่งที่มีอยู่แล้วถูกข้าม · ใช้ role engine"
        ),
    )
    import_bars.add_argument(
        "--csv", required=True, metavar="PATH", help="ไฟล์ CSV ที่ export จาก TradingView"
    )
    import_bars.add_argument(
        "--market", required=True, choices=["usdtm_perp", "spot"]
    )
    import_bars.add_argument(
        "--symbol", required=True, help="รูปสั้น เช่น BTC/USDT"
    )
    import_bars.add_argument("--timeframe", default="1d")
    import_bars.set_defaults(run=_import_bars)

    replay_cmds = commands.add_parser("replay", help="เดินไปป์ไลน์ย้อนหลัง").add_subparsers(
        dest="command", required=True
    )
    replay_run = replay_cmds.add_parser(
        "run",
        help="replay ช่วงเวลาหนึ่งผ่าน PaperBroker บน scratch database",
        description=(
            "ต้องตั้ง CANE_DB_DSN ไป database ที่ชื่อมีคำว่า replay (สร้าง migrate seed config นำเข้าแท่งก่อน) · "
            "เขียนการตัดสินใจทุกแท่งลงตาราง decisions ของ database นั้น · รันซ้ำใน database เดิมไม่ได้"
        ),
    )
    replay_run.add_argument("--profile", default="paper", choices=["paper"])
    replay_run.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    replay_run.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    replay_run.add_argument(
        "--judge",
        default="none",
        choices=["none", "typesafe", "openai"],
        help="none = ไม่มี LLM ทุกไม้ตกไป fallback ที่ base_pct · typesafe/openai อ่านค่าจาก .env",
    )
    replay_run.set_defaults(run=_replay_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.run(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
