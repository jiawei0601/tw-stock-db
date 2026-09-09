import json
import sqlite3

import pytest

from backfill_finmind_revenue import init_db, parse_rows, store_response


def payload(value=123):
    return dict(status=200, data=[dict(stock_id='2456', revenue_year=2020, revenue_month=1, revenue=value,
                                     date='2020-02-01', create_time='')])


def test_response_preserves_amount_and_date_semantics():
    c = sqlite3.connect(':memory:');init_db(c)
    query = dict(data_id='2456')
    assert store_response(c, query, json.dumps(payload()), '2026-09-09') == 1
    assert c.execute('SELECT month,revenue_twd,api_date,create_time FROM observations').fetchone() == ('2020-01',123,'2020-02-01','')
    store_response(c, query, json.dumps(payload(-10)), '2026-09-10')
    assert c.execute('SELECT count(*),sum(revenue_twd) FROM observations').fetchone() == (1,-10)


@pytest.mark.parametrize('value', [None,True,1.5,float('inf'),float('nan')])
def test_missing_or_invalid_amount_rejected(value):
    with pytest.raises(ValueError):parse_rows(payload(value),'2456')


def test_empty_success_is_not_failure_and_duplicates_rejected():
    assert parse_rows(dict(status=200,data=[]),'2456') == {}
    p=payload();p['data']*=2
    with pytest.raises(ValueError):parse_rows(p,'2456')
    with pytest.raises(ValueError):parse_rows(payload(),'WRONG')
