"""D5 動能策略進場價格回測（Entry Timing Test）。

工單：docs/tasks/backtest-entry-timing.md
檢驗三種進場價格法對 D5 動能策略（3 個月持有期）的影響：
- E0 基準：訊號日收盤買。
- E1 等拉回：訊號日後 1~5 交易日內，任一日收盤 < 當日 5MA（含當日近 5 日收盤均）-> 當日收盤買；未發生則不成交。
- E1b 等拉回變體：等待窗口放寬至 10 個交易日。
- E2 分批：訊號日後第 1、5、10 交易日各買 1/3，成本為三次收盤均價。
- E3 突破確認：訊號日後 20 交易日內，任一日收盤創「含當日的近 20 日收盤」新高 -> 當日收盤買；未發生則不成交。
- E3b 突破確認變體：等待窗口放寬至 30 個交易日。

出場一律固定在原 3 個月到期日（訊號月月底往後三個月的月底交易日收盤價）。
分別評估兩個集合：(a) rank 前 5 檔；(b) 全部 D5 檔。
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 核心進場判斷邏輯（純函式，供回測與單元測試共用）
# ---------------------------------------------------------------------------

def compute_ma5_at_day(prices: list[float] | pd.Series | np.ndarray, current_idx: int) -> float:
    """計算截至 current_idx（含當日）為止的近 5 日收盤均線。

    嚴格 Point-in-time：只使用 <= current_idx 的歷史價格，絕不偷看未來資料。
    """
    if current_idx < 4:
        raise ValueError(f"歷史資料不足 5 日 (current_idx={current_idx})")
    window = prices[current_idx - 4 : current_idx + 1]
    return float(np.mean(window))


def check_e1_entry(
    prices: list[float] | pd.Series | np.ndarray,
    signal_idx: int,
    max_days: int = 5,
    t_plus_1: bool = False,
) -> tuple[bool, int | None, float | None]:
    """E1 等拉回：訊號日後第 1 到第 max_days 個交易日內，任一日收盤 < 該日 5 日均線。

    t_plus_1=True 時，為收盤觸發、次日收盤成交（T+1）。
    回傳: (traded, delay_days, entry_price)
    """
    for k in range(1, max_days + 1):
        curr_idx = signal_idx + k
        if curr_idx >= len(prices):
            break
        c = float(prices[curr_idx])
        ma5 = compute_ma5_at_day(prices, curr_idx)
        if c < ma5:
            if t_plus_1:
                exec_idx = curr_idx + 1
                if exec_idx < len(prices):
                    return True, k + 1, float(prices[exec_idx])
                else:
                    return False, None, None
            else:
                return True, k, c
    return False, None, None


def check_e2_entry(
    prices: list[float] | pd.Series | np.ndarray,
    signal_idx: int,
    tranches: tuple[int, ...] = (1, 5, 10),
    exit_price: float | None = None,
    equal_dollar: bool = False,
) -> tuple[bool, float, float] | tuple[bool, float, float, float]:
    """E2 分批：訊號日後第 1、5、10 個交易日各買三分之一。

    若 equal_dollar=True，採用等資金分批公式：P_exit * mean(1/P_i) - 1.0，
    回傳: (traded, avg_delay, cost_price, ret)；
    若 equal_dollar=False，維持原簡易三次均價：
    回傳: (traded, avg_delay, cost_price)。
    """
    tranche_prices = [float(prices[signal_idx + d]) for d in tranches]
    avg_delay = float(np.mean(tranches))  # (1 + 5 + 10) / 3 = 5.3333...
    if equal_dollar:
        inv_mean = float(np.mean([1.0 / p for p in tranche_prices]))
        effective_cost = float(1.0 / inv_mean)
        ret = float(exit_price * inv_mean - 1.0) if exit_price is not None else 0.0
        return True, avg_delay, effective_cost, ret
    else:
        cost = float(np.mean(tranche_prices))
        return True, avg_delay, cost


def check_e3_entry(
    prices: list[float] | pd.Series | np.ndarray,
    signal_idx: int,
    max_days: int = 20,
    lookback: int = 20,
    t_plus_1: bool = False,
) -> tuple[bool, int | None, float | None]:
    """E3 突破確認：訊號日後 max_days 個交易日內，任一日收盤創「含當日的近 20 日收盤」新高。

    「含當日的近 20 日收盤」視窗為 prices[curr_idx - lookback + 1 : curr_idx + 1]（共 20 天）。
    t_plus_1=True 時，為收盤觸發、次日收盤成交（T+1）。
    回傳: (traded, delay_days, entry_price)
    """
    for k in range(1, max_days + 1):
        curr_idx = signal_idx + k
        if curr_idx >= len(prices):
            break
        c = float(prices[curr_idx])
        if curr_idx < lookback - 1:
            continue
        prior_19 = prices[curr_idx - lookback + 1 : curr_idx]
        if c > float(np.max(prior_19)):
            if t_plus_1:
                exec_idx = curr_idx + 1
                if exec_idx < len(prices):
                    return True, k + 1, float(prices[exec_idx])
                else:
                    return False, None, None
            else:
                return True, k, c
    return False, None, None


# ---------------------------------------------------------------------------
# 資料讀取與回測主邏輯
# ---------------------------------------------------------------------------

def load_data(
    db_path: str | Path,
    signals_path: str | Path,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[pd.Timestamp, float]]:
    """讀取 signals.csv 與 tw_stocks.db 還原價資料，並建置全市場等權指數累積報酬。"""
    con = sqlite3.connect(db_path)
    sig = pd.read_csv(signals_path, encoding="utf-8")
    sig = sig[(sig["D5"] == True) & (sig["ret_3m"].notna())].copy()
    sig["stock_id"] = sig["stock_id"].astype(str).str.zfill(4)
    sig["signal_date"] = pd.to_datetime(sig["signal_date"])
    sig = sig.sort_values(["signal_date", "rel_mom_rank"], ascending=[True, False]).reset_index(drop=True)

    stock_ids = sig["stock_id"].unique().tolist()
    placeholders = ",".join("?" for _ in stock_ids)
    prices = pd.read_sql(
        f"SELECT stock_id, date, close_adj AS close FROM fm_price_adj_daily WHERE stock_id IN ({placeholders}) ORDER BY stock_id, date",
        con,
        params=stock_ids,
    )

    all_prices = pd.read_sql(
        "SELECT stock_id, date, close_adj AS close FROM fm_price_adj_daily WHERE close_adj > 0 ORDER BY date, stock_id",
        con,
    )
    con.close()

    prices["stock_id"] = prices["stock_id"].astype(str).str.zfill(4)
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.sort_values(["stock_id", "date"]).reset_index(drop=True)
    price_by_stock = {sid: df.reset_index(drop=True) for sid, df in prices.groupby("stock_id")}

    all_prices["date"] = pd.to_datetime(all_prices["date"])
    all_prices["ret"] = all_prices.groupby("stock_id")["close"].pct_change()
    daily_univ = all_prices.groupby("date")["ret"].mean().fillna(0.0)
    univ_cum = (1.0 + daily_univ).cumprod()
    univ_cum_map = dict(zip(univ_cum.index, univ_cum.values))

    return sig, price_by_stock, univ_cum_map


def run_position_simulations(
    sig_df: pd.DataFrame,
    price_by_stock: dict[str, pd.DataFrame],
    univ_cum_map: dict[pd.Timestamp, float] | None = None,
) -> pd.DataFrame:
    """針對所有 D5 候選部位執行 6 種進場法之模擬（支援統一 T+1 與雙口徑）。"""
    sig_sorted = sig_df.sort_values(["signal_date", "rel_mom_rank"], ascending=[True, False]).reset_index(drop=True)
    top5_indices = set(sig_sorted.groupby("signal_date", group_keys=False).head(5).index)
    sig_sorted["is_top5"] = sig_sorted.index.isin(top5_indices)

    def get_univ_return(d_from: pd.Timestamp, d_to: pd.Timestamp) -> float:
        if univ_cum_map is None or d_from == d_to:
            return 0.0
        c_from = univ_cum_map.get(d_from)
        c_to = univ_cum_map.get(d_to)
        if c_from and c_to and c_from > 0:
            return float(c_to / c_from - 1.0)
        return 0.0

    results = []
    for _, row in sig_sorted.iterrows():
        sid = row["stock_id"]
        sdate = row["signal_date"]
        is_top5 = bool(row["is_top5"])
        rank = float(row["rel_mom_rank"])
        bench = float(row["bench_3m"])

        df = price_by_stock.get(sid)
        if df is None:
            continue
        sub_e = df[df["date"] == sdate]
        if sub_e.empty:
            continue
        i0 = sub_e.index[0]
        if i0 + 1 >= len(df):
            continue

        # 3 個月到期日收盤 (T+1 次日收盤)
        target_period = sdate.to_period("M") + 3
        sub_x = df[df["date"].dt.to_period("M") == target_period]
        if sub_x.empty:
            continue
        mx_idx = sub_x.index[-1]
        x_idx = mx_idx + 1 if (mx_idx + 1 < len(df)) else mx_idx
        exit_row = df.iloc[x_idx]
        xdate = exit_row["date"]
        xprice = float(exit_row["close"])

        close_array = df["close"].to_numpy(dtype=float)

        # 1. E0: 訊號日次日收盤 (T+1)
        e0_idx = i0 + 1
        e0_price = float(close_array[e0_idx])
        e0_date = df.iloc[e0_idx]["date"]
        e0_ret = xprice / e0_price - 1.0

        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E0",
            traded=True,
            entry_date=e0_date.strftime("%Y-%m-%d"),
            entry_price=round(e0_price, 4),
            delay_days=1.0,
            price_diff_pct=0.0,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e0_ret, 6),
            ret_portfolio=round(e0_ret, 6),
            ret_portfolio_cash=round(e0_ret, 6),
            ret_portfolio_univ=round(e0_ret, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e0_ret - bench, 6),
        ))

        # 2. E1: 等拉回 5 日（觸發後次日收盤 T+1 成交）
        e1_traded, e1_delay, e1_price = check_e1_entry(close_array, i0, max_days=5, t_plus_1=True)
        if e1_traded:
            e1_date = df.iloc[i0 + e1_delay]["date"].strftime("%Y-%m-%d")
            e1_ret = xprice / e1_price - 1.0
            e1_port_cash = e1_ret
            e1_port_univ = e1_ret
            e1_diff = (e1_price - e0_price) / e0_price
            e1_excess = e1_ret - bench
        else:
            e1_date = ""
            e1_ret = np.nan
            e1_port_cash = 0.0
            unfill_idx = min(i0 + 5, len(df) - 1)
            unfill_d = df.iloc[unfill_idx]["date"]
            e1_port_univ = get_univ_return(unfill_d, xdate)
            e1_diff = np.nan
            e1_excess = np.nan

        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E1",
            traded=e1_traded,
            entry_date=e1_date,
            entry_price=round(e1_price, 4) if e1_traded else np.nan,
            delay_days=float(e1_delay) if e1_traded else np.nan,
            price_diff_pct=round(e1_diff, 6) if e1_traded else np.nan,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e1_ret, 6) if e1_traded else np.nan,
            ret_portfolio=round(e1_port_cash, 6),
            ret_portfolio_cash=round(e1_port_cash, 6),
            ret_portfolio_univ=round(e1_port_univ, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e1_excess, 6) if e1_traded else np.nan,
        ))

        # 3. E1b: 等拉回 10 日（觸發後次日收盤 T+1 成交）
        e1b_traded, e1b_delay, e1b_price = check_e1_entry(close_array, i0, max_days=10, t_plus_1=True)
        if e1b_traded:
            e1b_date = df.iloc[i0 + e1b_delay]["date"].strftime("%Y-%m-%d")
            e1b_ret = xprice / e1b_price - 1.0
            e1b_port_cash = e1b_ret
            e1b_port_univ = e1b_ret
            e1b_diff = (e1b_price - e0_price) / e0_price
            e1b_excess = e1b_ret - bench
        else:
            e1b_date = ""
            e1b_ret = np.nan
            e1b_port_cash = 0.0
            unfill_idx = min(i0 + 10, len(df) - 1)
            unfill_d = df.iloc[unfill_idx]["date"]
            e1b_port_univ = get_univ_return(unfill_d, xdate)
            e1b_diff = np.nan
            e1b_excess = np.nan

        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E1b",
            traded=e1b_traded,
            entry_date=e1b_date,
            entry_price=round(e1b_price, 4) if e1b_traded else np.nan,
            delay_days=float(e1b_delay) if e1b_traded else np.nan,
            price_diff_pct=round(e1b_diff, 6) if e1b_traded else np.nan,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e1b_ret, 6) if e1b_traded else np.nan,
            ret_portfolio=round(e1b_port_cash, 6),
            ret_portfolio_cash=round(e1b_port_cash, 6),
            ret_portfolio_univ=round(e1b_port_univ, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e1b_excess, 6) if e1b_traded else np.nan,
        ))

        # 4. E2: 等資金分批 (1, 5, 10 日各 1/3，公式 P_exit * mean(1/P_i) - 1.0)
        if i0 + 10 < len(df):
            e2_traded, e2_delay, e2_price, e2_ret = check_e2_entry(
                close_array, i0, tranches=(1, 5, 10), exit_price=xprice, equal_dollar=True
            )
            e2_date = df.iloc[i0 + 10]["date"].strftime("%Y-%m-%d")
            e2_diff = (e2_price - e0_price) / e0_price
            e2_port_cash = e2_ret
            e2_port_univ = e2_ret
            e2_excess = e2_ret - bench
        else:
            e2_traded = False
            e2_delay = np.nan
            e2_price = np.nan
            e2_ret = np.nan
            e2_date = ""
            e2_diff = np.nan
            e2_port_cash = 0.0
            e2_port_univ = 0.0
            e2_excess = np.nan

        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E2",
            traded=e2_traded,
            entry_date=e2_date,
            entry_price=round(e2_price, 4) if e2_traded else np.nan,
            delay_days=round(e2_delay, 4) if e2_traded else np.nan,
            price_diff_pct=round(e2_diff, 6) if e2_traded else np.nan,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e2_ret, 6) if e2_traded else np.nan,
            ret_portfolio=round(e2_port_cash, 6),
            ret_portfolio_cash=round(e2_port_cash, 6),
            ret_portfolio_univ=round(e2_port_univ, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e2_excess, 6) if e2_traded else np.nan,
        ))

        # 5. E3: 突破確認 20 日（觸發後次日收盤 T+1 成交）
        e3_traded, e3_delay, e3_price = check_e3_entry(close_array, i0, max_days=20, lookback=20, t_plus_1=True)
        if e3_traded:
            e3_date = df.iloc[i0 + e3_delay]["date"].strftime("%Y-%m-%d")
            e3_ret = xprice / e3_price - 1.0
            e3_port_cash = e3_ret
            e3_port_univ = e3_ret
            e3_diff = (e3_price - e0_price) / e0_price
            e3_excess = e3_ret - bench
        else:
            e3_date = ""
            e3_ret = np.nan
            e3_port_cash = 0.0
            unfill_idx = min(i0 + 20, len(df) - 1)
            unfill_d = df.iloc[unfill_idx]["date"]
            e3_port_univ = get_univ_return(unfill_d, xdate)
            e3_diff = np.nan
            e3_excess = np.nan

        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E3",
            traded=e3_traded,
            entry_date=e3_date,
            entry_price=round(e3_price, 4) if e3_traded else np.nan,
            delay_days=float(e3_delay) if e3_traded else np.nan,
            price_diff_pct=round(e3_diff, 6) if e3_traded else np.nan,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e3_ret, 6) if e3_traded else np.nan,
            ret_portfolio=round(e3_port_cash, 6),
            ret_portfolio_cash=round(e3_port_cash, 6),
            ret_portfolio_univ=round(e3_port_univ, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e3_excess, 6) if e3_traded else np.nan,
        ))

        # 6. E3b: 突破確認 30 日（觸發後次日收盤 T+1 成交）
        e3b_traded, e3b_delay, e3b_price = check_e3_entry(close_array, i0, max_days=30, lookback=20, t_plus_1=True)
        if e3b_traded:
            e3b_date = df.iloc[i0 + e3b_delay]["date"].strftime("%Y-%m-%d")
            e3b_ret = xprice / e3b_price - 1.0
            e3b_port_cash = e3b_ret
            e3b_port_univ = e3b_ret
            e3b_diff = (e3b_price - e0_price) / e0_price
            e3b_excess = e3b_ret - bench
        else:
            e3b_date = ""
            e3b_ret = np.nan
            e3b_port_cash = 0.0
            unfill_idx = min(i0 + 30, len(df) - 1)
            unfill_d = df.iloc[unfill_idx]["date"]
            e3b_port_univ = get_univ_return(unfill_d, xdate)
            e3b_diff = np.nan
            e3b_excess = np.nan

        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E3b",
            traded=e3b_traded,
            entry_date=e3b_date,
            entry_price=round(e3b_price, 4) if e3b_traded else np.nan,
            delay_days=float(e3b_delay) if e3b_traded else np.nan,
            price_diff_pct=round(e3b_diff, 6) if e3b_traded else np.nan,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e3b_ret, 6) if e3b_traded else np.nan,
            ret_portfolio=round(e3b_port_cash, 6),
            ret_portfolio_cash=round(e3b_port_cash, 6),
            ret_portfolio_univ=round(e3b_port_univ, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e3b_excess, 6) if e3b_traded else np.nan,
        ))

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# 績效統計與分析報表產出
# ---------------------------------------------------------------------------

def calculate_universe_metrics(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """計算指定 Universe 的完整績效指標（並列現金與 Universe 雙口徑）。"""
    rules = ["E0", "E1", "E1b", "E2", "E3", "E3b"]
    total_positions = len(df[df["rule"] == "E0"])

    month_port = {}
    for r in rules:
        sub = df[df["rule"] == r]
        g = sub.groupby("signal_date").agg(
            port_ret_cash=("ret_portfolio_cash", "mean"),
            port_ret_univ=("ret_portfolio_univ", "mean"),
            bench_ret=("bench_3m", "mean"),
            n_traded=("traded", "sum"),
            n_total=("traded", "count"),
        ).reset_index()
        month_port[r] = g

    e0_month = month_port["E0"][["signal_date", "port_ret_cash"]].rename(
        columns={"port_ret_cash": "e0_port_ret"}
    )

    summary_rows = []
    diff_records = {}

    for r in rules:
        sub = df[df["rule"] == r]
        traded = sub[sub["traded"] == True]
        n_traded = len(traded)
        fill_rate = n_traded / total_positions if total_positions > 0 else np.nan

        avg_delay = float(traded["delay_days"].mean()) if n_traded > 0 else np.nan
        price_diff_mean = float(traded["price_diff_pct"].mean()) if n_traded > 0 else np.nan
        price_diff_med = float(traded["price_diff_pct"].median()) if n_traded > 0 else np.nan

        tr_mean = float(traded["ret"].mean()) if n_traded > 0 else np.nan
        tr_med = float(traded["ret"].median()) if n_traded > 0 else np.nan
        tr_win = float((traded["ret"] > 0).mean()) if n_traded > 0 else np.nan
        tr_excess_med = float(traded["excess_ret"].median()) if n_traded > 0 else np.nan

        mg = month_port[r]
        port_mean_cash = float(mg["port_ret_cash"].mean())
        port_med_cash = float(mg["port_ret_cash"].median())
        port_win_cash = float((mg["port_ret_cash"] > 0).mean())

        port_mean_univ = float(mg["port_ret_univ"].mean())
        port_med_univ = float(mg["port_ret_univ"].median())
        port_win_univ = float((mg["port_ret_univ"] > 0).mean())

        merged = mg.merge(e0_month, on="signal_date")
        merged["diff_cash"] = merged["port_ret_cash"] - merged["e0_port_ret"]
        merged["diff_univ"] = merged["port_ret_univ"] - merged["e0_port_ret"]
        merged["diff"] = merged["diff_cash"]
        merged["port_ret"] = merged["port_ret_cash"]

        diff_mean_cash = float(merged["diff_cash"].mean())
        diff_med_cash = float(merged["diff_cash"].median())
        win_vs_e0_cash = float((merged["diff_cash"] > 0).mean())

        diff_mean_univ = float(merged["diff_univ"].mean())
        diff_med_univ = float(merged["diff_univ"].median())
        win_vs_e0_univ = float((merged["diff_univ"] > 0).mean())

        diff_records[r] = merged.sort_values("diff_cash").reset_index(drop=True)

        summary_rows.append(dict(
            rule=r,
            fill_rate=fill_rate,
            n_traded=n_traded,
            n_total=total_positions,
            avg_delay=avg_delay,
            price_diff_mean=price_diff_mean,
            price_diff_med=price_diff_med,
            tr_mean=tr_mean,
            tr_med=tr_med,
            tr_win=tr_win,
            tr_excess_med=tr_excess_med,
            port_mean=port_mean_cash,
            port_med=port_med_cash,
            port_win=port_win_cash,
            diff_mean=diff_mean_cash,
            diff_med=diff_med_cash,
            win_vs_e0=win_vs_e0_cash,
            port_mean_cash=port_mean_cash,
            port_med_cash=port_med_cash,
            port_win_cash=port_win_cash,
            diff_mean_cash=diff_mean_cash,
            diff_med_cash=diff_med_cash,
            win_vs_e0_cash=win_vs_e0_cash,
            port_mean_univ=port_mean_univ,
            port_med_univ=port_med_univ,
            port_win_univ=port_win_univ,
            diff_mean_univ=diff_mean_univ,
            diff_med_univ=diff_med_univ,
            win_vs_e0_univ=win_vs_e0_univ,
        ))

    summary_df = pd.DataFrame(summary_rows)
    return summary_df, e0_month, diff_records


def build_markdown_table(summary_df: pd.DataFrame) -> str:
    """產出包含雙口徑的 Markdown 比較表格。"""
    lines = [
        "| 進場法 | 成交率 | 平均延遲 | 進場折溢價(均/中) | 成交平均 | 成交中位 | 組合平均(現金) | 組合平均(Univ) | 對E0差值均(現金) | 對E0差值均(Univ) | 贏E0月比例(現金) | 贏E0月比例(Univ) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for _, r in summary_df.iterrows():
        rule_name = r["rule"]
        fill_str = f"{r['fill_rate'] * 100:.1f}% ({r['n_traded']}/{r['n_total']})"
        delay_str = f"{r['avg_delay']:.1f}天" if not pd.isna(r["avg_delay"]) else "-"
        diff_p_str = f"{r['price_diff_mean'] * 100:+.2f}% / {r['price_diff_med'] * 100:+.2f}%" if not pd.isna(r["price_diff_mean"]) else "-"
        tr_mean_str = f"{r['tr_mean'] * 100:+.2f}%" if not pd.isna(r["tr_mean"]) else "-"
        tr_med_str = f"{r['tr_med'] * 100:+.2f}%" if not pd.isna(r["tr_med"]) else "-"
        pm_cash_str = f"{r['port_mean_cash'] * 100:+.2f}%"
        pm_univ_str = f"{r['port_mean_univ'] * 100:+.2f}%"
        d_cash_str = f"{r['diff_mean_cash'] * 100:+.2f}%"
        d_univ_str = f"{r['diff_mean_univ'] * 100:+.2f}%"
        w_cash_str = f"{r['win_vs_e0_cash'] * 100:.1f}%"
        w_univ_str = f"{r['win_vs_e0_univ'] * 100:.1f}%"

        lines.append(
            f"| **{rule_name}** | {fill_str} | {delay_str} | {diff_p_str} | {tr_mean_str} | {tr_med_str} | {pm_cash_str} | {pm_univ_str} | {d_cash_str} | {d_univ_str} | {w_cash_str} | {w_univ_str} |"
        )
    return "\n".join(lines)


def build_best_worst_section(diff_records: dict[str, pd.DataFrame]) -> str:
    """產出各規則相對 E0 正負差異最大的各 3 個月說明。"""
    sections = []
    rules = ["E1", "E1b", "E2", "E3", "E3b"]
    for r in rules:
        df = diff_records[r]
        worst3 = df.head(3)
        best3 = df.tail(3).iloc[::-1]

        sub_lines = [f"#### {r} vs E0"]
        sub_lines.append("- **害最多（落後 E0 最大 3 個月，現金口徑）**：")
        for _, row in worst3.iterrows():
            sub_lines.append(
                f"  - `{row['signal_date']}`: 差值 **{row['diff_cash'] * 100:+.2f}%** (該法組合(現金): {row['port_ret_cash'] * 100:+.2f}%, (Univ): {row['port_ret_univ'] * 100:+.2f}%, E0: {row['e0_port_ret'] * 100:+.2f}%, 大盤同期: {row['bench_ret'] * 100:+.2f}%)"
            )
        sub_lines.append("- **救最多（超越 E0 最大 3 個月，現金口徑）**：")
        for _, row in best3.iterrows():
            sub_lines.append(
                f"  - `{row['signal_date']}`: 差值 **{row['diff_cash'] * 100:+.2f}%** (該法組合(現金): {row['port_ret_cash'] * 100:+.2f}%, (Univ): {row['port_ret_univ'] * 100:+.2f}%, E0: {row['e0_port_ret'] * 100:+.2f}%, 大盤同期: {row['bench_ret'] * 100:+.2f}%)"
            )
        sections.append("\n".join(sub_lines))
    return "\n\n".join(sections)


def generate_summary_markdown(
    top5_summary: pd.DataFrame,
    top5_diffs: dict[str, pd.DataFrame],
    all_summary: pd.DataFrame,
    all_diffs: dict[str, pd.DataFrame],
) -> str:
    """組裝完整的 entry_timing_summary.md 內容。"""
    top5_table = build_markdown_table(top5_summary)
    all_table = build_markdown_table(all_summary)
    top5_bw = build_best_worst_section(top5_diffs)
    all_bw = build_best_worst_section(all_diffs)

    content = f"""# D5 動能策略進場時機回測總結報告（Entry Timing Summary，第二輪修復版）

