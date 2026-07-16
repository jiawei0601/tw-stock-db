"""族群（概念股）資金流向彙總（股數 + 金額，日/週）— sector_flow_* 系列的族群版。

從 institutional_flow_daily JOIN stock_groups（19 個族群、91 檔標的）聚合出「每個族群
每日/每週三大法人買賣超合計」，完全比照 build_sector_flow.py + build_sector_flow_value.py
的既有模式，一次產出四張表：
    group_flow_daily / group_flow_weekly              （股數口徑，schema 比照
                                                          sector_flow_daily/weekly，
                                                          只是 industry_name 換成 group_name）
    group_flow_value_daily / group_flow_value_weekly  （金額口徑，JOIN daily_prices，schema
                                                          比照 sector_flow_value_daily/weekly，
                                                          含 NULL 語意與涵蓋率欄位的既有設計）

**重要語意註記：族群成分重疊，跨族群加總會重複計算**
`stock_groups` 的 PK 是 (stock_id, group_name)，同一檔股票可以同時屬於多個族群
（例如 2330 台積電同時屬於「CoWoS先進封裝設備」與「半導體設備」等族群，實測 91 檔中有
多檔股票橫跨 2 個以上族群）。這跟板塊表（sector_flow_*，每檔股票在 stocks.industry_name
裡只有唯一一個官方產業別）的假設完全不同——**group_flow_* 系列的數字絕對不可以跨族群
相加**（例如「把 19 個族群的 total_net 加總」得到的數字沒有意義，會重複計算橫跨多族群
的股票，跟全市場三大法人合計對不上）。每一列的數字只在「該族群自己內部」的語意下成立，
用途是比較「不同概念股題材」之間的資金流向強弱，不是拆解全市場資金流向的加總分解。

week_index 沿用 sector_flow_weekly 完全相同的交易日序列切分（同一套 dates 列表、同一種
i // chunk_size 编号方式），確保族群版跟板塊版的「第 N 週」對到同一段日期區間，可互相對照。

範圍：stock_groups 全部 19 個族群、91 檔標的（人工整理的概念股清單，非官方產業別，
範圍不會隨 stocks 全市場擴大而變動，除非未來手動擴充 stock_groups 本身）。

Idempotent：整批刷新（DELETE + 整批 INSERT），因為來源（institutional_flow_daily +
daily_prices + stock_groups）本身都已經是完整的時序表/快照，沒有增量語意。

用法：
    python build_group_flow.py [--db-path PATH] [--chunk-size 5]
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS group_flow_daily (
    group_name    TEXT NOT NULL,
    date          TEXT NOT NULL,
    stock_count   INTEGER NOT NULL,
    foreign_net   INTEGER NOT NULL,
    trust_net     INTEGER NOT NULL,
    dealer_net    INTEGER NOT NULL,
    total_net     INTEGER NOT NULL,
    PRIMARY KEY (group_name, date)
);
CREATE INDEX IF NOT EXISTS idx_group_flow_date ON group_flow_daily(date);

CREATE TABLE IF NOT EXISTS group_flow_weekly (
    group_name     TEXT NOT NULL,
    week_index     INTEGER NOT NULL,
    week_start     TEXT NOT NULL,
    week_end       TEXT NOT NULL,
    trading_days   INTEGER NOT NULL,
    foreign_net    INTEGER NOT NULL,
    trust_net      INTEGER NOT NULL,
    dealer_net     INTEGER NOT NULL,
    total_net      INTEGER NOT NULL,
    PRIMARY KEY (group_name, week_index)
);
CREATE INDEX IF NOT EXISTS idx_group_flow_weekly_week ON group_flow_weekly(week_index);

CREATE TABLE IF NOT EXISTS group_flow_value_daily (
    group_name           TEXT NOT NULL,
    date                 TEXT NOT NULL,
    stock_count          INTEGER NOT NULL,  -- 該族群當天有三大法人股數流向資料的股票數
    priced_stock_count   INTEGER NOT NULL,  -- 其中同時查得到收盤價、實際被納入金額換算的股票數
    foreign_value        REAL,   -- 約略金額（股數 x 收盤價，新台幣元）；NULL = 當天族群內
                                  -- 完全沒有股票查得到收盤價，無法換算（不是 0）
    trust_value           REAL,
    dealer_value           REAL,
    total_value             REAL,
    PRIMARY KEY (group_name, date)
);
CREATE INDEX IF NOT EXISTS idx_group_flow_value_date ON group_flow_value_daily(date);

CREATE TABLE IF NOT EXISTS group_flow_value_weekly (
    group_name     TEXT NOT NULL,
    week_index     INTEGER NOT NULL,
    week_start     TEXT NOT NULL,
    week_end       TEXT NOT NULL,
    trading_days   INTEGER NOT NULL,  -- 該週交易日數（同 group_flow_weekly，供對照）
    priced_days    INTEGER NOT NULL,  -- 該週內「至少 1 檔股票有收盤價可換算」的交易日數
    foreign_value  REAL,   -- NULL = 該週 priced_days = 0，完全無法換算
    trust_value     REAL,
    dealer_value     REAL,
    total_value       REAL,
    PRIMARY KEY (group_name, week_index)
);
CREATE INDEX IF NOT EXISTS idx_group_flow_value_weekly_week ON group_flow_value_weekly(week_index);
"""


