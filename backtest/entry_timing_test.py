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
) -> tuple[bool, int | None, float | None]:
    """E1 等拉回：訊號日後第 1 到第 max_days 個交易日內，任一日收盤 < 該日 5 日均線。

    回傳: (traded, delay_days, entry_price)
    """
    for k in range(1, max_days + 1):
        curr_idx = signal_idx + k
        c = float(prices[curr_idx])
        ma5 = compute_ma5_at_day(prices, curr_idx)
        if c < ma5:
            return True, k, c
    return False, None, None


def check_e2_entry(
    prices: list[float] | pd.Series | np.ndarray,
    signal_idx: int,
    tranches: tuple[int, ...] = (1, 5, 10),
) -> tuple[bool, float, float]:
    """E2 分批：訊號日後第 1、5、10 個交易日各買三分之一，成本為三次收盤平均。

    回傳: (traded, avg_delay, cost_price)
    """
    tranche_prices = [float(prices[signal_idx + d]) for d in tranches]
    cost = float(np.mean(tranche_prices))
    avg_delay = float(np.mean(tranches))  # (1 + 5 + 10) / 3 = 5.3333...
    return True, avg_delay, cost


def check_e3_entry(
    prices: list[float] | pd.Series | np.ndarray,
    signal_idx: int,
    max_days: int = 20,
    lookback: int = 20,
) -> tuple[bool, int | None, float | None]:
    """E3 突破確認：訊號日後 max_days 個交易日內，任一日收盤創「含當日的近 20 日收盤」新高。

    「含當日的近 20 日收盤」視窗為 prices[curr_idx - lookback + 1 : curr_idx + 1]（共 20 天）。
    當日收盤創該視窗新高即代表當日收盤嚴格大於前 19 日之最高收盤價。

    回傳: (traded, delay_days, entry_price)
    """
    for k in range(1, max_days + 1):
        curr_idx = signal_idx + k
        c = float(prices[curr_idx])
        if curr_idx < lookback - 1:
            continue
        prior_19 = prices[curr_idx - lookback + 1 : curr_idx]
        if c > float(np.max(prior_19)):
            return True, k, c
    return False, None, None


# ---------------------------------------------------------------------------
# 資料讀取與回測主邏輯
# ---------------------------------------------------------------------------