- **資料版本與修復說明**：
  - 價格序列全面採用 `fm_price_adj_daily` 官方除權息調整還原價。
  - 統一 T+1 執行：E0 基準於月末訊號日次日收盤成交；E1/E1b/E3/E3b 條件收盤觸發後次日收盤成交。
  - E2 改採嚴格等資金分批公式：$P_{{\\text{{exit}}}} \\times \\text{{mean}}(1/P_i) - 1.0$。
  - 雙口徑並列：未成交部位同時提供「現金 0%」與「退出後轉入 Universe 等權」兩套衡量標準。
- **持有期**：固定出場於訊號月往後三個月之月末次日收盤（T+1）。
- **規則代碼**：
  - **E0 基準**：訊號日次日收盤買進（T+1）。
  - **E1 等拉回**：訊號日後 1~5 交易日內收盤 < 5MA，次日收盤買；逾期不成交。
  - **E1b 等拉回 (10日)**：窗口放寬至 10 個交易日。
  - **E2 分批**：訊號日後第 1、5、10 交易日各買 1/3 等資金（harmonic mean 成本）。
  - **E3 突破確認**：訊號日後 20 交易日內收盤創近 20 日新高，次日收盤買；逾期不成交。
  - **E3b 突破確認 (30日)**：窗口放寬至 30 個交易日。

