"""台灣總經序列 collector — FinMind（優先，需 .env 的 FINMIND_TOKEN）+ 官方部會
API（FinMind 沒有對應資料集時的備援/唯一來源）。

**FinMind 覆蓋範圍已實測確認**（2026-07-26，逐一嘗試候選 dataset 名稱，非 FinMind
涵蓋的一律回 HTTP 422/400，確認資料集不存在）：FinMind 只有 `TaiwanStockPrice`
（`stock_id="TAIEX"` 可查到加權指數日收盤，2026-07 實測回溯至 1999-01-05）與
`TaiwanExchangeRate`（`currency="USD"` 可查到美元/台幣，回溯至 2006-01-02）這兩個跟
本專案需求相關的資料集；`CurrencyCirculation`（僅 2 個總經資料集之一）是「貨幣發行
（通貨）」不是 M1B/M2 貨幣總計數，且 `data_id="Taiwan"` 實測回空，用不上。**出口值/
外銷訂單/CPI/核心CPI/M1B年增率/M2年增率/景氣對策信號/領先落後指標/PMI 這些序列
FinMind 完全沒有對應資料集**（`TaiwanExportOrder`／`TaiwanTradeStatistics`／
`TaiwanCPI`／`TaiwanPMI`／`TaiwanMoneyAggregates`／`TaiwanBusinessCycleIndicator`
等候選名稱全部 422 Unprocessable Entity），因此這些序列直接用官方來源（非「FinMind
失敗後 fallback」，是「FinMind 本來就沒有」），來源皆為免費、不需金鑰、不解
CAPTCHA/bot-detection 的官方公開 endpoint：

- 出口值/電子零組件/資通與視聽產品出口值：財政部統計處「出口值_按主要貨品分」
  （web02.mof.gov.tw njswww CSV，按美元計算，百萬美元，月頻，回溯至民國90年=2001）。
- 外銷訂單金額：經濟部「外銷訂單」opendata（service.moea.gov.tw，百萬美元，月頻，
  回溯至民國73年=1984）。
- CPI(年增率)/核心CPI(年增率)：主計總處消費者物價「基本分類指數」/「特殊分類指數」
  XML（ws.dgbas.gov.tw，Item/TIME_PERIOD/FREQ/TYPE/Item_VALUE 格式，TYPE="年增率(%)"
  的列即官方已計算好的年增率，直接採用不重複計算，回溯至1981年）。**ws.dgbas.gov.tw
  的憑證鏈不完整**（伺服器只送 leaf 憑證，缺中繼 CA「TWCA Secure SSL Certification
  Authority」，實測用 `openssl s_client -showcerts` 確認；curl/瀏覽器能過是因為作業
  系統的信任庫本身快取了這張中繼憑證，Python `requests`／`certifi` 的獨立信任庫沒有
  快取、驗證會失敗）。不採用 `verify=False`（整個關掉驗證），而是用 leaf 憑證
  Authority Information Access 擴充欄位指到的中繼憑證下載網址
  （`http://sslserver.twca.com.tw/cacert/secure_sha2_2023G3.crt`，DER 格式）補進
  `certifi` 內建信任庫組成暫存合併 bundle 傳給 `verify=`，這樣依然驗證到根 CA
  （已確認該中繼憑證的簽發者「TWCA Global Root CA」存在於 `certifi` 內建信任庫），
  只是補齊伺服器自己漏送的中繼憑證，見 `_dgbas_verify_bundle()`。
- M1B/M2(日平均,年增率)：中央銀行「貨幣總計數」CSV（cbc.gov.tw，日平均數月資料，
  「貨幣總計數-Ｍ１Ｂ-年增率」/「貨幣總計數-Ｍ２-年增率」欄位即官方已計算好的年增率，
  回溯至1987年）。
- 景氣對策信號分數/領先指標/落後指標：國發會「景氣指標及燈號」ZIP（ws.ndc.gov.tw，
  ZIP 內檔名為 Big5 編碼、內容為 UTF-8 BOM CSV，回溯至1982年）。
- PMI（僅綜合指數，無「新增訂單」「客戶存貨」兩個分項）：國發會「臺灣採購經理人指數」
  CSV（ws.ndc.gov.tw，回溯至2012-07）。**分項指數（新增訂單/客戶存貨）沒有找到可用
  的官方免費 CSV/JSON 來源** —— 唯一疑似來源 index.ndc.gov.tw 的細項頁面掛在
  Cloudflare 之後（子資源請求回傳 Cloudflare 攔截頁「Sorry, you have been blocked」），
  本專案不解 bot-detection 挑戰，這兩個序列直接跳過、不臆測資料，見 build_macro.py
  執行摘要的失敗清單。
"""
from __future__ import annotations