def _build_shares_daily(conn: sqlite3.Connection) -> dict:
    conn.execute("DELETE FROM group_flow_daily")
    conn.execute(
        """
        INSERT INTO group_flow_daily
            (group_name, date, stock_count, foreign_net, trust_net, dealer_net, total_net)
        SELECT
            g.group_name,
            f.date,
            COUNT(DISTINCT f.stock_id),
            SUM(f.foreign_net),
            SUM(f.trust_net),
            SUM(f.dealer_net),
            SUM(f.foreign_net + f.trust_net + f.dealer_net)
        FROM institutional_flow_daily f
        JOIN stock_groups g ON g.stock_id = f.stock_id
        GROUP BY g.group_name, f.date
        """
    )
    conn.commit()
    n_rows = conn.execute("SELECT COUNT(*) FROM group_flow_daily").fetchone()[0]
    n_groups = conn.execute("SELECT COUNT(DISTINCT group_name) FROM group_flow_daily").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM group_flow_daily").fetchone()
    return {"rows": n_rows, "groups": n_groups, "date_range": date_range}


def _trading_day_weeks(conn: sqlite3.Connection, chunk_size: int) -> tuple[dict, dict]:
    """依 sector_flow_daily 的交易日序列每 chunk_size 筆切一組，跟 build_sector_flow_weekly.py/
    build_sector_flow_value.py 用完全相同的日期序列與 week_index 編號邏輯，確保族群版跟板塊版
    的「第 N 週」對到同一段日期區間，可互相對照。"""
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM sector_flow_daily ORDER BY date"
    ).fetchall()]
    if not dates:
        raise RuntimeError("sector_flow_daily 是空的，請先跑 build_sector_flow.py")

    week_of_date: dict[str, int] = {}
    for i, d in enumerate(dates):
        week_of_date[d] = i // chunk_size
    week_bounds: dict[int, tuple[str, str, int]] = {}
    for week_idx in sorted(set(week_of_date.values())):
        days_in_week = sorted(d for d, w in week_of_date.items() if w == week_idx)
        week_bounds[week_idx] = (days_in_week[0], days_in_week[-1], len(days_in_week))
    return week_of_date, week_bounds


def _build_shares_weekly(conn: sqlite3.Connection, week_of_date: dict, week_bounds: dict) -> dict:
    conn.execute("DELETE FROM group_flow_weekly")

    rows = conn.execute(
        "SELECT group_name, date, foreign_net, trust_net, dealer_net, total_net "
        "FROM group_flow_daily"
    ).fetchall()

    agg: dict[tuple[str, int], list[int]] = {}
    for group_name, date, foreign_net, trust_net, dealer_net, total_net in rows:
        if date not in week_of_date:
            continue  # 理論上不會發生（daily 表本身就是從 institutional_flow_daily 衍生），保底跳過
        week_idx = week_of_date[date]
        key = (group_name, week_idx)
        if key not in agg:
            agg[key] = [0, 0, 0, 0]
        agg[key][0] += foreign_net
        agg[key][1] += trust_net
        agg[key][2] += dealer_net
        agg[key][3] += total_net

    insert_rows = []
    for (group_name, week_idx), (fn, tn, dn, total) in agg.items():
        week_start, week_end, n_days = week_bounds[week_idx]
        insert_rows.append((group_name, week_idx, week_start, week_end, n_days, fn, tn, dn, total))

    conn.executemany(
        "INSERT INTO group_flow_weekly "
        "(group_name, week_index, week_start, week_end, trading_days, "
        " foreign_net, trust_net, dealer_net, total_net) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        insert_rows,
    )
    conn.commit()

    n_rows = conn.execute("SELECT COUNT(*) FROM group_flow_weekly").fetchone()[0]
    n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM group_flow_weekly").fetchone()[0]
    n_groups = conn.execute("SELECT COUNT(DISTINCT group_name) FROM group_flow_weekly").fetchone()[0]
    return {"rows": n_rows, "weeks": n_weeks, "groups": n_groups}


def _build_value_daily(conn: sqlite3.Connection) -> dict:
    conn.execute("DELETE FROM group_flow_value_daily")
    conn.execute(
        """
        INSERT INTO group_flow_value_daily
            (group_name, date, stock_count, priced_stock_count,
             foreign_value, trust_value, dealer_value, total_value)
        SELECT
            g.group_name,
            f.date,
            COUNT(DISTINCT f.stock_id),
            COUNT(DISTINCT CASE WHEN p.close IS NOT NULL THEN f.stock_id END),
            SUM(CASE WHEN p.close IS NOT NULL THEN f.foreign_net * p.close END),
            SUM(CASE WHEN p.close IS NOT NULL THEN f.trust_net * p.close END),
            SUM(CASE WHEN p.close IS NOT NULL THEN f.dealer_net * p.close END),
            SUM(CASE WHEN p.close IS NOT NULL
                     THEN (f.foreign_net + f.trust_net + f.dealer_net) * p.close END)
        FROM institutional_flow_daily f
        JOIN stock_groups g ON g.stock_id = f.stock_id
        LEFT JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date
        GROUP BY g.group_name, f.date
        """
    )
    conn.commit()
    n_rows = conn.execute("SELECT COUNT(*) FROM group_flow_value_daily").fetchone()[0]
    n_null = conn.execute(
        "SELECT COUNT(*) FROM group_flow_value_daily WHERE total_value IS NULL"
    ).fetchone()[0]
    n_groups = conn.execute("SELECT COUNT(DISTINCT group_name) FROM group_flow_value_daily").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM group_flow_value_daily").fetchone()
    return {"rows": n_rows, "groups": n_groups, "date_range": date_range, "fully_null_rows": n_null}