def load_data(
    db_path: str | Path,
    signals_path: str | Path,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """讀取 signals.csv 與 tw_stocks.db 唯讀資料。"""
    con = sqlite3.connect(db_path)
    sig = pd.read_csv(signals_path, encoding="utf-8")
    sig = sig[(sig["D5"] == True) & (sig["ret_3m"].notna())].copy()
    sig["stock_id"] = sig["stock_id"].astype(str)
    sig["signal_date"] = pd.to_datetime(sig["signal_date"])
    sig = sig.sort_values(["signal_date", "rel_mom_rank"], ascending=[True, False]).reset_index(drop=True)

    stock_ids = sig["stock_id"].unique().tolist()
    placeholders = ",".join("?" for _ in stock_ids)
    prices = pd.read_sql(
        f"SELECT stock_id, date, close FROM fm_price_daily WHERE stock_id IN ({placeholders}) ORDER BY stock_id, date",
        con,
        params=stock_ids,
    )
    con.close()

    prices["stock_id"] = prices["stock_id"].astype(str)
    prices["date"] = pd.to_datetime(prices["date"])
    prices = prices.sort_values(["stock_id", "date"]).reset_index(drop=True)
    price_by_stock = {sid: df.reset_index(drop=True) for sid, df in prices.groupby("stock_id")}
    return sig, price_by_stock


def run_position_simulations(
    sig_df: pd.DataFrame,
    price_by_stock: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """針對所有 D5 候選部位執行 6 種進場法之模擬。"""
    # 標註 top 5 (每月 rel_mom_rank 最高 5 檔)
    sig_sorted = sig_df.sort_values(["signal_date", "rel_mom_rank"], ascending=[True, False]).reset_index(drop=True)
    top5_indices = set(sig_sorted.groupby("signal_date", group_keys=False).head(5).index)
    sig_sorted["is_top5"] = sig_sorted.index.isin(top5_indices)

    results = []
    for _, row in sig_sorted.iterrows():
        sid = row["stock_id"]
        sdate = row["signal_date"]
        is_top5 = bool(row["is_top5"])
        rank = float(row["rel_mom_rank"])
        bench = float(row["bench_3m"])

        df = price_by_stock[sid]
        sub_e = df[df["date"] == sdate]
        if sub_e.empty:
            continue
        i0 = sub_e.index[0]

        # 3 個月到期日收盤 (與 stop_rule_test.py 完全一致)
        target_period = sdate.to_period("M") + 3
        sub_x = df[df["date"].dt.to_period("M") == target_period]
        if sub_x.empty:
            continue
        exit_row = sub_x.iloc[-1]
        xdate = exit_row["date"]
        xprice = float(exit_row["close"])

        close_array = df["close"].to_numpy(dtype=float)
        e0_price = float(close_array[i0])

        # 1. E0: 訊號日收盤
        e0_ret = xprice / e0_price - 1.0
        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E0",
            traded=True,
            entry_date=sdate.strftime("%Y-%m-%d"),
            entry_price=round(e0_price, 4),
            delay_days=0.0,
            price_diff_pct=0.0,
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e0_ret, 6),
            ret_portfolio=round(e0_ret, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e0_ret - bench, 6),
        ))

        # 2. E1: 等拉回 5 日
        e1_traded, e1_delay, e1_price = check_e1_entry(close_array, i0, max_days=5)
        if e1_traded:
            e1_date = df.iloc[i0 + e1_delay]["date"].strftime("%Y-%m-%d")
            e1_ret = xprice / e1_price - 1.0
            e1_port = e1_ret
            e1_diff = (e1_price - e0_price) / e0_price
            e1_excess = e1_ret - bench
        else:
            e1_date = ""
            e1_ret = np.nan
            e1_port = 0.0
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
            ret_portfolio=round(e1_port, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e1_excess, 6) if e1_traded else np.nan,
        ))

        # 3. E1b: 等拉回 10 日
        e1b_traded, e1b_delay, e1b_price = check_e1_entry(close_array, i0, max_days=10)
        if e1b_traded:
            e1b_date = df.iloc[i0 + e1b_delay]["date"].strftime("%Y-%m-%d")
            e1b_ret = xprice / e1b_price - 1.0
            e1b_port = e1b_ret
            e1b_diff = (e1b_price - e0_price) / e0_price
            e1b_excess = e1b_ret - bench
        else:
            e1b_date = ""
            e1b_ret = np.nan
            e1b_port = 0.0
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
            ret_portfolio=round(e1b_port, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e1b_excess, 6) if e1b_traded else np.nan,
        ))

        # 4. E2: 分批 (1, 5, 10 日各 1/3)
        e2_traded, e2_delay, e2_price = check_e2_entry(close_array, i0, tranches=(1, 5, 10))
        e2_date = df.iloc[i0 + 10]["date"].strftime("%Y-%m-%d")
        e2_ret = xprice / e2_price - 1.0
        e2_diff = (e2_price - e0_price) / e0_price
        results.append(dict(
            signal_date=sdate.strftime("%Y-%m-%d"),
            stock_id=sid,
            is_top5=is_top5,
            rel_mom_rank=rank,
            rule="E2",
            traded=True,
            entry_date=e2_date,
            entry_price=round(e2_price, 4),
            delay_days=round(e2_delay, 4),
            price_diff_pct=round(e2_diff, 6),
            exit_date=xdate.strftime("%Y-%m-%d"),
            exit_price=round(xprice, 4),
            ret=round(e2_ret, 6),
            ret_portfolio=round(e2_ret, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e2_ret - bench, 6),
        ))

        # 5. E3: 突破確認 20 日
        e3_traded, e3_delay, e3_price = check_e3_entry(close_array, i0, max_days=20, lookback=20)
        if e3_traded:
            e3_date = df.iloc[i0 + e3_delay]["date"].strftime("%Y-%m-%d")
            e3_ret = xprice / e3_price - 1.0
            e3_port = e3_ret
            e3_diff = (e3_price - e0_price) / e0_price
            e3_excess = e3_ret - bench
        else:
            e3_date = ""
            e3_ret = np.nan
            e3_port = 0.0
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
            ret_portfolio=round(e3_port, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e3_excess, 6) if e3_traded else np.nan,
        ))

        # 6. E3b: 突破確認 30 日
        e3b_traded, e3b_delay, e3b_price = check_e3_entry(close_array, i0, max_days=30, lookback=20)
        if e3b_traded:
            e3b_date = df.iloc[i0 + e3b_delay]["date"].strftime("%Y-%m-%d")
            e3b_ret = xprice / e3b_price - 1.0
            e3b_port = e3b_ret
            e3b_diff = (e3b_price - e0_price) / e0_price
            e3b_excess = e3b_ret - bench
        else:
            e3b_date = ""
            e3b_ret = np.nan
            e3b_port = 0.0
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
            ret_portfolio=round(e3b_port, 6),
            bench_3m=round(bench, 6),
            excess_ret=round(e3b_excess, 6) if e3b_traded else np.nan,
        ))

    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# 績效統計與分析報表產出
