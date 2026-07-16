"""建置/刷新全市場股票的三大法人動態：近 3 年逐日歷史 + 近 5/20/60 日彙總。

資料源：TWSE `fund/T86`（上市）+ TPEx `3itrade_hedge_result.php`（上櫃），官方按日期查詢
endpoint（`collectors/institutional_official.py`），皆為「查一天、回全市場」，逐日往回抓
才能湊歷史。

**【第七輪】篩選範圍從 `stock_groups`（91 檔概念股）擴大為 `stocks` 全部（目前 1971
檔）**：API 請求次數不變（endpoint 本來就回全市場），但先前寫入 DB 前就先篩選成 91 檔，
原始回應中非 91 檔的部分沒有被保留，因此擴大範圍後無法用「這個日期已經抓過」跳過——
必須區分「這個日期已經抓過『91 檔篩選版』」跟「這個日期已經抓過『全市場版』」是兩件
不同的事。做法：`build()` 一開始偵測 `institutional_flow_daily` 現有資料涵蓋的相異股票數
是否遠低於本次目標股票數，若是（判定為舊版窄範圍殘留），先清空
`institutional_fetch_log`／`institutional_flow_daily`／`institutional_flow_summary`
三張表再整個重新 backfill（不保留舊的「只有91檔」殘留資料，見 HANDOFF.md 第七輪決策）。

**【第五輪】從「每次執行都整批重抓近 60 個交易日」改為「累積近 3 年（約 750 個交易日）
的本地歷史 + 增量刷新」**：
    - `institutional_flow_daily`（PK 不變 `(stock_id, date)`）現在是**累積式**時序表：
      backfill 目標約 750 個交易日（近 3 年），首次執行會逐日往回抓到 750 個交易日或
      撞到 3 年+緩衝的日曆日下限；之後重跑只需要抓「比本地資料庫目前最新日期更新」的
      新增交易日（增量），不會每次都整批重抓 750 天。
    - `institutional_flow_summary`（PK 不變 `stock_id`，近 5/20/60 日彙總 + streak）
      計算邏輯不變，但資料來源改成**讀本地 `institutional_flow_daily`**（該表現在已有
      近 3 年資料，绝对夠算 60 日彙總），不再對外多發送請求重複抓最近期資料。
    - 用 `institutional_fetch_log` 表（PK `(market, date)`）記錄「這個市場+日期是否已經
      查詢過」（含非交易日的空結果），讓增量判斷精確、不必用 `institutional_flow_daily`
      裡『有沒有資料列』去猜測『這天有沒有查過』（兩者語意不同：查過但非交易日 vs 根本
      沒查過）。

**可續傳設計**：
    - 逐日抓取的迴圈分成「往前補新交易日（forward）」與「往回補歷史交易日（backward）」
      兩段，各自以 `institutional_fetch_log` 目前記錄的最新/最舊日期為起點接著抓，
      不必每次從頭開始。
    - 每抓完約 20 個交易日就 commit 一次（不是等全部 ~750×2 次請求都成功才寫入），
      中途中斷重跑可以從資料庫現有進度接續。
    - 單一交易日請求失敗（`_http.get()` 內建重試 3 次後仍失敗）記錄下來、跳過繼續下一天，
      不讓整個 3 年 backfill 因為一次暫時性網路問題全部作廢；失敗的日期不會寫入
      `institutional_fetch_log`，之後重跑本腳本會自動再嘗試（該日期落在 forward/backward
      掃描範圍內的話）。

用法：
    python build_institutional_summary.py [--db-path PATH] [--target-trading-days N]
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from collectors import institutional_official as inst_api
from models import CollectorError

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"

SUMMARY_WINDOW_DAYS = 60          # institutional_flow_summary 近 5/20/60 日彙總的視窗上限（不變）
TARGET_TRADING_DAYS = 750         # 近 3 年 backfill 目標交易日數（約 250 交易日/年 x 3）
CALENDAR_FLOOR_DAYS = 3 * 366 + 60  # 往回查詢的日曆日絕對下限（3 年 + 緩衝，避免無窮迴圈）
COMMIT_BATCH_TRADING_DAYS = 20    # 每湊滿 N 個交易日就 commit 一次

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
    dealer_net_20d               INTEGER,
    dealer_net_60d               INTEGER,
    trading_days_covered       INTEGER,  -- 近期彙總實際涵蓋的交易日數（<=60，反映資料涵蓋度）
    foreign_streak_days        INTEGER,  -- 外資連續買/賣超天數（正=連續買超、負=連續賣超、0=無資料或最新一日打平）
    foreign_streak_truncated   INTEGER,  -- 1 表示 streak 撞到 60 日彙總視窗上限，真實連續天數可能更長（下界值）
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

CREATE INDEX IF NOT EXISTS idx_institutional_flow_daily_stock_date
    ON institutional_flow_daily(stock_id, date DESC);

CREATE TABLE IF NOT EXISTS institutional_fetch_log (
    market     TEXT NOT NULL,   -- 'TWSE' / 'TPEx'
    date       TEXT NOT NULL,
    is_trading_day INTEGER NOT NULL,  -- 1=當天有資料（交易日），0=查過但無資料（非交易日）
    checked_at TEXT NOT NULL,
    PRIMARY KEY (market, date)
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _target_stocks(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """回傳 [(stock_id, market), ...]。【第七輪】動態取自 stocks 全市場
    （不再限定 stock_groups 概念股名單）。"""
    cur = conn.execute("SELECT stock_id, market FROM stocks ORDER BY stock_id")
    return cur.fetchall()


def _needs_full_rebuild(conn: sqlite3.Connection, target_stock_count: int) -> bool:
    """偵測 institutional_flow_daily 是否還停留在舊版『只涵蓋 stock_groups 91 檔』的
    篩選範圍（第七輪擴大為全市場前的殘留資料）。用『目前資料庫內已涵蓋的相異股票數』
    遠低於本次目標股票數（抓 50% 當保守門檻，避免把「剛好抓到一半」的正常增量續傳
    誤判成舊範圍殘留）來判斷。若判定為舊範圍資料，呼叫端會清空
    institutional_fetch_log／institutional_flow_daily／institutional_flow_summary
    三張表後整個重新 backfill（不保留舊的「只有91檔」殘留資料）。"""
    cur = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM institutional_flow_daily")
    existing_stock_count = cur.fetchone()[0]
    if existing_stock_count == 0:
        return False  # 空表，走正常 backfill 即可，不需特別處理
    return existing_stock_count < target_stock_count * 0.5


def _fetch_log_bounds(conn: sqlite3.Connection, market: str) -> tuple[str | None, str | None]:
    cur = conn.execute(
        "SELECT MIN(date), MAX(date) FROM institutional_fetch_log WHERE market = ?", (market,)
    )
    return cur.fetchone()


def _trading_days_logged(conn: sqlite3.Connection, market: str) -> int:
    cur = conn.execute(
        "SELECT COUNT(*) FROM institutional_fetch_log WHERE market = ? AND is_trading_day = 1",
        (market,),
    )
    return cur.fetchone()[0]


def _log_day(conn: sqlite3.Connection, market: str, iso_date: str, is_trading_day: bool) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO institutional_fetch_log (market, date, is_trading_day, checked_at) "
        "VALUES (?, ?, ?, ?)",
        (market, iso_date, int(is_trading_day), _now_iso()),
    )


def _fetch_day(fetch_fn, iso_date: str, target_ids: set[str]) -> tuple[bool, list[dict], str | None]:
    """回傳 (成功與否, 過濾後的目標股票 rows, 失敗訊息)。成功但無資料（非交易日）回 (True, [], None)。"""
    try:
        rows = fetch_fn(iso_date)
    except CollectorError as e:
        return False, [], str(e)
    return True, [r for r in rows if r["stock_id"] in target_ids], None


def backfill_market(
    conn: sqlite3.Connection, fetch_fn, target_ids: set[str], market: str,
    target_trading_days: int, floor_date: date, today: date,
) -> tuple[int, list[str]]:
    """對單一市場做增量 + 回補式 backfill。回傳 (本次新抓到的交易日數, 失敗日期清單)。

    分兩段：
      1) forward：從 fetch_log 目前記錄的最新日期之後一路補到今天（增量刷新新交易日）。
      2) backward：從 fetch_log 目前記錄的最舊日期之前一路往回補，直到 fetch_log 累計
         交易日數達到 target_trading_days，或撞到 floor_date 絕對下限。
    每湊滿 COMMIT_BATCH_TRADING_DAYS 個交易日就 commit 一次；單日失敗記錄下來跳過繼續。
    """
    oldest, newest = _fetch_log_bounds(conn, market)
    failed: list[str] = []
    new_trading_days = 0
    pending_since_commit = 0

    def maybe_commit():
        nonlocal pending_since_commit
        if pending_since_commit >= COMMIT_BATCH_TRADING_DAYS:
            conn.commit()
            pending_since_commit = 0

    def process_day(d: date) -> bool:
        """回傳這天是否為有效交易日（成功查到資料）。"""
        nonlocal new_trading_days, pending_since_commit
        iso_date = d.isoformat()
        ok, rows, err = _fetch_day(fetch_fn, iso_date, target_ids)
        if not ok:
            failed.append(f"{market} {iso_date}（{err}）")
            return False
        is_trading_day = len(rows) > 0
        _log_day(conn, market, iso_date, is_trading_day)
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO institutional_flow_daily "
                "(stock_id, date, foreign_net, trust_net, dealer_net) VALUES (?, ?, ?, ?, ?)",
                [(r["stock_id"], r["date"], r["foreign_net"], r["trust_net"], r["dealer_net"]) for r in rows],
            )
        if is_trading_day:
            new_trading_days += 1
            pending_since_commit += 1
        return is_trading_day

    # 1) forward：補新交易日（增量刷新，日常重跑主要走這段）
    if newest is not None:
        d = date.fromisoformat(newest) + timedelta(days=1)
        forward_days = 0
        while d <= today:
            process_day(d)
            forward_days += 1
            maybe_commit()
            d += timedelta(days=1)
        if forward_days:
            print(f"  {market} forward：檢查了 {forward_days} 個新日曆日（{date.fromisoformat(newest) + timedelta(days=1)} ~ {today}）")

    # 2) backward：往回補歷史，直到湊滿 target_trading_days 或撞到 floor_date
    oldest, newest = _fetch_log_bounds(conn, market)  # forward 段執行完後重新查一次
    trading_days_total = _trading_days_logged(conn, market)
    start_backward = (date.fromisoformat(oldest) - timedelta(days=1)) if oldest is not None else today
    d = start_backward
    checked_backward = 0
    started = time.monotonic()
    while trading_days_total < target_trading_days and d >= floor_date:
        was_trading_day = process_day(d)
        checked_backward += 1
        if was_trading_day:
            trading_days_total += 1
            if trading_days_total % COMMIT_BATCH_TRADING_DAYS == 0:
                elapsed = time.monotonic() - started
                rate = trading_days_total / elapsed if elapsed > 0 else 0
                remaining = target_trading_days - trading_days_total
                eta_min = (remaining / rate / 60) if rate > 0 else float("nan")
                print(f"  {market} 已累積 {trading_days_total}/{target_trading_days} 個交易日"
                      f"（backward 掃到 {d.isoformat()}，本次已耗時 {elapsed/60:.1f} 分鐘，"
                      f"預估剩餘 {eta_min:.1f} 分鐘）")
        maybe_commit()
        d -= timedelta(days=1)
    conn.commit()

    if trading_days_total < target_trading_days and d < floor_date:
        print(f"  {market} 警告：撞到 {floor_date.isoformat()} 日曆日下限，只累積到 {trading_days_total} 個交易日")

    return new_trading_days, failed


def _summary_from_local(conn: sqlite3.Connection, target_ids: list[str]) -> tuple[list[dict], list[str]]:
    """從本地 institutional_flow_daily 讀最近 <=60 筆算 5/20/60 日彙總 + streak，
    不對外發送任何額外請求（第五輪核心變更：summary 改讀本地累積資料）。"""
    now = _now_iso()
    summary_rows: list[dict] = []
    missing: list[str] = []

    for stock_id in target_ids:
        cur = conn.execute(
            "SELECT date, foreign_net, trust_net, dealer_net FROM institutional_flow_daily "
            "WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
            (stock_id, SUMMARY_WINDOW_DAYS),
        )
        rows = cur.fetchall()  # [(date, foreign_net, trust_net, dealer_net), ...] 新到舊
        if not rows:
            missing.append(f"{stock_id}（本地 institutional_flow_daily 查無資料）")
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
            "trading_days_covered": min(len(rows), SUMMARY_WINDOW_DAYS),
            "foreign_streak_days": streak_days,
            "foreign_streak_truncated": int(truncated),
            "updated_at": now,
        })
    return summary_rows, missing


def _sum_net(rows: list[tuple], idx: int, n: int) -> int | None:
    window = rows[:n]
    if not window:
        return None
    return sum(r[idx] for r in window if r[idx] is not None)


def _foreign_streak(rows: list[tuple]) -> tuple[int, bool]:
    """回傳 (streak_days, truncated)。正值=連續買超天數、負值=連續賣超天數、0=無資料或最新一日打平。
    truncated=1 表示 streak 撞到本次彙總視窗（最多 SUMMARY_WINDOW_DAYS 筆）上限，
    真實連續天數可能更長（下界值），語意與第四輪相同，只是資料來源改為本地表。"""
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


def write_summary(conn: sqlite3.Connection, summary_rows: list[dict]) -> None:
    with conn:
        conn.execute("DELETE FROM institutional_flow_summary")
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


def build(db_path: Path, target_trading_days: int = TARGET_TRADING_DAYS) -> None:
    if not db_path.exists():
        raise SystemExit(f"{db_path} 不存在，請先跑: python build_db.py")

    started = time.monotonic()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()

        targets = _target_stocks(conn)
        if not targets:
            raise SystemExit("stocks 表目前沒有任何股票，先確認 build_db.py 已執行")

        if _needs_full_rebuild(conn, len(targets)):
            print(f"[migration] 偵測到 institutional_flow_daily 仍是舊版窄範圍資料"
                  f"（涵蓋股票數遠低於本次目標 {len(targets)} 檔），判定為第七輪擴大範圍前的殘留，"
                  f"清空 institutional_fetch_log／institutional_flow_daily／"
                  f"institutional_flow_summary 後全量重新 backfill（不保留舊的「只有91檔」資料）")
            conn.execute("DELETE FROM institutional_fetch_log")
            conn.execute("DELETE FROM institutional_flow_daily")
            conn.execute("DELETE FROM institutional_flow_summary")
            conn.commit()

        twse_ids = {sid for sid, market in targets if market == "TWSE"}
        tpex_ids = {sid for sid, market in targets if market == "TPEx"}
        unknown_market = [f"{sid}（未知市場別 {market}）" for sid, market in targets if market not in ("TWSE", "TPEx")]

        today = date.today()
        floor_date = today - timedelta(days=CALENDAR_FLOOR_DAYS)
        print(f"目標股票：{len(targets)} 檔（TWSE {len(twse_ids)} / TPEx {len(tpex_ids)}），"
              f"backfill 目標 {target_trading_days} 個交易日（約 3 年），日曆日下限 {floor_date.isoformat()}")

        print("\n=== TWSE 三大法人買賣超（fund/T86，增量 + 回補近 3 年）===")
        twse_new, twse_failed = backfill_market(conn, inst_api.fetch_twse_t86, twse_ids, "TWSE",
                                                  target_trading_days, floor_date, today)

        print("\n=== TPEx 三大法人買賣超（3itrade_hedge_result.php，增量 + 回補近 3 年）===")
        tpex_new, tpex_failed = backfill_market(conn, inst_api.fetch_tpex_hedge, tpex_ids, "TPEx",
                                                  target_trading_days, floor_date, today)

        failed = twse_failed + tpex_failed + unknown_market

        print("\n=== 從本地 institutional_flow_daily 計算 institutional_flow_summary（不額外打網路請求）===")
        target_ids_sorted = sorted(twse_ids | tpex_ids)
        summary_rows, missing_summary = _summary_from_local(conn, target_ids_sorted)
        write_summary(conn, summary_rows)
        conn.commit()

        twse_total = _trading_days_logged(conn, "TWSE")
        tpex_total = _trading_days_logged(conn, "TPEx")
        cur = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM institutional_flow_daily")
        min_date, max_date, total_daily_rows = cur.fetchone()

        elapsed_total = time.monotonic() - started
        print(f"\n本次新抓交易日：TWSE +{twse_new}、TPEx +{tpex_new}；"
              f"本地累積交易日：TWSE {twse_total}、TPEx {tpex_total}")
        print(f"institutional_flow_daily：{total_daily_rows} 列，涵蓋 {min_date} ~ {max_date}")
        print(f"institutional_flow_summary：目標 {len(targets)} 檔，涵蓋 {len(summary_rows)} 檔，"
              f"缺資料 {len(missing_summary)} 檔")
        if missing_summary:
            print("summary 缺資料清單：")
            for m in sorted(missing_summary):
                print(f"  - {m}")
        if failed:
            print(f"\n本次執行有 {len(failed)} 個「市場+日期」抓取失敗（重試 3 次仍失敗，已跳過，"
                  f"之後重跑本腳本會自動補抓）：")
            for f in failed:
                print(f"  - {f}")
        print(f"\n總耗時 {elapsed_total/60:.1f} 分鐘，寫入完成: {db_path}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新全市場股票的三大法人近 3 年歷史 + 近期彙總（官方 API 直抓，增量可續傳）")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--target-trading-days", type=int, default=TARGET_TRADING_DAYS,
        help=f"backfill 目標交易日數（預設 {TARGET_TRADING_DAYS}，約 3 年）",
    )
    args = parser.parse_args()
    build(args.db_path, args.target_trading_days)


if __name__ == "__main__":
    main()
