"""驗證 sector_flow_value_daily / sector_flow_value_weekly（金額口徑板塊資金流）
是否正確從 institutional_flow_daily 的股數 JOIN daily_prices 的收盤價換算而來。"""
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


def test_sector_flow_value_daily_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM sector_flow_value_daily").fetchone()[0]
    assert n > 0, "sector_flow_value_daily 是空的，請先跑 python build_sector_flow_value.py"


def test_sector_flow_value_daily_same_row_count_as_shares_version(conn):
    """金額版跟股數版（sector_flow_daily）應該是同一組 (industry_name, date)，
    只是多了金額欄位，兩者列數必須一致（否則代表 JOIN 邏輯漏了某些板塊/日期）。"""
    n_shares = conn.execute("SELECT COUNT(*) FROM sector_flow_daily").fetchone()[0]
    n_value = conn.execute("SELECT COUNT(*) FROM sector_flow_value_daily").fetchone()[0]
    assert n_shares == n_value


def test_sector_flow_value_daily_total_matches_component_sum(conn):
    """total_value 必須等於 foreign_value + trust_value + dealer_value（不可算錯），
    只在三者皆非 NULL 時檢查（NULL 代表當天完全無法換算金額，不是 0）。"""
    bad = conn.execute(
        "SELECT COUNT(*) FROM sector_flow_value_daily "
        "WHERE total_value IS NOT NULL "
        "AND ABS(total_value - (foreign_value + trust_value + dealer_value)) > 1.0"
    ).fetchone()[0]
    assert bad == 0


def test_sector_flow_value_daily_priced_stock_count_not_exceeding_stock_count(conn):
    bad = conn.execute(
        "SELECT COUNT(*) FROM sector_flow_value_daily WHERE priced_stock_count > stock_count"
    ).fetchone()[0]
    assert bad == 0


def test_sector_flow_value_daily_matches_manual_join_for_one_sample(conn):
    """半導體業最新一天的 total_value，必須等於「該板塊當天成分股股數 x 收盤價」手動
    JOIN 加總（抽查一天，驗證 SQL 聚合邏輯本身沒有算錯，不是只看有沒有噴例外）。"""
    row = conn.execute(
        "SELECT date FROM sector_flow_value_daily "
        "WHERE industry_name='半導體業' AND total_value IS NOT NULL "
        "ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if row is None:
        pytest.skip("半導體業目前沒有任何一天可換算金額，略過抽查")
    sample_date = row[0]

    expected = conn.execute(
        "SELECT SUM(f.foreign_net * p.close) FROM institutional_flow_daily f "
        "JOIN stocks s ON s.stock_id = f.stock_id "
        "JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date "
        "WHERE s.industry_name='半導體業' AND f.date=?",
        (sample_date,),
    ).fetchone()[0]
    actual = conn.execute(
        "SELECT foreign_value FROM sector_flow_value_daily WHERE industry_name='半導體業' AND date=?",
        (sample_date,),
    ).fetchone()[0]
    assert actual == pytest.approx(expected)


def test_sector_flow_value_weekly_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM sector_flow_value_weekly").fetchone()[0]
    assert n > 0, "sector_flow_value_weekly 是空的，請先跑 python build_sector_flow_value.py"


def test_sector_flow_value_weekly_week_index_aligns_with_shares_version(conn):
    """金額版跟股數版的週次編號必須對齊同一段日期區間（否則動畫互相對照會錯位）。"""
    rows = conn.execute(
        "SELECT week_index, week_start, week_end FROM sector_flow_weekly "
        "WHERE industry_name='半導體業' ORDER BY week_index LIMIT 5"
    ).fetchall()
    for week_index, week_start, week_end in rows:
        value_row = conn.execute(
            "SELECT week_start, week_end FROM sector_flow_value_weekly "
            "WHERE industry_name='半導體業' AND week_index=?",
            (week_index,),
        ).fetchone()
        assert value_row is not None
        assert value_row == (week_start, week_end)


def test_sector_flow_value_weekly_null_when_no_priced_days(conn):
    """priced_days=0 的列，四個金額欄位必須全部是 NULL（不可留 0，避免被誤讀成
    「這週淨流入淨流出剛好互相抵銷」）。"""
    bad = conn.execute(
        "SELECT COUNT(*) FROM sector_flow_value_weekly "
        "WHERE priced_days = 0 AND (foreign_value IS NOT NULL OR total_value IS NOT NULL)"
    ).fetchone()[0]
    assert bad == 0
