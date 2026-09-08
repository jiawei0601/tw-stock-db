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
from split_price_returns import holding_return_from_split_prices
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
    ret, exit_reason = holding_return_from_split_prices(build_split_fixture(price_rows, 100.0 / 150.0), "2023-05-10", "2023-05-11")
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
    ret, exit_reason = holding_return_from_split_prices(build_split_fixture(price_rows, 3.0), "2023-06-01", "2023-06-02")
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
    ret, exit_reason = holding_return_from_split_prices(price_rows, "2023-02-01", "2023-07-31")
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


# ---------------------------------------------------------------------------
# (i) 事件表確認才還原、unresolved 不還原
# ---------------------------------------------------------------------------

def test_confirmed_events_adjusted_unresolved_unadjusted():
    """驗證公司行動候選中，有獨立事件表匹配者判定為 confirmed，找不到者為 unresolved。
    unresolved 價格跳動視為真實報酬，不進行任何還原調整。
    """
    price_rows_0001 = [("2023-05-10", 100.0), ("2023-05-11", 150.0)]
    price_rows_0002 = [("2023-06-01", 100.0), ("2023-06-02", 200.0)]

    cands_0001 = bv.detect_corporate_actions_for_stock(price_rows_0001, stock_id="0001")
    cands_0002 = bv.detect_corporate_actions_for_stock(price_rows_0002, stock_id="0002")

    events_map = {
        "0001": [("2023-05-11", "減資", 1.5)],
    }

    assert len(cands_0001) == 1
    assert "0001" in events_map

    assert len(cands_0002) == 1
    assert "0002" not in events_map

    ret_unresolved, _ = holding_return_from_split_prices(build_split_fixture(price_rows_0002, None), "2023-06-01", "2023-06-02")
    assert ret_unresolved == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# (ii) 還原價與原價在無事件區間報酬相同
# ---------------------------------------------------------------------------

def test_adjusted_price_equals_unadjusted_in_event_free_period():
    """驗證在無公司行動/除權息的事件空白期，還原價序列與未還原價序列的持有報酬完全相同。"""
    raw_prices = [
        ("2023-03-01", 50.0),
        ("2023-03-15", 55.0),
        ("2023-03-31", 60.0),
    ]
    k_adj = 1.25
    adj_prices = [(d, p * k_adj) for d, p in raw_prices]

    ret_raw = raw_prices[-1][1] / raw_prices[0][1] - 1.0
    ret_adj = adj_prices[-1][1] / adj_prices[0][1] - 1.0

    assert ret_adj == pytest.approx(ret_raw)
    assert ret_raw == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# (iii) bootstrap 函式被主流程呼叫且 CI 可重現（固定 seed 兩次相同）
# ---------------------------------------------------------------------------

def test_bootstrap_reproducible_with_fixed_seed():
    """驗證 block_bootstrap_paired_diff 固定 seed 兩次重抽產生完全相同的 CI 與統計量。"""
    np.random.seed(42)
    s_child = [0.08, 0.05, -0.02, 0.12, 0.06, 0.01, -0.04, 0.09, 0.03, 0.07]
    s_parent = [0.04, 0.03, -0.01, 0.07, 0.02, 0.00, -0.02, 0.05, 0.01, 0.04]

    run1 = bv.block_bootstrap_paired_diff(s_child, s_parent, block_size=3, n_resamples=1000, seed=2026)
    run2 = bv.block_bootstrap_paired_diff(s_child, s_parent, block_size=3, n_resamples=1000, seed=2026)

    assert run1["mean_diff"] == run2["mean_diff"]
    assert run1["median_diff"] == run2["median_diff"]
    assert run1["ci_95_lower"] == run2["ci_95_lower"]
    assert run1["ci_95_upper"] == run2["ci_95_upper"]
    assert run1["p_child_gt_parent"] == run2["p_child_gt_parent"]
    assert run1["n_blocks"] == run2["n_blocks"]


# ---------------------------------------------------------------------------
# (iv) 父子配對只取兩策略共同月
# ---------------------------------------------------------------------------

def test_paired_bootstrap_uses_common_months_only():
    """驗證父子配對嚴格只取該父子兩策略均有選股訊號之月份，不使用全策略交集。"""
    dates_a = {"2023-01", "2023-02", "2023-03", "2023-04", "2023-05"}
    dates_b = {"2023-02", "2023-03", "2023-04", "2023-06"}
    dates_c = {"2023-03", "2023-04"}

    common_ab = dates_a & dates_b
    assert len(common_ab) == 3
    assert "2023-02" in common_ab

    all_intersection = dates_a & dates_b & dates_c
    assert len(all_intersection) == 2
    assert common_ab != all_intersection


# ---------------------------------------------------------------------------
# (v) 下市部位用最後價結算並標記
# ---------------------------------------------------------------------------

def test_delisted_position_settled_at_last_price_and_marked():
    """驗證下市標的對照 delist_map 後，在下市日前最後可得價結算，並標記 delisted。"""
    all_dates = ["2023-01-31", "2023-02-01", "2023-03-01", "2023-03-15", "2023-04-28", "2023-05-02"]
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    delist_map = {"9999": "2023-03-20"}
    price_rows = [
        ("2023-01-31", 100.0),
        ("2023-02-01", 100.0),
        ("2023-03-01", 110.0),
        ("2023-03-15", 125.0),
    ]
    price_dict = dict(price_rows)

    target_dates = {"3m": "2023-04-28"}

    res = bv.compute_stock_forward_returns(
        sid="9999",
        sig_date="2023-01-31",
        target_dates=target_dates,
        price_dict=price_dict,
        price_rows=price_rows,
        all_trading_dates=all_dates,
        date_to_idx=date_to_idx,
        corp_actions_map=None,
        delist_map=delist_map,
    )

    assert res["valuation_status_3m"] == "delisted"
    assert res["ret_3m"] == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# (vi) 等資金分批公式
