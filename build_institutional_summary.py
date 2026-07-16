"""建置/刷新 `stock_groups` 名單股票的三大法人近期買賣超動態快照。

資料源：TWSE `fund/T86`（上市）+ TPEx `3itrade_hedge_result.php`（上櫃），官方按日期查詢
endpoint，逐日往回抓，直到湊滿 60 個實際交易日（`collectors/institutional_official.py`）。
**不再讀取 `tw_cache/institutional.db`**（該共用資料源只涵蓋 tw-momentum-scanner 篩選過的
696 檔動能股歷史清單，不是全市場，會讓 91 檔中固定有一批股票查無資料；改用官方 API 直抓
可涵蓋全部 91 檔，且資料新鮮度一致，見 HANDOFF.md 決策紀錄）。

兩個 endpoint 都是「查一天、回全市場」，不是逐檔查詢，所以 TWSE/TPEx 各自只需要約 60~90
次請求（60 個交易日 + 假日跳過的額外嘗試），本地過濾出 91 檔目標股票即可，不對整個股票
宇宙分檔打 API。務必用 `collectors/_http.py` 既有的節流機制（同 bucket 至少間隔
`MIN_INTERVAL_SEC` 秒 + 403/429/5xx 自動退避重試）。

本腳本輸出兩張表，schema 與第三輪（讀 institutional.db 版本）完全相同，皆為**快照覆蓋**
（每次執行整批 DELETE + INSERT，不逐筆 upsert）：
    - institutional_flow_summary：每檔股票一列，近 5/20/60 日三大法人累計買賣超 +
      外資連續買/賣超天數（streak）
    - institutional_flow_daily：近 60 個交易日逐日明細（供未來視覺化畫走勢圖用）

用法：
    python build_institutional_summary.py [--db-path PATH] [--start-date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from collectors import institutional_official as inst_api

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

DETAIL_WINDOW_DAYS = 60      # institutional_flow_daily / 目標交易日數（不無限累積），也是抓取目標交易日數
MAX_CALENDAR_DAYS = 150      # 往回查詢的日曆日安全上限（60 交易日 + 假日緩衝，避免無窮迴圈）

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS institutional_flow_summary (
    stock_id                  TEXT PRIMARY KEY,
    latest_date                TEXT,     -- 該股最新一筆資料日期
    foreign_net_5d             INTEGER,  -- 近 5 個交易日外資買賣超累計（股數，正=買超）
    foreign_net_20d            INTEGER,
    foreign_net_60d            INTEGER,
    trust_net_5d                INTEGER,
    trust_net_20d               INTEGER,
    trust_net_60d                INTEGER,
    dealer_net_5d               INTEGER,
    dealer_net_20d              INTEGER,
    dealer_net_60d               INTEGER,
    trading_days_covered       INTEGER,  -- 實際抓到的近期交易日數（<=60，反映資料涵蓋度，新股/資料缺口會 <60）
    foreign_streak_days        INTEGER,  -- 外資連續買/賣超天數（正=連續買超、負=連續賣超、0=無資料或最新一日打平）
    foreign_streak_truncated   INTEGER,  -- 1 表示 streak 撞到 lookback 視窗上限，真實連續天數可能更長（下界值）
    updated_at                 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS institutional_flow_daily (
    stock_id    TEXT NOT NULL,
    date        TEXT NOT NULL,
    foreign_net INTEGER,
    trust_net   INTEGER,
    dealer_net  INTEGER,
    PRIMARY KEY (stock_id, date)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _target_stocks(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """回傳 [(stock_id, market), ...]，動態取自 stock_groups x stocks（不寫死清單）。"""
    cur = conn.execute(
        "SELECT DISTINCT s.stock_id, s.market FROM stock_groups g "
        "JOIN stocks s ON g.stock_id = s.stock_id ORDER BY s.stock_id"
    )
    return cur.fetchall()


def collect_market_history(
    fetch_fn, target_ids: set[str], market_label: str,
    start_date: date, target_trading_days: int = DETAIL_WINDOW_DAYS,
    max_calendar_days: int = MAX_CALENDAR_DAYS,
) -> dict[str, list[tuple[str, int | None, int | None, int | None]]]:
    """逐日往回查詢官方 endpoint，直到湊滿 target_trading_days 個有效交易日或撞到
    max_calendar_days 日曆日上限。空結果（非交易日）跳過，不視為錯誤。

    回傳 {stock_id: [(date, foreign_net, trust_net, dealer_net), ...]}，每檔股票的
    list 依日期新到舊排序（因為迴圈本身就是從新到舊查詢）。只收錄 target_ids 內的股票，
    本地過濾，不逐檔打 API。
    """
    collected: dict[str, list[tuple]] = {sid: [] for sid in target_ids}
    trading_days = 0
    d = start_date
    checked = 0
    while trading_days < target_trading_days and checked < max_calendar_days:
        iso_date = d.isoformat()
        rows = fetch_fn(iso_date)
        checked += 1
        if rows:
            trading_days += 1
            print(f"  {market_label} 已抓 {trading_days}/{target_trading_days} 個交易日（{iso_date}）")
            for r in rows:
                sid = r["stock_id"]
                if sid in collected:
                    collected[sid].append((r["date"], r["foreign_net"], r["trust_net"], r["dealer_net"]))
        d -= timedelta(days=1)
    if trading_days < target_trading_days:
        print(f"  {market_label} 警告：撞到 {max_calendar_days} 日曆日上限，只湊到 {trading_days} 個交易日")
    return collected


def _sum_net(rows: list[tuple], idx: int, n: int) -> int | None:
    window = rows[:n]
    if not window:
        return None
    return sum(r[idx] for r in window if r[idx] is not None)


def _foreign_streak(rows: list[tuple]) -> tuple[int, bool]:
    """回傳 (streak_days, truncated)。正值=連續買超天數、負值=連續賣超天數、0=無資料或最新一日打平。"""
    if not rows:
        return 0, False
    first_net = rows[0][1]
    if first_net is None or first_net == 0:
        return 0, False
    direction = 1 if first_net > 0 else -1
    streak = 0
    for row in rows:
        net = row[1]
        sign = 1 if (net or 0) > 0 else -1 if (net or 0) < 0 else 0
        if net is None or sign != direction:
            break
        streak += 1
    truncated = streak == len(rows)
    return streak * direction, truncated


def build_summary_and_daily_rows(
    history: dict[str, list[tuple]],
) -> tuple[list[dict], list[dict], list[str]]:
    now = _now_iso()
    summary_rows: list[dict] = []
    daily_rows: list[dict] = []
    missing: list[str] = []

    for stock_id in sorted(history):
        rows = history[stock_id]
        if not rows:
            missing.append(f"{stock_id}（官方 API 近 {MAX_CALENDAR_DAYS} 個日曆日內查無此代號的三大法人資料）")
            continue

        streak_days, truncated = _foreign_streak(rows)
        summary_rows.append({
            "stock_id": stock_id,
            "latest_date": rows[0][0],
            "foreign_net_5d": _sum_net(rows, 1, 5),
            "foreign_net_20d": _sum_net(rows, 1, 20),
            "foreign_net_60d": _sum_net(rows, 1, 60),
            "trust_net_5d": _sum_net(rows, 2, 5),
            "trust_net_20d": _sum_net(rows, 2, 20),
            "trust_net_60d": _sum_net(rows, 2, 60),
            "dealer_net_5d": _sum_net(rows, 3, 5),
            "dealer_net_20d": _sum_net(rows, 3, 20),
            "dealer_net_60d": _sum_net(rows, 3, 60),
            "trading_days_covered": min(len(rows), DETAIL_WINDOW_DAYS),
            "foreign_streak_days": streak_days,
            "foreign_streak_truncated": int(truncated),
            "updated_at": now,
        })
        for date_, foreign_net, trust_net, dealer_net in rows[:DETAIL_WINDOW_DAYS]:
            daily_rows.append({
                "stock_id": stock_id, "date": date_,
                "foreign_net": foreign_net, "trust_net": trust_net, "dealer_net": dealer_net,
            })
    return summary_rows, daily_rows, missing


def write_db(summary_rows: list[dict], daily_rows: list[dict], db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        with conn:
            conn.execute("DELETE FROM institutional_flow_summary")
            conn.execute("DELETE FROM institutional_flow_daily")
            conn.executemany(
                """
                INSERT INTO institutional_flow_summary
                    (stock_id, latest_date, foreign_net_5d, foreign_net_20d, foreign_net_60d,
                     trust_net_5d, trust_net_20d, trust_net_60d,
                     dealer_net_5d, dealer_net_20d, dealer_net_60d,
                     trading_days_covered, foreign_streak_days, foreign_streak_truncated, updated_at)
                VALUES (:stock_id, :latest_date, :foreign_net_5d, :foreign_net_20d, :foreign_net_60d,
                        :trust_net_5d, :trust_net_20d, :trust_net_60d,
                        :dealer_net_5d, :dealer_net_20d, :dealer_net_60d,
                        :trading_days_covered, :foreign_streak_days, :foreign_streak_truncated, :updated_at)
                """,
                summary_rows,
            )
            if daily_rows:
                conn.executemany(
                    """
                    INSERT INTO institutional_flow_daily (stock_id, date, foreign_net, trust_net, dealer_net)
                    VALUES (:stock_id, :date, :foreign_net, :trust_net, :dealer_net)
                    """,
                    daily_rows,
                )
    finally:
        conn.close()


def build(db_path: Path, start_date: date | None = None) -> None:
    if not db_path.exists():
        raise SystemExit(f"{db_path} 不存在，請先跑: python build_db.py")

    conn = sqlite3.connect(db_path)
    try:
        targets = _target_stocks(conn)
    finally:
        conn.close()
    if not targets:
        raise SystemExit("stock_groups 目前沒有任何股票，先確認 build_db.py 已建置族群資料")

    twse_ids = {sid for sid, market in targets if market == "TWSE"}
    tpex_ids = {sid for sid, market in targets if market == "TPEx"}
    unknown_market = [f"{sid}（未知市場別 {market}）" for sid, market in targets if market not in ("TWSE", "TPEx")]

    start = start_date or date.today()
    print(f"目標股票：{len(targets)} 檔（TWSE {len(twse_ids)} / TPEx {len(tpex_ids)}），查詢起點 {start.isoformat()}")

    print("\n=== 抓取 TWSE 三大法人買賣超（fund/T86，逐日往回）===")
    twse_history = collect_market_history(inst_api.fetch_twse_t86, twse_ids, "TWSE", start)

    print("\n=== 抓取 TPEx 三大法人買賣超（3itrade_hedge_result.php，逐日往回）===")
    tpex_history = collect_market_history(inst_api.fetch_tpex_hedge, tpex_ids, "TPEx", start)

    history: dict[str, list[tuple]] = {**twse_history, **tpex_history}
    summary_rows, daily_rows, missing = build_summary_and_daily_rows(history)
    missing.extend(unknown_market)

    write_db(summary_rows, daily_rows, db_path)

    print(f"\n目標股票 {len(targets)} 檔，涵蓋 {len(summary_rows)} 檔，缺資料 {len(missing)} 檔")
    if missing:
        print("缺資料清單：")
        for m in sorted(missing):
            print(f"  - {m}")
    print(f"寫入完成: {db_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新 stock_groups 名單的三大法人近期買賣超動態快照（官方 API 直抓）")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--start-date", type=str, default=None,
        help="往回查詢的起始日期 YYYY-MM-DD（預設今天，主要供測試/回補特定區間使用）",
    )
    args = parser.parse_args()
    start_date = date.fromisoformat(args.start_date) if args.start_date else None
    build(args.db_path, start_date)


if __name__ == "__main__":
    main()
