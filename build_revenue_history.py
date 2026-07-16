"""建置/刷新全市場股票的月營收「近 3 年歷史」時序表。

取代 build_fundamentals.py 原本只抓「最新一期」的 monthly_revenue 快照做法（第三輪），
改用 MOPS 歷史封存頁面（`collectors/revenue_history.py`）逐月逐市場抓取近 36 個月。
**【第七輪】篩選範圍從 `stock_groups`（91 檔概念股）擴大為 `stocks` 全部（目前 1971
檔，動態查詢不寫死），MOPS 封存頁本身就是全市場下載，不需要新增任何請求。**

**Schema 異動（破壞性）**：`monthly_revenue` PK 從 `stock_id`（單列快照）改為
`(stock_id, ym)`（時序表）。首次執行偵測到舊版 schema 會自動 DROP 重建（backfill 本來
就會把最新一期含在 36 個月範圍內，不會遺失資訊），之後重跑不會再次觸發（新版 schema
已經是 (stock_id, ym) PK，`CREATE TABLE IF NOT EXISTS` 不會動到既有資料）。

**可續傳設計**：另建 `revenue_fetch_log` 表（(source_market, ym) 為 PK）記錄「這個
市場+年月的頁面是否已經抓過」。已經抓過的年月（且不是「最近 2 個月」）重跑時直接跳過
不再打網路請求；「最近 2 個月」（當月 + 上月）每次執行都強制重新抓取，因為月營收公告
本身有時間差（月底後約 10 日內才公告），舊資料可能在下次執行時已經補齊或修正。
單一年月頁面抓取失敗（重試 3 次仍失敗）記錄下來、跳過繼續下一個年月，不讓整個 36 個月
backfill 因為一次暫時性網路問題全部作廢；每抓完一個年月頁面就立即寫入 DB（不是等全部
72 次請求都成功才一次寫入）。

用法：
    python build_revenue_history.py [--db-path PATH] [--months N]
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

from collectors import revenue_history
from models import CollectorError

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_MONTHS = 36          # 近 3 年
ALWAYS_REFRESH_MONTHS = 2    # 最近 N 個候選年月每次都強制重抓（見模組說明）

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monthly_revenue (
    stock_id                     TEXT NOT NULL,
    ym                           TEXT NOT NULL,   -- 營收所屬年月 YYYY-MM
    company_name                 TEXT,
    announce_date                TEXT,      -- 頁面級出表日期 YYYY-MM-DD（近似值，非逐公司公告日，見 collectors/revenue_history.py）
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
    updated_at                   TEXT NOT NULL,
    PRIMARY KEY (stock_id, ym)
);

CREATE INDEX IF NOT EXISTS idx_monthly_revenue_ym ON monthly_revenue(ym);

CREATE TABLE IF NOT EXISTS revenue_fetch_log (
    source_market TEXT NOT NULL,
    ym            TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    row_count     INTEGER NOT NULL,  -- 該頁過濾出的目標股票筆數（可能為 0，例如尚未公告）
    PRIMARY KEY (source_market, ym)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='monthly_revenue'")
    row = cur.fetchone()
    if row and "stock_id, ym" not in row[0].replace("\n", " ").replace("  ", " "):
        print("偵測到 monthly_revenue 為舊版單列快照 schema（PK=stock_id），"
              "改為時序表（PK=(stock_id, ym)），DROP 重建"
              "（backfill 會在 36 個月範圍內重新涵蓋最新一期，不遺失資訊）")
        conn.execute("DROP TABLE monthly_revenue")
        conn.commit()
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _target_stock_ids(conn: sqlite3.Connection) -> list[str]:
    """【第七輪】改為全市場 stocks 表（不再限定 stock_groups 概念股名單）。"""
    cur = conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id")
    return [r[0] for r in cur.fetchall()]


def _needs_full_rebuild(conn: sqlite3.Connection, target_stock_count: int) -> bool:
    """偵測 monthly_revenue 是否還停留在舊版『只涵蓋 stock_groups 91 檔』的篩選範圍
    （第七輪擴大為全市場前的殘留資料）。`revenue_fetch_log` 只記錄「這個市場+年月的
    頁面是否已經抓過」，不記錄「當時用的是哪個篩選範圍」，若直接沿用舊 log 重跑，
    「最近 2 個月強制重抓」以外的舊月份會被誤判成「已抓過」而跳過，導致舊月份繼續
    停留在 91 檔範圍、只有最新月份是全市場範圍（本專案第七輪實測時真的踩到這個問題：
    重跑一次後 34/35 個月仍是 87~88 檔，只有強制重抓的當月是 1839 檔）。用『扣掉最近
    ALWAYS_REFRESH_MONTHS 個月，其餘月份的平均相異股票數』遠低於目標股票數（抓 50%
    當保守門檻）來判斷，若判定為舊範圍資料，清空 monthly_revenue／revenue_fetch_log
    後整個重新 backfill。"""
    cur = conn.execute(
        "SELECT COUNT(DISTINCT stock_id) FROM monthly_revenue "
        "WHERE ym NOT IN (SELECT ym FROM monthly_revenue GROUP BY ym ORDER BY ym DESC LIMIT ?)",
        (ALWAYS_REFRESH_MONTHS,),
    )
    row = cur.fetchone()
    existing_stock_count = row[0] if row and row[0] is not None else 0
    if existing_stock_count == 0:
        return False  # 沒有「非最近幾個月」的舊資料，走正常 backfill 即可
    return existing_stock_count < target_stock_count * 0.5


def target_year_months(reference: date, n_months: int) -> list[tuple[int, int]]:
    """回傳 [(year, month), ...] 由近到遠，共 n_months 個候選年月（含當月，當月通常尚未
    公告，會抓到空結果，屬預期行為）。"""
    result = []
    y, m = reference.year, reference.month
    for _ in range(n_months):
        result.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return result


def _already_fetched(conn: sqlite3.Connection, market: str, ym: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM revenue_fetch_log WHERE source_market = ? AND ym = ?", (market, ym)
    )
    return cur.fetchone() is not None


def _write_month(conn: sqlite3.Connection, rows: list[dict], market: str, ym: str) -> None:
    now = _now_iso()
    with conn:
        if rows:
            conn.executemany(
                """
                INSERT OR REPLACE INTO monthly_revenue
                    (stock_id, ym, company_name, announce_date, revenue, revenue_prev_month,
                     revenue_last_year_month, mom_pct, yoy_pct, revenue_cumulative,
                     revenue_cumulative_last_year, cumulative_yoy_pct, remark, source_market, updated_at)
                VALUES (:stock_id, :ym, :company_name, :announce_date, :revenue, :revenue_prev_month,
                        :revenue_last_year_month, :mom_pct, :yoy_pct, :revenue_cumulative,
                        :revenue_cumulative_last_year, :cumulative_yoy_pct, :remark, :source_market, :updated_at)
                """,
                [{**r, "updated_at": now} for r in rows],
            )
        conn.execute(
            "INSERT OR REPLACE INTO revenue_fetch_log (source_market, ym, fetched_at, row_count) "
            "VALUES (?, ?, ?, ?)",
            (market, ym, now, len(rows)),
        )


def build(db_path: Path, n_months: int = DEFAULT_MONTHS) -> None:
    if not db_path.exists():
        raise SystemExit(f"{db_path} 不存在，請先跑: python build_db.py")

    conn = sqlite3.connect(db_path)
    started = time.monotonic()
    try:
        _ensure_schema(conn)
        target_ids = set(_target_stock_ids(conn))
        if not target_ids:
            raise SystemExit("stocks 表目前沒有任何股票，先確認 build_db.py 已執行")

        if _needs_full_rebuild(conn, len(target_ids)):
            print(f"[migration] 偵測到 monthly_revenue 舊月份仍是窄範圍資料"
                  f"（涵蓋股票數遠低於本次目標 {len(target_ids)} 檔），判定為第七輪擴大範圍前的殘留，"
                  f"清空 monthly_revenue／revenue_fetch_log 後全量重新 backfill（不保留舊的「只有91檔」資料）")
            conn.execute("DELETE FROM monthly_revenue")
            conn.execute("DELETE FROM revenue_fetch_log")
            conn.commit()

        today = date.today()
        candidates = target_year_months(today, n_months)
        always_refresh = set(candidates[:ALWAYS_REFRESH_MONTHS])

        print(f"目標股票（來自 stocks 全市場）：{len(target_ids)} 檔")
        print(f"目標年月：{len(candidates)} 個（{candidates[-1][0]}-{candidates[-1][1]:02d} ~ "
              f"{candidates[0][0]}-{candidates[0][1]:02d}），最近 {ALWAYS_REFRESH_MONTHS} 個月每次強制重抓")

        total_pages = len(candidates) * 2
        done_pages = 0
        skipped_pages = 0
        failed: list[str] = []
        fetched_month_count = 0

        for market in ("TWSE", "TPEx"):
            for year, month in candidates:
                ym = f"{year:04d}-{month:02d}"
                force = (year, month) in always_refresh
                if not force and _already_fetched(conn, market, ym):
                    skipped_pages += 1
                    continue
                try:
                    rows = revenue_history.fetch_month(market, year, month)
                except CollectorError as e:
                    failed.append(f"{market} {ym}（{e}）")
                    done_pages += 1
                    elapsed = time.monotonic() - started
                    print(f"  [失敗，跳過] {market} {ym}：{e}")
                    continue
                target_rows = [r for r in rows if r["stock_id"] in target_ids]
                _write_month(conn, target_rows, market, ym)
                fetched_month_count += 1
                done_pages += 1
                elapsed = time.monotonic() - started
                remaining = total_pages - done_pages - skipped_pages
                eta_sec = (elapsed / done_pages) * remaining if done_pages else 0
                print(f"  {market} {ym}：{len(target_rows)}/{len(rows)} 檔命中目標清單 "
                      f"（頁面進度 {done_pages+skipped_pages}/{total_pages}，"
                      f"約剩 {eta_sec/60:.1f} 分鐘）")

        elapsed_total = time.monotonic() - started
        print(f"\n頁面請求：新抓 {fetched_month_count}、略過已抓過 {skipped_pages}、"
              f"失敗 {len(failed)}（共 {total_pages} 頁），耗時 {elapsed_total/60:.1f} 分鐘")
        if failed:
            print("失敗年月（可之後重跑本腳本自動補，或個別排查原因）：")
            for f in failed:
                print(f"  - {f}")

        cur = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_id), COUNT(DISTINCT ym) FROM monthly_revenue")
        total_rows, distinct_stocks, distinct_yms = cur.fetchone()
        print(f"\nmonthly_revenue 現況：{total_rows} 列，{distinct_stocks} 檔股票，{distinct_yms} 個不同年月")
        print(f"寫入完成: {db_path}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新 stock_groups 名單股票的月營收近 3 年歷史（MOPS 封存頁面）")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS, help="回補月數（預設 36，約 3 年）")
    args = parser.parse_args()
    build(args.db_path, args.months)


if __name__ == "__main__":
    main()
