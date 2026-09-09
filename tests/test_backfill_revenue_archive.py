import hashlib
import sqlite3

import pytest

import backfill_revenue_archive as archive


def test_raw_snapshot_retains_all_stocks_and_converts_thousands_to_twd(monkeypatch):
    conn = sqlite3.connect(':memory:')
    archive.init_db(conn)
    rows = [dict(stock_id='OLD', revenue=123, announce_date='2026-09-09'),
            dict(stock_id='NEW', revenue=None, announce_date=None)]
    monkeypatch.setattr(archive, '_parse_html', lambda *args: rows)
    body = b'original archive</html>'
    assert archive.store_page(conn, 'https://example.test/page', 'TWSE', '2018-01', body, '2026-09-09T00:00:00Z') == 2
    saved = conn.execute('SELECT stock_id,revenue_twd,page_date,fetched_at FROM revenue_snapshots ORDER BY stock_id').fetchall()
    assert saved[0][1] is None
    assert saved[1][1:] == (123000, '2026-09-09', '2026-09-09T00:00:00Z')
    assert conn.execute('SELECT body,sha256 FROM pages').fetchone() == (body, hashlib.sha256(body).hexdigest())
    result = archive.audit(conn, 2)
    assert result['stocks'] == 2 and not result['download_complete']
    assert not result['historical_release_dates_verified']
    assert archive.audit(conn, 2, '2020-01', '2020-12')['completed_pages'] == 0


def test_invalid_page_is_not_recorded_as_success(monkeypatch):
    conn = sqlite3.connect(':memory:')
    archive.init_db(conn)
    monkeypatch.setattr(archive, '_parse_html', lambda *args: [])
    with pytest.raises(ValueError):
        archive.store_page(conn, 'url', 'TWSE', '2018-01', b'blocked</html>', 'now')
    assert conn.execute('SELECT count(*) FROM pages').fetchone()[0] == 0


def test_duplicate_stock_requires_review(monkeypatch):
    conn = sqlite3.connect(':memory:')
    archive.init_db(conn)
    monkeypatch.setattr(archive, '_parse_html', lambda *args: [dict(stock_id='A'), dict(stock_id='A')])
    with pytest.raises(ValueError, match='重複'):
        archive.store_page(conn, 'url', 'TWSE', '2018-01', b'duplicates</html>', 'now')


def test_truncated_http_200_is_not_a_complete_page():
    conn = sqlite3.connect(':memory:')
    archive.init_db(conn)
    with pytest.raises(ValueError, match='截斷'):
        archive.store_page(conn, 'url', 'TPEx', '2022-02', b'<html><table>partial', 'now')
    assert conn.execute('SELECT count(*) FROM pages').fetchone()[0] == 0


def test_missing_growth_cell_does_not_drop_valid_revenue_stock():
    # Actual historical format: growth cannot be computed when base is zero;
    # MOPS emits a plain td with nbsp instead of td nowrap for this cell.
    html = '<html><table><tr align=right><td align=center>6743</td><td align=left>Test</td><td nowrap>173,618</td><td nowrap>0</td><td nowrap>151,030</td><td>&nbsp;</td><Td nowrap>14.95</td><td nowrap>173,618</td><td nowrap>151,030</td><td nowrap>14.95</td><td align=center>-</td></tr></table></html>'
    conn = sqlite3.connect(':memory:')
    archive.init_db(conn)
    assert archive.store_page(conn, 'url', 'TWSE', '2018-01', html.encode(), 'now') == 1
    assert conn.execute('SELECT revenue_twd FROM revenue_snapshots').fetchone()[0] == 173618000
