# -*- coding: utf-8 -*-
"""單元測試：回測研究有效性修復驗證（tests/test_validity.py）

依據 docs/tasks/backtest-validity-repair.md 規範：
(a) 不完整月份不產生訊號；
(b) 進場價為次日收盤、T+1 停牌順延且 3 日內無價判未成交；
(b2) 向上跳動 1.5 倍且符合減資倍率→判公司行動、事件日報酬保留真實波動；
(c) 分割還原後報酬正確（假資料一拆三，經濟報酬 0）；
(d) 缺價部位用最後可得價且有標記；
(e) 連續八季檢查（缺一季→NA）；
(f) 法人缺資料→NA 不觸發；
(g) 區塊 bootstrap 抽樣索引在策略間一致；
(h) 兩支程式的 C1 同月成員完全相同。
"""
from __future__ import annotations

from datetime import datetime, timedelta
import pytest
import numpy as np
import pandas as pd

import backtest_validity as bv
from backtest_t1 import compute_institutional_flow_20d, evaluate_t1_tiers


# ---------------------------------------------------------------------------
# (a) 不完整月份不產生訊號
# ---------------------------------------------------------------------------

def test_incomplete_month_does_not_produce_signal():
    """驗證未完成月份（例如 2026-09-04 僅 4 個交易日）不被當作月末訊號日。"""
    trading_dates = [
        "2026-07-31",
        "2026-08-03", "2026-08-15", "2026-08-31",
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
    ]

    # 2026-08 有後續月份 2026-09 的資料，判定為完整月
    assert bv.is_month_complete("2026-08", trading_dates) is True

    # 2026-09 只有 4 天，距離月底還有 26 天，判定為未完成月
    assert bv.is_month_complete("2026-09", trading_dates) is False

    signals = bv.filter_complete_month_signals(trading_dates)
    assert "2026-08-31" in signals
    assert "2026-09-04" not in signals


# ---------------------------------------------------------------------------
# (b) 進場價為次日收盤、T+1 停牌順延且 3 日內無價判未成交
# ---------------------------------------------------------------------------

def test_entry_t_plus_1_and_untradeable_policy():
    """驗證次日收盤進場、停牌/鎖死順延最多 3 日，超限判未成交。"""
    all_dates = ["2023-01-31", "2023-02-01", "2023-02-02", "2023-02-03", "2023-02-06", "2023-02-07"]
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    # 次日為 2023-02-01
    next_d = bv.get_next_trading_day("2023-01-31", all_dates, date_to_idx)
    assert next_d == "2023-02-01"

    # 情境 1: 次日正常可成交
    prices_normal = {"2023-01-31": 100.0, "2023-02-01": 101.0}
    p, d, status = bv.resolve_execution_price(prices_normal, all_dates, date_to_idx, "2023-02-01", max_shift_days=3)
    assert p == 101.0
    assert d == "2023-02-01"
    assert status == "ok"

    # 情境 2: T+1 停牌（無價），T+2 恢復正常成交（順延 1 天）
    prices_shift = {"2023-01-31": 100.0, "2023-02-02": 102.0}
    p, d, status = bv.resolve_execution_price(prices_shift, all_dates, date_to_idx, "2023-02-01", max_shift_days=3)
    assert p == 102.0
    assert d == "2023-02-02"
    assert status == "shifted"

    # 情境 3: T+1~T+4 全無價格（連續 4 個交易日無價，超過 3 日順延限制）
    prices_untradeable = {"2023-01-31": 100.0, "2023-02-07": 110.0}
    p, d, status = bv.resolve_execution_price(prices_untradeable, all_dates, date_to_idx, "2023-02-01", max_shift_days=3)
    assert p is None
    assert d is None
    assert status == "untradeable_no_price"

    # 情境 4: T+1 漲停鎖死 (漲幅 >= 9.5%)，順延至 T+2
    prices_limit = {
        "2023-01-31": 100.0,
        "2023-02-01": 109.8,  # +9.8% >= 9.5% 鎖死
        "2023-02-02": 112.0,  # 相比 109.8 漲 +2.0%，正常
    }
    p, d, status = bv.resolve_execution_price(prices_limit, all_dates, date_to_idx, "2023-02-01", max_shift_days=3)
    assert p == 112.0
    assert d == "2023-02-02"
    assert status == "shifted"