def _build_value_weekly(conn: sqlite3.Connection, week_of_date: dict, week_bounds: dict) -> dict:
    conn.execute("DELETE FROM group_flow_value_weekly")

    rows = conn.execute(
        "SELECT group_name, date, priced_stock_count, foreign_value, trust_value, dealer_value, total_value "
        "FROM group_flow_value_daily"
    ).fetchall()

    # agg[(group, week)] = [foreign_sum, trust_sum, dealer_sum, total_sum, priced_days]
    agg: dict[tuple[str, int], list] = {}
    for group_name, dt, priced_stock_count, foreign_value, trust_value, dealer_value, total_value in rows:
        if dt not in week_of_date:
            continue  # 理論上不會發生，保底跳過
        week_idx = week_of_date[dt]
        key = (group_name, week_idx)
        if key not in agg:
            agg[key] = [0.0, 0.0, 0.0, 0.0, 0]
        if total_value is not None and priced_stock_count > 0:
            agg[key][0] += foreign_value or 0.0
            agg[key][1] += trust_value or 0.0
            agg[key][2] += dealer_value or 0.0
            agg[key][3] += total_value
            agg[key][4] += 1

    insert_rows = []
    for (group_name, week_idx), (fv, tv, dv, total, priced_days) in agg.items():
        week_start, week_end, n_days = week_bounds[week_idx]
        if priced_days == 0:
            insert_rows.append((group_name, week_idx, week_start, week_end, n_days, 0, None, None, None, None))
        else:
            insert_rows.append((group_name, week_idx, week_start, week_end, n_days, priced_days, fv, tv, dv, total))

    conn.executemany(
        "INSERT INTO group_flow_value_weekly "
        "(group_name, week_index, week_start, week_end, trading_days, priced_days, "
        " foreign_value, trust_value, dealer_value, total_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        insert_rows,
    )
    conn.commit()

    n_rows = conn.execute("SELECT COUNT(*) FROM group_flow_value_weekly").fetchone()[0]
    n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM group_flow_value_weekly").fetchone()[0]
    n_null = conn.execute("SELECT COUNT(*) FROM group_flow_value_weekly WHERE total_value IS NULL").fetchone()[0]
    return {"rows": n_rows, "weeks": n_weeks, "fully_null_rows": n_null}


def build(db_path: Path, chunk_size: int = 5) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)

        for table in ("institutional_flow_daily", "stock_groups", "daily_prices", "sector_flow_daily"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n == 0:
                raise RuntimeError(f"{table} 是空的，請先跑對應的 build 腳本")

        shares_daily = _build_shares_daily(conn)
        week_of_date, week_bounds = _trading_day_weeks(conn, chunk_size)
        shares_weekly = _build_shares_weekly(conn, week_of_date, week_bounds)
        value_daily = _build_value_daily(conn)
        value_weekly = _build_value_weekly(conn, week_of_date, week_bounds)

        return {
            **shares_daily,
            **{f"weekly_{k}": v for k, v in shares_weekly.items()},
            **{f"value_{k}": v for k, v in value_daily.items()},
            **{f"value_weekly_{k}": v for k, v in value_weekly.items()},
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--chunk-size", type=int, default=5)
    args = parser.parse_args()

    summary = build(args.db_path, args.chunk_size)
    print(f"group_flow_daily 建置完成：{summary['rows']} 列、{summary['groups']} 個族群、"
          f"日期範圍 {summary['date_range']}")
    print(f"group_flow_weekly 建置完成：{summary['weekly_rows']} 列、{summary['weekly_weeks']} 週、"
          f"{summary['weekly_groups']} 個族群")
    print(f"group_flow_value_daily 建置完成：{summary['value_rows']} 列、{summary['value_groups']} 個族群、"
          f"日期範圍 {summary['value_date_range']}，其中 {summary['value_fully_null_rows']} 列完全無法換算金額（收盤價缺口）")
    print(f"group_flow_value_weekly 建置完成：{summary['value_weekly_rows']} 列、{summary['value_weekly_weeks']} 週，"
          f"其中 {summary['value_weekly_fully_null_rows']} 列完全無法換算金額")
    print("注意：族群成分重疊（一檔股票可屬多個族群），跨族群把 total_net/total_value 加總"
          "沒有意義，會重複計算橫跨多族群的股票，見腳本 docstring。")


if __name__ == "__main__":
    main()
