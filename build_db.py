"""建置/刷新台股上市+上櫃股票資料庫（含官方產業別標記）。

跑一次就能從零建置整個資料庫，idempotent（重跑不會產生重複列 —— 每次先清空 stocks 表
再整批寫入，不用 INSERT OR REPLACE 逐筆比對，因為本來就是「整批刷新」語意：資料來源
只提供「當下最新」快照，沒有增量更新的必要）。

用法：
    python build_db.py [--db-path PATH]

執行內容：
    1. 抓 TWSE + TPEx 股票清單（collectors/isin.py，含官方產業別文字）
    2. 抓 TWSE + TPEx 官方產業別數字代碼（collectors/company_info.py），用 stock_id 對應補齊
    3. 寫入 SQLite：stocks 表（整批刷新）＋ stock_groups 表（建表結構，本次不寫入資料，
       留給未來「族群/概念股」標記任務使用）
    4. 印出摘要：上市/上櫃檔數、產業別分布、缺 industry_code 的股票清單（若有）
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from collectors import company_info, isin
from models import CollectorError

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stocks (
    stock_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    market        TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEx')),
    isin          TEXT,
    listed_date   TEXT,
    industry_code TEXT,
    industry_name TEXT,
    cfi_code      TEXT,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stocks_industry_code ON stocks(industry_code);
CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);

-- 族群/概念股標記表（本次任務只建表結構，不填資料 —— 沒有官方資料來源，留待未來任務）
CREATE TABLE IF NOT EXISTS stock_groups (
    stock_id   TEXT NOT NULL,
    group_name TEXT NOT NULL,
    group_type TEXT NOT NULL,   -- 例如未來的 'concept' / 'theme'
    source     TEXT NOT NULL,   -- 標記依據來源（人工/爬蟲/第三方資料商...）
    created_at TEXT NOT NULL,
    PRIMARY KEY (stock_id, group_name)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_all() -> list[dict]:
    """依序抓 TWSE/TPEx 股票清單 + 官方產業別代碼，合併成待寫入資料庫的列。

    任一 collector 拋 CollectorError 就整體中止（不寫入部分資料，避免資料庫出現
    「上市有、上櫃缺」這種不上不下的半殘狀態）。
    """
    twse_stocks = isin.fetch_twse_stocks()
    tpex_stocks = isin.fetch_tpex_stocks()
    if not twse_stocks:
        raise CollectorError("build_db", "TWSE 股票清單為空，中止（避免清空既有資料庫）", retriable=True)
    if not tpex_stocks:
        raise CollectorError("build_db", "TPEx 股票清單為空，中止（避免清空既有資料庫）", retriable=True)

    twse_codes = company_info.fetch_twse_industry_codes()
    tpex_codes = company_info.fetch_tpex_industry_codes()

    now = _now_iso()
    rows: list[dict] = []
    missing_industry_code: list[str] = []

    for stock in twse_stocks:
        code = stock["stock_id"]
        industry_code = twse_codes.get(code)
        if industry_code is None:
            missing_industry_code.append(f"TWSE {code} {stock['name']}")
        rows.append({**stock, "industry_code": industry_code, "updated_at": now})

    for stock in tpex_stocks:
        code = stock["stock_id"]
        industry_code = tpex_codes.get(code)
        if industry_code is None:
            missing_industry_code.append(f"TPEx {code} {stock['name']}")
        rows.append({**stock, "industry_code": industry_code, "updated_at": now})

    if missing_industry_code:
        print(f"[警告] {len(missing_industry_code)} 檔股票在官方公司基本資料中查無數字產業別代碼"
              f"（industry_name 仍有值，僅 industry_code 留空，不臆測）：")
        for line in missing_industry_code:
            print(f"  - {line}")

    return rows


def write_db(rows: list[dict], db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        with conn:
            conn.execute("DELETE FROM stocks")  # 整批刷新，idempotent
            conn.executemany(
                """
                INSERT INTO stocks
                    (stock_id, name, market, isin, listed_date,
                     industry_code, industry_name, cfi_code, updated_at)
                VALUES (:stock_id, :name, :market, :isin, :listed_date,
                        :industry_code, :industry_name, :cfi_code, :updated_at)
                """,
                rows,
            )
    finally:
        conn.close()


def print_summary(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT market, COUNT(*) FROM stocks GROUP BY market")
        print("\n=== 市場別檔數 ===")
        for market, count in cur.fetchall():
            print(f"  {market}: {count}")

        cur.execute(
            "SELECT market, industry_name, COUNT(*) FROM stocks "
            "GROUP BY market, industry_name ORDER BY market, COUNT(*) DESC"
        )
        print("\n=== 產業別分布 ===")
        current_market = None
        for market, industry_name, count in cur.fetchall():
            if market != current_market:
                print(f"  [{market}]")
                current_market = market
            print(f"    {industry_name or '(無)'}: {count}")

        cur.execute("SELECT COUNT(*) FROM stocks WHERE industry_code IS NULL")
        print(f"\n缺 industry_code 檔數: {cur.fetchone()[0]}")

        cur.execute("SELECT COUNT(*) FROM stock_groups")
        print(f"stock_groups 列數（預期為 0，本次任務不填資料）: {cur.fetchone()[0]}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新台股上市+上櫃股票資料庫")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    print(f"開始建置資料庫: {args.db_path}")
    rows = collect_all()
    write_db(rows, args.db_path)
    print(f"寫入完成，共 {len(rows)} 檔股票。")
    print_summary(args.db_path)


if __name__ == "__main__":
    main()
