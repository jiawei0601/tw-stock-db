"""建置/刷新 macro_series + macro_observations：美國＋台灣共約 38 個總經/市場序列
的全部可得歷史，取代原本需要企業訂閱才能下載的 MacroMicro 匯出功能。

**來源分工**（詳見 collectors/macro_us.py、collectors/macro_tw.py 檔頭說明）：
- 美國序列：FRED（免 API key CSV endpoint，19 個序列）+ Yahoo Finance 非官方
  chart API（S&P 500，因原規劃來源 Stooq 已加上瀏覽器 JS proof-of-work 驗證、
  本專案不解 bot-detection 挑戰，改用此替代來源，起始年份只到 1970 而非 Stooq
  理論上能提供的 1920 年代，已在下方執行摘要誠實揭露）。
- 台灣序列：**FinMind 優先**（`tw_taiex`／`usd_twd`，2026-07-26 實測確認 FinMind
  只有這兩個序列有對應資料集，token 讀取 .env 的 FINMIND_TOKEN），其餘 FinMind
  沒有資料集的序列（出口值/外銷訂單/CPI/核心CPI/M1B年增率/M2年增率/景氣對策信號/
  領先落後指標/PMI）直接用官方部會來源（財政部/經濟部/主計總處/央行/國發會，皆為
  免費、不需金鑰、不解 CAPTCHA 的公開 endpoint）。PMI 分項指數（新增訂單/客戶存貨）
  沒找到可用的官方免費來源，跳過不臆測。

YoY 計算規則（美國 CPI/GDP/零售銷售等序列適用；台灣 CPI/M1B/M2 直接採用官方已計算
好的年增率，不重複計算）：以「同序列往前推 12 個月（月頻）/ 4 季（季頻）的觀測值」
計算 (v/v_prev - 1)*100，四捨五入 2 位；找不到對應期數的前期值或前期值為 0 則跳過
該點（不臆測、不用相鄰列頂替，避免資料缺月造成的錯位）。

Idempotent：INSERT OR REPLACE by (series_id, date)；可重跑覆蓋，不會重複。單一序列
失敗不中斷整體，最後列出成功/失敗清單。

用法：
    python build_macro.py [--db-path PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from collectors import macro_tw, macro_us
from models import CollectorError

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_series (
    series_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    region     TEXT NOT NULL,
    frequency  TEXT NOT NULL,
    units      TEXT,
    source     TEXT NOT NULL,
    source_detail TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS macro_observations (
    series_id TEXT NOT NULL,
    date      TEXT NOT NULL,
    value     REAL NOT NULL,
    PRIMARY KEY (series_id, date)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ==================== YoY / 差值計算（純函式，不打網路，可單獨測試） ====================

def _shift_month(date_s: str, months: int) -> str:
    """月頻 ISO 日期（每月第一天）往前/往後推 N 個月，仍回傳每月第一天。"""
    y, m, _d = (int(x) for x in date_s.split("-"))
    total = y * 12 + (m - 1) + months
    ny, nm = divmod(total, 12)
    return f"{ny:04d}-{nm + 1:02d}-01"


def compute_yoy_monthly(observations: list[dict]) -> list[dict]:
    """月頻序列 YoY：往前推 12 個月比對。observations 為 [{"date","value"}, ...]
    （date 一律是每月第一天 YYYY-MM-01）。前期值缺或為 0 則跳過該點。"""
    by_date = {o["date"]: o["value"] for o in observations}
    out = []
    for o in observations:
        prev_date = _shift_month(o["date"], -12)
        prev = by_date.get(prev_date)
        if prev is None or prev == 0:
            continue
        out.append({"date": o["date"], "value": round((o["value"] / prev - 1) * 100, 2)})
    return out


def compute_yoy_quarterly(observations: list[dict]) -> list[dict]:
    """季頻序列 YoY：往前推 4 季（= 12 個月，因為季頻日期一律對齊每季第一個月的
    第一天，跟月頻用同一套「移動月份」邏輯即可）。前期值缺或為 0 則跳過該點。"""
    return compute_yoy_monthly(observations)


def compute_diff(observations: list[dict]) -> list[dict]:
    """相鄰期差值（例如非農就業人數月變動）：依日期排序後，跟前一筆觀測值相減。
    observations 需已經是同一序列的完整歷史（由 collector 依日期由舊到新回傳）。"""
    ordered = sorted(observations, key=lambda o: o["date"])
    out = []
    for prev, cur in zip(ordered, ordered[1:]):
        out.append({"date": cur["date"], "value": round(cur["value"] - prev["value"], 2)})
    return out


def compute_spread(a: list[dict], b: list[dict]) -> list[dict]:
    """兩個序列在相同日期的差值（a - b），只保留兩邊都有值的日期。"""
    b_by_date = {o["date"]: o["value"] for o in b}
    out = []
    for o in a:
        bv = b_by_date.get(o["date"])
        if bv is None:
            continue
        out.append({"date": o["date"], "value": round(o["value"] - bv, 2)})
    return out


# ==================== 寫入（純 SQL，不打網路，可單獨測試冪等性） ====================

def write_series(
    conn: sqlite3.Connection, *, series_id: str, name: str, region: str, frequency: str,
    units: str | None, source: str, source_detail: str | None,
    observations: list[dict], now: str,
) -> int:
    """寫入單一序列的 metadata（INSERT OR REPLACE）+ 觀測值（INSERT OR REPLACE by
    (series_id, date)）。回傳這次寫入的觀測值筆數。"""
    conn.execute(
        "INSERT OR REPLACE INTO macro_series "
        "(series_id, name, region, frequency, units, source, source_detail, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (series_id, name, region, frequency, units, source, source_detail, now),
    )
    if observations:
        conn.executemany(
            "INSERT OR REPLACE INTO macro_observations (series_id, date, value) VALUES (?, ?, ?)",
            [(series_id, o["date"], o["value"]) for o in observations],
        )
    conn.commit()
    return len(observations)


# ==================== 序列定義 ====================
# 美國：series_id -> (FRED series id, 中文名, frequency, units, derive)
# derive: None=原值 | "yoy_m"=月頻YoY | "yoy_q"=季頻YoY | "diff"=相鄰期差值
US_FRED_SERIES: list[tuple[str, str, str, str, str | None, str | None]] = [
    ("us_nasdaq", "NASDAQCOM", "美國-NASDAQ指數", "D", None, None),
    ("us_10y_yield", "DGS10", "美國-10年期公債殖利率", "D", "%", None),
    ("us_2y_yield", "DGS2", "美國-2年期公債殖利率", "D", "%", None),
    ("us_3m_yield", "DTB3", "美國-3個月期公債殖利率", "D", "%", None),
    ("us_10y_2y_spread", "T10Y2Y", "美國-10年期-2年期公債利差", "D", "百分點", None),
    ("us_real_gdp_yoy", "GDPC1", "美國-實質GDP(年增率)", "Q", "%", "yoy_q"),
    ("us_real_gdp_saar_qoq", "A191RL1Q225SBEA", "美國-實質GDP(SAAR季增年率)", "Q", "%", None),
    ("us_cpi_yoy_sa", "CPIAUCSL", "美國-CPI(SA,年增率)", "M", "%", "yoy_m"),
    ("us_core_cpi_yoy_sa", "CPILFESL", "美國-核心CPI(SA,年增率)", "M", "%", "yoy_m"),
    ("us_cpi_yoy_nsa", "CPIAUCNS", "美國-CPI(NSA,年增率)", "M", "%", "yoy_m"),
    ("us_core_pce_yoy", "PCEPILFE", "美國-核心PCE物價指數(年增率)", "M", "%", "yoy_m"),
    ("us_unemployment_rate", "UNRATE", "美國-失業率", "M", "%", None),
    ("us_nonfarm_payrolls_mom", "PAYEMS", "美國-非農就業人數(月變動,千人)", "M", "千人", "diff"),
    ("us_fed_funds_rate", "DFF", "美國-聯邦基金有效利率(日)", "D", "%", None),
    ("us_fed_target_upper", "DFEDTARU", "美國-聯準會目標利率上限", "D", "%", None),
    ("us_retail_sales_yoy", "RSAFS", "美國-零售銷售(年增率)", "M", "%", "yoy_m"),
    ("us_retail_sales_autos_yoy", "RSMVPD", "美國-零售銷售-汽車相關(年增率)", "M", "%", "yoy_m"),
    ("us_avg_hourly_earnings_yoy", "CES0500000003", "美國-每小時薪資(年增率)", "M", "%", "yoy_m"),
    ("us_on_rrp", "RRPONTSYD", "美國-聯準會隔夜逆回購(十億美元)", "D", "十億美元", None),
]

_DERIVE_FN = {"yoy_m": compute_yoy_monthly, "yoy_q": compute_yoy_quarterly, "diff": compute_diff}


def _build_us_series(conn: sqlite3.Connection, now: str) -> list[tuple[str, str, int | None, str]]:
    """回傳 [(series_id, status, n_rows, detail), ...] 摘要。"""
    results = []

    for series_id, fred_id, name, freq, units, derive in US_FRED_SERIES:
        try:
            raw = macro_us.fetch_fred_series(fred_id)
        except CollectorError as e:
            results.append((series_id, "FAIL", None, str(e)))
            continue
        if not raw:
            results.append((series_id, "FAIL", None, f"FRED {fred_id} 回空資料"))
            continue
        obs = _DERIVE_FN[derive](raw) if derive else raw
        n = write_series(
            conn, series_id=series_id, name=name, region="US", frequency=freq, units=units,
            source="FRED", source_detail=fred_id, observations=obs, now=now,
        )
        date_range = (obs[0]["date"], obs[-1]["date"]) if obs else (None, None)
        results.append((series_id, "OK", n, f"{date_range[0]} ~ {date_range[1]}"))

    # us_sp500：Yahoo Finance chart API（Stooq 因 bot-detection 挑戰無法用，見檔頭說明）
    try:
        raw = macro_us.fetch_yahoo_chart_daily("^GSPC")
        if not raw:
            results.append(("us_sp500", "FAIL", None, "Yahoo ^GSPC 回空資料"))
        else:
            n = write_series(
                conn, series_id="us_sp500", name="美國-S&P 500", region="US", frequency="D",
                units="點", source="Yahoo Finance", source_detail="^GSPC", observations=raw, now=now,
            )
            results.append(("us_sp500", "OK", n, f"{raw[0]['date']} ~ {raw[-1]['date']}"))
    except CollectorError as e:
        results.append(("us_sp500", "FAIL", None, str(e)))

    return results


def _build_tw_series(conn: sqlite3.Connection, now: str) -> list[tuple[str, str, int | None, str]]:
    results = []

    def _ok(series_id, obs):
        date_range = (obs[0]["date"], obs[-1]["date"]) if obs else (None, None)
        results.append((series_id, "OK", len(obs), f"{date_range[0]} ~ {date_range[1]}"))

    def _fail(series_id, msg):
        results.append((series_id, "FAIL", None, msg))

    # ---- FinMind（優先來源，實測確認只有這兩個序列有對應資料集） ----
    try:
        taiex = macro_tw.fetch_finmind_taiex_daily()
        if taiex:
            write_series(conn, series_id="tw_taiex", name="台灣-加權股價指數", region="TW",
                         frequency="D", units="點", source="FinMind", source_detail="TaiwanStockPrice/TAIEX",
                         observations=taiex, now=now)
            _ok("tw_taiex", taiex)
        else:
            _fail("tw_taiex", "FinMind TaiwanStockPrice/TAIEX 回空資料")
    except CollectorError as e:
        _fail("tw_taiex", str(e))

    try:
        usdtwd = macro_tw.fetch_finmind_usd_twd_daily()
        if usdtwd:
            write_series(conn, series_id="usd_twd", name="美元/台幣", region="TW",
                         frequency="D", units="元", source="FinMind", source_detail="TaiwanExchangeRate/USD",
                         observations=usdtwd, now=now)
            _ok("usd_twd", usdtwd)
        else:
            _fail("usd_twd", "FinMind TaiwanExchangeRate/USD 回空資料")
    except CollectorError as e:
        _fail("usd_twd", str(e))

    # ---- 財政部：出口值/電子零組件/資通與視聽產品（+ 各自 YoY） ----
    try:
        export_rows = macro_tw.fetch_mof_export_by_product()
    except CollectorError as e:
        export_rows = None
        for sid in ("tw_export_value", "tw_export_yoy", "tw_export_electronics",
                    "tw_export_electronics_yoy", "tw_export_ict", "tw_export_ict_yoy"):
            _fail(sid, str(e))

    if export_rows:
        specs = [
            ("tw_export_value", "tw_export_yoy", "total", "台灣-出口值", "台灣-出口值(年增率)"),
            ("tw_export_electronics", "tw_export_electronics_yoy", "electronics",
             "台灣-出口值-電子零組件", "台灣-出口值-電子零組件(年增率)"),
            ("tw_export_ict", "tw_export_ict_yoy", "ict",
             "台灣-出口值-資通與視聽產品", "台灣-出口值-資通與視聽產品(年增率)"),
        ]
        for level_id, yoy_id, key, level_name, yoy_name in specs:
            obs = [{"date": r["date"], "value": r[key]} for r in export_rows]
            write_series(conn, series_id=level_id, name=level_name, region="TW", frequency="M",
                         units="百萬美元", source="財政部統計處", source_detail="出口值_按主要貨品分",
                         observations=obs, now=now)
            _ok(level_id, obs)
            yoy_obs = compute_yoy_monthly(obs)
            write_series(conn, series_id=yoy_id, name=yoy_name, region="TW", frequency="M",
                         units="%", source="財政部統計處", source_detail="出口值_按主要貨品分(計算YoY)",
                         observations=yoy_obs, now=now)
            _ok(yoy_id, yoy_obs)

    # ---- 經濟部：外銷訂單（+ YoY） ----
    try:
        orders = macro_tw.fetch_export_orders()
        if orders:
            write_series(conn, series_id="tw_export_orders", name="台灣-外銷訂單金額", region="TW",
                         frequency="M", units="百萬美元", source="經濟部", source_detail="外銷訂單",
                         observations=orders, now=now)
            _ok("tw_export_orders", orders)
            yoy_obs = compute_yoy_monthly(orders)
            write_series(conn, series_id="tw_export_orders_yoy", name="台灣-外銷訂單(年增率)", region="TW",
                         frequency="M", units="%", source="經濟部", source_detail="外銷訂單(計算YoY)",
                         observations=yoy_obs, now=now)
            _ok("tw_export_orders_yoy", yoy_obs)
        else:
            _fail("tw_export_orders", "經濟部外銷訂單 opendata 回空資料")
            _fail("tw_export_orders_yoy", "依賴 tw_export_orders，來源回空資料")
    except CollectorError as e:
        _fail("tw_export_orders", str(e))
        _fail("tw_export_orders_yoy", f"依賴 tw_export_orders：{e}")

    # ---- 主計總處：CPI/核心CPI 年增率（官方已計算好，直接採用） ----
    try:
        cpi = macro_tw.fetch_cpi_yoy()
        if cpi:
            write_series(conn, series_id="tw_cpi_yoy", name="台灣-CPI(年增率)", region="TW",
                         frequency="M", units="%", source="主計總處", source_detail="消費者物價基本分類指數",
                         observations=cpi, now=now)
            _ok("tw_cpi_yoy", cpi)
        else:
            _fail("tw_cpi_yoy", "主計總處 CPI XML 回空資料")
    except CollectorError as e:
        _fail("tw_cpi_yoy", str(e))

    try:
        core_cpi = macro_tw.fetch_core_cpi_yoy()
        if core_cpi:
            write_series(conn, series_id="tw_core_cpi_yoy", name="台灣-核心CPI(年增率)", region="TW",
                         frequency="M", units="%", source="主計總處", source_detail="消費者物價特殊分類指數",
                         observations=core_cpi, now=now)
            _ok("tw_core_cpi_yoy", core_cpi)
        else:
            _fail("tw_core_cpi_yoy", "主計總處核心CPI XML 回空資料")
    except CollectorError as e:
        _fail("tw_core_cpi_yoy", str(e))

    # ---- 央行：M1B/M2 年增率（官方已計算好）+ M1B-M2 利差 ----
    try:
        monetary = macro_tw.fetch_cbc_monetary_aggregates()
    except CollectorError as e:
        monetary = None
        for sid in ("tw_m1b_yoy", "tw_m2_yoy", "tw_m1b_m2_spread"):
            _fail(sid, str(e))

    if monetary:
        m1b_obs = [{"date": r["date"], "value": r["m1b_yoy"]} for r in monetary if r["m1b_yoy"] is not None]
        m2_obs = [{"date": r["date"], "value": r["m2_yoy"]} for r in monetary if r["m2_yoy"] is not None]
        write_series(conn, series_id="tw_m1b_yoy", name="台灣-M1B(日平均,年增率)", region="TW",
                     frequency="M", units="%", source="央行", source_detail="貨幣總計數(日平均數)",
                     observations=m1b_obs, now=now)
        _ok("tw_m1b_yoy", m1b_obs)
        write_series(conn, series_id="tw_m2_yoy", name="台灣-M2(日平均,年增率)", region="TW",
                     frequency="M", units="%", source="央行", source_detail="貨幣總計數(日平均數)",
                     observations=m2_obs, now=now)
        _ok("tw_m2_yoy", m2_obs)
        spread_obs = compute_spread(m1b_obs, m2_obs)
        write_series(conn, series_id="tw_m1b_m2_spread", name="台灣-M1B-M2年增率差", region="TW",
                     frequency="M", units="百分點", source="央行", source_detail="貨幣總計數(計算差值)",
                     observations=spread_obs, now=now)
        _ok("tw_m1b_m2_spread", spread_obs)

    # ---- 國發會：景氣對策信號分數/領先指標/落後指標 ----
    try:
        ndc = macro_tw.fetch_ndc_monitoring_indicators()
        if ndc:
            leading_obs = [{"date": r["date"], "value": r["leading_index"]} for r in ndc if r["leading_index"] is not None]
            lagging_obs = [{"date": r["date"], "value": r["lagging_index"]} for r in ndc if r["lagging_index"] is not None]
            score_obs = [{"date": r["date"], "value": r["monitoring_score"]} for r in ndc if r["monitoring_score"] is not None]
            write_series(conn, series_id="tw_leading_index", name="台灣-領先指標", region="TW",
                         frequency="M", units="指數", source="國發會", source_detail="景氣指標及燈號",
                         observations=leading_obs, now=now)
            _ok("tw_leading_index", leading_obs)
            write_series(conn, series_id="tw_lagging_index", name="台灣-落後指標", region="TW",
                         frequency="M", units="指數", source="國發會", source_detail="景氣指標及燈號",
                         observations=lagging_obs, now=now)
            _ok("tw_lagging_index", lagging_obs)
            write_series(conn, series_id="tw_monitoring_score", name="台灣-景氣對策信號分數", region="TW",
                         frequency="M", units="分", source="國發會", source_detail="景氣指標及燈號",
                         observations=score_obs, now=now)
            _ok("tw_monitoring_score", score_obs)
        else:
            for sid in ("tw_leading_index", "tw_lagging_index", "tw_monitoring_score"):
                _fail(sid, "國發會景氣指標及燈號 ZIP 回空資料")
    except CollectorError as e:
        for sid in ("tw_leading_index", "tw_lagging_index", "tw_monitoring_score"):
            _fail(sid, str(e))

    # ---- 國發會：PMI（僅綜合指數；新增訂單/客戶存貨無可用免費來源，見檔頭說明） ----
    try:
        pmi = macro_tw.fetch_ndc_pmi()
        if pmi:
            write_series(conn, series_id="tw_pmi", name="台灣-製造業PMI", region="TW",
                         frequency="M", units="指數", source="國發會", source_detail="臺灣採購經理人指數",
                         observations=pmi, now=now)
            _ok("tw_pmi", pmi)
        else:
            _fail("tw_pmi", "國發會 PMI CSV 回空資料")
    except CollectorError as e:
        _fail("tw_pmi", str(e))

    _fail("tw_pmi_new_orders", "無可用免費來源（index.ndc.gov.tw 細項頁面掛在 Cloudflare 之後，"
                                "子資源請求回傳 bot-detection 攔截頁，本專案不解 CAPTCHA/bot-detection，見 collectors/macro_tw.py 檔頭說明）")
    _fail("tw_pmi_customer_inventory", "無可用免費來源，原因同 tw_pmi_new_orders")

    return results


def build(db_path: Path) -> list[tuple[str, str, int | None, str]]:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        now = _now_iso()
        results = _build_us_series(conn, now) + _build_tw_series(conn, now)
        return results
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新總經指標 macro_series + macro_observations")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    results = build(args.db_path)

    ok = [r for r in results if r[1] == "OK"]
    failed = [r for r in results if r[1] == "FAIL"]

    print(f"總經指標建置完成：{len(ok)} 個序列成功、{len(failed)} 個序列失敗（共 {len(results)} 個）\n")
    for series_id, status, n, detail in results:
        if status == "OK":
            print(f"  [OK]   {series_id:32s} {n:6d} 筆  {detail}")
    if failed:
        print("\n失敗清單：")
        for series_id, status, n, detail in failed:
            print(f"  [FAIL] {series_id:32s} {detail}")


if __name__ == "__main__":
    main()
