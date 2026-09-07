from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "tw_stocks.db"
SIG = Path(__file__).resolve().parent / "signals.csv"
OUT_DIR = Path(__file__).resolve().parent

con = sqlite3.connect(DB)
sig = pd.read_csv(SIG, encoding="utf-8")

# select signal months: D5==True and ret_3m not null
sig = sig[(sig["D5"] == True) & (sig["ret_3m"].notna())].copy()
sig["stock_id"] = sig["stock_id"].astype(str).str.zfill(4)
sig["signal_date"] = pd.to_datetime(sig["signal_date"])

# top 5 by rel_mom_rank per signal_date
sig_sorted = sig.sort_values(["signal_date", "rel_mom_rank"], ascending=[True, False])
groups = sig_sorted.groupby("signal_date", group_keys=False).head(5).reset_index(drop=True)

months = sorted(groups["signal_date"].unique())
print(f"# signal months: {len(months)}")

# preload price data for needed stock_ids from fm_price_adj_daily
stock_ids = groups["stock_id"].astype(str).unique().tolist()
placeholders = ",".join("?" for _ in stock_ids)
prices = pd.read_sql(
    f"SELECT stock_id, date, close_adj AS close FROM fm_price_adj_daily WHERE stock_id IN ({placeholders}) ORDER BY stock_id, date",
    con, params=stock_ids
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

def get_univ_return(d_from: pd.Timestamp, d_to: pd.Timestamp) -> float:
    if d_from == d_to:
        return 0.0
    c_from = univ_cum_map.get(d_from)
    c_to = univ_cum_map.get(d_to)
    if c_from and c_to and c_from > 0:
        return float(c_to / c_from - 1.0)
    return 0.0

groups["stock_id"] = groups["stock_id"].astype(str).str.zfill(4)
groups["signal_date"] = pd.to_datetime(groups["signal_date"])

# ---- Step 1: verify baseline hold matches ret_3m (T+1 entry and exit) ----
positions = []
for _, row in groups.iterrows():
    sid = row["stock_id"]
    sdate = row["signal_date"]
    df = price_by_stock.get(sid)
    if df is None:
        continue
    sub_e = df[df["date"] == sdate]
    if sub_e.empty:
        continue
    i0 = sub_e.index[0]
    if i0 + 1 >= len(df):
        continue
    edate = df.iloc[i0 + 1]["date"]
    eprice = float(df.iloc[i0 + 1]["close"])

    target_period = sdate.to_period("M") + 3
    sub_x = df[df["date"].dt.to_period("M") == target_period]
    if sub_x.empty:
        continue
    mx_idx = sub_x.index[-1]
    x_idx = mx_idx + 1 if (mx_idx + 1 < len(df)) else mx_idx
    xdate = df.iloc[x_idx]["date"]
    xprice = float(df.iloc[x_idx]["close"])
    computed_ret = xprice / eprice - 1.0

    positions.append(dict(
        signal_date=sdate, stock_id=sid, entry_date=edate, entry_price=eprice,
        exit3m_date=xdate, exit3m_price=xprice, computed_ret3m=computed_ret,
        reported_ret3m=row["ret_3m"], bench_3m=row["bench_3m"]
    ))

pos_df = pd.DataFrame(positions)
pos_df["diff"] = pos_df["computed_ret3m"] - pos_df["reported_ret3m"]
n_match = (pos_df["diff"].abs() < 0.01).sum()
print(f"\n[驗證] 基準持有計算 vs ret_3m 一致筆數: {n_match}/{len(pos_df)}, 平均誤差: {pos_df['diff'].abs().mean():.4f}, 最大誤差: {pos_df['diff'].abs().max():.4f}")

# ---- Step 2: build daily price path for each position up to entry+3m (and a bit beyond for MA lookback) ----
def full_path(stock_id, signal_date):
    df = price_by_stock.get(stock_id)
    if df is None:
        return None
    lookback_start = signal_date - pd.Timedelta(days=200)  # enough for 60MA
    end_date = signal_date + pd.DateOffset(months=3) + pd.Timedelta(days=15)
    sub = df[(df["date"] >= lookback_start) & (df["date"] <= end_date)].reset_index(drop=True)
    return sub

# ---- Apply exit rules ----
def simulate_rule(pos, rule):
    """
    pos: dict with signal_date, stock_id, entry_date, entry_price, exit3m_date
    rule: one of 'none','R1','R2','R3','R4','R5'
    returns dict: ret, exited_early(bool), hold_days
    """
    sid = pos["stock_id"]
    sdate = pos["signal_date"]
    path = full_path(sid, sdate)
    if path is None or path.empty:
        return None

    # trading day series from entry to exit3m
    trade_days = path[(path["date"] >= pos["entry_date"]) & (path["date"] <= pos["exit3m_date"])].reset_index(drop=True)
    if trade_days.empty:
        return None

    entry_price = pos["entry_price"]
    n = len(trade_days)
    maturity_date = trade_days.iloc[n - 1]["date"]

    if rule == "none":
        exit_idx = n - 1
        ret = trade_days.iloc[exit_idx]["close"] / entry_price - 1.0
        return dict(ret=ret, ret_cash=ret, ret_univ=ret, exited_early=False, hold_days=n - 1, exit_date=trade_days.iloc[exit_idx]["date"])

    # compute MA using full 'path' (which has lookback for pre-entry days), aligned by date
    path = path.sort_values("date").reset_index(drop=True)
    path["ma20"] = path["close"].rolling(20, min_periods=20).mean()
    path["ma60"] = path["close"].rolling(60, min_periods=60).mean()

    # map trade_days indices into path indices
    path_idx_of_date = {d: i for i, d in enumerate(path["date"])}

    below20_streak = 0
    below60_streak = 0
    running_max_close = entry_price
    trigger_i = None

    for i in range(n):
        d = trade_days.iloc[i]["date"]
        c = trade_days.iloc[i]["close"]
        pidx = path_idx_of_date[d]

        if i == 0:
            running_max_close = c
            continue

        running_max_close = max(running_max_close, c)

        ma20 = path.iloc[pidx]["ma20"]
        ma60 = path.iloc[pidx]["ma60"]

        below20 = (not pd.isna(ma20)) and (c < ma20)
        below60 = (not pd.isna(ma60)) and (c < ma60)

        below20_streak = below20_streak + 1 if below20 else 0
        below60_streak = below60_streak + 1 if below60 else 0

        triggered = False
        if rule == "R1" and below20_streak >= 2:
            triggered = True
        elif rule == "R2" and below20_streak >= 3:
            triggered = True
        elif rule == "R3" and below60_streak >= 2:
            triggered = True
        elif rule == "R4":
            if running_max_close > 0 and (c / running_max_close - 1.0) <= -0.15:
                triggered = True
        elif rule == "R5":
            if i >= 20 and below20_streak >= 2:
                triggered = True

        if triggered:
            trigger_i = i
            break

    if trigger_i is None:
        exit_i = n - 1
        exited_early = False
    else:
        # 收盤觸發、次日收盤成交（T+1）
        exit_i = min(trigger_i + 1, n - 1)
        exited_early = (exit_i < n - 1)

    exit_date = trade_days.iloc[exit_i]["date"]
    exit_price = trade_days.iloc[exit_i]["close"]
    stock_ret = exit_price / entry_price - 1.0

    if exited_early:
        ret_cash = stock_ret
        univ_reinvest = get_univ_return(exit_date, maturity_date)
        ret_univ = (exit_price / entry_price) * (1.0 + univ_reinvest) - 1.0
    else:
        ret_cash = stock_ret
        ret_univ = stock_ret

    return dict(
        ret=ret_cash,
        ret_cash=ret_cash,
        ret_univ=ret_univ,
        exited_early=exited_early,
        hold_days=exit_i,
        exit_date=exit_date,
    )


rules = ["none", "R1", "R2", "R3", "R4", "R5"]
sim_results = {r: [] for r in rules}

for _, pos in pos_df.iterrows():
    posd = pos.to_dict()
    for r in rules:
        res = simulate_rule(posd, r)
        if res is None:
            continue
        res.update(signal_date=posd["signal_date"], stock_id=posd["stock_id"],
                    bench_3m=posd["bench_3m"], entry_price=posd["entry_price"])
        sim_results[r].append(res)

sim_df = {r: pd.DataFrame(v) for r, v in sim_results.items()}

# ---- Aggregate per signal_date (month) = average of 5 stocks ----
month_stats = {}
for r in rules:
    df = sim_df[r]
    g = df.groupby("signal_date").agg(
        port_ret=("ret", "mean"),
        port_ret_cash=("ret_cash", "mean"),
        port_ret_univ=("ret_univ", "mean"),
        bench_ret=("bench_3m", "mean"),
        n_exit_early=("exited_early", "sum"),
        avg_hold=("hold_days", "mean"),
    ).reset_index()
    month_stats[r] = g

# ---- Summary table ----
summary_rows = []
for r in rules:
    g = month_stats[r].copy()
    port_cash = g["port_ret_cash"]
    port_univ = g["port_ret_univ"]
    bench = g["bench_ret"]
    excess_cash = port_cash - bench
    excess_univ = port_univ - bench

    summary_rows.append(dict(
        rule=r,
        n_months=len(g),
        mean_ret_cash=port_cash.mean(),
        median_ret_cash=port_cash.median(),
        mean_ret_univ=port_univ.mean(),
        median_ret_univ=port_univ.median(),
        winrate_abs=(port_cash > 0).mean(),
        winrate_vs_bench=(port_cash > bench).mean(),
        median_excess_cash=excess_cash.median(),
        median_excess_univ=excess_univ.median(),
        worst_cash=port_cash.min(),
        best_cash=port_cash.max(),
        worst_univ=port_univ.min(),
        best_univ=port_univ.max(),
        avg_early_exit_count=g["n_exit_early"].mean() if r != "none" else 0.0,
        avg_hold_days=g["avg_hold"].mean(),
    ))

summary_df = pd.DataFrame(summary_rows)

# ---- 回吐保留度 ----
base = month_stats["none"][["signal_date", "bench_ret", "port_ret_cash"]].rename(columns={"port_ret_cash": "base_port_ret"})

retention_rows = []
for r in rules:
    if r == "none":
        continue
    g = month_stats[r][["signal_date", "port_ret_cash", "port_ret_univ"]].rename(
        columns={"port_ret_cash": "rule_port_cash", "port_ret_univ": "rule_port_univ"}
    )
    m = base.merge(g, on="signal_date")
    neg_months = m[m["bench_ret"] < 0]
    pos_months = m[m["bench_ret"] > 0]
    retention_rows.append(dict(
        rule=r,
        n_neg_months=len(neg_months),
        base_avg_in_neg=neg_months["base_port_ret"].mean(),
        rule_cash_in_neg=neg_months["rule_port_cash"].mean(),
        rule_univ_in_neg=neg_months["rule_port_univ"].mean(),
        n_pos_months=len(pos_months),
        base_avg_in_pos=pos_months["base_port_ret"].mean(),
        rule_cash_in_pos=pos_months["rule_port_cash"].mean(),
        rule_univ_in_pos=pos_months["rule_port_univ"].mean(),
        pos_month_giveup_cash=(pos_months["base_port_ret"] - pos_months["rule_port_cash"]).mean(),
        pos_month_giveup_univ=(pos_months["base_port_ret"] - pos_months["rule_port_univ"]).mean(),
    ))

retention_df = pd.DataFrame(retention_rows)

# ---- R1 per-month diff vs baseline ----
r1_g = month_stats["R1"][["signal_date", "port_ret_cash", "port_ret_univ"]].rename(
    columns={"port_ret_cash": "r1_ret_cash", "port_ret_univ": "r1_ret_univ"}
)
base_g = month_stats["none"][["signal_date", "port_ret_cash", "bench_ret"]].rename(columns={"port_ret_cash": "base_ret"})
r1_compare = base_g.merge(r1_g, on="signal_date")
r1_compare["diff_cash"] = r1_compare["r1_ret_cash"] - r1_compare["base_ret"]
r1_compare["diff_univ"] = r1_compare["r1_ret_univ"] - r1_compare["base_ret"]
r1_compare_sorted = r1_compare.sort_values("diff_cash")

worst5 = r1_compare_sorted.head(5)
best5 = r1_compare_sorted.tail(5).sort_values("diff_cash", ascending=False)

# ---- Print everything ----
pd.set_option("display.float_format", lambda x: f"{x:.4f}")
pd.set_option("display.width", 200)

print("\n===== 規則比較總表（並列現金與 Universe 轉入口徑）=====")
print(summary_df[["rule", "n_months", "mean_ret_cash", "median_ret_cash", "mean_ret_univ", "median_ret_univ", "winrate_abs", "winrate_vs_bench", "avg_early_exit_count", "avg_hold_days"]].to_string(index=False))

print("\n===== 回吐保留度（基準持有為負 / 為正月份）=====")
print(retention_df.to_string(index=False))

print("\n===== R1 vs 基準：差異最大 5 個月（現金口徑，R1 - 基準）=====")
print("\n-- R1 害最多的 5 個月 (R1 - 基準 最負) --")
print(worst5[["signal_date", "base_ret", "r1_ret_cash", "r1_ret_univ", "diff_cash", "bench_ret"]].to_string(index=False))
print("\n-- R1 救最多的 5 個月 (R1 - 基準 最正) --")
print(best5[["signal_date", "base_ret", "r1_ret_cash", "r1_ret_univ", "diff_cash", "bench_ret"]].to_string(index=False))

# save csv outputs to backtest directory
OUT_DIR.mkdir(parents=True, exist_ok=True)
summary_df.to_csv(OUT_DIR / "stop_rule_summary.csv", index=False)
retention_df.to_csv(OUT_DIR / "stop_rule_retention.csv", index=False)
r1_compare.to_csv(OUT_DIR / "stop_rule_R1_vs_base.csv", index=False)

print(f"\n輸出檔案已存至 {OUT_DIR}。")
