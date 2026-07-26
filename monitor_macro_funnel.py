"""總經崩盤漏斗週報監控 —— 依 `analysis/crash_leading_indicators.py` 實證出的三層漏斗
規則，評估 10 個訊號目前是否亮燈，組成報告推播 Telegram（排程用）。

三層漏斗（實證細節見 `analysis/crash_leading_indicators.py` 檔頭；規則以下方函式為準）：
- 第一梯隊（長週期警報，領先 1~3 年）：美10Y-3M倒掛、美10Y-2Y倒掛、台M1B-M2死亡交叉
- 第二梯隊（過熱體制標記）：台景氣燈號≥32（≥38 標示紅燈）、美CPI年增>5%、
  Fed 18個月內升息≥2pp
- 第三梯隊（轉折確認器）：台出口年增轉負、台製造業PMI<50、台領先指標6個月變動轉負、
  美失業率自12個月低點回升≥0.4pp

月頻化：沿用 `analysis/crash_leading_indicators.monthly()`（日頻序列取當月最後一筆），
跟 analysis/ 下的實證腳本語意保持一致，不重複定義一套邏輯。

**AI 週期泡沫記分卡整合**：報告在三層漏斗之後追加一段，資料來自姊妹專案
`C:\\CLAUDE\\investing\\ai-cycle-scorecard`（獨立 repo，`--refresh` 時會先
`git pull --ff-only` 拉最新、失敗不中止用本地現況）。讀法：`reports/YYYYQn.md`
取最新一季的量化指標表（🔴/🟡 燈號）與「總分計算」段落；量化小計優先信任
`data/history.jsonl` 最新一筆同季紀錄（結構化，比 markdown 正則穩定）；該專案
目前「質化小計／總分」欄位在報告 md 裡留白給人工填寫（見其 docs/CONTRACT.md
§4），若尚未手動填入，退而解析 `HANDOFF.md` 裡的一次性人工紀錄句子（例如
「質化三項人工評分完成：1+0+1...總分 6」）作備援；兩者都讀不到則該季總分
標示「尚未計算」，不臆測，不中止。整個記分卡 repo/報告讀不到 → 整段標示
「記分卡資料不可用」。

**Notion 寫入（預留介面）**：`write_notion_record()` 用 Notion REST API
`POST /v1/pages` 把報告全文寫入一列（title=當日日期，body 依段落切成
≤2000 字元的 paragraph block，Notion 單一 rich_text block 上限即為 2000）。
設定讀 repo 根 `.env` 的 `NOTION_TOKEN`／`NOTION_PARENT_ID`（環境變數優先，
比照 `collectors/macro_tw.py` 的 `_finmind_token()` 慣例）；兩者任一缺值視為
「功能未啟用」，印出提示後跳過、不算失敗。

執行：
    python monitor_macro_funnel.py --dry-run   # 完整評估、印出報告全文，不刷新不推播
    python monitor_macro_funnel.py --refresh   # 先刷新總經資料＋拉記分卡 repo，評估後推播
    python monitor_macro_funnel.py             # 直接用現有資料評估並推播

任一序列缺資料時該訊號標示「無資料」，不中止整體評估。`--refresh` 刷新失敗（例如逾時、
子行程非 0 結束）不中止，改在報告開頭標註警告、以既有資料繼續評估。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from analysis.crash_leading_indicators import add_months, monthly

REPO_ROOT = Path(__file__).parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "tw_stocks.db"
BUILD_MACRO_SCRIPT = REPO_ROOT / "build_macro.py"
BUILD_MACRO_TIMEOUT_SECONDS = 30 * 60  # 30 分鐘
TELEGRAM_NOTIFY_DIR = Path(r"C:\CLAUDE\tools\telegram")
ENV_PATH = REPO_ROOT / ".env"
AI_SCORECARD_REPO = Path(r"C:\CLAUDE\investing\ai-cycle-scorecard")
NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_BLOCK_CHAR_LIMIT = 2000


@dataclass
class Signal:
    """單一訊號評估結果。

    lit=True 表示亮燈、False 表示不亮燈、None 表示無資料（不可與「不亮燈」混用）。
    value/month 在無資料時皆為 None。note 用於額外標示（例如景氣燈號的「紅燈」）。
    """
    name: str
    lit: bool | None
    value: float | None
    month: str | None
    note: str = ""


def _latest(m: dict) -> tuple[str, float] | None:
    """回傳月頻 dict 最新月份的 (month, value)；無資料回傳 None。"""
    if not m:
        return None
    k = max(m)
    return k, m[k]


# ==================== 十個訊號評估函式（純函式，吃 monthly dict，不碰網路/DB） ====================

def eval_us_10y_3m(m_10y: dict, m_3m: dict) -> Signal:
    """第一梯隊：美 10Y-3M 倒掛（最新共同月份 10Y - 3M < 0）。"""
    common = sorted(set(m_10y) & set(m_3m))
    if not common:
        return Signal("美10Y-3M倒掛", None, None, None)
    mth = common[-1]
    val = round(m_10y[mth] - m_3m[mth], 2)
    return Signal("美10Y-3M倒掛", val < 0, val, mth)


def eval_us_10y_2y(m_spread: dict) -> Signal:
    """第一梯隊：美 10Y-2Y 倒掛（us_10y_2y_spread 最新值 < 0）。"""
    latest = _latest(m_spread)
    if latest is None:
        return Signal("美10Y-2Y倒掛", None, None, None)
    mth, val = latest
    return Signal("美10Y-2Y倒掛", val < 0, val, mth)


def eval_tw_m1b_m2_cross(m_spread: dict) -> Signal:
    """第一梯隊：台 M1B-M2 死亡交叉（tw_m1b_m2_spread 最新值 < 0）。"""
    latest = _latest(m_spread)
    if latest is None:
        return Signal("台M1B-M2死亡交叉", None, None, None)
    mth, val = latest
    return Signal("台M1B-M2死亡交叉", val < 0, val, mth)


def eval_tw_monitoring_score(m_score: dict) -> Signal:
    """第二梯隊：台景氣燈號 ≥32（黃紅以上）；≥38 額外標示紅燈字樣。"""
    latest = _latest(m_score)
    if latest is None:
        return Signal("台景氣燈號", None, None, None)
    mth, val = latest
    note = "紅燈" if val >= 38 else ""
    return Signal("台景氣燈號", val >= 32, val, mth, note)


def eval_us_cpi_yoy(m_cpi: dict) -> Signal:
    """第二梯隊：美 CPI 年增 >5%。"""
    latest = _latest(m_cpi)
    if latest is None:
        return Signal("美CPI年增", None, None, None)
    mth, val = latest
    return Signal("美CPI年增", val > 5, val, mth)


def eval_fed_hike_18m(m_ff: dict) -> Signal:
    """第二梯隊：Fed 18 個月內升息 ≥2pp（月頻最新值 − 18 個月前值）。

    18 個月前對應月份沒有資料（歷史不夠長）視為無資料，不臆測。
    """
    if not m_ff:
        return Signal("Fed18個月升息", None, None, None)
    latest_m = max(m_ff)
    prev_m = add_months(latest_m, -18)
    if prev_m not in m_ff:
        return Signal("Fed18個月升息", None, None, None)
    val = round(m_ff[latest_m] - m_ff[prev_m], 2)
    return Signal("Fed18個月升息", val >= 2.0, val, latest_m)


def eval_tw_export_yoy(m_export: dict) -> Signal:
    """第三梯隊：台出口年增轉負。"""
    latest = _latest(m_export)
    if latest is None:
        return Signal("台出口年增", None, None, None)
    mth, val = latest
    return Signal("台出口年增", val < 0, val, mth)


def eval_tw_pmi(m_pmi: dict) -> Signal:
    """第三梯隊：台製造業 PMI < 50。"""
    latest = _latest(m_pmi)
    if latest is None:
        return Signal("台製造業PMI", None, None, None)
    mth, val = latest
    return Signal("台製造業PMI", val < 50, val, mth)


def eval_tw_leading_index_turn(m_li: dict) -> Signal:
    """第三梯隊：台領先指標 6 個月變動轉負（最新值 < 6 個月前值）。

    6 個月前對應月份沒有資料視為無資料，不臆測。
    """
    if not m_li:
        return Signal("台領先指標6月變動", None, None, None)
    latest_m = max(m_li)
    prev_m = add_months(latest_m, -6)
    if prev_m not in m_li:
        return Signal("台領先指標6月變動", None, None, None)
    val = round(m_li[latest_m] - m_li[prev_m], 2)
    return Signal("台領先指標6月變動", val < 0, val, latest_m)


def eval_us_unemployment_rise(m_un: dict) -> Signal:
    """第三梯隊：美失業率自 12 個月低點回升 ≥0.4pp（不含當月本身的前 12 個月窗）。"""
    keys = sorted(m_un)
    if len(keys) < 2:
        return Signal("美失業率12月低點回升", None, None, None)
    latest_m = keys[-1]
    i = len(keys) - 1
    window = keys[max(0, i - 12):i]
    if not window:
        return Signal("美失業率12月低點回升", None, None, None)
    low = min(m_un[k] for k in window)
    val = round(m_un[latest_m] - low, 2)
    return Signal("美失業率12月低點回升", val >= 0.4, val, latest_m)


# ==================== 綜合判讀（純函式） ====================

def composite_verdict(tier1_lit: int, tier2_lit: int, tier3_lit: int) -> str:
    """依三梯隊各自亮燈數判斷綜合等級（由重到輕依序檢查，符合疊加關係）。"""
    if tier1_lit < 2:
        return "常態：無系統性警報"
    if tier2_lit >= 2 and tier3_lit >= 2:
        return "循環反轉訊號：停止逢低攤平"
    if tier2_lit >= 2:
        return "過熱確認：依SOP考慮降槓桿節奏"
    return "警戒期：歷史上距頂部通常還有1-2年，開始留意第二梯隊"


# ==================== AI 週期泡沫記分卡（讀外部 repo 的報告 md + history.jsonl） ====================

@dataclass
class ScorecardSummary:
    """AI 週期泡沫記分卡最新一季摘要。available=False 表示整段讀不到（repo/報告
    不存在或無法解析出季別），呼叫端應顯示「記分卡資料不可用」。"""
    available: bool
    quarter: str | None = None
    run_date: str | None = None
    quant_total: int | None = None
    qual_total: int | None = None
    total: int | None = None
    max_total: int = 16
    red_items: list[str] = field(default_factory=list)
    yellow_items: list[str] = field(default_factory=list)


def parse_report_header(text: str) -> tuple[str | None, str | None]:
    """從報告 md 開頭解析 (季別, 執行日期)；解析不到回 (None, None)。"""
    m = re.search(r"執行日期：(\d{4}-\d{2}-\d{2})\s*季度：(\S+)", text)
    if not m:
        return None, None
    return m.group(2), m.group(1)


def parse_quant_table(text: str) -> list[dict]:
    """解析『量化指標』markdown 表格，回傳 [{"name","value","light","reason"}, ...]；
    找不到表格回傳空清單（不中止，呼叫端據此視為紅黃燈項目皆缺）。"""
    rows: list[dict] = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("| 指標 |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        if line.startswith("|---") or set(line.strip()) <= {"|", "-", " "}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        rows.append({"name": cells[0], "value": cells[1], "light": cells[2], "reason": cells[3]})
    return rows


def parse_score_totals(text: str) -> dict:
    """從『總分計算』段落解析量化小計/質化小計/總分（可能是「＿」佔位符，代表
    尚未人工填寫，該欄回 None，不臆測）。"""

    def _search_int(pattern: str) -> int | None:
        m = re.search(pattern, text)
        return int(m.group(1)) if m else None

    return {
        "quant_total": _search_int(r"量化小計[^\n]*?\*\*(-?\d+)\*\*"),
        "qual_total": _search_int(r"質化小計[^\n]*?：\s*[*]*(-?\d+)"),
        "total": _search_int(r"總分（量化\s*\+\s*質化）[^\n]*?：\s*[*]*(-?\d+)"),
    }


def parse_handoff_qualitative_fallback(handoff_text: str, quarter: str) -> tuple[int, int] | None:
    """報告 md 的質化/總分留白時的備援：解析 HANDOFF.md 裡形如「質化三項人工評分
    完成：1+0+1...總分 6」的一次性人工紀錄句子。回傳 (質化小計, 總分)；找不到回 None，
    不臆測（這只是暫時的文字備援，若對方專案改把數字直接填回報告 md，會優先使用
    `parse_score_totals` 的結果，不會再走到這裡）。"""
    pattern = re.compile(
        re.escape(quarter) + r".*?質化三項人工評分完成：(\d)\+(\d)\+(\d)[^\n]*?總分\s*(\d+)"
    )
    m = pattern.search(handoff_text)
    if not m:
        return None
    a, b, c, total = (int(x) for x in m.groups())
    return a + b + c, total


def _latest_report_path(reports_dir: Path) -> Path | None:
    if not reports_dir.is_dir():
        return None
    candidates = [p for p in reports_dir.glob("*.md") if re.fullmatch(r"\d{4}Q\d\.md", p.name)]
    return max(candidates, default=None, key=lambda p: p.name)


def _latest_history_quant_total(history_path: Path, quarter: str) -> int | None:
    """從 data/history.jsonl 找同季最後一筆紀錄的 quant_total（結構化資料，比
    markdown 正則穩定，優先信任）。"""
    if not history_path.exists():
        return None
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("quarter") == quarter:
            return rec.get("quant_total")
    return None


def pull_scorecard_repo(repo_path: Path = AI_SCORECARD_REPO) -> bool:
    """`git pull --ff-only` 拉記分卡 repo 最新資料。失敗不中止，用本地現況繼續。"""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            print(f"[monitor_macro_funnel] AI記分卡 repo pull 失敗：{(result.stderr or '').strip()}")
        return result.returncode == 0
    except Exception as e:  # noqa: BLE001 - pull 失敗不可中止整體評估
        print(f"[monitor_macro_funnel] AI記分卡 repo pull 失敗：{e}")
        return False


def load_ai_scorecard(repo_path: Path = AI_SCORECARD_REPO) -> ScorecardSummary:
    """讀取最新一季 AI 週期泡沫記分卡摘要。任何一步讀不到/解析不到季別都回傳
    available=False，不中止整體監控。"""
    reports_dir = repo_path / "reports"
    latest = _latest_report_path(reports_dir)
    if latest is None:
        return ScorecardSummary(available=False)
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        return ScorecardSummary(available=False)

    quarter, run_date = parse_report_header(text)
    if quarter is None:
        return ScorecardSummary(available=False)

    rows = parse_quant_table(text)
    red_items = [r["name"] for r in rows if "🔴" in r["light"]]
    yellow_items = [r["name"] for r in rows if "🟡" in r["light"]]

    totals = parse_score_totals(text)
    quant_total, qual_total, total = totals["quant_total"], totals["qual_total"], totals["total"]

    hist_quant = _latest_history_quant_total(repo_path / "data" / "history.jsonl", quarter)
    if hist_quant is not None:
        quant_total = hist_quant

    if qual_total is None or total is None:
        handoff_path = repo_path / "HANDOFF.md"
        if handoff_path.exists():
            try:
                fallback = parse_handoff_qualitative_fallback(
                    handoff_path.read_text(encoding="utf-8"), quarter
                )
            except OSError:
                fallback = None
            if fallback is not None:
                qual_total, total = fallback

    if total is None and quant_total is not None and qual_total is not None:
        total = quant_total + qual_total

    return ScorecardSummary(
        available=True, quarter=quarter, run_date=run_date,
        quant_total=quant_total, qual_total=qual_total, total=total,
        red_items=red_items, yellow_items=yellow_items,
    )


def render_scorecard_section(sc: ScorecardSummary) -> str:
    """組成報告裡的「AI 週期泡沫記分卡」段落文字。"""
    if not sc.available:
        return "━ AI週期泡沫記分卡\n記分卡資料不可用"
    lines = [f"━ AI週期泡沫記分卡 {sc.quarter}（產出 {sc.run_date or '?'}）"]
    if sc.total is not None:
        lines.append(f"總分 {sc.total}/{sc.max_total}")
    else:
        qt = sc.quant_total if sc.quant_total is not None else "?"
        lines.append(f"總分 尚未計算（量化小計 {qt}，質化待人工評分）")
    for name in sc.red_items:
        lines.append(f"🔴 {name}")
    for name in sc.yellow_items:
        lines.append(f"🟡 {name}")
    if not sc.red_items and not sc.yellow_items:
        lines.append("（無紅黃燈項目）")
    return "\n".join(lines)


# ==================== Notion 寫入（預留介面，尚無憑證時安靜跳過） ====================

def load_notion_config(env_path: Path = ENV_PATH) -> dict:
    """讀 NOTION_TOKEN／NOTION_PARENT_ID：環境變數優先，否則從 repo 根 .env 讀取；
    皆無則該欄位為空字串（呼叫端據此判斷「未設定，跳過」）。"""
    cfg = {}
    lines = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    for key in ("NOTION_TOKEN", "NOTION_PARENT_ID"):
        val = os.environ.get(key, "").strip()
        if not val:
            for line in lines:
                line = line.strip()
                if line.startswith(f"{key}="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        cfg[key] = val
    return cfg


def split_into_notion_blocks(text: str, limit: int = NOTION_BLOCK_CHAR_LIMIT) -> list[str]:
    """把長文字依段落（空行分隔）切成多個 ≤limit 字元的區塊，供 Notion paragraph
    block 使用（Notion 單一 rich_text block 上限 2000 字元）。單一段落本身就超過
    limit 時直接硬切，不丟例外。"""
    paragraphs = text.split("\n\n")
    blocks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            blocks.append(current)
            current = ""
        while len(para) > limit:
            blocks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        blocks.append(current)
    return blocks


VERDICT_SELECT_OPTIONS = ("循環反轉", "過熱確認", "警戒期", "常態")


def verdict_to_select(verdict: str) -> str:
    """把 composite_verdict 的完整句子映射成 Notion select 選項名（前綴比對）。"""
    for opt in VERDICT_SELECT_OPTIONS:
        if verdict.startswith(opt):
            return opt
    return "常態"


def write_notion_record(report_text: str, cfg: dict, meta: dict | None = None) -> bool:
    """把報告全文寫入 Notion database 一列（title=當日日期，body 依段落分 block）。

    meta（選填）填入 database 的結構化欄位：verdict（綜合判讀句）、funnel
    （漏斗亮燈摘要字串）、scorecard（記分卡總分字串）。欄位名須與目標 database
    （「總經×AI泡沫 週報」）一致；欄位不存在時 Notion 會回 400，故只在有值時附上。
    cfg 缺 NOTION_TOKEN/NOTION_PARENT_ID 視為「功能未啟用」，印出提示後回傳 True
    （不算失敗）。請求例外（憑證錯誤/網路失敗等）回傳 False，不可讓主流程當掉。
    """
    token = cfg.get("NOTION_TOKEN")
    parent_id = cfg.get("NOTION_PARENT_ID")
    if not token or not parent_id:
        print("[monitor_macro_funnel] Notion 未設定，跳過")
        return True

    properties: dict = {
        "title": {"title": [{"text": {"content": f"{date.today():%Y-%m-%d}"}}]},
        "日期": {"date": {"start": f"{date.today():%Y-%m-%d}"}},
    }
    if meta:
        if meta.get("verdict"):
            properties["綜合判讀"] = {"select": {"name": verdict_to_select(meta["verdict"])}}
        if meta.get("funnel"):
            properties["漏斗亮燈"] = {"rich_text": [{"text": {"content": meta["funnel"]}}]}
        if meta.get("scorecard"):
            properties["記分卡總分"] = {"rich_text": [{"text": {"content": meta["scorecard"]}}]}

    blocks = split_into_notion_blocks(report_text)
    payload = {
        "parent": {"database_id": parent_id},
        "properties": properties,
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": b}}]},
            }
            for b in blocks
        ],
    }
    try:
        req = urllib.request.Request(
            NOTION_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:  # noqa: BLE001 - Notion 寫入失敗不可讓主流程當掉
        print(f"[monitor_macro_funnel] Notion 寫入失敗：{e}")
        return False


# ==================== 讀取 DB、組報告（跟 DB/格式相關，不做邏輯測試對象） ====================

NEEDED_SERIES_IDS = [
    "us_10y_yield", "us_3m_yield", "us_10y_2y_spread", "tw_m1b_m2_spread",
    "tw_monitoring_score", "us_cpi_yoy_sa", "us_fed_funds_rate",
    "tw_export_yoy", "tw_pmi", "tw_leading_index", "us_unemployment_rate",
]


def load_monthly_series(con: sqlite3.Connection) -> dict[str, dict]:
    """把本次評估需要的所有序列一次性讀成 monthly dict，缺資料的序列回傳空 dict。"""
    return {sid: monthly(con, sid) for sid in NEEDED_SERIES_IDS}


def evaluate_all(m: dict[str, dict]) -> tuple[list[Signal], list[Signal], list[Signal]]:
    """回傳 (第一梯隊, 第二梯隊, 第三梯隊) 訊號清單。"""
    tier1 = [
        eval_us_10y_3m(m["us_10y_yield"], m["us_3m_yield"]),
        eval_us_10y_2y(m["us_10y_2y_spread"]),
        eval_tw_m1b_m2_cross(m["tw_m1b_m2_spread"]),
    ]
    tier2 = [
        eval_tw_monitoring_score(m["tw_monitoring_score"]),
        eval_us_cpi_yoy(m["us_cpi_yoy_sa"]),
        eval_fed_hike_18m(m["us_fed_funds_rate"]),
    ]
    tier3 = [
        eval_tw_export_yoy(m["tw_export_yoy"]),
        eval_tw_pmi(m["tw_pmi"]),
        eval_tw_leading_index_turn(m["tw_leading_index"]),
        eval_us_unemployment_rise(m["us_unemployment_rate"]),
    ]
    return tier1, tier2, tier3


def _format_signal_line(sig: Signal) -> str:
    if sig.lit is None:
        return f"⚫ {sig.name} 無資料"
    icon = "🔴" if sig.lit else "⚪"
    note = f" ({sig.note})" if sig.note else ""
    month = f" ({sig.month})" if sig.month else ""
    return f"{icon} {sig.name} {sig.value:+.2f}{note}{month}"


def build_report(
    tier1: list[Signal], tier2: list[Signal], tier3: list[Signal], *, refresh_failed: bool,
    scorecard: ScorecardSummary | None = None,
) -> str:
    """組成繁體中文純文字報告（Telegram 用）。"""
    n1 = sum(1 for s in tier1 if s.lit)
    n2 = sum(1 for s in tier2 if s.lit)
    n3 = sum(1 for s in tier3 if s.lit)

    lines = []
    if refresh_failed:
        lines.append("⚠️ 資料刷新失敗，以下為既有資料")
    lines.append(f"📊 總經×AI泡沫 週日綜合監測 {date.today():%Y-%m-%d}")
    lines.append(f"━ 第一梯隊 長週期警報 {n1}/{len(tier1)}")
    lines += [_format_signal_line(s) for s in tier1]
    lines.append(f"━ 第二梯隊 過熱標記 {n2}/{len(tier2)}")
    lines += [_format_signal_line(s) for s in tier2]
    lines.append(f"━ 第三梯隊 轉折確認 {n3}/{len(tier3)}")
    lines += [_format_signal_line(s) for s in tier3]
    lines.append(f"綜合：{composite_verdict(n1, n2, n3)}")
    lines.append("註：單一訊號誤報率高，疊加判讀；外生衝擊(如2020疫情)不在預警範圍。")
    lines.append(render_scorecard_section(scorecard if scorecard is not None else ScorecardSummary(available=False)))
    return "\n".join(lines)


def refresh_macro_data() -> bool:
    """執行 `build_macro.py` 刷新總經資料。回傳是否成功（逾時/非 0 結束皆視為失敗）。"""
    try:
        result = subprocess.run(
            [sys.executable, str(BUILD_MACRO_SCRIPT)],
            cwd=str(REPO_ROOT),
            timeout=BUILD_MACRO_TIMEOUT_SECONDS,
        )
        return result.returncode == 0
    except Exception as e:  # noqa: BLE001 - 刷新失敗不可中止整體評估
        print(f"[monitor_macro_funnel] 資料刷新失敗：{e}")
        return False


def send_report(text: str) -> bool:
    """推播報告到 Telegram。回傳是否成功；設定缺失或發送例外皆回傳 False。"""
    try:
        sys.path.insert(0, str(TELEGRAM_NOTIFY_DIR))
        import notify  # type: ignore

        cfg = notify.load_env()
        token = cfg.get("TELEGRAM_BOT_TOKEN")
        chat_id = cfg.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            print("[monitor_macro_funnel] 錯誤：找不到 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
            return False
        notify.send(token, chat_id, text)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[monitor_macro_funnel] 推播失敗：{e}")
        return False


def _ensure_utf8_stdout() -> None:
    """Windows 主控台常見非 UTF-8 codepage（例如 cp950）印不出 emoji，重新配置
    stdout 編碼避免 `UnicodeEncodeError` 中斷報告輸出；失敗不影響主流程。"""
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 純輸出編碼調整，失敗不可擋主流程
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(description="總經崩盤漏斗週報監控")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--refresh", action="store_true", help="先刷新總經資料再評估")
    parser.add_argument("--dry-run", action="store_true", help="只印報告，不刷新不推播")
    args = parser.parse_args(argv)

    refresh_failed = False
    if args.refresh and not args.dry_run:
        refresh_failed = not refresh_macro_data()
        pull_scorecard_repo()

    con = sqlite3.connect(args.db_path)
    try:
        m = load_monthly_series(con)
    finally:
        con.close()

    tier1, tier2, tier3 = evaluate_all(m)
    scorecard = load_ai_scorecard()
    report = build_report(tier1, tier2, tier3, refresh_failed=refresh_failed, scorecard=scorecard)

    if args.dry_run:
        print(report)
        return 0

    print(report)
    ok = send_report(report)

    def _lit(sigs):
        return sum(1 for s in sigs if s.lit)

    meta = {
        "verdict": composite_verdict(_lit(tier1), _lit(tier2), _lit(tier3)),
        "funnel": f"一{_lit(tier1)}/3 二{_lit(tier2)}/3 三{_lit(tier3)}/4",
        "scorecard": (f"{scorecard.total}/{scorecard.max_total}"
                      if scorecard.available and scorecard.total is not None else "不可用"),
    }
    notion_cfg = load_notion_config()
    if not write_notion_record(report, notion_cfg, meta=meta):
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
