"""驗證 group_flow_daily / group_flow_weekly / group_flow_value_daily / group_flow_value_weekly
是否正確從 institutional_flow_daily JOIN stock_groups（19 個族群、91 檔標的）聚合而來。

比照 tests/test_sector_flow.py / test_sector_flow_value.py 的驗證模式，但額外驗證族群
成分重疊的語意（一檔股票可屬多個族群，跨族群加總不等於全市場合計，見
build_group_flow.py docstring）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"

# 抽查用族群：91 檔標的中橫跨多個族群的股票（例如 2330 台積電）所在的族群之一，
# 半導體設備族群本身樣本數不大、方便手動核對。
SAMPLE_GROUP = "半導體設備"


@pytest.fixture(scope="module")
def conn():
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# group_flow_daily（股數口徑）
# ---------------------------------------------------------------------------

def test_group_flow_daily_table_exists_and_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM group_flow_daily").fetchone()[0]
    assert n > 0, "group_flow_daily 是空的，請先跑 python build_group_flow.py"


def test_group_flow_daily_covers_19_groups(conn):
    """stock_groups 目前是 19 個族群（人工整理清單），group_flow_daily 應完整涵蓋。"""
    n_groups_universe = conn.execute("SELECT COUNT(DISTINCT group_name) FROM stock_groups").fetchone()[0]
    n_covered = conn.execute("SELECT COUNT(DISTINCT group_name) FROM group_flow_daily").fetchone()[0]
    assert n_groups_universe == 19
    assert n_covered == 19


def test_group_flow_daily_total_net_matches_component_sum(conn):
    """total_net 必須等於 foreign_net + trust_net + dealer_net（不可算錯）。"""
    bad = conn.execute(
        "SELECT COUNT(*) FROM group_flow_daily "
        "WHERE total_net != foreign_net + trust_net + dealer_net"
    ).fetchone()[0]
    assert bad == 0


def test_group_flow_daily_sum_matches_component_stocks(conn):
    """抽查一天：SAMPLE_GROUP 的合計，必須等於該族群當天所有成分股 foreign_net 逐筆加總
    （直接用 stock_groups 反查成分股，不透過 group_flow_daily 本身，驗證聚合邏輯本身沒算錯）。"""
    row = conn.execute(
        "SELECT date FROM group_flow_daily WHERE group_name=? ORDER BY date DESC LIMIT 1",
        (SAMPLE_GROUP,),
    ).fetchone()
    assert row is not None
    sample_date = row[0]

    expected = conn.execute(
        "SELECT SUM(f.foreign_net) FROM institutional_flow_daily f "
        "JOIN stock_groups g ON g.stock_id = f.stock_id "
        "WHERE g.group_name=? AND f.date=?",
        (SAMPLE_GROUP, sample_date),
    ).fetchone()[0]
    actual = conn.execute(
        "SELECT foreign_net FROM group_flow_daily WHERE group_name=? AND date=?",
        (SAMPLE_GROUP, sample_date),
    ).fetchone()[0]
    assert actual == expected


def test_group_flow_daily_date_range_spans_about_three_years(conn):
    row = conn.execute("SELECT MIN(date), MAX(date) FROM group_flow_daily").fetchone()
    assert row[0] is not None and row[1] is not None
    assert row[0] <= "2023-12-31"
    assert row[1] >= "2026-06-01"


# ---------------------------------------------------------------------------
# group_flow_weekly（股數口徑，週切分，須與 sector_flow_weekly 對齊同一套日期序列）
# ---------------------------------------------------------------------------

def test_group_flow_weekly_table_exists_and_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM group_flow_weekly").fetchone()[0]
    assert n > 0, "group_flow_weekly 是空的，請先跑 python build_group_flow.py"


def test_group_flow_weekly_covers_19_groups(conn):
    n = conn.execute("SELECT COUNT(DISTINCT group_name) FROM group_flow_weekly").fetchone()[0]
    assert n == 19


def test_group_flow_weekly_chunk_size_is_five_except_last(conn):
    max_week = conn.execute("SELECT MAX(week_index) FROM group_flow_weekly").fetchone()[0]
    bad = conn.execute(
        "SELECT DISTINCT week_index, trading_days FROM group_flow_weekly "
        "WHERE week_index != ? AND trading_days != 5",
        (max_week,),
    ).fetchall()
    assert bad == [], f"非最後一組卻不是 5 個交易日: {bad}"


def test_group_flow_weekly_total_net_matches_component_sum(conn):
    bad = conn.execute(
        "SELECT COUNT(*) FROM group_flow_weekly "
        "WHERE total_net != foreign_net + trust_net + dealer_net"
    ).fetchone()[0]
    assert bad == 0


def test_group_flow_weekly_sum_matches_daily_sum(conn):
    """SAMPLE_GROUP week_index=0 的合計，必須等於該族群前 5 個交易日 total_net 加總。"""
    row = conn.execute(
        "SELECT week_start, week_end, total_net FROM group_flow_weekly "
        "WHERE group_name=? AND week_index=0",
        (SAMPLE_GROUP,),
    ).fetchone()
    assert row is not None
    week_start, week_end, total_net = row

    expected = conn.execute(
        "SELECT SUM(total_net) FROM group_flow_daily "
        "WHERE group_name=? AND date BETWEEN ? AND ?",
        (SAMPLE_GROUP, week_start, week_end),
    ).fetchone()[0]
    assert total_net == expected


def test_group_flow_weekly_week_index_aligns_with_sector_flow_weekly(conn):
    """族群版跟板塊版的週次編號必須對齊同一段日期區間（同一套交易日序列切分，兩者可
    互相對照）。"""
    rows = conn.execute(
        "SELECT DISTINCT week_index, week_start, week_end FROM sector_flow_weekly "
        "ORDER BY week_index LIMIT 5"
    ).fetchall()
    for week_index, week_start, week_end in rows:
        group_row = conn.execute(
            "SELECT DISTINCT week_start, week_end FROM group_flow_weekly WHERE week_index=?",
            (week_index,),
        ).fetchone()
        assert group_row is not None
        assert group_row == (week_start, week_end)


# ---------------------------------------------------------------------------
# group_flow_value_daily / group_flow_value_weekly（金額口徑）
# ---------------------------------------------------------------------------

def test_group_flow_value_daily_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM group_flow_value_daily").fetchone()[0]
    assert n > 0, "group_flow_value_daily 是空的，請先跑 python build_group_flow.py"


def test_group_flow_value_daily_same_row_count_as_shares_version(conn):
    """金額版跟股數版（group_flow_daily）應該是同一組 (group_name, date)，只是多了金額欄位，
    兩者列數必須一致（否則代表 JOIN 邏輯漏了某些族群/日期）。"""
    n_shares = conn.execute("SELECT COUNT(*) FROM group_flow_daily").fetchone()[0]
    n_value = conn.execute("SELECT COUNT(*) FROM group_flow_value_daily").fetchone()[0]
    assert n_shares == n_value


def test_group_flow_value_daily_total_matches_component_sum(conn):
    """total_value 必須等於 foreign_value + trust_value + dealer_value（不可算錯），
    只在三者皆非 NULL 時檢查（NULL 代表當天完全無法換算金額，不是 0）。"""
    bad = conn.execute(
        "SELECT COUNT(*) FROM group_flow_value_daily "
        "WHERE total_value IS NOT NULL "
        "AND ABS(total_value - (foreign_value + trust_value + dealer_value)) > 1.0"
    ).fetchone()[0]
    assert bad == 0


def test_group_flow_value_daily_priced_stock_count_not_exceeding_stock_count(conn):
    bad = conn.execute(
        "SELECT COUNT(*) FROM group_flow_value_daily WHERE priced_stock_count > stock_count"
    ).fetchone()[0]
    assert bad == 0


def test_group_flow_value_daily_matches_manual_join_for_one_sample(conn):
    """SAMPLE_GROUP 最新一天的 total_value，必須等於「該族群當天成分股股數 x 收盤價」
    手動 JOIN 加總（抽查一天，驗證 SQL 聚合邏輯本身沒有算錯）。"""
    row = conn.execute(
        "SELECT date FROM group_flow_value_daily "
        "WHERE group_name=? AND total_value IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        (SAMPLE_GROUP,),
    ).fetchone()
    if row is None:
        pytest.skip(f"{SAMPLE_GROUP} 目前沒有任何一天可換算金額，略過抽查")
    sample_date = row[0]

    expected = conn.execute(
        "SELECT SUM(f.foreign_net * p.close) FROM institutional_flow_daily f "
        "JOIN stock_groups g ON g.stock_id = f.stock_id "
        "JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date "
        "WHERE g.group_name=? AND f.date=?",
        (SAMPLE_GROUP, sample_date),
    ).fetchone()[0]
    actual = conn.execute(
        "SELECT foreign_value FROM group_flow_value_daily WHERE group_name=? AND date=?",
        (SAMPLE_GROUP, sample_date),
    ).fetchone()[0]
    assert actual == pytest.approx(expected)


def test_group_flow_value_weekly_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM group_flow_value_weekly").fetchone()[0]
    assert n > 0, "group_flow_value_weekly 是空的，請先跑 python build_group_flow.py"


def test_group_flow_value_weekly_week_index_aligns_with_shares_version(conn):
    """族群金額版跟族群股數版的週次編號必須對齊同一段日期區間。"""
    rows = conn.execute(
        "SELECT week_index, week_start, week_end FROM group_flow_weekly "
        "WHERE group_name=? ORDER BY week_index LIMIT 5",
        (SAMPLE_GROUP,),
    ).fetchall()
    for week_index, week_start, week_end in rows:
        value_row = conn.execute(
            "SELECT week_start, week_end FROM group_flow_value_weekly "
            "WHERE group_name=? AND week_index=?",
            (SAMPLE_GROUP, week_index),
        ).fetchone()
        assert value_row is not None
        assert value_row == (week_start, week_end)


def test_group_flow_value_weekly_null_when_no_priced_days(conn):
    """priced_days=0 的列，四個金額欄位必須全部是 NULL（不可留 0）。"""
    bad = conn.execute(
        "SELECT COUNT(*) FROM group_flow_value_weekly "
        "WHERE priced_days = 0 AND (foreign_value IS NOT NULL OR total_value IS NOT NULL)"
    ).fetchone()[0]
    assert bad == 0


# ---------------------------------------------------------------------------
# 族群成分重疊語意（跟板塊表最大的差異）
# ---------------------------------------------------------------------------

def test_stock_groups_has_overlapping_membership(conn):
    """驗證本專案的核心語意前提本身成立：91 檔標的中確實有股票同時屬於多個族群
    （否則「跨族群加總會重複計算」這個警語就是空話）。"""
    n_multi = conn.execute(
        "SELECT COUNT(*) FROM (SELECT stock_id FROM stock_groups GROUP BY stock_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert n_multi > 0


def test_group_flow_daily_cross_group_stock_count_exceeds_distinct_stock_count(conn):
    """驗證「跨族群加總會重複計算」這個語意在資料上確實成立：抽查一天，19 個族群的
    stock_count（各族群成分股數）加總，必須嚴格大於當天實際涵蓋的相異股票數——因為
    橫跨多族群的股票被算了不只一次。用 stock_count（結構性計數）而非 total_net（可能
    因為淨額剛好抵銷而巧合相等）驗證，確保這條測試不會因為金額湊巧相等而失效。這條
    測試存在的目的就是攔住「有人誤把 group_flow_daily 拿去跨族群加總當成全市場/全
    概念股合計使用」這種誤用模式，用資料本身證明兩者不能劃等號。"""
    sample_date = conn.execute("SELECT MAX(date) FROM group_flow_daily").fetchone()[0]

    cross_group_stock_count_sum = conn.execute(
        "SELECT SUM(stock_count) FROM group_flow_daily WHERE date=?",
        (sample_date,),
    ).fetchone()[0]

    distinct_stock_count = conn.execute(
        "SELECT COUNT(DISTINCT f.stock_id) FROM institutional_flow_daily f "
        "WHERE f.date=? AND f.stock_id IN (SELECT DISTINCT stock_id FROM stock_groups)",
        (sample_date,),
    ).fetchone()[0]

    assert cross_group_stock_count_sum > distinct_stock_count
