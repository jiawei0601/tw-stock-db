"""單元測試：D5 動能策略進場時機邏輯驗證。

工單要求：
(a) E1 的 5 日均只用 <= 當日資料（嚴格杜絕未來資料窺探）
(b) 5 天內未跌破 -> 不成交
(c) E2 成本為三次平均（第 1、5、10 交易日收盤均價）
(d) E3 新高判定含當日（近 20 日視窗包含當日，即當日突破前 19 日高點）
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import pytest

# 確保可自 backtest.entry_timing_test 匯入核心函式
REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backtest.entry_timing_test import (
    compute_ma5_at_day,
    check_e1_entry,
    check_e2_entry,
    check_e3_entry,
)


# ---------------------------------------------------------------------------
# (a) E1 的 5 日均只用 <= 當日資料
# ---------------------------------------------------------------------------

def test_e1_ma5_strictly_uses_only_past_and_current_data():
    """驗證 (a) E1 的 5 日均只取用 <= 當日之歷史價格，絕不偷看未來資料。"""
    # 建立 10 天序列，訊號日在 index 4 (價格 100.0)
    # 評估第 1 天 (index 5)，價格為 102.0
    # 歷史前 4 天至當天 (index 1..5): [100.0, 100.0, 100.0, 100.0, 102.0] -> 均值為 100.4
    # 當日收盤 102.0 > 100.4，未跌破均線
    # 測試組 A: 未來 (index 6 之後) 出現極端暴跌 (10.0)
    prices_with_crash = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 102.0, 10.0, 10.0, 10.0, 10.0])
    # 測試組 B: 未來 (index 6 之後) 出現極端暴漲 (999.0)
    prices_with_spike = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 102.0, 999.0, 999.0, 999.0, 999.0])

    # 驗證在 index 5 算出的均線值不受任何未來資料影響
    ma5_a = compute_ma5_at_day(prices_with_crash, current_idx=5)
    ma5_b = compute_ma5_at_day(prices_with_spike, current_idx=5)

    assert ma5_a == pytest.approx(100.4)
    assert ma5_b == pytest.approx(100.4)
    assert ma5_a == ma5_b

    # 驗證在第 1 天當日若收盤跌破 (99.0 < 100.0)，不論未來是崩盤還是暴漲，第 1 天皆能獨立且正確觸發
    prices_trigger_a = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 99.0, 10.0, 10.0, 10.0, 10.0])
    prices_trigger_b = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 99.0, 999.0, 999.0, 999.0, 999.0])

    res_a = check_e1_entry(prices_trigger_a, signal_idx=4, max_days=5)
    res_b = check_e1_entry(prices_trigger_b, signal_idx=4, max_days=5)

    assert res_a == (True, 1, 99.0)
    assert res_b == (True, 1, 99.0)


# ---------------------------------------------------------------------------
# (b) 5 天內未跌破 -> 不成交
# ---------------------------------------------------------------------------

def test_e1_unfilled_when_no_break_below_ma5():
    """驗證 (b) 5 天內若每日收盤皆 >= 當日 5MA，則該檔不成交。"""
    # 訊號日 index 4 (價格 100.0)
    # 後續 5 天每天皆持續上揚，每日收盤價皆高於近 5 日均線
    # index 0..4: 100.0
    # index 5: 105.0 (ma5 = 101.0, 105 > 101)
    # index 6: 110.0 (ma5 = 103.0, 110 > 103)
    # index 7: 115.0 (ma5 = 106.0, 115 > 106)
    # index 8: 120.0 (ma5 = 110.0, 120 > 110)
    # index 9: 125.0 (ma5 = 115.0, 125 > 115)
    prices = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 105.0, 110.0, 115.0, 120.0, 125.0])

    traded, delay, price = check_e1_entry(prices, signal_idx=4, max_days=5)

    assert traded is False
    assert delay is None
    assert price is None


# ---------------------------------------------------------------------------
# (c) E2 成本為三次平均
# ---------------------------------------------------------------------------

def test_e2_cost_is_average_of_three_tranches():
    """驗證 (c) E2 成本為訊號日後第 1、5、10 交易日收盤價之算術平均。"""
    # 訊號日 index 0
    # Day 1: 102.0
    # Day 5: 114.0
    # Day 10: 120.0
    # 成本應為 (102.0 + 114.0 + 120.0) / 3 = 336.0 / 3 = 112.0
    prices = np.full(15, 100.0)
    prices[1] = 102.0
    prices[5] = 114.0
    prices[10] = 120.0

    traded, avg_delay, cost = check_e2_entry(prices, signal_idx=0, tranches=(1, 5, 10))

    assert traded is True
    assert avg_delay == pytest.approx(16.0 / 3.0)  # (1 + 5 + 10) / 3 = 5.3333...
    assert cost == pytest.approx(112.0)


# ---------------------------------------------------------------------------
# (d) E3 新高判定含當日
# ---------------------------------------------------------------------------

def test_e3_breakout_includes_today_in_window():
    """驗證 (d) E3 新高判定之 20 日視窗包含當日（近 20 日視窗為 index t - 19 到 t）。

    核心情境：
    1. index t - 20 (距今 20 天前) 存在歷史高點 200.0；
    2. 近 19 日 (index t - 19 到 t - 1) 最高價僅為 100.0；
    3. 當日 (index t) 價格為 150.0。

    若視窗包含當日（正確）：
      - 20 日視窗為 index t - 19 到 t，20 天前的 200.0 已滑出視窗外！
      - 當日 150.0 > 過去 19 日最高 100.0，成功創近 20 日新高！
    若視窗不含當日（錯誤）：
      - 過去 20 日視窗包含 20 天前的 200.0，導致 150.0 < 200.0 誤判為未創新高。
    """
    signal_idx = 20
    # 建立長度 30 的序列
    prices = np.full(30, 100.0)

    # 距 Day 1 (index 21) 往前數 20 天的點為 index 1 (21 - 20 = 1)
    prices[1] = 200.0
    # Day 1 (index 21) 收盤價為 150.0
    prices[21] = 150.0

    traded, delay, price = check_e3_entry(prices, signal_idx=signal_idx, max_days=1, lookback=20)

    # 包含當日的 20 日視窗為 index 2..21，前 19 日最高為 100.0，故 150.0 成功突破！
    assert traded is True
    assert delay == 1
    assert price == 150.0

    # 反向檢驗：若當日價格未高於過去 19 日最高價（例如僅為 95.0），則未突破、不成交
    prices[21] = 95.0
    traded, delay, price = check_e3_entry(prices, signal_idx=signal_idx, max_days=1, lookback=20)
    assert traded is False
    assert delay is None
    assert price is None
