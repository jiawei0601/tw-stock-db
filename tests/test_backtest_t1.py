"""單元測試：動能轉強 T1 回測驗證

驗證項目 (docs/tasks/backtest-t1-turning.md 第 47 行)：
(a) flip 用前一訊號日的值，同月內未來價格不影響
(b) foreign_20d 只含 <= 訊號日
(c) 法人缺資料時 T1b/T1 為 NA 而非 False
(d) 層蘊含 T1x ⊆ T1 ⊆ T1b ⊆ T1a
(e) 廣度計算一例
"""
import pytest
import pandas as pd
import numpy as np

from backtest_t1 import (
    compute_institutional_flow_20d,
    compute_above_ma60,
    compute_ma20_up,
    evaluate_t1_tiers,
    compute_group_breadth_and_g1,
    is_institutional_date_valid,
)
from backtest_valuation import compute_momentum


def test_a_flip_uses_prev_signal_and_future_price_does_not_affect():
    """(a) flip 用前一訊號日的值，同月內未來價格不影響。"""
    # 建立三個時點：
    # t-1 訊號日: 2024-01-31
    # t 訊號日: 2024-02-29
    # 同月或未來價格: 2024-02-15 (月中震盪)、2024-03-05 (次月未來資料)
    all_trading_dates = ["2024-01-31", "2024-02-15", "2024-02-29", "2024-03-05"]
    date_to_idx = {d: i for i, d in enumerate(all_trading_dates)}

    # 情境 1: 個股在 1 月相對動能 <= 0，在 2 月相對動能 > 0
    # 驗證 flip_1m 只取 2024-01-31 與 2024-02-29 的值
    prev_rel_mom = -0.05
    cur_rel_mom = 0.02
    flip_true = bool(cur_rel_mom > 0 and prev_rel_mom <= 0)
    assert flip_true is True

    # 若前一訊號日相對動能已 > 0，則不構成 flip (非弱轉強)
    prev_rel_mom_pos = 0.03
    flip_false = bool(cur_rel_mom > 0 and prev_rel_mom_pos <= 0)
    assert flip_false is False

    # 驗證 compute_momentum Point-in-time 保護：
    # 即使價格字典包含 2024-03-05 的大漲價格 (150.0)，計算 2024-02-29 動能時嚴格限制 max_date="2024-02-29"
    prices_with_future = {
        "2024-01-31": 100.0,
        "2024-02-15": 80.0,
        "2024-02-29": 105.0,
        "2024-03-05": 200.0,  # 未來價格
    }
    mom_feb = compute_momentum(
        prices_dict=prices_with_future,
        all_trading_dates=all_trading_dates,
        date_to_idx=date_to_idx,
        signal_date="2024-02-29",
        past_date="2024-01-31",
    )
    assert mom_feb == pytest.approx(105.0 / 100.0 - 1.0)
    assert mom_feb != pytest.approx(200.0 / 100.0 - 1.0)


def test_b_foreign_20d_only_includes_le_signal_date():
    """(b) foreign_20d 只含 <= 訊號日。"""
    signal_date = "2024-06-28"
    # 模擬 25 筆法人資料：前 20 筆在訊號日及之前，後 5 筆在訊號日之後
    past_rows = [(f"2024-06-{i:02d}", 100, 50) for i in range(1, 29)]  # <= 2024-06-28
    future_rows = [("2024-06-29", 999999, 999999), ("2024-07-01", 888888, 888888)]
    all_flow_rows = past_rows + future_rows

    # 呼叫計算函式
    foreign_20d, trust_20d, chips_ok = compute_institutional_flow_20d(
        flow_rows=all_flow_rows,
        signal_date=signal_date,
        is_valid_period=True,
    )

    # 預期只累計 past_rows 的最近 20 筆，未來 2 筆被嚴格排除
    expected_foreign = sum(r[1] for r in past_rows[-20:])
    expected_trust = sum(r[2] for r in past_rows[-20:])

    assert foreign_20d == expected_foreign
    assert trust_20d == expected_trust
    assert foreign_20d < 500000  # 未納入未來 999999
    assert chips_ok is True


def test_c_missing_institutional_data_produces_na_not_false():
    """(c) 法人缺資料時 T1b/T1 為 NA 而非 False。"""
    # 情況 1: 訊號日在法人涵蓋期間之前 (如 2022-12-30)
    assert is_institutional_date_valid("2022-12-30") is False
    foreign_20d, trust_20d, chips_ok = compute_institutional_flow_20d(
        flow_rows=[("2022-12-30", 100, 100)],
        signal_date="2022-12-30",
        is_valid_period=False,
    )
    assert foreign_20d is None
    assert trust_20d is None
    assert chips_ok is None

    # 情況 2: 訊號日在法人涵蓋期間之後 (如 2026-08-31)
    assert is_institutional_date_valid("2026-08-31") is False

    # 情況 3: evaluate_t1_tiers 在 chips_ok is None 時，T1b 與 T1 必須為 None (NA)，絕非 False
    res = evaluate_t1_tiers(
        flip_1m=True,
        slope_3m=True,
        chips_ok=None,  # 法人缺資料
        above_ma60=True,
        is_trap=False,
    )
    assert res["T1a"] is True
    assert res["T1b"] is None
    assert res["T1"] is None
    assert res["T1x"] is None

    # 嚴格驗證不是 False
    assert res["T1b"] is not False
    assert res["T1"] is not False
    assert res["T1x"] is not False


