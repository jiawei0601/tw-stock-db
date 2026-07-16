"""驗證 daily_prices 表內容是否合理（institutional_flow_daily 涵蓋範圍的個股歷史收盤價）。"""
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


def test_daily_prices_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
    assert n > 0, "daily_prices 是空的，請先跑 python build_daily_prices.py"


def test_daily_prices_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || date) FROM daily_prices"
    ).fetchone()
    assert total == distinct


def test_daily_prices_close_values_positive_and_plausible(conn):
    """收盤價必須是正數，且不該出現離譜量級（例如把成交金額誤植進收盤價欄位）。"""
    row = conn.execute("SELECT MIN(close), MAX(close) FROM daily_prices").fetchone()
    assert row[0] > 0
    assert row[1] < 100000  # 個股收盤價不可能到十萬元量級，防欄位錯位


def test_daily_prices_known_stock_2330_matches_realworld_value(conn):
    """交叉核對已知標的：2330 台積電 2024-06-03 收盤價 846.00（本專案任務展開前
    已用官方 MI_INDEX 端點人工核對過的已知值），確認欄位對應無誤。"""
    row = conn.execute(
        "SELECT close FROM daily_prices WHERE stock_id='2330' AND date='2024-06-03'"
    ).fetchone()
    if row is None:
        pytest.skip("2024-06-03 不在本次 institutional_flow_daily 的回補範圍內，略過抽查")
    assert row[0] == pytest.approx(846.00)


def test_daily_prices_date_range_within_institutional_flow_daily(conn):
    """daily_prices 的日期範圍不應超出 institutional_flow_daily 的範圍（本表的存在目的
    就是對齊三大法人資料範圍，不該無中生有多出範圍外的日期）。"""
    inst_range = conn.execute("SELECT MIN(date), MAX(date) FROM institutional_flow_daily").fetchone()
    price_range = conn.execute("SELECT MIN(date), MAX(date) FROM daily_prices").fetchone()
    assert price_range[0] >= inst_range[0]
    assert price_range[1] <= inst_range[1]


def test_daily_prices_twse_coverage_high(conn):
    """TWSE 個股收盤價（MI_INDEX 官方 endpoint）預期涵蓋率接近 100%（比照三大法人
    backfill 的量級與可靠度，見 docs/data-sources.md 第 20 節）。"""
    total = conn.execute(
        "SELECT COUNT(*) FROM institutional_flow_daily f JOIN stocks s ON s.stock_id = f.stock_id "
        "WHERE s.market = 'TWSE'"
    ).fetchone()[0]
    matched = conn.execute(
        "SELECT COUNT(*) FROM institutional_flow_daily f "
        "JOIN stocks s ON s.stock_id = f.stock_id "
        "JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date "
        "WHERE s.market = 'TWSE'"
    ).fetchone()[0]
    assert total > 0
    assert matched / total > 0.95


def test_daily_prices_tpex_coverage_high(conn):
    """TPEx 個股收盤價第九輪實測已確認能正確支援歷史查詢（www/zh-tw/afterTrading/
    dailyQuotes，見 docs/data-sources.md 第 21 節），預期涵蓋率同樣接近 100%，
    不像先前排查過的 tpex_mainboard_daily_close_quotes/stk_quote_result.php 那樣
    永遠只回最新一天。"""
    total = conn.execute(
        "SELECT COUNT(*) FROM institutional_flow_daily f JOIN stocks s ON s.stock_id = f.stock_id "
        "WHERE s.market = 'TPEx'"
    ).fetchone()[0]
    matched = conn.execute(
        "SELECT COUNT(*) FROM institutional_flow_daily f "
        "JOIN stocks s ON s.stock_id = f.stock_id "
        "JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date "
        "WHERE s.market = 'TPEx'"
    ).fetchone()[0]
    assert total > 0
    assert matched / total > 0.90
