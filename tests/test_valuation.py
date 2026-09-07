"""驗證估值篩選表（build_valuation.py）的表結構與核心計算邏輯，不打網路 API。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import build_valuation as bv

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


@pytest.fixture(scope="module")
def conn():
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def test_tables_exist(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in ("per_daily", "eps_quarterly", "valuation_fetch_log", "valuation_screen", "fm_price_daily"):
        assert t in tables, f"缺少表 {t}，請先跑 python build_valuation.py --import-cache ... --screen"


def test_fm_price_daily_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || date) FROM fm_price_daily"
    ).fetchone()
    assert total == distinct


def test_valuation_screen_has_price_date_column(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(valuation_screen)").fetchall()}
    assert "price_date" in cols


def test_per_daily_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || date) FROM per_daily"
    ).fetchone()
    assert total == distinct


def test_eps_quarterly_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || quarter_end) FROM eps_quarterly"
    ).fetchone()
    assert total == distinct


def test_valuation_screen_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT run_date || '|' || stock_id || '|' || universe) FROM valuation_screen"
    ).fetchone()
    assert total == distinct


def test_valuation_screen_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM valuation_screen").fetchone()[0]
    assert n > 0, ("valuation_screen 是空的，請先跑 python build_valuation.py "
                   "--import-cache ... --screen")


def test_valuation_screen_universe_values_valid(conn):
    rows = conn.execute("SELECT DISTINCT universe FROM valuation_screen").fetchall()
    for (u,) in rows:
        assert u in ("ai_chain", "semiconductor")


# ---- 純函式邏輯（假資料，不碰資料庫） ----

def test_position_formula_example():
    """位置公式：(現價PER - P25) / (P75 - P25)，PER 剛好等於 P50 時位置應介於 0~1 之間。"""
    per_vals = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]  # p25=12.5 p50=15 p75=17.5（依 pct 公式）
    p25 = bv._pct(per_vals, 0.25)
    p75 = bv._pct(per_vals, 0.75)
    cur_per = 15.0
    position = (cur_per - p25) / (p75 - p25)
    assert 0 <= position <= 1

    # 現價 PER 低於 P25 -> 位置為負 -> 分類「低於合理區間」
    cur_per_low = p25 - 1.0
    position_low = (cur_per_low - p25) / (p75 - p25)
    assert position_low < 0


def test_split_flag_detects_large_single_day_jump():
    """緯穎 2026-09-02 一拆三案例：收盤價從 7800 掉到 2610，單日跌幅遠超 40% 門檻。"""
    prices_sorted = [
        ("2026-08-31", 7750.0),
        ("2026-09-01", 7800.0),
        ("2026-09-02", 2610.0),
        ("2026-09-04", 2565.0),
    ]
    assert bv._detect_split_flag(prices_sorted) is True


def test_split_flag_false_for_normal_price_series():
    prices_sorted = [
        ("2026-08-31", 100.0),
        ("2026-09-01", 101.5),
        ("2026-09-02", 99.0),
        ("2026-09-04", 100.5),
    ]
    assert bv._detect_split_flag(prices_sorted) is False


def test_price_asof_exact_date_match():
    rows = [("2026-09-01", 100.0), ("2026-09-02", 101.0), ("2026-09-04", 102.0)]
    assert bv._price_asof(rows, "2026-09-04") == (102.0, "2026-09-04")


def test_price_asof_falls_back_to_most_recent_earlier_date():
    """PER 最後日在 fm_price_daily 缺當天資料時，取 <= 該日最近一筆。"""
    rows = [("2026-08-20", 100.0), ("2026-08-25", 105.0)]
    assert bv._price_asof(rows, "2026-09-04") == (105.0, "2026-08-25")


def test_price_asof_none_when_no_data_before_asof():
    rows = [("2026-09-10", 100.0)]
    assert bv._price_asof(rows, "2026-09-04") is None


def test_split_via_per_jump_detects_jump_without_eps_change():
    """per_daily 本身跳動 >40%，附近沒有 eps_quarterly 的 quarter_end 可解釋，
    視為疑似分割（第二道保險，不依賴價格資料是否完整）。"""
    per_rows = [
        ("2026-08-31", 25.0),
        ("2026-09-01", 25.01),
        ("2026-09-02", 8.37),
        ("2026-09-04", 8.22),
    ]
    assert bv._detect_split_via_per_jump(per_rows, eps_quarter_ends=["2026-06-30"]) is True


def test_split_via_per_jump_false_when_near_quarter_end():
    """PER 跳動剛好發生在財報認列（quarter_end 附近 10 天內），視為正常 EPS 波動，
    不誤判為分割。"""
    per_rows = [
        ("2026-08-10", 25.0),
        ("2026-08-12", 8.5),
    ]
    assert bv._detect_split_via_per_jump(per_rows, eps_quarter_ends=["2026-08-14"]) is False


def test_split_via_per_jump_false_for_normal_series():
    per_rows = [("2026-08-31", 20.0), ("2026-09-01", 20.5), ("2026-09-02", 19.8)]
    assert bv._detect_split_via_per_jump(per_rows, eps_quarter_ends=[]) is False


def test_band_ok_false_when_split_flag_true():
    """split_flag=True 時無論其他指標多漂亮，band_ok 一律 False（本輪新增的緯穎盲點修正）。"""
    eps_cv = 0.1
    loss_q = 0
    n_per = 400
    n_eps_q = 8
    split_flag = True
    band_ok = (eps_cv < 0.5) and (loss_q == 0) and (n_per >= 300) and (n_eps_q == 8) and not split_flag
    assert band_ok is False


# ---- 【2026-09-07】營收 vs EPS 背離四欄 ----

def test_valuation_screen_has_revenue_divergence_columns(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(valuation_screen)").fetchall()}
    for c in ("rev_ym_latest", "rev_yoy_3m", "rev_yoy_ytd", "rev_eps_diverge"):
        assert c in cols, f"valuation_screen 缺少欄位 {c}"


def test_revenue_metrics_yoy_3m_formula(tmp_path):
    """rev_yoy_3m 用金額加總算，不是對 yoy_pct 取平均。"""
    db_path = tmp_path / "fake.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE monthly_revenue (stock_id TEXT, ym TEXT, revenue INTEGER, "
        "revenue_last_year_month INTEGER, yoy_pct REAL, revenue_cumulative INTEGER, "
        "cumulative_yoy_pct REAL)"
    )
    # 3 個月：今年合計 330、去年合計 300 -> yoy = 330/300 - 1 = 0.10
    rows = [
        ("2026-05", 100, 90),
        ("2026-06", 110, 100),
        ("2026-07", 120, 110),
    ]
    for ym, cur, last in rows:
        conn.execute(
            "INSERT INTO monthly_revenue (stock_id, ym, revenue, revenue_last_year_month, "
            "yoy_pct, revenue_cumulative, cumulative_yoy_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("9999", ym, cur, last, (cur / last - 1) * 100, cur, 12.34),
        )
    conn.commit()

    result = bv._revenue_metrics(conn, "9999", eps_ttm_growth=None)
    assert result["rev_ym_latest"] == "202607"
    assert result["rev_yoy_3m"] == pytest.approx(330 / 300 - 1)
    assert result["rev_yoy_ytd"] == pytest.approx(0.1234)
    assert result["rev_eps_diverge"] == 0
    conn.close()


def test_revenue_metrics_diverge_flag(tmp_path):
    """rev_yoy_3m < -0.10 且 eps_ttm_growth > 0.20 -> rev_eps_diverge=1。"""
    db_path = tmp_path / "fake2.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE monthly_revenue (stock_id TEXT, ym TEXT, revenue INTEGER, "
        "revenue_last_year_month INTEGER, yoy_pct REAL, revenue_cumulative INTEGER, "
        "cumulative_yoy_pct REAL)"
    )
    # 今年合計 240、去年合計 300 -> yoy = 240/300 - 1 = -0.20（< -0.10）
    rows = [
        ("2026-05", 80, 100),
        ("2026-06", 80, 100),
        ("2026-07", 80, 100),
    ]
    for ym, cur, last in rows:
        conn.execute(
            "INSERT INTO monthly_revenue (stock_id, ym, revenue, revenue_last_year_month, "
            "yoy_pct, revenue_cumulative, cumulative_yoy_pct) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("8888", ym, cur, last, (cur / last - 1) * 100, cur, -20.0),
        )
    conn.commit()

    result = bv._revenue_metrics(conn, "8888", eps_ttm_growth=0.25)  # EPS 仍成長 25%
    assert result["rev_yoy_3m"] == pytest.approx(-0.20)
    assert result["rev_eps_diverge"] == 1

    # EPS 沒有仍在頂 -> 不應標記
    result2 = bv._revenue_metrics(conn, "8888", eps_ttm_growth=0.05)
    assert result2["rev_eps_diverge"] == 0
    conn.close()


def test_revenue_metrics_insufficient_months_is_none(tmp_path):
    db_path = tmp_path / "fake3.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE monthly_revenue (stock_id TEXT, ym TEXT, revenue INTEGER, "
        "revenue_last_year_month INTEGER, yoy_pct REAL, revenue_cumulative INTEGER, "
        "cumulative_yoy_pct REAL)"
    )
    conn.execute(
        "INSERT INTO monthly_revenue VALUES ('7777', '2026-07', 100, 90, 11.1, 100, 5.0)"
    )
    conn.commit()
    result = bv._revenue_metrics(conn, "7777", eps_ttm_growth=0.5)
    assert result["rev_yoy_3m"] is None
    assert result["rev_eps_diverge"] == 0
    conn.close()
