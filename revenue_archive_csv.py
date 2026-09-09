"""MOPS官方CSV補洞；僅當前封存快照，不提供歷史known_on。"""
import csv
import hashlib
import io
import json
import re
import sqlite3


_REQUIRED_COLUMNS = {
    '出表日期',
    '資料年月',
    '公司代號',
    '營業收入-當月營收',
}
_REQUIRED_FORM_FIELDS = {'step', 'functionName', 'filePath', 'fileName'}


def _validate_month(month):
    if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('月份格式錯誤')
    if not 1 <= int(month[5:]) <= 12:
        raise ValueError('月份格式錯誤')


def _decode_csv(body):
    if not isinstance(body, bytes) or not body:
        raise ValueError('CSV無資料')
    try:
        text = body.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('CSV不是UTF-8') from exc
    leading = text.lstrip().lower()
    if not leading or leading.startswith('<') or '<html' in leading[:500]:
        raise ValueError('回應不是CSV')
    return text


def parse_csv(body: bytes, month: str) -> list[dict]:
    """Parse all domestic/foreign issuers in an official monthly CSV."""
    _validate_month(month)
    reader = csv.DictReader(io.StringIO(_decode_csv(body)))
    if reader.fieldnames is None or not _REQUIRED_COLUMNS.issubset(reader.fieldnames):
        raise ValueError('CSV缺少必要欄位')

    result = []
    stock_ids = set()
    for raw in reader:
        if raw is None or all(value in (None, '') for value in raw.values()):
            continue
        try:
            data_month = raw['資料年月'].strip()
            match = re.fullmatch(r'(\d{2,3})/(\d{1,2})', data_month)
            if not match or not 1 <= int(match.group(2)) <= 12:
                raise ValueError('CSV資料年月格式錯誤')
            parsed_month = f'{int(match.group(1)) + 1911:04d}-{int(match.group(2)):02d}'
            if parsed_month != month:
                raise ValueError('CSV月份不符')

            stock_id = raw['公司代號'].strip()
            if not stock_id:
                raise ValueError('CSV公司代號缺值')
            if stock_id in stock_ids:
                raise ValueError('CSV股票代號重複')

            revenue_text = raw['營業收入-當月營收'].strip().replace(',', '')
            if not re.fullmatch(r'[+-]?\d+', revenue_text):
                raise ValueError('CSV營收不是整數')
            revenue_twd = int(revenue_text) * 1000
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError('CSV欄位內容錯誤') from exc

        stock_ids.add(stock_id)
        result.append({
            'stock_id': stock_id,
            'month': month,
            'revenue_twd': revenue_twd,
            'page_date': raw['出表日期'],
            'raw': raw,
        })

    if not result:
        raise ValueError('CSV無資料')
    return result


def _canonical_request(request):
    if not isinstance(request, dict):
        raise ValueError('request格式錯誤')
    url = request.get('url')
    form = request.get('form')
    if not isinstance(url, str) or not url.strip() or not isinstance(form, dict):
        raise ValueError('request缺少必要欄位')
    if any(key not in form or form[key] in (None, '') for key in _REQUIRED_FORM_FIELDS):
        raise ValueError('request缺少必要欄位')
    try:
        identity_json = json.dumps(
            {'url': url, 'form': form},
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        request_json = json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        form_json = json.dumps(
            form,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError('request無法序列化') from exc
    return hashlib.sha256(identity_json.encode('utf-8')).hexdigest(), url, form_json, request_json


def store_csv(
    conn: sqlite3.Connection,
    body: bytes,
    month: str,
    market: str,
    request: dict,
    fetched_at: str,
) -> int:
    """Atomically replace one reproducible official CSV source snapshot."""
    if not isinstance(conn, sqlite3.Connection):
        raise ValueError('conn必須是SQLite連線')
    if not isinstance(market, str) or not market.strip():
        raise ValueError('market不可為空')
    if not isinstance(fetched_at, str) or not fetched_at.strip():
        raise ValueError('fetched_at不可為空')

    rows = parse_csv(body, month)
    source_key, url, form_json, request_json = _canonical_request(request)
    body_sha256 = hashlib.sha256(body).hexdigest()

    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS csv_sources (
                source_key TEXT PRIMARY KEY,
                request_url TEXT NOT NULL,
                request_form_json TEXT NOT NULL,
                request_json TEXT NOT NULL,
                market TEXT NOT NULL,
                month TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                body BLOB NOT NULL,
                row_count INTEGER NOT NULL
            )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS csv_revenues (
                source_key TEXT NOT NULL,
                stock_id TEXT NOT NULL,
                month TEXT NOT NULL,
                revenue_twd INTEGER NOT NULL,
                page_date TEXT,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (source_key, stock_id),
                FOREIGN KEY (source_key) REFERENCES csv_sources(source_key)
            )''')
        conn.execute(
            '''INSERT INTO csv_sources
               (source_key, request_url, request_form_json, request_json,
                market, month, fetched_at, body_sha256, body, row_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_key) DO UPDATE SET
                   request_url=excluded.request_url,
                   request_form_json=excluded.request_form_json,
                   request_json=excluded.request_json,
                   market=excluded.market,
                   month=excluded.month,
                   fetched_at=excluded.fetched_at,
                   body_sha256=excluded.body_sha256,
                   body=excluded.body,
                   row_count=excluded.row_count''',
            (source_key, url, form_json, request_json, market, month, fetched_at,
             body_sha256, body, len(rows)),
        )
        conn.execute('DELETE FROM csv_revenues WHERE source_key = ?', (source_key,))
        conn.executemany(
            '''INSERT INTO csv_revenues
               (source_key, stock_id, month, revenue_twd, page_date, raw_json)
               VALUES (?, ?, ?, ?, ?, ?)''',
            [
                (source_key, row['stock_id'], row['month'], row['revenue_twd'],
                 row['page_date'], json.dumps(row['raw'], ensure_ascii=False))
                for row in rows
            ],
        )
    return len(rows)
