"""驗證 sector_flow_weekly 是否正確從 sector_flow_daily 依交易日序列每 5 筆切分彙整。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


@pytest.fixture(scope="module")
def conn():
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def test_sector_flow_weekly_table_exists_and_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM sector_flow_weekly").fetchone()[0]
    assert n > 0, "sector_flow_weekly 是空的，請先跑 python build_sector_flow_weekly.py"


def test_sector_flow_weekly_covers_same_sectors_as_daily(conn):
    n_daily = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_daily").fetchone()[0]
    n_weekly = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_weekly").fetchone()[0]
    assert n_daily == n_weekly


def test_sector_flow_weekly_chunk_size_is_five_except_last(conn):
    """每組應為 5 個交易日，只有最後一組（最大 week_index）允許不足 5 天。"""
    max_week = conn.execute("SELECT MAX(week_index) FROM sector_flow_weekly").fetchone()[0]
    bad = conn.execute(
        "SELECT DISTINCT week_index, trading_days FROM sector_flow_weekly "
        "WHERE week_index != ? AND trading_days != 5",
        (max_week,),
    ).fetchall()
    assert bad == [], f"非最後一組卻不是 5 個交易日: {bad}"


def test_sector_flow_weekly_total_net_matches_component_sum(conn):
    bad = conn.execute(
        "SELECT COUNT(*) FROM sector_flow_weekly "
        "WHERE total_net != foreign_net + trust_net + dealer_net"
    ).fetchone()[0]
    assert bad == 0


def test_sector_flow_weekly_sum_matches_daily_sum(conn):
    """半導體業 week_index=0 的合計，必須等於該板塊前 5 個交易日 total_net 加總（抽查一組）。"""
    row = conn.execute(
        "SELECT week_start, week_end, total_net FROM sector_flow_weekly "
        "WHERE industry_name='半導體業' AND week_index=0"
    ).fetchone()
    assert row is not None
    week_start, week_end, total_net = row

    expected = conn.execute(
        "SELECT SUM(total_net) FROM sector_flow_daily "
        "WHERE industry_name='半導體業' AND date BETWEEN ? AND ?",
        (week_start, week_end),
    ).fetchone()[0]
    assert total_net == expected


def test_sector_flow_weekly_week_count_matches_daily_trading_days(conn):
    """150 週 = 750 個交易日 / 5，容許最後一週不足 5 天造成的誤差在 1 週內。"""
    n_days = conn.execute("SELECT COUNT(DISTINCT date) FROM sector_flow_daily").fetchone()[0]
    n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM sector_flow_weekly").fetchone()[0]
    import math
    assert n_weeks == math.ceil(n_days / 5)