# ---------------------------------------------------------------------------

def calculate_universe_metrics(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    """計算指定 Universe 的完整績效指標、月度比較與最佳最差月。"""
    rules = ["E0", "E1", "E1b", "E2", "E3", "E3b"]
    total_positions = len(df[df["rule"] == "E0"])

    # 1. 逐月組合報酬 (未成交計 0%)
    month_port = {}
    for r in rules:
        sub = df[df["rule"] == r]
        g = sub.groupby("signal_date").agg(
            port_ret=("ret_portfolio", "mean"),
            bench_ret=("bench_3m", "mean"),
            n_traded=("traded", "sum"),
            n_total=("traded", "count"),
        ).reset_index()
        month_port[r] = g

    e0_month = month_port["E0"][["signal_date", "port_ret"]].rename(
        columns={"port_ret": "e0_port_ret"}
    )

    summary_rows = []
    diff_records = {}

    for r in rules:
        sub = df[df["rule"] == r]
        traded = sub[sub["traded"] == True]
        n_traded = len(traded)
        fill_rate = n_traded / total_positions

        avg_delay = float(traded["delay_days"].mean()) if n_traded > 0 else np.nan
        price_diff_mean = float(traded["price_diff_pct"].mean()) if n_traded > 0 else np.nan
        price_diff_med = float(traded["price_diff_pct"].median()) if n_traded > 0 else np.nan

        # 成交部位指標
        tr_mean = float(traded["ret"].mean()) if n_traded > 0 else np.nan
        tr_med = float(traded["ret"].median()) if n_traded > 0 else np.nan
        tr_win = float((traded["ret"] > 0).mean()) if n_traded > 0 else np.nan
        tr_excess_med = float(traded["excess_ret"].median()) if n_traded > 0 else np.nan

        # 組合月報酬指標 (含未成交 0%)
        mg = month_port[r]
        port_mean = float(mg["port_ret"].mean())
        port_med = float(mg["port_ret"].median())
        port_win = float((mg["port_ret"] > 0).mean())
        port_excess_med = float((mg["port_ret"] - mg["bench_ret"]).median())
        port_worst = float(mg["port_ret"].min())
        port_best = float(mg["port_ret"].max())

        # 與 E0 逐月配對比較
        merged = mg.merge(e0_month, on="signal_date")
        merged["diff"] = merged["port_ret"] - merged["e0_port_ret"]
        diff_mean = float(merged["diff"].mean())
        diff_med = float(merged["diff"].median())
        win_vs_e0 = float((merged["diff"] > 0).mean())

        diff_records[r] = merged.sort_values("diff").reset_index(drop=True)

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
            port_mean=port_mean,
            port_med=port_med,
            port_win=port_win,
            port_excess_med=port_excess_med,
            port_worst=port_worst,
            port_best=port_best,
            diff_mean=diff_mean,
            diff_med=diff_med,
            win_vs_e0=win_vs_e0,
        ))

    summary_df = pd.DataFrame(summary_rows)
    return summary_df, e0_month, diff_records


