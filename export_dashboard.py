"""台股資金流向儀表板 v1 —— 完全獨立、免伺服器、瀏覽器雙擊即開的靜態 HTML。

【第十一輪】本專案主產出物。彙整前十輪累積的板塊／族群資金流資料（金額口徑）＋
TAIEX＋個股彙總數字（月營收年增率、籌碼集中度），做成單一 HTML 儀表板，比照
`export_sector_flow_animation.py` 的模式：資料內嵌 JSON、Chart.js 走 CDN、
color-scheme 明確鎖 light（避免瀏覽器深色模式反轉既有教訓）。

**產品定位（誠實邊界，也寫進頁面本身）**：這是資金結構的觀察工具，不是訊號產生器。
`analysis/` 下五份「法人資金流向預測力系列」報告已確立：法人流向資料「描述當下
有效、預測未來全部失敗」，頁面底部「使用須知」區塊如實引用這個結論。

資料來源（全部唯讀，本腳本不寫入資料庫）：
  - `sector_flow_value_weekly` / `sector_flow_value_daily`：板塊金額口徑週/日彙總
  - `group_flow_value_weekly` / `group_flow_value_daily`：族群金額口徑週/日彙總
    （**族群成分重疊，數字不可跨族群相加**，見 AGENTS.md Interface Contract）
  - `taiex_daily`：大盤加權指數日收盤
  - `institutional_flow_daily` JOIN `daily_prices`：個股近 4 週（近 20 交易日）
    法人買賣超金額（現算，非既有彙總表）
  - `monthly_revenue`／`shareholding_concentration`：個股最新一期月營收年增率／
    籌碼集中度（只嵌最新一筆彙總數字，不嵌時序，控制檔案大小）
  - `stocks`／`stock_groups`：股票基本資料與族群標記

用法：
    python export_dashboard.py [--db-path PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_OUT_PATH = Path(__file__).parent / "dashboard.html"

YI = 100_000_000  # 億元換算

ANALYSIS_REPORTS = [
    "taiex-flow-correlation-2026-07-16.md",
    "flow-persistence-seasonality-2026-07-16.md",
    "trust-streak-price-impact-2026-07-17.md",
    "trust-streak-taiex-2026-07-17.md",
    "sector-flow-entry-signal-2026-07-17.md",
]


# ---------------------------------------------------------------------------
# 資料撈取
# ---------------------------------------------------------------------------

def _weeks(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT DISTINCT week_index, week_start, week_end FROM sector_flow_value_weekly "
        "ORDER BY week_index"
    ).fetchall()
    return [{"i": i, "s": s, "e": e} for i, s, e in rows]


def _taiex_weekly(conn: sqlite3.Connection, weeks: list[dict]) -> list[float | None]:
    """每週取區間內最後一個交易日的收盤點（週線）。"""
    taiex = conn.execute("SELECT date, close FROM taiex_daily ORDER BY date").fetchall()
    out: list[float | None] = []
    last_seen: float | None = None
    for wk in weeks:
        in_range = [c for d, c in taiex if wk["s"] <= d <= wk["e"]]
        if in_range:
            last_seen = in_range[-1]
        out.append(round(last_seen, 0) if last_seen is not None else None)
    return out


def _market_weekly(conn: sqlite3.Connection, weeks: list[dict]) -> dict:
    """全市場週度三大法人金額（板塊彙總表跨板塊 SUM，因每檔股票只屬一個板塊、
    加總語意合法，跟 group_flow_* 不可跨族群相加不同）。NULL 視為 0（該板塊當週
    完全無法換算金額，屬極少數缺口，見 sector_flow_value_* NULL 語意）。"""
    rows = conn.execute(
        "SELECT week_index, "
        "SUM(COALESCE(foreign_value,0)), SUM(COALESCE(trust_value,0)), "
        "SUM(COALESCE(dealer_value,0)), SUM(COALESCE(total_value,0)) "
        "FROM sector_flow_value_weekly GROUP BY week_index ORDER BY week_index"
    ).fetchall()
    by_week = {r[0]: r[1:] for r in rows}
    foreign, trust, dealer, total = [], [], [], []
    for wk in weeks:
        f, t, d, tot = by_week.get(wk["i"], (0, 0, 0, 0))
        foreign.append(round(f / YI, 1))
        trust.append(round(t / YI, 1))
        dealer.append(round(d / YI, 1))
        total.append(round(tot / YI, 1))
    return {"foreign": foreign, "trust": trust, "dealer": dealer, "total": total}


def _heatmap(conn: sqlite3.Connection, weeks: list[dict], table: str, name_col: str) -> dict:
    """依三年總金額活動量（SUM(ABS(total_value))）由大到小排序取得標籤，
    再組出 labels x weeks 的矩陣（億元，1 位小數；NULL 視為 0）。"""
    order = conn.execute(
        f"SELECT {name_col}, SUM(ABS(COALESCE(total_value,0))) as act FROM {table} "
        f"GROUP BY {name_col} ORDER BY act DESC"
    ).fetchall()
    labels = [r[0] for r in order]
    label_idx = {lab: i for i, lab in enumerate(labels)}
    week_idx = {wk["i"]: i for i, wk in enumerate(weeks)}

    matrix = [[0.0] * len(weeks) for _ in labels]
    rows = conn.execute(
        f"SELECT {name_col}, week_index, total_value FROM {table}"
    ).fetchall()
    for lab, wi, val in rows:
        if lab not in label_idx or wi not in week_idx:
            continue
        matrix[label_idx[lab]][week_idx[wi]] = round((val or 0) / YI, 1)
    return {"labels": labels, "matrix": matrix}


def _ranking(conn: sqlite3.Connection, weeks: list[dict], table: str, name_col: str, n_weeks: int) -> dict:
    """近 N 週淨流入排行（前十/後十），依 SUM(total_value) 排序。"""
    max_i = weeks[-1]["i"]
    min_i = max(0, max_i - n_weeks + 1)
    rows = conn.execute(
        f"SELECT {name_col}, SUM(COALESCE(total_value,0)) as net FROM {table} "
        f"WHERE week_index BETWEEN ? AND ? GROUP BY {name_col} ORDER BY net DESC",
        (min_i, max_i),
    ).fetchall()
    ranked = [{"name": r[0], "v": round(r[1] / YI, 1)} for r in rows]
    return {"top": ranked[:10], "bottom": list(reversed(ranked[-10:]))}


def _last_n_trading_days(conn: sqlite3.Connection, n: int) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM institutional_flow_daily ORDER BY date DESC LIMIT ?", (n,)
    ).fetchall()
    return [r[0] for r in rows]


def _stock_near4w_summary(conn: sqlite3.Connection) -> dict:
    """全部個股近 4 週（近 20 交易日）法人淨額（億元，現算：股數 x 收盤價，
    收盤價缺口的日子自然被排除，不臆測），附最新月營收年增率、最新籌碼集中度。
    只在 Python 內存留一份完整字典供後續依板塊/族群篩選 top10，最終只有
    被選中的 top10 會寫進輸出 JSON（見呼叫端），不會把全部 1970 檔都嵌進頁面。"""
    dates = _last_n_trading_days(conn, 20)
    if not dates:
        return {}
    placeholders = ",".join("?" * len(dates))
    rows = conn.execute(
        f"SELECT f.stock_id, "
        f"SUM((f.foreign_net + f.trust_net + f.dealer_net) * p.close) as net_value "
        f"FROM institutional_flow_daily f "
        f"JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date "
        f"WHERE f.date IN ({placeholders}) "
        f"GROUP BY f.stock_id",
        dates,
    ).fetchall()
    net_by_stock = {r[0]: r[1] for r in rows}

    yoy_rows = conn.execute(
        "SELECT m.stock_id, m.yoy_pct FROM monthly_revenue m "
        "JOIN (SELECT stock_id, MAX(ym) as ym FROM monthly_revenue GROUP BY stock_id) latest "
        "ON m.stock_id = latest.stock_id AND m.ym = latest.ym"
    ).fetchall()
    yoy_by_stock = {r[0]: r[1] for r in yoy_rows}

    conc_rows = conn.execute(
        "SELECT s.stock_id, s.pct_gt_400zhang FROM shareholding_concentration s "
        "JOIN (SELECT stock_id, MAX(as_of) as as_of FROM shareholding_concentration GROUP BY stock_id) latest "
        "ON s.stock_id = latest.stock_id AND s.as_of = latest.as_of"
    ).fetchall()
    conc_by_stock = {r[0]: r[1] for r in conc_rows}

    names = dict(conn.execute("SELECT stock_id, name FROM stocks").fetchall())

    summary = {}
    for stock_id, net_value in net_by_stock.items():
        summary[stock_id] = {
            "id": stock_id,
            "name": names.get(stock_id, stock_id),
            "net4w": round(net_value / YI, 2),
            "yoy": round(yoy_by_stock[stock_id], 1) if yoy_by_stock.get(stock_id) is not None else None,
            "pct400": round(conc_by_stock[stock_id], 1) if conc_by_stock.get(stock_id) is not None else None,
        }
    return summary


def _sector_drill_stocks(conn: sqlite3.Connection, labels: list[str], stock_summary: dict) -> dict:
    rows = conn.execute("SELECT stock_id, industry_name FROM stocks WHERE industry_name IS NOT NULL").fetchall()
    by_sector: dict[str, list[str]] = {lab: [] for lab in labels}
    for stock_id, industry_name in rows:
        if industry_name in by_sector:
            by_sector[industry_name].append(stock_id)

    drill = {}
    for lab in labels:
        entries = [stock_summary[sid] for sid in by_sector[lab] if sid in stock_summary]
        entries.sort(key=lambda e: e["net4w"], reverse=True)
        drill[lab] = entries[:10]
    return drill


def _group_drill_stocks(conn: sqlite3.Connection, labels: list[str], stock_summary: dict) -> dict:
    rows = conn.execute("SELECT stock_id, group_name FROM stock_groups").fetchall()
    by_group: dict[str, list[str]] = {lab: [] for lab in labels}
    for stock_id, group_name in rows:
        if group_name in by_group:
            by_group[group_name].append(stock_id)

    drill = {}
    for lab in labels:
        entries = [stock_summary[sid] for sid in by_group[lab] if sid in stock_summary]
        entries.sort(key=lambda e: e["net4w"], reverse=True)
        drill[lab] = entries[:10]
    return drill


def _current_streak(series: list[tuple[str, float]]) -> dict:
    """series 依日期升冪排列的 (date, value) list，從最後一天往回數連續同號天數。
    0 視為中性、會打斷連續。"""
    if not series:
        return {"sign": "none", "days": 0, "last_date": None}
    last_val = series[-1][1]
    if last_val == 0:
        return {"sign": "flat", "days": 0, "last_date": series[-1][0]}
    sign = 1 if last_val > 0 else -1
    days = 0
    for _, v in reversed(series):
        s = 1 if v > 0 else (-1 if v < 0 else 0)
        if s != sign:
            break
        days += 1
    return {"sign": "buy" if sign > 0 else "sell", "days": days, "last_date": series[-1][0]}


def _next_quarter_end(last_date_str: str) -> dict:
    y, m, d = (int(x) for x in last_date_str.split("-"))
    cur = date(y, m, d)
    quarter_end_months = [3, 6, 9, 12]
    candidates = []
    for qm in quarter_end_months:
        last_day = 31 if qm in (3, 12) else 30
        candidates.append(date(y, qm, last_day))
        candidates.append(date(y + 1, qm, last_day))
    future = sorted(c for c in candidates if c > cur)
    nxt = future[0]
    return {"date": nxt.isoformat(), "calendar_days": (nxt - cur).days}


def _trust_spotlight(conn: sqlite3.Connection, weeks: list[dict], market_weekly: dict) -> dict:
    daily_rows = conn.execute(
        "SELECT date, SUM(COALESCE(trust_value,0)) FROM sector_flow_value_daily "
        "GROUP BY date ORDER BY date"
    ).fetchall()
    streak = _current_streak(daily_rows)
    quarter_info = _next_quarter_end(daily_rows[-1][0]) if daily_rows else {"date": None, "calendar_days": None}

    sector_daily = conn.execute(
        "SELECT industry_name, date, COALESCE(trust_value,0) FROM sector_flow_value_daily ORDER BY industry_name, date"
    ).fetchall()
    by_sector: dict[str, list[tuple[str, float]]] = {}
    for name, d, v in sector_daily:
        by_sector.setdefault(name, []).append((d, v))
    sector_streaks = []
    for name, series in by_sector.items():
        st = _current_streak(series)
        if st["sign"] == "buy":
            sector_streaks.append({"name": name, "days": st["days"]})
    sector_streaks.sort(key=lambda e: e["days"], reverse=True)

    return {
        "market_weekly_trust": market_weekly["trust"],
        "streak": streak,
        "next_quarter_end": quarter_info,
        "top5_buying_sectors": sector_streaks[:5],
        "seasonality_note": (
            "投信季底最後 5 個交易日日均淨買超 +4,314 萬元，高於其他日均值 +1,676 萬元"
            "（差距 +2,638 萬元，較平日高約 157%，t 檢定 p=0.024 達統計顯著），"
            "是本專案五份分析中唯一明確且符合市場傳說的季節性規律"
            "（見 flow-persistence-seasonality-2026-07-16.md）。"
        ),
    }


# ---------------------------------------------------------------------------
# HTML 組裝
# ---------------------------------------------------------------------------

def _build_data(conn: sqlite3.Connection) -> dict:
    weeks = _weeks(conn)
    taiex_weekly = _taiex_weekly(conn, weeks)
    market_weekly = _market_weekly(conn, weeks)

    sector_heat = _heatmap(conn, weeks, "sector_flow_value_weekly", "industry_name")
    group_heat = _heatmap(conn, weeks, "group_flow_value_weekly", "group_name")

    sector_rank4 = _ranking(conn, weeks, "sector_flow_value_weekly", "industry_name", 4)
    sector_rank12 = _ranking(conn, weeks, "sector_flow_value_weekly", "industry_name", 12)
    group_rank4 = _ranking(conn, weeks, "group_flow_value_weekly", "group_name", 4)
    group_rank12 = _ranking(conn, weeks, "group_flow_value_weekly", "group_name", 12)

    stock_summary = _stock_near4w_summary(conn)
    sector_drill = _sector_drill_stocks(conn, sector_heat["labels"], stock_summary)
    group_drill = _group_drill_stocks(conn, group_heat["labels"], stock_summary)

    trust_spotlight = _trust_spotlight(conn, weeks, market_weekly)

    latest_date = conn.execute("SELECT MAX(date) FROM institutional_flow_daily").fetchone()[0]

    return {
        "meta": {
            "latest_date": latest_date,
            "n_weeks": len(weeks),
            "n_sectors": len(sector_heat["labels"]),
            "n_groups": len(group_heat["labels"]),
        },
        "weeks": weeks,
        "taiex_weekly": taiex_weekly,
        "market_weekly": market_weekly,
        "sector_heat": sector_heat,
        "group_heat": group_heat,
        "sector_rank": {"w4": sector_rank4, "w12": sector_rank12},
        "group_rank": {"w4": group_rank4, "w12": group_rank12},
        "sector_drill": sector_drill,
        "group_drill": group_drill,
        "trust": trust_spotlight,
    }


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>台股資金流向儀表板</title>
<style>
  html { color-scheme: light; }
  * { box-sizing: border-box; }
  body { font-family: "Microsoft JhengHei", "微軟正黑體", sans-serif; margin: 0; padding: 0;
         background: #ffffff; color: #1a1a1a; }
  header { position: sticky; top: 0; background: #ffffff; border-bottom: 1px solid #ddd;
           padding: 10px 20px; z-index: 10; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; white-space: nowrap; }
  header nav a { margin-right: 12px; font-size: 13px; color: #186bb5; text-decoration: none; }
  header nav a:hover { text-decoration: underline; }
  header .meta { margin-left: auto; font-size: 12px; color: #888; }
  main { max-width: 1200px; margin: 0 auto; padding: 8px 20px 60px; }
  section { padding-top: 56px; margin-top: -40px; }
  h2 { font-size: 18px; border-left: 4px solid #378ADD; padding-left: 8px; margin-top: 36px; }
  h3 { font-size: 14px; color: #333; }
  p.note { font-size: 12px; color: #888; }
  .charts-row { display: flex; flex-direction: column; gap: 4px; }
  .chart-box { position: relative; width: 100%; }
  .chart-box.tall { height: 260px; }
  .chart-box.short { height: 160px; }
  .btn-row { display: flex; gap: 6px; margin: 8px 0; flex-wrap: wrap; }
  button.toggle { font-size: 12px; padding: 4px 10px; border: 1px solid #ccc; background: #f5f5f5;
                  border-radius: 4px; cursor: pointer; }
  button.toggle.active { background: #378ADD; color: #fff; border-color: #378ADD; }
  select { font-size: 13px; padding: 4px 8px; }
  table.heat { border-collapse: collapse; font-size: 10px; }
  table.heat th, table.heat td { border: 1px solid #eee; padding: 0; text-align: center; }
  table.heat th.rowlabel, table.heat td.rowlabel { text-align: left; padding: 1px 6px; white-space: nowrap;
      font-size: 11px; position: sticky; left: 0; background: #fff; }
  table.heat td.cell { width: 6px; min-width: 6px; height: 16px; cursor: default; }
  .heat-wrap { overflow-x: auto; border: 1px solid #eee; }
  .legend { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #666; margin: 6px 0; }
  .legend .bar { width: 160px; height: 12px; background: linear-gradient(to right, #D85A30, #ffffff, #378ADD); }
  .rank-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .rank-grid table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .rank-grid th, .rank-grid td { padding: 3px 6px; border-bottom: 1px solid #eee; text-align: right; }
  .rank-grid td:first-child, .rank-grid th:first-child { text-align: left; cursor: pointer; color: #186bb5; }
  .pos { color: #186bb5; } .neg { color: #b5401e; }
  .drill-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 8px; }
  .drill-table th, .drill-table td { padding: 4px 8px; border-bottom: 1px solid #eee; text-align: right; }
  .drill-table th:nth-child(1), .drill-table td:nth-child(1),
  .drill-table th:nth-child(2), .drill-table td:nth-child(2) { text-align: left; }
  .warn-box { background: #fff8e6; border: 1px solid #f0d78c; padding: 8px 12px; font-size: 12px;
              border-radius: 4px; margin: 8px 0; }
  .kpi-row { display: flex; gap: 16px; flex-wrap: wrap; margin: 12px 0; }
  .kpi { border: 1px solid #eee; border-radius: 6px; padding: 10px 16px; min-width: 160px; }
  .kpi .label { font-size: 12px; color: #888; }
  .kpi .value { font-size: 20px; font-weight: 600; margin-top: 2px; }
  #notice { background: #fafafa; border: 1px solid #eee; border-radius: 6px; padding: 16px 20px; font-size: 13px; }
  #notice ul { margin: 6px 0; padding-left: 20px; }
  footer { text-align: center; font-size: 12px; color: #aaa; padding: 20px; }
</style>
</head><body>
<header>
  <h1>台股資金流向儀表板</h1>
  <nav>
    <a href="#overview">總覽</a>
    <a href="#heatmap">板塊熱力圖</a>
    <a href="#ranking">板塊排行/下鑽</a>
    <a href="#groups">族群視圖</a>
    <a href="#trust">投信特寫</a>
    <a href="#notice">使用須知</a>
  </nav>
  <span class="meta">資料至 <span id="latestDate"></span>｜金額口徑（億元，股數 x 收盤價約略值）</span>
</header>
<main>

<section id="overview">
  <h2>總覽</h2>
  <p class="note">上圖為 TAIEX 週線，下圖為全市場三大法人週度淨買賣超金額（可切換 外資/投信/自營商/合計），
  兩圖時間軸對齊，共用同一組週別。</p>
  <div class="charts-row">
    <div class="chart-box short"><canvas id="taiexChart"></canvas></div>
    <div class="btn-row" id="marketToggle"></div>
    <div class="chart-box tall"><canvas id="marketChart"></canvas></div>
  </div>
</section>

<section id="heatmap">
  <h2>板塊熱力圖</h2>
  <p class="note">34 個官方產業別板塊（依 3 年總金額活動量排序）x 150 週，色階：藍=淨流入、橘紅=淨流出、白=近零。
  滑鼠移到格子上可看板塊/週期間/金額。</p>
  <div class="legend"><span>淨流出</span><span class="bar"></span><span>淨流入</span></div>
  <div class="heat-wrap"><table class="heat" id="sectorHeatTable"></table></div>
</section>

<section id="ranking">
  <h2>板塊排行與下鑽</h2>
  <div class="rank-grid" id="sectorRankGrid"></div>
  <h3>下鑽：選擇板塊看 150 週金額流走勢與成分股</h3>
  <select id="sectorDrillSelect"></select>
  <div class="chart-box tall"><canvas id="sectorDrillChart"></canvas></div>
  <table class="drill-table" id="sectorDrillTable"></table>
</section>

<section id="groups">
  <h2>族群視圖（概念股）</h2>
  <div class="warn-box">族群成分重疊（例如台積電同時屬於多個概念股族群），以下數字<b>不可跨族群加總</b>，
  只在單一族群自己內部的語意下解讀。</div>
  <div class="legend"><span>淨流出</span><span class="bar"></span><span>淨流入</span></div>
  <div class="heat-wrap"><table class="heat" id="groupHeatTable"></table></div>
  <div class="rank-grid" id="groupRankGrid"></div>
  <h3>下鑽：選擇族群看 150 週金額流走勢與成分股</h3>
  <select id="groupDrillSelect"></select>
  <div class="chart-box tall"><canvas id="groupDrillChart"></canvas></div>
  <table class="drill-table" id="groupDrillTable"></table>
</section>

<section id="trust">
  <h2>投信特寫</h2>
  <p class="note">五份分析報告中唯一具統計顯著規律的法人別：投信有季底作帳現象、連續買賣超對自身後續動向有一定慣性。</p>
  <div class="kpi-row" id="trustKpis"></div>
  <div class="chart-box tall"><canvas id="trustChart"></canvas></div>
  <h3>投信目前連買中的板塊（前 5 名，依連續淨買天數排序）</h3>
  <table class="drill-table" id="trustSectorTable"></table>
</section>

<section id="notice">
  <h2>使用須知</h2>
  <p>本頁是<b>資金結構的觀察工具，不是訊號產生器</b>。專案累積的五份分析報告已確立：
  法人流向資料「描述當下有效、預測未來全部失敗」——能如實呈現資金去了哪裡，
  但沒有找到可扣除交易成本後仍站得住腳的預測性訊號，請勿把任何頁面上的排行/熱力圖
  當成買賣建議。</p>
  <ul id="reportList"></ul>
</section>

</main>
<footer>台股上市（TWSE）+ 上櫃（TPEx）資料，個人投資用途，非投資建議。</footer>

<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
const DATA = __DATA_JSON__;
const REPORTS = __REPORTS_JSON__;
const YI_COLOR_POS = '#378ADD';
const YI_COLOR_NEG = '#D85A30';

function fmt1(v) {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString('en-US', {minimumFractionDigits: 1, maximumFractionDigits: 1});
}
function fmtSigned1(v) {
  if (v === null || v === undefined) return '—';
  return (v >= 0 ? '+' : '') + fmt1(v);
}
function fmt2(v) {
  if (v === null || v === undefined) return '—';
  return v.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

document.getElementById('latestDate').textContent = DATA.meta.latest_date;

const weekLabels = DATA.weeks.map(w => w.s);

// ---- 總覽：TAIEX 週線 + 全市場三大法人週度金額（切換） ----
const taiexChart = new Chart(document.getElementById('taiexChart'), {
  type: 'line',
  data: { labels: weekLabels, datasets: [{
    data: DATA.taiex_weekly, borderColor: '#534AB7', backgroundColor: 'rgba(83,74,183,0.08)',
    fill: true, tension: 0.15, pointRadius: 0, borderWidth: 1.5,
  }]},
  options: { responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, title: { display: true, text: 'TAIEX 週線（收盤點）', font: {size: 12} } },
    scales: { x: { ticks: { maxTicksLimit: 12, color: '#898781' } }, y: { ticks: { color: '#898781' } } } }
});

const marketDatasetKeys = ['total', 'foreign', 'trust', 'dealer'];
const marketDatasetLabels = { total: '合計', foreign: '外資', trust: '投信', dealer: '自營商' };
let marketCurrentKey = 'total';

function marketColors(arr) { return arr.map(v => v >= 0 ? YI_COLOR_POS : YI_COLOR_NEG); }

const marketChart = new Chart(document.getElementById('marketChart'), {
  type: 'bar',
  data: { labels: weekLabels, datasets: [{
    data: DATA.market_weekly[marketCurrentKey], backgroundColor: marketColors(DATA.market_weekly[marketCurrentKey]),
  }]},
  options: { responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, title: { display: true, text: '全市場三大法人週度淨買賣超（合計，億元）', font: {size: 12} } },
    scales: { x: { ticks: { maxTicksLimit: 12, color: '#898781' } }, y: { ticks: { color: '#898781' } } } }
});

const marketToggleDiv = document.getElementById('marketToggle');
marketDatasetKeys.forEach(key => {
  const btn = document.createElement('button');
  btn.className = 'toggle' + (key === marketCurrentKey ? ' active' : '');
  btn.textContent = marketDatasetLabels[key];
  btn.addEventListener('click', () => {
    marketCurrentKey = key;
    marketChart.data.datasets[0].data = DATA.market_weekly[key];
    marketChart.data.datasets[0].backgroundColor = marketColors(DATA.market_weekly[key]);
    marketChart.options.plugins.title.text = '全市場三大法人週度淨買賣超（' + marketDatasetLabels[key] + '，億元）';
    marketChart.update();
    [...marketToggleDiv.children].forEach(b => b.classList.toggle('active', b === btn));
  });
  marketToggleDiv.appendChild(btn);
});

// ---- 熱力圖 ----
function colorScale(v, scaleMax) {
  if (scaleMax <= 0) return '#ffffff';
  const t = Math.max(-1, Math.min(1, v / scaleMax));
  if (t >= 0) {
    // 白 -> 藍
    const c = Math.round(255 - t * (255 - 0x37));
    const g = Math.round(255 - t * (255 - 0x8A));
    const b = Math.round(255 - t * (255 - 0xDD));
    return `rgb(${c},${g},${b})`;
  } else {
    const tt = -t;
    const c = Math.round(255 - tt * (255 - 0xD8));
    const g = Math.round(255 - tt * (255 - 0x5A));
    const b = Math.round(255 - tt * (255 - 0x30));
    return `rgb(${c},${g},${b})`;
  }
}

function buildHeatTable(tableEl, heat, weeks) {
  const allVals = heat.matrix.flat();
  const absSorted = allVals.map(Math.abs).sort((a, b) => a - b);
  const p95 = absSorted[Math.floor(absSorted.length * 0.95)] || 1;

  const thead = document.createElement('thead');
  const trh = document.createElement('tr');
  const th0 = document.createElement('th'); th0.className = 'rowlabel'; th0.textContent = '';
  trh.appendChild(th0);
  weeks.forEach(w => { const th = document.createElement('th'); trh.appendChild(th); });
  thead.appendChild(trh);
  tableEl.appendChild(thead);

  const tbody = document.createElement('tbody');
  heat.labels.forEach((label, ri) => {
    const tr = document.createElement('tr');
    const tdLabel = document.createElement('td');
    tdLabel.className = 'rowlabel';
    tdLabel.textContent = label;
    tr.appendChild(tdLabel);
    heat.matrix[ri].forEach((v, ci) => {
      const td = document.createElement('td');
      td.className = 'cell';
      td.style.backgroundColor = colorScale(v, p95);
      const wk = weeks[ci];
      td.title = `${label} / ${wk.s} ~ ${wk.e} / ${fmtSigned1(v)} 億元`;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  tableEl.appendChild(tbody);
}

buildHeatTable(document.getElementById('sectorHeatTable'), DATA.sector_heat, DATA.weeks);
buildHeatTable(document.getElementById('groupHeatTable'), DATA.group_heat, DATA.weeks);

// ---- 排行 ----
function buildRankGrid(container, rank, titlePrefix, onClickName) {
  container.innerHTML = '';
  const blocks = [
    ['近 4 週淨流入 Top 10', rank.w4.top], ['近 4 週淨流出 Top 10', rank.w4.bottom],
    ['近 12 週淨流入 Top 10', rank.w12.top], ['近 12 週淨流出 Top 10', rank.w12.bottom],
  ];
  blocks.forEach(([title, list]) => {
    const box = document.createElement('div');
    const h = document.createElement('h3'); h.textContent = title; box.appendChild(h);
    const table = document.createElement('table');
    const tbody = document.createElement('tbody');
    list.forEach(item => {
      const tr = document.createElement('tr');
      const tdName = document.createElement('td');
      tdName.textContent = item.name;
      tdName.addEventListener('click', () => onClickName(item.name));
      const tdVal = document.createElement('td');
      tdVal.textContent = fmtSigned1(item.v) + ' 億元';
      tdVal.className = item.v >= 0 ? 'pos' : 'neg';
      tr.appendChild(tdName); tr.appendChild(tdVal);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
    container.appendChild(box);
  });
}

// ---- 下鑽（板塊/族群共用邏輯） ----
function makeDrill(selectEl, chartCanvas, tableEl, heat, drillData, rankContainer, rankData) {
  selectEl.innerHTML = '';
  heat.labels.forEach(label => {
    const opt = document.createElement('option'); opt.value = label; opt.textContent = label;
    selectEl.appendChild(opt);
  });

  const chart = new Chart(chartCanvas, {
    type: 'bar',
    data: { labels: weekLabels, datasets: [{ data: [], backgroundColor: [] }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false }, title: { display: true, text: '', font: {size: 12} } },
      scales: { x: { ticks: { maxTicksLimit: 12, color: '#898781' } }, y: { ticks: { color: '#898781' } } } }
  });

  function renderStockTable(label) {
    tableEl.innerHTML = '';
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>代號</th><th>名稱</th><th>近4週法人淨額(億元)</th><th>最新月營收YoY%</th><th>籌碼集中度(>400張,%)</th></tr>';
    tableEl.appendChild(thead);
    const tbody = document.createElement('tbody');
    (drillData[label] || []).forEach(s => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${s.id}</td><td>${s.name}</td>` +
        `<td class="${s.net4w >= 0 ? 'pos' : 'neg'}">${fmtSigned1(s.net4w)}</td>` +
        `<td>${s.yoy === null ? '—' : fmtSigned1(s.yoy) + '%'}</td>` +
        `<td>${s.pct400 === null ? '—' : fmt1(s.pct400) + '%'}</td>`;
      tbody.appendChild(tr);
    });
    tableEl.appendChild(tbody);
  }

  function renderLabel(label) {
    const ri = heat.labels.indexOf(label);
    const series = ri >= 0 ? heat.matrix[ri] : [];
    chart.data.datasets[0].data = series;
    chart.data.datasets[0].backgroundColor = marketColors(series);
    chart.options.plugins.title.text = label + ' 週度金額流（億元）';
    chart.update();
    renderStockTable(label);
    selectEl.value = label;
  }

  selectEl.addEventListener('change', () => renderLabel(selectEl.value));
  renderLabel(heat.labels[0]);

  buildRankGrid(rankContainer, rankData, '', renderLabel);
}

makeDrill(
  document.getElementById('sectorDrillSelect'), document.getElementById('sectorDrillChart'),
  document.getElementById('sectorDrillTable'), DATA.sector_heat, DATA.sector_drill,
  document.getElementById('sectorRankGrid'), DATA.sector_rank
);
makeDrill(
  document.getElementById('groupDrillSelect'), document.getElementById('groupDrillChart'),
  document.getElementById('groupDrillTable'), DATA.group_heat, DATA.group_drill,
  document.getElementById('groupRankGrid'), DATA.group_rank
);

// ---- 投信特寫 ----
const trustKpiDiv = document.getElementById('trustKpis');
const streak = DATA.trust.streak;
const streakLabel = streak.sign === 'buy' ? '連續買超' : (streak.sign === 'sell' ? '連續賣超' : '無明確方向');
const qend = DATA.trust.next_quarter_end;
const kpis = [
  { label: '投信目前狀態', value: streakLabel + (streak.days ? '（' + streak.days + ' 天）' : '') },
  { label: '距下個季底', value: qend.date ? (qend.calendar_days + ' 個日曆天（' + qend.date + '）') : '—' },
];
kpis.forEach(k => {
  const box = document.createElement('div'); box.className = 'kpi';
  box.innerHTML = `<div class="label">${k.label}</div><div class="value">${k.value}</div>`;
  trustKpiDiv.appendChild(box);
});
const seasonalityP = document.createElement('p');
seasonalityP.className = 'note';
seasonalityP.style.width = '100%';
seasonalityP.textContent = DATA.trust.seasonality_note;
trustKpiDiv.appendChild(seasonalityP);

new Chart(document.getElementById('trustChart'), {
  type: 'bar',
  data: { labels: weekLabels, datasets: [{
    data: DATA.trust.market_weekly_trust, backgroundColor: marketColors(DATA.trust.market_weekly_trust),
  }]},
  options: { responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, title: { display: true, text: '全市場投信週度淨買賣超（億元）', font: {size: 12} } },
    scales: { x: { ticks: { maxTicksLimit: 12, color: '#898781' } }, y: { ticks: { color: '#898781' } } } }
});

const trustSectorTableEl = document.getElementById('trustSectorTable');
const tsThead = document.createElement('thead');
tsThead.innerHTML = '<tr><th>板塊</th><th>連續淨買天數</th></tr>';
trustSectorTableEl.appendChild(tsThead);
const tsBody = document.createElement('tbody');
if (DATA.trust.top5_buying_sectors.length === 0) {
  const tr = document.createElement('tr');
  tr.innerHTML = '<td colspan="2">目前沒有任何板塊處於投信連續買超狀態</td>';
  tsBody.appendChild(tr);
} else {
  DATA.trust.top5_buying_sectors.forEach(s => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${s.name}</td><td class="pos">${s.days} 天</td>`;
    tsBody.appendChild(tr);
  });
}
trustSectorTableEl.appendChild(tsBody);

// ---- 使用須知：報告清單 ----
const reportListEl = document.getElementById('reportList');
REPORTS.forEach(r => {
  const li = document.createElement('li');
  li.textContent = 'analysis/' + r;
  reportListEl.appendChild(li);
});
</script>
</body></html>
"""


def export(db_path: Path, out_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        data = _build_data(conn)
    finally:
        conn.close()

    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    reports_json = json.dumps(ANALYSIS_REPORTS, ensure_ascii=False)

    html = _HTML_TEMPLATE.replace("__DATA_JSON__", data_json).replace("__REPORTS_JSON__", reports_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    return {
        "out_path": str(out_path),
        "size_bytes": out_path.stat().st_size,
        "n_weeks": data["meta"]["n_weeks"],
        "n_sectors": data["meta"]["n_sectors"],
        "n_groups": data["meta"]["n_groups"],
        "latest_date": data["meta"]["latest_date"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH)
    args = parser.parse_args()

    summary = export(args.db_path, args.out)
    print(
        f"匯出完成：{summary['n_sectors']} 板塊 / {summary['n_groups']} 族群 / {summary['n_weeks']} 週，"
        f"資料至 {summary['latest_date']}，檔案大小 {summary['size_bytes'] / 1024:.1f} KB -> {summary['out_path']}"
    )


if __name__ == "__main__":
    main()