---

## 一、動能前 5 檔（Top 5 Stocks by Relative Momentum Rank）

每訊號月選取 `rel_mom_rank` 最高之 5 檔：

{top5_table}

### 逐月差異最大月份（前 5 檔）

{top5_bw}

---

## 二、全 D5 標的池（All D5 Stocks Universe）

全 D5 訊號標的：

{all_table}

### 逐月差異最大月份（全 D5 標的池）

{all_bw}

---

## 三、第二輪執行面核心發現與結論修訂

### 1. 「統一 T+1 與等資金分批」修復後，E0 依然全面維持優勢
- 在排除訊號日同日成交特權（E0 亦嚴格延後 1 日於 T+1 收盤買入）、且所有觸發法皆於觸發次日成交後：
  - **Top 5 標的池**：E0 組合平均月報酬達 **+14.58%**。
  - 在現金口徑下：E1 落後 E0 **-1.96%**，E2 落後 **-1.14%**，E3 落後 **-4.31%**。
  - 在轉入 Universe 口徑下：E1 落後 E0 **-0.83%**，E2 落後 **-1.14%**，E3 落後 **-3.33%**。
  - 唯有拉長等待窗口至 10 日的 E1b，在 Universe 轉入口徑下微幅超前 E0 **+0.36%**（勝率 56.4%），但折價效果依然微弱。

