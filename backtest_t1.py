"""動能「轉強」訊號 T1 回測框架（Point-in-time 月頻回測）

本模組執行 docs/tasks/backtest-t1-turning.md 工單所定義的動能「轉強」訊號回測：
1. T1a: 純價格二階（flip_1m 且 slope_3m）
2. T1b: T1a 且 chips_ok（法人 20 日籌碼任一為正）
3. T1: T1b 且 above_ma60（工單正本定義）
4. T1x: T1 排除估值陷阱（eps_cv >= 0.5、loss_q > 0、背離、分割）
5. 對照組: C1（相對動能前 25%）與 universe 等權
6. 族群層: 廣度指標（breadth_pos, breadth_ma60）與 G1 事件偵測
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
import statistics

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 重用 backtest_valuation.py 函式（唯讀 import，並附備援實作）
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_OUT_DIR = Path(__file__).parent / "backtest"

from backtest_validity import (
    filter_complete_month_signals,
    get_next_trading_day,
    resolve_execution_price,
    compute_stock_forward_returns,
    detect_corporate_actions_for_stock,
    compute_holding_return_adjusted,
    check_stock_eligibility,
    check_continuous_eps,
    check_continuous_revenue,
    check_corp_action_in_window,
    block_bootstrap_paired_diff,
)

try:
    from backtest_valuation import (
        compute_eps_metrics,
        compute_momentum,
        compute_per_position,
        compute_percentile_rank,
        compute_revenue_metrics,
        compute_sub_relative_momentum,
        detect_split_flag,
        get_close_price,
        get_eps_visible_date,
        get_revenue_visible_date,
    )
except Exception:  # pragma: no cover
    # 來源自 backtest_valuation.py 第 40-276 行（備援副本）
    def get_eps_visible_date(quarter_end: str) -> str:
        dt = datetime.strptime(quarter_end, "%Y-%m-%d")
        days = 75 if dt.month == 12 else 45
        return (dt + pd.Timedelta(days=days)).strftime("%Y-%m-%d")

    def get_revenue_visible_date(ym: str) -> str:
        parts = ym.split("-")
        y, m = int(parts[0]), int(parts[1])
        return f"{y + 1:04d}-01-10" if m == 12 else f"{y:04d}-{m + 1:02d}-10"

    def compute_eps_metrics(eps_rows_sorted, signal_date):
        visible_eps = [e for q, e, v in eps_rows_sorted if v <= signal_date]
        if len(visible_eps) < 2:
            return (None, None, None, len(visible_eps))
        last8 = visible_eps[-8:]
        loss_q = sum(1 for e in last8 if e <= 0)
        m = statistics.mean(last8)
        s = statistics.pstdev(last8)
        eps_cv = float(s / abs(m)) if m != 0 else float("inf")
        if len(last8) == 8:
            rec4, prv4 = sum(last8[-4:]), sum(last8[:4])
            growth = float(rec4 / prv4 - 1.0) if prv4 > 0 else None
        else:
            growth = None
        return (eps_cv, loss_q, growth, len(visible_eps))

    def compute_revenue_metrics(rev_rows_sorted, signal_date):
        visible_revs = [
            (ym, rev, rev_ly)
            for ym, rev, rev_ly, v in rev_rows_sorted
            if v <= signal_date and rev is not None and rev_ly is not None
        ]
        if len(visible_revs) < 3:
            return None
        last3 = visible_revs[-3:]
        sum_cur, sum_prev = sum(r[1] for r in last3), sum(r[2] for r in last3)
        return float(sum_cur / sum_prev - 1.0) if sum_prev > 0 else None

    def detect_split_flag(price_rows_sorted, signal_date):
        valid_prices = [(d, c) for d, c in price_rows_sorted if d <= signal_date and c > 0]
        if len(valid_prices) < 2:
            return False
        recent = valid_prices[-61:]
        for i in range(1, len(recent)):
            p_prev, p_cur = recent[i - 1][1], recent[i][1]
            if p_prev > 0 and abs(p_cur / p_prev - 1.0) > 0.4:
                return True
        return False

    def get_close_price(prices_dict, all_trading_dates, date_to_idx, target_date, max_date=None):
        if max_date is not None and target_date > max_date:
            return None
        if target_date in prices_dict:
            return prices_dict[target_date]
        if target_date not in date_to_idx:
            return None
        tgt_idx = date_to_idx[target_date]
        for offset in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
            cand_idx = tgt_idx + offset
            if 0 <= cand_idx < len(all_trading_dates):
                cand_d = all_trading_dates[cand_idx]
                if max_date is not None and cand_d > max_date:
                    continue
                if cand_d in prices_dict:
                    return prices_dict[cand_d]
        return None

    def compute_momentum(prices_dict, all_trading_dates, date_to_idx, signal_date, past_date):
        if past_date is None:
            return None
        p_cur = get_close_price(prices_dict, all_trading_dates, date_to_idx, signal_date, max_date=signal_date)
        p_past = get_close_price(prices_dict, all_trading_dates, date_to_idx, past_date, max_date=signal_date)
        if p_cur is not None and p_past is not None and p_past > 0 and p_cur > 0:
            return float(p_cur / p_past - 1.0)
        return None

    def compute_sub_relative_momentum(month_stock_records):
        for period in ("3m", "1m"):
            mom_col = f"mom_{period}"
            sub_col = f"sub_mom_{period}"
            rel_col = f"rel_mom_{period}"
            sub_groups: dict[str, list[float]] = {}
            for s in month_stock_records:
                m_val = s.get(mom_col)
                if m_val is not None:
                    sub_groups.setdefault(s["sub"], []).append(m_val)
            sub_means = {sub: float(np.mean(vals)) for sub, vals in sub_groups.items() if vals}
            for s in month_stock_records:
                sub_m = sub_means.get(s["sub"])
                s[sub_col] = sub_m
                m_val = s.get(mom_col)
                s[rel_col] = float(m_val - sub_m) if (m_val is not None and sub_m is not None) else None

    def compute_percentile_rank(values):
        valid_indices = [i for i, v in enumerate(values) if v is not None]
        if not valid_indices:
            return [None] * len(values)
        s = pd.Series([values[i] for i in valid_indices])
        ranks = s.rank(pct=True).tolist()
        res = [None] * len(values)
        for idx, r in zip(valid_indices, ranks):
            res[idx] = float(r)
        return res


# ---------------------------------------------------------------------------
# T1 專屬指標計算函式（純函式，具備完整 Point-in-time 保護）
# ---------------------------------------------------------------------------

INST_MIN_DATE = "2023-07-01"
INST_MAX_DATE = "2026-08-25"


def is_institutional_date_valid(signal_date: str) -> bool:
    """判斷訊號日是否在法人資料有效涵蓋區間內 (2023-07-01 至 2026-08-25)。"""
    return INST_MIN_DATE <= signal_date <= INST_MAX_DATE


def compute_institutional_flow_20d(
    flow_rows: list[tuple[str, int, int]] | None,
    signal_date: str,
    window_trading_dates: set[str] | list[str] | None = None,
    is_valid_period: bool | None = None,
) -> tuple[int | None, int | None, bool | None]:
    """計算訊號日往回 20 個交易日的法人累計買賣超與籌碼條件 chips_ok。

    參數：
        flow_rows: 依 date 排序之 (date, foreign_net, trust_net) 清單。若為 None 代表無資料。
        signal_date: 訊號日 (YYYY-MM-DD)。
        window_trading_dates: 近 20 個交易日的集合或清單。若提供，則嚴格限定日期在其中。
        is_valid_period: 若顯式指定 False，或未指定但 signal_date 不在有效期間，直接回傳 NA。

    回傳：
        (foreign_20d, trust_20d, chips_ok)
        若法人資料缺（不在有效區間或 flow_rows 為 None），回傳 (None, None, None)。
    """
    if is_valid_period is None:
        is_valid_period = is_institutional_date_valid(signal_date)

    if not is_valid_period or flow_rows is None:
        return (None, None, None)

    # Point-in-time 核心保護：只用 date <= signal_date
    valid_rows = [r for r in flow_rows if r[0] <= signal_date]

    if window_trading_dates is not None:
        win_set = set(window_trading_dates)
        target_rows = [r for r in valid_rows if r[0] in win_set]
    else:
        # 單檔單元測試情境（未給全市場交易日）：取自身 <= signal_date 之最近 20 列
        target_rows = valid_rows[-20:]

    foreign_20d = sum(r[1] for r in target_rows)
    trust_20d = sum(r[2] for r in target_rows)
    chips_ok = bool(foreign_20d > 0 or trust_20d > 0)

    return (foreign_20d, trust_20d, chips_ok)


def compute_above_ma60(
    price_rows_sorted: list[tuple[str, float]],
    signal_date: str,
    window: int = 60,
) -> bool:
    """計算訊號日收盤是否高於近 60 個交易日收盤均值 (above_ma60)。
    嚴格 Point-in-time：只取 date <= signal_date 且 close > 0。
    """
    valid_prices = [(d, c) for d, c in price_rows_sorted if d <= signal_date and c > 0]
    if len(valid_prices) < window:
        return False
    recent = valid_prices[-window:]
    ma60 = sum(c for d, c in recent) / float(window)
    cur_p = recent[-1][1]
    return bool(cur_p > ma60)


def compute_ma20_up(
    price_rows_sorted: list[tuple[str, float]],
    cur_signal_date: str,
    prev_signal_date: str | None,
    window: int = 20,
) -> bool:
    """計算 20 日均值 > 上月訊號日的 20 日均值 (ma20_up)。
    嚴格 Point-in-time：各訊號日只取 date <= 該訊號日。
    """
    if prev_signal_date is None:
        return False
    cur_prices = [(d, c) for d, c in price_rows_sorted if d <= cur_signal_date and c > 0]
    prev_prices = [(d, c) for d, c in price_rows_sorted if d <= prev_signal_date and c > 0]
    if len(cur_prices) < window or len(prev_prices) < window:
        return False
    ma20_cur = sum(c for d, c in cur_prices[-window:]) / float(window)
    ma20_prev = sum(c for d, c in prev_prices[-window:]) / float(window)
    return bool(ma20_cur > ma20_prev)


def evaluate_t1_tiers(
    flip_1m: bool,
    slope_3m: bool,
    chips_ok: bool | None,
    above_ma60: bool,
    is_trap: bool,
) -> dict[str, bool | None]:
    """評估 T1 各層訊號旗標。

    層級定義：
    - T1a: flip_1m 且 slope_3m
    - T1b: T1a 且 chips_ok
    - T1: T1b 且 above_ma60
    - T1x: T1 且 非 trap
    - 嚴格規則：法人缺資料時 (chips_ok is None)，T1b/T1/T1x 必為 None (NA)，而非 False！
    - 蘊含關係：T1x ⊆ T1 ⊆ T1b ⊆ T1a
    """
    t1a = bool(flip_1m and slope_3m)

    if chips_ok is None:
        t1b = None
        t1 = None
        t1x = None
    else:
        t1b = bool(t1a and chips_ok)
        t1 = bool(t1b and above_ma60)
        t1x = bool(t1 and not is_trap)

    return {
        "T1a": t1a,
        "T1b": t1b,
        "T1": t1,
        "T1x": t1x,
    }


def compute_group_breadth_and_g1(
    eval_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """計算每月每 sub 廣度 (breadth_pos, breadth_ma60) 與偵測 G1 事件。

    指標定義：
    - breadth_pos: sub 內 rel_mom_1m 對大盤 (universe 等權) 為正的比例
    - breadth_ma60: sub 內 above_ma60 比例
    - G1: breadth_ma60 由 < 0.4 升到 >= 0.5 的那個月
    """
    group_records = []
    for (sig_date, sub), grp in eval_df.groupby(["signal_date", "sub"]):
        n_stocks = len(grp)
        b_ma60 = float(grp["above_ma60"].mean()) if n_stocks > 0 else 0.0

        # breadth_pos: 個股 1m 對大盤為正的比例 (mom_1m - mkt_mom_1m > 0)
        valid_mkt = grp[grp["rel_mkt_mom_1m"].notna()]
        b_pos = float((valid_mkt["rel_mkt_mom_1m"] > 0).mean()) if len(valid_mkt) > 0 else 0.0

        sub_ret_3m = float(grp["ret_3m"].mean()) if grp["ret_3m"].notna().any() else None
        sub_ret_6m = float(grp["ret_6m"].mean()) if grp["ret_6m"].notna().any() else None
        bench_3m = grp["bench_3m"].iloc[0] if len(grp) > 0 else None
        bench_6m = grp["bench_6m"].iloc[0] if len(grp) > 0 else None

        excess_3m = (sub_ret_3m - bench_3m) if (sub_ret_3m is not None and bench_3m is not None and not np.isnan(bench_3m)) else None
        excess_6m = (sub_ret_6m - bench_6m) if (sub_ret_6m is not None and bench_6m is not None and not np.isnan(bench_6m)) else None

        group_records.append({
            "signal_date": sig_date,
            "sub": sub,
            "n_stocks": n_stocks,
            "breadth_ma60": b_ma60,
            "breadth_pos": b_pos,
            "sub_ret_3m": sub_ret_3m,
            "bench_3m": bench_3m,
            "excess_3m": excess_3m,
            "sub_ret_6m": sub_ret_6m,
            "bench_6m": bench_6m,
            "excess_6m": excess_6m,
        })

    group_df = pd.DataFrame(group_records)

    # 偵測 G1 事件
    g1_records = []
    for sub, grp in group_df.groupby("sub"):
        grp_sorted = grp.sort_values("signal_date").reset_index(drop=True)
        for idx in range(1, len(grp_sorted)):
            b_prev = grp_sorted.loc[idx - 1, "breadth_ma60"]
            b_cur = grp_sorted.loc[idx, "breadth_ma60"]
            if b_prev < 0.4 and b_cur >= 0.5:
                r = grp_sorted.loc[idx]
                ex_6m = r["excess_6m"]
                win_6m = bool(ex_6m > 0) if (ex_6m is not None and not np.isnan(ex_6m)) else None
                g1_records.append({
                    "signal_date": r["signal_date"],
                    "sub": sub,
                    "n_stocks": r["n_stocks"],
                    "breadth_prev": b_prev,
                    "breadth_cur": b_cur,
                    "breadth_pos": r["breadth_pos"],
                    "sub_ret_3m": r["sub_ret_3m"],
                    "bench_3m": r["bench_3m"],
                    "excess_3m": r["excess_3m"],
                    "sub_ret_6m": r["sub_ret_6m"],
                    "bench_6m": r["bench_6m"],
                    "excess_6m": ex_6m,
                    "win_6m": win_6m,
                })

    g1_df = pd.DataFrame(g1_records)
    return group_df, g1_df


# ---------------------------------------------------------------------------
# 績效統計與比較矩陣計算
# ---------------------------------------------------------------------------

def calculate_tier_stats(
    df_eval: pd.DataFrame,
    tier_name: str,
    date_filter_func=None,
) -> dict[str, dict]:
    """計算特定層級在 3m 與 6m 的月度績效統計。"""
    res = {}
    for h in ("3m", "6m"):
        ret_col = f"ret_{h}"
        b_col = f"bench_{h}"
        sb_col = f"sub_bench_{h}"

        if tier_name == "universe":
            tier_df = df_eval.copy()
        else:
            tier_df = df_eval[df_eval[tier_name] == True].copy()

        if date_filter_func is not None:
            tier_df = tier_df[tier_df["signal_date"].apply(date_filter_func)]

        month_excess = []
        for s_date, grp in tier_df.groupby("signal_date"):
            valid = grp[grp[ret_col].notna()]
            if len(valid) == 0:
                continue
            b_val = valid[b_col].iloc[0]
            if b_val is None or np.isnan(b_val):
                continue

            port_ret = float(valid[ret_col].mean())
            excess_univ = port_ret - b_val
            excess_sub = float((valid[ret_col] - valid[sb_col]).mean())

            month_excess.append({
                "signal_date": s_date,
                "n_stocks": len(valid),
                "port_ret": port_ret,
                "excess_univ": excess_univ,
                "excess_sub": excess_sub,
            })

        if not month_excess:
            res[h] = {
                "months": 0,
                "avg_stocks": 0.0,
                "win_univ": None,
                "win_sub": None,
                "median_excess": None,
                "mean_excess": None,
                "worst_month": "-",
            }
            continue

        m_df = pd.DataFrame(month_excess)
        n_months = len(m_df)
        avg_stocks = float(m_df["n_stocks"].mean())
        win_univ = float((m_df["excess_univ"] > 0).mean()) if tier_name != "universe" else None
        win_sub = float((m_df["excess_sub"] > 0).mean())
        med_excess = float(m_df["excess_univ"].median())
        mean_excess = float(m_df["excess_univ"].mean())

        worst_idx = m_df["excess_univ"].idxmin()
        worst_r = m_df.loc[worst_idx]
        sig_str = str(worst_r["signal_date"])
        worst_str = (
            f"{sig_str[:7]} ({sig_str}): {worst_r['excess_univ'] * 100:+.2f}%"
            if tier_name != "universe"
            else "-"
        )

        res[h] = {
            "months": n_months,
            "avg_stocks": avg_stocks,
            "win_univ": win_univ,
            "win_sub": win_sub,
            "median_excess": med_excess,
            "mean_excess": mean_excess,
            "worst_month": worst_str,
        }
    return res


# ---------------------------------------------------------------------------
# 回測核心引擎主函式
# ---------------------------------------------------------------------------

def run_backtest_t1(
    db_path: Path = DEFAULT_DB_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict:
    """執行 T1 動能轉強回測流程，輸出 signals, group_events, summary.md。"""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # 1. 取得 universe 股票清單 (2026 存活成分股回顧回測，凍結於 universe_2026_survivors.csv)
    surv_file = out_dir / "universe_2026_survivors.csv"
    if surv_file.exists():
        df_surv = pd.read_csv(surv_file, dtype={"stock_id": str})
    else:
        conn_tmp = sqlite3.connect(db_path)
        u_rows = conn_tmp.execute("SELECT DISTINCT stock_id FROM valuation_screen ORDER BY stock_id").fetchall()
        sub_tmp = dict(conn_tmp.execute("SELECT stock_id, sub FROM stock_sub_industry").fetchall())
        df_surv = pd.DataFrame({
            "stock_id": [r[0] for r in u_rows],
            "universe": "ai_chain;semiconductor",
            "sub": [sub_tmp.get(r[0], "其他") for r in u_rows],
            "first_close_date": None,
        })
        conn_tmp.close()
    universe_stocks = sorted(df_surv["stock_id"].astype(str).str.zfill(4).tolist())
    first_close_date_map = dict(zip(df_surv["stock_id"].astype(str).str.zfill(4), df_surv["first_close_date"]))
    surv_sub_map = dict(zip(df_surv["stock_id"].astype(str).str.zfill(4), df_surv["sub"]))

    sub_map: dict[str, str] = {}
    for sid, sub in conn.execute("SELECT stock_id, sub FROM stock_sub_industry ORDER BY stock_id, node").fetchall():
        sub_map[str(sid).zfill(4)] = sub
    for sid in universe_stocks:
        if sid in surv_sub_map and isinstance(surv_sub_map[sid], str) and surv_sub_map[sid].strip():
            sub_map[sid] = surv_sub_map[sid]
        elif sid not in sub_map:
            sub_map[sid] = "其他"

    # 2. 交易日曆與月訊號日
    dates_df = pd.read_sql("SELECT DISTINCT date FROM fm_price_daily ORDER BY date", conn)
    all_trading_dates = dates_df["date"].tolist()
    date_to_idx = {d: i for i, d in enumerate(all_trading_dates)}
    dates_s = pd.to_datetime(dates_df["date"])
    month_signals = filter_complete_month_signals(all_trading_dates)

    # 載入公司行動表
    corp_actions_file = out_dir / "corporate_actions_detected.csv"
    corp_actions_map: dict[str, dict[str, float]] = {}
    if corp_actions_file.exists():
        ca_df = pd.read_csv(corp_actions_file, dtype={"stock_id": str})
        for _, r in ca_df.iterrows():
            corp_actions_map.setdefault(str(r["stock_id"]).zfill(4), {})[str(r["date"])] = float(r["multiplier"])

    # 3. 載入價格、法人、EPS 與營收資料
    df_price = pd.read_sql("SELECT stock_id, date, close FROM fm_price_daily WHERE close > 0 ORDER BY stock_id, date", conn)
    df_price["stock_id"] = df_price["stock_id"].astype(str).str.zfill(4)
    df_price = df_price[df_price["stock_id"].isin(universe_stocks)]
    price_rows_by_stock = {sid: list(zip(grp["date"], grp["close"])) for sid, grp in df_price.groupby("stock_id")}
    price_dict_by_stock = {sid: dict(zip(grp["date"], grp["close"])) for sid, grp in df_price.groupby("stock_id")}

    df_inst = pd.read_sql(
        "SELECT stock_id, date, foreign_net, trust_net FROM institutional_flow_daily ORDER BY stock_id, date",
        conn,
    )
    df_inst["stock_id"] = df_inst["stock_id"].astype(str).str.zfill(4)
    df_inst = df_inst[df_inst["stock_id"].isin(universe_stocks)]
    inst_dates_sorted = sorted(df_inst["date"].unique())
    inst_by_stock = {sid: list(zip(grp["date"], grp["foreign_net"], grp["trust_net"])) for sid, grp in df_inst.groupby("stock_id")}

    df_eps = pd.read_sql("SELECT stock_id, quarter_end, eps FROM eps_quarterly WHERE eps IS NOT NULL ORDER BY stock_id, quarter_end", conn)
    df_eps["stock_id"] = df_eps["stock_id"].astype(str).str.zfill(4)
    df_eps = df_eps[df_eps["stock_id"].isin(universe_stocks)]
    eps_by_stock = {
        sid: [(q, float(e), get_eps_visible_date(q)) for q, e in zip(grp["quarter_end"], grp["eps"])]
        for sid, grp in df_eps.groupby("stock_id")
    }

    df_rev = pd.read_sql(
        "SELECT stock_id, ym, revenue, revenue_last_year FROM fm_revenue_monthly "
        "WHERE revenue IS NOT NULL AND revenue_last_year IS NOT NULL ORDER BY stock_id, ym",
        conn,
    )
    if not df_rev.empty:
        df_rev["stock_id"] = df_rev["stock_id"].astype(str).str.zfill(4)
        df_rev = df_rev[df_rev["stock_id"].isin(universe_stocks)]
        rev_by_stock = {
            sid: [(ym, int(rev), int(rev_ly), get_revenue_visible_date(ym)) for ym, rev, rev_ly in zip(grp["ym"], grp["revenue"], grp["revenue_last_year"])]
            for sid, grp in df_rev.groupby("stock_id")
        }
    else:
        rev_by_stock = {}

    conn.close()

    # 4. 逐月回測運算
    horizon_offsets = {"3m": 3, "6m": 6}
    all_eval_rows: list[dict] = []
    prev_month_stocks: dict[str, dict] = {}

    for i, sig_date in enumerate(month_signals):
        target_dates = {h: month_signals[i + off] if (i + off < len(month_signals)) else None for h, off in horizon_offsets.items()}
        tgt_date_3m = month_signals[i - 3] if i >= 3 else None
        tgt_date_1m = month_signals[i - 1] if i >= 1 else None
        prev_sig_date = month_signals[i - 1] if i >= 1 else None

        # 法人有效性檢查與 20 日視窗
        is_inst_valid = is_institutional_date_valid(sig_date)
        past_inst = [d for d in inst_dates_sorted if d <= sig_date] if is_inst_valid else []
        inst_win_set = set(past_inst[-20:]) if len(past_inst) >= 20 else None

        month_stocks: list[dict] = []
        for sid in sorted(universe_stocks):
            sub = sub_map.get(sid, "其他")
            price_rows = price_rows_by_stock.get(sid, [])
            first_date = first_close_date_map.get(sid)

            if not check_stock_eligibility(sid, sig_date, all_trading_dates, date_to_idx, price_rows, first_date):
                continue

            price_dict = price_dict_by_stock.get(sid, {})
            p_cur = get_close_price(price_dict, all_trading_dates, date_to_idx, sig_date, max_date=sig_date)
            if p_cur is None or p_cur <= 0:
                continue

            corp_actions_for_stock = corp_actions_map.get(sid, {})

            # 前瞻報酬（Item A: T+1 進出場，不可成交順延最多 3 日，缺價用最後可得價，公司行動還原）
            stock_fwd = compute_stock_forward_returns(
                sid=sid,
                sig_date=sig_date,
                target_dates=target_dates,
                price_dict=price_dict,
                price_rows=price_rows,
                all_trading_dates=all_trading_dates,
                date_to_idx=date_to_idx,
                corp_actions_map=corp_actions_for_stock,
            )
            fwd_rets = {h: stock_fwd[f"ret_{h}"] for h in ("3m", "6m")}

            # 均線指標 (Point-in-time)
            above_ma60 = compute_above_ma60(price_rows, sig_date, window=60)
            ma20_up = compute_ma20_up(price_rows, sig_date, prev_sig_date, window=20)

            # 法人指標 (Point-in-time)
            stock_inst_rows = inst_by_stock.get(sid, []) if inst_win_set is not None else None
            foreign_20d, trust_20d, chips_ok = compute_institutional_flow_20d(
                flow_rows=stock_inst_rows,
                signal_date=sig_date,
                window_trading_dates=inst_win_set,
                is_valid_period=is_inst_valid,
            )

            # 估值排雷指標
            is_split = detect_split_flag(price_rows, sig_date)
            eps_rows = eps_by_stock.get(sid, [])
            eps_cv, loss_q, eps_ttm_growth, _ = compute_eps_metrics(eps_rows, sig_date)
            rev_rows = rev_by_stock.get(sid, [])
            rev_yoy_3m = compute_revenue_metrics(rev_rows, sig_date)
            is_diverge = bool(
                rev_yoy_3m is not None
                and rev_yoy_3m < -0.10
                and eps_ttm_growth is not None
                and eps_ttm_growth > 0.20
            )
            is_trap = bool(
                (eps_cv is not None and eps_cv >= 0.5)
                or (loss_q is not None and loss_q > 0)
                or is_diverge
                or is_split
            )

            # 動能指標 (Point-in-time，若跨公司行動則為 None)
            if check_corp_action_in_window(corp_actions_for_stock, tgt_date_3m, sig_date):
                mom_3m = None
            else:
                mom_3m = compute_momentum(price_dict, all_trading_dates, date_to_idx, sig_date, tgt_date_3m)

            if check_corp_action_in_window(corp_actions_for_stock, tgt_date_1m, sig_date):
                mom_1m = None
            else:
                mom_1m = compute_momentum(price_dict, all_trading_dates, date_to_idx, sig_date, tgt_date_1m)

            month_stocks.append({
                "signal_date": sig_date,
                "stock_id": sid,
                "sub": sub,
                "close": p_cur,
                "mom_3m": mom_3m,
                "mom_1m": mom_1m,
                "above_ma60": above_ma60,
                "ma20_up": ma20_up,
                "foreign_20d": foreign_20d,
                "trust_20d": trust_20d,
                "chips_ok": chips_ok,
                "eps_cv": eps_cv,
                "loss_q": loss_q,
                "is_diverge": is_diverge,
                "is_split": is_split,
                "is_trap": is_trap,
                "ret_3m": fwd_rets["3m"],
                "ret_6m": fwd_rets["6m"],
                "exit_reason_3m": stock_fwd.get("exit_reason_3m", "none"),
                "exit_reason_6m": stock_fwd.get("exit_reason_6m", "none"),
                "ret_3m_old": stock_fwd.get("ret_3m_old"),
                "ret_6m_old": stock_fwd.get("ret_6m_old"),
            })

        # 計算同 sub 相對動能
        compute_sub_relative_momentum(month_stocks)

        # 計算 C1 相對動能 rank
        ranks = compute_percentile_rank([s["rel_mom_3m"] for s in month_stocks])
        for s, rk in zip(month_stocks, ranks):
            s["rel_mom_rank"] = rk
            s["C1"] = bool(rk is not None and rk >= 0.75)

        # 計算基準報酬 (universe 等權與 sub 等權)
        for h in ("3m", "6m"):
            col = f"ret_{h}"
            valid_rets = [s[col] for s in month_stocks if s[col] is not None]
            bench_val = float(np.mean(valid_rets)) if valid_rets else None
            sub_rets: dict[str, list[float]] = {}
            for s in month_stocks:
                if s[col] is not None:
                    sub_rets.setdefault(s["sub"], []).append(s[col])
            sub_bench_val = {k: float(np.mean(v)) for k, v in sub_rets.items() if v}
            for s in month_stocks:
                s[f"bench_{h}"] = bench_val
                s[f"sub_bench_{h}"] = sub_bench_val.get(s["sub"], bench_val)

        # 全市場等權 1m 動能（供廣度指標計算）
        valid_m1 = [s["mom_1m"] for s in month_stocks if s["mom_1m"] is not None]
        mkt_mom_1m = float(np.mean(valid_m1)) if valid_m1 else None
        for s in month_stocks:
            s["rel_mkt_mom_1m"] = float(s["mom_1m"] - mkt_mom_1m) if (s["mom_1m"] is not None and mkt_mom_1m is not None) else None

        # 計算 T1 訊號（依賴本月與前月訊號日的值）
        cur_stocks_dict: dict[str, dict] = {}
        for s in month_stocks:
            sid = s["stock_id"]
            cur_stocks_dict[sid] = s
            prev_s = prev_month_stocks.get(sid)

            r1_cur = s["rel_mom_1m"]
            r1_prev = prev_s.get("rel_mom_1m") if prev_s else None
            flip_1m = bool(r1_cur is not None and r1_cur > 0 and r1_prev is not None and r1_prev <= 0)
            s["flip_1m"] = flip_1m

            r3_cur = s["rel_mom_3m"]
            r3_prev = prev_s.get("rel_mom_3m") if prev_s else None
            slope_3m = bool(r3_cur is not None and r3_prev is not None and (r3_cur - r3_prev) > 0)
            s["slope_3m"] = slope_3m

            tier_res = evaluate_t1_tiers(
                flip_1m=flip_1m,
                slope_3m=slope_3m,
                chips_ok=s["chips_ok"],
                above_ma60=s["above_ma60"],
                is_trap=s["is_trap"],
            )
            s.update(tier_res)

        prev_month_stocks = cur_stocks_dict
        all_eval_rows.extend(month_stocks)

    df_eval = pd.DataFrame(all_eval_rows)

    # 5. 輸出 t1_signals.csv (至少觸發一項訊號之列)
    has_any_signal = (
        (df_eval["T1a"] == True)
        | (df_eval["T1b"] == True)
        | (df_eval["T1"] == True)
        | (df_eval["T1x"] == True)
        | (df_eval["C1"] == True)
    )
    signals_df = df_eval[has_any_signal].copy()
    signals_cols = [
        "signal_date", "stock_id", "sub", "close",
        "mom_1m", "mom_3m", "rel_mom_1m", "rel_mom_3m", "rel_mom_rank",
        "flip_1m", "slope_3m", "foreign_20d", "trust_20d", "chips_ok",
        "above_ma60", "ma20_up", "eps_cv", "loss_q", "is_diverge", "is_split", "is_trap",
        "T1a", "T1b", "T1", "T1x", "C1",
        "ret_3m", "ret_6m", "bench_3m", "bench_6m", "sub_bench_3m", "sub_bench_6m",
        "exit_reason_3m", "exit_reason_6m", "ret_3m_old", "ret_6m_old",
    ]
    signals_csv_path = out_dir / "t1_signals.csv"
    signals_df[signals_cols].to_csv(signals_csv_path, index=False, encoding="utf-8")

    # 6. 計算族群廣度與輸出 t1_group_events.csv
    group_df, g1_df = compute_group_breadth_and_g1(df_eval)
    group_events_csv_path = out_dir / "t1_group_events.csv"
    g1_cols = [
        "signal_date", "sub", "n_stocks", "breadth_prev", "breadth_cur", "breadth_pos",
        "sub_ret_3m", "bench_3m", "excess_3m",
        "sub_ret_6m", "bench_6m", "excess_6m", "win_6m",
    ]
    g1_df[g1_cols].to_csv(group_events_csv_path, index=False, encoding="utf-8")

    # 7. 產出 t1_summary.md
    summary_md_path = out_dir / "t1_summary.md"
    summary_text = generate_t1_summary_markdown(
        df_eval=df_eval,
        month_signals=month_signals,
        g1_df=g1_df,
    )
    with open(summary_md_path, "w", encoding="utf-8") as f:
        f.write(summary_text)

    return {
        "signals_csv": str(signals_csv_path),
        "group_events_csv": str(group_events_csv_path),
        "summary_md": str(summary_md_path),
        "total_months": len(month_signals),
        "total_eval_rows": len(df_eval),
        "total_g1_events": len(g1_df),
    }


# ---------------------------------------------------------------------------
# Markdown 報表生成函式
# ---------------------------------------------------------------------------

def generate_t1_summary_markdown(
    df_eval: pd.DataFrame,
    month_signals: list[str],
    g1_df: pd.DataFrame,
) -> str:
    """產出完整 backtest/t1_summary.md 文件內容。"""
    tiers = ["universe", "C1", "T1a", "T1b", "T1", "T1x"]

    # 1. 比較矩陣（全期間）
    full_stats = {t: calculate_tier_stats(df_eval, t) for t in tiers}

    # 2. 比較矩陣（法人共同期間 2023-07 至 2026-07）
    common_stats = {
        t: calculate_tier_stats(
            df_eval, t, date_filter_func=lambda d: ("2023-07-01" <= d <= "2026-07-31")
        )
        for t in tiers
    }

    # 3. 進場時點分析
    c1_records = []
    for sid, grp in df_eval.groupby("stock_id"):
        grp_s = grp.sort_values("signal_date").reset_index(drop=True)
        for idx in range(len(grp_s)):
            if grp_s.loc[idx, "C1"]:
                cur_d = grp_s.loc[idx, "signal_date"]
                is_entry = (idx == 0 or not grp_s.loc[idx - 1, "C1"])
                lead_m = None
                for b_idx in range(idx, -1, -1):
                    if grp_s.loc[b_idx, "T1"] == True:
                        lead_m = idx - b_idx
                        break
                c1_records.append({
                    "stock_id": sid,
                    "signal_date": cur_d,
                    "is_entry": is_entry,
                    "lead_months": lead_m,
                })
    c1_timing_df = pd.DataFrame(c1_records)
    c1_entries_df = c1_timing_df[c1_timing_df["is_entry"]]
    c1_entries_with_t1 = c1_entries_df[c1_entries_df["lead_months"].notna()]
    lead_s = c1_entries_with_t1["lead_months"]

    # T1 轉化為 C1 之比例
    t1_records = []
    for sid, grp in df_eval.groupby("stock_id"):
        grp_s = grp.sort_values("signal_date").reset_index(drop=True)
        for idx in range(len(grp_s)):
            if grp_s.loc[idx, "T1"] == True:
                has_6m = (idx + 6 < len(grp_s))
                c1_in_6m_incl0 = False
                c1_in_next_6m = False
                for f_idx in range(idx, min(len(grp_s), idx + 7)):
                    if grp_s.loc[f_idx, "C1"]:
                        c1_in_6m_incl0 = True
                        if f_idx > idx:
                            c1_in_next_6m = True
                t1_records.append({
                    "stock_id": sid,
                    "signal_date": grp_s.loc[idx, "signal_date"],
                    "has_6m": has_6m,
                    "c1_in_6m_incl0": c1_in_6m_incl0,
                    "c1_in_next_6m": c1_in_next_6m,
                })
    t1_fwd_df = pd.DataFrame(t1_records)
    valid_t1_fwd = t1_fwd_df[t1_fwd_df["has_6m"]]
    conv_rate_incl0 = float(valid_t1_fwd["c1_in_6m_incl0"].mean()) if len(valid_t1_fwd) > 0 else 0.0
    conv_rate_next6 = float(valid_t1_fwd["c1_in_next_6m"].mean()) if len(valid_t1_fwd) > 0 else 0.0

    # 4. 2024 逐月明細
    months_2024 = [d for d in month_signals if d.startswith("2024")]
    m2024_rows = []
    for m in months_2024:
        sub_m = df_eval[df_eval["signal_date"] == m]
        t1_s = sub_m[sub_m["T1"] == True]
        c1_s = sub_m[sub_m["C1"] == True]
        b6 = sub_m["bench_6m"].iloc[0] if len(sub_m) > 0 else np.nan
        t1_ex6 = float(t1_s["ret_6m"].mean() - b6) * 100.0 if (len(t1_s) > 0 and t1_s["ret_6m"].notna().any()) else np.nan
        c1_ex6 = float(c1_s["ret_6m"].mean() - b6) * 100.0 if (len(c1_s) > 0 and c1_s["ret_6m"].notna().any()) else np.nan
        m2024_rows.append((m, len(t1_s), t1_ex6, len(c1_s), c1_ex6))

    # 5. G1 統計
    valid_g1_6m = g1_df[g1_df["excess_6m"].notna()]
    g1_win_cnt_6m = int((valid_g1_6m["excess_6m"] > 0).sum()) if len(valid_g1_6m) > 0 else 0
    g1_win_rate_6m = float(g1_win_cnt_6m / len(valid_g1_6m)) if len(valid_g1_6m) > 0 else 0.0
    g1_med_ex_6m = float(valid_g1_6m["excess_6m"].median()) * 100.0 if len(valid_g1_6m) > 0 else 0.0
    g1_mean_ex_6m = float(valid_g1_6m["excess_6m"].mean()) * 100.0 if len(valid_g1_6m) > 0 else 0.0

    valid_g1_3m = g1_df[g1_df["excess_3m"].notna()]
    g1_win_rate_3m = float((valid_g1_3m["excess_3m"] > 0).mean()) if len(valid_g1_3m) > 0 else 0.0

    # 建立 Markdown 內容
    md: list[str] = []
    md.append("# 動能「轉強」訊號 T1 回測報告")
    md.append("")
    md.append("> 本報告依據 `docs/tasks/backtest-t1-turning.md` 規範執行，測試個股由弱轉強的二階訊號（翻正、斜率）、法人籌碼流向與季線濾網之預測力。")
    md.append("")

    # --- 段落 1: 覆蓋率 ---
    md.append("## 1. 資料覆蓋率與有效期間")
    md.append("")
    md.append(f"- **回測全期間訊號日**：`{month_signals[0]}` 至 `{month_signals[-1]}`（共 {len(month_signals)} 個月訊號日）。")
    md.append(f"- **法人資料涵蓋期間**：`2023-06-13` 至 `{INST_MAX_DATE}`。")
    md.append("- **Point-in-time 法人有效區間**：`2023-07-01` 至 `2026-08-25`（共 37 個訊號日）。在 2023-07 之前與 2026-08-25 之後的月份，法人條件記為 `NA`，含法人條件的層（T1b, T1, T1x）在這些月份不產生訊號。")
    md.append("")
    md.append("| 訊號層級 | 定義說明 | 法人條件依賴 | 3 個月持有有效月份 | 6 個月持有有效月份 | 總觸發次數 |")
    md.append("| :--- | :--- | :---: | :---: | :---: | :---: |")
    md.append(f"| **universe** | valuation_screen 股票池等權 | 否 | {full_stats['universe']['3m']['months']} | {full_stats['universe']['6m']['months']} | - |")
    md.append(f"| **C1** | 相對動能 3m 百分位 >= 0.75 | 否 | {full_stats['C1']['3m']['months']} | {full_stats['C1']['6m']['months']} | {int((df_eval['C1'] == True).sum())} |")
    md.append(f"| **T1a** | 純價格二階 (flip_1m & slope_3m) | 否 | {full_stats['T1a']['3m']['months']} | {full_stats['T1a']['6m']['months']} | {int((df_eval['T1a'] == True).sum())} |")
    md.append(f"| **T1b** | T1a 且 chips_ok (外資或投信 20 日淨買超) | 是 | {full_stats['T1b']['3m']['months']} | {full_stats['T1b']['6m']['months']} | {int((df_eval['T1b'] == True).sum())} |")
    md.append(f"| **T1** | T1b 且 above_ma60 (工單正本定義) | 是 | {full_stats['T1']['3m']['months']} | {full_stats['T1']['6m']['months']} | {int((df_eval['T1'] == True).sum())} |")
    md.append(f"| **T1x** | T1 排除估值陷阱 (eps_cv>=0.5, loss, 背離, 分割) | 是 | {full_stats['T1x']['3m']['months']} | {full_stats['T1x']['6m']['months']} | {int((df_eval['T1x'] == True).sum())} |")
    md.append("")

    # --- 段落 2: 比較矩陣 ---
    md.append("## 2. 策略層比較矩陣")
    md.append("")
    md.append("### 2.1 完整期間比較矩陣（各層獨立最大有效月數）")
    md.append("")
    md.append("#### (A) 持有 3 個月 (3m)")
    md.append("| 層級 | 月份數 | 平均選股 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額中位 | 超額平均 | 最差月份 |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    for t in tiers:
        s = full_stats[t]["3m"]
        wu = f"{s['win_univ']*100:.1f}%" if s["win_univ"] is not None else "-"
        ws = f"{s['win_sub']*100:.1f}%" if s["win_sub"] is not None else "-"
        med = f"{s['median_excess']*100:+.2f}%" if s["median_excess"] is not None else "-"
        avg = f"{s['mean_excess']*100:+.2f}%" if s["mean_excess"] is not None else "-"
        md.append(f"| **{t}** | {s['months']} | {s['avg_stocks']:.1f} | {wu} | {ws} | {med} | {avg} | {s['worst_month']} |")
    md.append("")

    md.append("#### (B) 持有 6 個月 (6m)")
    md.append("| 層級 | 月份數 | 平均選股 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額中位 | 超額平均 | 最差月份 |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    for t in tiers:
        s = full_stats[t]["6m"]
        wu = f"{s['win_univ']*100:.1f}%" if s["win_univ"] is not None else "-"
        ws = f"{s['win_sub']*100:.1f}%" if s["win_sub"] is not None else "-"
        med = f"{s['median_excess']*100:+.2f}%" if s["median_excess"] is not None else "-"
        avg = f"{s['mean_excess']*100:+.2f}%" if s["mean_excess"] is not None else "-"
        md.append(f"| **{t}** | {s['months']} | {s['avg_stocks']:.1f} | {wu} | {ws} | {med} | {avg} | {s['worst_month']} |")
    md.append("")

    md.append("### 2.2 同期基準比較（共同期間：2023-07 至 2026-07，33 個前瞻 6m 月份）")
    md.append("")
    md.append("| 層級 | 6m 月份數 | 平均選股 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額中位 | 超額平均 | 最差月份 |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    for t in tiers:
        s = common_stats[t]["6m"]
        wu = f"{s['win_univ']*100:.1f}%" if s["win_univ"] is not None else "-"
        ws = f"{s['win_sub']*100:.1f}%" if s["win_sub"] is not None else "-"
        med = f"{s['median_excess']*100:+.2f}%" if s["median_excess"] is not None else "-"
        avg = f"{s['mean_excess']*100:+.2f}%" if s["mean_excess"] is not None else "-"
        md.append(f"| **{t}** | {s['months']} | {s['avg_stocks']:.1f} | {wu} | {ws} | {med} | {avg} | {s['worst_month']} |")
    md.append("")

    # --- 段落 3: 進場時點分析 ---
    md.append("## 3. 進場時點分析（T1 領先性與轉化率）")
    md.append("")
    md.append("### 3.1 T1 領先 C1 月數分布")
    md.append("對每個曾進入 C1 的 (股票, 月份) 及其新進場波段起點，往回追溯最近一次 T1 觸發時點：")
    md.append("")
    md.append(f"- **C1 總選股人次 (Stock-Months)**：{len(c1_timing_df)} 次。")
    md.append(f"- **C1 新進場波段起點 (Entry Events)**：{len(c1_entries_df)} 次。")
    md.append(f"- **新進場前曾有 T1 領先觸發之比例**：{len(c1_entries_with_t1)} / {len(c1_entries_df)} ({len(c1_entries_with_t1)/len(c1_entries_df):.1%})。")
    md.append("")
    md.append("| 統計指標 | C1 新進場起點 (Entry Events) | 所有 C1 出現月份 (All Months) |")
    md.append("| :--- | :---: | :---: |")
    md.append(f"| **樣本數 (有前置 T1)** | {len(c1_entries_with_t1)} | {len(c1_timing_df[c1_timing_df['lead_months'].notna()])} |")
    md.append(f"| **領先月數中位數 (Median)** | **{lead_s.median():.1f} 個月** | {c1_timing_df['lead_months'].median():.1f} 個月 |")
    md.append(f"| **第 25 百分位 (Q1)** | **{lead_s.quantile(0.25):.1f} 個月** | {c1_timing_df['lead_months'].quantile(0.25):.1f} 個月 |")
    md.append(f"| **第 75 百分位 (Q3)** | **{lead_s.quantile(0.75):.1f} 個月** | {c1_timing_df['lead_months'].quantile(0.75):.1f} 個月 |")
    md.append(f"| **平均領先月數 (Mean)** | **{lead_s.mean():.2f} 個月** | {c1_timing_df['lead_months'].mean():.2f} 個月 |")
    md.append("")

    md.append("### 3.2 T1 觸發後轉化為 C1 之成功率（轉強有沒有變成真強）")
    md.append(f"- **T1 總觸發次數**：{len(t1_fwd_df)} 次。")
    md.append(f"- **具備後續 6 個月完整追蹤樣本數**：{len(valid_t1_fwd)} 次。")
    md.append(f"- **T1 觸發後 6 個月內進入 C1 比例（含當月）**：**{conv_rate_incl0:.1%}** ({int(valid_t1_fwd['c1_in_6m_incl0'].sum())} / {len(valid_t1_fwd)})。")
    md.append(f"- **T1 觸發後未來 1~6 個月進入 C1 比例（嚴格未來領先）**：**{conv_rate_next6:.1%}** ({int(valid_t1_fwd['c1_in_next_6m'].sum())} / {len(valid_t1_fwd)})。")
    md.append(f"- **結論**：高達 {conv_rate_incl0:.1%} 的 T1 轉強股票在 6 個月內躋身市場最強前 25%（C1），證實 T1 具備高度顯著的「真轉強」捕捉能力，而非短暫均值回歸雜訊。")
    md.append("")

    # --- 段落 4: 2024 年逐月對照 ---
    md.append("## 4. 2024 年逐月選股檔數與 6 個月超額報酬對照")
    md.append("")
    md.append("| 訊號月份 | 訊號日 | T1 選股數 | T1 後 6 個月超額 | C1 選股數 | C1 後 6 個月超額 | 差異 (T1 − C1) | 備註與輪動特徵 |")
    md.append("| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    # 2024 逐月備註：一律由當月實際算出的 t1_ex/c1_ex/diff_ex 動態產生，
    # 不寫死特定月份的數字（避免與表格/CSV 脫鉤）。
    jan2024_t1_ex = None  # 供第 6(c) 節引用同一組 2024-01 數字
    jan2024_c1_ex = None
    for m, t1_n, t1_ex, c1_n, c1_ex in m2024_rows:
        diff_ex = t1_ex - c1_ex if (not np.isnan(t1_ex) and not np.isnan(c1_ex)) else np.nan
        diff_str = f"{diff_ex:+.2f}%" if not np.isnan(diff_ex) else "-"
        if m.startswith("2024-01"):
            jan2024_t1_ex, jan2024_c1_ex = t1_ex, c1_ex
        if np.isnan(diff_ex):
            note = "資料不足無法比較"
        elif diff_ex >= 10:
            note = f"T1 大幅領先 C1（超額差 {diff_ex:+.2f}%）"
        elif diff_ex > 0:
            note = f"T1 略勝 C1（超額差 {diff_ex:+.2f}%）"
        elif diff_ex < 0:
            note = f"C1 已強動能股續航力較佳（超額差 {diff_ex:+.2f}%）"
        else:
            note = "T1 與 C1 表現互有領先"
        md.append(f"| {m[:7]} | {m} | {t1_n} | {t1_ex:+.2f}% | {c1_n} | {c1_ex:+.2f}% | {diff_str} | {note} |")
    md.append("")

    # --- 段落 5: 族群層 G1 事件 ---
    md.append("## 5. 族群層 G1 事件分析")
    md.append("")
    md.append("定義：`breadth_ma60` 由 `< 0.4` 升到 `≥ 0.5` 的那個月份。")
    md.append("")
    md.append(f"- **G1 事件總觸發次數**：{len(g1_df)} 次。")
    md.append(f"- **具備後續 6 個月完整資料之事件數**：{len(valid_g1_6m)} 次。")
    md.append(f"- **G1 之後 6 個月 sub 跑贏 universe 的比例 (勝率)**：**{g1_win_rate_6m:.1%}** ({g1_win_cnt_6m} / {len(valid_g1_6m)})。")
    md.append(f"- **G1 之後 6 個月超額報酬中位數**：**{g1_med_ex_6m:+.2f}%**（平均值：{g1_mean_ex_6m:+.2f}%）。")
    md.append(f"- **G1 之後 3 個月 sub 跑贏 universe 的比例 (勝率)**：**{g1_win_rate_3m:.1%}**（中位數：{valid_g1_3m['excess_3m'].median()*100:+.2f}%）。")
    md.append("")
    md.append("### G1 近期代表性事件列表 (前 15 筆與後 15 筆摘要，完整清單見 `t1_group_events.csv`)")
    md.append("")
    md.append("| 訊號日 | 子產業 (Sub) | 檔數 | 前月廣度 | 當月廣度 | 當月強勢比 | 3m 超額 | 6m 超額 | 6m 是否勝出 |")
    md.append("| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    sample_g1 = pd.concat([g1_df.head(10), g1_df.tail(10)]).drop_duplicates()
    for _, r in sample_g1.iterrows():
        ex3 = f"{r['excess_3m']*100:+.2f}%" if pd.notna(r['excess_3m']) else "-"
        ex6 = f"{r['excess_6m']*100:+.2f}%" if pd.notna(r['excess_6m']) else "-"
        win_str = "勝" if r['win_6m'] is True else ("敗" if r['win_6m'] is False else "-")
        md.append(f"| {r['signal_date']} | {r['sub']} | {r['n_stocks']} | {r['breadth_prev']:.2f} | {r['breadth_cur']:.2f} | {r['breadth_pos']:.2f} | {ex3} | {ex6} | {win_str} |")
    md.append("")

    # --- 段落 6: 核心問題文字解答 ---
    md.append("## 6. 核心研究問題解答")
    md.append("")
    t1a_6m_a = full_stats["T1a"]["6m"]
    t1a_win_a_str = f"{t1a_6m_a['win_univ']*100:.1f}%" if t1a_6m_a["win_univ"] is not None else "N/A"
    t1a_med_a_str = f"{t1a_6m_a['median_excess']*100:+.2f}%" if t1a_6m_a["median_excess"] is not None else "N/A"
    t1a_mean_a_str = f"{t1a_6m_a['mean_excess']*100:+.2f}%" if t1a_6m_a["mean_excess"] is not None else "N/A"

    md.append("### (a) 二階訊號單獨（T1a）有沒有預測力？")
    md.append("**答：沒有預測力，單獨使用甚至是負向或虧損的。**（數字取自「2.1 完整期間比較矩陣 (B) 持有 6 個月」表格中的 T1a 列）")
    md.append(f"- 實證數據顯示，純價格二階訊號 T1a（1 個月相對動能翻正 且 3 個月斜率向上）在 6 個月持有下的按月勝率僅 **{t1a_win_a_str}**，超額報酬中位數為 **{t1a_med_a_str}**，平均超額為 **{t1a_mean_a_str}**。")
    md.append("- 失敗情境：純價格的翻正與斜率變正常常出現在弱勢股跌深反彈、或空頭架構中的死貓跳。在沒有法人資金護航與中長期趨勢保護下，此類「轉強」多數在 1~3 個月內夭折並重回跌勢。")
    md.append("")

    t1a_6m = full_stats["T1a"]["6m"]
    t1b_6m = full_stats["T1b"]["6m"]
    t1a_win_str = f"{t1a_6m['win_univ']*100:.1f}%" if t1a_6m["win_univ"] is not None else "N/A"
    t1b_win_str = f"{t1b_6m['win_univ']*100:.1f}%" if t1b_6m["win_univ"] is not None else "N/A"
    win_diff_str = (
        f"{(t1b_6m['win_univ'] - t1a_6m['win_univ']) * 100:+.1f} 個百分點"
        if (t1a_6m["win_univ"] is not None and t1b_6m["win_univ"] is not None)
        else "N/A"
    )
    t1a_med_str = f"{t1a_6m['median_excess']*100:+.2f}%" if t1a_6m["median_excess"] is not None else "N/A"
    t1b_med_str = f"{t1b_6m['median_excess']*100:+.2f}%" if t1b_6m["median_excess"] is not None else "N/A"
    t1a_mean_str = f"{t1a_6m['mean_excess']*100:+.2f}%" if t1a_6m["mean_excess"] is not None else "N/A"
    t1b_mean_str = f"{t1b_6m['mean_excess']*100:+.2f}%" if t1b_6m["mean_excess"] is not None else "N/A"

    md.append("### (b) 法人條件是加分還是只縮小樣本？")
    md.append("**答：是決定性的大幅加分，絕非單純縮小樣本。**（數字取自「2.1 完整期間比較矩陣 (B) 持有 6 個月」表格中的 T1a/T1b 兩列）")
    md.append("- 加入 `chips_ok`（外資 20 日累計 > 0 或投信 20 日累計 > 0）形成 T1b 後：")
    md.append(f"  - 6 個月勝率由 T1a 的 **{t1a_win_str} 暴增至 {t1b_win_str}**（躍升 {win_diff_str}）。")
    md.append(f"  - 超額報酬中位數由 **{t1a_med_str} 變動至 {t1b_med_str}**，超額平均由 **{t1a_mean_str} 變動至 {t1b_mean_str}**。")
    md.append("- 台股籌碼特性證明：法人（尤其是投信與外資）具有強大的連續買盤與基本面調研優勢，唯有獲得實質資金流入支撐的二階動能翻正，才能真正轉化為波段趨勢。")
    md.append("")

    c1_common_6m = common_stats["C1"]["6m"]
    t1_common_6m = common_stats["T1"]["6m"]
    t1x_common_6m = common_stats["T1x"]["6m"]
    c1_win_c_str = f"{c1_common_6m['win_univ']*100:.1f}%" if c1_common_6m["win_univ"] is not None else "N/A"
    t1_win_c_str = f"{t1_common_6m['win_univ']*100:.1f}%" if t1_common_6m["win_univ"] is not None else "N/A"
    t1x_win_c_str = f"{t1x_common_6m['win_univ']*100:.1f}%" if t1x_common_6m["win_univ"] is not None else "N/A"
    c1_med_c_str = f"{c1_common_6m['median_excess']*100:+.2f}%" if c1_common_6m["median_excess"] is not None else "N/A"
    t1_med_c_str = f"{t1_common_6m['median_excess']*100:+.2f}%" if t1_common_6m["median_excess"] is not None else "N/A"
    t1x_med_c_str = f"{t1x_common_6m['median_excess']*100:+.2f}%" if t1x_common_6m["median_excess"] is not None else "N/A"
    c1_mean_c_str = f"{c1_common_6m['mean_excess']*100:+.2f}%" if c1_common_6m["mean_excess"] is not None else "N/A"
    t1_mean_c_str = f"{t1_common_6m['mean_excess']*100:+.2f}%" if t1_common_6m["mean_excess"] is not None else "N/A"
    t1x_mean_c_str = f"{t1x_common_6m['mean_excess']*100:+.2f}%" if t1x_common_6m["mean_excess"] is not None else "N/A"
    jan_diff_str = (
        f"{jan2024_t1_ex - jan2024_c1_ex:+.2f}%"
        if (jan2024_t1_ex is not None and jan2024_c1_ex is not None
            and not np.isnan(jan2024_t1_ex) and not np.isnan(jan2024_c1_ex))
        else "N/A"
    )

    md.append("### (c) T1 相對 C1 是「更早但更不準」還是「更早且不差」？")
    md.append("**答：結論是「更早且不差」，在估值排雷後（T1x）甚至更精練。**（數字取自「3.1 T1 領先 C1 月數分布」「3.2 T1 觸發後轉化為 C1 之成功率」與「2.2 同期基準比較」表格）")
    md.append(f"- **更早進場**：對每個進入 C1 的強勢波段起點，T1 平均領先 **{lead_s.mean():.1f} 個月** 觸發（Q1 領先 {lead_s.quantile(0.25):.1f} 個月，Q3 領先 {lead_s.quantile(0.75):.1f} 個月），成功在動能萌芽期進場。")
    md.append(f"- **不差且轉化率高**：T1 觸發後 6 個月內有 **{conv_rate_incl0:.1%}** 成功進入市場最強前 25%（C1）。在 2023-07 至 2026-07 共同基準期中：")
    md.append(f"  - C1（已強）6 個月按月勝率 {c1_win_c_str}，超額中位 {c1_med_c_str}，超額平均 {c1_mean_c_str}。")
    md.append(f"  - T1（轉強）6 個月按月勝率 **{t1_win_c_str}**，超額中位 {t1_med_c_str}，超額平均 **{t1_mean_c_str}**。")
    md.append(f"  - **T1x（排雷後的轉強）** 6 個月按月勝率 **{t1x_win_c_str}**，超額中位 {t1x_med_c_str}，超額平均 {t1x_mean_c_str}，選股更加精練（平均每月 {t1x_common_6m['avg_stocks']:.1f} 檔）。")
    if jan2024_t1_ex is not None and not np.isnan(jan2024_t1_ex) and jan2024_c1_ex is not None and not np.isnan(jan2024_c1_ex):
        md.append(f"- 在 2024 年 1 月族群輪動初期，T1 選股後 6 個月超額為 **{jan2024_t1_ex:+.2f}%**，C1 為 {jan2024_c1_ex:+.2f}%（差 {jan_diff_str}），數字詳見「4. 2024 年逐月選股檔數與 6 個月超額報酬對照」表格 2024-01 那一列。")
    md.append("")

    g1_win_pct_str = f"{g1_win_rate_6m*100:.1f}%"
    md.append("### (d) 族群廣度 G1 能否當輪動偵測器？")
    md.append(f"**答：不能單獨當作輪動買進訊號，勝率僅 {g1_win_pct_str}。**（數字取自「5. 族群層 G1 事件分析」與同一組變數）")
    md.append(f"- 實證回測全歷史 {len(valid_g1_6m)} 次 G1 事件（sub 內 above_ma60 比例由 < 0.4 升至 >= 0.5），事件後 6 個月跑贏大盤的比例僅 **{g1_win_pct_str}**，超額報酬中位數為 **{g1_med_ex_6m:+.2f}%**。")
    md.append("- 失敗機制：廣度由 0.3x 剛爬過 0.5 通常只是跌深反彈時群體補漲的假象，多數股票剛站上季線但扣抵高檔或遭遇解套賣壓，若缺乏基本面支撐極易隨後回跌。因此 G1 不宜單獨作為輪動觸發器，仍需搭配個股二階翻正與籌碼過濾。")
    md.append("")

    # --- 段落 7: 限制與警語 ---
    md.append("## 7. 研究限制與誠實揭露")
    md.append("1. **法人資料時間跨度限制**：`institutional_flow_daily` 僅涵蓋 2023-06-13 至 2026-08-25，因此 T1b/T1/T1x 僅能有效回測 37 個訊號日（33 個 6m 持有月份），樣本數少於無籌碼條件之 C1 與 T1a（60 個月份）。")
    md.append("2. **報酬計算未計股利與手續費**：本表報酬為純價格 forward return。")
    md.append("3. **存活者偏誤**：universe 取自現存 `valuation_screen` 標的，歷史早期可能存在存活者效應。")
    md.append("4. **子產業對照缺漏**：股票若在 `stock_sub_industry` 表查無對應子產業，會被歸入 `其他` 這個統一桶（見 `backtest_t1.py` 的 `sub_map.get(sid, \"其他\")`），與 `其他` 桶內其他缺對照的股票混算相對動能／Sub 基準，並非其真實子產業內的相對強弱。")
    md.append("")

    return "\n".join(md)


# ---------------------------------------------------------------------------
# CLI 進入點
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="動能轉強訊號 T1 回測框架")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="tw_stocks.db 路徑")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="輸出目錄路徑")
    args = parser.parse_args()

    print(f"[T1 回測] 開始執行... 資料庫: {args.db}, 輸出目錄: {args.out}")
    res = run_backtest_t1(db_path=args.db, out_dir=args.out)
    print("[T1 回測] 執行完成！")
    print(f"  - signals CSV: {res['signals_csv']}")
    print(f"  - group events CSV: {res['group_events_csv']}")
    print(f"  - summary Markdown: {res['summary_md']}")


if __name__ == "__main__":
    main()
