# -*- coding: utf-8 -*-
"""回測研究有效性修復模組（Point-in-time 與量測修復）

依據 docs/tasks/backtest-validity-repair.md 與 Codex 審查報告：
- A: 訊號時點與成交時點分離（完整交易月、T+1 成交、不可成交處理）
- B: 公司行動還原（雙向偵測、日報酬連乘、事件日保留真實波動）
- C: 持有期報酬與缺值語意（訊號日初始成員固定、最後可得價結算）
- D: 統一母體與缺值（250日/200筆門檻、上市滿一年、連續8季EPS、連續3月營收、法人NA）
- E: 組合層重算（漂移、實際買賣各扣0.3%淨額）
- F: 同期配對比較與區塊 Bootstrap
- G: 執行面測試對照標準
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_OUT_DIR = Path(__file__).parent / "backtest"


# ---------------------------------------------------------------------------
# A. 完整交易月與時點分離輔助函式
# ---------------------------------------------------------------------------

def is_month_complete(
    year_month: str,
    all_trading_dates: list[str],
) -> bool:
    """判斷某個 YYYY-MM 是否為完整交易月。
    規則：
    1. 若資料庫中存在嚴格晚於該月份的交易日，則該月份所有交易日皆已收錄，判定為完整交易月。
    2. 若該月份為資料庫中最後一個月份，則檢查其最大交易日是否接近該月曆月底（距最後一天 <= 3 天）。
       例如 2026-09-04（9月第4天）非完整月，回傳 False。
    """
    ym_dates = [d for d in all_trading_dates if d.startswith(year_month)]
    if not ym_dates:
        return False

    later_dates = [d for d in all_trading_dates if d > ym_dates[-1] and not d.startswith(year_month)]
    if later_dates:
        return True

    # 最後一個月份：檢查最後一個日期是否為月底
    max_d = ym_dates[-1]
    parts = year_month.split("-")
    y, m = int(parts[0]), int(parts[1])
    last_day_of_month = calendar.monthrange(y, m)[1]
    last_cal_date = datetime(y, m, last_day_of_month)
    cur_date = datetime.strptime(max_d, "%Y-%m-%d")

    # 若距月底小於等於 3 日（涵蓋五六日休市情況），視為月底收盤
    if (last_cal_date - cur_date).days <= 3:
        return True
    return False


def filter_complete_month_signals(
    all_trading_dates: list[str],
) -> list[str]:
    """從所有交易日中篩選出『完整交易月』的月末訊號日清單。
    排除如 2026-09-04 這種月內未完成快照。
    """
    df = pd.DataFrame({"date": all_trading_dates, "ym": [d[:7] for d in all_trading_dates]})
    month_max = df.groupby("ym")["date"].max().reset_index()

    complete_signals = []
    for _, row in month_max.iterrows():
        ym = row["ym"]
        max_date = row["date"]
        if is_month_complete(ym, all_trading_dates):
            complete_signals.append(max_date)

    return sorted(complete_signals)


def get_next_trading_day(
    target_date: str,
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
) -> str | None:
    """取得目標日的下一個交易日（T+1）。"""
    if target_date not in date_to_idx:
        for d in all_trading_dates:
            if d > target_date:
                return d
        return None
    idx = date_to_idx[target_date]
    if idx + 1 < len(all_trading_dates):
        return all_trading_dates[idx + 1]
    return None


def get_target_execution_date(
    sig_date: str,
    horizon_months: int,
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
) -> tuple[str | None, bool]:
    """計算到期預定執行日（共同市場交易日）。
    到期月份為日曆月加 horizon_months。
    預定執行日為到期月份最後一個市場交易日的次一共同交易日（T+1）。
    若預定執行日超出資料最後日，或到期月未涵蓋完整交易月，回傳 (None, True) 表示 right_censored。
    """
    parts = sig_date.split("-")
    y, m = int(parts[0]), int(parts[1])
    target_m = m + horizon_months
    target_y = y + (target_m - 1) // 12
    target_m = (target_m - 1) % 12 + 1

    month_str = f"{target_y:04d}-{target_m:02d}"
    trading_in_month = [dt for dt in all_trading_dates if dt.startswith(month_str)]
    if not trading_in_month:
        return None, True

    last_trading_in_month = trading_in_month[-1]
    # 檢查是否為完整交易月（資料庫中必須有嚴格晚於該月的交易日）
    later_dates = [dt for dt in all_trading_dates if dt > last_trading_in_month and not dt.startswith(month_str)]
    if not later_dates:
        return None, True

    idx = date_to_idx.get(last_trading_in_month)
    if idx is None or idx + 1 >= len(all_trading_dates):
        return None, True

    target_exec_date = all_trading_dates[idx + 1]
    return target_exec_date, False


def resolve_execution_price(
    prices_dict: dict[str, float],
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    ideal_date: str,
    max_shift_days: int = 3,
) -> tuple[float | None, str | None, str]:
    """不可成交政策解析執行價格。
    在 ideal_date 當天或之後最多 max_shift_days 個交易日內尋找第一個可成交日。
    不可成交判定：
    - 當日無價格（停牌）
    - 收盤價鎖死：|close/prev - 1| >= 0.095
    回傳：(exec_price, exec_date, status)
    status: 'ok', 'shifted', 'untradeable_no_price', 'untradeable_limit_locked'
    """
    if ideal_date not in date_to_idx:
        return (None, None, "untradeable_no_price")

    start_idx = date_to_idx[ideal_date]
    last_reason = "untradeable_no_price"

    for offset in range(max_shift_days + 1):
        cur_idx = start_idx + offset
        if cur_idx >= len(all_trading_dates):
            break
        d = all_trading_dates[cur_idx]
        if d not in prices_dict:
            last_reason = "untradeable_no_price"
            continue
        p = prices_dict[d]
        if p <= 0:
            last_reason = "untradeable_no_price"
            continue

        # 檢查漲跌停鎖死（以 |close/prev - 1| >= 0.095 判定）
        if cur_idx > 0:
            prev_d = all_trading_dates[cur_idx - 1]
            if prev_d in prices_dict and prices_dict[prev_d] > 0:
                prev_p = prices_dict[prev_d]
                if abs(p / prev_p - 1.0) >= 0.095:
                    last_reason = "untradeable_limit_locked"
                    continue

        status = "ok" if offset == 0 else "shifted"
        return (p, d, status)

    return (None, None, last_reason)


# ---------------------------------------------------------------------------
# B. 公司行動雙向偵測與還原
# ---------------------------------------------------------------------------

COMMON_SPLIT_RATIOS = [2.0, 3.0, 4.0, 5.0, 10.0]
COMMON_REDUCTION_RATIOS = [1.25, 1.33, 1.43, 1.5, 1.67, 2.0, 2.5, 3.0]

def detect_corporate_actions_for_stock(
    price_rows_sorted: list[tuple[str, float]],
    stock_id: str = "",
) -> list[dict]:
    """雙向偵測單檔股票的所有公司行動事件。
    向下（分割）：close/prev < 0.6 且 prev/close 接近 2, 3, 4, 5, 10 (±5%) -> S = round(prev/close)
    向上（減資）：close/prev > 1.2 且 close/prev 接近 1.25, 1.33, 1.43, 1.5, 1.67, 2.0, 2.5, 3.0 (±5%)
                 -> S = 1.0 / target（保留當日真實波動，不抹平成0）
    """
    actions = []
    for i in range(1, len(price_rows_sorted)):
        d_prev, p_prev = price_rows_sorted[i - 1]
        d_cur, p_cur = price_rows_sorted[i]
        if p_prev <= 0 or p_cur <= 0:
            continue

        ratio = p_cur / p_prev

        # 向下（分割）
        if ratio < 0.6:
            mult = p_prev / p_cur
            for target in COMMON_SPLIT_RATIOS:
                if abs(mult - target) / target <= 0.05:
                    S = float(round(mult))
                    actions.append({
                        "stock_id": stock_id,
                        "date": d_cur,
                        "prev_close": p_prev,
                        "close": p_cur,
                        "direction": "down",
                        "multiplier": S,
                        "reason": f"split_{target:g}x",
                    })
                    break

        # 向上（減資換發新股）
        elif ratio > 1.2:
            mult = p_cur / p_prev
            for target in COMMON_REDUCTION_RATIOS:
                if abs(mult - target) / target <= 0.05:
                    S = float(1.0 / target)
                    actions.append({
                        "stock_id": stock_id,
                        "date": d_cur,
                        "prev_close": p_prev,
                        "close": p_cur,
                        "direction": "up",
                        "multiplier": S,
                        "reason": f"reduction_{target:g}x",
                    })
                    break

    return actions


def generate_corporate_action_candidates(
    conn: sqlite3.Connection,
    out_dir: Path,
) -> dict:
    """雙向偵測全市場價格跳動候選，並對照 fm_corporate_events (±3 交易日)。
    產生 backtest/corporate_action_candidates.csv，欄位加 status (confirmed / unresolved)。
    unresolved 的跳動一律視為真實報酬，不做任何還原。
    回傳統計數據字典。"""
    df_price = pd.read_sql("SELECT stock_id, date, close FROM fm_price_daily WHERE close > 0 ORDER BY stock_id, date", conn)
    df_price["stock_id"] = df_price["stock_id"].astype(str).str.zfill(4)

    try:
        df_events = pd.read_sql("SELECT stock_id, date, event_type, before_price, after_price, ratio, source FROM fm_corporate_events", conn)
        df_events["stock_id"] = df_events["stock_id"].astype(str).str.zfill(4)
    except Exception:
        df_events = pd.DataFrame(columns=["stock_id", "date", "event_type", "before_price", "after_price", "ratio", "source"])

    trading_dates = sorted(df_price["date"].unique().tolist())
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    def _match(stock_id: str, cand_date: str) -> tuple[str, str | None, float | None, str | None, str | None]:
        if cand_date not in date_to_idx:
            cand_dt = datetime.strptime(cand_date, "%Y-%m-%d")
            valid_dates = [d for d in trading_dates if abs((datetime.strptime(d, "%Y-%m-%d") - cand_dt).days) <= 5]
        else:
            idx = date_to_idx[cand_date]
            min_idx = max(0, idx - 3)
            max_idx = min(len(trading_dates) - 1, idx + 3)
            valid_dates = trading_dates[min_idx : max_idx + 1]

        matches = df_events[(df_events["stock_id"] == stock_id) & (df_events["date"].isin(valid_dates))]
        if not matches.empty:
            r = matches.iloc[0]
            return "confirmed", r["event_type"], float(r["ratio"]) if r["ratio"] is not None else None, r["source"], r["date"]
        return "unresolved", None, None, None, None

    candidates = []
    for sid, grp in df_price.groupby("stock_id"):
        p_rows = list(zip(grp["date"], grp["close"]))
        for i in range(1, len(p_rows)):
            d_prev, p_prev = p_rows[i - 1]
            d_cur, p_cur = p_rows[i]
            if p_prev <= 0 or p_cur <= 0:
                continue
            ratio = p_cur / p_prev

            if ratio < 0.6:
                mult = p_prev / p_cur
                for target in COMMON_SPLIT_RATIOS:
                    if abs(mult - target) / target <= 0.05:
                        status, ev_type, ev_ratio, src, ev_date = _match(sid, d_cur)
                        candidates.append({
                            "stock_id": sid, "date": d_cur, "prev_close": p_prev, "close": p_cur,
                            "direction": "down", "multiplier": float(round(mult)), "reason": f"split_{target:g}x",
                            "status": status, "event_type": ev_type, "event_ratio": ev_ratio, "event_source": src, "event_date": ev_date,
                        })
                        break
            elif ratio > 1.2:
                mult = p_cur / p_prev
                for target in COMMON_REDUCTION_RATIOS:
                    if abs(mult - target) / target <= 0.05:
                        status, ev_type, ev_ratio, src, ev_date = _match(sid, d_cur)
                        candidates.append({
                            "stock_id": sid, "date": d_cur, "prev_close": p_prev, "close": p_cur,
                            "direction": "up", "multiplier": float(1.0 / target), "reason": f"reduction_{target:g}x",
                            "status": status, "event_type": ev_type, "event_ratio": ev_ratio, "event_source": src, "event_date": ev_date,
                        })
                        break

    cand_cols = [
        "stock_id", "date", "prev_close", "close", "direction", "multiplier",
        "reason", "status", "event_type", "event_ratio", "event_source", "event_date",
    ]
    df_cand = pd.DataFrame(candidates, columns=cand_cols)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "corporate_action_candidates.csv"
    df_cand.to_csv(out_path, index=False, encoding="utf-8")

    n_confirmed = int((df_cand["status"] == "confirmed").sum()) if not df_cand.empty else 0
    n_unresolved = int((df_cand["status"] == "unresolved").sum()) if not df_cand.empty else 0

    # 對照第一輪 38 筆減資候選
    r1_file = out_dir / "corporate_actions_detected.csv"
    r1_confirmed = 0
    r1_vetoed = 0
    if r1_file.exists():
        df_r1 = pd.read_csv(r1_file)
        df_r1_red = df_r1[df_r1["direction"] == "up"]
        for _, r in df_r1_red.iterrows():
            st, _, _, _, _ = _match(str(r["stock_id"]).zfill(4), str(r["date"]))
            if st == "confirmed":
                r1_confirmed += 1
            else:
                r1_vetoed += 1

    top20_unresolved = df_cand[df_cand["status"] == "unresolved"].head(20).to_dict(orient="records") if not df_cand.empty else []

    return {
        "total_candidates": len(df_cand),
        "confirmed_count": n_confirmed,
        "unresolved_count": n_unresolved,
        "r1_38_reduction_confirmed": r1_confirmed,
        "r1_38_reduction_vetoed": r1_vetoed,
        "top20_unresolved": top20_unresolved,
        "candidates_csv": str(out_path),
    }


def compute_holding_return_adjusted(
    price_rows_sorted: list[tuple[str, float]],
    entry_date: str,
    exit_date: str,
    corp_actions_map: dict[str, float],
) -> tuple[float | None, str]:
    """計算還原公司行動後的持有期報酬。
    以日報酬連乘計算：
    一般日：ret_t = close_t / close_{t-1} - 1
    事件日：ret_event = (close_t * S) / close_{t-1} - 1 （保留當日真實漲跌）
    回傳：(ret, exit_reason)
    """
    sub_prices = [(d, c) for d, c in price_rows_sorted if entry_date <= d <= exit_date and c > 0]
    if len(sub_prices) < 2:
        return (None, "insufficient_data")

    actual_exit_date = sub_prices[-1][0]
    exit_reason = "normal" if actual_exit_date == exit_date else "last_available"

    cum_ret = 1.0
    for i in range(1, len(sub_prices)):
        d_prev, p_prev = sub_prices[i - 1]
        d_cur, p_cur = sub_prices[i]

        if d_cur in corp_actions_map:
            S = corp_actions_map[d_cur]
            daily_r = (p_cur * S) / p_prev - 1.0
        else:
            daily_r = p_cur / p_prev - 1.0

        cum_ret *= (1.0 + daily_r)

    return (cum_ret - 1.0, exit_reason)


def check_corp_action_in_window(
    corp_actions_for_stock: dict[str, float] | None,
    start_date: str | None,
    end_date: str,
) -> bool:
    """檢查在 (start_date, end_date] 區間內是否存在公司行動事件。"""
    if not corp_actions_for_stock or start_date is None:
        return False
    for d in corp_actions_for_stock:
        if start_date < d <= end_date:
            return True
    return False


def check_corp_action_in_per_window(
    corp_actions_for_stock: dict[str, float] | None,
    dt_3y_start: str,
    sig_date: str,
) -> bool:
    """檢查在 [dt_3y_start, sig_date] 區間內是否存在公司行動事件。"""
    if not corp_actions_for_stock:
        return False
    for d in corp_actions_for_stock:
        if dt_3y_start <= d <= sig_date:
            return True
    return False


# ---------------------------------------------------------------------------
# C & D. 統一母體資格、連續季/月、法人 NA 輔助函式
# ---------------------------------------------------------------------------

def check_stock_eligibility(
    stock_id: str,
    signal_date: str,
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    price_rows_sorted: list[tuple[str, float]],
    first_price_date: str | None,
) -> bool:
    """統一母體資格判斷：
    1. 該月要有訊號日收盤；
    2. 訊號日前 250 個交易日內至少 200 筆收盤；
    3. 訊號日當下該檔在 fm_price_daily 的首個有價日必須早於訊號日 250 個交易日以上（已上市滿一年）。
    """
    if signal_date not in date_to_idx:
        return False

    sig_idx = date_to_idx[signal_date]
    if sig_idx < 250:
        return False

    # 檢查首日是否早於訊號日 250 個交易日以上
    actual_first_date = None
    if isinstance(first_price_date, str) and first_price_date.strip():
        actual_first_date = first_price_date.strip()
    elif price_rows_sorted:
        actual_first_date = price_rows_sorted[0][0]

    if actual_first_date is None:
        return False
    cutoff_250_date = all_trading_dates[sig_idx - 250]
    if actual_first_date > cutoff_250_date:
        return False

    # 檢查訊號日當天是否有價
    prices_map = dict(price_rows_sorted)
    if prices_map.get(signal_date, 0.0) <= 0:
        return False

    # 檢查近 250 個交易日內是否至少 200 筆有價
    recent_250_dates = set(all_trading_dates[sig_idx - 250 : sig_idx + 1])
    n_valid = sum(1 for d, c in price_rows_sorted if d in recent_250_dates and c > 0)
    if n_valid < 200:
        return False

    return True


def check_continuous_eps(
    visible_eps_sorted: list[tuple[str, float, str]],
    signal_date: str,
    required_quarters: int = 8,
    max_days_freshness: int = 200,
) -> tuple[bool, list[float]]:
    """檢查 EPS 是否連續且新鮮。
    visible_eps_sorted: 依 quarter_end 排序且 visible_date <= signal_date 的 (quarter_end, eps, vis_date)
    要求：
    1. 至少 required_quarters 季
    2. 最近 required_quarters 季嚴格連續（每季相隔約 3 個月）
    3. 最新一季距 signal_date 不超過 max_days_freshness 天
    """
    if len(visible_eps_sorted) < required_quarters:
        return False, []

    recent = visible_eps_sorted[-required_quarters:]

    # 檢查新鮮度
    latest_q = datetime.strptime(recent[-1][0], "%Y-%m-%d")
    sig_dt = datetime.strptime(signal_date, "%Y-%m-%d")
    if (sig_dt - latest_q).days > max_days_freshness:
        return False, []

    # 檢查嚴格連續（前一季與後一季相隔 80~100 天）
    for i in range(1, len(recent)):
        q_prev = datetime.strptime(recent[i - 1][0], "%Y-%m-%d")
        q_cur = datetime.strptime(recent[i][0], "%Y-%m-%d")
        diff_days = (q_cur - q_prev).days
        if not (80 <= diff_days <= 100):
            return False, []

    eps_vals = [r[1] for r in recent]
    return True, eps_vals


def check_continuous_revenue(
    visible_rev_sorted: list[tuple[str, int, int, str]],
    signal_date: str,
    required_months: int = 3,
    max_days_freshness: int = 45,
) -> tuple[bool, list[tuple[int, int]]]:
    """檢查月營收是否連續且新鮮。
    visible_rev_sorted: 依 ym 排序且 vis_date <= signal_date 的 (ym, rev, rev_ly, vis_date)
    要求：
    1. 至少 required_months 個月
    2. 最近 required_months 個月嚴格連續（年月連續）
    3. 最新一期之可見日距 signal_date 不超過 max_days_freshness 天
    """
    if len(visible_rev_sorted) < required_months:
        return False, []

    recent = visible_rev_sorted[-required_months:]

    # 檢查新鮮度（最新一期的 visible_date）
    vis_dt = datetime.strptime(recent[-1][3], "%Y-%m-%d")
    sig_dt = datetime.strptime(signal_date, "%Y-%m-%d")
    if (sig_dt - vis_dt).days > max_days_freshness:
        return False, []

    # 檢查年月嚴格連續
    for i in range(1, len(recent)):
        y_p, m_p = [int(x) for x in recent[i - 1][0].split("-")]
        y_c, m_c = [int(x) for x in recent[i][0].split("-")]
        expected_y = y_p if m_p < 12 else y_p + 1
        expected_m = m_p + 1 if m_p < 12 else 1
        if y_c != expected_y or m_c != expected_m:
            return False, []

    vals = [(r[1], r[2]) for r in recent]
    return True, vals


# ---------------------------------------------------------------------------
# 前瞻報酬整合運算函式（Item A, B, C 整合）
# ---------------------------------------------------------------------------

def compute_stock_forward_returns(
    sid: str,
    sig_date: str,
    target_dates: dict[str, str | None],
    price_dict: dict[str, float],
    price_rows: list[tuple[str, float]],
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    corp_actions_map: dict[str, float] | None = None,
    div_yield_at_sig: float = 0.0,
    cost: float = 0.006,
    delist_map: dict[str, str] | None = None,
    unadj_price_dict: dict[str, float] | None = None,
) -> dict[str, any]:
    """計算單檔股票的前瞻報酬，含 T+1 進場、到期 T+1 出場、右設限整月排除與下市結算。
    狀態欄位：
    - entry_status: filled_T1 / filled_T2 / filled_T3 / unfilled
    - exit_status_{h}: filled / delayed / unresolved / right_censored
    - valuation_status_{h}: realized / last_available / delisted / right_censored
    """
    res = {}
    delist_d = delist_map.get(sid) if delist_map else None

    entry_d_ideal = get_next_trading_day(sig_date, all_trading_dates, date_to_idx)
    if entry_d_ideal is None or entry_d_ideal not in date_to_idx:
        res["entry_status"] = "unfilled"
        for h in target_dates:
            res[f"ret_{h}"] = None
            res[f"ret_{h}_tr"] = None
            res[f"ret_{h}_net"] = None
            res[f"exit_status_{h}"] = "unresolved"
            res[f"valuation_status_{h}"] = "unresolved"
            res[f"exit_reason_{h}"] = "none"
            res[f"ret_{h}_old"] = None
            res[f"ret_{h}_cons"] = None
        return res

    # 進場嘗試窗口：T+1, T+2, T+3（有價且 > 0 即可成交）
    start_idx = date_to_idx[entry_d_ideal]
    p_entry = None
    actual_entry_d = None
    entry_status = "unfilled"

    for offset in range(3):
        cur_idx = start_idx + offset
        if cur_idx >= len(all_trading_dates):
            break
        d = all_trading_dates[cur_idx]
        p = price_dict.get(d)
        if p is not None and p > 0:
            p_entry = p
            actual_entry_d = d
            entry_status = f"filled_T{offset + 1}"
            break

    res["entry_status"] = entry_status

    # 保守情境進場（接近漲跌停界線延後 1 日）
    p_entry_cons = None
    for offset in range(3):
        cur_idx = start_idx + offset
        if cur_idx >= len(all_trading_dates):
            break
        d = all_trading_dates[cur_idx]
        p = price_dict.get(d)
        if p is not None and p > 0:
            if cur_idx > 0:
                prev_d = all_trading_dates[cur_idx - 1]
                prev_p = price_dict.get(prev_d)
                if prev_p and prev_p > 0 and abs(p / prev_p - 1.0) >= 0.095:
                    continue
            p_entry_cons = p
            break

    for h, tgt_d in target_dates.items():
        months = int(h.replace("m", ""))

        # 舊同日收盤報酬（供對照）
        p_sig_old = unadj_price_dict.get(sig_date) if unadj_price_dict else price_dict.get(sig_date)
        p_tgt_old = (unadj_price_dict.get(tgt_d) if unadj_price_dict else price_dict.get(tgt_d)) if tgt_d else None
        ret_old = (p_tgt_old / p_sig_old - 1.0) if (p_sig_old and p_tgt_old and p_sig_old > 0 and p_tgt_old > 0) else None
        res[f"ret_{h}_old"] = ret_old

        # 計算日曆月到期預定執行日
        tgt_exec_d, is_censored = get_target_execution_date(sig_date, months, all_trading_dates, date_to_idx)
        if is_censored or tgt_exec_d is None:
            res[f"ret_{h}"] = None
            res[f"ret_{h}_tr"] = None
            res[f"ret_{h}_net"] = None
            res[f"exit_status_{h}"] = "right_censored"
            res[f"valuation_status_{h}"] = "right_censored"
            res[f"exit_reason_{h}"] = "right_censored"
            res[f"ret_{h}_cons"] = None
            continue

        # 進場未成交：現金 0%
        if p_entry is None or actual_entry_d is None:
            res[f"ret_{h}"] = 0.0
            res[f"ret_{h}_tr"] = 0.0
            res[f"ret_{h}_net"] = 0.0
            res[f"exit_status_{h}"] = "unresolved"
            res[f"valuation_status_{h}"] = "realized"
            res[f"exit_reason_{h}"] = "unfilled"
            res[f"ret_{h}_cons"] = 0.0
            continue

        # 下市判定
        if delist_d is not None and delist_d <= tgt_exec_d:
            if delist_d <= actual_entry_d:
                res[f"ret_{h}"] = 0.0
                res[f"ret_{h}_tr"] = 0.0
                res[f"ret_{h}_net"] = 0.0
                res[f"exit_status_{h}"] = "unresolved"
                res[f"valuation_status_{h}"] = "delisted"
                res[f"exit_reason_{h}"] = "delisted"
                res[f"ret_{h}_cons"] = 0.0
                continue
            else:
                valid_before_delist = [c for d, c in price_rows if actual_entry_d <= d <= delist_d and c > 0]
                p_last = valid_before_delist[-1] if valid_before_delist else p_entry
                r_final = p_last / p_entry - 1.0
                res[f"ret_{h}"] = float(r_final)
                res[f"ret_{h}_tr"] = float(r_final)
                res[f"ret_{h}_net"] = float(r_final - cost)
                res[f"exit_status_{h}"] = "unresolved"
                res[f"valuation_status_{h}"] = "delisted"
                res[f"exit_reason_{h}"] = "delisted"
                res[f"ret_{h}_cons"] = float(r_final)
                continue

        # 出場嘗試窗口：tgt_exec_d, +1, +2
        exit_start_idx = date_to_idx.get(tgt_exec_d)
        p_exit = None
        exit_status = "unresolved"
        valuation_status = "unresolved"

        if exit_start_idx is not None:
            for offset in range(3):
                cur_idx = exit_start_idx + offset
                if cur_idx >= len(all_trading_dates):
                    break
                d = all_trading_dates[cur_idx]
                p = price_dict.get(d)
                if p is not None and p > 0:
                    p_exit = p
                    exit_status = "filled" if offset == 0 else "delayed"
                    valuation_status = "realized"
                    break

        if p_exit is not None:
            r_final = p_exit / p_entry - 1.0
        else:
            # 出場 unresolved：以到期日前最後可得還原價估值，不得重設為 0%
            valid_before_exit = [c for d, c in price_rows if actual_entry_d <= d <= tgt_exec_d and c > 0]
            if valid_before_exit:
                p_last = valid_before_exit[-1]
                r_final = p_last / p_entry - 1.0
            else:
                r_final = 0.0
            exit_status = "unresolved"
            valuation_status = "last_available"

        # 保守情境出場計算
        r_cons = r_final
        if p_entry_cons is not None and exit_start_idx is not None:
            p_exit_cons = None
            for offset in range(3):
                cur_idx = exit_start_idx + offset
                if cur_idx >= len(all_trading_dates):
                    break
                d = all_trading_dates[cur_idx]
                p = price_dict.get(d)
                if p is not None and p > 0:
                    if cur_idx > 0:
                        prev_d = all_trading_dates[cur_idx - 1]
                        prev_p = price_dict.get(prev_d)
                        if prev_p and prev_p > 0 and abs(p / prev_p - 1.0) >= 0.095:
                            continue
                    p_exit_cons = p
                    break
            if p_exit_cons is not None:
                r_cons = p_exit_cons / p_entry_cons - 1.0

        dy = div_yield_at_sig if (div_yield_at_sig is not None and not np.isnan(div_yield_at_sig)) else 0.0
        r_tr = float(r_final + (dy / 100.0) * (months / 12.0))
        r_net = float(r_tr - cost)

        res[f"ret_{h}"] = float(r_final)
        res[f"ret_{h}_tr"] = r_tr
        res[f"ret_{h}_net"] = r_net
        res[f"ret_{h}_cons"] = float(r_cons)
        res[f"exit_status_{h}"] = exit_status
        res[f"valuation_status_{h}"] = valuation_status
        res[f"exit_reason_{h}"] = exit_status if valuation_status == "realized" else valuation_status

    return res


# ---------------------------------------------------------------------------
# F. 時間區塊 Bootstrap
# ---------------------------------------------------------------------------

def block_bootstrap_paired_diff(
    series_child: list[float],
    series_parent: list[float],
    block_size: int = 3,
    n_resamples: int = 2000,
    seed: int = 42,
) -> dict:
    """時間區塊 Bootstrap 檢定父子策略配對差。
    所有策略使用相同隨機抽樣索引。
    回傳 mean_diff, median_diff, win_rate, ci_lower, ci_upper, n_blocks。
    """
    n = len(series_child)
    if n == 0 or len(series_parent) != n:
        return {
            "mean_diff": 0.0,
            "median_diff": 0.0,
            "win_rate": 0.0,
            "ci_95_lower": 0.0,
            "ci_95_upper": 0.0,
            "n_blocks": 0,
        }

    diffs = np.array(series_child) - np.array(series_parent)
    mean_diff = float(np.mean(diffs))
    median_diff = float(np.median(diffs))
    win_rate = float(np.mean(diffs > 0))

    num_blocks = max(1, n // block_size)
    rng = np.random.RandomState(seed)

    boot_means = []
    for _ in range(n_resamples):
        indices = []
        while len(indices) < n:
            start_idx = rng.randint(0, max(1, n - block_size + 1))
            indices.extend(range(start_idx, min(n, start_idx + block_size)))
        indices = indices[:n]
        boot_means.append(float(np.mean(diffs[indices])))

    ci_lower = float(np.percentile(boot_means, 2.5))
    ci_upper = float(np.percentile(boot_means, 97.5))

    return {
        "mean_diff": mean_diff,
        "median_diff": median_diff,
        "win_rate": win_rate,
        "p_child_gt_parent": win_rate,
        "ci_95_lower": ci_lower,
        "ci_95_upper": ci_upper,
        "n_blocks": num_blocks,
    }


# ---------------------------------------------------------------------------
# E. 組合層重算：漂移持股與淨額交易成本
# ---------------------------------------------------------------------------

def compute_drifted_portfolio_equity_curve(
    signals_by_date: dict[str, dict[str, set[str]]],
    valid_dates: list[str],
    price_dict_by_stock: dict[str, dict[str, float]],
    price_rows_by_stock: dict[str, list[tuple[str, float]]],
    all_trading_dates: list[str],
    date_to_idx: dict[str, int],
    div_yield_dict: dict[str, dict[str, float]],
    corp_actions_map: dict[str, dict[str, float]] | None = None,
    strats: tuple[str, ...] = ("D0", "D3", "bench"),
) -> tuple[pd.DataFrame, dict[str, dict[str, any]]]:
    """計算漂移組合層淨值曲線與指標。
    - 3 個 cohort 各自買入持有 3 個月（權重隨價格漂移，不每月重設）
    - cohort 間資金按實際到期再投入
    - 成本按實際買入與賣出金額各計 0.3%（合計 0.6%），同一檔跨 cohort 先淨額再計費
    - 暖機期（策略無選股）不扣成本；統計「有部位月份數／總月份數」
    - 年化只用完整月份
    """
    corp_map = corp_actions_map or {}
    port_metrics = {}
    strat_monthly_rets = {strat: [] for strat in strats}
    strat_equity = {strat: [1.0] for strat in strats}
    strat_active_months = {strat: 0 for strat in strats}

    n_months = len(valid_dates) - 1
    if n_months <= 0:
        empty_df = pd.DataFrame({"date": valid_dates})
        return empty_df, {}

    for strat in strats:
        # 初始化 3 個 cohort，各分得 1/3 資金
        cohort_holdings = [{} for _ in range(3)]  # [{sid: dollar_val}, ...]
        cohort_cash = [1.0 / 3.0 for _ in range(3)]
        active_count = 0

        for i in range(n_months):
            d_cur = valid_dates[i]
            d_next = valid_dates[i + 1]
            k_reb = i % 3

            # 記錄再平衡前全組合持股
            holdings_before = {}
            for k in range(3):
                for s, v in cohort_holdings[k].items():
                    holdings_before[s] = holdings_before.get(s, 0.0) + v

            # 到期 cohort 清算為現金
            mature_val = sum(cohort_holdings[k_reb].values())
            cohort_cash[k_reb] += mature_val
            cohort_holdings[k_reb] = {}

            # 依策略訊號建構新部位
            stks = sorted(list(signals_by_date.get(d_cur, {}).get(strat, set())))
            avail_cap = cohort_cash[k_reb]

            target_holdings_k = {}
            if stks and avail_cap > 0:
                w_each = avail_cap / len(stks)
                target_holdings_k = {s: w_each for s in stks}
                cohort_cash[k_reb] = 0.0

            # 計算再平衡後目標持股
            holdings_after = {}
            for k in range(3):
                if k == k_reb:
                    for s, v in target_holdings_k.items():
                        holdings_after[s] = holdings_after.get(s, 0.0) + v
                else:
                    for s, v in cohort_holdings[k].items():
                        holdings_after[s] = holdings_after.get(s, 0.0) + v

            # 跨 cohort 淨額計算交易成本
            all_s = set(holdings_before.keys()) | set(holdings_after.keys())
            net_buy = sum(max(0.0, holdings_after.get(s, 0.0) - holdings_before.get(s, 0.0)) for s in all_s)
            net_sell = sum(max(0.0, holdings_before.get(s, 0.0) - holdings_after.get(s, 0.0)) for s in all_s)
            tx_cost = 0.003 * (net_buy + net_sell)

            # 扣除交易成本（暖機期 net_buy=net_sell=0 則 tx_cost=0）
            if tx_cost > 0:
                tot_target = sum(target_holdings_k.values())
                if tot_target > 0:
                    scale = max(0.0, 1.0 - tx_cost / tot_target)
                    for s in target_holdings_k:
                        target_holdings_k[s] *= scale
                else:
                    cohort_cash[k_reb] = max(0.0, cohort_cash[k_reb] - tx_cost)

            cohort_holdings[k_reb] = target_holdings_k

            # 檢查當月是否有持股
            tot_stks_held = sum(len(cohort_holdings[k]) for k in range(3))
            if tot_stks_held > 0:
                active_count += 1

            # 經歷一個月 (d_cur 到 d_next) 價格漂移
            # 各檔股票計算當月 T+1 報酬
            entry_d = get_next_trading_day(d_cur, all_trading_dates, date_to_idx) or d_cur
            exit_d = get_next_trading_day(d_next, all_trading_dates, date_to_idx) or d_next

            for k in range(3):
                for s in list(cohort_holdings[k].keys()):
                    p_rows = price_rows_by_stock.get(s, [])
                    r_stock, _ = compute_holding_return_adjusted(
                        p_rows, entry_d, exit_d, corp_map.get(s, {})
                    )
                    if r_stock is None:
                        r_stock = 0.0
                    dy = div_yield_dict.get(s, {}).get(d_cur, 0.0)
                    if dy and not np.isnan(dy):
                        r_stock += (dy / 100.0) * (1.0 / 12.0)

                    cohort_holdings[k][s] *= (1.0 + r_stock)

            # 月末總資產與月報酬
            eq_end = sum(sum(cohort_holdings[k].values()) + cohort_cash[k] for k in range(3))
            eq_prev = strat_equity[strat][-1]
            m_ret = (eq_end / eq_prev - 1.0) if eq_prev > 0 else 0.0

            strat_monthly_rets[strat].append(m_ret)
            strat_equity[strat].append(eq_end)

        strat_active_months[strat] = active_count

    # 組合 DataFrame
    equity_df_dict = {"date": valid_dates}
    for strat in strats:
        equity_df_dict[strat] = strat_equity[strat]
    equity_df = pd.DataFrame(equity_df_dict)

    # 計算各策略統計指標
    for strat in strats:
        rets = np.array(strat_monthly_rets[strat])
        eq_list = strat_equity[strat]
        final_eq = eq_list[-1]
        cagr = float(final_eq ** (12.0 / n_months) - 1.0) if final_eq > 0 else -1.0
        ann_vol = float(np.std(rets, ddof=1) * np.sqrt(12.0)) if len(rets) > 1 else 0.0
        sharpe = float((np.mean(rets) * 12.0) / ann_vol) if ann_vol > 0 else 0.0

        # 計算最大回撤
        peak = np.maximum.accumulate(eq_list)
        dd = (peak - eq_list) / peak
        max_dd = float(np.max(dd))
        mdd_idx = int(np.argmax(dd))
        mdd_date = valid_dates[mdd_idx] if mdd_idx < len(valid_dates) else "N/A"

        active_cnt = strat_active_months[strat]
        port_metrics[strat] = {
            "final_equity": final_eq,
            "cagr": cagr,
            "ann_vol": ann_vol,
            "max_dd": max_dd,
            "mdd_date": mdd_date,
            "sharpe": sharpe,
            "active_months": active_cnt,
            "total_months": n_months,
            "active_ratio": f"{active_cnt}/{n_months}",
        }

    return equity_df, port_metrics


# ---------------------------------------------------------------------------
# G. 等資金分批報酬公式
# ---------------------------------------------------------------------------

def compute_equal_dollar_tranche_return(
    tranche_prices: list[float],
    exit_price: float,
) -> float:
    """等資金分批報酬公式：
    每一批投入相同金額 D，買入股數為 D / P_i。
    總投入為 K * D，到期總市值為 P_exit * sum(D / P_i) = D * P_exit * sum(1 / P_i)。
    總報酬為 (總市值 / 總投入) - 1 = (P_exit / K) * sum(1 / P_i) - 1 = P_exit * mean(1 / P_i) - 1.0。
    """
    if not tranche_prices or exit_price <= 0:
        return 0.0
    for p in tranche_prices:
        if p <= 0:
            return 0.0
    inv_mean = float(np.mean([1.0 / p for p in tranche_prices]))
    return float(exit_price * inv_mean - 1.0)