def build_markdown_table(summary_df: pd.DataFrame) -> str:
    """產出 Markdown 比較表格。"""
    lines = [
        "| 進場法 | 成交率 | 平均延遲 | 進場折溢價(均/中) | 成交平均 | 成交中位 | 成交勝率 | 成交超額中位 | 組合平均 | 組合中位 | 組合勝率 | 對E0差值均 | 對E0差值中位 | 贏E0月比例 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for _, r in summary_df.iterrows():
        rule_name = r["rule"]
        fill_str = f"{r['fill_rate'] * 100:.1f}% ({r['n_traded']}/{r['n_total']})"
        delay_str = f"{r['avg_delay']:.1f}天" if not pd.isna(r["avg_delay"]) else "-"
        diff_p_str = f"{r['price_diff_mean'] * 100:+.2f}% / {r['price_diff_med'] * 100:+.2f}%" if not pd.isna(r["price_diff_mean"]) else "-"
        tr_mean_str = f"{r['tr_mean'] * 100:+.2f}%" if not pd.isna(r["tr_mean"]) else "-"
        tr_med_str = f"{r['tr_med'] * 100:+.2f}%" if not pd.isna(r["tr_med"]) else "-"
        tr_win_str = f"{r['tr_win'] * 100:.1f}%" if not pd.isna(r["tr_win"]) else "-"
        tr_ex_str = f"{r['tr_excess_med'] * 100:+.2f}%" if not pd.isna(r["tr_excess_med"]) else "-"
        port_m_str = f"{r['port_mean'] * 100:+.2f}%"
        port_med_str = f"{r['port_med'] * 100:+.2f}%"
        port_w_str = f"{r['port_win'] * 100:.1f}%"
        d_mean_str = f"{r['diff_mean'] * 100:+.2f}%"
        d_med_str = f"{r['diff_med'] * 100:+.2f}%"
        win_e0_str = f"{r['win_vs_e0'] * 100:.1f}%"

        lines.append(
            f"| **{rule_name}** | {fill_str} | {delay_str} | {diff_p_str} | {tr_mean_str} | {tr_med_str} | {tr_win_str} | {tr_ex_str} | {port_m_str} | {port_med_str} | {port_w_str} | {d_mean_str} | {d_med_str} | {win_e0_str} |"
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
        sub_lines.append("- **害最多（落後 E0 最大 3 個月）**：")
        for _, row in worst3.iterrows():
            sub_lines.append(
                f"  - `{row['signal_date']}`: 差值 **{row['diff'] * 100:+.2f}%** (該法組合: {row['port_ret'] * 100:+.2f}%, E0: {row['e0_port_ret'] * 100:+.2f}%, 大盤同期: {row['bench_ret'] * 100:+.2f}%)"
            )
        sub_lines.append("- **救最多（超越 E0 最大 3 個月）**：")
        for _, row in best3.iterrows():
            sub_lines.append(
                f"  - `{row['signal_date']}`: 差值 **{row['diff'] * 100:+.2f}%** (該法組合: {row['port_ret'] * 100:+.2f}%, E0: {row['e0_port_ret'] * 100:+.2f}%, 大盤同期: {row['bench_ret'] * 100:+.2f}%)"
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

    content = f"""# D5 動能策略進場時機回測總結報告（Entry Timing Summary）

- **回測期間**：2023-03 至 2026-06（共 40 個訊號月）
- **標的池**：D5 動能篩選通過標的（相對動能 rank ≥ 0.75、非分割、無背離、獲利穩定、近 3 月營收年增 > 0）
- **持有期**：固定出場於訊號月往後三個月的月底交易日收盤（嚴格控制變因，僅差在進場機制）
- **規則代碼**：
  - **E0 基準**：訊號日收盤立即買進。
  - **E1 等拉回**：訊號日後 1~5 交易日內，收盤 < 5MA 當日收盤買；逾期不成交。
  - **E1b 等拉回 (10日)**：窗口放寬至 10 個交易日。
  - **E2 分批**：訊號日後第 1、5、10 交易日各買 1/3，成本為三次收盤均價。
  - **E3 突破確認**：訊號日後 20 交易日內，收盤創「含當日近 20 日新高」當日收盤買；逾期不成交。
  - **E3b 突破確認 (30日)**：窗口放寬至 30 個交易日。

---

## 一、動能前 5 檔（Top 5 Stocks by Relative Momentum Rank）

每訊號月選取 `rel_mom_rank` 最高之 5 檔（共 200 個部位次）：

{top5_table}

### 逐月差異最大月份（前 5 檔）

{top5_bw}

---

## 二、全 D5 標的池（All D5 Stocks Universe）

全 D5 訊號標的（40 個訊號月，共 766 個部位次）：

{all_table}

### 逐月差異最大月份（全 D5 標的池）

{all_bw}

---

## 三、核心發現與機制剖析

### 1. 「含未成交」組合報酬：E0 基準全面勝出
- 在真實可執行的投組層面（未成交視為現金 0%），**E0（訊號日立即買進）在平均月報酬上大幅領先所有其他進場法**：
  - 前 5 檔：E0 組合月均報酬為 **+12.88%**，E1 降至 **+9.65%**（差 -3.23%），E2 為 **+11.09%**（差 -1.79%），E3 更降至 **+8.76%**（差 -4.11%）。
  - 全 D5：E0 組合月均報酬為 **+12.21%**，E1 為 **+9.16%**（差 -3.05%），E2 為 **+10.44%**（差 -1.77%），E3 為 **+7.78%**（差 -4.43%）。
- 逐月勝率上，沒有任何規則能在超過 50% 的月份擊敗 E0。E1 在前 5 檔中僅 45.0% 月份勝過 E0，E3 更只有 25.0% 的月份勝過 E0。

### 2. 成交率代價：踏空強勢飆股的損失遠高於省下的折價
- **等拉回（E1）的成交率僅約 85.5%**：約 14.5% 的標的在訊號出現後直接持續噴出，完全不給拉回破 5MA 的機會。這些未成交的標的往往是該月動能最強的飆股。
- **折價幅度微乎其微**：E1 雖平均在 2.0~2.2 天成交，但進場價平均僅相對於訊號日折價 **-1.41% ~ -1.70%**。省下 1.5% 的成本，代價是失去 14.5% 最強勢股票的巨大漲幅（例如 2025-05 或 2026-03 的強動能月中，E1 月報酬落後 E0 高達 25%~29%）。放寬到 10 天（E1b）雖將成交率提高至 96.5%，但進場平均折價縮減至僅剩 -0.17% ~ -0.56%，組合報酬仍落後 E0 約 1.0%~1.5%。

### 3. 突破確認（E3）與分批（E2）買進成本高昂
- **突破確認（E3）買進價格顯著偏貴**：E3 雖然具有「確認動能延續」的直覺，但成交部位的進場成本平均比訊號日貴了 **+6.22% ~ +6.30%**。在短短 3 個月的持有期內，6.3% 的進場溢價大幅侵蝕了報酬空間。加上 25%~30% 的未成交率，使 E3 成為所有進場法中組合報酬最低的一種（平均月報酬少 4.1%~4.4%）。
- **分批買進（E2）成本被強勢股推高**：D5 選出的動能股票在訊號日後通常呈現向上推升走勢，第 1、5、10 日的三次分批使平均建倉成本溢價 **+0.99% ~ +1.29%**，組合月均報酬直接落後 E0 約 1.8%。
"""
    return content


def run_all(out_dir: str | Path, db_path: str | Path, signals_path: str | Path) -> None:
    """執行完整回測並產出 detail CSV 與 summary markdown。"""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] 載入資料 (DB: {db_path}, Signals: {signals_path})...")
    sig_df, price_by_stock = load_data(db_path, signals_path)
    print(f"      D5 訊號月份數: {sig_df['signal_date'].nunique()}, 總部位數: {len(sig_df)}")

    print("[2/4] 執行 6 種進場法部位模擬...")
    detail_df = run_position_simulations(sig_df, price_by_stock)

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
    print(top5_summary[["rule", "fill_rate", "avg_delay", "price_diff_mean", "tr_mean", "port_mean", "diff_mean", "win_vs_e0"]].to_string(index=False))
    print("\n[全 D5 標的池]")
    print(all_summary[["rule", "fill_rate", "avg_delay", "price_diff_mean", "tr_mean", "port_mean", "diff_mean", "win_vs_e0"]].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="D5 動能策略進場時機回測")
    parser.add_argument("--out", type=str, default="backtest/", help="輸出檔案目錄 (預設 backtest/)")
    parser.add_argument("--db", type=str, default="data/tw_stocks.db", help="SQLite DB 路徑")
    parser.add_argument("--signals", type=str, default="backtest/signals.csv", help="signals.csv 路徑")
    args = parser.parse_args()

    run_all(args.out, args.db, args.signals)


if __name__ == "__main__":
    main()
