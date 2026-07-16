"""大盤加權指數（TAIEX）歷史日收盤 backfill — 涵蓋範圍比照 sector_flow_daily
的日期範圍（近 3 年），供週度資金流動畫的指數走勢參考線使用。

Idempotent：INSERT OR REPLACE by date；可續傳（重跑只會覆蓋，不會重複）。
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path

from collectors.taiex import fetch_month
from models import CollectorError

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS taiex_daily (
    date    TEXT PRIMARY KEY,
    close   REAL NOT NULL,
    change  REAL,
    updated_at TEXT NOT NULL
);
"""


def _month_range(start: date, end: date) -> list[tuple[int, int]]:
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def build(db_path: Path, start_date: str | None = None, end_date: str | None = None) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)

        if start_date is None or end_date is None:
            row = conn.execute("SELECT MIN(date), MAX(date) FROM sector_flow_daily").fetchone()
            if row is None or row[0] is None:
                raise RuntimeError("sector_flow_daily 是空的，無法推斷日期範圍，請先跑 build_sector_flow.py")
            start_date = start_date or row[0]
            end_date = end_date or row[1]

        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        n_months = 0
        n_rows = 0
        failed_months: list[str] = []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        for y, m in _month_range(start, end):
            try:
                rows = fetch_month(y, m)
            except CollectorError as e:
                failed_months.append(f"{y:04d}-{m:02d} ({e})")
                continue
            if not rows:
                continue
            conn.executemany(
                "INSERT OR REPLACE INTO taiex_daily (date, close, change, updated_at) VALUES (?, ?, ?, ?)",
                [(r["date"], r["close"], r["change"], now) for r in rows],
            )
            conn.commit()
            n_months += 1
            n_rows += len(rows)

        total = conn.execute("SELECT COUNT(*) FROM taiex_daily").fetchone()[0]
        date_range = conn.execute("SELECT MIN(date), MAX(date) FROM taiex_daily").fetchone()
        return {
            "months_fetched": n_months,
            "rows_this_run": n_rows,
            "total_rows": total,
            "date_range": date_range,
            "failed_months": failed_months,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    args = parser.parse_args()

    summary = build(args.db_path, args.start_date, args.end_date)
    print(
        f"taiex_daily 建置完成：本次抓 {summary['months_fetched']} 個月、"
        f"{summary['rows_this_run']} 筆，總計 {summary['total_rows']} 筆，"
        f"日期範圍 {summary['date_range']}"
    )
    if summary["failed_months"]:
        print(f"抓取失敗的月份：{summary['failed_months']}")


if __name__ == "__main__":
    main()
