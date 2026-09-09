import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import revenue_archive_csv as archive_csv


HEADERS = (
    '出表日期,資料年月,公司代號,公司名稱,產業別,營業收入-當月營收\n'
)


def csv_body(*rows):
    return (HEADERS + '\n'.join(rows) + '\n').encode('utf-8-sig')


def request(file_name='t21sc03_111_2.csv', **extra):
    result = {
        'url': 'https://mopsov.twse.com.tw/server-java/FileDownLoad',
        'form': {
            'step': '9',
            'functionName': 'show_file2',
            'filePath': '/t21/otc/',
            'fileName': file_name,
        },
    }
    result.update(extra)
    return result


def test_parse_csv_converts_thousands_and_preserves_negative_and_raw():
    body = csv_body(
        '115/09/05,111/2,1240,茂生農經,農業科技,211683',
        '115/09/05,111/2,6024,群益期,金融保險,-244632',
    )

    rows = archive_csv.parse_csv(body, '2022-02')

    assert [(row['stock_id'], row['revenue_twd']) for row in rows] == [
        ('1240', 211_683_000),
        ('6024', -244_632_000),
    ]
    assert rows[0]['month'] == '2022-02'
    assert rows[0]['page_date'] == '115/09/05'
    assert rows[0]['raw']['營業收入-當月營收'] == '211683'


@pytest.mark.parametrize(
    ('body', 'month', 'message'),
    [
        (b'', '2022-02', '無資料'),
        (b'<html>blocked</html>', '2022-02', '不是CSV'),
        (csv_body('115/09/05,111/2,1240,A,X,1'), '2022-13', '月份格式'),
        (csv_body('115/09/05,111/3,1240,A,X,1'), '2022-02', '月份不符'),
        (csv_body('115/09/05,111/2,1240,A,X,'), '2022-02', '營收不是整數'),
        (csv_body('115/09/05,111/2,1240,A,X,1', '115/09/05,111/2,1240,A,X,2'), '2022-02', '重複'),
        (b'\xef\xbb\xbf\xe5\x87\xba\xe8\xa1\xa8\xe6\x97\xa5\xe6\x9c\x9f,\xe8\xb3\x87\xe6\x96\x99\xe5\xb9\xb4\xe6\x9c\x88\n115/09/05,111/2\n', '2022-02', '必要欄位'),
    ],
)
def test_parse_csv_rejects_invalid_input_consistently(body, month, message):
    with pytest.raises(ValueError, match=message):
        archive_csv.parse_csv(body, month)


