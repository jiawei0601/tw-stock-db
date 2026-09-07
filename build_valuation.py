"""估值篩選表（PER/PBR 歷史分位 + 八季 EPS 品質檢查）— 合併自
`C:\\CLAUDE\\專案-投資\\ai-valuation-screen\\ai_valuation_v2.py`（AI 主鏈 83 檔）與
`semi_screen.py`（半導體全市場），統一寫進 tw-stock-db 的 SQLite，脫離散落各處的
CSV/JSON cache 手動流程。

背景：FinMind 免費額度會耗盡（402）且觸發後 IP 會被暫時封鎖（403），因此本腳本把
「灌入既有 cache」「打 API 補缺」「純本地運算篩選」三件事拆成三個獨立、冪等的子命令，
可以分開重跑、互不影響：

    python build_valuation.py --import-cache <cache.json> [--db-path PATH]
        把舊腳本留下的 JSON cache（key 格式 `股號:dataset:起日`，dataset 有
        TaiwanStockPER / TaiwanStockPrice / TaiwanStockFinancialStatements）灌入
        per_daily / eps_quarterly；daily_prices 若缺該檔資料才補（已有的不覆蓋，
        daily_prices 是全市場既有表，不是本腳本專屬）。可重複執行同一份 cache 或
        不同 cache，INSERT OR REPLACE 覆蓋同一 key，不會重複。

    python build_valuation.py --fetch [--db-path PATH]
        對 universe（AI 主鏈 + 半導體全市場）中 per_daily 或 eps_quarterly 仍缺資料的
        股票才打 FinMind API（已有資料的不重抓，天然增量續傳）。每檔請求間 sleep 0.6
        秒；遇 402（額度耗盡）或 403（IP 被封）**立刻停止**整個流程並把目前進度寫進
        valuation_fetch_log，下次重跑會自動跳過已成功的股票、從中斷處續抓。
        token 讀取方式比照 collectors/macro_tw.py 的 `_finmind_token()`：環境變數
        FINMIND_TOKEN 優先，否則讀專案根目錄 `.env`。

    python build_valuation.py --screen [--db-path PATH]
        純本地運算（不打任何網路請求），從 per_daily / eps_quarterly / daily_prices
        算出 PER/PBR 三年分位、位置、合理價、八季 EPS 品質指標、分割旗標，寫入
        valuation_screen（整批覆蓋當次 run_date，可重跑）。

三個子命令可以同一次呼叫組合使用（例如 `--import-cache a.json --import-cache b.json
--screen`），也可以分開跑。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
ENV_PATH = Path(__file__).parent / ".env"

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
PER_START = "2023-09-01"
FIN_START = "2024-01-01"
FETCH_SLEEP_SECONDS = 0.6

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS per_daily (
    stock_id        TEXT NOT NULL,
    date            TEXT NOT NULL,
    per             REAL,
    pbr             REAL,
    dividend_yield  REAL,
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS eps_quarterly (
    stock_id     TEXT NOT NULL,
    quarter_end  TEXT NOT NULL,
    eps          REAL,
    PRIMARY KEY (stock_id, quarter_end)
);

-- 【2026-09-07】專存 FinMind TaiwanStockPrice 的估值用價格表，不受 daily_prices 的
-- institutional_flow_daily 日期範圍限制、也不受 _cleanup_daily_prices_anomalies 清理
-- 邏輯影響（daily_prices 每日排程落後，會把超出範圍的價格整批砍掉，見 HANDOFF.md）。
CREATE TABLE IF NOT EXISTS fm_price_daily (
    stock_id  TEXT NOT NULL,
    date      TEXT NOT NULL,
    close     REAL,
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS valuation_fetch_log (
    stock_id    TEXT NOT NULL,
    dataset     TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    status      TEXT NOT NULL,   -- 'cached_import' / 'fetched' / 'empty' / 'error_402' / 'error_403' / 'error_other'
    rows        INTEGER NOT NULL,
    PRIMARY KEY (stock_id, dataset, fetched_at)
);

CREATE TABLE IF NOT EXISTS valuation_screen (
    run_date         TEXT NOT NULL,
    stock_id         TEXT NOT NULL,
    name             TEXT,
    universe         TEXT NOT NULL,   -- 'ai_chain' / 'semiconductor'（重疊者兩邊各一列）
    sub              TEXT,            -- 子產業/區塊標籤
    price            REAL,
    per              REAL,
    per_p25          REAL,
    per_p50          REAL,
    per_p75          REAL,
    position         REAL,
    fair_low         REAL,
    fair_high        REAL,
    pbr              REAL,
    pbr_p25          REAL,
    pbr_p75          REAL,
    dividend_yield   REAL,
    per_points       INTEGER,
    eps_quarters     INTEGER,
    eps_cv           REAL,
    loss_q           INTEGER,
    eps_ttm_growth   REAL,
    split_flag       INTEGER NOT NULL DEFAULT 0,
    band_ok          INTEGER NOT NULL DEFAULT 0,
    not_ok_reason    TEXT,
    category         TEXT,
    sub_multi        INTEGER NOT NULL DEFAULT 0,  -- 【2026-09-07】該股票在 stock_sub_industry
                                                   -- 有多列（跨節點重疊），sub 只取第一列代表值
    rev_ym_latest    TEXT,             -- 【2026-09-07】monthly_revenue 最新月份，格式 YYYYMM
    rev_yoy_3m       REAL,             -- 最近 3 個月營收合計年增率（小數，加總不取平均）
    rev_yoy_ytd      REAL,             -- 最新月 cumulative_yoy_pct 換算成小數
    rev_eps_diverge  INTEGER NOT NULL DEFAULT 0,  -- 營收轉負但 EPS 仍在成長頂點：
                                                   -- rev_yoy_3m<-0.10 且 eps_ttm_growth>0.20
    price_date       TEXT,              -- 【2026-09-07】price 實際取自哪一天（可能早於
                                         -- run_date，也可能是 daily_prices fallback）
    per_jump_flag    INTEGER NOT NULL DEFAULT 0,  -- 【2026-09-07】per_daily 單日 PER 跳動
                                         -- >40% 且附近無 eps_quarterly 同期跳變可解釋的標記，
                                         -- 純觀察用途，不影響 band_ok / not_ok_reason（見
                                         -- HANDOFF.md 撤回紀錄：曾誤把此當 split_flag 判準）
    PRIMARY KEY (run_date, stock_id, universe)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    # 【2026-09-07】既有 db 若是舊版建的 valuation_screen（沒有 sub_multi 欄位），
    # CREATE TABLE IF NOT EXISTS 不會補欄位，這裡用 ALTER TABLE 補上（冪等，欄位已
    # 存在時吞掉錯誤）。
    try:
        conn.execute("ALTER TABLE valuation_screen ADD COLUMN sub_multi INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # 【2026-09-07】營收 vs EPS 背離四欄，比照 sub_multi 的冪等 ALTER TABLE 補欄位模式。
    for col_sql in (
        "ALTER TABLE valuation_screen ADD COLUMN rev_ym_latest TEXT",
        "ALTER TABLE valuation_screen ADD COLUMN rev_yoy_3m REAL",
        "ALTER TABLE valuation_screen ADD COLUMN rev_yoy_ytd REAL",
        "ALTER TABLE valuation_screen ADD COLUMN rev_eps_diverge INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE valuation_screen ADD COLUMN price_date TEXT",
        "ALTER TABLE valuation_screen ADD COLUMN per_jump_flag INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(col_sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Universe 定義（純資料，不打網路請求 —— 刻意不 import ai_valuation_v2.py / semi_screen.py，
# 那兩支是 top-level script，import 會直接觸發它們的抓取邏輯）
# ---------------------------------------------------------------------------

# ---- AI 主鏈（搬自 ai_valuation_v2.py，含去重邏輯：AI 主鏈優先保留） ----
_AI_MAIN = {
    "2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "6669": "緯穎",
    "2454": "聯發科", "3711": "日月光投控", "2308": "台達電", "3017": "奇鋐", "3324": "雙鴻",
    "3653": "健策", "2376": "技嘉", "2356": "英業達", "3706": "神達", "5274": "信驊",
    "3661": "世芯-KY", "3443": "創意", "2383": "台光電", "2345": "智邦", "2059": "川湖",
    "2301": "光寶科", "2449": "京元電子", "3037": "欣興", "2408": "南亞科", "2344": "華邦電",
    "2303": "聯電", "8996": "高力", "2404": "漢唐", "6274": "台燿", "2368": "金像電",
    "6488": "環球晶", "3680": "家登", "6515": "穎崴", "2360": "致茂", "3034": "聯詠",
    "2379": "瑞昱", "8299": "群聯",
}

_EXT_GROUPS = {
    "光通訊": {
        "3081": "聯亞", "4979": "華星光", "6442": "光聖", "3363": "上詮",
        "4908": "前鼎", "4977": "眾達-KY", "3163": "波若威", "3450": "聯鈞",
        "6426": "統新", "8111": "立碁", "3596": "智易", "3380": "明泰",
        "6451": "訊芯-KY", "4991": "環宇-KY", "3234": "光環",
    },
    "氮化鎵/化合物半導體": {
        "3707": "漢磊", "3016": "嘉晶", "3105": "穩懋", "8086": "宏捷科",
        "2455": "全新", "2342": "茂矽", "2340": "台亞", "6488": "環球晶",
        "6854": "錼創-KY",
    },
    "功率元件": {
        "2481": "強茂", "5425": "台半", "3675": "德微", "8255": "朋程",
        "8261": "富鼎", "3317": "尼克森", "6435": "大中", "5305": "敦南",
        "6573": "虹揚-KY", "5299": "杰力", "6138": "茂達", "8081": "致新",
    },
    "被動元件": {
        "2327": "國巨", "2492": "華新科", "2456": "奇力新", "3026": "禾伸堂",
        "6173": "信昌電", "2472": "立隆電", "5317": "凱美", "3236": "千如",
        "2428": "興勤", "3624": "光頡", "2437": "旺詮", "6284": "佳邦",
        "6449": "鈺邦", "5328": "華容",
    },
}

_AI_CHAIN_EXCLUDE = {"2412"}


def ai_chain_universe() -> dict[str, tuple[str, str, bool]]:
    """回傳 {stock_id: (name, sub, sub_multi)}，比照 ai_valuation_v2.py 的去重邏輯
    （AI主鏈優先）。sub_multi 固定 False（這條 universe 的 sub 本來就是寫死人工標記，
    沒有「多節點來源」的概念，欄位存在只是跟 semiconductor_universe 的回傳形狀一致，
    方便 screen() 統一處理）。"""
    universe: dict[str, tuple[str, str, bool]] = {}
    for sid, name in _AI_MAIN.items():
        if sid in _AI_CHAIN_EXCLUDE:
            continue
        universe[sid] = (name, "AI主鏈", False)
    for group, stocks in _EXT_GROUPS.items():
        for sid, name in stocks.items():
            if sid in _AI_CHAIN_EXCLUDE:
                continue
            if sid not in universe:
                universe[sid] = (name, group, False)
    return universe


# ---- 半導體子產業標記 ----
# 【2026-09-07 起】改讀 stock_sub_industry 表（來源：證交所/櫃買產業價值鏈資訊平台
# https://ic.tpex.org.tw/，見 build_sub_industry.py），取代原本寫死在這裡的
# SEMI_SUB_MAP dict（人工憑印象填寫、覆蓋率不足且無來源可查證，committee 2026-09-07
# 評估時發現 147/190 檔落「其他」而汰換）。一檔股票在產業鏈平台可能對應多個節點
# （見 collectors/industry_chain.py 模組說明），此處固定取第一列（按 node 字母序），
# 呼叫端用 sub_multi 欄位得知是否有多列被捨棄，不代表該欄位以外的列不存在。


def semiconductor_universe(conn: sqlite3.Connection) -> dict[str, tuple[str, str, bool]]:
    """回傳 {stock_id: (name, sub, sub_multi)}，universe 來源 = stocks 表 industry_name
    含「半導體」的上市上櫃全部股票。sub 來自 stock_sub_industry（chain='半導體'，
    一檔多列時取 node 字母序第一列）；查無任何節點列的股票 sub 標「其他」，
    不臆測。sub_multi=True 表示該股票在 stock_sub_industry 有多列（跨節點重疊，
    例如同時是晶圓製造又是DRAM製造），只取了其中一列當代表值。"""
    rows = conn.execute(
        "SELECT stock_id, name FROM stocks WHERE industry_name LIKE '%半導體%'"
    ).fetchall()
    sub_rows = conn.execute(
        "SELECT stock_id, sub FROM stock_sub_industry WHERE chain = '半導體' ORDER BY stock_id, node"
    ).fetchall()
    sub_by_stock: dict[str, list[str]] = {}
    for sid, sub in sub_rows:
        sub_by_stock.setdefault(sid, []).append(sub)

    result = {}
    for sid, name in rows:
        subs = sub_by_stock.get(sid)
        if not subs:
            result[sid] = (name, "其他", False)
        else:
            result[sid] = (name, subs[0], len(subs) > 1)
    return result


# ---------------------------------------------------------------------------
# --import-cache
# ---------------------------------------------------------------------------

def _cleanup_daily_prices_anomalies(conn: sqlite3.Connection) -> int:
    """自我修復：清掉任何違反 daily_prices 既有 invariant 的列（close<=0，或超出
    institutional_flow_daily 日期範圍）。冪等，每次 import_cache 開頭都會跑一次，
    修正舊版本（未過濾 close<=0/日期範圍前）留下的殘留列，回傳刪除列數。"""
    inst_range = conn.execute("SELECT MIN(date), MAX(date) FROM institutional_flow_daily").fetchone()
    inst_min, inst_max = inst_range if inst_range and inst_range[0] else (None, None)
    if inst_min is None:
        cur = conn.execute("DELETE FROM daily_prices WHERE close <= 0")
    else:
        cur = conn.execute(
            "DELETE FROM daily_prices WHERE close <= 0 OR date < ? OR date > ?",
            (inst_min, inst_max),
        )
    conn.commit()
    return cur.rowcount


def import_cache(conn: sqlite3.Connection, cache_path: Path) -> dict:
    _cleanup_daily_prices_anomalies(conn)
    with open(cache_path, encoding="utf-8") as f:
        cache = json.load(f)

    per_rows = 0
    eps_rows = 0
    price_rows = 0
    now = _now_iso()

    for key, data in cache.items():
        if not data or ":" not in key:
            continue
        parts = key.split(":", 2)
        if len(parts) != 3:
            continue
        sid, dataset, _start = parts

        if dataset == "TaiwanStockPER":
            batch = [
                (sid, d["date"], d.get("PER"), d.get("PBR"), d.get("dividend_yield"))
                for d in data if d.get("date")
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO per_daily (stock_id, date, per, pbr, dividend_yield) "
                "VALUES (?, ?, ?, ?, ?)",
                batch,
            )
            per_rows += len(batch)
            conn.execute(
                "INSERT OR REPLACE INTO valuation_fetch_log (stock_id, dataset, fetched_at, status, rows) "
                "VALUES (?, ?, ?, 'cached_import', ?)",
                (sid, dataset, now, len(batch)),
            )

        elif dataset == "TaiwanStockFinancialStatements":
            batch = [
                (sid, d["date"], d.get("value"))
                for d in data if d.get("type") == "EPS" and d.get("date")
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO eps_quarterly (stock_id, quarter_end, eps) VALUES (?, ?, ?)",
                batch,
            )
            eps_rows += len(batch)
            conn.execute(
                "INSERT OR REPLACE INTO valuation_fetch_log (stock_id, dataset, fetched_at, status, rows) "
                "VALUES (?, ?, ?, 'cached_import', ?)",
                (sid, dataset, now, len(batch)),
            )

        elif dataset == "TaiwanStockPrice":
            # 【2026-09-07 改動】估值用價格改灌 fm_price_daily，不再動 daily_prices。
            # 理由：daily_prices 是全市場既有表，其 invariant 是「日期範圍不可超出
            # institutional_flow_daily」，而 institutional_flow_daily 由每日排程刷新、
            # 常態性落後（實測落後到 08-25 而 per_daily 已到 09-04），把估值用的最新
            # 價格寫進 daily_prices 會被 _cleanup_daily_prices_anomalies 依 invariant
            # 整批砍掉（這正是緯穎 6669 一拆三後新價格沒進 daily_prices、split_flag
            # 誤判為 0 的根因）。fm_price_daily 只有 close<=0 這一條過濾，不受
            # institutional_flow_daily 日期範圍限制，直接整批 INSERT OR REPLACE
            # （比 daily_prices 的「只補缺」寬鬆，因為這裡沒有跟另一支官方回補腳本
            # 互相覆蓋的疑慮，fm_price_daily 只有這支腳本會寫）。
            batch = [
                (sid, d["date"], d.get("close"))
                for d in data
                if d.get("date") and d.get("close") is not None and d["close"] > 0
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO fm_price_daily (stock_id, date, close) VALUES (?, ?, ?)",
                batch,
            )
            price_rows += len(batch)
            conn.execute(
                "INSERT OR REPLACE INTO valuation_fetch_log (stock_id, dataset, fetched_at, status, rows) "
                "VALUES (?, ?, ?, 'cached_import', ?)",
                (sid, dataset, now, len(batch)),
            )

    conn.commit()
    return {"per_daily_rows": per_rows, "eps_quarterly_rows": eps_rows, "fm_price_daily_rows": price_rows}


# ---------------------------------------------------------------------------
# --fetch（本次任務不執行；FinMind 免費額度已耗盡且 IP 暫時被封）
# ---------------------------------------------------------------------------

def _finmind_token() -> str:
    """比照 collectors/macro_tw.py 的 `_finmind_token()`：環境變數優先，否則讀 .env。"""
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if token:
        return token
    try:
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FINMIND_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _fetch_finmind(session, dataset: str, sid: str, start_date: str, token: str) -> tuple[list, int | None]:
    """回傳 (data, http_status_flag)；http_status_flag 只在 402/403 時回傳該碼，其餘回 None。"""
    resp = session.get(FINMIND_URL, params={
        "dataset": dataset, "data_id": sid, "start_date": start_date, "token": token,
    }, timeout=30)
    if resp.status_code in (402, 403):
        return [], resp.status_code
    payload = resp.json()
    if payload.get("status") == 200:
        return payload.get("data", []), None
    return [], None


def fetch_missing(conn: sqlite3.Connection, universe: dict[str, tuple[str, str]]) -> dict:
    """對 universe 中 per_daily / eps_quarterly / fm_price_daily 仍缺資料的股票補抓
    FinMind。有資料的（該表已有該股票任何一列）視為「已抓過」直接跳過 —— 不逐筆比對
    日期範圍是否完整，維持跟舊腳本一致的『整段快取即視為完成』語意。
    【2026-09-07 起】新增 TaiwanStockPrice → fm_price_daily（原本本函式只抓
    TaiwanStockPER / TaiwanStockFinancialStatements，完全沒有補抓價格的路徑——這是
    之前那批 155 檔 --fetch 沒有留下任何價格資料的根因，見 HANDOFF.md）。
    遇 402/403 立即停止，回傳目前進度供呼叫端印出。"""
    import requests  # 延遲 import，--import-cache / --screen 不需要 requests 也能跑

    token = _finmind_token()
    session = requests.Session()
    now = _now_iso()

    have_per = {row[0] for row in conn.execute("SELECT DISTINCT stock_id FROM per_daily").fetchall()}
    have_eps = {row[0] for row in conn.execute("SELECT DISTINCT stock_id FROM eps_quarterly").fetchall()}
    have_price = {row[0] for row in conn.execute("SELECT DISTINCT stock_id FROM fm_price_daily").fetchall()}

    fetched = 0
    skipped = 0
    stopped_reason = None

    for sid in sorted(universe.keys()):
        if stopped_reason:
            break
        for dataset, start_date, have_set in (
            ("TaiwanStockPER", PER_START, have_per),
            ("TaiwanStockFinancialStatements", FIN_START, have_eps),
            ("TaiwanStockPrice", PER_START, have_price),
        ):
            if sid in have_set:
                skipped += 1
                continue
            data, err_status = _fetch_finmind(session, dataset, sid, start_date, token)
            if err_status is not None:
                status = f"error_{err_status}"
                conn.execute(
                    "INSERT OR REPLACE INTO valuation_fetch_log (stock_id, dataset, fetched_at, status, rows) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (sid, dataset, now, status),
                )
                conn.commit()
                stopped_reason = status
                break
            status = "fetched" if data else "empty"
            conn.execute(
                "INSERT OR REPLACE INTO valuation_fetch_log (stock_id, dataset, fetched_at, status, rows) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, dataset, now, status, len(data)),
            )
            if dataset == "TaiwanStockPER" and data:
                batch = [(sid, d["date"], d.get("PER"), d.get("PBR"), d.get("dividend_yield"))
                         for d in data if d.get("date")]
                conn.executemany(
                    "INSERT OR REPLACE INTO per_daily (stock_id, date, per, pbr, dividend_yield) "
                    "VALUES (?, ?, ?, ?, ?)", batch)
            elif dataset == "TaiwanStockFinancialStatements" and data:
                batch = [(sid, d["date"], d.get("value")) for d in data if d.get("type") == "EPS" and d.get("date")]
                conn.executemany(
                    "INSERT OR REPLACE INTO eps_quarterly (stock_id, quarter_end, eps) VALUES (?, ?, ?)", batch)
            elif dataset == "TaiwanStockPrice" and data:
                batch = [(sid, d["date"], d.get("close"))
                         for d in data if d.get("date") and d.get("close") is not None and d["close"] > 0]
                conn.executemany(
                    "INSERT OR REPLACE INTO fm_price_daily (stock_id, date, close) VALUES (?, ?, ?)", batch)
            conn.commit()
            fetched += 1
            time.sleep(FETCH_SLEEP_SECONDS)

    return {"fetched": fetched, "skipped": skipped, "stopped_reason": stopped_reason}


# ---------------------------------------------------------------------------
# --screen（純本地運算）
# ---------------------------------------------------------------------------

def _pct(vals: list[float], q: float) -> float:
    vals_sorted = sorted(vals)
    k = (len(vals_sorted) - 1) * q
    f = int(k)
    c = min(f + 1, len(vals_sorted) - 1)
    if f == c:
        return vals_sorted[f]
    return vals_sorted[f] + (vals_sorted[c] - vals_sorted[f]) * (k - f)


def _detect_split_flag(prices_sorted: list[tuple[str, float]]) -> bool:
    """近 60 個交易日內收盤價單日跳動 > 40% 視為疑似分割/減資（搬自 semi_screen.py）。"""
    recent = prices_sorted[-61:] if len(prices_sorted) >= 2 else prices_sorted
    for i in range(1, len(recent)):
        prev_close = recent[i - 1][1]
        cur_close = recent[i][1]
        if prev_close and cur_close and prev_close > 0:
            if abs(cur_close / prev_close - 1) > 0.4:
                return True
    return False


def _detect_split_via_per_jump(
    per_rows_sorted: list[tuple[str, float]],
    eps_quarter_ends: list[str],
) -> bool:
    """【2026-09-07 新增】第二道分割/減資保險：只靠 fm_price_daily/daily_prices 抓
    分割會有盲點——如果價格資料本身缺漏（例如某檔剛好兩份 cache 都沒收到、也還沒
    --fetch 補上），單日跳價根本看不到。但 PER = price / EPS，只要 EPS 分母沒變，
    分割造成的價格跳動一樣會反映在 PER 的日對日跳動上（例如 1 股拆 3 股，價格變 1/3，
    PER 也跟著變 1/3，跟真的獲利掉了 2/3 在數字上無法區分，必須排除「EPS 剛好在同一
    時間點跳變」的情況——那種是財報認列的正常波動，不是分割）。
    邏輯：per_daily 中若某日 PER 相對前一日變動 |Δ|>40%，且該日期附近（±10 天，涵蓋
    財報公告到 FinMind 更新的時間差）沒有任何 eps_quarterly 的 quarter_end，視為
    疑似分割（EPS 沒有同期跳變可以解釋這個 PER 跳動，只能是價格本身跳動）。"""
    if len(per_rows_sorted) < 2:
        return False
    from datetime import date as _date

    def _to_date(s: str):
        try:
            return _date.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    quarter_dates = [d for d in (_to_date(q) for q in eps_quarter_ends) if d is not None]

    for i in range(1, len(per_rows_sorted)):
        prev_date, prev_per = per_rows_sorted[i - 1]
        cur_date, cur_per = per_rows_sorted[i]
        if not prev_per or not cur_per or prev_per <= 0:
            continue
        if abs(cur_per / prev_per - 1) <= 0.4:
            continue
        cur_d = _to_date(cur_date)
        if cur_d is None:
            continue
        near_quarter_end = any(abs((cur_d - qd).days) <= 10 for qd in quarter_dates)
        if not near_quarter_end:
            return True
    return False


def _revenue_metrics(conn: sqlite3.Connection, sid: str, eps_ttm_growth: float | None) -> dict:
    """從 monthly_revenue 算營收 vs EPS 背離四欄（純本地運算，不打 FinMind API）。

    - rev_ym_latest：最新月份，DB 存 'YYYY-MM'，輸出轉成 'YYYYMM'。
    - rev_yoy_3m：最近 3 個月 revenue 合計 / 去年同 3 個月 revenue_last_year_month 合計 − 1
      （用金額加總，不對 yoy_pct 取平均）；不足 3 個月或任一月缺 revenue_last_year_month
      記 None。
    - rev_yoy_ytd：最新月 cumulative_yoy_pct（DB 存百分比數字，如 37.01）換算成小數。
    - rev_eps_diverge：rev_yoy_3m < -0.10 且 eps_ttm_growth > 0.20 → 1，否則 0。
    """
    rows = conn.execute(
        "SELECT ym, revenue, revenue_last_year_month, cumulative_yoy_pct "
        "FROM monthly_revenue WHERE stock_id = ? ORDER BY ym",
        (sid,),
    ).fetchall()
    if not rows:
        return {
            "rev_ym_latest": None, "rev_yoy_3m": None, "rev_yoy_ytd": None,
            "rev_eps_diverge": 0,
        }

    latest_ym, _, _, latest_cum_yoy = rows[-1]
    rev_ym_latest = latest_ym.replace("-", "") if latest_ym else None
    rev_yoy_ytd = (latest_cum_yoy / 100.0) if latest_cum_yoy is not None else None

    last3 = rows[-3:]
    if len(last3) == 3 and all(r[1] is not None and r[2] is not None for r in last3):
        cur_sum = sum(r[1] for r in last3)
        prev_sum = sum(r[2] for r in last3)
        rev_yoy_3m = (cur_sum / prev_sum - 1) if prev_sum > 0 else None
    else:
        rev_yoy_3m = None

    rev_eps_diverge = int(
        rev_yoy_3m is not None and rev_yoy_3m < -0.10
        and eps_ttm_growth is not None and eps_ttm_growth > 0.20
    )

    return {
        "rev_ym_latest": rev_ym_latest, "rev_yoy_3m": rev_yoy_3m,
        "rev_yoy_ytd": rev_yoy_ytd, "rev_eps_diverge": rev_eps_diverge,
    }


def _price_asof(rows_sorted: list[tuple[str, float]], asof_date: str) -> tuple[float, str] | None:
    """從 (date, close) 排序列取 asof_date 當天收盤；沒有就取 <= asof_date 最近一筆。
    回傳 (close, 實際日期) 或 None（完全沒有 <= asof_date 的資料）。"""
    best = None
    for d, close in rows_sorted:
        if d > asof_date:
            break
        best = (close, d)
    return best


def _screen_one(conn: sqlite3.Connection, sid: str, name: str, universe: str, sub: str, sub_multi: bool = False) -> dict | None:
    per_rows = conn.execute(
        "SELECT date, per, pbr, dividend_yield FROM per_daily WHERE stock_id = ? ORDER BY date", (sid,)
    ).fetchall()
    pers = [(d, per, pbr, dy) for d, per, pbr, dy in per_rows if per is not None and 0 < per <= 300]
    if not pers:
        return None

    last_date = pers[-1][0]
    per_vals = [p[1] for p in pers]
    pbr_vals = [p[2] for p in pers if p[2] is not None and p[2] > 0]
    dy_vals = [p[3] for p in pers if p[3] is not None]

    n_per = len(per_vals)
    cur_per = per_vals[-1]
    cur_pbr = pbr_vals[-1] if pbr_vals else None
    cur_dy = dy_vals[-1] if dy_vals else None

    p25 = _pct(per_vals, 0.25)
    p50 = _pct(per_vals, 0.50)
    p75 = _pct(per_vals, 0.75)
    pbr_p25 = _pct(pbr_vals, 0.25) if pbr_vals else None
    pbr_p75 = _pct(pbr_vals, 0.75) if pbr_vals else None

    # 【2026-09-07 改動】price/split 偵測改讀 fm_price_daily（估值專屬、不受
    # daily_prices 的 institutional_flow_daily 日期範圍限制），fm_price_daily
    # 完全沒有該檔資料時才 fallback daily_prices（並在 price_date 標記來源）。
    fm_price_rows = conn.execute(
        "SELECT date, close FROM fm_price_daily WHERE stock_id = ? ORDER BY date", (sid,)
    ).fetchall()
    if fm_price_rows:
        price_rows = fm_price_rows
        price_source = "fm_price_daily"
    else:
        price_rows = conn.execute(
            "SELECT date, close FROM daily_prices WHERE stock_id = ? ORDER BY date", (sid,)
        ).fetchall()
        price_source = "daily_prices_fallback"

    price_result = _price_asof(price_rows, last_date)
    if price_result is not None:
        cur_price, price_date = price_result
        if price_source == "daily_prices_fallback":
            price_date = f"{price_date}(daily_prices_fallback)"
    else:
        cur_price, price_date = None, None

    # 【2026-09-07 撤回】split_flag 只由價格序列判定（fm_price_daily，缺才 fallback
    # daily_prices）。per_daily 單日 PER 跳動 >40% 曾被當成第二道分割保險，但 FinMind
    # 每季套用新一期 EPS 時 PER 本來就會跳（尤其低 EPS 小型股），「附近無 eps_quarterly
    # 同期跳變」的排除條件實際上沒擋住，273 列裡誤判到 139 列 split_flag=1。改成獨立
    # 欄位 per_jump_flag，只做標記、不影響 band_ok，見 HANDOFF.md 撤回紀錄。
    split_flag = _detect_split_flag(price_rows)
    per_jump_flag = _detect_split_via_per_jump(
        [(d, per) for d, per, _, _ in pers],
        [q for q, _ in conn.execute(
            "SELECT quarter_end, eps FROM eps_quarterly WHERE stock_id = ? AND eps IS NOT NULL", (sid,)
        ).fetchall()],
    )

    eps_ttm = (cur_price / cur_per) if (cur_price is not None and cur_per) else None

    if p75 != p25:
        position = (cur_per - p25) / (p75 - p25)
    else:
        position = 0.0

    if position < 0:
        category = "低於合理區間"
    elif position <= 1:
        category = "區間內"
    else:
        category = "高於區間"

    fair_low = eps_ttm * p25 if eps_ttm else None
    fair_high = eps_ttm * p75 if eps_ttm else None

    eps_rows = conn.execute(
        "SELECT quarter_end, eps FROM eps_quarterly WHERE stock_id = ? AND eps IS NOT NULL ORDER BY quarter_end",
        (sid,),
    ).fetchall()
    last8 = eps_rows[-8:] if len(eps_rows) >= 8 else eps_rows
    eps_vals8 = [v for _, v in last8]

    if len(eps_vals8) >= 2:
        mean8 = statistics.mean(eps_vals8)
        std8 = statistics.pstdev(eps_vals8)
        eps_cv = (std8 / abs(mean8)) if mean8 != 0 else float("inf")
    else:
        eps_cv = float("inf")

    loss_q = sum(1 for v in eps_vals8 if v <= 0)

    if len(eps_vals8) >= 8:
        recent4 = sum(eps_vals8[-4:])
        prev4 = sum(eps_vals8[-8:-4])
        eps_ttm_growth = (recent4 / prev4 - 1) if prev4 > 0 else None
    else:
        eps_ttm_growth = None

    n_eps_q = len(eps_vals8)
    # split_flag=True 一律 band_ok=False（緯穎 2026-09 一拆三是本輪新增的盲點修正，
    # 原 ai_valuation_v2.py 完全沒有分割偵測，semi_screen.py 有但只套用在半導體 universe，
    # 這裡統一套用到兩個 universe）。
    band_ok = (eps_cv < 0.5) and (loss_q == 0) and (n_per >= 300) and (n_eps_q == 8) and not split_flag

    reasons = []
    if split_flag:
        reasons.append("疑似分割/減資")
    if not (eps_cv < 0.5):
        reasons.append(f"cv={eps_cv:.2f}" if eps_cv != float("inf") else "cv=inf")
    if loss_q > 0:
        reasons.append(f"虧損{loss_q}季")
    if n_per < 300:
        reasons.append(f"PER資料點僅{n_per}")
    if n_eps_q < 8:
        reasons.append(f"EPS僅{n_eps_q}季")

    rev_metrics = _revenue_metrics(conn, sid, eps_ttm_growth)

    return {
        "stock_id": sid, "name": name, "universe": universe, "sub": sub,
        "price": cur_price, "per": cur_per, "per_p25": p25, "per_p50": p50, "per_p75": p75,
        "position": position, "fair_low": fair_low, "fair_high": fair_high,
        "pbr": cur_pbr, "pbr_p25": pbr_p25, "pbr_p75": pbr_p75, "dividend_yield": cur_dy,
        "per_points": n_per, "eps_quarters": n_eps_q,
        "eps_cv": None if eps_cv == float("inf") else eps_cv,
        "loss_q": loss_q, "eps_ttm_growth": eps_ttm_growth,
        "split_flag": split_flag, "per_jump_flag": per_jump_flag, "band_ok": band_ok,
        "not_ok_reason": "；".join(reasons) if reasons else None,
        "category": category, "last_date": last_date, "sub_multi": sub_multi,
        "price_date": price_date,
        **rev_metrics,
    }


def screen(conn: sqlite3.Connection) -> dict:
    ai_chain = ai_chain_universe()
    semiconductor = semiconductor_universe(conn)

    rows = []
    last_date_overall = None
    for universe_name, universe in (("ai_chain", ai_chain), ("semiconductor", semiconductor)):
        for sid, (name, sub, sub_multi) in universe.items():
            r = _screen_one(conn, sid, name, universe_name, sub, sub_multi)
            if r is None:
                continue
            if last_date_overall is None or r["last_date"] > last_date_overall:
                last_date_overall = r["last_date"]
            rows.append(r)

    if last_date_overall is None:
        return {"run_date": None, "rows_written": 0, "band_ok": 0, "categories": {}}

    run_date = last_date_overall
    conn.execute("DELETE FROM valuation_screen WHERE run_date = ?", (run_date,))
    for r in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO valuation_screen (
                run_date, stock_id, name, universe, sub, price, per,
                per_p25, per_p50, per_p75, position, fair_low, fair_high,
                pbr, pbr_p25, pbr_p75, dividend_yield, per_points, eps_quarters,
                eps_cv, loss_q, eps_ttm_growth, split_flag, band_ok, not_ok_reason, category,
                sub_multi, rev_ym_latest, rev_yoy_3m, rev_yoy_ytd, rev_eps_diverge, price_date,
                per_jump_flag
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date, r["stock_id"], r["name"], r["universe"], r["sub"], r["price"], r["per"],
                r["per_p25"], r["per_p50"], r["per_p75"], r["position"], r["fair_low"], r["fair_high"],
                r["pbr"], r["pbr_p25"], r["pbr_p75"], r["dividend_yield"], r["per_points"], r["eps_quarters"],
                r["eps_cv"], r["loss_q"], r["eps_ttm_growth"], int(r["split_flag"]), int(r["band_ok"]),
                r["not_ok_reason"], r["category"], int(r["sub_multi"]),
                r["rev_ym_latest"], r["rev_yoy_3m"], r["rev_yoy_ytd"], int(r["rev_eps_diverge"]),
                r["price_date"], int(r["per_jump_flag"]),
            ),
        )
    conn.commit()

    band_ok_count = sum(1 for r in rows if r["band_ok"])
    categories: dict[str, int] = {}
    for r in rows:
        categories[r["category"]] = categories.get(r["category"], 0) + 1

    return {"run_date": run_date, "rows_written": len(rows), "band_ok": band_ok_count, "categories": categories}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="估值篩選表建置（cache 匯入 / FinMind 補抓 / 本地篩選運算）")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--import-cache", action="append", default=[], metavar="CACHE_JSON",
                         help="可重複指定，依序匯入多份舊腳本留下的 JSON cache")
    parser.add_argument("--fetch", action="store_true", help="對缺資料的股票打 FinMind API 補抓")
    parser.add_argument("--screen", action="store_true", help="本地運算篩選並寫入 valuation_screen")
    args = parser.parse_args()

    conn = get_conn(args.db_path)
    try:
        for cache_path in args.import_cache:
            print(f"匯入 cache: {cache_path}")
            result = import_cache(conn, Path(cache_path))
            print(f"  per_daily +{result['per_daily_rows']} 列、"
                  f"eps_quarterly +{result['eps_quarterly_rows']} 列、"
                  f"fm_price_daily +{result['fm_price_daily_rows']} 列")

        if args.fetch:
            universe = {**ai_chain_universe(), **semiconductor_universe(conn)}
            print(f"開始補抓 FinMind，universe 共 {len(universe)} 檔（已有資料的會跳過）")
            result = fetch_missing(conn, universe)
            print(f"本次新抓 {result['fetched']} 個 (股票,dataset)，跳過已有 {result['skipped']} 個")
            if result["stopped_reason"]:
                print(f"因 {result['stopped_reason']} 提前停止，下次重跑會從中斷處續抓")

        if args.screen:
            result = screen(conn)
            print(f"valuation_screen 寫入完成：run_date={result['run_date']}，"
                  f"{result['rows_written']} 列，band_ok={result['band_ok']}")
            print(f"分類分布：{result['categories']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
