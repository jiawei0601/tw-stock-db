"""TWSE/TPEx 官方三大法人買賣超（依日期查詢）collector。取代舊版讀取
`tw_cache/institutional.db` 的做法，改為對官方 endpoint 按日期直接抓歷史。

見 docs/data-sources.md 第 10-11 節（endpoint 實測結果，2026-07-16）。

- TWSE `fund/T86`：上市，`date` 為西元年 YYYYMMDD，一次查詢回傳「當日全市場」。
- TPEx `web/stock/3insti/daily_trade/3itrade_hedge_result.php`：上櫃，`d` 為民國年
  斜線格式 YYY/MM/DD，一次查詢回傳「當日全市場」。
- 兩者皆無「查詢區間」參數，只能逐日查詢；非交易日（假日）回空結果，不是錯誤，
  呼叫端應視為「當天無交易」跳過，不重試。
- 兩者皆需瀏覽器 UA（`collectors/_http.py` 的 `BROWSER_UA` 已內建）。
"""
from __future__ import annotations

import json

from models import CollectorError

from ._http import get

SOURCE = "institutional_official"

_TWSE_T86_URL = "https://www.twse.com.tw/fund/T86"
_TPEX_HEDGE_URL = "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"


def _to_int(s) -> int | None:
    if s is None:
        return None
    s = str(s).replace(",", "").strip()
    if s in ("", "--"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _iso_to_roc_slash(iso_date: str) -> str:
    """西元年 YYYY-MM-DD -> 民國年斜線 YYY/MM/DD（TPEx `d` 參數格式）。"""
    year, month, day = iso_date.split("-")
    return f"{int(year) - 1911}/{month}/{day}"


def fetch_twse_t86(iso_date: str) -> list[dict]:
    """TWSE 三大法人買賣超日報（T86），當日全市場一次回傳。

    欄位對應（實測確認，見 docs/data-sources.md 第 10 節）：
        index 4  = 外陸資買賣超股數(不含外資自營商)
        index 7  = 外資自營商買賣超股數
        index 10 = 投信買賣超股數
        index 11 = 自營商買賣超股數（已是自行+避險合計，不可再重複累加子欄位）
    `foreign_net` = index4 + index7（業界慣例的「外資買賣超」= 外資陸資本體 + 外資自營商）。

    非交易日：`stat` != 'OK'（實測訊息為「很抱歉，沒有符合條件的資料!」），回空 list，
    不是錯誤，呼叫端應視為當天無交易。
    """
    ymd = iso_date.replace("-", "")
    resp = get(
        SOURCE, _TWSE_T86_URL,
        params={"response": "json", "date": ymd, "selectType": "ALLBUT0999"},
        throttle_bucket="twse_t86",
    )
    try:
        # 注意：resp.json()/requests 的自動編碼偵測對此 endpoint 不可靠（content-type 宣告
        # charset=UTF-8，但 requests 有時會誤判成其他編碼導致亂碼），故直接對原始 bytes
        # 用 utf-8 解碼（實測確認回應本身就是合法 UTF-8 bytes）。
        payload = json.loads(resp.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e

    if payload.get("stat") != "OK":
        return []
    data = payload.get("data") or []

    rows: list[dict] = []
    for row in data:
        if len(row) < 19:
            continue
        stock_id = (row[0] or "").strip()
        if not stock_id:
            continue
        foreign_ex_dealer = _to_int(row[4])
        foreign_dealer = _to_int(row[7])
        foreign_net = (
            None if foreign_ex_dealer is None and foreign_dealer is None
            else (foreign_ex_dealer or 0) + (foreign_dealer or 0)
        )
        rows.append({
            "stock_id": stock_id,
            "date": iso_date,
            "foreign_net": foreign_net,
            "trust_net": _to_int(row[10]),
            "dealer_net": _to_int(row[11]),
        })
    return rows


def fetch_tpex_hedge(iso_date: str) -> list[dict]:
    """TPEx 三大法人買賣超日報（3itrade_hedge_result.php），當日全市場一次回傳。

    回應為 24 欄（非文件初估的 25 欄，實測確認，見 docs/data-sources.md 第 11 節）：
    `代號`、`名稱`，接著 7 組「買進/賣出/買賣超」三欄
    （外資及陸資(不含外資自營商) / 外資自營商 / 外資合計 / 投信 / 自營商自行 /
    自營商避險 / 自營商合計），最後 1 欄三大法人合計。
    欄位對應（0-indexed）：
        index 10 = 外資合計買賣超股數（已是「外資及陸資本體」+「外資自營商」加總，
                   核對 TWSE 同日 2330 / TPEx OpenAPI tpex_3insti_daily_trading 同日
                   3105 數字皆一致，不可再重複相加子欄位）
        index 13 = 投信買賣超股數
        index 22 = 自營商合計買賣超股數（已是「自行買賣」+「避險」加總）

    非交易日：`tables[0]['data']` 為空 list（實測發現 TPEx 此 endpoint 的頂層 `stat`
    欄位固定回 `'ok'`，即使非交易日也一樣，不能用 stat 判斷，只能看 data 是否為空）。
    """
    roc_date = _iso_to_roc_slash(iso_date)
    resp = get(
        SOURCE, _TPEX_HEDGE_URL,
        params={"d": roc_date, "se": "EW"},
        throttle_bucket="tpex_hedge",
    )
    try:
        payload = json.loads(resp.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e

    tables = payload.get("tables") or []
    if not tables:
        return []
    data = tables[0].get("data") or []

    rows: list[dict] = []
    for row in data:
        if len(row) < 23:
            continue
        stock_id = (row[0] or "").strip()
        if not stock_id:
            continue
        rows.append({
            "stock_id": stock_id,
            "date": iso_date,
            "foreign_net": _to_int(row[10]),
            "trust_net": _to_int(row[13]),
            "dealer_net": _to_int(row[22]),
        })
    return rows
