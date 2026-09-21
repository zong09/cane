"""อ่านไฟล์ export รายวันของ TradingView — `read_tradingview_csv()` กับ `OfflineClient`

ไม่แตะฐาน (ส่วนที่ต้องมีตารางอยู่ใน `test_csv_import_db.py`) — ที่นี่พิสูจน์การแปลงแถว CSV เป็น
`Bar` และด่านที่กันไฟล์ผิด: timeframe ที่รับไม่ได้ · คอลัมน์หาย · วันซ้ำ · เซลล์ว่าง

**ทุกด่านต้องล้มดัง** ไฟล์ที่ขาดแท่งหรือมีแท่งซ้ำแล้วผ่านเงียบๆ ทำให้ indicator คำนวณบนชุดที่ไม่ครบ
โดยไม่มีอะไรบอก และแท่งที่ลงตาราง `bars` แล้วแก้ไม่ได้อีก (engine ไม่มี UPDATE/DELETE)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cane.cli import build_parser
from cane.data import Bar, OfflineClient, read_tradingview_csv

FIXTURE = (
    Path(__file__).parent / "fixtures" / "action_zone" / "BINANCE_BTCUSDT.P, 1D.csv"
)

DAY = 86_400_000
HEADER = "time,open,high,low,close,Volume\n"


def _csv(tmp_path, body: str, *, header: str = HEADER) -> Path:
    path = tmp_path / "bars.csv"
    path.write_text(header + body, encoding="utf-8")
    return path


def test_the_fixture_reads_into_bars_in_order():
    bars = read_tradingview_csv(FIXTURE, "1d")

    assert len(bars) == 2564
    assert all(isinstance(bar, Bar) for bar in bars)
    # 2019-09-08 00:00 UTC — วันเปิดของแท่งแรก และปิดหนึ่งวันหลังจากนั้น
    assert bars[0].open_ts == 1_567_900_800_000
    assert bars[0].close_ts == bars[0].open_ts + DAY
    assert all(a.open_ts < b.open_ts for a, b in zip(bars, bars[1:]))


def test_a_timeframe_finer_than_a_day_is_refused():
    """คอลัมน์เวลามีแค่วัน จึงบอกเวลาเปิดของแท่ง 1h ไม่ได้ — ห้ามเดา"""
    with pytest.raises(ValueError, match="1d"):
        read_tradingview_csv(FIXTURE, "1h")


def test_a_missing_column_is_named_in_the_error(tmp_path):
    path = _csv(tmp_path, "2020-01-01,100,101,99,100.5\n", header="time,open,high,low,close\n")

    with pytest.raises(ValueError, match="Volume"):
        read_tradingview_csv(path, "1d")


def test_two_rows_for_the_same_day_are_refused(tmp_path):
    path = _csv(
        tmp_path,
        "2020-01-01,100,101,99,100.5,10\n2020-01-01,100,101,99,100.5,10\n",
    )

    with pytest.raises(ValueError, match="2020-01-01"):
        read_tradingview_csv(path, "1d")


def test_an_empty_cell_is_refused_not_skipped(tmp_path):
    path = _csv(tmp_path, "2020-01-01,100,101,,100.5,10\n")

    with pytest.raises(ValueError, match="ว่าง"):
        read_tradingview_csv(path, "1d")


def test_rows_out_of_order_come_back_sorted(tmp_path):
    path = _csv(
        tmp_path,
        "2020-01-02,100,101,99,100.5,10\n2020-01-01,100,101,99,100.5,10\n",
    )

    bars = read_tradingview_csv(path, "1d")

    assert [bar.open_ts for bar in bars] == sorted(bar.open_ts for bar in bars)


def test_the_offline_client_fetches_nothing_and_has_no_funding():
    client = OfflineClient()

    assert client.fetch_ohlcv("BTC/USDT", "1d") == []
    assert client.fetch_ohlcv("BTC/USDT", "1d", since=1, limit=500) == []
    with pytest.raises(RuntimeError, match="funding"):
        client.fetch_funding_rate("BTC/USDT")


def test_the_import_command_parses_with_a_daily_default():
    args = build_parser().parse_args(
        [
            "data", "import-bars",
            "--csv", "x.csv",
            "--market", "usdtm_perp",
            "--symbol", "BTC/USDT",
        ]
    )

    assert args.timeframe == "1d"
    assert (args.market, args.symbol) == ("usdtm_perp", "BTC/USDT")
