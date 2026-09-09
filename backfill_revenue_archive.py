"""補齊全市場月營收封存數值並保存原始頁；不冒充歷史公告版本。"""
import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from collectors.revenue_history import _parse_html
from revenue_filter import month_shift


def init_db(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS pages (
            url TEXT PRIMARY KEY, market TEXT, month TEXT, fetched_at TEXT,
            sha256 TEXT, body BLOB, row_count INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS revenue_snapshots (
            url TEXT, stock_id TEXT, month TEXT, revenue_twd INTEGER,
            page_date TEXT, fetched_at TEXT, row_json TEXT,
            PRIMARY KEY(url, stock_id));
    ''')


def store_page(conn, url, market, month, body, fetched_at):
    if b'</html>' not in body.lower():
        raise ValueError('HTML回應被截斷，不能標成下載完成')
    rows = _parse_html(body.decode('cp950', errors='replace'), market, month)
    if not rows:
        raise ValueError('封存頁無可解析資料，不能標成下載完成')
    if len({r['stock_id'] for r in rows}) != len(rows):
        raise ValueError('同頁股票代號重複，需核對頁面格式')
    with conn:
        conn.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?,NULL)',
                     (url, market, month, fetched_at, hashlib.sha256(body).hexdigest(), body, len(rows)))
        conn.execute('DELETE FROM revenue_snapshots WHERE url=?', (url,))
        for row in rows:
            value = row['revenue']
            conn.execute('INSERT OR REPLACE INTO revenue_snapshots VALUES (?,?,?,?,?,?,?)',
                         (url, row['stock_id'], month, value * 1000 if value is not None else None,
                          row['announce_date'], fetched_at, json.dumps(row, ensure_ascii=False)))
    return len(rows)


def audit(conn, expected, start="0000-00", end="9999-99"):
    completed = conn.execute('SELECT count(*) FROM pages WHERE error IS NULL AND row_count>0 AND month BETWEEN ? AND ?', (start,end)).fetchone()[0]
    stats = conn.execute('SELECT count(*),count(distinct stock_id),min(month),max(month),coalesce(sum(revenue_twd<0),0),coalesce(sum(revenue_twd IS NULL),0) FROM revenue_snapshots WHERE month BETWEEN ? AND ? AND url IN (SELECT url FROM pages WHERE error IS NULL AND row_count>0)', (start,end)).fetchone()
    return dict(expected_pages=expected, completed_pages=completed, rows=stats[0], stocks=stats[1],
                first_month=stats[2], last_month=stats[3], negative_revenue_rows=stats[4], missing_revenue_rows=stats[5], download_complete=completed == expected,
                certification='CURRENT_ARCHIVE_SNAPSHOT_NOT_HISTORICAL_PIT',
                historical_release_dates_verified=False,
                errors=conn.execute('SELECT url,error FROM pages WHERE error IS NOT NULL AND month BETWEEN ? AND ?', (start,end)).fetchall())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-failed', action='store_true', help='明確重試先前失敗頁，預設保留錯誤不反覆請求')
    parser.add_argument('--reparse', action='store_true', help='以現有原始頁重新解析，不重抓有效網頁')
    parser.add_argument('--start', default='2018-01')
    parser.add_argument('--end', default='2026-08')
    args = parser.parse_args()
    # Validate before constructing URLs or entering a network loop.
    month_shift(args.start, 0)
    month_shift(args.end, 0)
    if args.start > args.end:parser.error('start 必須早於 end')
    root = Path('data/momentum_pit/revenue_archive')
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / 'snapshots.db')
    init_db(conn)
    months = []
    month = args.start
    while month <= args.end:
        months.append(month)
        month = month_shift(month, 1)
    session = requests.Session()
    failed = 0
    for month in months:
        for market, code, variant in [(m,c,v) for m,c in [('TWSE','sii'),('TPEx','otc')] for v in (0,1)]:
            year, number = map(int, month.split('-'))
            url = f'https://mopsov.twse.com.tw/nas/t21/{code}/t21sc03_{year-1911}_{number}_{variant}.html'
            cached = conn.execute('SELECT row_count,error,body,fetched_at FROM pages WHERE url=?', (url,)).fetchone()
            if cached and cached[1] is not None and not args.retry_failed:continue
            if cached and cached[0] and cached[1] is None and cached[2] and b'</html>' in cached[2].lower():
                if args.reparse:store_page(conn, url, market, month, cached[2], cached[3])
                continue
            stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
            response = None
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                count = store_page(conn, url, market, month, response.content, stamp)
                failed = 0
                print(month, market, variant, count, flush=True)
            except (requests.RequestException, ValueError) as exc:
                failed += 1
                with conn:
                    error_body = response.content if response is not None else (cached[2] if cached else None)
                    conn.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,0,?)',
                                 (url, market, month, stamp, hashlib.sha256(error_body).hexdigest() if error_body else None, error_body, str(exc)))
                print('FETCH_FAILED', month, market, type(exc).__name__, flush=True)
            (root / 'audit.json').write_text(json.dumps(audit(conn, len(months)*4, args.start, args.end), ensure_ascii=False, indent=2), encoding='utf-8')
            if failed >= 2:
                print('連續兩次失敗，停止網路請求，保留續傳狀態。', flush=True)
                conn.close()
                raise SystemExit(1)
            time.sleep(1.7)
    final = audit(conn, len(months)*4, args.start, args.end)
    (root / 'audit.json').write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(final, ensure_ascii=False), flush=True)
    conn.close()


if __name__ == '__main__':
    main()
