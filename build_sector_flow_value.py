"""板塊資金「金額」流向彙總（日/週）— 從 institutional_flow_daily 的股數 JOIN
daily_prices 的收盤價，換算成「約略金額」（股數 x 收盤價，單位新台幣元）後依
stocks.industry_name 聚合，供跟 TAIEX 指數（市值加權）方向對照使用。

**為什麼需要這張表（跟既有的 sector_flow_daily/sector_flow_weekly 的差異）**：
sector_flow_daily/weekly 存的是「買賣超股數」，但 TAIEX 是市值加權指數，股數不能直接
跟指數點數比較（賣 1 億股 10 元的小型股，跟賣 1 億股 1000 元的台積電，對指數的影響天差
地遠）。本表把股數換算成金額，才能跟指數的金額/市值邏輯對得上。**這是新增的分析維度，
不取代、不修改 sector_flow_daily/sector_flow_weekly（股數版本對「籌碼張數變化」本身
仍有意義，兩者並存）。**

**金額是近似值，不是精確成交金額**：同一天同一檔股票的買賣可能發生在不同價位，
本表一律用「當日收盤價」換算全部買賣超股數，這是近似值不是逐筆成交價的精確金額，
文件與程式碼皆需註記此限制。

**收盤價缺口的處理原則**：某檔股票某天若在 daily_prices 查無收盤價（見
build_daily_prices.py 涵蓋率報告），該筆股數流向**不可硬套/亂猜價格**，直接排除在
金額加總之外（SQL SUM 對 NULL 項自然略過），並記錄該板塊/週次有多少檔股票缺價，
讓使用者知情，不是默默漏算。

範圍：只涵蓋 institutional_flow_daily x daily_prices 有交集的部分（即
build_daily_prices.py 實際查到收盤價的股票x日期組合），不臆測缺價股票的金額。

Idempotent：整批刷新（DELETE + 整批 INSERT），因為來源（institutional_flow_daily +
daily_prices）本身已經是完整的時序表，沒有增量語意。

用法：
    python build_sector_flow_value.py [--db-path PATH] [--chunk-size 5]
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_flow_value_daily (
    industry_name       TEXT NOT NULL,
    date                 TEXT NOT NULL,
    stock_count          INTEGER NOT NULL,  -- 該板塊當天有三大法人股數流向資料的股票數
    priced_stock_count   INTEGER NOT NULL,  -- 其中同時查得到收盤價、實際被納入金額換算的股票數
    foreign_value        REAL,   -- 約略金額（股數 x 收盤價，新台幣元）；NULL = 當天板塊內
                                  -- 完全沒有股票查得到收盤價，無法換算（不是 0）
    trust_value           REAL,
    dealer_value           REAL,
    total_value             REAL,
    PRIMARY KEY (industry_name, date)
);
CREATE INDEX IF NOT EXISTS idx_sector_flow_value_date ON sector_flow_value_daily(date);

CREATE TABLE IF NOT EXISTS sector_flow_value_weekly (
    industry_name  TEXT NOT NULL,
    week_index     INTEGER NOT NULL,
    week_start     TEXT NOT NULL,
    week_end       TEXT NOT NULL,
    trading_days   INTEGER NOT NULL,  -- 該週交易日數（同 sector_flow_weekly，供對照）
    priced_days    INTEGER NOT NULL,  -- 該週內「至少 1 檔股票有收盤價可換算」的交易日數
    foreign_value  REAL,   -- NULL = 該週 priced_days = 0，完全無法換算
    trust_value     REAL,
    dealer_value     REAL,
    total_value       REAL,
    PRIMARY KEY (industry_name, week_index)
);
CREATE INDEX IF NOT EXISTS idx_sector_flow_value_weekly_week ON sector_flow_value_weekly(week_index);
"""