# ---------------------------------------------------------------------------

def test_equal_dollar_tranche_formula():
    """驗證等資金分批公式 P_exit * mean(1 / P_i) - 1.0 的代數正確性。"""
    tranche_prices = [100.0, 120.0, 150.0]
    exit_price = 180.0

    ret_formula = bv.compute_equal_dollar_tranche_return(tranche_prices, exit_price)
    assert ret_formula == pytest.approx(0.50)

    arithmetic_cost = np.mean(tranche_prices)
    ret_arithmetic = exit_price / arithmetic_cost - 1.0
    assert ret_arithmetic != pytest.approx(0.50)
    assert ret_formula != ret_arithmetic



def build_split_fixture(rows, ratio):
    import sqlite3
    from build_valuation import build_adj_prices_from_events
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("CREATE TABLE fm_price_daily(stock_id,date,close); CREATE TABLE fm_corporate_events(stock_id,date,ratio,source); CREATE TABLE fm_price_adj_daily(stock_id,date,close_adj,PRIMARY KEY(stock_id,date));")
        conn.executemany("INSERT INTO fm_price_daily VALUES ('TEST',?,?)", rows)
        if ratio is not None:
            conn.execute("INSERT INTO fm_corporate_events VALUES ('TEST',?,?,'測試')", (rows[-1][0], ratio))
        build_adj_prices_from_events(conn, stock_id="TEST")
        return conn.execute("SELECT date,close_adj FROM fm_price_adj_daily ORDER BY date").fetchall()


def test_event_fetch_utf8_and_idempotent(monkeypatch):
    import json
    import sqlite3
    import build_valuation as build
    import requests
    class Response:
        status_code = 200
        def __init__(self, dataset):
            data = [{"stock_id": "1234", "date": "2025-06-01", "type": "股票分割", "before_price": 400, "after_price": 100}] if dataset == "TaiwanStockSplitPrice" else []
            self.content = json.dumps({"status": 200, "data": data}, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(requests.Session, "get", lambda self, url, params, timeout: Response(params["dataset"]))
    monkeypatch.setattr(build, "_finmind_token", lambda: "假權杖")
    monkeypatch.setattr(build.time, "sleep", lambda seconds: None)
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("CREATE TABLE fm_corporate_events(stock_id,date,event_type,before_price,after_price,ratio,raw_json,source,PRIMARY KEY(stock_id,date,source)); CREATE TABLE fm_delisting(stock_id,date,name);")
        build.fetch_corporate_events(conn)
        build.fetch_corporate_events(conn)
        row = conn.execute("SELECT event_type,raw_json FROM fm_corporate_events").fetchall()
        assert len(row) == 1
        assert row[0][0] == "股票分割"
        assert json.loads(row[0][1])["type"] == "股票分割"
        # 相同事件不同來源仍只保留一列，NULL 價格亦同。
        event = ("5678", "2025-06-01", "減資", None, None, None, "{}", "來源一")
        build.write_corporate_event(conn, event)
        build.write_corporate_event(conn, event[:-1] + ("來源二",))
        assert conn.execute("SELECT count(*) FROM fm_corporate_events WHERE stock_id='5678'").fetchone()[0] == 1


@pytest.mark.parametrize("block", [6, 12])
def test_recheck_bootstrap_actual_repeat_and_seed_drift(block):
    from verify_validity_numbers import bootstrap
    parent = [i / 1000 for i in range(36)]
    child = [p + ((i * 7) % 19 - 8) / 100 for i, p in enumerate(parent)]
    diff = [c - p for c, p in zip(child, parent)]
    first = bootstrap(diff, block, seed=42)
    assert first == bootstrap(diff, block, seed=42)
    assert first != bootstrap(diff, block, seed=43)
    production1 = bv.block_bootstrap_paired_diff(child, parent, block_size=block, seed=42)
    production2 = bv.block_bootstrap_paired_diff(child, parent, block_size=block, seed=42)
    production43 = bv.block_bootstrap_paired_diff(child, parent, block_size=block, seed=43)
    for i, endpoint in enumerate(["ci_95_lower", "ci_95_upper"]):
        assert production1[endpoint] == production2[endpoint]
        assert first[i] == pytest.approx(production1[endpoint], abs=1e-14)
    assert (production43["ci_95_lower"], production43["ci_95_upper"]) != first


def test_recheck_ranks_ties_and_missing():
    from verify_validity_numbers import ranks
    assert ranks({"a": 2, "b": 2, "c": 1, "d": None}) == {"a": 2.5/3, "b": 2.5/3, "c": 1/3}


def test_recheck_forward_missing_cash_and_censoring():
    from verify_validity_numbers import forward
    dates = ["2023-01-31", "2023-02-01", "2023-02-02", "2023-02-03", "2023-05-02", "2023-08-01"]
    index = {d:i for i,d in enumerate(dates)}
    p = {"2023-02-02":100, "2023-05-02":125}
    assert forward(p, dates, index, dates[0], dates[-1], None) == (0.25, True)
    assert forward({}, dates, index, dates[0], dates[-1], None) == (0.0, False)
    assert forward(p, dates, index, dates[0], None, None) == (None, False)


def test_split_price_holding_return_empty_and_single_price():
    assert holding_return_from_split_prices([], "2023-01-01", "2023-02-01") == (None, "insufficient_data")
    assert holding_return_from_split_prices([("2023-01-02",100)], "2023-01-01", "2023-02-01") == (None, "insufficient_data")
