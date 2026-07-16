"""MOPS 公開資訊觀測站歷史月營收封存頁面 collector（近 3 年月營收，含 MoM/YoY）。

跟 `collectors/revenue.py`（TWSE/TPEx opendata，只回「最新一期」）不同，這是唯一能查
「歷史特定年月」月營收的免費來源，但格式是 HTML 頁面（非 JSON），需自行解析。

見 docs/data-sources.md 第 12 節（endpoint 實測結果，2026-07-16）。

- URL: https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc_year}_{month}_0.html
  market: 'sii'（上市）/ 'otc'（上櫃）；roc_year 民國年；month 1~12（不補零）。
  **注意網域是 mopsov.twse.com.tw，不是 mops.twse.com.tw（後者會 404）。**
- 編碼：宣告 charset=big5，但實測須用 'cp950'（MS950 超集）才能正確解碼，跟
  `collectors/isin.py` 踩過的坑同理，不可用 Python 標準 'big5' codec。
- 格式：HTML，依產業別分成多個子表格，每個公司一列，固定 11 欄（代號、名稱、
  當月營收、上月營收、去年當月營收、上月比較增減%（MoM）、去年同月增減%（YoY）、
  當月累計營收、去年累計營收、前期比較增減%（累計YoY）、備註）。
- 每頁另有一個頁面級「出表日期」（非逐公司），本 collector 用它當該頁全部列的
  `announce_date`（近似值，不是每家公司個別公告日，來源格式限制，見 data-sources.md）。
- 該年月尚未公告或無資料時，頁面出現「查無資料」字樣、無資料表格，視為空月份，
  不是錯誤（例如尚未到來的未來月份）。
"""
from __future__ import annotations

import re

from ._http import get

SOURCE = "revenue_history"

_URL_TMPL = "https://mopsov.twse.com.tw/nas/t21/{market_path}/t21sc03_{roc_year}_{month}_0.html"
_MARKET_PATH = {"TWSE": "sii", "TPEx": "otc"}

# 陷阱 1：組間絕對不要加 `\s*` 去「順便」吃掉數字前導空白——`\s*` 與後面的 `[^<]*` 對同一段
# 空白有歧義的多種切法，在近 44 萬字元的單行 HTML 上會觸發災難性回溯（catastrophic
# backtracking），實測會直接掛住、逾時都跑不完。`[^<]*` 本身就會吃掉空白，取值後再
# `.strip()` 即可，不需要 `\s*`（本專案第五輪任務實測踩過這個坑）。
# 陷阱 2：最後一欄「備註」的 `<td>` alignment 不是固定 `align=center`——只有備註為空
# （顯示 `-`）時才是 `align=center`；備註有實際文字時（例如 2330 台積電 2026-06
# 「因先進製程產品需求增加所致。」）該欄變成 `align=left`。只比對 `align=center` 會讓
# 大量有備註的公司整列完全不 match、被靜默漏掉（本專案第五輪任務實測 2026-06 TWSE
# 頁面漏掉的列中就包含 2330，直到核對已知標的才發現），必須兩種 align 都接受。
_ROW_RE = re.compile(
    r"<tr align=right><td align=center>([^<]*)</td><td align=left>([^<]*)</td>"
    r"<td nowrap>([^<]*)</td><td nowrap>([^<]*)</td><td nowrap>([^<]*)</td>"
    r"<td nowrap>([^<]*)</td>(?:<td|<Td) nowrap>([^<]*)</td>"
    r"<td nowrap>([^<]*)</td><td nowrap>([^<]*)</td><td nowrap>([^<]*)</td>"
    r"<td align=(?:left|center)>([^<]*)</td></tr>"
)
_ANNOUNCE_DATE_RE = re.compile(r"出表日期[:：]\s*(\d+)/(\d+)/(\d+)")


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.replace(",", "").strip()
    if s in ("", "--", "-"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").strip()
    if s in ("", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_announce_date(html: str) -> str | None:
    m = _ANNOUNCE_DATE_RE.search(html)
    if not m:
        return None
    roc_year, month, day = m.groups()
    return f"{int(roc_year) + 1911:04d}-{month}-{day}"


def _parse_html(html: str, market: str, ym: str) -> list[dict]:
    if not html or "查無資料" in html or "<table" not in html:
        return []
    announce_date = _parse_announce_date(html)
    rows: list[dict] = []
    for m in _ROW_RE.finditer(html):
        stock_id = m.group(1).strip()
        if not stock_id:
            continue
        remark = m.group(11).strip()
        rows.append({
            "stock_id": stock_id,
            "company_name": m.group(2).strip(),
            "ym": ym,
            "announce_date": announce_date,
            "revenue": _to_int(m.group(3)),
            "revenue_prev_month": _to_int(m.group(4)),
            "revenue_last_year_month": _to_int(m.group(5)),
            "mom_pct": _to_float(m.group(6)),
            "yoy_pct": _to_float(m.group(7)),
            "revenue_cumulative": _to_int(m.group(8)),
            "revenue_cumulative_last_year": _to_int(m.group(9)),
            "cumulative_yoy_pct": _to_float(m.group(10)),
            "remark": remark if remark not in ("", "-") else None,
            "source_market": market,
        })
    return rows


def fetch_month(market: str, year: int, month: int) -> list[dict]:
    """抓取指定市場（'TWSE'/'TPEx'）指定西元年月的全市場月營收封存頁面。

    回傳 list[dict]（本地未過濾，呼叫端自行過濾目標股票）；該年月查無資料（未來月份、
    尚未公告）回空 list，不是錯誤，不重試。HTTP 層級錯誤（403/5xx/timeout）由
    `_http.get()` 統一拋 CollectorError（含重試）。
    """
    market_path = _MARKET_PATH[market]
    roc_year = year - 1911
    url = _URL_TMPL.format(market_path=market_path, roc_year=roc_year, month=month)
    resp = get(SOURCE, url, throttle_bucket="revenue_history", encoding="cp950")
    ym = f"{year:04d}-{month:02d}"
    return _parse_html(resp.text, market, ym)
