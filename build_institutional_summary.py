"""建置/刷新 `stock_groups` 名單股票的三大法人近期買賣超動態快照。

資料源：`C:\\CLAUDE\\tw_cache\\institutional.db`（tw-momentum-scanner 專案的既有唯讀
共用資產）。本腳本**只讀取，絕不寫入或搬動該檔案**；只讀連線用 SQLite URI `mode=ro` 開啟，
連寫入權限都沒有。schema：`institutional(date, stock_id, foreign_net, trust_net,
dealer_net)`，逐日、股數單位、涵蓋 2013-01-02 至今（實際涵蓋期間以執行當下 MAX(date) 為準）。

**跨資料庫 stock_id 格式陷阱**：institutional.db 的 stock_id 是 yfinance 風格代號
（TWSE 股票帶 `.TW` 後綴、TPEx 股票帶 `.TWO` 後綴，例如 `2330.TW`、`3105.TWO`），
不是本專案 `stocks` 表用的純 4 碼代號，比對前必須依 `stocks.market` 組出對應後綴。

**已知覆蓋限制**：institutional.db 只涵蓋 tw-momentum-scanner 篩選過的動能股歷史清單
（696 檔，不是全市場），本專案 91 檔概念股中有些從未進過該清單，institutional.db 查無
資料，一律留 NULL，不臆測。

本腳本輸出兩張表，皆為**快照覆蓋**（每次執行整批 DELETE + INSERT，不逐筆 upsert，也不
無限累積歷史 —— 逐日明細只保留近 60 個交易日的明確時間窗）：
    - institutional_flow_summary：每檔股票一列，近 5/20/60 日三大法人累計買賣超 +
      外資連續買/賣超天數（streak）
    - institutional_flow_daily：近 60 個交易日逐日明細（供未來視覺化畫走勢圖用）

用法：
    python build_institutional_summary.py [--db-path PATH] [--institutional-db PATH]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "data" / "tw_stocks.db"
DEFAULT_INSTITUTIONAL_DB_PATH = Path("C:/CLAUDE/tw_cache/institutional.db")

DETAIL_WINDOW_DAYS = 60    # institutional_flow_daily 明確保留的交易日視窗上限（不無限累積）
STREAK_LOOKBACK_DAYS = 90  # streak 計算用的抓取筆數緩衝（避免恰好卡在 60 日視窗邊界低估連續天數）

_MARKET_SUFFIX = {"TWSE": ".TW", "TPEx": ".TWO"}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS institutional_flow_summary (
    stock_id                  TEXT PRIMARY KEY,
    latest_date                TEXT,     -- institutional.db 中該股最新一筆資料日期
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


def _fetch_recent_rows(inst_conn: sqlite3.Connection, inst_stock_id: str, limit: int):
    cur = inst_conn.execute(
        "SELECT date, foreign_net, trust_net, dealer_net FROM institutional "
        "WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
        (inst_stock_id, limit),
    )
    return cur.fetchall()


def _sum_net(rows, idx: int, n: int) -> int | None:
    window = rows[:n]
    if not window:
        return None
    return sum(r[idx] for r in window if r[idx] is not None)


def _foreign_streak(rows) -> tuple[int, bool]:
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


def build(db_path: Path, institutional_db_path: Path) -> None:
    if not institutional_db_path.exists():
        raise SystemExit(f"找不到 institutional.db：{institutional_db_path}（唯讀共用資料源，不可自行建立）")
    if not db_path.exists():
        raise SystemExit(f"{db_path} 不存在，請先跑: python build_db.py")

    conn = sqlite3.connect(db_path)
    inst_conn = sqlite3.connect(f"file:{institutional_db_path.as_posix()}?mode=ro", uri=True)
    try:
        conn.executescript(SCHEMA_SQL)

        targets = _target_stocks(conn)
        institutional_max_date = inst_conn.execute("SELECT MAX(date) FROM institutional").fetchone()[0]

        now = _now_iso()
        summary_rows: list[dict] = []
        daily_rows: list[dict] = []
        missing: list[str] = []

        for stock_id, market in targets:
            suffix = _MARKET_SUFFIX.get(market)
            if suffix is None:
                missing.append(f"{stock_id}（未知市場別 {market}）")
                continue
            rows = _fetch_recent_rows(inst_conn, f"{stock_id}{suffix}", STREAK_LOOKBACK_DAYS)
            if not rows:
                missing.append(f"{stock_id}（institutional.db 查無此代號，不在 tw-momentum-scanner 追蹤清單內）")
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

        print(f"institutional.db 最新資料日期: {institutional_max_date}")
        print(f"目標股票 {len(targets)} 檔，涵蓋 {len(summary_rows)} 檔，缺資料 {len(missing)} 檔")
        if missing:
            print("缺資料清單：")
            for m in missing:
                print(f"  - {m}")
    finally:
        inst_conn.close()
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="建置/刷新 stock_groups 名單的三大法人近期買賣超動態快照")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--institutional-db", type=Path, default=DEFAULT_INSTITUTIONAL_DB_PATH)
    args = parser.parse_args()
    build(args.db_path, args.institutional_db)


if __name__ == "__main__":
    main()
