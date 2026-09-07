import sqlite3
import pandas as pd
import numpy as np

DB = "C:/CLAUDE/專案-投資/tw-stock-db/data/tw_stocks.db"
SIG = "C:/CLAUDE/專案-投資/tw-stock-db/backtest/signals.csv"

con = sqlite3.connect(DB)
sig = pd.read_csv(SIG, encoding="utf-8")

# select signal months: D5==True and ret_3m not null
sig = sig[(sig["D5"] == True) & (sig["ret_3m"].notna())].copy()

# top 5 by rel_mom_rank per signal_date (assume higher rank = better; verify direction)
# rel_mom_rank likely 1 = best. We'll take the 5 lowest rank values (rank 1..5) as "highest" momentum rank position.
# To be safe, check both directions by looking at correlation with mom would need extra data; use ascending rank (rank 1 = top).
# rel_mom_rank observed range ~0.75-1.0 (percentile); higher = stronger relative momentum -> take top (largest)
sig_sorted = sig.sort_values(["signal_date", "rel_mom_rank"], ascending=[True, False])
groups = sig_sorted.groupby("signal_date", group_keys=False).head(5).reset_index(drop=True)

months = sorted(groups["signal_date"].unique())
print(f"# signal months: {len(months)}")

# preload price data for needed stock_ids
stock_ids = groups["stock_id"].astype(str).unique().tolist()
placeholders = ",".join("?" for _ in stock_ids)
prices = pd.read_sql(
    f"SELECT stock_id, date, close FROM fm_price_daily WHERE stock_id IN ({placeholders})",
    con, params=stock_ids
)
prices["stock_id"] = prices["stock_id"].astype(str)
prices["date"] = pd.to_datetime(prices["date"])
prices = prices.sort_values(["stock_id", "date"]).reset_index(drop=True)
price_by_stock = {sid: df.reset_index(drop=True) for sid, df in prices.groupby("stock_id")}

groups["stock_id"] = groups["stock_id"].astype(str)
groups["signal_date"] = pd.to_datetime(groups["signal_date"])

def get_series(stock_id, start_date, end_date=None):
    df = price_by_stock.get(stock_id)
    if df is None:
        return None
    mask = df["date"] >= start_date
    if end_date is not None:
        mask &= df["date"] <= end_date
    return df.loc[mask].reset_index(drop=True)

def nearest_close_on_or_after(stock_id, target_date, horizon_days=400):
    df = price_by_stock.get(stock_id)
    if df is None:
        return None, None
    sub = df[(df["date"] >= target_date)]
    if sub.empty:
        return None, None
    row = sub.iloc[0]
    return row["date"], row["close"]

def entry_close(stock_id, signal_date):
    df = price_by_stock.get(stock_id)
    if df is None:
        return None, None
    sub = df[df["date"] == signal_date]
    if not sub.empty:
        return sub.iloc[0]["date"], sub.iloc[0]["close"]
    # fallback: nearest on/after
    return nearest_close_on_or_after(stock_id, signal_date)

def exit_3m_close(stock_id, signal_date):
    # exit = last trading day of the month that is 3 calendar months after signal_date's month
    df = price_by_stock.get(stock_id)
    if df is None:
        return None, None
    target_period = (signal_date.to_period("M") + 3)
    sub = df[df["date"].dt.to_period("M") == target_period]
    if sub.empty:
        return None, None
    row = sub.iloc[-1]
    return row["date"], row["close"]

# ---- Step 1: verify baseline hold matches ret_3m ----
verify_rows = []
positions = []  # list of dict per position: signal_date, stock_id, entry_date, entry_price, series (full to +3m), bench_3m, ret_3m(reported)
for _, row in groups.iterrows():
    sid = row["stock_id"]
    sdate = row["signal_date"]
    edate, eprice = entry_close(sid, sdate)
    if edate is None or eprice is None or eprice == 0:
        continue
    xdate, xprice = exit_3m_close(sid, sdate)
    if xdate is None:
        continue
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

    if rule == "none":
        exit_idx = n - 1
        ret = trade_days.iloc[exit_idx]["close"] / entry_price - 1.0
        return dict(ret=ret, exited_early=False, hold_days=n - 1, exit_date=trade_days.iloc[exit_idx]["date"])

    # compute MA using full 'path' (which has lookback for pre-entry days), aligned by date
    path = path.sort_values("date").reset_index(drop=True)
    path["ma20"] = path["close"].rolling(20, min_periods=20).mean()
    path["ma60"] = path["close"].rolling(60, min_periods=60).mean()

    # map trade_days indices into path indices
    path_idx_of_date = {d: i for i, d in enumerate(path["date"])}

    below20_streak = 0
    below60_streak = 0
    running_max_close = entry_price
    exit_i = None  # index within trade_days (0=entry day)

    for i in range(n):
        d = trade_days.iloc[i]["date"]
        c = trade_days.iloc[i]["close"]
        pidx = path_idx_of_date[d]

        if i == 0:
            # entry day: no check, but initialize running max
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
            exit_i = i
            break

    if exit_i is None:
        exit_i = n - 1
        exited_early = False
    else:
        exited_early = True

    exit_price = trade_days.iloc[exit_i]["close"]
    ret_to_exit = exit_price / entry_price - 1.0

    if exited_early:
        # remaining period = cash (0 return) -> total position return over full 3m = ret_to_exit
        ret = ret_to_exit
    else:
        ret = ret_to_exit

    return dict(ret=ret, exited_early=exited_early, hold_days=exit_i, exit_date=trade_days.iloc[exit_i]["date"])


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
        bench_ret=("bench_3m", "mean"),
        n_exit_early=("exited_early", "sum"),
        avg_hold=("hold_days", "mean"),
    ).reset_index()
    month_stats[r] = g

