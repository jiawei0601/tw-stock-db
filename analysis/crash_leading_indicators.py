"""崩盤領先指標實證 — 用 macro_series/macro_observations 檢驗「哪些總經指標在
歷史崩盤前有預先訊號」。

方法：
1. 崩盤事件定義：S&P 500（1970+）與台灣加權指數（1999+）從「滾動 252 交易日高點」
   回撤 ≥20% 的事件，峰值日 = 滾動高點日。
   （不用全歷史新高法：TAIEX 2000 年高點直到 2017 才收復，全歷史法會把 2008 金融
   海嘯整段吞進 2000 年事件裡看不見。）
2. 訊號評估：每個候選指標定義一條二值化規則（如 10Y-2Y < 0），統計：
   - 命中率：崩盤峰前 lookback 月內是否觸發過
   - 領先月數：最早觸發月 → 峰值月
   - 誤報：訊號連續段（中斷 >6 個月算新段）起點後 18 個月內沒有任何崩盤峰
3. 誠實原則：規則全部事前指定（經典教科書訊號），不做參數優化；樣本數少
   （美 8 次、台 5 次），結論只能當「歷史相符程度」不能當預測保證。

執行：python analysis/crash_leading_indicators.py（repo 根目錄；只讀 DB，不寫入）
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


def series(con, sid):
    return con.execute(
        "SELECT date, value FROM macro_observations WHERE series_id=? ORDER BY date",
        (sid,),
    ).fetchall()


def find_crashes_rolling(con, sid, dd_threshold=-0.20, window=252, exit_dd=-0.05):
    """從滾動 window 日高點回撤 ≥|dd_threshold| 的事件 [(峰日, 谷日, 最大回撤)]。

    修正（2026-07-26 檢驗員發現的 bug）：舊版在事件內用「進場當下凍結的高點」判斷
    復原，收復耗時極長時（TAIEX 2000 高點 2017 才收復）會把中間的獨立崩盤
    （2008/2011/2015）整段吞掉。新版事件結束條件改為「相對*當下*滾動高點回到
    exit_dd 以內」——滾動高點會隨時間衰減，市場止穩約一年後 dd 自然歸零，
    之後的新一輪 ≥20% 回撤就能被辨識為獨立事件。
    回撤最大值仍相對「事件觸發時的峰值」計算（峰日固定，符合崩盤敘事直覺）。"""
    rows = series(con, sid)
    vals = [v for _, v in rows]
    dates = [d for d, _ in rows]
    episodes = []
    n = len(rows)
    in_ep = False
    peak_v = peak_d = trough = tj = None
    for i in range(n):
        lo = max(0, i - window)
        rm = max(vals[lo:i + 1])
        dd_now = vals[i] / rm - 1
        if not in_ep:
            if dd_now <= dd_threshold:
                in_ep = True
                rm_idx = lo + vals[lo:i + 1].index(rm)
                peak_v, peak_d = rm, dates[rm_idx]
                trough, tj = vals[i] / peak_v - 1, i
        else:
            d2 = vals[i] / peak_v - 1
            if d2 < trough:
                trough, tj = d2, i
            if dd_now >= exit_dd:
                episodes.append((peak_d, dates[tj], trough))
                in_ep = False
    if in_ep:
        episodes.append((peak_d, dates[tj], trough))
    return episodes


def month_floor(d):
    return d[:7]


def monthly(con, sid):
    """月頻 dict 'YYYY-MM' -> value（日頻序列取當月最後一筆）。"""
    out = {}
    for d, v in series(con, sid):
        out[month_floor(d)] = v
    return out


def months_between(m1, m2):
    y1, mo1 = map(int, m1.split("-"))
    y2, mo2 = map(int, m2.split("-"))
    return (y2 - y1) * 12 + (mo2 - mo1)


def add_months(m, k):
    y, mo = map(int, m.split("-"))
    t = y * 12 + mo - 1 + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def eval_signal(sig_months, crash_peaks, lookback=24, fp_horizon=18):
    """回傳 (每次崩盤的領先月數清單, 訊號段數, 誤報段數)。"""
    sig_sorted = sorted(sig_months)
    hits = []
    for peak in crash_peaks:
        pm = month_floor(peak)
        trig = [m for m in sig_sorted if 0 <= months_between(m, pm) <= lookback]
        hits.append((pm, months_between(trig[0], pm) if trig else None))
    eps = []
    for m in sig_sorted:
        if not eps or months_between(eps[-1][-1], m) > 6:
            eps.append([m])
        else:
            eps[-1].append(m)
    fp = sum(
        1 for ep in eps
        if not any(0 <= months_between(ep[0], month_floor(p)) <= fp_horizon
                   for p in crash_peaks)
    )
    return hits, len(eps), fp


def report(name, sig, peaks, lookback=24):
    hits, n_ep, fp = eval_signal(sig, peaks, lookback=lookback)
    hit_n = sum(1 for _, l in hits if l is not None)
    detail = ", ".join(f"{pm}:{'-' if l is None else str(l) + 'm'}" for pm, l in hits)
    print(f"◆ {name}")
    print(f"   命中 {hit_n}/{len(hits)} | 訊號段 {n_ep}、誤報 {fp} | 領先: {detail}")


def main():
    con = sqlite3.connect(DB_PATH)
    us_crash = [p for p, _, _ in find_crashes_rolling(con, "us_sp500")]
    tw_crash = [p for p, _, _ in find_crashes_rolling(con, "tw_taiex")]
    print("US 崩盤峰(滾動252日高點回撤≥20%):", us_crash)
    print("TW 崩盤峰(同法):", tw_crash)
    print()

    m_1022 = monthly(con, "us_10y_2y_spread")
    report("美 10Y-2Y 倒掛(<0)", {k for k, v in m_1022.items() if v < 0}, us_crash, 36)

    y10, y3 = monthly(con, "us_10y_yield"), monthly(con, "us_3m_yield")
    report("美 10Y-3M 倒掛(<0)",
           {k for k in y10 if k in y3 and y10[k] - y3[k] < 0}, us_crash, 36)

    ff = monthly(con, "us_fed_funds_rate")
    sig = {k for k in ff if add_months(k, -18) in ff and ff[k] - ff[add_months(k, -18)] >= 2.0}
    report("Fed 18個月升息≥2pp", sig, us_crash)

    cpi = monthly(con, "us_cpi_yoy_sa")
    report("美 CPI年增 >5%", {k for k, v in cpi.items() if v > 5}, us_crash)

    un = monthly(con, "us_unemployment_rate")
    keys = sorted(un)
    sig = {k for i, k in enumerate(keys)
           if keys[max(0, i - 12):i]
           and un[k] >= min(un[p] for p in keys[max(0, i - 12):i]) + 0.4}
    report("美 失業率自12月低點回升≥0.4pp", sig, us_crash, 6)

    ms = monthly(con, "tw_monitoring_score")
    report("台 景氣燈號紅燈(≥38)", {k for k, v in ms.items() if v >= 38}, tw_crash, 18)
    report("台 景氣燈號黃紅以上(≥32)", {k for k, v in ms.items() if v >= 32}, tw_crash, 18)

    sp = monthly(con, "tw_m1b_m2_spread")
    report("台 M1B-M2 死亡交叉(<0)", {k for k, v in sp.items() if v < 0}, tw_crash, 18)

    ex = monthly(con, "tw_export_yoy")
    report("台 出口年增轉負", {k for k, v in ex.items() if v < 0}, tw_crash, 12)

    pmi = monthly(con, "tw_pmi")
    report("台 製造業PMI<50", {k for k, v in pmi.items() if v < 50}, tw_crash, 12)

    li = monthly(con, "tw_leading_index")
    keys = sorted(li)
    sig = {k for i, k in enumerate(keys) if i >= 6 and li[k] < li[keys[i - 6]]}
    report("台 領先指標6個月變動轉負", sig, tw_crash, 12)

    report("[台股用] 美10Y-2Y倒掛", {k for k, v in m_1022.items() if v < 0}, tw_crash, 36)


if __name__ == "__main__":
    main()
