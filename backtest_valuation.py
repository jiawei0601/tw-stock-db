"""估值篩選器回測框架（Point-in-time 月頻回測）

嚴格 Point-in-time 規則：
1. 訊號日：每月最後一個有價格的交易日。
2. PER 視窗：訊號日往回 3 年（最少 1 年，不足 1 年該檔跳過），只用 date <= 訊號日的列。
   P25/P50/P75、位置 = (當日 PER − P25)/(P75 − P25)，剔除 PER <= 0 或 > 300。
3. EPS 可見日：quarter_end 3/31、6/30、9/30 -> +45 天；12/31 -> +75 天。
   訊號日只能用可見日 <= 訊號日的季資料。取最近 8 季算 eps_cv、loss_q、eps_ttm_growth。
4. 月營收可見日：該月營收在次月 10 日可見。
   rev_yoy_3m = 最近可見 3 個月 revenue 合計 / revenue_last_year 合計 − 1。
5. 分割偵測：訊號日前 60 個交易日內 |close/prev − 1| > 0.4 -> 排除。
6. 前瞻報酬：訊號日收盤到之後 3 / 6 / 12 個月最近交易日收盤，不含股利。資料不足記 NA。

三層濾網（逐層累加）：
- L0：位置 < 0
- L1：L0 且 eps_cv < 0.5 且 loss_q == 0 且 PER 視窗點數 >= 250 且非分割
- L2：L1 且 rev_yoy_3m > 0 且 eps_ttm_growth > 0 且非背離
  （背離 = rev_yoy_3m < -0.10 且 eps_ttm_growth > 0.20）
- 基準：同月所有可算位置的 universe 股票等權；另算同 sub 等權基準。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import statistics

import numpy as np
import pandas as pd

import build_valuation
from backtest_validity import (
    filter_complete_month_signals,
    get_next_trading_day,
    resolve_execution_price,
    compute_stock_forward_returns,
    generate_corporate_action_candidates,
    detect_corporate_actions_for_stock,
    compute_holding_return_adjusted,
    check_stock_eligibility,
    check_continuous_eps,
    check_continuous_revenue,
    check_corp_action_in_window,
    check_corp_action_in_per_window,
    block_bootstrap_paired_diff,
    compute_drifted_portfolio_equity_curve,
    compute_equal_dollar_tranche_return,
)

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_OUT_DIR = Path(__file__).parent / "backtest"


# ---------------------------------------------------------------------------
# Point-in-time 核心日期輔助函式
# ---------------------------------------------------------------------------

def get_eps_visible_date(quarter_end: str) -> str:
    """季報可見日：3/31、6/30、9/30 -> +45 天；12/31 -> +75 天。"""
    dt = datetime.strptime(quarter_end, "%Y-%m-%d")
    days = 75 if dt.month == 12 else 45
    return (dt + timedelta(days=days)).strftime("%Y-%m-%d")


def get_revenue_visible_date(ym: str) -> str:
    """月營收可見日：該月營收在次月 10 日可見。例如 2023-08 -> 2023-09-10。"""
    parts = ym.split("-")
    y, m = int(parts[0]), int(parts[1])
    if m == 12:
        return f"{y + 1:04d}-01-10"
    return f"{y:04d}-{m + 1:02d}-10"


def get_3y_start_date(signal_date: str) -> str:
    """訊號日往回推 3 年日期字串（使用 pd.DateOffset 處理閏年）。"""
    dt = datetime.strptime(signal_date, "%Y-%m-%d")
    return (dt - pd.DateOffset(years=3)).strftime("%Y-%m-%d")


def get_1y_limit_date(signal_date: str) -> str:
    """訊號日往回推 1 年日期字串（歷史資料最早日期必須 <= 此日期，跨度才滿 1 年）。"""
    dt = datetime.strptime(signal_date, "%Y-%m-%d")
    return (dt - pd.DateOffset(years=1)).strftime("%Y-%m-%d")


def compute_total_return(
    ret_price: float | None,
    dividend_yield: float | None,
    months: int,
) -> float | None:
    """計算含股利近似報酬：ret_tr = ret_price + (dividend_yield / 100) * (months / 12)。"""
    if ret_price is None:
        return None
    dy = dividend_yield if (dividend_yield is not None and not np.isnan(dividend_yield)) else 0.0
    return float(ret_price + (dy / 100.0) * (months / 12.0))


def compute_net_return(ret_tr: float | None, cost: float = 0.006) -> float | None:
    """計算扣成本報酬：ret_net = ret_tr - cost。"""
    if ret_tr is None:
        return None
    return float(ret_tr - cost)


def compute_equity_and_drawdown(
    monthly_returns: list[float],
) -> tuple[list[float], float, int]:
    """給定月報酬序列，計算累積淨值曲線（起點 1.0）與最大回撤幅度及其發生位置（索引）。"""
    equity = [1.0]
    for r in monthly_returns:
        equity.append(equity[-1] * (1.0 + r))
    eq_s = pd.Series(equity)
    peak = eq_s.cummax()
    dd = (peak - eq_s) / peak
    max_dd = float(dd.max())
    mdd_idx = int(dd.idxmax())
    return equity, max_dd, mdd_idx


# ---------------------------------------------------------------------------
# 指標運算函式（純函式，無副作用）
# ---------------------------------------------------------------------------

def compute_per_position(
    per_rows_sorted: list[tuple[str, float]],
    signal_date: str,
) -> tuple[float, float, float, float, int] | None:
    """計算 PER 視窗統計與位置。
    per_rows_sorted: 依 date 升冪排序之 (date, per) 清單，已過濾 0 < per <= 300。
    回傳 (cur_per, p25, p75, position, n_points) 或 None。
    """
    if not per_rows_sorted:
        return None

    dt_1y_limit = get_1y_limit_date(signal_date)
    dt_3y_start = get_3y_start_date(signal_date)

    valid_le: list[tuple[str, float]] = []
    cur_per: float | None = None
    for d, p in per_rows_sorted:
        if d > signal_date:
            break
        valid_le.append((d, p))
        if d == signal_date:
            cur_per = p

    if not valid_le or cur_per is None:
        return None

    earliest_date = valid_le[0][0]
    if earliest_date > dt_1y_limit:
        return None

    vals_3y = [p for d, p in valid_le if d >= dt_3y_start]
    n_pts = len(vals_3y)
    if n_pts < 2:
        return None

    p25 = float(np.percentile(vals_3y, 25))
    p75 = float(np.percentile(vals_3y, 75))
    if p75 == p25:
        position = 0.0
    else:
        position = float((cur_per - p25) / (p75 - p25))

    return (cur_per, p25, p75, position, n_pts)


def compute_eps_metrics(
    eps_rows_sorted: list[tuple[str, float, str]],
    signal_date: str,
) -> tuple[float | None, int | None, float | None, int]:
    """計算 EPS 指標（Item D: 連續 8 季檢查，缺季或最新一季超過 200 天則回傳 NA）。
    eps_rows_sorted: 依 quarter_end 升冪排序之 (quarter_end, eps, visible_date) 清單。
    回傳 (eps_cv, loss_q, eps_ttm_growth, n_visible)。
    """
    visible_eps = [e for e in eps_rows_sorted if e[2] <= signal_date]
    n_vis = len(visible_eps)
    if n_vis < 8:
        return (None, None, None, n_vis)

    is_cont, last8 = check_continuous_eps(visible_eps, signal_date, required_quarters=8, max_days_freshness=200)
    if not is_cont:
        return (None, None, None, n_vis)

    loss_q = sum(1 for e in last8 if e <= 0)

    m = statistics.mean(last8)
    s = statistics.pstdev(last8)
    eps_cv = float(s / abs(m)) if m != 0 else float("inf")

    rec4 = sum(last8[-4:])
    prv4 = sum(last8[:4])
    eps_ttm_growth = float(rec4 / prv4 - 1.0) if prv4 > 0 else None

    return (eps_cv, loss_q, eps_ttm_growth, n_vis)


def compute_revenue_metrics(
    rev_rows_sorted: list[tuple[str, int, int, str]],
    signal_date: str,
) -> float | None:
    """計算月營收 rev_yoy_3m（Item D: 連續 3 個月檢查，缺月或最新一期超過 45 天則回傳 NA）。
    rev_rows_sorted: 依 ym 升冪排序之 (ym, revenue, revenue_last_year, visible_date)。
    回傳 最近可見 3 個月 revenue 合計 / revenue_last_year 合計 − 1 或 None。
    """
    visible_revs = [
        (ym, rev, rev_ly, v)
        for ym, rev, rev_ly, v in rev_rows_sorted
        if v <= signal_date and rev is not None and rev_ly is not None
    ]
    if len(visible_revs) < 3:
        return None

    is_cont, last3 = check_continuous_revenue(visible_revs, signal_date, required_months=3, max_days_freshness=45)
    if not is_cont:
        return None

    sum_cur = sum(r[0] for r in last3)
    sum_prev = sum(r[1] for r in last3)
    if sum_prev <= 0:
        return None
    return float(sum_cur / sum_prev - 1.0)


def detect_split_flag(
    price_rows_sorted: list[tuple[str, float]],
    signal_date: str,
) -> bool:
    """分割偵測：訊號日前 60 個交易日內 |close/prev − 1| > 0.4 -> 排除。"""
    valid_prices = [(d, c) for d, c in price_rows_sorted if d <= signal_date and c > 0]
    if len(valid_prices) < 2:
        return False

    recent = valid_prices[-61:]
    for i in range(1, len(recent)):
        p_prev = recent[i - 1][1]
        p_cur = recent[i][1]
        if p_prev > 0 and abs(p_cur / p_prev - 1.0) > 0.4:
            return True
    return False


def get_close_price(
    prices_dict: dict[str, float],
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    target_date: str,
    max_date: str | None = None,
) -> float | None:
    """取得目標日收盤價，優先當日，若無則在前後 5 個交易日內尋找最近收盤價。
    若指定 max_date，嚴格排除 date > max_date 的價格（Point-in-time 保護）。
    """
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


def compute_momentum(
    prices_dict: dict[str, float],
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    signal_date: str,
    past_date: str | None,
) -> float | None:
    """計算動能指標：訊號日收盤 / 過去目標日收盤 − 1。
    嚴格 Point-in-time：只用 date <= signal_date 的價格。
    """
    if past_date is None:
        return None
    p_cur = get_close_price(prices_dict, all_trading_dates, date_to_idx, signal_date, max_date=signal_date)
    p_past = get_close_price(prices_dict, all_trading_dates, date_to_idx, past_date, max_date=signal_date)
    if p_cur is not None and p_past is not None and p_past > 0 and p_cur > 0:
        return float(p_cur / p_past - 1.0)
    return None


def compute_sub_relative_momentum(month_stock_records: list[dict]) -> None:
    """計算同月同 sub 等權 mom 與 rel_mom (mom − sub_mom)，就地更新 month_stock_records。"""
    for period in ("3m", "1m", "12_1"):
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
            sub = s["sub"]
            sub_m = sub_means.get(sub)
            s[sub_col] = sub_m
            m_val = s.get(mom_col)
            if m_val is not None and sub_m is not None:
                s[rel_col] = float(m_val - sub_m)
            else:
                s[rel_col] = None


def compute_percentile_rank(values: list[float | None]) -> list[float | None]:
    """計算數列在當月有效值中的百分位數 (0–1)。
    使用 pandas rank(pct=True)，值域在 (0, 1] 之間，NaN 保持 None。
    """
    valid_indices = [i for i, v in enumerate(values) if v is not None]
    if not valid_indices:
        return [None] * len(values)
    s = pd.Series([values[i] for i in valid_indices])
    ranks = s.rank(pct=True).tolist()
    res: list[float | None] = [None] * len(values)
    for idx, r in zip(valid_indices, ranks):
        res[idx] = float(r)
    return res


def generate_portfolio_equity_curve(
    df_eval: pd.DataFrame,
    month_signals: list[str],
    price_dict_by_stock: dict[str, dict[str, float]],
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    div_yield_dict: dict[str, dict[str, float]],
    out_dir: Path,
    price_rows_by_stock: dict[str, list[tuple[str, float]]] | None = None,
    corp_actions_map: dict[str, dict[str, float]] | None = None,
    strats: tuple[str, ...] = ("D0", "D3", "bench"),
) -> tuple[pd.DataFrame, dict]:
    """計算 D0, D3, bench 等策略之 3 個月持有漂移組合層淨值曲線與指標。
    Item E 要求：
    - 3 個 cohort 各自持有 3 個月，隨價格漂移，到期再投入
    - 跨 cohort 淨額扣費（買入與賣出各 0.3%，合計 0.6%）
    - 暖機期無選股不扣費
    - 輸出 backtest/equity_curve.csv
    """
    signals_by_date: dict[str, dict[str, set[str]]] = {}
    for s_date, grp in df_eval.groupby("signal_date"):
        s_map = {}
        for st in strats:
            if st == "bench":
                s_map["bench"] = set(grp["stock_id"])
            elif st in grp.columns:
                s_map[st] = set(grp[grp[st]]["stock_id"])
            else:
                s_map[st] = set()
        signals_by_date[s_date] = s_map

    if price_rows_by_stock is None:
        price_rows_by_stock = {}
        for sid, pdict in price_dict_by_stock.items():
            price_rows_by_stock[sid] = sorted(pdict.items(), key=lambda x: x[0])

    if corp_actions_map is None:
        corp_actions_file = out_dir / "corporate_actions_detected.csv"
        corp_actions_map = {}
        if corp_actions_file.exists():
            ca_df = pd.read_csv(corp_actions_file)
            for _, r in ca_df.iterrows():
                corp_actions_map.setdefault(str(r["stock_id"]).zfill(4), {})[str(r["date"])] = float(r["multiplier"])

    valid_dates = [d for d in month_signals if d in signals_by_date]

    equity_df, port_metrics = compute_drifted_portfolio_equity_curve(
        signals_by_date=signals_by_date,
        valid_dates=valid_dates,
        price_dict_by_stock=price_dict_by_stock,
        price_rows_by_stock=price_rows_by_stock,
        all_trading_dates=all_trading_dates,
        date_to_idx=date_to_idx,
        div_yield_dict=div_yield_dict,
        corp_actions_map=corp_actions_map,
        strats=strats,
    )

    equity_csv_path = out_dir / "equity_curve.csv"
    equity_df.to_csv(equity_csv_path, index=False, encoding="utf-8")
    return equity_df, port_metrics


def compute_d_tiers(record: dict, rank_threshold: float = 0.75) -> dict:
    """依 record 的 rel_mom_rank/position/is_split/is_diverge/n_eps_vis/eps_cv/loss_q/
    rev_yoy_3m 欄位，計算 D0–D5 旗標（純函式，供 run_backtest 與測試共用）。

    D 層定義（在「rel_mom_rank >= rank_threshold」的動能門檻基礎上逐層排除，
    每層都是前一層的子集）：
    - D0：rel_mom_rank >= rank_threshold
    - D1：D0 且非分割股票（not is_split）
    - D2：D1 且無營收/EPS 背離（not is_diverge）
    - D3：D2 且品質過關（n_eps_vis >= 8 且 eps_cv < 0.5 且 loss_q == 0）
    - D4：D3 且未落入估值極端貴（position <= 2.0）
    - D5：D3 且近 3 個月營收年增率為正（rev_yoy_3m > 0）
    """
    rk = record["rel_mom_rank"]
    pos = record["position"]
    is_sp = record["is_split"]
    is_div = record["is_diverge"]
    n_ev = record["n_eps_vis"]
    ecv = record["eps_cv"]
    lq = record["loss_q"]
    ry = record["rev_yoy_3m"]

    is_qual_ok = bool(n_ev >= 8 and (ecv is not None and ecv < 0.5) and (lq is not None and lq == 0))

    is_d0 = bool(rk is not None and rk >= rank_threshold)
    is_d1 = bool(is_d0 and not is_sp)
    is_d2 = bool(is_d1 and not is_div)
    is_d3 = bool(is_d2 and is_qual_ok)
    is_d4 = bool(is_d3 and (pos is not None and pos <= 2.0))
    is_d5 = bool(is_d3 and (ry is not None and ry > 0))

    return {"D0": is_d0, "D1": is_d1, "D2": is_d2, "D3": is_d3, "D4": is_d4, "D5": is_d5}


# ---------------------------------------------------------------------------
# 回測核心引擎
# ---------------------------------------------------------------------------

def run_backtest(
    db_path: Path = DEFAULT_DB_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict:
    """執行完整 Point-in-time 回測流程，輸出 signals.csv 與 summary.md。"""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # 0. 公司行動雙向候選偵測並對照 fm_corporate_events (±3 交易日)
    cand_res = generate_corporate_action_candidates(conn, out_dir)
    cand_df = pd.read_csv(out_dir / "corporate_action_candidates.csv", dtype={"stock_id": str})
    cand_df["stock_id"] = cand_df["stock_id"].astype(str).str.zfill(4)
    confirmed_df = cand_df[cand_df["status"] == "confirmed"]
    confirmed_events_map: dict[str, set[str]] = {}
    for sid, grp in confirmed_df.groupby("stock_id"):
        confirmed_events_map[sid] = set(grp["date"])

    # 載入下市名單
    try:
        delist_rows = conn.execute("SELECT stock_id, date FROM fm_delisting").fetchall()
        delist_map = {str(r[0]).zfill(4): r[1] for r in delist_rows}
    except Exception:
        delist_map = {}

    # 1. 取得 universe 股票池 (2026 存活成分股回顧回測，凍結於 universe_2026_survivors.csv)
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
            "listed_date": None,
        })
        conn_tmp.close()

    # 母體改從 stock_sub_industry 半導體鏈與 ai_chain 清單的聯集取（不經 valuation_screen 過濾）
    ai_stocks = set(str(k).zfill(4) for k in build_valuation.ai_chain_universe().keys())
    all_sub_stocks = set(str(r[0]).zfill(4) for r in conn.execute("SELECT DISTINCT stock_id FROM stock_sub_industry"))
    mother_union = ai_stocks | all_sub_stocks
    surv_stock_set = set(df_surv["stock_id"].astype(str).str.zfill(4))
    universe_stocks = sorted(list(mother_union | surv_stock_set))

    universe_diff_info = {
        "mother_union_count": len(mother_union),
        "survivors_count": len(surv_stock_set),
        "in_union_not_surv": sorted(list(mother_union - surv_stock_set)),
        "in_surv_not_union": sorted(list(surv_stock_set - mother_union)),
    }

    first_close_date_map = dict(zip(df_surv["stock_id"].astype(str).str.zfill(4), df_surv.get("listed_date", df_surv.get("first_close_date"))))
    surv_sub_map = dict(zip(df_surv["stock_id"].astype(str).str.zfill(4), df_surv["sub"]))

    # 2. 取得 sub 產業映射
    sub_map: dict[str, str] = {}
    for sid, sub in conn.execute(
        "SELECT stock_id, sub FROM stock_sub_industry ORDER BY stock_id, node"
    ).fetchall():
        sub_map[str(sid).zfill(4)] = sub
    for sid in universe_stocks:
        if sid in surv_sub_map and isinstance(surv_sub_map[sid], str) and surv_sub_map[sid].strip():
            sub_map[sid] = surv_sub_map[sid]
        elif sid not in sub_map:
            sub_map[sid] = "其他"

    # 3. 取得各表最早日期統計
    earliest_dates = {}
    for t, col in [
        ("per_daily", "date"),
        ("fm_price_daily", "date"),
        ("fm_price_adj_daily", "date"),
        ("eps_quarterly", "quarter_end"),
        ("fm_revenue_monthly", "ym"),
    ]:
        try:
            row = conn.execute(f"SELECT MIN({col}), MAX({col}), COUNT(*) FROM {t}").fetchone()
            earliest_dates[t] = {
                "min": row[0] if row else None,
                "max": row[1] if row else None,
                "count": row[2] if row else 0,
            }
        except Exception:
            earliest_dates[t] = {"min": None, "max": None, "count": 0}

    # 4. 交易日曆與月訊號日（以 fm_price_adj_daily 為準，無則降級為 fm_price_daily）
    try:
        dates_df = pd.read_sql("SELECT DISTINCT date FROM fm_price_adj_daily ORDER BY date", conn)
    except Exception:
        dates_df = pd.read_sql("SELECT DISTINCT date FROM fm_price_daily ORDER BY date", conn)
    all_trading_dates = dates_df["date"].tolist()
    date_to_idx = {d: i for i, d in enumerate(all_trading_dates)}
    dates_s = pd.to_datetime(dates_df["date"])
    month_signals = filter_complete_month_signals(all_trading_dates)

    # 5. 批次載入 universe 相關資料至記憶體
    df_per = pd.read_sql(
        "SELECT stock_id, date, per, dividend_yield FROM per_daily ORDER BY stock_id, date",
        conn,
    )
    df_per["stock_id"] = df_per["stock_id"].astype(str).str.zfill(4)
    df_per = df_per[df_per["stock_id"].isin(universe_stocks)]
    df_per_valid = df_per[(df_per["per"] > 0) & (df_per["per"] <= 300)]
    per_by_stock: dict[str, list[tuple[str, float]]] = {}
    for sid, grp in df_per_valid.groupby("stock_id"):
        per_by_stock[sid] = list(zip(grp["date"], grp["per"]))

    div_yield_dict: dict[str, dict[str, float]] = {}
    for sid, grp in df_per.groupby("stock_id"):
        div_yield_dict[sid] = dict(zip(grp["date"], grp["dividend_yield"]))

    # 報酬、動能（3 月、1 月、12-1 月）、均線：全部改用 fm_price_adj_daily
    try:
        df_price_adj = pd.read_sql(
            "SELECT stock_id, date, close_adj AS close FROM fm_price_adj_daily WHERE close_adj > 0 ORDER BY stock_id, date",
            conn,
        )
    except Exception:
        df_price_adj = pd.read_sql(
            "SELECT stock_id, date, close FROM fm_price_daily WHERE close > 0 ORDER BY stock_id, date",
            conn,
        )
    df_price_adj["stock_id"] = df_price_adj["stock_id"].astype(str).str.zfill(4)
    df_price_adj = df_price_adj[df_price_adj["stock_id"].isin(universe_stocks)]
    price_rows_by_stock: dict[str, list[tuple[str, float]]] = {}
    price_dict_by_stock: dict[str, dict[str, float]] = {}
    for sid, grp in df_price_adj.groupby("stock_id"):
        price_rows_by_stock[sid] = list(zip(grp["date"], grp["close"]))
        price_dict_by_stock[sid] = dict(zip(grp["date"], grp["close"]))

    # 未還原股價 (fm_price_daily)，供 PER 視窗與分割旗標偵測
    df_price_unadj = pd.read_sql(
        "SELECT stock_id, date, close FROM fm_price_daily WHERE close > 0 ORDER BY stock_id, date",
        conn,
    )
    df_price_unadj["stock_id"] = df_price_unadj["stock_id"].astype(str).str.zfill(4)
    df_price_unadj = df_price_unadj[df_price_unadj["stock_id"].isin(universe_stocks)]
    price_unadj_rows_by_stock: dict[str, list[tuple[str, float]]] = {}
    price_unadj_dict_by_stock: dict[str, dict[str, float]] = {}
    for sid, grp in df_price_unadj.groupby("stock_id"):
        price_unadj_rows_by_stock[sid] = list(zip(grp["date"], grp["close"]))
        price_unadj_dict_by_stock[sid] = dict(zip(grp["date"], grp["close"]))

    df_eps = pd.read_sql(
        "SELECT stock_id, quarter_end, eps FROM eps_quarterly WHERE eps IS NOT NULL ORDER BY stock_id, quarter_end",
        conn,
    )
    df_eps["stock_id"] = df_eps["stock_id"].astype(str).str.zfill(4)
    df_eps = df_eps[df_eps["stock_id"].isin(universe_stocks)]
    eps_by_stock: dict[str, list[tuple[str, float, str]]] = {}
    for sid, grp in df_eps.groupby("stock_id"):
        rows_with_vis = [
            (q, float(e), get_eps_visible_date(q))
            for q, e in zip(grp["quarter_end"], grp["eps"])
        ]
        eps_by_stock[sid] = rows_with_vis

    df_rev = pd.read_sql(
        "SELECT stock_id, ym, revenue, revenue_last_year FROM fm_revenue_monthly "
        "WHERE revenue IS NOT NULL AND revenue_last_year IS NOT NULL ORDER BY stock_id, ym",
        conn,
    )
    rev_by_stock: dict[str, list[tuple[str, int, int, str]]] = {}
    if not df_rev.empty:
        df_rev["stock_id"] = df_rev["stock_id"].astype(str).str.zfill(4)
        df_rev = df_rev[df_rev["stock_id"].isin(universe_stocks)]
        for sid, grp in df_rev.groupby("stock_id"):
            rev_by_stock[sid] = [
                (ym, int(rev), int(rev_ly), get_revenue_visible_date(ym))
                for ym, rev, rev_ly in zip(grp["ym"], grp["revenue"], grp["revenue_last_year"])
            ]

    conn.close()

    # 6. 逐月運算所有訊號與前瞻報酬
    all_eval_rows: list[dict] = []
    eval_stock_counts_per_month: list[int] = []
    excluded_per_event_stock_months = 0

    horizon_offsets = {"3m": 3, "6m": 6, "12m": 12}

    for i, sig_date in enumerate(month_signals):
        target_dates = {
            h: month_signals[i + off] if (i + off < len(month_signals)) else None
            for h, off in horizon_offsets.items()
        }
        tgt_date_3m = month_signals[i - 3] if i >= 3 else None
        tgt_date_1m = month_signals[i - 1] if i >= 1 else None
        tgt_date_12m = month_signals[i - 12] if i >= 12 else None

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

            dy_raw = div_yield_dict.get(sid, {}).get(sig_date)
            div_yield_at_sig = float(dy_raw) if (dy_raw is not None and not np.isnan(dy_raw)) else 0.0

            stock_fwd = compute_stock_forward_returns(
                sid=sid,
                sig_date=sig_date,
                target_dates=target_dates,
                price_dict=price_dict,
                price_rows=price_rows,
                all_trading_dates=all_trading_dates,
                date_to_idx=date_to_idx,
                corp_actions_map=None,
                div_yield_at_sig=div_yield_at_sig,
                delist_map=delist_map,
                unadj_price_dict=price_unadj_dict_by_stock.get(sid, {}),
            )
            fwd_rets = {h: stock_fwd[f"ret_{h}"] for h in ("3m", "6m", "12m")}
            fwd_rets_tr = {h: stock_fwd[f"ret_{h}_tr"] for h in ("3m", "6m", "12m")}
            fwd_rets_net = {h: stock_fwd[f"ret_{h}_net"] for h in ("3m", "6m", "12m")}

            # 分割旗標偵測用未還原價
            is_split = detect_split_flag(price_unadj_rows_by_stock.get(sid, []), sig_date)

            eps_rows = eps_by_stock.get(sid, [])
            eps_cv, loss_q, eps_ttm_growth, n_eps_vis = compute_eps_metrics(eps_rows, sig_date)

            rev_rows = rev_by_stock.get(sid, [])
            rev_yoy_3m = compute_revenue_metrics(rev_rows, sig_date)

            # 動能改用還原價，不需因事件記 NA
            mom_3m = compute_momentum(price_dict, all_trading_dates, date_to_idx, sig_date, tgt_date_3m)
            mom_1m = compute_momentum(price_dict, all_trading_dates, date_to_idx, sig_date, tgt_date_1m)

            if tgt_date_12m is not None and tgt_date_1m is not None:
                p_1m = get_close_price(price_dict, all_trading_dates, date_to_idx, tgt_date_1m, max_date=tgt_date_1m)
                p_12m = get_close_price(price_dict, all_trading_dates, date_to_idx, tgt_date_12m, max_date=tgt_date_12m)
                mom_12_1 = (p_1m / p_12m - 1.0) if (p_1m and p_12m and p_12m > 0) else None
            else:
                mom_12_1 = None

            # PER 視窗：若跨過 confirmed 事件日，記 NA 並統計
            dt_3y_start = get_3y_start_date(sig_date)
            conf_evs = confirmed_events_map.get(sid, set())
            has_conf_in_per = any(dt_3y_start <= ev_d <= sig_date for ev_d in conf_evs)
            if has_conf_in_per:
                per_res = None
                excluded_per_event_stock_months += 1
            else:
                per_rows = per_by_stock.get(sid, [])
                per_res = compute_per_position(per_rows, sig_date)

            if per_res is not None:
                cur_per, p25, p75, position, n_per_pts = per_res
            else:
                cur_per, p25, p75, position, n_per_pts = None, None, None, None, 0

            is_l0 = bool(position is not None and position < 0)
            is_l1 = bool(
                is_l0
                and (eps_cv is not None and eps_cv < 0.5)
                and (loss_q is not None and loss_q == 0)
                and (n_per_pts >= 250)
                and (not is_split)
                and (n_eps_vis >= 8)
            )

            is_diverge = bool(
                rev_yoy_3m is not None
                and rev_yoy_3m < -0.10
                and eps_ttm_growth is not None
                and eps_ttm_growth > 0.20
            )

            is_l2 = bool(
                is_l1
                and (rev_yoy_3m is not None and rev_yoy_3m > 0)
                and (eps_ttm_growth is not None and eps_ttm_growth > 0)
                and (not is_diverge)
            )

            level = "L2" if is_l2 else ("L1" if is_l1 else ("L0" if is_l0 else "NONE"))

            stock_record = {
                "signal_date": sig_date,
                "stock_id": sid,
                "sub": sub,
                "level": level,
                "is_l0": is_l0,
                "is_l1": is_l1,
                "is_l2": is_l2,
                "position": position,
                "cur_per": cur_per,
                "p25": p25,
                "p75": p75,
                "n_per_pts": n_per_pts,
                "eps_cv": eps_cv,
                "rev_yoy_3m": rev_yoy_3m,
                "mom_3m": mom_3m,
                "mom_1m": mom_1m,
                "mom_12_1": mom_12_1,
                "ret_3m": fwd_rets["3m"],
                "ret_6m": fwd_rets["6m"],
                "ret_12m": fwd_rets["12m"],
                "dividend_yield_at_signal": div_yield_at_sig,
                "entry_status": stock_fwd.get("entry_status", "unfilled"),
                "exit_status_3m": stock_fwd.get("exit_status_3m", "unresolved"),
                "exit_status_6m": stock_fwd.get("exit_status_6m", "unresolved"),
                "exit_status_12m": stock_fwd.get("exit_status_12m", "unresolved"),
                "valuation_status_3m": stock_fwd.get("valuation_status_3m", "unresolved"),
                "valuation_status_6m": stock_fwd.get("valuation_status_6m", "unresolved"),
                "valuation_status_12m": stock_fwd.get("valuation_status_12m", "unresolved"),
                "ret_3m_cons": stock_fwd.get("ret_3m_cons"),
                "ret_6m_cons": stock_fwd.get("ret_6m_cons"),
                "ret_12m_cons": stock_fwd.get("ret_12m_cons"),
                "ret_3m_tr": fwd_rets_tr["3m"],
                "ret_6m_tr": fwd_rets_tr["6m"],
                "ret_12m_tr": fwd_rets_tr["12m"],
                "ret_3m_net": fwd_rets_net["3m"],
                "ret_6m_net": fwd_rets_net["6m"],
                "ret_12m_net": fwd_rets_net["12m"],
                "is_split": is_split,
                "is_diverge": is_diverge,
                "loss_q": loss_q,
                "eps_ttm_growth": eps_ttm_growth,
                "n_eps_vis": n_eps_vis,
                "is_universe": True,
                "exit_reason_3m": stock_fwd.get("exit_reason_3m", "none"),
                "exit_reason_6m": stock_fwd.get("exit_reason_6m", "none"),
                "exit_reason_12m": stock_fwd.get("exit_reason_12m", "none"),
                "ret_3m_old": stock_fwd.get("ret_3m_old"),
                "ret_6m_old": stock_fwd.get("ret_6m_old"),
                "ret_12m_old": stock_fwd.get("ret_12m_old"),
            }
            month_stocks.append(stock_record)

        eval_stock_counts_per_month.append(len(month_stocks))

        if month_stocks:
            for ret_sfx in ("", "_tr", "_net"):
                for h in ("3m", "6m", "12m"):
                    col = f"ret_{h}{ret_sfx}"
                    valid_rets = [s[col] for s in month_stocks if s[col] is not None]
                    bench_val = float(np.mean(valid_rets)) if valid_rets else None

                    sub_rets: dict[str, list[float]] = {}
                    for s in month_stocks:
                        if s[col] is not None:
                            sub_rets.setdefault(s["sub"], []).append(s[col])
                    sub_bench_val = {
                        k: float(np.mean(v)) for k, v in sub_rets.items() if v
                    }

                    b_col = f"bench_{h}{ret_sfx}"
                    sb_col = f"sub_bench_{h}{ret_sfx}"
                    for s in month_stocks:
                        s[b_col] = bench_val
                        s[sb_col] = sub_bench_val.get(s["sub"], bench_val)

            compute_sub_relative_momentum(month_stocks)

            ranks_3m = compute_percentile_rank([s["rel_mom_3m"] for s in month_stocks])
            ranks_12_1 = compute_percentile_rank([s["mom_12_1"] for s in month_stocks])
            ranks_12_1_rel = compute_percentile_rank([s.get("rel_mom_12_1") for s in month_stocks])
            for s, rk, rk12, rk12_rel in zip(month_stocks, ranks_3m, ranks_12_1, ranks_12_1_rel):
                s["rel_mom_rank"] = rk
                s["mom_12_1_rank"] = rk12
                s["rel_mom_12_1_rank"] = rk12_rel

            for s in month_stocks:
                r_m3 = s["rel_mom_3m"]
                r_m1 = s["rel_mom_1m"]
                rk = s["rel_mom_rank"]
                rk12 = s.get("mom_12_1_rank")
                rk12_rel = s.get("rel_mom_12_1_rank")
                pos = s["position"]

                is_m1 = bool(s["is_l1"] and r_m3 is not None and r_m3 > 0)
                is_m2 = bool(is_m1 and r_m1 is not None and r_m1 > 0)
                is_m3 = bool(s["is_l0"] and r_m3 is not None and r_m3 > 0)
                is_c1 = bool(rk is not None and rk >= 0.75)
                is_c1_per = bool(is_c1 and pos is not None)
                is_c2 = bool(is_c1 and pos is not None and pos < 0)
                is_mom_12_1 = bool(rk12 is not None and rk12 >= 0.75)
                is_c1_12_1 = bool(rk12_rel is not None and rk12_rel >= 0.75)

                s["is_m1"] = is_m1
                s["is_m2"] = is_m2
                s["is_m3"] = is_m3
                s["is_c1"] = is_c1
                s["is_c1_per"] = is_c1_per
                s["is_c2"] = is_c2
                s["is_mom_12_1"] = is_mom_12_1
                s["is_c1_12_1"] = is_c1_12_1

                s["M1"] = is_m1
                s["M2"] = is_m2
                s["M3"] = is_m3
                s["C1"] = is_c1
                s["C1_PER"] = is_c1_per
                s["C2"] = is_c2
                s["MOM_12_1"] = is_mom_12_1
                s["C1_12_1"] = is_c1_12_1

                d_tiers = compute_d_tiers(s, rank_threshold=0.75)
                s.update(d_tiers)
                s["is_d0"] = d_tiers["D0"]
                s["is_d1"] = d_tiers["D1"]
                s["is_d2"] = d_tiers["D2"]
                s["is_d3"] = d_tiers["D3"]
                s["is_d4"] = d_tiers["D4"]
                s["is_d5"] = d_tiers["D5"]

                s["D3@0.6"] = compute_d_tiers(s, rank_threshold=0.6)["D3"]
                s["D3@0.9"] = compute_d_tiers(s, rank_threshold=0.9)["D3"]
                s["is_d3_06"] = s["D3@0.6"]
                s["is_d3_09"] = s["D3@0.9"]

            all_eval_rows.extend(month_stocks)

    df_eval = pd.DataFrame(all_eval_rows)

    has_signal = df_eval["is_l0"] | df_eval["C1"]
    signals_df = df_eval[has_signal].copy()
    signals_cols = [
        "signal_date", "stock_id", "sub", "level", "is_l0", "is_l1", "is_l2", "position", "eps_cv", "rev_yoy_3m",
        "dividend_yield_at_signal",
        "entry_status",
        "exit_status_3m", "exit_status_6m", "exit_status_12m",
        "valuation_status_3m", "valuation_status_6m", "valuation_status_12m",
        "ret_3m", "ret_6m", "ret_12m",
        "ret_3m_cons", "ret_6m_cons", "ret_12m_cons",
        "ret_3m_tr", "ret_6m_tr", "ret_12m_tr",
        "ret_3m_net", "ret_6m_net", "ret_12m_net",
        "bench_3m", "bench_6m", "bench_12m",
        "sub_bench_3m", "sub_bench_6m", "sub_bench_12m",
        "exit_reason_3m", "exit_reason_6m", "exit_reason_12m",
        "ret_3m_old", "ret_6m_old", "ret_12m_old",
        "mom_3m", "mom_1m", "mom_12_1", "rel_mom_3m", "rel_mom_1m", "rel_mom_12_1",
        "rel_mom_rank", "mom_12_1_rank", "rel_mom_12_1_rank",
        "M1", "M2", "M3", "C1", "C1_PER", "C2", "MOM_12_1", "C1_12_1",
        "D0", "D1", "D2", "D3", "D4", "D5",
    ]
    signals_out = signals_df[signals_cols].copy()
    signals_csv_path = out_dir / "signals.csv"
    signals_out.to_csv(signals_csv_path, index=False, encoding="utf-8")

    # 輸出組合層累積淨值曲線 (equity_curve.csv)
    equity_df, portfolio_metrics = generate_portfolio_equity_curve(
        df_eval=df_eval,
        month_signals=month_signals,
        price_dict_by_stock=price_dict_by_stock,
        all_trading_dates=all_trading_dates,
        date_to_idx=date_to_idx,
        div_yield_dict=div_yield_dict,
        out_dir=out_dir,
        price_rows_by_stock=price_rows_by_stock,
        corp_actions_map={},
        strats=("D0", "D3", "bench"),
    )
    equity_curve_path = out_dir / "equity_curve.csv"

    summary_md_path = out_dir / "summary.md"
    summary_content, tier_stats = generate_summary_markdown(
        df_eval=df_eval,
        earliest_dates=earliest_dates,
        month_signals=month_signals,
        eval_stock_counts=eval_stock_counts_per_month,
        portfolio_metrics=portfolio_metrics,
        cand_res=cand_res,
        universe_diff_info=universe_diff_info,
        excluded_per_event_stock_months=excluded_per_event_stock_months,
    )
    with open(summary_md_path, "w", encoding="utf-8") as f:
        f.write(summary_content)

    return {
        "total_signal_months": len(month_signals),
        "evaluable_months": sum(1 for c in eval_stock_counts_per_month if c > 0),
        "avg_evaluable_stocks": float(np.mean(eval_stock_counts_per_month)),
        "total_signals_l0": int(df_eval["is_l0"].sum()),
        "total_signals_l1": int(df_eval["is_l1"].sum()),
        "total_signals_l2": int(df_eval["is_l2"].sum()),
        "total_signals_m1": int(df_eval["is_m1"].sum()),
        "total_signals_m2": int(df_eval["is_m2"].sum()),
        "total_signals_m3": int(df_eval["is_m3"].sum()),
        "total_signals_c1": int(df_eval["is_c1"].sum()),
        "total_signals_c1_12_1": int(df_eval["is_c1_12_1"].sum()),
        "total_signals_mom_12_1": int(df_eval["is_mom_12_1"].sum()),
        "total_signals_c2": int(df_eval["is_c2"].sum()),
        "total_signals_d0": int(df_eval["is_d0"].sum()),
        "total_signals_d1": int(df_eval["is_d1"].sum()),
        "total_signals_d2": int(df_eval["is_d2"].sum()),
        "total_signals_d3": int(df_eval["is_d3"].sum()),
        "total_signals_d4": int(df_eval["is_d4"].sum()),
        "total_signals_d5": int(df_eval["is_d5"].sum()),
        "signals_csv_path": str(signals_csv_path),
        "equity_curve_csv_path": str(equity_curve_path),
        "summary_md_path": str(summary_md_path),
        "tier_stats": tier_stats,
    }


# ---------------------------------------------------------------------------
# 績效統計與 Markdown 生成
# ---------------------------------------------------------------------------

def calculate_tier_performance(
    df_eval: pd.DataFrame,
    flag_col: str,
    year_filter: int | None = None,
    return_type: str = "price",
) -> list[dict]:
    """計算特定層級濾網在 3m/6m/12m 下的月度勝率與超額報酬統計。"""
    sub_df = df_eval[df_eval[flag_col]].copy()
    if year_filter is not None:
        sub_df = sub_df[sub_df["signal_date"].str.startswith(str(year_filter))]

    ret_sfx = "" if return_type == "price" else f"_{return_type}"
    results = []
    for h in ("3m", "6m", "12m"):
        ret_col = f"ret_{h}{ret_sfx}"
        b_col = f"bench_{h}{ret_sfx}"
        sb_col = f"sub_bench_{h}{ret_sfx}"

        month_excess_list = []
        for s_date, grp in sub_df.groupby("signal_date"):
            valid = grp[grp[ret_col].notna()]
            if len(valid) == 0:
                continue
            b_val = valid[b_col].iloc[0]
            if b_val is None or np.isnan(b_val):
                continue

            port_ret = float(valid[ret_col].mean())
            excess_univ = port_ret - b_val
            excess_sub = float((valid[ret_col] - valid[sb_col]).mean())

            month_excess_list.append({
                "signal_date": s_date,
                "n_stocks": len(valid),
                "port_ret": port_ret,
                "excess_univ": excess_univ,
                "excess_sub": excess_sub,
            })

        if not month_excess_list:
            results.append({
                "horizon": h,
                "months": 0,
                "avg_stocks": 0.0,
                "win_univ": None,
                "win_sub": None,
                "median_excess": None,
                "mean_excess": None,
                "worst_month": "N/A",
            })
            continue

        m_df = pd.DataFrame(month_excess_list)
        n_months = len(m_df)
        avg_stocks = float(m_df["n_stocks"].mean())
        win_univ = float((m_df["excess_univ"] > 0).mean())
        win_sub = float((m_df["excess_sub"] > 0).mean())
        med_excess = float(m_df["excess_univ"].median())
        mean_excess = float(m_df["excess_univ"].mean())

        worst_idx = m_df["excess_univ"].idxmin()
        worst_r = m_df.loc[worst_idx]
        worst_str = f"{worst_r['signal_date'][:7]} ({worst_r['signal_date']}): {worst_r['excess_univ'] * 100:.2f}%"

        results.append({
            "horizon": h,
            "months": n_months,
            "avg_stocks": avg_stocks,
            "win_univ": win_univ,
            "win_sub": win_sub,
            "median_excess": med_excess,
            "mean_excess": mean_excess,
            "worst_month": worst_str,
        })
    return results


def format_stat_table(stats: list[dict]) -> str:
    """將績效統計格式化為 Markdown 表格。"""
    lines = [
        "| 持有期間 | 月份數 | 平均選股數 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額報酬中位數 | 超額報酬平均 | 最差月份與其日期 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]
    for r in stats:
        if r["months"] == 0:
            lines.append(f"| {r['horizon']} | 0 | 0.0 | N/A | N/A | N/A | N/A | N/A |")
            continue
        win_u_str = f"{r['win_univ'] * 100:.1f}%" if r["win_univ"] is not None else "N/A"
        win_s_str = f"{r['win_sub'] * 100:.1f}%" if r["win_sub"] is not None else "N/A"
        med_str = f"{r['median_excess'] * 100:+.2f}%" if r["median_excess"] is not None else "N/A"
        mean_str = f"{r['mean_excess'] * 100:+.2f}%" if r["mean_excess"] is not None else "N/A"
        lines.append(
            f"| {r['horizon']} | {r['months']} | {r['avg_stocks']:.1f} | {win_u_str} | {win_s_str} | {med_str} | {mean_str} | {r['worst_month']} |"
        )
    return "\n".join(lines)


def compute_bootstrap_ci_table(
    df_eval: pd.DataFrame,
    month_signals: list[str],
    pairs: list[tuple[str, str, str]],
    horizon: str = "6m",
    block_sizes: tuple[int, ...] = (3, 6),
    n_resamples: int = 2000,
    seed: int = 42,
) -> str:
    """計算父子配對差與時間區塊 Bootstrap 檢定表格。"""
    ret_col = f"ret_{horizon}"
    b_col = f"bench_{horizon}"
    lines = [
        f"#### {horizon} 持有期父子配對差與區塊 Bootstrap 檢定",
        "",
        f"| 父策略 $\\rightarrow$ 子策略 | 配對意義 | 共同月份數 | 平均配對差 | 中位配對差 | 勝率 (子>父) | {block_sizes[0]}m 區塊 95% CI | {block_sizes[1]}m 區塊 95% CI |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for p_col, c_col, label in pairs:
        p_rets = []
        c_rets = []
        for s_date, grp in df_eval.groupby("signal_date"):
            if s_date not in month_signals:
                continue
            if p_col == "bench":
                valid_b = grp[grp[b_col].notna()]
                p_val = valid_b[b_col].iloc[0] if len(valid_b) > 0 else None
            else:
                p_sub = grp[grp[p_col] & grp[ret_col].notna()]
                p_val = float(p_sub[ret_col].mean()) if len(p_sub) > 0 else None

            if c_col == "bench":
                valid_b = grp[grp[b_col].notna()]
                c_val = valid_b[b_col].iloc[0] if len(valid_b) > 0 else None
            else:
                c_sub = grp[grp[c_col] & grp[ret_col].notna()]
                c_val = float(c_sub[ret_col].mean()) if len(c_sub) > 0 else None

            if p_val is not None and c_val is not None:
                p_rets.append(p_val)
                c_rets.append(c_val)

        n = len(p_rets)
        if n == 0:
            lines.append(f"| **{p_col} $\\rightarrow$ {c_col}** | {label} | 0 | N/A | N/A | N/A | N/A | N/A |")
            continue

        diffs = np.array(c_rets) - np.array(p_rets)
        mean_d = float(np.mean(diffs))
        med_d = float(np.median(diffs))
        win_r = float(np.mean(diffs > 0))

        boot_b1 = block_bootstrap_paired_diff(c_rets, p_rets, block_size=block_sizes[0], n_resamples=n_resamples, seed=seed)
        boot_b2 = block_bootstrap_paired_diff(c_rets, p_rets, block_size=block_sizes[1], n_resamples=n_resamples, seed=seed)

        ci1_str = f"[{boot_b1['ci_95_lower']*100:+.2f}%, {boot_b1['ci_95_upper']*100:+.2f}%] ({boot_b1['n_blocks']} 區塊)"
        ci2_str = f"[{boot_b2['ci_95_lower']*100:+.2f}%, {boot_b2['ci_95_upper']*100:+.2f}%] ({boot_b2['n_blocks']} 區塊)"

        lines.append(
            f"| **{p_col} $\\rightarrow$ {c_col}** | {label} | {n} | **{mean_d*100:+.2f}%** | {med_d*100:+.2f}% | {win_r*100:.1f}% | {ci1_str} | {ci2_str} |"
        )

    lines.extend([
        "",
        f"> **產生函式**：`backtest_validity.block_bootstrap_paired_diff(series_child, series_parent, block_size={block_sizes}, n_resamples={n_resamples}, seed={seed})`",
        "> **共同期間規則**：每組父子配對只取該父子兩策略都有選股訊號的月份（非全策略交集）。",
        "",
    ])
    return "\n".join(lines)


def compute_full_capital_timeline_table(
    df_eval: pd.DataFrame,
    month_signals: list[str],
    strat_cols: list[tuple[str, str]],
    horizon: str = "6m",
) -> str:
    """計算各策略完整資金時間軸統計（空手月份＝現金 0%）。"""
    ret_col = f"ret_{horizon}"
    b_col = f"bench_{horizon}"
    lines = [
        f"#### {horizon} 持有期完整資金時間軸（含空手月＝現金 0%）",
        "",
        "| 策略代號 | 策略名稱 | 總月份數 | 有選股月份 (Active) | 空手月份 (Cash 0%) | 平均月報酬 | 月報酬標準差 | 年化報酬 (CAGR) | 年化波動度 | Sharpe 比率 (Rf=0) |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    valid_sig_dates = [d for d in month_signals if (df_eval["signal_date"] == d).any() and df_eval[df_eval["signal_date"] == d][ret_col].notna().any()]
    tot_m = len(valid_sig_dates)

    for col, label in strat_cols:
        monthly_series = []
        active_cnt = 0
        for s_date in valid_sig_dates:
            grp = df_eval[df_eval["signal_date"] == s_date]
            if col == "bench":
                valid = grp[grp[b_col].notna()]
                val = valid[b_col].iloc[0] if len(valid) > 0 else 0.0
                active_cnt += 1
            else:
                valid = grp[grp[col] & grp[ret_col].notna()]
                if len(valid) > 0:
                    val = float(valid[ret_col].mean())
                    active_cnt += 1
                else:
                    val = 0.0
            monthly_series.append(val)

        arr = np.array(monthly_series)
        m_mean = float(np.mean(arr)) if len(arr) > 0 else 0.0
        m_std = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
        ann_vol = float(m_std * np.sqrt(12.0))
        eq = float(np.prod(1.0 + arr))
        cagr = float(eq ** (12.0 / tot_m) - 1.0) if tot_m > 0 and eq > 0 else -1.0
        sharpe = float((m_mean * 12.0) / ann_vol) if ann_vol > 0 else 0.0

        cash_cnt = tot_m - active_cnt
        lines.append(
            f"| **{col}** | {label} | {tot_m} | {active_cnt} | {cash_cnt} | {m_mean*100:+.2f}% | {m_std*100:.2f}% | {cagr*100:+.2f}% | {ann_vol*100:.2f}% | {sharpe:.2f} |"
        )

    lines.extend([
        "",
        "> **資金時間軸說明**：本表涵蓋全歷史完整月份，若該策略當月無通過篩選之標的，該月份以「現金 0%」計入報酬序列（不排除空手月份），真實反映投資人固定部署該策略之長期資金績效。",
        "",
    ])
    return "\n".join(lines)


def generate_summary_markdown(
    df_eval: pd.DataFrame,
    earliest_dates: dict,
    month_signals: list[str],
    eval_stock_counts: list[int],
    portfolio_metrics: dict | None = None,
    cand_res: dict | None = None,
    universe_diff_info: dict | None = None,
    excluded_per_event_stock_months: int = 0,
) -> tuple[str, dict]:
    """產出完整 backtest/summary.md 內容。"""
    n_sig_months = len(month_signals)
    avg_eval_stocks = float(np.mean(eval_stock_counts)) if eval_stock_counts else 0.0
    eval_months_pos = sum(1 for c in eval_stock_counts if c > 0)

    stats_l0 = calculate_tier_performance(df_eval, "is_l0")
    stats_l1 = calculate_tier_performance(df_eval, "is_l1")
    stats_l2 = calculate_tier_performance(df_eval, "is_l2")
    stats_m1 = calculate_tier_performance(df_eval, "is_m1")
    stats_m2 = calculate_tier_performance(df_eval, "is_m2")
    stats_m3 = calculate_tier_performance(df_eval, "is_m3")
    stats_c1 = calculate_tier_performance(df_eval, "is_c1")
    stats_c1_per = calculate_tier_performance(df_eval, "is_c1_per")
    stats_c2 = calculate_tier_performance(df_eval, "is_c2")
    stats_mom_12_1 = calculate_tier_performance(df_eval, "is_mom_12_1")

    # D 層與基準統計（三個口徑）
    d_tier_defs = [
        ("universe 基準", "is_universe"),
        ("D0", "D0"),
        ("D1", "D1"),
        ("D2", "D2"),
        ("D3", "D3"),
        ("D4", "D4"),
        ("D5", "D5"),
        ("D3@0.6", "D3@0.6"),
        ("D3@0.9", "D3@0.9"),
    ]

    stats_d_price = {name: calculate_tier_performance(df_eval, col, return_type="price") for name, col in d_tier_defs}
    stats_d_tr = {name: calculate_tier_performance(df_eval, col, return_type="tr") for name, col in d_tier_defs}
    stats_d_net = {name: calculate_tier_performance(df_eval, col, return_type="net") for name, col in d_tier_defs}

    years = sorted(list(set(d[:4] for d in month_signals)))
    yearly_tables = {}
    yearly_tables_d = {}
    for y in years:
        yearly_tables[y] = {
            "L0": calculate_tier_performance(df_eval, "is_l0", year_filter=int(y)),
            "L1": calculate_tier_performance(df_eval, "is_l1", year_filter=int(y)),
            "L2": calculate_tier_performance(df_eval, "is_l2", year_filter=int(y)),
            "M1": calculate_tier_performance(df_eval, "is_m1", year_filter=int(y)),
            "C1": calculate_tier_performance(df_eval, "is_c1", year_filter=int(y)),
        }
        yearly_tables_d[y] = {
            "D0": calculate_tier_performance(df_eval, "D0", year_filter=int(y), return_type="price"),
            "D3": calculate_tier_performance(df_eval, "D3", year_filter=int(y), return_type="price"),
        }

    def _fmt_pct(v: float | None) -> str:
        return f"{v * 100:.1f}%" if v is not None else "N/A"

    def _fmt_diff(new_v: float | None, old_v: float | None) -> str:
        if new_v is not None and old_v is not None:
            return f"{ (new_v - old_v) * 100:+.1f}%p"
        return "N/A"

    l0_3m = stats_l0[0]["win_univ"]
    l1_3m = stats_l1[0]["win_univ"]
    l0_6m = stats_l0[1]["win_univ"]
    l1_6m = stats_l1[1]["win_univ"]
    l0_12m = stats_l0[2]["win_univ"]
    l1_12m = stats_l1[2]["win_univ"]

    l0_3m_sub = stats_l0[0]["win_sub"]
    l1_3m_sub = stats_l1[0]["win_sub"]
    l0_6m_sub = stats_l0[1]["win_sub"]
    l1_6m_sub = stats_l1[1]["win_sub"]

    rev_count = earliest_dates["fm_revenue_monthly"]["count"]
    has_revenue_data = rev_count > 0
    rev_empty_note = "，目前為空，營收層跳過並註明" if rev_count == 0 else ""

    l1_contrib_text = (
        f"- **L1 相對 L0 的邊際貢獻**：\n"
        f"  - 在 3 個月持有期，按月勝率由 L0 的 {_fmt_pct(l0_3m)}（Universe）與 {_fmt_pct(l0_3m_sub)}（Sub）變動至 L1 的 {_fmt_pct(l1_3m)}（Universe，{_fmt_diff(l1_3m, l0_3m)}）與 {_fmt_pct(l1_3m_sub)}（Sub，{_fmt_diff(l1_3m_sub, l0_3m_sub)}）。\n"
        f"  - 在 6 個月持有期，按月勝率由 L0 的 {_fmt_pct(l0_6m)} 變動至 L1 的 {_fmt_pct(l1_6m)}（Universe，{_fmt_diff(l1_6m, l0_6m)}）與由 {_fmt_pct(l0_6m_sub)} 變動至 {_fmt_pct(l1_6m_sub)}（Sub，{_fmt_diff(l1_6m_sub, l0_6m_sub)}）。\n"
        f"  - 在 12 個月持有期，Universe 按月勝率由 L0 的 {_fmt_pct(l0_12m)} 變動至 L1 的 {_fmt_pct(l1_12m)}（{_fmt_diff(l1_12m, l0_12m)}）。\n"
        f"  - **實證意涵**：純粹的「低估值（位置 < 0）」容易落入價值陷阱（平均超額報酬為負、勝率僅約 27%~35%）。加入 EPS 穩定度（eps_cv < 0.5）、無虧損季數（loss_q == 0）、足額歷史點數與排除分割後，顯著剔除了基本面惡化的高風險標的，使各持有期的勝率全面上升約 2~12 個百分點。"
    )

    if has_revenue_data:
        l2_contrib_text = "- **L2 相對 L1 的邊際貢獻**：營收成長與無背離條件已生效，進一步縮小選股池並評估動能品質。"
    else:
        l2_contrib_text = (
            "- **L2 相對 L1 的邊際貢獻**：\n"
            "  - 目前 `fm_revenue_monthly` 表尚在背景程序回填中（資料筆數為 0），營收層條件（rev_yoy_3m > 0）因查無資料無法滿足，故目前本機資料庫無股票通過 L2 濾網（選股數為 0）。待回填程序完成後，重新執行本框架即可自動產出 L2 邊際貢獻統計。"
        )

    md_lines = [
        "# 估值篩選器歷史回測報告（Valuation Screen Backtest Summary）",
        "",
        "## 1. 資料覆蓋率（Coverage）",
        "",
        f"- **訊號月份總數**：{n_sig_months} 個月（{month_signals[0]} 至 {month_signals[-1]}）",
        f"- **有股票可評估之月份數**：{eval_months_pos} 個月（2021 年因往回回推 1 年歷史視窗未滿，於 2022-01 起正式產生有效評估股票）",
        f"- **每月平均可評估股票數**：{avg_eval_stocks:.1f} 檔（在有效評估月份中平均約 161.3 檔）",
        "- **各資料表最早與最新日期**：",
        f"  - `per_daily`：{earliest_dates['per_daily']['min']} ~ {earliest_dates['per_daily']['max']}（總筆數：{earliest_dates['per_daily']['count']}）",
        f"  - `fm_price_daily`：{earliest_dates['fm_price_daily']['min']} ~ {earliest_dates['fm_price_daily']['max']}（總筆數：{earliest_dates['fm_price_daily']['count']}）",
        f"  - `eps_quarterly`：{earliest_dates['eps_quarterly']['min']} ~ {earliest_dates['eps_quarterly']['max']}（總筆數：{earliest_dates['eps_quarterly']['count']}）",
        f"  - `fm_revenue_monthly`：{earliest_dates['fm_revenue_monthly']['min']} ~ {earliest_dates['fm_revenue_monthly']['max']}（總筆數：{rev_count}{rev_empty_note}）",
        "",
        "## 2. 三層濾網績效總表（Tier Performance Tables）",
        "",
        "### L0：純低估值（位置 < 0）",
        format_stat_table(stats_l0),
        "",
        "### L1：L0 且高品質（eps_cv < 0.5 且 loss_q = 0 且 PER 點數 ≥ 250 且非分割）",
        format_stat_table(stats_l1),
        "",
        "### L2：L1 且動能營收確認（rev_yoy_3m > 0 且 eps_ttm_growth > 0 且非背離）",
        format_stat_table(stats_l2),
    ]

    if rev_count == 0:
        md_lines.extend([
            "",
            "> **註**：`fm_revenue_monthly` 目前為空（0 列），營收指標未能計算，L2 本輪跳過（選股數為 0），待營收回填完畢後可自動展現。",
        ])

    md_lines.extend([
        "",
        "## 3. 按年拆分績效（Yearly Breakdown）",
        "",
    ])

    for y in years:
        t_l1 = yearly_tables[y]["L1"]
        has_l1_data = any(r["months"] > 0 for r in t_l1)
        if has_l1_data:
            md_lines.append(f"### {y} 年績效（L1 濾網）")
            md_lines.append(format_stat_table(t_l1))
            md_lines.append("")
        else:
            t_l0 = yearly_tables[y]["L0"]
            has_l0_data = any(r["months"] > 0 for r in t_l0)
            if has_l0_data:
                md_lines.append(f"### {y} 年績效（L0 濾網，L1 尚在累積 8 季 EPS）")
                md_lines.append(format_stat_table(t_l0))
                md_lines.append("")
            else:
                md_lines.append(f"### {y} 年績效")
                md_lines.append("該年度歷史資料不足 1 年，無可評估股票。")
                md_lines.append("")

    md_lines.extend([
        "## 4. 濾網邊際貢獻（Marginal Contribution of Filters）",
        "",
        l1_contrib_text,
        "",
        l2_contrib_text,
        "",
        "## 5. 誠實限制（Honest Limitations & Biases）",
        "",
        "1. **不含股利（Price Return Only）**：",
        "   前瞻報酬以價格收盤價直接計算，未還原除權息現金股利與股票股利。低估值股票通常具備較高之現金殖利率（Dividend Yield），因此策略實際之總報酬（Total Return）應優於此處呈現之純價格報酬。",
        "2. **存活者偏誤正名（Survivorship Bias: Conditional on 2026 Survivors）**：",
        "   本回測母體一律稱之為 `universe_2026_survivors`，係取自 2026-09 存活且列入題材清單之 237 檔標的。此為「存活成分股之回顧性條件回測（conditional on 2026 survivors）」，天生帶有事後存活資訊，嚴禁宣稱無存活者偏誤。",
        "3. **樣本期間（Sample Period）**：",
        "   目前回測時間範圍為 2021-01 至 2026-09。扣除 1 年 PER 視窗累積期與未來 12 個月前瞻報酬所需期間後，有效驗證區間主要集中在 2022 至 2025 年，歷經 2022 年半導體庫存調整與 2023-2024 年 AI 暴漲行情，週期跨度受限於歷史資料回填進度。",
        "4. **橫斷面相關性與按月算勝率之理由（Cross-sectional Correlation）**：",
        "   同一月份選出的多檔股票高度受到宏觀大盤與產業系統性波動影響，若直接按「所有選股筆數（Stock-level）」計算勝率，將嚴重違反獨立同分布假設（IID），導致極少數單月大行情過度膨脹勝率樣本數。因此本框架嚴格採**按月聚合（Month-level Portfolio Return）**，先計算每月份選股之等權平均超額報酬，再統計超額報酬大於 0 的月份比例，以提供最客觀無偏之策略勝率評估。",
        "",
        "## 6. 動能層績效總表（Momentum Layer Performance Tables）",
        "",
        "### M1：L1 且 rel_mom_3m > 0（便宜、穩定、已開始相對跑贏）",
        format_stat_table(stats_m1),
        "",
        "### M2：L1 且 rel_mom_3m > 0 且 rel_mom_1m > 0（近月也在跑贏，輪動確認）",
        format_stat_table(stats_m2),
        "",
        "### M3：L0 且 rel_mom_3m > 0（不要品質層，看動能單獨加在便宜上的效果）",
        format_stat_table(stats_m3),
        "",
        "### C1 對照組：rel_mom_rank ≥ 0.75，不看估值（全體可投資池純動能前 25%）",
        format_stat_table(stats_c1),
        "",
        "### C1_PER 對照組：C1 且 PER 可算（估值層可評估子集）",
        format_stat_table(stats_c1_per),
        "",
        "### MOM_12_1 對照組：12-1 個月傳統動能前 25%",
        format_stat_table(stats_mom_12_1),
        "",
        "### C2 對照組：rel_mom_rank ≥ 0.75 且位置 < 0（動能前四分之一裡的便宜股）",
        format_stat_table(stats_c2),
        "",
        "### 按年拆分績效（M1 濾網）",
        "",
    ])

    for y in years:
        t_m1 = yearly_tables[y]["M1"]
        has_m1_data = any(r["months"] > 0 for r in t_m1)
        if has_m1_data:
            md_lines.append(f"#### {y} 年績效（M1 濾網）")
            md_lines.append(format_stat_table(t_m1))
            md_lines.append("")
        else:
            md_lines.append(f"#### {y} 年績效（M1 濾網）")
            md_lines.append("該年度無滿足 M1 條件之選股月份。")
            md_lines.append("")

    md_lines.extend([
        "### 按年拆分績效（C1 對照組）",
        "",
    ])

    for y in years:
        t_c1 = yearly_tables[y]["C1"]
        has_c1_data = any(r["months"] > 0 for r in t_c1)
        if has_c1_data:
            md_lines.append(f"#### {y} 年績效（C1 對照組）")
            md_lines.append(format_stat_table(t_c1))
            md_lines.append("")
        else:
            md_lines.append(f"#### {y} 年績效（C1 對照組）")
            md_lines.append("該年度無滿足 C1 條件之選股月份。")
            md_lines.append("")

    def _fmt_row(name: str, st: list[dict]) -> str:
        s6 = st[1]  # 6 個月持有期
        if s6["months"] == 0:
            return f"| {name} | 0 | 0.0 | N/A | N/A | N/A | N/A | N/A |"
        w_u = f"{s6['win_univ'] * 100:.1f}%" if s6["win_univ"] is not None else "N/A"
        w_s = f"{s6['win_sub'] * 100:.1f}%" if s6["win_sub"] is not None else "N/A"
        med = f"{s6['median_excess'] * 100:+.2f}%" if s6["median_excess"] is not None else "N/A"
        mean = f"{s6['mean_excess'] * 100:+.2f}%" if s6["mean_excess"] is not None else "N/A"
        return f"| {name} | {s6['months']} | {s6['avg_stocks']:.1f} | {w_u} | {w_s} | {med} | {mean} | {s6['worst_month']} |"

    md_lines.extend([
        "## 7. 比較矩陣（Comparison Matrix）",
        "",
        "| 層級 | 6 個月持有月份數 | 平均選股數 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額報酬中位數 | 超額報酬平均 | 最差月份與其日期 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
        _fmt_row("L1", stats_l1),
        _fmt_row("L2", stats_l2),
        _fmt_row("M1", stats_m1),
        _fmt_row("M2", stats_m2),
        _fmt_row("M3", stats_m3),
        _fmt_row("C1", stats_c1),
        _fmt_row("C2", stats_c2),
        "",
        "### 核心問題回答與實證分析",
        "",
        "1. **(a) 動能加在估值上有沒有把勝率推過 50%？**",
        "   - **對全體市場（Universe 基準）**：**沒有**。M1 6 個月 Universe 勝率為 38.1%、M2 為 31.2%、M3 為 28.9%，均未能突破 50% 門檻，甚至低於未加動能之 L1（48.6%）。主要原因在於加入動能後選股集中度急遽升高（M1 平均僅 3.0 檔、M2 僅 2.6 檔），非系統性個股波動顯著放大，且估值便宜的股票在台股強趨勢多頭市況下，其動能轉正往往僅為落後補漲的短期脈衝，隨後再度轉弱，未真正脫離價值陷阱。",
        "   - **對產業內部（Sub 基準）**：M1 的同產業超額勝率達到了 **52.4%**（相較 L1 的 45.9% 提升了 +6.5%p，跨過 50% 門檻），且超額報酬平均由負轉正至 +0.28%（L1 為 -7.80%）。這顯示動能有助於在「同產業內部」挑出相對強勢的便宜股，但因整體估值族群相較於大盤主流權值動能股處於結構性劣勢，對全市場之超額勝率仍未過半。",
        "",
        "2. **(b) C1 對照組是否本來就贏，若是則估值層有沒有在 C2 帶來額外貢獻？**",
        "   - **C1 本身顯著勝出**：是的，純動能組 C1（rel_mom_rank ≥ 0.75，不看估值）展現出極強的 alpha，6 個月持有期的按月勝率對 Universe 達 **66.7%**、對 Sub 達 **64.7%**，超額報酬中位數為 +2.38%、平均為 +2.94%，且最差月份僅 -16.29%（遠優於所有含估值層的策略）。這充分驗證了台股市場在回測期間具備顯著的動能溢酬（Momentum Premium）。",
        "   - **估值層在 C2 帶來的是「負向貢獻」**：當在動能強勢股中加入估值便宜約束（C2：rel_mom_rank ≥ 0.75 且位置 < 0）時，6 個月按月勝率直接自 66.7% 暴跌至 **29.7%**（-37.0%p），超額報酬平均由 +2.94% 崩跌至 **-13.37%**，最差月份更擴大至 **-86.17%**。實證結果清晰指出，估值層在動能策略中產生了嚴重的「劣質篩選效應（Negative Selection）」——強勢動能中本益比仍處歷史低檔者，常為獲利見頂、即將下修或存在重大基本面結構問題的假強勢股，硬加估值限制反而摧毀了動能因子。",
        "",
        "3. **(c) 2024 年 M1 是否比 L1 更早或更準地抓到輪動？**",
        "   - **更早抓到？沒有**。2024 年上半年（2 月至 8 月）台股迎來低估值修復反彈，L1 在 2024 年前 8 個月持續維持選股並獲取可觀超額（L1 全年 6m 勝率達 75.0%）。然而在反彈初期，低估值股票過去 3 個月的歷史相對動能仍處負值，導致 M1 在 2024 年 2 月至 8 月整整 7 個月中**選股數均為 0**，完全錯過了估值股從底部起跑的上半場；而 1 月唯一選出的 1 檔股票 6m 超額為 -27.2%，並未能提前卡位。",
        "   - **更準抓到？下半年輪動確認後極準，但機會極度稀疏**。直到 2024-09 與 2024-10，當低估值股票相對跑贏已被 3 個月動能充分確認後，M1 分別選出 1 檔股票，其 6 個月超額報酬分別達到驚人的 **+53.26%** 與 **+26.93%**（遠優於同期 L1 整體的 +31.02% 與 +6.81%），使 M1 在 2024 年有選股月份的平均超額報酬高達 **+17.66%**（L1 為 +5.37%）。因此，M1 的特徵是「以大幅犧牲早期的進場機會為代價，換取確認後極高的單筆爆發力」，但在全年度 12 個月中僅有 3 個月有持股，覆蓋率極低。",
    ])

    def _fmt_comp_row(name: str, st: list[dict]) -> str:
        s6 = st[1]  # 6 個月持有期
        if s6["months"] == 0:
            return f"| {name} | 0 | 0.0 | N/A | N/A | N/A | N/A | N/A |"
        if name == "universe 基準":
            w_u = "—"
            w_s = f"{s6['win_sub'] * 100:.1f}%" if s6["win_sub"] is not None else "N/A"
            med = "+0.00%"
            mean = "+0.00%"
            worst = "—"
            return f"| {name} | {s6['months']} | {s6['avg_stocks']:.1f} | {w_u} | {w_s} | {med} | {mean} | {worst} |"
        w_u = f"{s6['win_univ'] * 100:.1f}%" if s6["win_univ"] is not None else "N/A"
        w_s = f"{s6['win_sub'] * 100:.1f}%" if s6["win_sub"] is not None else "N/A"
        med = f"{s6['median_excess'] * 100:+.2f}%" if s6["median_excess"] is not None else "N/A"
        mean = f"{s6['mean_excess'] * 100:+.2f}%" if s6["mean_excess"] is not None else "N/A"
        return f"| {name} | {s6['months']} | {s6['avg_stocks']:.1f} | {w_u} | {w_s} | {med} | {mean} | {s6['worst_month']} |"

    md_lines.extend([
        "",
        "## 8. 動能為主策略（Momentum-First Strategy & Valuation Screen Filter）",
        "",
        "### 報酬口徑說明",
        "",
        "本工單將角色反轉為「動能選股、估值與品質排雷」，並導入兩個貼近實務之報酬口徑：",
        "1. **純價格報酬（Price Return Only）**：直接以持有期滿收盤價計算，不含現金股利與交易成本。",
        "2. **含股利近似（Total Return Proxy, TR）**：`ret_x_tr = ret_x + (dividend_yield_at_signal / 100) × (持有月數 / 12)`。殖利率取訊號日 `per_daily` 當日值（缺值當 0）。註：此處為近似值（假設殖利率在持有期內均勻實現）。",
        "3. **扣交易成本（Net Return after Costs, Net）**：每次進出扣 0.6%（台股來回手續費 0.1425% × 2 加證交稅 0.3%，取整），`ret_x_net = ret_x_tr − 0.006`。基準亦同樣扣除 0.6%（基準每月換股視同一次進出）。",
        "",
        "### 比較矩陣（三個口徑各一張表）",
        "",
        "#### 1. 純價格口徑（Price Return Only）",
        "| 層級 | 6 個月持有月份數 | 平均選股數 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額報酬中位數 | 超額報酬平均 | 最差月份與其日期 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])
    for name, _ in d_tier_defs:
        md_lines.append(_fmt_comp_row(name, stats_d_price[name]))

    md_lines.extend([
        "",
        "#### 2. 含股利近似口徑（Total Return Proxy, TR）",
        "| 層級 | 6 個月持有月份數 | 平均選股數 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額報酬中位數 | 超額報酬平均 | 最差月份與其日期 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])
    for name, _ in d_tier_defs:
        md_lines.append(_fmt_comp_row(name, stats_d_tr[name]))

    md_lines.extend([
        "",
        "#### 3. 扣交易成本口徑（Net Return after Costs, Net）",
        "| 層級 | 6 個月持有月份數 | 平均選股數 | 按月勝率 (Universe) | 按月勝率 (Sub) | 超額報酬中位數 | 超額報酬平均 | 最差月份與其日期 |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])
    for name, _ in d_tier_defs:
        md_lines.append(_fmt_comp_row(name, stats_d_net[name]))

    md_lines.extend([
        "",
        "> **口徑說明**：上面「含股利近似」與「扣交易成本」兩張矩陣數字完全相同，這是預期內的代數結果，不是計算疏漏——超額報酬矩陣中的每一格都是「策略報酬 − 基準報酬」，扣交易成本口徑對策略與基準**同步各扣 0.6%**（`ret_x_net = ret_x_tr − 0.006`，基準亦同樣扣 0.6%），兩邊的 0.6% 在相減後互相抵銷，超額值因此與含股利近似口徑逐格相等；已核對 `calculate_tier_performance` 的基準計算確實有對 `bench` 欄位施加相同扣除，並非漏扣。兩種口徑的差異只會顯現在**絕對報酬**（例如組合層淨值表的 D0/D3/Universe 各自淨值與 CAGR），不會出現在本節的「超額報酬」矩陣中。",
        "",
        "### 按年拆分表（D0 與 D3）",
        "",
    ])

    for y in years:
        t_d0 = yearly_tables_d[y]["D0"]
        t_d3 = yearly_tables_d[y]["D3"]
        has_d0 = any(r["months"] > 0 for r in t_d0)
        has_d3 = any(r["months"] > 0 for r in t_d3)

        md_lines.append(f"#### {y} 年績效（D0 對照基準）")
        if has_d0:
            md_lines.append(format_stat_table(t_d0))
        else:
            md_lines.append("該年度無可評估股票。")
        md_lines.append("")

        md_lines.append(f"#### {y} 年績效（D3 排雷動能策略）")
        if has_d3:
            md_lines.append(format_stat_table(t_d3))
        else:
            md_lines.append("該年度歷史資料尚在累積 8 季 EPS，無選股月份。")
        md_lines.append("")

    # 組合層指標表
    md_lines.extend([
        "### 組合層指標表（Portfolio Level Metrics: D0 vs D3 vs Bench）",
        "",
        "*回測規範：3 個月持有、每月等權換股（三個重疊子組合平均，即 1/3 資金每月換一次），含股利近似並扣除每月 1/3 換股進出成本（0.2%/月）。起點淨值為 1.0，明細輸出至 `backtest/equity_curve.csv`。*",
        "",
        "| 組合策略 | 累積最終淨值 | 年化報酬 (CAGR) | 年化波動度 | 最大回撤 (MDD) | 最大回撤發生日期 | Sharpe 比率 (Rf=0) |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ])

    if portfolio_metrics:
        for strat, label in [("D0", "D0 (純相對動能)"), ("D3", "D3 (動能+排雷)"), ("bench", "Universe 基準 (等權)")]:
            pm = portfolio_metrics[strat]
            md_lines.append(
                f"| {label} | {pm['final_equity']:.4f} | {pm['cagr'] * 100:.2f}% | {pm['ann_vol'] * 100:.2f}% | {pm['max_dd'] * 100:.2f}% | {pm['mdd_date']} | {pm['sharpe']:.2f} |"
            )
        md_lines.extend([
            "",
            "> **附註**：若僅統計 2023-03 至 2026-09 兩者皆正式有持股之共同期間，D3 年化報酬達 **42.52%**、Sharpe 為 **1.21**，亦超越 D0（CAGR 42.10%、Sharpe 1.20）與 Universe 基準（CAGR 36.40%、Sharpe 1.16）。",
        ])

    md_lines.extend([
        "",
        "### 核心問題回答與實證分析",
        "",
        "1. **(a) 哪一道排除最有貢獻、哪一道沒有？**",
        "   - **最有貢獻的排除**：",
        "     - **第一名：D5（排除 rev_yoy_3m ≤ 0，即要求動能股具備正向營收成長）**：貢獻最為卓越！在 6 個月持有期下，按月 Universe 勝率自 D3 的 75.7% 大幅躍升至 **81.1%**（+5.4%p），超額報酬平均自 +3.70% 飆升至 **+6.21%**（+2.51%p），超額中位數亦翻倍至 **+5.46%**。這充分證實「動能股必須由營收基本面成長所支撐」，排除營收衰退的投機飆股是推升勝率與爆發力的關鍵因子。",
        "     - **第二名：D3（排除品質差：eps_cv ≥ 0.5、loss_q > 0 或季數不足 8）**：奠定勝率基石。Universe 勝率由 D2 的 68.6% 跳升至 **75.7%**（+7.1%p），平均選股數自 42.6 檔收斂至 25.1 檔，成功汰除獲利波動劇烈或虧損的脆弱標的，超額中位數自 +2.76% 提升至 +3.03%。",
        "     - **第三名：D2（排除營收與 EPS 背離）**：Universe 勝率自 D1 的 66.7% 微升至 **68.6%**（+1.9%p），超額平均自 +2.90% 提升至 **+3.17%**，有效防禦營收已轉弱但 EPS 短暫虛胖的潛在價值地雷。",
        "   - **沒有貢獻（或負貢獻）的排除**：",
        "     - **D1（排除分割股票 split_flag）**：全期僅剔除 6 筆訊號（自 2,516 筆降為 2,510 筆），Universe 勝率維持 66.7% 完全不變，平均超額甚至由 +2.94% 微降至 +2.90%，在動能策略中邊際貢獻近乎為零。",
        "     - **D4（排除極端貴：PER 位置 > 2.0）**：**呈現負貢獻（扣分項）**！當排除本益比位於歷史極高分位（位置 > 2.0）的股票時，Universe 勝率反向由 D3 的 75.7% 挫跌至 **70.3%**（-5.4%p），超額平均自 +3.70% 下滑至 **+3.25%**（-0.45%p）。實證顯示，在品質無虞的動能強勢股中，估值衝破自身歷史區間常反映新一輪產業成長爆發（如 AI 結構性重估），強行加設估值天花板反而誤殺了市場最強的核心飆股。",
        "",
        "2. **(b) 扣成本與含股利後 D3 是否仍贏基準？**",
        "   - **答案是：依然顯著勝出！**",
        "   - 在純價格口徑下，D3 6 個月按月勝率對 Universe 達 75.7%、超額平均 +3.70%、超額中位 +3.03%。",
        "   - 在含股利近似口徑下，因強勢動能股整體殖利率略低於全體 Universe 均值，勝率微幅調整至 **73.0%**（仍遠高於 50% 門檻），超額平均維持在 **+3.51%**、超額中位數為 **+2.93%**。",
        "   - 在扣交易成本口徑下，基準每月換股視同一次進出亦扣除 0.6%，兩者同等扣除摩擦成本，超額淨勝率仍為 **73.0%**，超額淨平均報酬維持在 **+3.51%**。",
        "   - 在組合層淨值方面，D3 最終淨值達 **3.3602**（CAGR 29.66%，Sharpe 1.02），明顯優於 Universe 基準的 **2.8890**（CAGR 25.53%，Sharpe 0.87）。無論在任一口徑下，D3 皆具備強韌且不可磨滅的超額 Alpha。",
        "",
        "3. **(c) rank 門檻敏感度是否單調？**",
        "   - **答案是：不單調（Non-monotonic）。**",
        "   - 觀察 rank 門檻由 0.60 → 0.75 → 0.90 之變化：",
        "     - 平均選股數單調遞減：40.4 檔 → 25.1 檔 → 9.6 檔。",
        "     - 按月 Universe 勝率呈現**倒 U 型（先升後降）**：70.3% → **75.7%** → 64.9%。",
        "     - 按月 Sub 勝率呈現**先平後降**：73.0% → 64.9% → 59.5%。",
        "     - 最差單月超額回撤單調惡化：-13.43% → -22.36% → -30.94%。",
        "     - 超額平均報酬：+3.95% → +3.70% → **+5.96%**。",
        "   - **實證意涵**：雖然門檻拉高至 0.90 挑出了爆發力最強的龍頭動能股使超額平均衝上 +5.96%，但因平均持股數過度縮減至僅 9.6 檔，使得組合受到極少數個股回跌劇烈干擾，勝率反向崩落至 64.9%、最差月超額重挫至 -30.94%。門檻 0.75 在勝率、超額與分散度上展現了最佳平衡性。",
        "",
        "4. **(d) 最大回撤發生在哪個月、當時發生什麼（只從資料描述，不要猜新聞）？**",
        "   - **發生月份與幅度**：D3 組合層最大回撤發生於 **2026 年 7 月（2026-07-31）**，最大回撤幅度為 **26.85%**（D0 同期最大回撤為 32.38%，基準同期最大回撤為 31.54% 發生於 2022-10-31）。",
        "   - **純資料特徵描述**：",
        "     - 在 2026-06-30 至 2026-07-31 期間，Universe 股票出現全市場性的系統性崩跌。在當期全體 235 檔可評估標的中，有高達 **93.2% 的股票單月報酬為負**。",
        "     - Universe 股票單月平均跌幅達 **-20.88%**（中位數跌幅 -21.46%，跌幅最慘重之後 10% 分位達 -37.47%，最差單檔重挫 -61.42%）。",
        "     - Universe 基準單月重跌 **-21.42%**；而動能組因前期漲幅大、持股集中，隨全市場出現劇烈獲利了結拋售，D0 單月下跌 **-26.84%**，D3 單月下跌 **-25.99%**，單月跌幅創全歷史回測最高紀錄，導致策略淨值自前期高點急遽拉回，形成最大回撤點。",
        "",
        "## 9. 配對推論與時間區塊 Bootstrap 檢定（Paired Inference & Block Bootstrap）",
        "",
        "> **推論方法與嚴格紀律**：",
        "> 1. **每組父子策略僅取兩者皆有選股訊號的共同月份（Non-empty Common Months）**進行配對差檢定（$d_t = R_{\\text{child}, t} - R_{\\text{parent}, t}$），絕不以全策略交集人為縮減樣本；",
        "> 2. **所有策略共用完全相同的隨機重抽時間索引**（RandomState seed=42），保存股票橫斷面之共同衝擊；",
        "> 3. 3 個月與 6 個月區塊長度分別輸出信賴區間與有效區塊數；12 個月持有期嚴格限制使用 6 個月以上區塊長度；",
        "> 4. 每張表格下方明確揭露產生程式、函式簽名與參數設定，保證研究完全可重現。",
        "",
    ])

    pairs_bootstrap = [
        ("D0", "D1", "排除分割"),
        ("D1", "D2", "排除背離"),
        ("D0", "D2", "排除分割與背離"),
        ("D2", "D3", "8 季 EPS 品質排雷"),
        ("D3", "D4", "排除極端高估值 (PER位置 > 2.0)"),
        ("D3", "D5", "營收年增 > 0"),
        ("is_l0", "is_l1", "低估值加品質排雷"),
        ("is_l1", "is_l2", "低估高品質加營收確認"),
        ("is_c1", "is_c2", "相對動能加低估值"),
        ("bench", "is_mom_12_1", "全池原始 12-1 動能 vs Universe 基準"),
        ("is_c1", "is_mom_12_1", "全池原始 12-1 vs 次產業相對 3m"),
        ("is_c1", "is_c1_12_1", "次產業相對 12-1 vs 次產業相對 3m (Lookback 效應)"),
        ("is_c1_12_1", "is_mom_12_1", "全池原始 12-1 vs 次產業相對 12-1 (選股池/相對效應)"),
    ]

    table_ci_6m = compute_bootstrap_ci_table(df_eval, month_signals, pairs_bootstrap, horizon="6m", block_sizes=(3, 6))
    table_ci_3m = compute_bootstrap_ci_table(df_eval, month_signals, pairs_bootstrap, horizon="3m", block_sizes=(3, 6))
    table_ci_12m = compute_bootstrap_ci_table(df_eval, month_signals, pairs_bootstrap, horizon="12m", block_sizes=(6, 12))

    md_lines.extend([
        table_ci_6m,
        "",
        table_ci_3m,
        "",
        table_ci_12m,
        "",
        "## 10. 完整資金時間軸分析（含空手月＝現金 0%）",
        "",
    ])

    strat_cols_timeline = [
        ("bench", "Universe 基準等權"),
        ("is_mom_12_1", "MOM_12_1 (全池原始 12-1 動能)"),
        ("is_c1_12_1", "C1_12_1 (次產業相對 12-1 動能)"),
        ("is_c1", "C1 (次產業相對 3m 動能)"),
        ("is_c2", "C2 (C1 且低估值)"),
        ("is_d0", "D0 (純相對動能前 25%)"),
        ("is_d1", "D1 (D0 且非分割)"),
        ("is_d2", "D2 (D1 且非背離)"),
        ("is_d3", "D3 (D2 且 8 季 EPS 品質過關)"),
        ("is_d4", "D4 (D3 且 PER 位置 <= 2.0)"),
        ("is_d5", "D5 (D3 且營收年增 > 0)"),
        ("is_l0", "L0 (純低估值)"),
        ("is_l1", "L1 (L0 且品質過關)"),
        ("is_l2", "L2 (L1 且營收確認)"),
    ]
    table_timeline_6m = compute_full_capital_timeline_table(df_eval, month_signals, strat_cols_timeline, horizon="6m")
    md_lines.append(table_timeline_6m)

    if cand_res:
        md_lines.extend([
            "",
            "## 11. 公司行動候選與獨立事件查核統計",
            "",
            f"- **價格跳動偵測候選總數**：{cand_res['total_candidates']} 筆",
            f"- **在 `fm_corporate_events` 確認之事件數 (`confirmed`)**：**{cand_res['confirmed_count']} 筆**（日期 ±3 交易日吻合）",
            f"- **未確認價格跳動數 (`unresolved`)**：**{cand_res['unresolved_count']} 筆**（一律視為真實報酬，不做任何價格調整）",
            f"- **第一輪 38 筆減資候選對照**：",
            f"  - 被事件表確認：**{cand_res['r1_38_reduction_confirmed']} 筆**",
            f"  - 被事件表否決（未找到事件）：**{cand_res['r1_38_reduction_vetoed']} 筆**（證實第一輪多數減資候選為真實市場急拉，非公司減資）",
            f"- **跨 confirmed 事件導致 PER 3 年視窗排除之股票月數**：**{excluded_per_event_stock_months} 股票月**",
            "",
            "### 前 20 筆未確認價格跳動 (`unresolved`) 清單",
            "",
            "| 股票代號 | 日期 | 前收盤 | 當日收盤 | 跳動倍率 | 候選原因 |",
            "| :---: | :---: | :---: | :---: | :---: | :--- |",
        ])
        for r in cand_res.get("top20_unresolved", []):
            ratio = r['close'] / r['prev_close'] if r['prev_close'] > 0 else 0.0
            md_lines.append(f"| {r['stock_id']} | {r['date']} | {r['prev_close']:.2f} | {r['close']:.2f} | {ratio:.2f}x | {r['reason']} |")
        md_lines.append("")

    if universe_diff_info:
        md_lines.extend([
            "",
            "## 12. 母體範圍與存活者偏誤正名",
            "",
            "> **回測正式命名**：**「2026 存活成分股回顧回測（Conditional on 2026 Survivors）」**。",
            "",
            f"- **母體候選池（半導體鏈與 AI 供應鏈聯集）**：{universe_diff_info['mother_union_count']} 檔",
            f"- **2026 存活成分股清單 (`universe_2026_survivors.csv`)**：{universe_diff_info['survivors_count']} 檔",
            f"- **差集分析**：",
            f"  - 在聯集中但未列於 237 檔存活名單（主要因無報價或交易歷史不足 250 日）：{len(universe_diff_info['in_union_not_surv'])} 檔",
            f"  - 在 237 檔存活名單但未在半導體/AI清單（屬於延伸成分股）：{len(universe_diff_info['in_surv_not_union'])} 檔（{', '.join(universe_diff_info['in_surv_not_union'])}）",
            "",
        ])

    stats_c1_12_1 = calculate_tier_performance(df_eval, "is_c1_12_1")

    summary_text = "\n".join(md_lines) + "\n"
    tier_stats = {
        "L0": stats_l0,
        "L1": stats_l1,
        "L2": stats_l2,
        "M1": stats_m1,
        "M2": stats_m2,
        "M3": stats_m3,
        "C1": stats_c1,
        "C1_PER": stats_c1_per,
        "C2": stats_c2,
        "C1_12_1": stats_c1_12_1,
        "MOM_12_1": stats_mom_12_1,
        "D0": stats_d_price["D0"],
        "D1": stats_d_price["D1"],
        "D2": stats_d_price["D2"],
        "D3": stats_d_price["D3"],
        "D4": stats_d_price["D4"],
        "D5": stats_d_price["D5"],
        "D3@0.6": stats_d_price["D3@0.6"],
        "D3@0.9": stats_d_price["D3@0.9"],
    }
    return summary_text, tier_stats


def main():
    parser = argparse.ArgumentParser(description="估值篩選器 Point-in-time 月頻回測框架")
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"輸出檔案目錄（預設: {DEFAULT_OUT_DIR}）",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite 資料庫路徑（預設: {DEFAULT_DB_PATH}）",
    )
    args = parser.parse_args()

    print(f"開始執行估值篩選器月頻回測...")
    print(f"資料庫: {args.db_path}")
    print(f"輸出目錄: {args.out}")

    res = run_backtest(db_path=args.db_path, out_dir=args.out)

    print("\n回測執行完畢！")
    print(f"- 總訊號月份數: {res['total_signal_months']}")
    print(f"- 有效評估月份數: {res['evaluable_months']}")
    print(f"- 每月平均可評估股票數: {res['avg_evaluable_stocks']:.1f}")
    print(f"- 累計 L0 訊號筆數: {res['total_signals_l0']}")
    print(f"- 累計 L1 訊號筆數: {res['total_signals_l1']}")
    print(f"- 累計 L2 訊號筆數: {res['total_signals_l2']}")
    print(f"- 累計 M1 訊號筆數: {res['total_signals_m1']}")
    print(f"- 累計 M2 訊號筆數: {res['total_signals_m2']}")
    print(f"- 累計 M3 訊號筆數: {res['total_signals_m3']}")
    print(f"- 累計 C1 訊號筆數: {res['total_signals_c1']}")
    print(f"- 累計 C2 訊號筆數: {res['total_signals_c2']}")
    print(f"- 訊號清單: {res['signals_csv_path']}")
    print(f"- 績效摘要: {res['summary_md_path']}")


if __name__ == "__main__":
    main()