def test_store_csv_keeps_request_body_hash_and_raw_json():
    conn = sqlite3.connect(':memory:')
    body = csv_body('115/09/05,111/2,1240,茂生農經,農業科技,211683')
    source_request = request(http_status=200, headers={'Date': 'probe-date'})

    assert archive_csv.store_csv(
        conn, body, '2022-02', 'TPEx', source_request, '2026-09-09T02:40:20Z'
    ) == 1

    source = conn.execute(
        '''SELECT source_key, request_url, request_form_json, request_json,
                  market, month, fetched_at, body_sha256, body, row_count
           FROM csv_sources'''
    ).fetchone()
    expected_identity = json.dumps(
        {'url': source_request['url'], 'form': source_request['form']},
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    assert source[0] == hashlib.sha256(expected_identity.encode()).hexdigest()
    assert source[1] == source_request['url']
    assert json.loads(source[2]) == source_request['form']
    assert json.loads(source[3]) == source_request
    assert source[4:7] == ('TPEx', '2022-02', '2026-09-09T02:40:20Z')
    assert source[7:] == (hashlib.sha256(body).hexdigest(), body, 1)

    revenue = conn.execute(
        'SELECT source_key,stock_id,month,revenue_twd,page_date,raw_json FROM csv_revenues'
    ).fetchone()
    assert revenue[:5] == (source[0], '1240', '2022-02', 211_683_000, '115/09/05')
    assert json.loads(revenue[5])['公司名稱'] == '茂生農經'


def test_source_key_depends_only_on_canonical_url_and_form():
    conn = sqlite3.connect(':memory:')
    body = csv_body('115/09/05,111/2,1240,茂生農經,農業科技,211683')
    first = request(http_status=200)
    reordered = {
        'headers': {'Date': 'later probe'},
        'form': dict(reversed(list(first['form'].items()))),
        'url': first['url'],
    }

    archive_csv.store_csv(conn, body, '2022-02', 'TPEx', first, 'first')
    first_key = conn.execute('SELECT source_key FROM csv_sources').fetchone()[0]
    archive_csv.store_csv(conn, body, '2022-02', 'TPEx', reordered, 'second')

    assert conn.execute('SELECT count(*) FROM csv_sources').fetchone()[0] == 1
    assert conn.execute('SELECT source_key FROM csv_sources').fetchone()[0] == first_key
    assert json.loads(conn.execute('SELECT request_json FROM csv_sources').fetchone()[0]) == reordered


def test_same_source_replaces_only_its_rows_and_is_idempotent():
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys = ON')
    first = csv_body(
        '115/09/05,111/2,A,A公司,X,1',
        '115/09/05,111/2,B,B公司,X,2',
    )
    replacement = csv_body(
        '115/09/06,111/2,A,A公司,X,3',
        '115/09/06,111/2,C,C公司,X,4',
    )
    other_request = request('other.csv')
    other = csv_body('115/09/05,111/2,Z,Z公司,X,9')

    archive_csv.store_csv(conn, first, '2022-02', 'TPEx', request(), 'first')
    archive_csv.store_csv(conn, other, '2022-02', 'TPEx', other_request, 'other')
    archive_csv.store_csv(conn, replacement, '2022-02', 'TPEx', request(), 'second')
    archive_csv.store_csv(conn, replacement, '2022-02', 'TPEx', request(), 'second')

    assert conn.execute('SELECT count(*) FROM csv_sources').fetchone()[0] == 2
    assert conn.execute('SELECT count(*) FROM csv_revenues').fetchone()[0] == 3
    assert conn.execute(
        'SELECT stock_id,revenue_twd FROM csv_revenues ORDER BY stock_id'
    ).fetchall() == [('A', 3000), ('C', 4000), ('Z', 9000)]


def test_database_failure_rolls_back_source_update_and_revenue_replacement():
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA foreign_keys = ON')
    original = csv_body('115/09/05,111/2,A,A公司,X,1')
    archive_csv.store_csv(conn, original, '2022-02', 'TPEx', request(), 'original')
    original_source = conn.execute(
        'SELECT fetched_at,body_sha256,body,row_count FROM csv_sources'
    ).fetchone()
    original_revenue = conn.execute(
        'SELECT stock_id,revenue_twd,page_date FROM csv_revenues'
    ).fetchone()

    # parse_csv accepts this as an integer, but SQLite cannot store it in INTEGER.
    overflow = csv_body('115/09/06,111/2,B,B公司,X,999999999999999999999999')
    with pytest.raises(OverflowError):
        archive_csv.store_csv(conn, overflow, '2022-02', 'TPEx', request(), 'failed')

    assert conn.execute(
        'SELECT fetched_at,body_sha256,body,row_count FROM csv_sources'
    ).fetchone() == original_source
    assert conn.execute(
        'SELECT stock_id,revenue_twd,page_date FROM csv_revenues'
    ).fetchone() == original_revenue


def test_invalid_csv_or_request_does_not_pollute_existing_database():
    conn = sqlite3.connect(':memory:')
    good = csv_body('115/09/05,111/2,A,A公司,X,1')
    archive_csv.store_csv(conn, good, '2022-02', 'TPEx', request(), 'good')

    duplicate = csv_body(
        '115/09/05,111/2,B,B公司,X,2',
        '115/09/05,111/2,B,B公司,X,3',
    )
    with pytest.raises(ValueError, match='重複'):
        archive_csv.store_csv(conn, duplicate, '2022-02', 'TPEx', request('bad.csv'), 'bad')
    with pytest.raises(ValueError, match='request缺少必要欄位'):
        archive_csv.store_csv(conn, good, '2022-02', 'TPEx', {'url': 'x', 'form': {}}, 'bad')

    assert conn.execute('SELECT count(*) FROM csv_sources').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM csv_revenues').fetchone()[0] == 1


def test_real_official_csv_sample_has_824_unique_rows():
    path = Path(
        'backtest/momentum_rerun_20260909_revenue_audit/'
        'official_csv/2022-02-otc.raw'
    )
    if not path.exists():
        pytest.skip('本機官方CSV probe未保存在CI checkout')

    rows = archive_csv.parse_csv(path.read_bytes(), '2022-02')

    assert len(rows) == 824
    assert len({row['stock_id'] for row in rows}) == 824
