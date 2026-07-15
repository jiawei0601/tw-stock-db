"""建置/刷新 `stock_groups` 名單股票的月營收 + 籌碼集中度快照。

延伸自 build_db.py 的同一個資料庫（`data/tw_stocks.db`），但只覆蓋 `SELECT DISTINCT
stock_id FROM stock_groups` 這批股票（目前 91 檔，動態查詢，不寫死清單），不對整個
1971 檔股票宇宙跑（TWSE/TDCC 全量下載一次即可涵蓋，無需逐檔打 API，見
collectors/revenue.py 與 collectors/shareholding.py 說明）。

跟 build_db.py 一樣是**整批快照覆蓋**（不是逐筆 upsert）：`monthly_revenue` 與
`shareholding_concentration` 兩表由本腳本完全擁有，每次執行先清空再整批寫入，
天然會讓「已從 stock_groups 移除的股票」跟著從這兩表消失，不會留孤兒列。

用法：
    python build_fundamentals.py [--db-path PATH]

執行內容：
    1. 抓 TWSE + TPEx 最新一期月營收全量（collectors/revenue.py），過濾出目標股票
    2. 抓 TDCC 股權分散表全市場最新一期快照（collectors/shareholding.py），過濾出目標股票，
       彙總 15 級距 -> 籌碼集中度代理指標
    3. 寫入 SQLite：monthly_revenue 表 + shareholding_concentration 表（整批覆蓋）
    4. 印出摘要：各資料維度成功/缺資料檔數與原因
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from collectors import revenue, shareholding

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

# 籌碼集中度代理指標門檻：TDCC 持股分級 12~15 對應 >400,001 股（>400 張），
# 分級 15 單獨對應 >1,000,000 股（>1000 張）。見 collectors/shareholding.py 模組說明。
_CONCENTRATION_400ZHANG_LEVELS = (12, 13, 14, 15)
_CONCENTRATION_1000ZHANG_LEVEL = 15
_TOTAL_LEVEL = 17  # TDCC「合計」列，人數/股數/比例為 1~16 加總

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monthly_revenue (
    stock_id                     TEXT PRIMARY KEY,
    company_name                 TEXT,
    ym                           TEXT,      -- 營收所屬年月 YYYY-MM
    announce_date                TEXT,      -- 公司公告出表日期 YYYY-MM-DD
    revenue                      INTEGER,   -- 當月營收（千元）
    revenue_prev_month           INTEGER,
    revenue_last_year_month      INTEGER,
    mom_pct                      REAL,      -- 較上月增減 %
    yoy_pct                      REAL,      -- 較去年同月增減 %（即使用者所稱「年增率」）
    revenue_cumulative           INTEGER,
    revenue_cumulative_last_year INTEGER,
    cumulative_yoy_pct           REAL,      -- 累計營收年增率
    remark                       TEXT,
    source_market                TEXT NOT NULL CHECK (source_market IN ('TWSE', 'TPEx')),
    updated_at                   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shareholding_concentration (
    stock_id           TEXT PRIMARY KEY,
    as_of              TEXT NOT NULL,   -- TDCC 資料日期（週更快照）YYYY-MM-DD
    total_holders      INTEGER,
    total_shares       INTEGER,
    pct_gt_400zhang    REAL,            -- 持股 >400 張股東合計占集保庫存比例（代理集中度指標）
    pct_gt_1000zhang   REAL,            -- 持股 >1000 張股東合計占集保庫存比例（代理集中度指標）
    levels_json        TEXT NOT NULL,   -- 15 級距明細 JSON: [{"level":1,"holders":..,"shares":..,"pct":..}, ...]
    updated_at         TEXT NOT NULL
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _target_stock_ids(conn: sqlite3.Connection) -> list[str]:
    """動態取自 stock_groups，不寫死清單（族群名單未來可能擴充）。"""
    cur = conn.execute("SELECT DISTINCT stock_id FROM stock_groups ORDER BY stock_id")
    return [r[0] for r in cur.fetchall()]


def build_revenue_rows(target_ids: set[str]) -> tuple[list[dict], list[str]]:
    twse_rows = revenue.fetch_twse_monthly_revenue()
    tpex_rows = revenue.fetch_tpex_monthly_revenue()
    by_id = {r["stock_id"]: r for r in twse_rows}
    by_id.update({r["stock_id"]: r for r in tpex_rows})

    now = _now_iso()
    rows: list[dict] = []
    missing: list[str] = []
    for sid in sorted(target_ids):
        r = by_id.get(sid)
        if r is None:
            missing.append(f"{sid}（TWSE/TPEx 最新一期月營收 opendata 皆查無此代號）")
            continue
        rows.append({**r, "updated_at": now})
    return rows, missing


def build_shareholding_rows(target_ids: set[str]) -> tuple[list[dict], list[str]]:
    raw_rows = shareholding.fetch_shareholding_distribution()
    by_stock: dict[str, dict[int, dict]] = {}
    for row in raw_rows:
        sid = row["stock_id"]
        if sid not in target_ids:
            continue
        by_stock.setdefault(sid, {})[row["level"]] = row

    now = _now_iso()
    rows: list[dict] = []
    missing: list[str] = []
    for sid in sorted(target_ids):
        levels = by_stock.get(sid)
        total_row = levels.get(_TOTAL_LEVEL) if levels else None
        if not levels or total_row is None:
            missing.append(f"{sid}（TDCC 股權分散表查無此代號，或缺合計列）")
            continue

        detail_levels = {lv: levels[lv] for lv in range(1, 16) if lv in levels}
        pct_400 = sum(levels[lv]["pct"] for lv in _CONCENTRATION_400ZHANG_LEVELS if lv in levels)
        pct_1000 = levels[_CONCENTRATION_1000ZHANG_LEVEL]["pct"] if _CONCENTRATION_1000ZHANG_LEVEL in levels else None

        rows.append({
            "stock_id": sid,
            "as_of": total_row["as_of"],
            "total_holders": total_row["holders"],
            "total_shares": total_row["shares"],
            "pct_gt_400zhang": round(pct_400, 4) if detail_levels else None,
            "pct_gt_1000zhang": pct_1000,
            "levels_json": json.dumps(
                [
                    {"level": lv, "holders": v["holders"], "shares": v["shares"], "pct": v["pct"]}
                    for lv, v in sorted(detail_levels.items())
                ],
                ensure_ascii=False,
            ),
            "updated_at": now,
        })
    return rows, missing


def write_db(revenue_rows: list[dict], shareholding_rows: list[dict], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        with conn:
            conn.execute("DELETE FROM monthly_revenue")
            conn.executemany(
                """
                INSERT INTO monthly_revenue
                    (stock_id, company_name, ym, announce_date, revenue, revenue_prev_month,
                     revenue_last_year_month, mom_pct, yoy_pct, revenue_cumulative,
                     revenue_cumulative_last_year, cumulative_yoy_pct, remark, source_market, updated_at)
                VALUES (:stock_id, :company_name, :ym, :announce_date, :revenue, :revenue_prev_month,
                        :revenue_last_year_month, :mom_pct, :yoy_pct, :revenue_cumulative,
                        :revenue_cumulative_last_year, :cumulative_yoy_pct, :remark, :source_market, :updated_at)
                """,
                revenue_rows,
            )
            conn.execute("DELETE FROM shareholding_concentration")
            conn.executemany(
                """
                INSERT INTO shareholding_concentration
                    (stock_id, as_of, total_holders, total_shares, pct_gt_400zhang,
                     pct_gt_1000zhang, levels_json, updated_at)
                VALUES (:stock_id, :as_of, :total_holders, :total_shares, :pct_gt_400zhang,
                        :pct_gt_1000zhang, :levels_json, :updated_at)
                """,
                shareholding_rows,
            )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新 stock_groups 名單的月營收 + 籌碼集中度快照")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    if not args.db_path.exists():
        raise SystemExit(f"{args.db_path} 不存在，請先跑: python build_db.py")

    conn = sqlite3.connect(args.db_path)
    try:
        target_ids = _target_stock_ids(conn)
    finally:
        conn.close()
    if not target_ids:
        raise SystemExit("stock_groups 目前沒有任何股票，先確認 build_db.py 已建置族群資料")
    target_set = set(target_ids)

    print(f"目標股票（來自 stock_groups DISTINCT stock_id）：{len(target_ids)} 檔")

    print("\n=== 抓取月營收 ===")
    revenue_rows, revenue_missing = build_revenue_rows(target_set)
    print(f"月營收成功: {len(revenue_rows)}/{len(target_ids)} 檔")
    if revenue_missing:
        print("缺資料：")
        for m in revenue_missing:
            print(f"  - {m}")

    print("\n=== 抓取籌碼集中度（TDCC 股權分散表）===")
    shareholding_rows, shareholding_missing = build_shareholding_rows(target_set)
    print(f"籌碼集中度成功: {len(shareholding_rows)}/{len(target_ids)} 檔")
    if shareholding_missing:
        print("缺資料：")
        for m in shareholding_missing:
            print(f"  - {m}")

    write_db(revenue_rows, shareholding_rows, args.db_path)
    print(f"\n寫入完成: {args.db_path}")


if __name__ == "__main__":
    main()
