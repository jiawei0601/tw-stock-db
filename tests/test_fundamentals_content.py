"""驗證 build_fundamentals.py / build_institutional_summary.py 產出的新表內容是否合理。

跟 test_db_content.py 同樣原則：不重新打網路 API、不重新讀 institutional.db，只驗證
`data/tw_stocks.db` 裡已經寫好的內容。跑測試前必須先跑過：
    python build_db.py
    python build_fundamentals.py
    python build_institutional_summary.py
"""
from __future__ import annotations

import json
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


@pytest.fixture(scope="module")
def target_stock_ids(conn) -> set[str]:
    cur = conn.execute("SELECT DISTINCT stock_id FROM stock_groups")
    return {r[0] for r in cur.fetchall()}


# ── monthly_revenue ──────────────────────────────────────────────────────────

def test_monthly_revenue_table_exists_with_expected_columns(conn):
    cur = conn.execute("PRAGMA table_info(monthly_revenue)")
    cols = {row[1] for row in cur.fetchall()}
    assert cols == {
        "stock_id", "company_name", "ym", "announce_date", "revenue",
        "revenue_prev_month", "revenue_last_year_month", "mom_pct", "yoy_pct",
        "revenue_cumulative", "revenue_cumulative_last_year", "cumulative_yoy_pct",
        "remark", "source_market", "updated_at",
    }


def test_monthly_revenue_covers_most_target_stocks(conn, target_stock_ids):
    if not DB_PATH.exists():
        pytest.skip("db not built")
    cur = conn.execute("SELECT COUNT(*) FROM monthly_revenue")
    total = cur.fetchone()[0]
    assert total > 0, "monthly_revenue 沒有任何資料，請先跑 python build_fundamentals.py"
    # 官方 opendata 只回「最新一期全量」，理論上應涵蓋幾乎所有目標股票，容許極少數新股缺漏
    assert total / len(target_stock_ids) >= 0.9, f"月營收覆蓋率過低: {total}/{len(target_stock_ids)}"


def test_monthly_revenue_known_stock_2330(conn):
    cur = conn.execute(
        "SELECT company_name, revenue, yoy_pct, source_market FROM monthly_revenue WHERE stock_id = ?",
        ("2330",),
    )
    row = cur.fetchone()
    assert row is not None, "2330 不在 monthly_revenue 中"
    company_name, rev, yoy_pct, source_market = row
    assert company_name == "台積電"
    assert rev is not None and rev > 0
    assert yoy_pct is not None
    assert source_market == "TWSE"


def test_monthly_revenue_no_orphan_rows(conn):
    cur = conn.execute(
        "SELECT COUNT(*) FROM monthly_revenue m "
        "LEFT JOIN stocks s ON m.stock_id = s.stock_id WHERE s.stock_id IS NULL"
    )
    assert cur.fetchone()[0] == 0


# ── shareholding_concentration ───────────────────────────────────────────────

def test_shareholding_table_exists_with_expected_columns(conn):
    cur = conn.execute("PRAGMA table_info(shareholding_concentration)")
    cols = {row[1] for row in cur.fetchall()}
    assert cols == {
        "stock_id", "as_of", "total_holders", "total_shares",
        "pct_gt_400zhang", "pct_gt_1000zhang", "levels_json", "updated_at",
    }


def test_shareholding_covers_most_target_stocks(conn, target_stock_ids):
    cur = conn.execute("SELECT COUNT(*) FROM shareholding_concentration")
    total = cur.fetchone()[0]
    assert total > 0, "shareholding_concentration 沒有任何資料，請先跑 python build_fundamentals.py"
    assert total / len(target_stock_ids) >= 0.9, f"籌碼集中度覆蓋率過低: {total}/{len(target_stock_ids)}"


