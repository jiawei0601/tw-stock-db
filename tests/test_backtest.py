"""驗證估值篩選器回測框架（backtest_valuation.py）的核心 Point-in-time 邏輯。

依據工單規範，以臨時 SQLite / 記憶體資料庫至少驗證：
(a) EPS 在可見日之前不會被用到；
(b) 月營收在次月 10 日前不可見；
(c) PER 視窗不包含訊號日之後的資料；
(d) 分割排除生效；
(e) 前瞻報酬取對日期。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

import backtest_valuation as bv


# ---------------------------------------------------------------------------
# (a) EPS 在可見日之前不會被用到
# ---------------------------------------------------------------------------

def test_eps_visible_date_rules():
    """驗證 EPS 可見日規則：3/31, 6/30, 9/30 -> +45 天；12/31 -> +75 天。"""
    # Q1: 03-31 + 45 days = 05-15
    assert bv.get_eps_visible_date("2023-03-31") == "2023-05-15"
    # Q2: 06-30 + 45 days = 08-14
    assert bv.get_eps_visible_date("2023-06-30") == "2023-08-14"
    # Q3: 09-30 + 45 days = 11-14
    assert bv.get_eps_visible_date("2023-09-30") == "2023-11-14"
    # Q4: 12-31 + 75 days = 次年 03-16 (非閏年 2023->2024 是閏年 3/15; 2022->2023 是 3/16)
    assert bv.get_eps_visible_date("2022-12-31") == "2023-03-16"


def test_eps_not_used_before_visible_date():
    """驗證在訊號日 <= 可見日之前，該季 EPS 絕對不會被納入指標計算。"""
    # 建立 8 季歷史資料 + 1 季未來尚未公告資料 (2023-03-31，可見日 2023-05-15)
    # 2023-03-31 故意給一個極端負值 -50.0 (若被採納會導致 loss_q > 0)
    quarters = [
        ("2021-03-31", 2.0, bv.get_eps_visible_date("2021-03-31")),
        ("2021-06-30", 2.0, bv.get_eps_visible_date("2021-06-30")),
        ("2021-09-30", 2.0, bv.get_eps_visible_date("2021-09-30")),
        ("2021-12-31", 2.0, bv.get_eps_visible_date("2021-12-31")),
        ("2022-03-31", 2.0, bv.get_eps_visible_date("2022-03-31")),
        ("2022-06-30", 2.0, bv.get_eps_visible_date("2022-06-30")),
        ("2022-09-30", 2.0, bv.get_eps_visible_date("2022-09-30")),
        ("2022-12-31", 2.0, bv.get_eps_visible_date("2022-12-31")),
        ("2023-03-31", -50.0, bv.get_eps_visible_date("2023-03-31")), # 2023-05-15 可見
    ]

    # 訊號日 2023-04-30 (早於 2023-05-15 可見日)
    eps_cv, loss_q, eps_ttm_growth, n_vis = bv.compute_eps_metrics(quarters, "2023-04-30")
    # 驗證此時只能看到前 8 季，2023-03-31 尚未被納入
    assert n_vis == 8
    assert loss_q == 0  # 負值未被算入
    assert eps_cv == 0.0 # 前 8 季均為 2.0，標準差為 0

    # 訊號日 2023-05-31 (晚於 2023-05-15 可見日)
    eps_cv_after, loss_q_after, eps_growth_after, n_vis_after = bv.compute_eps_metrics(quarters, "2023-05-31")
    # 驗證此時 2023-03-31 已可見並被算入
    assert n_vis_after == 9
    assert loss_q_after == 1 # -50.0 被採納，虧損季數變為 1
    assert eps_cv_after > 0.5


# ---------------------------------------------------------------------------
# (b) 月營收在次月 10 日前不可見
# ---------------------------------------------------------------------------

def test_revenue_visible_date_rules():
    """驗證月營收在次月 10 日可見。"""
    assert bv.get_revenue_visible_date("2023-08") == "2023-09-10"
    assert bv.get_revenue_visible_date("2023-12") == "2024-01-10"


def test_revenue_not_visible_before_10th_next_month():
    """驗證在次月 10 日之前，該月營收絕對不可見。"""
    # 2023-08 營收在 2023-09-10 公告
    # 2023-05 ~ 2023-08 營收
    rev_rows = [
        ("2023-05", 100, 100, bv.get_revenue_visible_date("2023-05")), # 06-10 可見
        ("2023-06", 100, 100, bv.get_revenue_visible_date("2023-06")), # 07-10 可見
        ("2023-07", 100, 100, bv.get_revenue_visible_date("2023-07")), # 08-10 可見
        ("2023-08", 300, 100, bv.get_revenue_visible_date("2023-08")), # 09-10 可見 (暴增 200%)
    ]

    # 訊號日為 2023-08-31 (8 月底，早於 2023-09-10)
    yoy_aug = bv.compute_revenue_metrics(rev_rows, "2023-08-31")
    # 此時 2023-08 不可見，最近 3 個月只能看到 05, 06, 07
    # 05~07 合計 300 / 去年 300 - 1 = 0.0
    assert yoy_aug == pytest.approx(0.0)

    # 訊號日為 2023-09-30 (9 月底，晚於 2023-09-10)
    yoy_sep = bv.compute_revenue_metrics(rev_rows, "2023-09-30")
    # 此時 2023-08 已可見，最近 3 個月為 06(100), 07(100), 08(300) -> sum=500 / sum_ly=300 -> yoy = 500/300 - 1 = 0.6667
    assert yoy_sep == pytest.approx(500.0 / 300.0 - 1.0)


# ---------------------------------------------------------------------------
# (c) PER 視窗不包含訊號日之後的資料
# ---------------------------------------------------------------------------

def test_per_window_excludes_data_after_signal_date():
    """驗證在訊號日 T 算 PER 視窗分位時，完全排除 date > T 的任何資料。"""
    # 建立 2021-01-01 到 2023-06-30 的平穩 PER 數列 (10 ~ 20 之間)
    dates = pd.date_range("2021-01-01", "2023-06-30", freq="B").strftime("%Y-%m-%d").tolist()
    per_rows = [(d, 15.0) for d in dates]

    # 在訊號日 2023-06-30 當天給定 PER = 15.0
    # 在訊號日之後（2023-07-01 ~ 2023-07-15）塞入極端高 PER 299.0
    per_rows.append(("2023-07-01", 299.0))
    per_rows.append(("2023-07-05", 299.0))
    per_rows.append(("2023-07-15", 299.0))

    # 依日期排序
    per_rows_sorted = sorted(per_rows, key=lambda x: x[0])

    res = bv.compute_per_position(per_rows_sorted, "2023-06-30")
    assert res is not None
    cur_per, p25, p75, pos, n_pts = res

    # 驗證點數與分位數完全不受 7 月極端值影響
    assert n_pts == len(dates)
    assert cur_per == 15.0
    assert p25 == 15.0
    assert p75 == 15.0


# ---------------------------------------------------------------------------
# (d) 分割排除生效
# ---------------------------------------------------------------------------

def test_split_flag_detection_and_exclusion():
    """驗證訊號日前 60 個交易日內單日漲跌幅 > 40% 被正確標記分割並排除。"""
    signal_date = "2023-06-30"

    # 股票 S1: 平穩走勢，每日收盤 100 ~ 105
    dates = pd.date_range("2023-01-01", signal_date, freq="B").strftime("%Y-%m-%d").tolist()
    prices_normal = [(d, 100.0) for d in dates]

    # 股票 S2: 在訊號日前 10 天 (2023-06-15) 發生除權/拆股，價格從 200 跌至 100 (跌幅 50% > 40%)
    prices_split = []
    for d in dates:
        if d < "2023-06-15":
            prices_split.append((d, 200.0))
        else:
            prices_split.append((d, 100.0))

    # 股票 S3: 在訊號日前 80 天 (超出 60 交易日視窗) 發生分割，近 60 天內無跳動
    prices_old_split = []
    for i, d in enumerate(dates):
        if i < 10:
            prices_old_split.append((d, 200.0))
        else:
            prices_old_split.append((d, 100.0))

    assert bv.detect_split_flag(prices_normal, signal_date) is False
    assert bv.detect_split_flag(prices_split, signal_date) is True
    assert bv.detect_split_flag(prices_old_split, signal_date) is False


# ---------------------------------------------------------------------------
# (e) 前瞻報酬取對日期
# ---------------------------------------------------------------------------

def test_forward_return_takes_correct_dates():
    """驗證前瞻報酬精確選取 3/6/12 個月後之目標訊號日價格。"""
    signal_date = "2023-01-31"
    tgt_3m = "2023-04-28"
    tgt_6m = "2023-07-31"
    tgt_12m = "2024-01-31"

    all_trading_dates = [
        "2023-01-31",
        "2023-02-15",
        "2023-04-28",
        "2023-05-10",
        "2023-07-31",
        "2023-10-15",
        "2024-01-31",
    ]
    date_to_idx = {d: i for i, d in enumerate(all_trading_dates)}

    prices_dict = {
        "2023-01-31": 100.0,
        "2023-02-15": 999.0, # 干擾價格
        "2023-04-28": 120.0, # 3m 目標收盤: +20%
        "2023-05-10": 10.0,  # 干擾價格
        "2023-07-31": 150.0, # 6m 目標收盤: +50%
        "2023-10-15": 5.0,   # 干擾價格
        "2024-01-31": 200.0, # 12m 目標收盤: +100%
    }

    p_cur = bv.get_close_price(prices_dict, all_trading_dates, date_to_idx, signal_date)
    p_3m = bv.get_close_price(prices_dict, all_trading_dates, date_to_idx, tgt_3m)
    p_6m = bv.get_close_price(prices_dict, all_trading_dates, date_to_idx, tgt_6m)
    p_12m = bv.get_close_price(prices_dict, all_trading_dates, date_to_idx, tgt_12m)

    assert p_cur == 100.0
    assert p_3m == 120.0
    assert p_6m == 150.0
    assert p_12m == 200.0

    ret_3m = p_3m / p_cur - 1.0
    ret_6m = p_6m / p_cur - 1.0
    ret_12m = p_12m / p_cur - 1.0

    assert ret_3m == pytest.approx(0.20)
    assert ret_6m == pytest.approx(0.50)
    assert ret_12m == pytest.approx(1.00)


# ---------------------------------------------------------------------------
# 端到端整合測試（使用臨時 SQLite 資料庫）
# ---------------------------------------------------------------------------

def test_backtest_end_to_end_temp_db(tmp_path):
    """在臨時 SQLite 資料庫建構最小可用資料，執行 run_backtest 驗證端到端無誤。"""
    test_db = tmp_path / "test_stocks.db"
    out_dir = tmp_path / "backtest_out"

    conn = sqlite3.connect(test_db)
    conn.executescript("""
    CREATE TABLE valuation_screen (stock_id TEXT PRIMARY KEY, universe TEXT);
    CREATE TABLE stock_sub_industry (stock_id TEXT, chain TEXT, node TEXT, sub TEXT);
    CREATE TABLE fm_price_daily (stock_id TEXT, date TEXT, close REAL, PRIMARY KEY (stock_id, date));
    CREATE TABLE per_daily (stock_id TEXT, date TEXT, per REAL, pbr REAL, dividend_yield REAL, PRIMARY KEY (stock_id, date));
    CREATE TABLE eps_quarterly (stock_id TEXT, quarter_end TEXT, eps REAL, PRIMARY KEY (stock_id, quarter_end));
    CREATE TABLE fm_revenue_monthly (stock_id TEXT, ym TEXT, revenue INTEGER, revenue_last_year INTEGER, PRIMARY KEY (stock_id, ym));
    """)

    conn.execute("INSERT INTO valuation_screen VALUES ('2330', 'semiconductor')")
    conn.execute("INSERT INTO stock_sub_industry VALUES ('2330', '半導體', '晶圓製造', '晶圓代工')")

    # 建立 2021-01 到 2024-06 的工作日
    dates = pd.date_range("2021-01-01", "2024-06-30", freq="B").strftime("%Y-%m-%d").tolist()
    price_rows = [('2330', d, 500.0) for d in dates]
    per_rows = [('2330', d, 15.0, 3.0, 2.5) for d in dates]
    conn.executemany("INSERT INTO fm_price_daily VALUES (?, ?, ?)", price_rows)
    conn.executemany("INSERT INTO per_daily VALUES (?, ?, ?, ?, ?)", per_rows)

    # 建立 8 季 EPS
    q_ends = [
        "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31",
        "2022-03-31", "2022-06-30", "2022-09-30", "2022-12-31",
    ]
    eps_rows = [('2330', q, 5.0) for q in q_ends]
    conn.executemany("INSERT INTO eps_quarterly VALUES (?, ?, ?)", eps_rows)

    conn.commit()
    conn.close()

    res = bv.run_backtest(db_path=test_db, out_dir=out_dir)

    assert (out_dir / "signals.csv").exists()
    assert (out_dir / "summary.md").exists()
    assert res["total_signal_months"] > 0
    assert res["evaluable_months"] > 0
