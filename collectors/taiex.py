"""TWSE 發行量加權股價指數（大盤加權指數/TAIEX）歷史日收盤，供週度動畫的指數走勢
參考線使用。見 docs/data-sources.md 第 19 節（endpoint 實測結果，2026-07-16）。

來源：https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=YYYYMMDD&response=json
- date 參數是西元年 YYYYMMDD，但只用來決定「查哪個月」，回傳整個月份的每日資料
  （比照 collectors/revenue_history.py 用過的「查一個月、拿到整月」模式，不需要
  逐日查詢）。
- 欄位：日期（民國年 yyy/mm/dd）、成交股數、成交金額、成交筆數、
  發行量加權股價指數（TAIEX 收盤）、漲跌點數。本 collector 只取日期與收盤指數，
  其餘欄位（成交量值）不在本專案需求範圍內，不解析。
"""
from __future__ import annotations

from models import CollectorError

from ._http import get

SOURCE = "taiex"

_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK"


def _roc_slash_to_iso(roc: str) -> str:
    """民國年 yyy/mm/dd -> 西元年 YYYY-MM-DD。"""
    year, month, day = roc.strip().split("/")
    return f"{int(year) + 1911:04d}-{month}-{day}"


def _to_float(s) -> float | None:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s == "" or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_month(year: int, month: int) -> list[dict]:
    """查詢西元年 (year, month) 該月份的 TAIEX 每日收盤。空月份（例如尚未到達的
    未來月份）回空 list，不臆測。"""
    date_param = f"{year:04d}{month:02d}01"
    resp = get(SOURCE, _URL, params={"date": date_param, "response": "json"}, throttle_bucket="taiex")
    try:
        data = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e

    if data.get("stat") != "OK":
        return []

    fields = data.get("fields") or []
    try:
        idx_date = fields.index("日期")
        idx_close = fields.index("發行量加權股價指數")
        idx_change = fields.index("漲跌點數")
    except ValueError as e:
        raise CollectorError(SOURCE, f"unexpected fields: {fields}", retriable=False) from e

    rows = []
    for row in data.get("data") or []:
        close = _to_float(row[idx_close])
        if close is None:
            continue
        rows.append({
            "date": _roc_slash_to_iso(row[idx_date]),
            "close": close,
            "change": _to_float(row[idx_change]),
        })
    return rows
