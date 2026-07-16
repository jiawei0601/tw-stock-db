"""板塊（官方產業別）資金流向彙總 — 從 institutional_flow_daily 依 stocks.industry_name
聚合出每個板塊每日三大法人買賣超合計，供板塊間資金流動關係分析與未來視覺化使用。

範圍：只涵蓋 stock_groups 名單（91 檔）目前橫跨的 13 個板塊，不是全市場 34 個板塊
（全市場覆蓋需要 institutional_flow_daily 擴大到 1971 檔，非本次範圍，見 HANDOFF.md）。

Idempotent：整批刷新（DELETE + 整批 INSERT），因為來源本身（institutional_flow_daily）
已經是完整的 3 年時序表，沒有增量語意。
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_flow_daily (
    industry_name TEXT NOT NULL,
    date          TEXT NOT NULL,
    stock_count   INTEGER NOT NULL,
    foreign_net   INTEGER NOT NULL,
    trust_net     INTEGER NOT NULL,
    dealer_net    INTEGER NOT NULL,
    total_net     INTEGER NOT NULL,
    PRIMARY KEY (industry_name, date)
);
CREATE INDEX IF NOT EXISTS idx_sector_flow_date ON sector_flow_daily(date);
"""


def build(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM sector_flow_daily")
        conn.execute(
            """
            INSERT INTO sector_flow_daily
                (industry_name, date, stock_count, foreign_net, trust_net, dealer_net, total_net)
            SELECT
                s.industry_name,
                f.date,
                COUNT(DISTINCT f.stock_id),
                SUM(f.foreign_net),
                SUM(f.trust_net),
                SUM(f.dealer_net),
                SUM(f.foreign_net + f.trust_net + f.dealer_net)
            FROM institutional_flow_daily f
            JOIN stocks s ON s.stock_id = f.stock_id
            WHERE f.stock_id IN (SELECT DISTINCT stock_id FROM stock_groups)
            GROUP BY s.industry_name, f.date
            """
        )
        conn.commit()

        n_rows = conn.execute("SELECT COUNT(*) FROM sector_flow_daily").fetchone()[0]
        n_sectors = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_daily").fetchone()[0]
        date_range = conn.execute("SELECT MIN(date), MAX(date) FROM sector_flow_daily").fetchone()
        return {"rows": n_rows, "sectors": n_sectors, "date_range": date_range}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    summary = build(args.db_path)
    print(f"sector_flow_daily 建置完成：{summary['rows']} 列、{summary['sectors']} 個板塊、"
          f"日期範圍 {summary['date_range']}")


if __name__ == "__main__":
    main()