def test_shareholding_known_stock_2330(conn):
    cur = conn.execute(
        "SELECT total_holders, total_shares, pct_gt_400zhang, pct_gt_1000zhang, levels_json "
        "FROM shareholding_concentration WHERE stock_id = ?",
        ("2330",),
    )
    row = cur.fetchone()
    assert row is not None, "2330 不在 shareholding_concentration 中"
    total_holders, total_shares, pct_400, pct_1000, levels_json = row
    assert total_holders is not None and total_holders > 0
    assert total_shares is not None and total_shares > 0
    assert 0 <= pct_1000 <= 100
    assert 0 <= pct_400 <= 100
    assert pct_400 >= pct_1000, ">400張比例理論上應 >= >1000張比例（後者是前者子集合）"

    levels = json.loads(levels_json)
    assert isinstance(levels, list)
    assert 1 <= len(levels) <= 15
    for entry in levels:
        assert set(entry.keys()) == {"level", "holders", "shares", "pct"}
        assert 1 <= entry["level"] <= 15


def test_shareholding_no_orphan_rows(conn):
    cur = conn.execute(
        "SELECT COUNT(*) FROM shareholding_concentration c "
        "LEFT JOIN stocks s ON c.stock_id = s.stock_id WHERE s.stock_id IS NULL"
    )
    assert cur.fetchone()[0] == 0


# ── institutional_flow_summary / institutional_flow_daily ───────────────────

def test_institutional_flow_summary_table_exists_with_expected_columns(conn):
    cur = conn.execute("PRAGMA table_info(institutional_flow_summary)")
    cols = {row[1] for row in cur.fetchall()}
    assert cols == {
        "stock_id", "latest_date", "foreign_net_5d", "foreign_net_20d", "foreign_net_60d",
        "trust_net_5d", "trust_net_20d", "trust_net_60d",
        "dealer_net_5d", "dealer_net_20d", "dealer_net_60d",
        "trading_days_covered", "foreign_streak_days", "foreign_streak_truncated", "updated_at",
    }


def test_institutional_flow_daily_table_exists_with_expected_columns(conn):
    cur = conn.execute("PRAGMA table_info(institutional_flow_daily)")
    cols = {row[1] for row in cur.fetchall()}
    assert cols == {"stock_id", "date", "foreign_net", "trust_net", "dealer_net"}


def test_institutional_flow_summary_has_data(conn):
    """institutional.db 只涵蓋 tw-momentum-scanner 篩選過的動能股清單（非全市場），
    91 檔中預期有一部分查無資料（見 HANDOFF.md），故不要求高覆蓋率，只要求「有資料」。"""
    cur = conn.execute("SELECT COUNT(*) FROM institutional_flow_summary")
    assert cur.fetchone()[0] > 0, "institutional_flow_summary 沒有任何資料，請先跑 python build_institutional_summary.py"


def test_institutional_flow_known_stock_2330(conn):
    cur = conn.execute(
        "SELECT latest_date, foreign_net_60d, trading_days_covered, foreign_streak_days "
        "FROM institutional_flow_summary WHERE stock_id = ?",
        ("2330",),
    )
    row = cur.fetchone()
    assert row is not None, "2330 不在 institutional_flow_summary 中（若未來 institutional.db 更新內容不再涵蓋此股，需重新確認）"
    latest_date, foreign_net_60d, trading_days_covered, streak = row
    assert latest_date is not None
    assert foreign_net_60d is not None
    assert 0 < trading_days_covered <= 60


def test_institutional_flow_daily_bounded_to_60_days_per_stock(conn):
    cur = conn.execute(
        "SELECT stock_id, COUNT(*) FROM institutional_flow_daily GROUP BY stock_id HAVING COUNT(*) > 60"
    )
    violations = cur.fetchall()
    assert violations == [], f"部分股票的逐日明細超過 60 日視窗上限: {violations}"


def test_institutional_flow_tables_no_orphan_rows(conn):
    cur = conn.execute(
        "SELECT COUNT(*) FROM institutional_flow_summary f "
        "LEFT JOIN stocks s ON f.stock_id = s.stock_id WHERE s.stock_id IS NULL"
    )
    assert cur.fetchone()[0] == 0
    cur = conn.execute(
        "SELECT COUNT(*) FROM institutional_flow_daily d "
        "LEFT JOIN stocks s ON d.stock_id = s.stock_id WHERE s.stock_id IS NULL"
    )
    assert cur.fetchone()[0] == 0
