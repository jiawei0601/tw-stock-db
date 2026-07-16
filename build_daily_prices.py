"""建置/刷新 daily_prices：`institutional_flow_daily` 實際出現過的全部股票、
`institutional_flow_daily` 完整涵蓋的交易日範圍（MIN~MAX date）的歷史收盤價（近似值），
供「股數 x 收盤價 ≈ 新台幣金額」換算三大法人金額流向使用。

**範圍刻意比 `institutional_flow_daily`/`stocks` 全市場更窄**：只抓「三大法人資料實際
出現過」的股票（`SELECT DISTINCT stock_id FROM institutional_flow_daily`），而不是
`stocks` 全部 1971 檔——這是「金額流向換算」這個特定用途需要的精確範圍，跟三大法人
backfill 本身的全市場範圍是兩件事（見 AGENTS.md）。

資料源：TWSE `afterTrading/MI_INDEX`（上市）+ TPEx `www/zh-tw/afterTrading/dailyQuotes`
（上櫃），皆為官方按日期查詢、當日全市場一次回傳的 endpoint（`collectors/prices.py`），
跟 `institutional_official.py` 是同一種「查一天、回全市場」模式，逐日往回抓才能湊歷史。
**TPEx 收盤價第九輪實測已確認能正確支援歷史查詢**（不是先前排查過、永遠只回最新一天的
`tpex_mainboard_daily_close_quotes`／`stk_quote_result.php`，見 docs/data-sources.md
第 21 節），所以 TWSE/TPEx 都能用同一套逐日 backfill 設計，沒有 TPEx 缺口。

可續傳設計（比照 build_institutional_summary.py）：
    - `price_fetch_log` 表（PK `(market, date)`）記錄「這個市場+日期是否已經查過」，
      重跑會跳過已查過的日期，不重複打 API。
    - 目標日期範圍固定為 `institutional_flow_daily` 的 MIN(date)~MAX(date)（不像
      institutional 自己要動態決定「近 3 年」，這裡直接對齊既有範圍即可）；若
      `institutional_flow_daily` 之後因增量刷新而多出新日期，重跑本腳本會自動涵蓋。
    - 每湊滿 COMMIT_BATCH_DAYS 個「已檢查」日曆日就 commit 一次，中途中斷可續傳。
    - 單日請求失敗（`_http.get()` 內建重試 3 次後仍失敗）記錄下來、跳過繼續下一天，
      不讓整個 backfill 因一次暫時性網路問題全部作廢；失敗的日期不會寫入
      `price_fetch_log`，之後重跑會自動再嘗試。

用法：
    python build_daily_prices.py [--db-path PATH]
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from collectors import prices as prices_api
from models import CollectorError

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

COMMIT_BATCH_DAYS = 20  # 每湊滿 N 個已檢查（不論是否為交易日）的日曆日就 commit 一次

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS daily_prices (
    stock_id TEXT NOT NULL,
    date     TEXT NOT NULL,
    close    REAL NOT NULL,
    PRIMARY KEY (stock_id, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(date);

CREATE TABLE IF NOT EXISTS price_fetch_log (
    market     TEXT NOT NULL,   -- 'TWSE' / 'TPEx'
    date       TEXT NOT NULL,
    is_trading_day INTEGER NOT NULL,  -- 1=當天有資料（交易日），0=查過但無資料（非交易日）
    checked_at TEXT NOT NULL,
    PRIMARY KEY (market, date)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _target_stock_ids(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """回傳 [(stock_id, market), ...]，範圍是 institutional_flow_daily 實際出現過的股票
    （不是 stocks 全市場，見檔案頂端說明）。"""
    cur = conn.execute(
        "SELECT DISTINCT f.stock_id, s.market FROM institutional_flow_daily f "
        "JOIN stocks s ON s.stock_id = f.stock_id ORDER BY f.stock_id"
    )
    return cur.fetchall()


def _institutional_date_range(conn: sqlite3.Connection) -> tuple[str, str]:
    row = conn.execute("SELECT MIN(date), MAX(date) FROM institutional_flow_daily").fetchone()
    if row is None or row[0] is None:
        raise SystemExit("institutional_flow_daily 是空的，請先跑: python build_institutional_summary.py")
    return row[0], row[1]


def _logged_dates(conn: sqlite3.Connection, market: str) -> set[str]:
    cur = conn.execute("SELECT date FROM price_fetch_log WHERE market = ?", (market,))
    return {r[0] for r in cur.fetchall()}


def _log_day(conn: sqlite3.Connection, market: str, iso_date: str, is_trading_day: bool) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO price_fetch_log (market, date, is_trading_day, checked_at) "
        "VALUES (?, ?, ?, ?)",
        (market, iso_date, int(is_trading_day), _now_iso()),
    )


def backfill_market(
    conn: sqlite3.Connection, fetch_fn, target_ids: set[str], market: str,
    start_date: date, end_date: date,
) -> tuple[int, int, list[str]]:
    """對單一市場、固定日期區間 [start_date, end_date] 做增量式 backfill（已查過的日期
    直接跳過，不重複打 API）。回傳 (本次新查到的交易日數, 本次新檢查的日曆日數, 失敗清單)。
    """
    already_logged = _logged_dates(conn, market)
    failed: list[str] = []
    new_trading_days = 0
    new_checked_days = 0
    pending_since_commit = 0
    started = time.monotonic()

    total_calendar_days = (end_date - start_date).days + 1
    d = start_date
    while d <= end_date:
        iso_date = d.isoformat()
        if iso_date in already_logged:
            d += timedelta(days=1)
            continue

        try:
            rows = fetch_fn(iso_date)
        except CollectorError as e:
            failed.append(f"{market} {iso_date}（{e}）")
            d += timedelta(days=1)
            continue

        matched = [r for r in rows if r["stock_id"] in target_ids]
        is_trading_day = len(rows) > 0
        _log_day(conn, market, iso_date, is_trading_day)
        if matched:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_prices (stock_id, date, close) VALUES (?, ?, ?)",
                [(r["stock_id"], r["date"], r["close"]) for r in matched],
            )
        new_checked_days += 1
        pending_since_commit += 1
        if is_trading_day:
            new_trading_days += 1

        if pending_since_commit >= COMMIT_BATCH_DAYS:
            conn.commit()
            pending_since_commit = 0
            elapsed = time.monotonic() - started
            rate = new_checked_days / elapsed if elapsed > 0 else 0
            remaining_days = (end_date - d).days
            eta_min = (remaining_days / rate / 60) if rate > 0 else float("nan")
            print(f"  {market} 已檢查 {new_checked_days} 個日曆日（本次新查，累積交易日 "
                  f"{new_trading_days}，掃到 {iso_date}，已耗時 {elapsed/60:.1f} 分鐘，"
                  f"預估剩餘 {eta_min:.1f} 分鐘）")

        d += timedelta(days=1)

    conn.commit()
    if new_checked_days:
        print(f"  {market} 本次新檢查 {new_checked_days}/{total_calendar_days} 個日曆日"
              f"（{start_date.isoformat()} ~ {end_date.isoformat()}），"
              f"其中 {new_trading_days} 個為交易日")
    else:
        print(f"  {market} 範圍內 {total_calendar_days} 個日曆日皆已查過，無需新請求")
    return new_trading_days, new_checked_days, failed


def build(db_path: Path) -> None:
    if not db_path.exists():
        raise SystemExit(f"{db_path} 不存在，請先跑: python build_db.py")

    started = time.monotonic()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        min_date, max_date = _institutional_date_range(conn)
        start_date = date.fromisoformat(min_date)
        end_date = date.fromisoformat(max_date)

        targets = _target_stock_ids(conn)
        if not targets:
            raise SystemExit("institutional_flow_daily 查無任何股票，先確認 build_institutional_summary.py 已執行")

        twse_ids = {sid for sid, market in targets if market == "TWSE"}
        tpex_ids = {sid for sid, market in targets if market == "TPEx"}
        unknown_market = [f"{sid}（未知市場別 {market}）" for sid, market in targets if market not in ("TWSE", "TPEx")]

        print(f"目標股票：{len(targets)} 檔（TWSE {len(twse_ids)} / TPEx {len(tpex_ids)}），"
              f"日期範圍 {start_date.isoformat()} ~ {end_date.isoformat()}"
              f"（對齊 institutional_flow_daily 的完整涵蓋範圍）")

        print("\n=== TWSE 個股收盤價（MI_INDEX）===")
        twse_new_trading, twse_new_checked, twse_failed = backfill_market(
            conn, prices_api.fetch_twse_close, twse_ids, "TWSE", start_date, end_date)

        print("\n=== TPEx 個股收盤價（www/zh-tw/afterTrading/dailyQuotes）===")
        tpex_new_trading, tpex_new_checked, tpex_failed = backfill_market(
            conn, prices_api.fetch_tpex_close, tpex_ids, "TPEx", start_date, end_date)

        failed = twse_failed + tpex_failed + unknown_market

        total_rows = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        date_range = conn.execute("SELECT MIN(date), MAX(date) FROM daily_prices").fetchone()

        # 涵蓋率：以 institutional_flow_daily 的 (stock_id, date) 組合數當分母，
        # daily_prices 實際涵蓋的 (stock_id, date) 組合數當分子（不是「股票數」涵蓋率，
        # 因為同一檔股票可能某些日子有價、某些日子沒有，逐筆比對才誠實）。
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM institutional_flow_daily f
            LEFT JOIN daily_prices p ON p.stock_id = f.stock_id AND p.date = f.date
            WHERE p.close IS NULL
            """
        )
        missing_pairs = cur.fetchone()[0]
        total_pairs = conn.execute("SELECT COUNT(*) FROM institutional_flow_daily").fetchone()[0]
        coverage_pct = 100.0 * (total_pairs - missing_pairs) / total_pairs if total_pairs else 0.0

        elapsed_total = time.monotonic() - started
        print(f"\ndaily_prices：{total_rows} 列，涵蓋 {date_range[0]} ~ {date_range[1]}")
        print(f"對齊 institutional_flow_daily 的 (stock_id, date) 逐筆涵蓋率："
              f"{total_pairs - missing_pairs}/{total_pairs}（{coverage_pct:.2f}%），"
              f"缺收盤價 {missing_pairs} 筆（該筆金額流向換算時會被排除，不臆測價格）")
        if failed:
            print(f"\n本次執行有 {len(failed)} 個「市場+日期」抓取失敗（重試 3 次仍失敗，已跳過，"
                  f"之後重跑本腳本會自動補抓）：")
            for f in failed[:50]:
                print(f"  - {f}")
            if len(failed) > 50:
                print(f"  ...（其餘 {len(failed) - 50} 筆略）")
        print(f"\n總耗時 {elapsed_total/60:.1f} 分鐘，寫入完成: {db_path}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新 institutional_flow_daily 涵蓋範圍的個股歷史收盤價（可續傳）")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    build(args.db_path)


if __name__ == "__main__":
    main()
