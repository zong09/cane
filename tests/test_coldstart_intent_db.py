"""`cold_start_intent` — เลือกได้โดยคอนโซล ใช้แล้วลบโดย engine และฐานบังคับทั้งสองทาง (ADR 35)"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from cane.db.repo import coldstart_intent as repo
from cane.db.schema import cold_start_intent

pytestmark = pytest.mark.db

PERP = "usdtm_perp"
BTC = "BTC/USDT"


@pytest.fixture
def user_id(db):
    """ชื่อคนเลือก · ชื่อฟิกซ์เจอร์คงไว้ให้เทสต์ข้างล่างอ่านเหมือนเดิม"""
    db.execute(cold_start_intent.delete())
    return "เทรดเดอร์"


def _choose(db, user_id, route, now=1_000):
    return repo.choose(db, profile="live", market=PERP, symbol=BTC, route=route,
                       by=user_id, now=now)


def test_a_choice_reads_back_with_who_chose_it(db, user_id):
    assert _choose(db, user_id, "trailing") is True

    intent = repo.read(db, profile="live", market=PERP, symbol=BTC)

    assert (intent.route, intent.chosen_by, intent.chosen_ts) == ("trailing", "เทรดเดอร์", 1_000)


def test_choosing_the_same_route_again_changes_nothing(db, user_id):
    _choose(db, user_id, "trailing", now=1_000)

    assert _choose(db, user_id, "trailing", now=2_000) is False
    assert repo.read(db, profile="live", market=PERP, symbol=BTC).chosen_ts == 1_000


def test_choosing_a_new_route_overwrites_the_old_one(db, user_id):
    _choose(db, user_id, "trailing", now=1_000)

    assert _choose(db, user_id, "skip", now=2_000) is True
    intent = repo.read(db, profile="live", market=PERP, symbol=BTC)
    assert (intent.route, intent.chosen_ts) == ("skip", 2_000)


def test_consuming_returns_the_route_once_and_leaves_nothing_behind(db, user_id):
    _choose(db, user_id, "trailing")

    assert repo.consume(db, profile="live", market=PERP, symbol=BTC) == "trailing"
    assert repo.consume(db, profile="live", market=PERP, symbol=BTC) is None
    assert repo.read(db, profile="live", market=PERP, symbol=BTC) is None


def test_an_intent_belongs_to_its_own_profile_and_market(db, user_id):
    _choose(db, user_id, "trailing")

    assert repo.read(db, profile="paper", market=PERP, symbol=BTC) is None
    assert repo.read(db, profile="live", market="spot", symbol=BTC) is None


def test_wait_1h_is_refused_before_it_reaches_the_table(db, user_id):
    with pytest.raises(ValueError):
        _choose(db, user_id, "wait_1h")


def test_the_table_itself_refuses_wait_1h(db, user_id):
    """CHECK เป็นชั้นที่สอง — เส้นทางที่ข้าม `choose()` ก็เขียนไม่ลง"""
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(cold_start_intent.insert().values(
                profile="live", market=PERP, symbol=BTC, route="wait_1h",
                chosen_by=user_id, chosen_ts=1,
            ))


def _as(db, role: str, sql: str, **params) -> None:
    with db.begin_nested():
        db.execute(text(f'SET LOCAL ROLE "{role}"'))
        db.execute(text(sql), params)


def _refused(db, role: str, sql: str, **params) -> None:
    with pytest.raises(DBAPIError):
        _as(db, role, sql, **params)


def test_the_engine_can_read_and_consume_but_not_choose(db, user_id):
    _choose(db, user_id, "trailing")

    _as(db, "cane_engine", "SELECT * FROM cold_start_intent")
    _refused(db, "cane_engine", "UPDATE cold_start_intent SET route = 'skip'")
    _refused(
        db, "cane_engine",
        "INSERT INTO cold_start_intent VALUES ('paper', 'spot', 'ETH/USDT', 'trailing', :u, 1)",
        u=user_id,
    )
    _as(db, "cane_engine", "DELETE FROM cold_start_intent")


def test_the_console_can_choose_but_not_delete(db, user_id):
    _as(
        db, "cane_console",
        "INSERT INTO cold_start_intent VALUES ('live', 'usdtm_perp', 'BTC/USDT', 'trailing', :u, 1)",
        u=user_id,
    )
    _as(db, "cane_console", "UPDATE cold_start_intent SET route = 'skip'")
    _refused(db, "cane_console", "DELETE FROM cold_start_intent")
