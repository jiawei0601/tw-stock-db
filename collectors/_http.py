"""collectors/ 共用 HTTP 基礎設施（比照 tw-momentum-scanner/collectors/_http.py 的設計哲學）。

- 共用 requests.Session，內建瀏覽器 UA（TPEx 需要，否則會被擋）
- 限流：同一 bucket（來源）兩次請求間至少間隔 MIN_INTERVAL 秒（TWSE 建議 3 req/5s，本專案保守用同一節流器）
- 重試：retriable 錯誤（403/429/timeout/5xx）指數退避 5/20/60 秒，共 3 次
- 統一拋 models.CollectorError，呼叫端不需自行 try/except requests 例外
"""
from __future__ import annotations

import time

import requests

from models import CollectorError

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

MIN_INTERVAL_SEC = 1.7  # 保守節流，比 TWSE 官方建議的 3 req/5s 再寬鬆一些
RETRY_BACKOFF_SEC = (5, 20, 60)
RETRIABLE_STATUS = {403, 429, 500, 502, 503, 504}

_session = requests.Session()
_last_request_ts: dict[str, float] = {}


def _throttle(bucket: str, min_interval: float) -> None:
    """依 bucket（來源）節流；同一 bucket 兩次請求間至少間隔 min_interval 秒。"""
    last = _last_request_ts.get(bucket)
    now = time.monotonic()
    if last is not None:
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_ts[bucket] = time.monotonic()


def get(
    source: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 20.0,
    throttle_bucket: str | None = None,
    min_interval: float = MIN_INTERVAL_SEC,
    encoding: str | None = None,
) -> requests.Response:
    """統一 GET 入口：節流 + 重試 + 錯誤轉譯為 CollectorError。

    encoding：若來源回應的 Content-Type 宣告編碼不準確（例如 ISIN 頁面宣告 MS950 但
    requests 有時猜錯），呼叫端可強制指定（例如 'big5'）。
    """
    bucket = throttle_bucket or source
    req_headers = {"User-Agent": BROWSER_UA}
    if headers:
        req_headers.update(headers)

    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(len(RETRY_BACKOFF_SEC) + 1):
        _throttle(bucket, min_interval)
        try:
            resp = _session.get(url, params=params, headers=req_headers, timeout=timeout)
        except requests.Timeout as e:
            last_exc = e
            last_status = None
            if attempt < len(RETRY_BACKOFF_SEC):
                time.sleep(RETRY_BACKOFF_SEC[attempt])
                continue
            raise CollectorError(source, f"timeout: {e}", http_status=None, retriable=True) from e
        except requests.RequestException as e:
            raise CollectorError(source, f"request failed: {e}", http_status=None, retriable=False) from e

        if resp.status_code == 200:
            if encoding:
                resp.encoding = encoding
            return resp

        last_status = resp.status_code
        if resp.status_code in RETRIABLE_STATUS and attempt < len(RETRY_BACKOFF_SEC):
            time.sleep(RETRY_BACKOFF_SEC[attempt])
            continue

        retriable = resp.status_code in RETRIABLE_STATUS
        raise CollectorError(
            source, f"HTTP {resp.status_code}: {resp.text[:200]}",
            http_status=resp.status_code, retriable=retriable,
        )

    # 理論上不會到這裡（迴圈內每個分支都 return 或 raise），保底處理
    raise CollectorError(
        source, f"exhausted retries: {last_exc}", http_status=last_status, retriable=True,
    )