import csv
import io
import os
import re
import ssl
import tempfile
import zipfile
from pathlib import Path

import certifi
import requests

from models import CollectorError

from ._http import get
from .macro_us import fetch_yahoo_chart_daily  # noqa: F401  (未使用時保留供未來 fallback)

SOURCE_FINMIND = "finmind"
SOURCE_MOF = "mof"
SOURCE_MOEA = "moea"
SOURCE_DGBAS = "dgbas"
SOURCE_CBC = "cbc"
SOURCE_NDC = "ndc"

_FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
_FINMIND_MIN_INTERVAL = 2.0  # 保守節流；本專案總呼叫量遠低於官方每小時 600 次上限

_MOF_EXPORT_BY_PRODUCT_URL = (
    "https://web02.mof.gov.tw/njswww/webMain.aspx?sys=220&ym=9000&kind=21&type=4"
    "&funid=i8121&cycle=41&outmode=12&compmode=00&outkind=1&fld0=1"
    "&codlst0=1101111010100011110111100111110110100&utf=1"
)
_MOEA_EXPORT_ORDERS_URL = "https://service.moea.gov.tw/EE520/opendata/b.csv"
_DGBAS_CPI_URL = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230555/pr0101a1m.xml"
_DGBAS_CORE_CPI_URL = "https://ws.dgbas.gov.tw/001/Upload/461/relfile/11525/230544/pr0103a1m.xml"
_CBC_MONETARY_AGGREGATES_URL = "https://www.cbc.gov.tw/public/data/OpenData/經研處/EF15M01.csv"
_NDC_MONITORING_ZIP_URL = (
    "https://ws.ndc.gov.tw/Download.ashx?"
    "u=LzAwMS9hZG1pbmlzdHJhdG9yLzEwL3JlbGZpbGUvNTc4MS82MzkyL2VhMjM1YmQ5LWQwNTItNGE2OS1hYmZjLWQ1Yzc4NWQzZDBlMi56aXA%3d"
    "&n=5pmv5rCj5oyH5qiZ5Y%2bK54eI6JmfLnppcA%3d%3d&icon=.zip"
)
_NDC_PMI_URL = (
    "https://ws.ndc.gov.tw/Download.ashx?"
    "u=LzAwMS9hZG1pbmlzdHJhdG9yLzEwL3JlbGZpbGUvNTc4MS82MzkxL2JmOGE0ZWI3LTEwZmUtNGZhMC1iNjQ2LTMwZTg5MGQwMjE4YS5jc3Y%3d"
    "&n=6Ie654Gj5o6h6LO857aT55CG5Lq65oyH5pW4KHBtaeWPim5taSkuY3N2&icon=.csv"
)

_MONITORING_ZIP_MEMBER_NAME = "景氣指標與燈號.csv"

