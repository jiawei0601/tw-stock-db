"""集保結算所（TDCC）股權分散表 collector — 籌碼集中度資料源。

見 docs/data-sources.md 第 8 節（endpoint 實測結果，2026-07-16）。

- 主要 URL：https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
  備援 URL：https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5（同一份資料，備用網域）
- 格式：CSV（`Content-Type: text/csv; charset=UTF-8`），requests 能正確自動偵測編碼
  （`r.encoding == 'UTF-8'`），但檔頭有 UTF-8 BOM（`﻿`），解析前需 lstrip 掉。
- 欄位：資料日期(YYYYMMDD)、證券代號、持股分級、人數、股數、占集保庫存數比例%
- **每週更新一次、只回「當週最新一期」全市場快照，無歷史**（TDCC 官方就是這樣提供，
  不是本專案的限制）。
- **這份資料涵蓋全市場所有證券代號**（實測 4003 個不同代號，含 ETF/TDR/受益證券等，
  不是只有普通股），呼叫端必須用自己的 stock_id 白名單過濾。
- 證券代號欄位是**固定 6 碼、右側補半形空格**（例如 `2330  `），不是單純 4 碼數字，
  比對前必須 `.strip()`。
- 持股分級 1~15 為 15 個標準級距（1,000 股以下 ~ 1,000,000 股以上，即台股慣稱的
  「張」= 1,000 股，故級距 15 即「持股 1,000 張以上」）；級距 16 實測對一般股票恆為
  0（保留用途不明，未查證，忠實記錄不臆測）；級距 17 為該證券的「合計」列
  （人數/股數/比例皆為 1~16 加總，可直接當作該證券的總集保人數/總集保股數使用，
  不需自行加總 1~15，避免因級距 16 語意不明而重複計算）。
"""
from __future__ import annotations

import csv
import io

from models import CollectorError

from ._http import get

SOURCE = "shareholding"

_URL_PRIMARY = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
_URL_FALLBACK = "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5"


def _parse_csv(text: str) -> list[dict]:
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    rows: list[dict] = []
    header = next(reader, None)
    if not header:
        return rows
    for parts in reader:
        if len(parts) < 6:
            continue
        as_of_raw, stock_id_raw, level_raw, holders_raw, shares_raw, pct_raw = parts[:6]
        as_of_raw = as_of_raw.strip()
        as_of = f"{as_of_raw[0:4]}-{as_of_raw[4:6]}-{as_of_raw[6:8]}" if len(as_of_raw) == 8 else None
        try:
            level = int(level_raw.strip())
            holders = int(holders_raw.strip())
            shares = int(shares_raw.strip())
            pct = float(pct_raw.strip())
        except ValueError:
            continue
        rows.append({
            "as_of": as_of,
            "stock_id": stock_id_raw.strip(),
            "level": level,
            "holders": holders,
            "shares": shares,
            "pct": pct,
        })
    return rows


def fetch_shareholding_distribution() -> list[dict]:
    """全市場股權分散表最新一期快照（含所有證券代號，呼叫端自行過濾白名單）。

    回傳每列 {as_of, stock_id, level, holders, shares, pct}。HTTP 200 但空資料回空 list，
    不造假。主要網域失敗（retriable）時嘗試備援網域；備援也失敗則讓 CollectorError 往外拋。
    """
    try:
        resp = get(SOURCE, _URL_PRIMARY, throttle_bucket="shareholding", timeout=60.0)
    except CollectorError as e:
        if not e.retriable:
            raise
        resp = get(SOURCE, _URL_FALLBACK, throttle_bucket="shareholding", timeout=60.0)

    if not resp.text or "證券代號" not in resp.text:
        return []
    return _parse_csv(resp.text)