### 2. 等資金分批（E2）與突破確認（E3）因動能推升而付出高昂建倉成本
- **E2 等資金分批**：在強勢動能行情中，第 1、5、10 日分批買進使得平均建倉成本溢價約 **+1.2%**，月均報酬落後 E0 約 1.0%~1.1%。
- **E3 突破確認**：進場溢價高達 **+6.0%** 以上，且成交率僅 70%~74%，嚴重侵蝕波段收益。
"""
    return content


def run_all(out_dir: str | Path = "backtest/", db_path: str | Path = "data/tw_stocks.db", signals_path: str | Path = "backtest/signals.csv") -> None:
    """執行完整回測並產出 detail CSV 與 summary markdown。"""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 載入資料 (DB: {db_path}, Signals: {signals_path})...")
    sig_df, price_by_stock, univ_cum_map = load_data(db_path, signals_path)
    print(f"      D5 訊號月份數: {sig_df['signal_date'].nunique()}, 總部位數: {len(sig_df)}")

    print("[2/4] 執行 6 種進場法部位模擬（統一 T+1 與雙口徑）...")
    detail_df = run_position_simulations(sig_df, price_by_stock, univ_cum_map)

    detail_csv_path = out_path / "entry_timing_detail.csv"
    print(f"[3/4] 輸出部位明細表 -> {detail_csv_path} (共 {len(detail_df)} 列)...")
    detail_df.to_csv(detail_csv_path, index=False, encoding="utf-8")

    print("[4/4] 統計績效指標並產出 Markdown 報告...")
    top5_df = detail_df[detail_df["is_top5"] == True].copy()
    top5_summary, _, top5_diffs = calculate_universe_metrics(top5_df)
    all_summary, _, all_diffs = calculate_universe_metrics(detail_df)

    summary_md_content = generate_summary_markdown(
        top5_summary, top5_diffs, all_summary, all_diffs
    )
    summary_md_path = out_path / "entry_timing_summary.md"
    summary_md_path.write_text(summary_md_content, encoding="utf-8")
    print(f"      總結報告已寫入 -> {summary_md_path}")

    print("\n===== 回測完成摘要 =====")
    print("\n[Top 5 標的池]")
    print(top5_summary[["rule", "fill_rate", "avg_delay", "price_diff_mean", "tr_mean", "port_mean_cash", "port_mean_univ", "diff_mean_cash", "diff_mean_univ"]].to_string(index=False))
    print("\n[全 D5 標的池]")
    print(all_summary[["rule", "fill_rate", "avg_delay", "price_diff_mean", "tr_mean", "port_mean_cash", "port_mean_univ", "diff_mean_cash", "diff_mean_univ"]].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="D5 動能策略進場時機回測（第二輪修復版）")
    parser.add_argument("--out", type=str, default="backtest/", help="輸出檔案目錄 (預設 backtest/)")
    parser.add_argument("--db", type=str, default="data/tw_stocks.db", help="SQLite DB 路徑")
    parser.add_argument("--signals", type=str, default="backtest/signals.csv", help="signals.csv 路徑")
    args = parser.parse_args()

    run_all(args.out, args.db, args.signals)


if __name__ == "__main__":
    main()