# ---- Summary table ----
summary_rows = []
for r in rules:
    g = month_stats[r].copy()
    port = g["port_ret"]
    bench = g["bench_ret"]
    excess = port - bench

    summary_rows.append(dict(
        rule=r,
        n_months=len(g),
        mean_ret=port.mean(),
        median_ret=port.median(),
        winrate_abs=(port > 0).mean(),
        winrate_vs_bench=(port > bench).mean(),
        median_excess=excess.median(),
        worst=port.min(),
        best=port.max(),
        p5=port.quantile(0.05),
        p95=port.quantile(0.95),
        avg_early_exit_count=g["n_exit_early"].mean() if r != "none" else 0.0,
        avg_hold_days=g["avg_hold"].mean(),
    ))

summary_df = pd.DataFrame(summary_rows)

# ---- 回吐保留度 ----
# Use "none" (baseline) month returns to classify months as bench-negative / bench-positive
base = month_stats["none"][["signal_date", "bench_ret", "port_ret"]].rename(columns={"port_ret": "base_port_ret"})

retention_rows = []
for r in rules:
    if r == "none":
        continue
    g = month_stats[r][["signal_date", "port_ret"]].rename(columns={"port_ret": "rule_port_ret"})
    m = base.merge(g, on="signal_date")
    neg_months = m[m["bench_ret"] < 0]
    pos_months = m[m["bench_ret"] > 0]
    retention_rows.append(dict(
        rule=r,
        n_neg_months=len(neg_months),
        base_avg_in_neg=neg_months["base_port_ret"].mean(),
        rule_avg_in_neg=neg_months["rule_port_ret"].mean(),
        n_pos_months=len(pos_months),
        base_avg_in_pos=pos_months["base_port_ret"].mean(),
        rule_avg_in_pos=pos_months["rule_port_ret"].mean(),
        pos_month_giveup=(pos_months["base_port_ret"] - pos_months["rule_port_ret"]).mean(),
    ))

retention_df = pd.DataFrame(retention_rows)

# ---- R1 per-month diff vs baseline ----
r1_g = month_stats["R1"][["signal_date", "port_ret"]].rename(columns={"port_ret": "r1_ret"})
base_g = month_stats["none"][["signal_date", "port_ret", "bench_ret"]].rename(columns={"port_ret": "base_ret"})
r1_compare = base_g.merge(r1_g, on="signal_date")
r1_compare["diff"] = r1_compare["r1_ret"] - r1_compare["base_ret"]
r1_compare_sorted = r1_compare.sort_values("diff")

worst5 = r1_compare_sorted.head(5)  # R1 hurts most (most negative diff)
best5 = r1_compare_sorted.tail(5).sort_values("diff", ascending=False)  # R1 saves most

# ---- Print everything ----
pd.set_option("display.float_format", lambda x: f"{x:.4f}")
pd.set_option("display.width", 200)

print("\n===== 規則比較總表 =====")
print(summary_df.to_string(index=False))

print("\n===== 回吐保留度（基準持有為負 / 為正月份）=====")
print(retention_df.to_string(index=False))

print("\n===== R1 vs 基準：差異最大 5 個月（R1 救最多，diff 由負到正取前5為害最多；後5為救最多）=====")
print("\n-- R1 害最多的 5 個月 (R1 - 基準 最負) --")
print(worst5[["signal_date", "base_ret", "r1_ret", "diff", "bench_ret"]].to_string(index=False))
print("\n-- R1 救最多的 5 個月 (R1 - 基準 最正) --")
print(best5[["signal_date", "base_ret", "r1_ret", "diff", "bench_ret"]].to_string(index=False))

# save csv outputs for reference
out_dir = "C:/Users/chang/AppData/Local/Temp/claude/C--CLAUDE/7a997c70-86d6-4e1a-9a5b-82831f164b00/scratchpad"
summary_df.to_csv(f"{out_dir}/stop_rule_summary.csv", index=False)
retention_df.to_csv(f"{out_dir}/stop_rule_retention.csv", index=False)
r1_compare.to_csv(f"{out_dir}/stop_rule_R1_vs_base.csv", index=False)

print("\n輸出檔案已存至 scratchpad。")
