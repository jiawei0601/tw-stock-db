"""TWSE/TPEx 月營收 collector（比照 tw-momentum-scanner/collectors/twse.py 的
`fetch_monthly_revenue_latest()` 做法，本專案不用 pandas，回傳 list[dict]）。

見 docs/data-sources.md 第 6-7 節（endpoint 實測結果，2026-07-16）。

- TWSE OpenAPI opendata/t187ap05_L：上市公司月營收，JSON list，1065 筆。
- TPEx OpenAPI mopsfin_t187ap05_O：上櫃公司月營收，JSON list，891 筆，欄位結構與 TWSE
  完全相同（同樣是「公開資訊觀測站」t187ap05 系列報表，TPEx 版本只是換了 `_O` 後綴）。
- 兩者皆只回「最新一期全量」，無歷史區間參數 —— 已知限制，不是 bug。
- 欄位「資料年月」「出表日期」為民國年字串，需轉西元年。
"""
from __future__ import annotations

from models import CollectorError

from ._http import get

SOURCE = "revenue"

_TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"


def _roc_to_iso(roc: str) -> str | None:
    """民國年 YYYMMDD -> 西元年 YYYY-MM-DD。空值回 None，不臆測。"""
    if not roc:
        return None
    roc = roc.strip()
    if len(roc) < 5:
        return None
    year = int(roc[:-4]) + 1911
    month = roc[-4:-2]
    day = roc[-2:]
    return f"{year:04d}-{month}-{day}"


def _roc_ym_to_iso(roc_ym: str) -> str | None:
    """民國年月 YYYMM -> 西元年月 YYYY-MM。空值回 None。"""
    if not roc_ym or len(roc_ym) < 3:
        return None
    roc_ym = roc_ym.strip()
    year = int(roc_ym[:-2]) + 1911
    month = roc_ym[-2:]
    return f"{year:04d}-{month}"


def _to_int(s) -> int | None:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(s) -> float | None:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_row(item: dict, market: str) -> dict:
    return {
        "stock_id": item.get("公司代號"),
        "company_name": item.get("公司名稱"),
        "ym": _roc_ym_to_iso(item.get("資料年月", "")),
        "announce_date": _roc_to_iso(item.get("出表日期", "")),
        "revenue": _to_int(item.get("營業收入-當月營收")),
        "revenue_prev_month": _to_int(item.get("營業收入-上月營收")),
        "revenue_last_year_month": _to_int(item.get("營業收入-去年當月營收")),
        "mom_pct": _to_float(item.get("營業收入-上月比較增減(%)")),
        "yoy_pct": _to_float(item.get("營業收入-去年同月增減(%)")),
        "revenue_cumulative": _to_int(item.get("累計營業收入-當月累計營收")),
        "revenue_cumulative_last_year": _to_int(item.get("累計營業收入-去年累計營收")),
        "cumulative_yoy_pct": _to_float(item.get("累計營業收入-前期比較增減(%)")),
        "remark": item.get("備註") if item.get("備註") not in (None, "-") else None,
        "source_market": market,
    }


def fetch_twse_monthly_revenue() -> list[dict]:
    """上市公司最新一期月營收。HTTP 200 但空資料回空 list，不造假。"""
    resp = get(SOURCE, _TWSE_URL, throttle_bucket="revenue")
    try:
        data = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e
    if not data:
        return []
    return [_parse_row(item, "TWSE") for item in data if item.get("公司代號")]


def fetch_tpex_monthly_revenue() -> list[dict]:
    """上櫃公司最新一期月營收。欄位結構與 TWSE 相同，見模組說明。"""
    resp = get(SOURCE, _TPEX_URL, throttle_bucket="revenue")
    try:
        data = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e
    if not data:
        return []
    return [_parse_row(item, "TPEx") for item in data if item.get("公司代號")]