def _build_daily(conn: sqlite3.Connection) -> dict:
    conn.execute("DELETE FROM sector_flow_value_daily")
    conn.execute(
        """
        INSERT INTO sector_flow_value_daily
            (industry_name, date, stock_count, priced_stock_count,
             foreign_value, trust_value, dealer_value, total_value)
        SELECT
            s.industry_name,
            f.date,
            COUNT(DISTINCT f.stock_id),
            COUNT(DISTINCT CASE WHEN p.close IS NOT NULL THEN f.stock_id END),
            SUM(CASE WHEN p.close IS NOT NULL THEN f.foreign_net * p.close END),
            SUM(CASE WHEN p.close IS NOT NULL THEN f.trust_net * p.close END),
            SUM(CASE WHEN p.close IS NOT NULL THEN f.dealer_net * p.close END),
            SUM(CASE WHEN p.close IS NOT NULL
                     THEN (f.foreign_net + f.trust_net + f.dealer_net) * p.close END)
        FROM institutional_flow_daily f
        JOIN stocks s ON s.stock_id = f.stock_id
        LEFT JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date
        GROUP BY s.industry_name, f.date
        """
    )
    conn.commit()
    n_rows = conn.execute("SELECT COUNT(*) FROM sector_flow_value_daily").fetchone()[0]
    n_null = conn.execute(
        "SELECT COUNT(*) FROM sector_flow_value_daily WHERE total_value IS NULL"
    ).fetchone()[0]
    n_sectors = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_value_daily").fetchone()[0]
    date_range = conn.execute("SELECT MIN(date), MAX(date) FROM sector_flow_value_daily").fetchone()
    return {"rows": n_rows, "sectors": n_sectors, "date_range": date_range, "fully_null_rows": n_null}


def _build_weekly(conn: sqlite3.Connection, chunk_size: int) -> dict:
    """依 sector_flow_daily 的交易日序列每 chunk_size 筆切一組（跟 build_sector_flow_weekly.py
    用完全相同的日期序列與 week_index 編號邏輯，確保股數版跟金額版的「第 N 週」對到同一段
    日期區間，動畫互相對照時不會錯位）。"""
    conn.execute("DELETE FROM sector_flow_value_weekly")

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

    rows = conn.execute(
        "SELECT industry_name, date, priced_stock_count, foreign_value, trust_value, dealer_value, total_value "
        "FROM sector_flow_value_daily"
    ).fetchall()

    # agg[(industry, week)] = [foreign_sum, trust_sum, dealer_sum, total_sum, priced_days]
    agg: dict[tuple[str, int], list] = {}
    for industry_name, dt, priced_stock_count, foreign_value, trust_value, dealer_value, total_value in rows:
        if dt not in week_of_date:
            continue  # 理論上不會發生（daily 表本身就是從 institutional_flow_daily 衍生），保底跳過
        week_idx = week_of_date[dt]
        key = (industry_name, week_idx)
        if key not in agg:
            agg[key] = [0.0, 0.0, 0.0, 0.0, 0]
        if total_value is not None and priced_stock_count > 0:
            agg[key][0] += foreign_value or 0.0
            agg[key][1] += trust_value or 0.0
            agg[key][2] += dealer_value or 0.0
            agg[key][3] += total_value
            agg[key][4] += 1

    insert_rows = []
    for (industry_name, week_idx), (fv, tv, dv, total, priced_days) in agg.items():
        week_start, week_end, n_days = week_bounds[week_idx]
        if priced_days == 0:
            insert_rows.append((industry_name, week_idx, week_start, week_end, n_days, 0, None, None, None, None))
        else:
            insert_rows.append((industry_name, week_idx, week_start, week_end, n_days, priced_days, fv, tv, dv, total))

    conn.executemany(
        "INSERT INTO sector_flow_value_weekly "
        "(industry_name, week_index, week_start, week_end, trading_days, priced_days, "
        " foreign_value, trust_value, dealer_value, total_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        insert_rows,
    )
    conn.commit()

    n_rows = conn.execute("SELECT COUNT(*) FROM sector_flow_value_weekly").fetchone()[0]
    n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM sector_flow_value_weekly").fetchone()[0]
    n_null = conn.execute("SELECT COUNT(*) FROM sector_flow_value_weekly WHERE total_value IS NULL").fetchone()[0]
    return {"rows": n_rows, "weeks": n_weeks, "fully_null_rows": n_null}


def build(db_path: Path, chunk_size: int = 5) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)

        for table in ("institutional_flow_daily", "daily_prices", "sector_flow_daily"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if n == 0:
                raise RuntimeError(f"{table} 是空的，請先跑對應的 build 腳本")

        daily_summary = _build_daily(conn)
        weekly_summary = _build_weekly(conn, chunk_size)
        return {**daily_summary, **{f"weekly_{k}": v for k, v in weekly_summary.items()}}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--chunk-size", type=int, default=5)
    args = parser.parse_args()

    summary = build(args.db_path, args.chunk_size)
    print(f"sector_flow_value_daily 建置完成：{summary['rows']} 列、{summary['sectors']} 個板塊、"
          f"日期範圍 {summary['date_range']}，其中 {summary['fully_null_rows']} 列完全無法換算金額（收盤價缺口）")
    print(f"sector_flow_value_weekly 建置完成：{summary['weekly_rows']} 列、{summary['weekly_weeks']} 週，"
          f"其中 {summary['weekly_fully_null_rows']} 列完全無法換算金額")


if __name__ == "__main__":
    main()
