"""共用型別契約 — 唯一真相（比照 tw-momentum-scanner/models.py 的設計哲學）。"""
from __future__ import annotations


class CollectorError(Exception):
    """資料採集失敗。retriable=True 表示 403/429/timeout 等可退避重試的錯誤。"""

    def __init__(self, source: str, message: str,
                 http_status: int | None = None, retriable: bool = False):
        super().__init__(f"[{source}] {message} (http={http_status}, retriable={retriable})")
        self.source = source
        self.http_status = http_status
        self.retriable = retriable
