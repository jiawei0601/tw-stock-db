"""驗證 build_revenue_history.py / build_fundamentals.py / build_institutional_summary.py
產出的表內容是否合理。

跟 test_db_content.py 同樣原則：不重新打網路 API，只驗證 `data/tw_stocks.db` 裡已經寫好
的內容。跑測試前必須先跑過：
    python build_db.py
    python build_revenue_history.py
    python build_fundamentals.py
    python build_institutional_summary.py

**【第四輪】institutional_flow_summary / institutional_flow_daily 改為官方 API 直抓**
（TWSE `fund/T86` + TPEx `3itrade_hedge_result.php`，見 `collectors/institutional_official.py`
與 `docs/data-sources.md` 第 10-11 節），不再讀取 `tw_cache/institutional.db`（該共用資料源
只涵蓋 tw-momentum-scanner 篩選過的動能股清單，非全市場）。改用官方 API 後理論上應涵蓋
全部 91 檔（全市場資料，非篩選過的子集合）。

**【第五輪】兩個維度從「只有最新一期」擴充為「近 3 年歷史」**：
    - `monthly_revenue` PK 從 `stock_id`（單列快照）改為 `(stock_id, ym)`（時序表），
      資料源改用 MOPS 歷史封存頁面（`collectors/revenue_history.py`），backfill 近 36
      個月，見 `docs/data-sources.md` 第 12 節。
    - `institutional_flow_daily` 累積視窗從「近 60 個交易日」擴大為「近 3 年（約 750
      個交易日）」，`institutional_flow_summary` 的 5/20/60 日彙總計算邏輯不變，但資料
      來源改成讀本地 `institutional_flow_daily`（不再對外重複發送請求）。
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


# ── monthly_revenue（第五輪起改為近 3 年時序表，PK=(stock_id, ym)）───────────

def test_monthly_revenue_table_exists_with_expected_columns(conn):
    cur = conn.execute("PRAGMA table_info(monthly_revenue)")
    info = cur.fetchall()
    cols = {row[1] for row in info}
    assert cols == {
        "stock_id", "company_name", "ym", "announce_date", "revenue",
        "revenue_prev_month", "revenue_last_year_month", "mom_pct", "yoy_pct",
        "revenue_cumulative", "revenue_cumulative_last_year", "cumulative_yoy_pct",
        "remark", "source_market", "updated_at",
    }
    # PK 應為 (stock_id, ym) 複合鍵（PRAGMA table_info 的 pk 欄位是 1-indexed 的複合鍵順序，
    # 0 表示不在 PK 內）——第五輪 schema 異動的核心驗證，不是單列快照。
    pk_cols = {row[1]: row[5] for row in info if row[5] > 0}
    assert pk_cols == {"stock_id": 1, "ym": 2}, f"monthly_revenue PK 應為 (stock_id, ym)，實際: {pk_cols}"


def test_monthly_revenue_covers_most_target_stocks(conn, target_stock_ids):
    if not DB_PATH.exists():
        pytest.skip("db not built")
    cur = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM monthly_revenue")
    distinct_stocks = cur.fetchone()[0]
    assert distinct_stocks > 0, "monthly_revenue 沒有任何資料，請先跑 python build_revenue_history.py"
    # 時序表覆蓋率看「涵蓋幾檔股票」而非「總列數」（總列數應是 檔數 x 月數量級）。
    # 已知 3 檔 -KY（外國發行人）股票在 MOPS 歷史封存頁面系統性缺席（見 docs/data-sources.md
    # 第 12 節），容許略低於 100% 覆蓋率。
    assert distinct_stocks / len(target_stock_ids) >= 0.9, (
        f"月營收覆蓋率過低: {distinct_stocks}/{len(target_stock_ids)}"
    )


def test_monthly_revenue_history_depth(conn, target_stock_ids):
    """核心驗證：這是近 3 年歷史，不是單列快照。"""
    cur = conn.execute("SELECT COUNT(DISTINCT ym) FROM monthly_revenue")
    distinct_yms = cur.fetchone()[0]
    # 目標 36 個月，允許少數月份因來源端缺頁（例如已知的 TWSE 2025-10 archive 回應空 body，
    # 見 docs/data-sources.md 第 12 節）而略少。
    assert distinct_yms >= 30, f"月營收歷史涵蓋月數過少（{distinct_yms} 個月），backfill 可能未完整執行"

    cur = conn.execute("SELECT COUNT(*) FROM monthly_revenue WHERE stock_id = ?", ("2330",))
    count_2330 = cur.fetchone()[0]
    assert count_2330 >= 30, f"2330 台積電近 3 年月營收筆數過少（{count_2330} 筆），預期 30+ 筆"


def test_monthly_revenue_known_stock_2330(conn):
    cur = conn.execute(
        "SELECT company_name, revenue, yoy_pct, mom_pct, source_market "
        "FROM monthly_revenue WHERE stock_id = ? ORDER BY ym DESC LIMIT 1",
        ("2330",),
    )
    row = cur.fetchone()
    assert row is not None, "2330 不在 monthly_revenue 中"
    company_name, rev, yoy_pct, mom_pct, source_market = row
    assert company_name == "台積電"
    assert rev is not None and rev > 0
    assert yoy_pct is not None
    assert mom_pct is not None
    assert source_market == "TWSE"


def test_monthly_revenue_yoy_mom_sane_range(conn):
    """量級合理性檢查：YoY/MoM 是百分比數字，不應是股數或營收金額誤植（防欄位錯位）。"""
    cur = conn.execute(
        "SELECT stock_id, ym, yoy_pct, mom_pct FROM monthly_revenue "
        "WHERE yoy_pct IS NOT NULL OR mom_pct IS NOT NULL"
    )
    rows = cur.fetchall()
    assert rows, "monthly_revenue 沒有任何 yoy_pct/mom_pct 資料"
    for stock_id, ym, yoy_pct, mom_pct in rows:
        if yoy_pct is not None:
            assert -100 <= yoy_pct <= 2000, f"{stock_id} {ym} yoy_pct 超出合理範圍: {yoy_pct}"
        if mom_pct is not None:
            assert -100 <= mom_pct <= 2000, f"{stock_id} {ym} mom_pct 超出合理範圍: {mom_pct}"


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


def test_institutional_flow_daily_covers_about_three_years(conn):
    """第五輪核心變更：institutional_flow_daily 從「近 60 個交易日」擴大為「近 3 年
    （約 750 個交易日）」累積式時序表。用全表 MIN/MAX date 落差驗證涵蓋期間確實約 3 年
    （允許來源端假日/個別缺漏，門檻抓 900 天，略低於 3*365=1095 天留緩衝）。"""
    cur = conn.execute("SELECT MIN(date), MAX(date) FROM institutional_flow_daily")
    min_date, max_date = cur.fetchone()
    assert min_date is not None and max_date is not None
    from datetime import date as _date
    span_days = (_date.fromisoformat(max_date) - _date.fromisoformat(min_date)).days
    assert span_days >= 900, f"institutional_flow_daily 涵蓋期間過短（{min_date} ~ {max_date}，{span_days} 天），backfill 可能未完整執行"


def test_institutional_flow_daily_trading_day_count_sane(conn):
    """每檔股票的交易日數應落在合理範圍：下限抓 100（避免只抓到一小段就被視為完整），
    上限抓 800（略高於 target_trading_days=750，抓明顯異常膨脹，例如重複寫入 bug）。
    個別股票可能因掛牌時間較晚、停牌等原因略低，不強制每檔都頂到 750。"""
    cur = conn.execute("SELECT stock_id, COUNT(*) AS n FROM institutional_flow_daily GROUP BY stock_id")
    rows = cur.fetchall()
    assert rows, "institutional_flow_daily 沒有任何資料"
    too_many = [(sid, n) for sid, n in rows if n > 800]
    assert too_many == [], f"部分股票交易日數超出合理上限（疑似重複寫入 bug）: {too_many}"
    median_n = sorted(n for _, n in rows)[len(rows) // 2]
    assert median_n >= 100, f"多數股票的交易日數過少（中位數 {median_n}），backfill 可能尚未完整執行"


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
