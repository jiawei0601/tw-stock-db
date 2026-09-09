"""FinMind免費月營收補洞；保存目前版本，不猜歷史公告時間。"""
import argparse
import hashlib
import json
import math
import sqlite3
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

ROOT = Path('data/momentum_pit/revenue_archive')


def init_db(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS responses (
            query TEXT PRIMARY KEY, stock_id TEXT, fetched_at TEXT,
            body TEXT, sha256 TEXT, api_status INTEGER, row_count INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS observations (
            query TEXT, stock_id TEXT, month TEXT, revenue_twd INTEGER,
            api_date TEXT, create_time TEXT, raw_json TEXT,
            PRIMARY KEY(query,stock_id,month));
    ''')


def parse_rows(payload, stock_id):
    if payload.get('status') != 200 or not isinstance(payload.get('data'), list):
        raise ValueError('FinMind API狀態或資料格式錯誤')
    rows = {}
    for raw in payload['data']:
        if raw.get('stock_id') != stock_id:
            raise ValueError('股票代號與請求不符')
        year, month = raw.get('revenue_year'), raw.get('revenue_month')
        if isinstance(year, bool) or isinstance(month, bool) or not isinstance(year, int) or not isinstance(month, int):
            raise ValueError('營收月份格式錯誤')
        date(year, month, 1)
        ym = f'{year:04d}-{month:02d}'
        value = raw.get('revenue')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value:
            raise ValueError('營收不是有限整數，缺值不可填零')
        if ym in rows:
            raise ValueError('同股同月重複，需核對是否不同版本')
        rows[ym] = (int(value), raw)
    return rows


def store_response(conn, query, body, fetched_at):
    sid = query['data_id']
    payload = json.loads(body)
    rows = parse_rows(payload, sid)
    key = json.dumps(query, sort_keys=True, separators=(',', ':'))
    with conn:
        conn.execute('INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,?,NULL)',
                     (key, sid, fetched_at, body, hashlib.sha256(body.encode()).hexdigest(), 200, len(rows)))
        conn.execute('DELETE FROM observations WHERE query=?', (key,))
        conn.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?)',
                         [(key, sid, ym, value, raw.get('date'), raw.get('create_time'), json.dumps(raw, ensure_ascii=False))
                          for ym, (value, raw) in rows.items()])
    return len(rows)


def seed_probes(conn):
    base = Path('backtest/momentum_rerun_20260909_revenue_audit')
    for path in [base / 'finmind_probe.json', *sorted((base / 'missing_probes').glob('*.json'))]:
        if not path.exists():continue
        data = json.loads(path.read_text(encoding='utf-8'))
        query = data['query']
        key = json.dumps(query, sort_keys=True, separators=(',', ':'))
        if conn.execute('SELECT 1 FROM responses WHERE query=? AND error IS NULL', (key,)).fetchone():continue
        store_response(conn, query, json.dumps(data['response'], ensure_ascii=False), datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', required=True, help='JSON list of stock IDs or object with target_ids')
    parser.add_argument('--end', default=date.today().isoformat())
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    date.fromisoformat(args.end)
    target_data = json.loads(Path(args.targets).read_text(encoding='utf-8'))
    targets = target_data if isinstance(target_data, list) else target_data['target_ids']
    if any(not isinstance(s, str) or not s.isdigit() for s in targets):parser.error('股票代號必須為數字字串')
    targets = list(dict.fromkeys(targets))
    ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROOT / 'free_revenue.db')
    init_db(conn)
    seed_probes(conn)
    session = requests.Session()
    failures = 0
    for index, sid in enumerate(targets, 1):
        query = dict(dataset='TaiwanStockMonthRevenue', data_id=sid, start_date='2018-01-01', end_date=args.end)
        key = json.dumps(query, sort_keys=True, separators=(',', ':'))
        cached = conn.execute('SELECT error FROM responses WHERE query=?', (key,)).fetchone()
        if cached and (cached[0] is None or not args.retry_failed):continue
        stamp = datetime.now(timezone.utc).isoformat()
        body = ''; status = None
        try:
            response = session.get('https://api.finmindtrade.com/api/v4/data', params=query, timeout=30)
            body = response.text
            response.raise_for_status()
            status = json.loads(body).get('status')
            count = store_response(conn, query, body, stamp)
            failures = 0
            print(f'{index}/{len(targets)} {sid} rows={count}', flush=True)
        except (requests.RequestException, ValueError) as exc:
            failures += 1
            with conn:
                conn.execute('INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,?,0,?)',
                             (key, sid, stamp, body, hashlib.sha256(body.encode()).hexdigest(), status, str(exc)))
            print(f'FAILED {sid} {type(exc).__name__}', flush=True)
            if status in (402, 429) or (isinstance(exc, requests.HTTPError) and exc.response.status_code in (402, 429)) or failures >= 2:
                print('來源限額或連續失敗，停止；成功快取可續傳。', flush=True)
                conn.close()
                raise SystemExit(1)
        time.sleep(1.8)
    report = dict(targets=len(targets), successful=sum(bool(conn.execute('SELECT 1 FROM responses WHERE query=? AND error IS NULL',
                  (json.dumps(dict(dataset='TaiwanStockMonthRevenue', data_id=s, start_date='2018-01-01', end_date=args.end), sort_keys=True, separators=(',', ':')),)).fetchone()) for s in targets),
                  certification='CURRENT_SNAPSHOT_NOT_HISTORICAL_PIT')
    (ROOT / 'finmind_download_audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(report, flush=True)
    conn.close()


if __name__ == '__main__':
    main()
