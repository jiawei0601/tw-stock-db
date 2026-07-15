"""TWSE/TPEx 公司基本資料 collector — 只取「官方產業別數字代碼」，補齊 isin.py 缺的 industry_code。

見 docs/data-sources.md 第 3-4 節（endpoint 實測結果，2026-07-16）。

- TWSE OpenAPI opendata/t187ap03_L：上市公司基本資料，JSON，欄位「產業別」是兩碼數字字串
  （如 "24" = 半導體業），不是文字名稱。實測與 isin.py 抓到的「股票」區塊產業別文字做交叉比對，
  1052 檔中 1051 檔可對應、code↔name 完全一致無衝突（僅 1 檔新掛牌股票尚未出現在本 opendata，
  見下方 fallback 說明）。
- TPEx openapi mopsfin_t187ap03_O：上櫃公司基本資料，JSON，欄位「SecuritiesIndustryCode」同構，
  與 TWSE 共用同一套產業別代碼表（交叉比對 26 個共同代碼，數字與文字完全一致，零衝突）。
- 兩來源皆只回「最新一期」全量清單，無歷史或單檔查詢參數。
- 已知缺口：極少數剛掛牌的股票尚未被收錄進本 opendata（實測 2026-07-16：TWSE 5236、TPEx 7814
  各 1 檔）。這類股票的 industry_code 會是 None，industry_name 仍可從 isin.py 取得（該來源涵蓋
  即時掛牌清單，資料較新）。不臆測代碼，寧可留空。
"""
from __future__ import annotations

from models import CollectorError

from ._http import get

SOURCE = "company_info"

_TWSE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
_TPEX_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"


def fetch_twse_industry_codes() -> dict[str, str]:
    """回傳 {stock_id: industry_code}（兩碼數字字串）。HTTP 200 但空資料回空 dict，不造假。"""
    resp = get(SOURCE, _TWSE_URL, throttle_bucket="company_info")
    try:
        data = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e
    if not data:
        return {}
    return {
        item["公司代號"]: item["產業別"]
        for item in data
        if item.get("公司代號") and item.get("產業別")
    }


def fetch_tpex_industry_codes() -> dict[str, str]:
    """回傳 {stock_id: industry_code}（與 TWSE 共用同一套代碼表，見上方模組說明）。"""
    resp = get(SOURCE, _TPEX_URL, throttle_bucket="company_info")
    try:
        data = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE, f"invalid JSON: {e}", http_status=resp.status_code, retriable=False) from e
    if not data:
        return {}
    return {
        item["SecuritiesCompanyCode"]: item["SecuritiesIndustryCode"]
        for item in data
        if item.get("SecuritiesCompanyCode") and item.get("SecuritiesIndustryCode")
    }
