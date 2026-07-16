"""驗證 sector_flow_daily 是否正確從 institutional_flow_daily 依 industry_name 聚合。"""
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


def test_sector_flow_table_exists_and_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM sector_flow_daily").fetchone()[0]
    assert n > 0, "sector_flow_daily 是空的，請先跑 python build_sector_flow.py"


def test_sector_flow_covers_all_industries_in_universe(conn):
    """stock_groups 名單橫跨的板塊數應與 sector_flow_daily 一致。"""
    n_industries = conn.execute(
        "SELECT COUNT(DISTINCT s.industry_name) FROM stocks s "
        "WHERE s.stock_id IN (SELECT DISTINCT stock_id FROM stock_groups) "
        "AND s.industry_name IS NOT NULL"
    ).fetchone()[0]
    n_covered = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_daily").fetchone()[0]
    assert n_covered == n_industries


def test_sector_flow_total_net_matches_component_sum(conn):
    """total_net 必須等於 foreign_net + trust_net + dealer_net（不可算錯）。"""
    bad = conn.execute(
        "SELECT COUNT(*) FROM sector_flow_daily "
        "WHERE total_net != foreign_net + trust_net + dealer_net"
    ).fetchone()[0]
    assert bad == 0


def test_sector_flow_sum_matches_institutional_flow_daily(conn):
    """半導體業某一天的合計，必須等於該板塊當天所有成分股 foreign_net 加總（抽查一天）。"""
    row = conn.execute(
        "SELECT date FROM sector_flow_daily WHERE industry_name='半導體業' "
        "ORDER BY date DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    sample_date = row[0]

    expected = conn.execute(
        "SELECT SUM(f.foreign_net) FROM institutional_flow_daily f "
        "JOIN stocks s ON s.stock_id = f.stock_id "
        "WHERE s.industry_name='半導體業' AND f.date=? "
        "AND f.stock_id IN (SELECT DISTINCT stock_id FROM stock_groups)",
        (sample_date,),
    ).fetchone()[0]
    actual = conn.execute(
        "SELECT foreign_net FROM sector_flow_daily WHERE industry_name='半導體業' AND date=?",
        (sample_date,),
    ).fetchone()[0]
    assert actual == expected


def test_sector_flow_date_range_spans_about_three_years(conn):
    row = conn.execute("SELECT MIN(date), MAX(date) FROM sector_flow_daily").fetchone()
    assert row[0] is not None and row[1] is not None
    assert row[0] <= "2023-12-31"
    assert row[1] >= "2026-06-01"
