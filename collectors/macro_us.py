"""美國總經/市場序列 collector — FRED（免 API key CSV endpoint）+ Yahoo Finance
非官方 chart API（S&P 500 全歷史）。

- FRED：https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES_ID>，免 API key，
  CSV 第一欄 observation_date 已是 ISO 格式（月頻/季頻資料的日期已經對齊成當月/當季
  第一天，不需自行對齊），缺值以 '.' 表示，本 collector 一律跳過缺值列（不臆測）。
- Yahoo Finance chart API（query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>）：
  非官方但廣泛使用的公開唯讀 JSON endpoint（同 yfinance 套件底層資料來源）。**S&P 500
  原規劃來源 Stooq（stooq.com/q/d/l/）已加上瀏覽器 JavaScript proof-of-work 驗證
  （回應內容為「This site requires JavaScript to verify your browser」的挑戰頁，
  即使帶瀏覽器 UA 仍必須執行 JS 才能過關），本專案不解 CAPTCHA/bot-detection 挑戰
  （見專案安全規範），改用此替代來源。**注意**：呼叫時必須明確帶 `period1`/`period2`
  參數取得日線，若只帶 `range=max&interval=1d`，Yahoo 會靜默把大範圍請求降頻成季線
  （實測 range=max 只回 168 筆 3mo 級距資料，改用 period1=0&period2=<未來時間戳>
  才拿到 14000+ 筆真正的日線全歷史）。Yahoo ^GSPC 的日線歷史只回溯到 1970-01-02
  （比 Stooq 理論上能回溯到 1920 年代更短），已誠實記錄於 build_macro.py 的執行摘要，
  不臆測補齊更早期資料。
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from models import CollectorError

from ._http import get

SOURCE_FRED = "fred"
SOURCE_YAHOO = "yahoo"

_FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo chart API 若不帶明確的 period1/period2，range=max 會被降頻，這裡用
# period1=0（epoch 起點）+ period2=遠未來時間戳，強迫拿到完整日線歷史。
_YAHOO_PERIOD1 = 0
_YAHOO_PERIOD2 = 9999999999


def fetch_fred_series(series_id: str) -> list[dict]:
    """抓取 FRED 序列全歷史。回傳 [{"date": "YYYY-MM-DD", "value": float}, ...]，
    依日期由舊到新排序（FRED CSV 本身已經是這個順序）。缺值（'.'）直接跳過，不臆測。

    UA 注意：FRED 前面的 Akamai 會封鎖「宣稱是 Chrome 但 TLS 指紋不是瀏覽器」的請求
    （帶 _http.BROWSER_UA 反而 read timeout；requests 預設 UA 0.3 秒回 200，
    2026-07-26 實測）。故此處明確覆蓋掉 _http.get 預設的瀏覽器 UA。"""
    resp = get(SOURCE_FRED, _FRED_URL, params={"id": series_id}, throttle_bucket="fred",
               headers={"User-Agent": "python-requests (tw-stock-db macro collector)"})
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CollectorError(SOURCE_FRED, f"{series_id}: decode failed: {e}", retriable=False) from e

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    header = rows[0]
    if len(header) < 2:
        raise CollectorError(SOURCE_FRED, f"{series_id}: unexpected header {header}", retriable=False)

    out: list[dict] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        date_s = row[0].strip()
        value_s = row[1].strip()
        if not date_s or value_s in ("", "."):
            continue
        try:
            value = float(value_s)
        except ValueError:
            continue
        out.append({"date": date_s, "value": value})
    return out


def fetch_yahoo_chart_daily(symbol: str) -> list[dict]:
    """抓取 Yahoo Finance chart API 的完整日線收盤歷史。回傳
    [{"date": "YYYY-MM-DD", "value": float}, ...]，依日期由舊到新排序。

    symbol 例如 ^GSPC（S&P 500）、^TWII（台灣加權指數）、TWD=X（美元/台幣）。
    """
    resp = get(
        SOURCE_YAHOO, _YAHOO_CHART_URL.format(symbol=symbol),
        params={"period1": _YAHOO_PERIOD1, "period2": _YAHOO_PERIOD2, "interval": "1d"},
        throttle_bucket="yahoo",
    )
    try:
        payload = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE_YAHOO, f"{symbol}: invalid JSON: {e}",
                              http_status=resp.status_code, retriable=False) from e

    chart = payload.get("chart") or {}
    result = chart.get("result") or []
    if not result:
        err = chart.get("error")
        if err:
            raise CollectorError(SOURCE_YAHOO, f"{symbol}: {err}", retriable=False)
        return []

    r = result[0]
    timestamps = r.get("timestamp") or []
    gmtoffset = (r.get("meta") or {}).get("gmtoffset") or 0
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []

    out: list[dict] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        # 用交易所當地時區（gmtoffset 秒數）換算出正確的交易日日期，而不是直接用 UTC
        # 日期（時差可能導致跨日錯位，尤其亞洲交易所）。
        local_dt = datetime.fromtimestamp(ts + gmtoffset, tz=timezone.utc)
        out.append({"date": local_dt.date().isoformat(), "value": float(close)})
    out.sort(key=lambda o: o["date"])
    return out
