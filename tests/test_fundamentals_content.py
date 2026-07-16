"""驗證 build_fundamentals.py / build_institutional_summary.py 產出的新表內容是否合理。

跟 test_db_content.py 同樣原則：不重新打網路 API，只驗證 `data/tw_stocks.db` 裡已經寫好
的內容。跑測試前必須先跑過：
    python build_db.py
    python build_fundamentals.py
    python build_institutional_summary.py

**【第四輪】institutional_flow_summary / institutional_flow_daily 改為官方 API 直抓**
（TWSE `fund/T86` + TPEx `3itrade_hedge_result.php`，見 `collectors/institutional_official.py`
與 `docs/data-sources.md` 第 10-11 節），不再讀取 `tw_cache/institutional.db`（該共用資料源
只涵蓋 tw-momentum-scanner 篩選過的動能股清單，非全市場）。改用官方 API 後理論上應涵蓋
全部 91 檔（全市場資料，非篩選過的子集合），且所有股票的 `latest_date` 應集中在最近 1-2
個交易日內（少數個股因當日停牌等原因略舊屬正常，不強制要求全部相同）。
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


def test_institutional_flow_summary_covers_most_target_stocks(conn, target_stock_ids):
    """官方 API（T86 + 3itrade_hedge_result.php）是全市場資料，理論上應涵蓋全部 91 檔；
    容許極少數個股因剛掛牌/長期停牌等原因缺漏，但要求高覆蓋率（不像舊版讀 institutional.db
    時只有 71/91，見 HANDOFF.md 第四輪決策紀錄）。"""
    cur = conn.execute("SELECT COUNT(*) FROM institutional_flow_summary")
    total = cur.fetchone()[0]
    assert total > 0, "institutional_flow_summary 沒有任何資料，請先跑 python build_institutional_summary.py"
    assert total / len(target_stock_ids) >= 0.9, f"三大法人動態覆蓋率過低: {total}/{len(target_stock_ids)}"


def test_institutional_flow_summary_freshness_consistent(conn):
    """本輪任務的核心目的：改用官方 API 直抓後，91 檔的資料新鮮度應一致（同一批最近交易日），
    不應再出現「71 檔是近日資料、20 檔是舊資料」的不一致情況。個別股票可能因當日停牌等原因
    latest_date 略舊，容許小範圍（<=5 個日曆日）差異，但不應出現大範圍新鮮度落差。"""
    cur = conn.execute("SELECT MIN(latest_date), MAX(latest_date) FROM institutional_flow_summary")
    min_date, max_date = cur.fetchone()
    assert min_date is not None and max_date is not None
    from datetime import date as _date
    gap_days = (_date.fromisoformat(max_date) - _date.fromisoformat(min_date)).days
    assert gap_days <= 5, f"latest_date 落差過大（{min_date} ~ {max_date}），資料新鮮度不一致"


def test_institutional_flow_known_stock_2330(conn):
    """2330 台積電是全市場成交量最大的股票之一，官方 API 理應每個交易日都有資料。
    數字量級核對基準：實測 2026-07-16 抓取時，T86 顯示 2330 單日外資買賣超約在
    數百萬~數千萬股量級（見 docs/data-sources.md 第 10 節樣本），故此處用寬鬆的量級
    上限（單日均量不超過 2 億股，遠高於任何實測觀察值）防止欄位錯位等離譜 bug，
    不緊咬特定數值（每次重跑資料本身就會不同）。"""
    cur = conn.execute(
        "SELECT latest_date, foreign_net_5d, foreign_net_60d, trading_days_covered, foreign_streak_days "
        "FROM institutional_flow_summary WHERE stock_id = ?",
        ("2330",),
    )
    row = cur.fetchone()
    assert row is not None, "2330 不在 institutional_flow_summary 中（全市場官方 API 理論上必涵蓋此股，若查無需重新確認 collector 是否故障）"
    latest_date, foreign_net_5d, foreign_net_60d, trading_days_covered, streak = row
    assert latest_date is not None
    assert foreign_net_60d is not None
    assert 0 < trading_days_covered <= 60
    assert abs(foreign_net_5d) / 5 < 200_000_000, f"2330 單日外資買賣超均量超出合理量級，疑似欄位錯位: {foreign_net_5d}"
    assert abs(foreign_net_60d) / trading_days_covered < 200_000_000, f"2330 單日外資買賣超均量超出合理量級，疑似欄位錯位: {foreign_net_60d}"


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
