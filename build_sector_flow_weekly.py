"""板塊資金流「每 5 個交易日」切分彙整 — 從 sector_flow_daily 依交易日序列每 5 筆
切成一組（不對齊日曆週，避開假日造成的週期不整問題），供週度資金流動動畫使用。

範圍：全市場（sector_flow_daily 本身已涵蓋 stocks 表全部，見 build_sector_flow.py）。

Idempotent：整批刷新（DELETE + 整批 INSERT），因為來源本身（sector_flow_daily）
已經是完整的 3 年時序表，沒有增量語意。
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_flow_weekly (
    industry_name  TEXT NOT NULL,
    week_index     INTEGER NOT NULL,
    week_start     TEXT NOT NULL,
    week_end       TEXT NOT NULL,
    trading_days   INTEGER NOT NULL,
    foreign_net    INTEGER NOT NULL,
    trust_net      INTEGER NOT NULL,
    dealer_net     INTEGER NOT NULL,
    total_net      INTEGER NOT NULL,
    PRIMARY KEY (industry_name, week_index)
);
CREATE INDEX IF NOT EXISTS idx_sector_flow_weekly_week ON sector_flow_weekly(week_index);
"""


def build(db_path: Path, chunk_size: int = 5) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("DELETE FROM sector_flow_weekly")

        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM sector_flow_daily ORDER BY date"
        ).fetchall()]
        if not dates:
            raise RuntimeError("sector_flow_daily 是空的，請先跑 build_sector_flow.py")

        # 交易日依序每 chunk_size 筆切一組；最後一組可能不足 chunk_size 天（如實記錄天數）。
        week_of_date: dict[str, int] = {}
        week_bounds: dict[int, tuple[str, str, int]] = {}
        for i, d in enumerate(dates):
            week_idx = i // chunk_size
            week_of_date[d] = week_idx
        for week_idx in sorted(set(week_of_date.values())):
            days_in_week = [d for d, w in week_of_date.items() if w == week_idx]
            days_in_week.sort()
            week_bounds[week_idx] = (days_in_week[0], days_in_week[-1], len(days_in_week))

        rows = conn.execute(
            "SELECT industry_name, date, foreign_net, trust_net, dealer_net, total_net "
            "FROM sector_flow_daily"
        ).fetchall()

        agg: dict[tuple[str, int], list[int]] = {}
        for industry_name, date, foreign_net, trust_net, dealer_net, total_net in rows:
            week_idx = week_of_date[date]
            key = (industry_name, week_idx)
            if key not in agg:
                agg[key] = [0, 0, 0, 0]
            agg[key][0] += foreign_net
            agg[key][1] += trust_net
            agg[key][2] += dealer_net
            agg[key][3] += total_net

        insert_rows = []
        for (industry_name, week_idx), (fn, tn, dn, total) in agg.items():
            week_start, week_end, n_days = week_bounds[week_idx]
            insert_rows.append((industry_name, week_idx, week_start, week_end, n_days, fn, tn, dn, total))

        conn.executemany(
            "INSERT INTO sector_flow_weekly "
            "(industry_name, week_index, week_start, week_end, trading_days, "
            " foreign_net, trust_net, dealer_net, total_net) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            insert_rows,
        )
        conn.commit()

        n_rows = conn.execute("SELECT COUNT(*) FROM sector_flow_weekly").fetchone()[0]
        n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM sector_flow_weekly").fetchone()[0]
        n_sectors = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_weekly").fetchone()[0]
        return {"rows": n_rows, "weeks": n_weeks, "sectors": n_sectors}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--chunk-size", type=int, default=5)
    args = parser.parse_args()

    summary = build(args.db_path, args.chunk_size)
    print(f"sector_flow_weekly 建置完成：{summary['rows']} 列、{summary['weeks']} 週、"
          f"{summary['sectors']} 個板塊")


if __name__ == "__main__":
    main()
