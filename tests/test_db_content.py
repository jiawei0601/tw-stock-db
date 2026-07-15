"""驗證已建置的 data/tw_stocks.db 內容是否合理。

不重新打網路 API（避免測試依賴外部服務且被限流），假設 `python build_db.py` 已跑過一次。
若資料庫不存在，測試會直接失敗並提示先跑 build_db.py。
"""
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


def test_market_counts_in_expected_range(conn):
    """台股上市約 1000 出頭、上櫃約 800 出頭（含本次過濾邏輯排除 ETF/權證/TDR 等後的普通股數）。"""
    cur = conn.execute("SELECT market, COUNT(*) FROM stocks GROUP BY market")
    counts = dict(cur.fetchall())
    assert 900 <= counts.get("TWSE", 0) <= 1300, f"TWSE 檔數異常: {counts.get('TWSE')}"
    assert 700 <= counts.get("TPEx", 0) <= 1000, f"TPEx 檔數異常: {counts.get('TPEx')}"


@pytest.mark.parametrize("stock_id,expected_name,expected_industry", [
    ("2330", "台積電", "半導體業"),
    ("2454", "聯發科", "半導體業"),
    ("2882", "國泰金", "金融保險業"),
])
def test_known_stocks_have_correct_industry(conn, stock_id, expected_name, expected_industry):
    cur = conn.execute(
        "SELECT name, industry_name FROM stocks WHERE stock_id = ?", (stock_id,)
    )
    row = cur.fetchone()
    assert row is not None, f"{stock_id} 不在資料庫中"
    name, industry_name = row
    assert name == expected_name
    assert industry_name == expected_industry


def test_no_duplicate_stock_ids(conn):
    cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_id) FROM stocks")
    total, distinct = cur.fetchone()
    assert total == distinct, "stock_id 有重複列（build_db.py 應為 idempotent 整批刷新）"


def test_market_values_are_valid(conn):
    cur = conn.execute("SELECT DISTINCT market FROM stocks")
    markets = {r[0] for r in cur.fetchall()}
    assert markets <= {"TWSE", "TPEx"}, f"出現未預期的 market 值: {markets}"


def test_industry_name_mostly_populated(conn):
    """股票區塊過濾邏輯下，理論上每檔都該有 industry_name；容許極少數例外但需 <1%。"""
    cur = conn.execute("SELECT COUNT(*) FROM stocks")
    total = cur.fetchone()[0]
    cur = conn.execute(
        "SELECT COUNT(*) FROM stocks WHERE industry_name IS NULL OR industry_name = ''"
    )
    missing = cur.fetchone()[0]
    assert missing / total < 0.01, f"industry_name 缺漏比例過高: {missing}/{total}"


def test_stock_groups_rows_reference_valid_stocks(conn):
    """stock_groups 為概念股/族群標記（非官方資料來源，人工整理），每筆都必須對應
    stocks 表中真實存在的 stock_id，不允許孤兒列。"""
    cur = conn.execute(
        "SELECT COUNT(*) FROM stock_groups g "
        "LEFT JOIN stocks s ON g.stock_id = s.stock_id WHERE s.stock_id IS NULL"
    )
    assert cur.fetchone()[0] == 0, "stock_groups 有對應不到 stocks 的孤兒列"


def test_stock_groups_schema_has_expected_columns(conn):
    cur = conn.execute("PRAGMA table_info(stock_groups)")
    cols = {row[1] for row in cur.fetchall()}
    assert cols == {"stock_id", "group_name", "group_type", "source", "created_at"}


def test_indexes_exist(conn):
    cur = conn.execute("PRAGMA index_list(stocks)")
    idx_names = {row[1] for row in cur.fetchall()}
    assert "idx_stocks_market" in idx_names
    assert "idx_stocks_industry_code" in idx_names