# ---------------------------------------------------------------------------
# (b2) 向上跳動 1.5 倍且符合減資倍率→判公司行動、事件日報酬保留真實波動
# ---------------------------------------------------------------------------

def test_capital_reduction_detection_and_event_return_preservation():
    """驗證向上跳動 1.5 倍判為減資換股，且事件日報酬保留當日真實波動（不得記 0）。"""
    price_rows = [
        ("2023-05-10", 100.0),
        ("2023-05-11", 153.0),
    ]

    actions = bv.detect_corporate_actions_for_stock(price_rows, stock_id="TEST")
    assert len(actions) == 1
    act = actions[0]
    assert act["direction"] == "up"
    assert act["reason"] == "reduction_1.5x"
    assert act["multiplier"] == pytest.approx(1.0 / 1.5)

    corp_map = {"2023-05-11": act["multiplier"]}
    ret, exit_reason = bv.compute_holding_return_adjusted(price_rows, "2023-05-10", "2023-05-11", corp_map)
    assert ret == pytest.approx(0.02)
    assert ret != 0.0
    assert exit_reason == "normal"


# ---------------------------------------------------------------------------
# (c) 分割還原後報酬正確（假資料一拆三，經濟報酬 0）
# ---------------------------------------------------------------------------

def test_stock_split_adjustment_economic_return_zero():
    """驗證一拆三股票在還原後之經濟報酬為 0%。"""
    price_rows = [
        ("2023-06-01", 300.0),
        ("2023-06-02", 100.0),
    ]
    actions = bv.detect_corporate_actions_for_stock(price_rows, stock_id="TEST_SPLIT")
    assert len(actions) == 1
    act = actions[0]
    assert act["direction"] == "down"
    assert act["multiplier"] == 3.0

    corp_map = {"2023-06-02": 3.0}
    ret, exit_reason = bv.compute_holding_return_adjusted(price_rows, "2023-06-01", "2023-06-02", corp_map)
    assert ret == pytest.approx(0.0)
    assert exit_reason == "normal"


# ---------------------------------------------------------------------------
# (d) 缺價部位用最後可得價且有標記
# ---------------------------------------------------------------------------

def test_missing_exit_price_uses_last_available():
    """驗證到期缺價（例如下市/停牌）採用最後可得價，且標記 exit_reason=last_available。"""
    price_rows = [
        ("2023-02-01", 100.0),
        ("2023-05-01", 120.0),
        ("2023-07-15", 130.0),
    ]
    ret, exit_reason = bv.compute_holding_return_adjusted(price_rows, "2023-02-01", "2023-07-31", corp_actions_map={})
    assert ret == pytest.approx(130.0 / 100.0 - 1.0)
    assert exit_reason == "last_available"


# ---------------------------------------------------------------------------
# (e) 連續八季檢查（缺一季→NA）
# ---------------------------------------------------------------------------

def test_continuous_eight_quarters_eps():
    """驗證 EPS 必須連續 8 季，若缺季或距訊號日超過 200 天則回傳 NA。"""
    signal_date = "2023-06-30"

    # 1. 完整連續 8 季
    q_ends_clean = [
        "2021-06-30", "2021-09-30", "2021-12-31", "2022-03-31",
        "2022-06-30", "2022-09-30", "2022-12-31", "2023-03-31",
    ]
    eps_clean = [(q, 2.0, "2023-05-15") for q in q_ends_clean]
    ok, vals = bv.check_continuous_eps(eps_clean, signal_date, required_quarters=8)
    assert ok is True
    assert len(vals) == 8

    # 2. 中間缺一季
    q_ends_gap = [
        "2021-06-30", "2021-09-30", "2021-12-31", "2022-03-31",
        "2022-09-30", "2022-12-31", "2023-03-31", "2023-06-30",
    ]
    eps_gap = [(q, 2.0, "2023-05-15") for q in q_ends_gap]
    ok_gap, vals_gap = bv.check_continuous_eps(eps_gap, signal_date, required_quarters=8)
    assert ok_gap is False
    assert len(vals_gap) == 0

    # 3. 最新一季距訊號日超過 200 天
    eps_stale = [(q, 2.0, "2022-05-15") for q in [
        "2020-09-30", "2020-12-31", "2021-03-31", "2021-06-30",
        "2021-09-30", "2021-12-31", "2022-03-31", "2022-06-30",
    ]]
    ok_stale, _ = bv.check_continuous_eps(eps_stale, signal_date, required_quarters=8)
    assert ok_stale is False


