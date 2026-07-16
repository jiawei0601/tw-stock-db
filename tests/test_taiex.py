"""驗證 taiex_daily 表內容是否合理。"""
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


def test_taiex_daily_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM taiex_daily").fetchone()[0]
    assert n > 0, "taiex_daily 是空的，請先跑 python build_taiex.py"


def test_taiex_daily_covers_sector_flow_date_range(conn):
    """taiex_daily 的日期範圍必須至少涵蓋 sector_flow_daily 的範圍，動畫才不會缺資料。"""
    sf_range = conn.execute("SELECT MIN(date), MAX(date) FROM sector_flow_daily").fetchone()
    tx_range = conn.execute("SELECT MIN(date), MAX(date) FROM taiex_daily").fetchone()
    assert tx_range[0] <= sf_range[0]
    assert tx_range[1] >= sf_range[1]


def test_taiex_close_values_in_plausible_range(conn):
    """台股加權指數這幾年實際落在合理量級（不可能是 0 或負值，也不該是幾千萬這種
    明顯欄位錯位的數字）。實測 2023-06~2026-07 區間確實從約 17,200 漲到約 47,700，
    符合本專案觀察到的 AI/半導體資金大量湧入現象，上限抓寬鬆一點，不當成資料錯誤。"""
    row = conn.execute("SELECT MIN(close), MAX(close) FROM taiex_daily").fetchone()
    assert 5000 < row[0] < 100000
    assert 5000 < row[1] < 100000


def test_taiex_no_duplicate_dates(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT date) FROM taiex_daily"
    ).fetchone()
    assert total == distinct
