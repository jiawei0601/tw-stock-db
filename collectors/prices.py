"""TWSE/TPEx 官方個股歷史收盤價（依日期查詢）collector，供三大法人「股數 x 收盤價 ≈
金額流向」換算使用。見 docs/data-sources.md 第 20-21 節（endpoint 實測結果，2026-07-16）。

- TWSE `afterTrading/MI_INDEX`：上市，`date` 為西元年 YYYYMMDD，一次查詢回傳「當日全市場」
  （跟三大法人 T86 是同一個「查一天、回全市場」endpoint 家族）。
- TPEx `www/zh-tw/afterTrading/dailyQuotes`：上櫃，`date` 為**西元年斜線格式** YYYY/MM/DD
  （注意跟 TPEx 其餘 endpoint 慣用的民國年格式不同），一次查詢回傳「當日全市場」。
  這是本專案第九輪實測踩過兩個「看似能查歷史、實際上永遠回傳查詢當下最新一天」的陷阱
  （TPEx openapi `tpex_mainboard_daily_close_quotes` 與舊版 `stk_quote_result.php`）之後
  才找到的**真正支援歷史查詢**的 TPEx 個股日收盤價來源，見 docs/data-sources.md 第 21 節
  完整排查過程。
- 兩者皆無「查詢區間」參數，只能逐日查詢；非交易日（假日）回空結果，不是錯誤，
  呼叫端應視為「當天無交易」跳過，不重試。
- 兩者皆需瀏覽器 UA（`collectors/_http.py` 的 `BROWSER_UA` 已內建，TPEx 實測不帶 UA 也能
  取得 200，但保守起見仍統一帶上，比照本專案其餘 TPEx collector 的慣例）。
"""
from __future__ import annotations

import json

from models import CollectorError

from ._http import get

SOURCE = "prices"

_TWSE_MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
_TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"


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


def fetch_twse_close(iso_date: str) -> list[dict]:
    """TWSE 個股日收盤價（MI_INDEX），當日全市場一次回傳。

    整頁回應含多個表格（大盤統計、漲跌家數、個股行情...），本專案要找的是欄位包含
    `證券代號`／`收盤價` 的那個表格（實測固定是 tables[8]，但用欄位名稱動態尋找，
    不寫死索引，避免 TWSE 未來調整表格順序就整個抓錯）。

    非交易日：`stat` != 'OK'（跟三大法人 T86 同一套判斷方式），回空 list，不是錯誤。
    """
    ymd = iso_date.replace("-", "")
    resp = get(
        SOURCE, _TWSE_MI_INDEX_URL,
        params={"date": ymd, "type": "ALLBUT0999", "response": "json"},
        throttle_bucket="twse_mi_index",
    )
    try:
        # 跟 collectors/institutional_official.py::fetch_twse_t86 同一個坑：
        # Content-Type 宣告 charset=UTF-8，但 resp.json()/自動編碼偵測不可靠，
        # 一律手動對 resp.content 用 utf-8 解碼再 json.loads()。
        payload = json.loads(resp.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e

    if payload.get("stat") != "OK":
        return []

    tables = payload.get("tables") or []
    target_table = None
    for t in tables:
        fields = t.get("fields") or []
        if "證券代號" in fields and "收盤價" in fields:
            target_table = t
            break
    if target_table is None:
        # 有交易但找不到個股收盤價表格，視為格式異動，明確報錯而非默默回空
        # （不同於「非交易日」情境，那是 stat != 'OK' 就已經提前 return 掉了）。
        raise CollectorError(SOURCE, "MI_INDEX 回應中找不到含「證券代號」「收盤價」欄位的表格",
                              http_status=resp.status_code, retriable=False)

    fields = target_table["fields"]
    idx_code = fields.index("證券代號")
    idx_close = fields.index("收盤價")

    rows: list[dict] = []
    for row in target_table.get("data") or []:
        stock_id = (row[idx_code] or "").strip()
        if not stock_id:
            continue
        close = _to_float(row[idx_close])
        if close is None:
            continue  # 該股當天無成交（例如全日暫停交易），沒有收盤價可用，不臆測
        rows.append({"stock_id": stock_id, "date": iso_date, "close": close})
    return rows


def fetch_tpex_close(iso_date: str) -> list[dict]:
    """TPEx 個股日收盤價（www/zh-tw/afterTrading/dailyQuotes），當日全市場一次回傳。

    **注意：`date` 參數是西元年斜線格式 `YYYY/MM/DD`**（跟本專案其餘 TPEx endpoint
    〔如 `3itrade_hedge_result.php` 用民國年斜線〕不同，是本專案唯二用西元年格式的
    TPEx endpoint 之一，容易誤用民國年格式而查錯，實測已確認西元年格式才正確）。

    非交易日：`tables[0]['totalCount'] == 0`（`tables[0]['data']` 同時為空 list），
    跟 TWSE 用 `stat` 判斷的方式不同，比照 `institutional_official.py::fetch_tpex_hedge`
    「不能只信 stat」的教訓，這裡直接看 totalCount／data 是否為空。
    """
    query_date = iso_date.replace("-", "/")
    resp = get(
        SOURCE, _TPEX_DAILY_QUOTES_URL,
        params={"date": query_date, "response": "json"},
        throttle_bucket="tpex_daily_quotes",
    )
    try:
        payload = json.loads(resp.content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e

    tables = payload.get("tables") or []
    if not tables:
        return []
    table = tables[0]
    if not table.get("data"):
        return []

    fields = table.get("fields") or []
    try:
        idx_code = fields.index("代號")
        idx_close = fields.index("收盤")
    except ValueError as e:
        raise CollectorError(SOURCE, f"dailyQuotes 回應欄位異常: {fields}", retriable=False) from e

    rows: list[dict] = []
    for row in table["data"]:
        stock_id = (row[idx_code] or "").strip()
        if not stock_id:
            continue
        close = _to_float(row[idx_close])
        if close is None:
            continue
        rows.append({"stock_id": stock_id, "date": iso_date, "close": close})
    return rows