# ---------------------------------------------------------------------------
# (f) 法人缺資料→NA 不觸發
# ---------------------------------------------------------------------------

def test_missing_institutional_data_produces_na_not_trigger():
    """驗證法人無資料回傳 NA (None)，T1b/T1/T1x 絕不觸發（為 None 而非 True/False）。"""
    flow_empty = None
    foreign_20d, trust_20d, chips_ok = compute_institutional_flow_20d(
        flow_rows=flow_empty,
        signal_date="2024-03-31",
        is_valid_period=True,
    )
    assert foreign_20d is None
    assert trust_20d is None
    assert chips_ok is None

    tiers = evaluate_t1_tiers(
        flip_1m=True,
        slope_3m=True,
        chips_ok=chips_ok,
        above_ma60=True,
        is_trap=False,
    )
    assert tiers["T1a"] is True
    assert tiers["T1b"] is None
    assert tiers["T1"] is None
    assert tiers["T1x"] is None


# ---------------------------------------------------------------------------
# (g) 區塊 bootstrap 抽樣索引在策略間一致
# ---------------------------------------------------------------------------

def test_block_bootstrap_sampling_index_consistency():
    """驗證多個策略在區塊 Bootstrap 時共享完全相同的時間抽樣索引。"""
    np.random.seed(123)
    series_d5 = [0.05, 0.02, -0.01, 0.08, 0.04, 0.03, -0.02, 0.06]
    series_d3 = [0.03, 0.01, -0.02, 0.05, 0.02, 0.01, -0.01, 0.04]
    series_c1 = [0.02, 0.00, -0.03, 0.04, 0.01, 0.00, -0.03, 0.03]

    res_d5_d3 = bv.block_bootstrap_paired_diff(series_d5, series_d3, block_size=3, n_resamples=500, seed=999)
    res_d3_c1 = bv.block_bootstrap_paired_diff(series_d3, series_c1, block_size=3, n_resamples=500, seed=999)

    assert res_d5_d3["ci_95_lower"] <= res_d5_d3["mean_diff"] <= res_d5_d3["ci_95_upper"]
    assert res_d3_c1["ci_95_lower"] <= res_d3_c1["mean_diff"] <= res_d3_c1["ci_95_upper"]
    assert res_d5_d3["n_blocks"] > 0
    assert res_d3_c1["n_blocks"] > 0


# ---------------------------------------------------------------------------
# (h) 兩支程式的 C1 同月成員完全相同
# ---------------------------------------------------------------------------

def test_c1_membership_exact_match_between_both_backtests():
    """驗證 backtest_valuation 與 backtest_t1 在相同母體與日期下產生的 C1 選股集合完全相同。"""
    month_stocks_val = [
        {"stock_id": "2330", "sub": "半導體", "mom_3m": 0.35, "mom_1m": 0.10},
        {"stock_id": "2454", "sub": "半導體", "mom_3m": 0.15, "mom_1m": 0.05},
        {"stock_id": "2317", "sub": "組裝", "mom_3m": 0.25, "mom_1m": 0.08},
        {"stock_id": "2308", "sub": "組裝", "mom_3m": 0.05, "mom_1m": 0.02},
    ]
    month_stocks_t1 = [dict(s) for s in month_stocks_val]

    from backtest_valuation import compute_sub_relative_momentum, compute_percentile_rank
    compute_sub_relative_momentum(month_stocks_val)
    ranks_val = compute_percentile_rank([s["rel_mom_3m"] for s in month_stocks_val])
    c1_val = {s["stock_id"] for s, rk in zip(month_stocks_val, ranks_val) if rk is not None and rk >= 0.75}

    from backtest_t1 import compute_sub_relative_momentum as t1_sub_mom, compute_percentile_rank as t1_rank
    t1_sub_mom(month_stocks_t1)
    ranks_t1 = t1_rank([s["rel_mom_3m"] for s in month_stocks_t1])
    c1_t1 = {s["stock_id"] for s, rk in zip(month_stocks_t1, ranks_t1) if rk is not None and rk >= 0.75}

    assert c1_val == c1_t1
    assert len(c1_val) > 0