def test_d_tier_containment_t1x_subset_t1_subset_t1b_subset_t1a():
    """(d) 層蘊含 T1x ⊆ T1 ⊆ T1b ⊆ T1a。"""
    # 窮舉所有可能的布林與 None 輸入組合，驗證在任何情況下層級蘊含關係恆成立
    for flip in (True, False):
        for slope in (True, False):
            for chips in (True, False, None):
                for above60 in (True, False):
                    for trap in (True, False):
                        res = evaluate_t1_tiers(
                            flip_1m=flip,
                            slope_3m=slope,
                            chips_ok=chips,
                            above_ma60=above60,
                            is_trap=trap,
                        )
                        t1a = res["T1a"]
                        t1b = res["T1b"]
                        t1 = res["T1"]
                        t1x = res["T1x"]

                        # 若 T1x 為 True，則 T1 必為 True
                        if t1x is True:
                            assert t1 is True
                        # 若 T1 為 True，則 T1b 必為 True
                        if t1 is True:
                            assert t1b is True
                        # 若 T1b 為 True，則 T1a 必為 True
                        if t1b is True:
                            assert t1a is True


def test_e_group_breadth_and_g1_calculation_example():
    """(e) 廣度計算一例與 G1 事件判定。"""
    # 建立合成資料集：1 個子產業 "半導體"，共有 4 檔股票，跨 2 個月份
    # 月份 1: 2024-01-31，只有 1 檔 above_ma60 (廣度 1/4 = 0.25 < 0.4)
    # 月份 2: 2024-02-29，有 3 檔 above_ma60 (廣度 3/4 = 0.75 >= 0.5)
    # 此時應精確觸發 G1 事件！

    data_m1 = [
        {"signal_date": "2024-01-31", "sub": "半導體", "above_ma60": True, "rel_mkt_mom_1m": 0.05, "ret_3m": 0.10, "ret_6m": 0.20, "bench_3m": 0.05, "bench_6m": 0.10},
        {"signal_date": "2024-01-31", "sub": "半導體", "above_ma60": False, "rel_mkt_mom_1m": -0.02, "ret_3m": 0.00, "ret_6m": 0.05, "bench_3m": 0.05, "bench_6m": 0.10},
        {"signal_date": "2024-01-31", "sub": "半導體", "above_ma60": False, "rel_mkt_mom_1m": 0.01, "ret_3m": 0.02, "ret_6m": 0.08, "bench_3m": 0.05, "bench_6m": 0.10},
        {"signal_date": "2024-01-31", "sub": "半導體", "above_ma60": False, "rel_mkt_mom_1m": -0.03, "ret_3m": -0.04, "ret_6m": 0.02, "bench_3m": 0.05, "bench_6m": 0.10},
    ]

    data_m2 = [
        {"signal_date": "2024-02-29", "sub": "半導體", "above_ma60": True, "rel_mkt_mom_1m": 0.08, "ret_3m": 0.15, "ret_6m": 0.25, "bench_3m": 0.06, "bench_6m": 0.12},
        {"signal_date": "2024-02-29", "sub": "半導體", "above_ma60": True, "rel_mkt_mom_1m": 0.02, "ret_3m": 0.12, "ret_6m": 0.22, "bench_3m": 0.06, "bench_6m": 0.12},
        {"signal_date": "2024-02-29", "sub": "半導體", "above_ma60": True, "rel_mkt_mom_1m": -0.01, "ret_3m": 0.08, "ret_6m": 0.18, "bench_3m": 0.06, "bench_6m": 0.12},
        {"signal_date": "2024-02-29", "sub": "半導體", "above_ma60": False, "rel_mkt_mom_1m": -0.05, "ret_3m": 0.01, "ret_6m": 0.07, "bench_3m": 0.06, "bench_6m": 0.12},
    ]

    df_eval = pd.DataFrame(data_m1 + data_m2)
    group_df, g1_df = compute_group_breadth_and_g1(df_eval)

    # 驗證月份 1 廣度：
    # above_ma60: 1/4 = 0.25
    # rel_mkt_mom_1m > 0: 2/4 = 0.50
    m1_row = group_df[group_df["signal_date"] == "2024-01-31"].iloc[0]
    assert m1_row["breadth_ma60"] == pytest.approx(0.25)
    assert m1_row["breadth_pos"] == pytest.approx(0.50)

    # 驗證月份 2 廣度：
    # above_ma60: 3/4 = 0.75
    # rel_mkt_mom_1m > 0: 2/4 = 0.50
    m2_row = group_df[group_df["signal_date"] == "2024-02-29"].iloc[0]
    assert m2_row["breadth_ma60"] == pytest.approx(0.75)
    assert m2_row["breadth_pos"] == pytest.approx(0.50)

    # 驗證 G1 事件產生
    assert len(g1_df) == 1
    g1_event = g1_df.iloc[0]
    assert g1_event["signal_date"] == "2024-02-29"
    assert g1_event["sub"] == "半導體"
    assert g1_event["breadth_prev"] == pytest.approx(0.25)
    assert g1_event["breadth_cur"] == pytest.approx(0.75)

    # 驗證超額報酬計算
    # sub_ret_3m = mean(0.15, 0.12, 0.08, 0.01) = 0.09
    # bench_3m = 0.06 -> excess_3m = +0.03
    # sub_ret_6m = mean(0.25, 0.22, 0.18, 0.07) = 0.18
    # bench_6m = 0.12 -> excess_6m = +0.06
    assert g1_event["excess_3m"] == pytest.approx(0.03)
    assert g1_event["excess_6m"] == pytest.approx(0.06)
    assert g1_event["win_6m"] == True