# ws.dgbas.gov.tw 的憑證鏈不完整（只送 leaf 憑證），缺的中繼 CA 下載網址取自該 leaf
# 憑證的 Authority Information Access 擴充欄位（`openssl x509 -text` 可看到，2026-07-26
# 實測確認），見檔頭說明與 `_dgbas_verify_bundle()`。
_TWCA_INTERMEDIATE_CERT_URL = "http://sslserver.twca.com.tw/cacert/secure_sha2_2023G3.crt"
_dgbas_verify_bundle_cache: bool | str | None = None


def _dgbas_verify_bundle() -> bool | str:
    """組出「certifi 內建信任庫 + TWCA 缺的中繼憑證」合併後的暫存 CA bundle 檔案路徑，
    供 ws.dgbas.gov.tw 的請求使用（見檔頭說明：伺服器本身憑證鏈不完整）。抓取/組裝
    任一步驟失敗就退回 `True`（沿用預設信任庫，讓原本的 SSL 驗證錯誤照常浮現，不靜默
    關閉驗證）。同一次執行只組裝一次（快取於模組變數）。"""
    global _dgbas_verify_bundle_cache
    if _dgbas_verify_bundle_cache is not None:
        return _dgbas_verify_bundle_cache
    try:
        resp = requests.get(_TWCA_INTERMEDIATE_CERT_URL, timeout=10)
        resp.raise_for_status()
        pem = ssl.DER_cert_to_PEM_cert(resp.content)
        with open(certifi.where(), encoding="ascii") as f:
            base_bundle = f.read()
        fd, path = tempfile.mkstemp(suffix=".pem", prefix="dgbas_ca_bundle_")
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(base_bundle)
            f.write("\n")
            f.write(pem)
        _dgbas_verify_bundle_cache = path
        return path
    except Exception:
        _dgbas_verify_bundle_cache = True
        return True


