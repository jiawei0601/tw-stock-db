import pytest

from listed_universe import ListedUniverse
from dynamic_momentum import signal_table
from tests.test_momentum_engine import fixture
from weekly_hermes_momentum import simulate_weekly


def interval(sid='A', start='2020-01-02', end=None, market='TWSE'):
    return dict(stock_id=sid,start=start,end=end,market=market,source='test evidence')


def test_listing_inclusive_delisting_exclusive_and_unknown_rejected():
    u=ListedUniverse([interval(end='2020-02-01')])
    assert not u.eligible('A','2020-01-01')
    assert u.eligible('A','2020-01-02')
    assert u.eligible('A','2020-01-31')
    assert not u.eligible('A','2020-02-01')
    assert not u.eligible('UNKNOWN','2020-01-10')


def test_verified_otc_to_twse_preserves_both_intervals_but_not_gap():
    u=ListedUniverse([interval(start='2010-01-01',end='2020-01-01',market='TPEX'),
                      interval(start='2020-01-03')])
    assert u.eligible('A','2019-12-31')
    assert not u.eligible('A','2020-01-02')
    assert u.eligible('A','2020-01-03')


def test_future_delisting_does_not_remove_past_membership_or_prices():
    rows={'A':{'2020-01-02':{'close':1}}}
    open_u=ListedUniverse([interval()])
    future_u=ListedUniverse([interval(end='2030-01-01')])
    assert open_u.restrict_prices(rows)==future_u.restrict_prices(rows)==rows


def test_emerging_rows_do_not_count_towards_200_session_warmup_or_ranking():
    data=fixture()
    prices,calendar=data['prices'],data['calendar']
    for rows in prices.values():
        for row in rows.values():row.update(Trading_Volume=1,Trading_money=200_000_000)
    day='2019-12-31'
    raw=signal_table(prices,{},calendar,day)
    assert raw
    sid=next(iter(raw))
    u=ListedUniverse([interval(sid=s,start='2019-12-01' if s==sid else '1900-01-01') for s in prices])
    masked=u.restrict_prices(prices)
    result=signal_table(masked,{},calendar,day)
    assert sid not in result
    assert all(d>='2019-12-01' for d in masked[sid])
    assert len(prices[sid])>len(masked[sid])  # raw input untouched


def test_order_cannot_fill_after_eligibility_ends_and_no_fake_liquidation():
    days=['2019-12-31','2020-01-02','2020-01-03']
    prices={'A':{d:dict(open=100,close=100,Trading_Volume=1) for d in days}}
    tables={'2019-12-31':{'A':dict(score=.1,top=True,liquid=True,outside=False)},
            days[1]:{'A':dict(score=.2,top=True,liquid=True,outside=False)}}
    u=ListedUniverse([interval(end='2020-01-03')])
    result=simulate_weekly(u.restrict_prices(prices),{},days,tables,trailing=False,
                           signal_weekday=3,equity_allocation=True,position_weight=.05)
    assert result[3] and not result[3][0]['filled']
    assert result[0][-1]['cash']==1e6


def test_existing_position_is_not_fictitiously_cashed_out_at_future_delisting():
    days=['2019-12-31','2020-01-02','2020-01-03','2020-01-06']
    prices={'A':{d:dict(open=100,close=100,Trading_Volume=1,max=100) for d in days}}
    tables={days[0]:{'A':dict(score=.1,top=True,liquid=True,outside=False)},
            days[1]:{'A':dict(score=.2,top=True,liquid=True,outside=False)}}
    u=ListedUniverse([interval(end='2020-01-06')])
    result=simulate_weekly(u.restrict_prices(prices),{},days,tables,trailing=False,
                           peak_stop=.30,signal_weekday=3,equity_allocation=True,position_weight=.05)
    assert result[3][0]['filled'] and 'A' in result[4]
    assert not result[2]
    assert result[0][-1]['cash']==950000
    assert result[0][-1]['missing_marks']==1


@pytest.mark.parametrize('record',[interval(market='EMERGING'),interval(end='2020-01-02'),
                                    {**interval(),'source':''}])
def test_invalid_or_unevidenced_intervals_rejected(record):
    with pytest.raises(ValueError):ListedUniverse([record])
