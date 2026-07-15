"""證交所 ISIN 頁面 collector（上市/上櫃股票清單 + 官方產業別文字）。

見 docs/data-sources.md 第 1-2 節（endpoint 實測結果，2026-07-16）。

來源：https://isin.twse.com.tw/isin/C_public.jsp?strMode=2（上市）／strMode=4（上櫃）
- 回傳 HTML table，非 JSON；編碼實測為 MS950（cp950，Big5 的 Microsoft 超集），不可用預設
  utf-8 解碼（會亂碼）。**注意：不可用 Python 標準 'big5' codec**——'big5' 不含「碁」等
  擴充字集字元（byte 0xf9 起會 raise UnicodeDecodeError 或被 requests 靜默 replace 成
  U+FFFD 亂碼，例如 6285 啟碁 曾因此寫入資料庫變成「啟��」），必須用 'cp950'。
- 頁面依「有價證券類型」分成多個區塊（股票／權證／ETF／特別股／TDR／REITs／創新板...），
  區塊標題是 `<td colspan=7><B>區塊名稱<B></td>`，區塊內每列固定 7 欄：
  代號及名稱、ISIN、上市日、市場別、產業別、CFICode、備註。
  **只有「股票」與「創新板」區塊的產業別欄位有值**，其餘區塊（權證/ETF/特別股/TDR/REITs）
  產業別欄位是空字串。本 collector 擷取「股票」＋「創新板」兩區塊，等同排除
  ETF/權證/特別股/TDR/REITs 等非普通股項目（見 docs/data-sources.md 過濾邏輯說明）。
  「創新板」（臺灣創新板）是 TWSE 底下另一個上市層級（門檻較寬鬆的成長型企業專板），
  代號與「股票」區塊不重複，本質仍是普通股、有真實產業別分類，故納入 market='TWSE'。
- 上市日欄位已是西元年 YYYY/MM/DD 格式（與 TWSE OpenAPI 常見的民國年不同，此處不需轉換）。
- 代號及名稱欄位以全形空格（　U+3000）分隔代號與名稱，非半形空格。
"""
from __future__ import annotations

import re

from models import CollectorError

from ._http import get

SOURCE = "isin"

_URL = "https://isin.twse.com.tw/isin/C_public.jsp"

_TOKEN_RE = re.compile(
    r"(?P<header>colspan=7\s*><B>\s*.*?\s*<B>\s*</td>)"
    r"|(?P<row><tr><td bgcolor=#FAFAD2>[^<]*</td><td bgcolor=#FAFAD2>[^<]*</td>"
    r"<td bgcolor=#FAFAD2>[^<]*</td><td bgcolor=#FAFAD2>[^<]*</td>"
    r"<td bgcolor=#FAFAD2>[^<]*</td><td bgcolor=#FAFAD2>[^<]*</td>"
    r"<td bgcolor=#FAFAD2>[^<]*</td></tr>)"
)
_SECTION_RE = re.compile(r"colspan=7\s*><B>\s*(.*?)\s*<B>\s*</td>")
_ROW_RE = re.compile(
    r"<tr><td bgcolor=#FAFAD2>([^<]*)</td><td bgcolor=#FAFAD2>([^<]*)</td>"
    r"<td bgcolor=#FAFAD2>([^<]*)</td><td bgcolor=#FAFAD2>([^<]*)</td>"
    r"<td bgcolor=#FAFAD2>([^<]*)</td><td bgcolor=#FAFAD2>([^<]*)</td>"
    r"<td bgcolor=#FAFAD2>([^<]*)</td></tr>"
)

_STOCK_SECTION_NAMES = {"股票", "創新板"}


def _parse_stock_section(html: str) -> list[dict]:
    """只擷取「股票」＋「創新板」區塊的資料列；其餘區塊（權證/ETF/特別股/TDR/REITs）一律略過。"""
    current_section = None
    rows: list[dict] = []
    for m in _TOKEN_RE.finditer(html):
        if m.group("header"):
            hm = _SECTION_RE.search(m.group(0))
            current_section = hm.group(1) if hm else None
            continue
        if current_section not in _STOCK_SECTION_NAMES:
            continue
        rm = _ROW_RE.match(m.group("row"))
        code_name = rm.group(1)
        parts = code_name.split("　", 1)  # 全形空格分隔
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else ""
        rows.append({
            "stock_id": code,
            "name": name,
            "isin": rm.group(2).strip(),
            "listed_date": rm.group(3).strip().replace("/", "-"),
            "market_label": rm.group(4).strip(),  # 原始文字，如「上市」「上櫃」
            "industry_name": rm.group(5).strip(),
            "cfi_code": rm.group(6).strip(),
        })
    return rows


def _fetch(str_mode: int, market: str) -> list[dict]:
    """market: 'TWSE'（strMode=2）或 'TPEx'（strMode=4）。空資料回空 list，不造假。"""
    resp = get(SOURCE, _URL, params={"strMode": str_mode}, throttle_bucket="isin",
               encoding="cp950")
    html = resp.text
    if not html or "股票" not in html:
        # HTTP 200 但頁面內容不含預期區塊（例如證交所改版），回空並讓呼叫端自行決定是否視為異常
        return []
    rows = _parse_stock_section(html)
    for row in rows:
        row["market"] = market
    return rows


def fetch_twse_stocks() -> list[dict]:
    """上市股票清單（strMode=2）。"""
    try:
        return _fetch(2, "TWSE")
    except CollectorError:
        raise
    except Exception as e:  # noqa: BLE001 — 轉譯為統一錯誤型別
        raise CollectorError(SOURCE, f"parse failed: {e}", retriable=False) from e


def fetch_tpex_stocks() -> list[dict]:
    """上櫃股票清單（strMode=4）。"""
    try:
        return _fetch(4, "TPEx")
    except CollectorError:
        raise
    except Exception as e:  # noqa: BLE001
        raise CollectorError(SOURCE, f"parse failed: {e}", retriable=False) from e