def _finmind_token() -> str:
    """FinMind API token：環境變數 FINMIND_TOKEN 優先，否則從專案 .env 讀取；皆無則回空字串。
    邏輯比照 C:\\CLAUDE\\市場熱度分析整理\\ingest.py 的 _finmind_token()。"""
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if token:
        return token
    env_path = Path(__file__).parent.parent / ".env"
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("FINMIND_TOKEN="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _fetch_finmind(dataset: str, data_id: str | None = None, start_date: str = "1900-01-01") -> list[dict]:
    """呼叫 FinMind v4 API，回傳 `data` 陣列（每筆為原始 dict，欄位依 dataset 而異）。
    402（額度/權限不足）與 429（超過限流）皆不重試轟炸，直接視為失敗回報（402 不在
    collectors/_http.py 的 RETRIABLE_STATUS 內，本來就不會重試；429 會走既有的
    5/20/60 秒退避，3 次後仍失敗才放棄，符合「退避但不轟炸」的要求）。"""
    params: dict = {"dataset": dataset, "start_date": start_date}
    if data_id:
        params["data_id"] = data_id
    token = _finmind_token()
    if token:
        params["token"] = token
    resp = get(SOURCE_FINMIND, _FINMIND_URL, params=params,
               throttle_bucket="finmind", min_interval=_FINMIND_MIN_INTERVAL)
    try:
        payload = resp.json()
    except ValueError as e:
        raise CollectorError(SOURCE_FINMIND, f"{dataset}/{data_id}: invalid JSON: {e}",
                              http_status=resp.status_code, retriable=False) from e
    if payload.get("status") != 200:
        raise CollectorError(SOURCE_FINMIND, f"{dataset}/{data_id}: {payload.get('msg')}",
                              http_status=resp.status_code, retriable=False)
    return payload.get("data") or []


def fetch_finmind_taiex_daily() -> list[dict]:
    """FinMind TaiwanStockPrice(stock_id=TAIEX)，回傳 [{"date","value"(收盤)}]。"""
    rows = _fetch_finmind("TaiwanStockPrice", "TAIEX")
    out = []
    for r in rows:
        close = r.get("close")
        if close is None:
            continue
        out.append({"date": r["date"], "value": float(close)})
    return out


def fetch_finmind_usd_twd_daily() -> list[dict]:
    """FinMind TaiwanExchangeRate(currency=USD)，優先用即期匯率(spot)中價，
    查無即期報價（spot_buy/spot_sell 為 -1，早期資料常見）時退回現金匯率(cash)中價。"""
    rows = _fetch_finmind("TaiwanExchangeRate", "USD")
    out = []
    for r in rows:
        spot_buy, spot_sell = r.get("spot_buy"), r.get("spot_sell")
        cash_buy, cash_sell = r.get("cash_buy"), r.get("cash_sell")
        if spot_buy and spot_sell and spot_buy > 0 and spot_sell > 0:
            value = (spot_buy + spot_sell) / 2
        elif cash_buy and cash_sell and cash_buy > 0 and cash_sell > 0:
            value = (cash_buy + cash_sell) / 2
        else:
            continue
        out.append({"date": r["date"], "value": round(value, 4)})
    return out


def _roc_month_label_to_iso(label: str) -> str | None:
    """MOF「115年 6月」格式 -> "2026-06-01"；純年度列（例如「115年」，無空格月份）
    回傳 None（呼叫端應跳過，非月頻資料，避免污染月頻時序表）。"""
    m = re.match(r"^\s*(\d+)年\s+(\d+)月\s*$", label)
    if not m:
        return None
    roc_year, month = int(m.group(1)), int(m.group(2))
    return f"{roc_year + 1911:04d}-{month:02d}-01"


def fetch_mof_export_by_product() -> list[dict]:
    """財政部統計處「出口值_按主要貨品分」，回傳
    [{"date","total","electronics","ict"}]（單位皆為百萬美元，月頻）。
    只取月份列，年度加總列（例如「90年」）跳過。"""
    resp = get(SOURCE_MOF, _MOF_EXPORT_BY_PRODUCT_URL, throttle_bucket="mof")
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CollectorError(SOURCE_MOF, f"decode failed: {e}", retriable=False) from e

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    header = rows[0]
    try:
        idx_total = header.index("按美元計算(百萬美元)/ 總計")
        idx_electronics = header.index("按美元計算(百萬美元)/ (1)電子零組件")
        idx_ict = header.index("按美元計算(百萬美元)/ (4)資通與視聽產品")
    except ValueError as e:
        raise CollectorError(SOURCE_MOF, f"未預期的欄位: {header}", retriable=False) from e

    out = []
    for row in rows[1:]:
        if not row or len(row) <= max(idx_total, idx_electronics, idx_ict):
            continue
        iso_date = _roc_month_label_to_iso(row[0])
        if iso_date is None:
            continue
        try:
            total = float(row[idx_total])
            electronics = float(row[idx_electronics])
            ict = float(row[idx_ict])
        except (ValueError, IndexError):
            continue
        out.append({"date": iso_date, "total": total, "electronics": electronics, "ict": ict})
    return out


def fetch_export_orders() -> list[dict]:
    """經濟部「外銷訂單」opendata，回傳 [{"date","value"}]（統計值(美元)，百萬美元，月頻）。
    資料期格式「07301」= 民國073年01月。"""
    resp = get(SOURCE_MOEA, _MOEA_EXPORT_ORDERS_URL, throttle_bucket="moea")
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CollectorError(SOURCE_MOEA, f"decode failed: {e}", retriable=False) from e

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    header = rows[0]
    try:
        idx_period = header.index("資料期(民國年)")
        idx_value = header.index("統計值(美元)")
    except ValueError as e:
        raise CollectorError(SOURCE_MOEA, f"未預期的欄位: {header}", retriable=False) from e

    out = []
    for row in rows[1:]:
        if not row or len(row) <= max(idx_period, idx_value):
            continue
        period = row[idx_period].strip()
        if len(period) != 5 or not period.isdigit():
            continue
        roc_year, month = int(period[:3]), int(period[3:5])
        try:
            value = float(row[idx_value])
        except ValueError:
            continue
        out.append({"date": f"{roc_year + 1911:04d}-{month:02d}-01", "value": value})
    return out


def _fetch_dgbas_cpi_xml_yoy(url: str, item_name: str) -> list[dict]:
    """主計總處 CPI XML（Item/TIME_PERIOD/FREQ/TYPE/Item_VALUE 格式）通用解析：
    篩選 Item == item_name 且 TYPE == '年增率(%)' 的觀測值，直接採用官方已計算好的
    年增率（不重複計算），回傳 [{"date","value"}]。TIME_PERIOD 格式「1981M01」已是
    西元年，不需 ROC 轉換。"""
    import xml.etree.ElementTree as ET

    resp = get(SOURCE_DGBAS, url, throttle_bucket="dgbas", verify=_dgbas_verify_bundle())
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise CollectorError(SOURCE_DGBAS, f"invalid XML: {e}", retriable=False) from e

    out = []
    for obs in root.findall("Obs"):
        item_el = obs.find("Item")
        type_el = obs.find("TYPE")
        period_el = obs.find("TIME_PERIOD")
        value_el = obs.find("Item_VALUE")
        if item_el is None or type_el is None or period_el is None or value_el is None:
            continue
        if item_el.text != item_name or type_el.text != "年增率(%)":
            continue
        value_s = (value_el.text or "").strip()
        if not value_s:
            continue
        try:
            value = float(value_s)
        except ValueError:
            continue
        m = re.match(r"^(\d{4})M(\d{2})$", period_el.text or "")
        if not m:
            continue
        out.append({"date": f"{m.group(1)}-{m.group(2)}-01", "value": value})
    return out


def fetch_cpi_yoy() -> list[dict]:
    """主計總處消費者物價「基本分類指數」，總指數年增率。"""
    return _fetch_dgbas_cpi_xml_yoy(_DGBAS_CPI_URL, "總指數(指數基期：民國110年=100)")


def fetch_core_cpi_yoy() -> list[dict]:
    """主計總處消費者物價「特殊分類指數」，核心物價（不含蔬果及能源）年增率。"""
    return _fetch_dgbas_cpi_xml_yoy(
        _DGBAS_CORE_CPI_URL, "總指數(不含蔬果及能源)【即核心物價】(指數基期：民國110年=100)"
    )


def fetch_cbc_monetary_aggregates() -> list[dict]:
    """中央銀行「貨幣總計數」日平均數月資料，回傳
    [{"date","m1b_yoy","m2_yoy"}]（官方已計算好的年增率，不重複計算）。"""
    resp = get(SOURCE_CBC, _CBC_MONETARY_AGGREGATES_URL, throttle_bucket="cbc")
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CollectorError(SOURCE_CBC, f"decode failed: {e}", retriable=False) from e

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    header = rows[0]
    try:
        idx_period = header.index("期間")
        idx_m1b_yoy = header.index("貨幣總計數 -Ｍ１Ｂ-年增率")
        idx_m2_yoy = header.index("貨幣總計數 -Ｍ２-年增率")
    except ValueError as e:
        raise CollectorError(SOURCE_CBC, f"未預期的欄位: {header}", retriable=False) from e

    out = []
    for row in rows[1:]:
        if not row or len(row) <= max(idx_period, idx_m1b_yoy, idx_m2_yoy):
            continue
        m = re.match(r"^(\d{4})M(\d{2})$", row[idx_period].strip())
        if not m:
            continue
        iso_date = f"{m.group(1)}-{m.group(2)}-01"

        def _parse(s: str) -> float | None:
            s = s.strip()
            if s in ("", "-"):
                return None
            try:
                return float(s)
            except ValueError:
                return None

        out.append({
            "date": iso_date,
            "m1b_yoy": _parse(row[idx_m1b_yoy]),
            "m2_yoy": _parse(row[idx_m2_yoy]),
        })
    return out


def _find_zip_member(zf: zipfile.ZipFile, target_name: str) -> str:
    """NDC ZIP 內檔名是 Big5 編碼但 Python zipfile 預設用 cp437 解碼（因為 ZIP 內部
    沒有標記 UTF-8 flag），需先用 cp437 編碼還原成原始 bytes 再用 big5 解碼，才能
    比對到正確的中文檔名。"""
    for name in zf.namelist():
        try:
            fixed = name.encode("cp437").decode("big5")
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue
        if fixed == target_name:
            return name
    raise CollectorError(SOURCE_NDC, f"ZIP 內找不到檔案 {target_name}（namelist={zf.namelist()}）",
                          retriable=False)


def fetch_ndc_monitoring_indicators() -> list[dict]:
    """國發會「景氣指標及燈號」ZIP，回傳
    [{"date","leading_index","lagging_index","monitoring_score"}]。
    monitoring_score 早期月份為 '-'（燈號制度尚未涵蓋該期間）時回 None，呼叫端跳過。"""
    resp = get(SOURCE_NDC, _NDC_MONITORING_ZIP_URL, throttle_bucket="ndc")
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except zipfile.BadZipFile as e:
        raise CollectorError(SOURCE_NDC, f"invalid ZIP: {e}", retriable=False) from e

    member = _find_zip_member(zf, _MONITORING_ZIP_MEMBER_NAME)
    text = zf.read(member).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    header = rows[0]
    try:
        idx_date = header.index("Date")
        idx_leading = header.index("領先指標綜合指數")
        idx_lagging = header.index("落後指標綜合指數")
        idx_score = header.index("景氣對策信號綜合分數")
    except ValueError as e:
        raise CollectorError(SOURCE_NDC, f"未預期的欄位: {header}", retriable=False) from e

    def _parse_float(s: str) -> float | None:
        s = s.strip()
        if s in ("", "-"):
            return None
        try:
            return float(s)
        except ValueError:
            return None

    out = []
    for row in rows[1:]:
        if not row or len(row) <= max(idx_date, idx_leading, idx_lagging, idx_score):
            continue
        date_s = row[idx_date].strip()
        if len(date_s) != 6 or not date_s.isdigit():
            continue
        iso_date = f"{date_s[:4]}-{date_s[4:6]}-01"
        score = _parse_float(row[idx_score])
        out.append({
            "date": iso_date,
            "leading_index": _parse_float(row[idx_leading]),
            "lagging_index": _parse_float(row[idx_lagging]),
            "monitoring_score": int(score) if score is not None else None,
        })
    return out


def fetch_ndc_pmi() -> list[dict]:
    """國發會「臺灣採購經理人指數」CSV，回傳 [{"date","value"}]（PMI 綜合指數，月頻）。
    NMI（非製造業）欄位本專案不需要，不解析。"""
    resp = get(SOURCE_NDC, _NDC_PMI_URL, throttle_bucket="ndc_pmi")
    try:
        text = resp.content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CollectorError(SOURCE_NDC, f"decode failed: {e}", retriable=False) from e

    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        return []
    header = rows[0]
    try:
        idx_date = header.index("Date")
        idx_pmi = header.index("PMI")
    except ValueError as e:
        raise CollectorError(SOURCE_NDC, f"未預期的欄位: {header}", retriable=False) from e

    out = []
    for row in rows[1:]:
        if not row or len(row) <= max(idx_date, idx_pmi):
            continue
        date_s = row[idx_date].strip()
        if len(date_s) != 6 or not date_s.isdigit():
            continue
        value_s = row[idx_pmi].strip()
        if value_s in ("", "-"):
            continue
        try:
            value = float(value_s)
        except ValueError:
            continue
        out.append({"date": f"{date_s[:4]}-{date_s[4:6]}-01", "value": value})
    return out
