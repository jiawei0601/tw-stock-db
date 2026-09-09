from datetime import date, timedelta

import pytest

from momentum_entry_filters import TechnicalFeatures, TechnicalEntryGate


def fixture(n=65):
    days = [(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(n)]
    prices = {'A': {d:{'close':100+i} for i,d in enumerate(days)}}
    market = {d:1000+i for i,d in enumerate(days)}
    return days, prices, market


class Revenue:
    def __init__(self, passed=True):
        self.passed = passed

    def evaluate(self, sid, day):
        return dict(stock_id=sid, signal_day=day, passed=self.passed,
                    reason='pass' if self.passed else 'revenue_failed')


def test_ma_slope_uses_five_market_sessions_and_includes_signal_close():
    days, prices, market = fixture()
    f = TechnicalFeatures(prices, {}, days, market)
    row = f.stock('A', days[-1])
    assert row['ma20'] == pytest.approx(sum(range(145,165))/20)
    assert row['prior_ma20'] == pytest.approx(sum(range(140,160))/20)
    assert f.market(days[-1])['market_ma60'] == pytest.approx(sum(range(1005,1065))/60)
    assert TechnicalEntryGate(Revenue(),f,'all')('A',days[-1])


def test_split_rebases_both_ma_windows_to_signal_units_and_ignores_future_events():
    days, prices, market = fixture()
    split_day = days[-3]
    for d in days[-3:]:
        prices['A'][d]['close'] /= 2
    events = {('A',split_day):2, ('A','2099-01-01'):1000}
    f = TechnicalFeatures(prices, events, days, market)
    row = f.stock('A', days[-1])
    assert row['ma20'] == pytest.approx(sum(range(145,165))/40)
    assert row['prior_ma20'] == pytest.approx(sum(range(140,160))/40)
    assert TechnicalEntryGate(Revenue(),f,'trend')('A',days[-1])


def test_future_prices_index_and_events_do_not_change_earlier_features():
    days, prices, market = fixture(90)
    cutoff = days[64]
    events = {('A',days[70]):2}
    full = TechnicalFeatures(prices,events,days,market)
    prefix = TechnicalFeatures({'A':{d:r for d,r in prices['A'].items() if d<=cutoff}},
                               {}, days[:65], {d:v for d,v in market.items() if d<=cutoff})
    assert full.stock('A',cutoff) == prefix.stock('A',cutoff)
    assert full.market(cutoff) == prefix.market(cutoff)


def test_missing_price_rejects_stock_filter_but_not_market_only():
    days, prices, market = fixture()
    del prices['A'][days[-10]]
    f = TechnicalFeatures(prices,{},days,market)
    assert TechnicalEntryGate(Revenue(),f,'trend').evaluate('A',days[-1])['reason']=='stock_ma_unavailable'
    assert TechnicalEntryGate(Revenue(),f,'market')('A',days[-1])


def test_missing_index_rejects_market_filter_but_not_stock_only():
    days, prices, market = fixture()
    del market[days[-30]]
    f = TechnicalFeatures(prices,{},days,market)
    assert TechnicalEntryGate(Revenue(),f,'market').evaluate('A',days[-1])['reason']=='market_ma_unavailable'
    assert TechnicalEntryGate(Revenue(),f,'trend')('A',days[-1])


@pytest.mark.parametrize('mode',['trend','market','trend_cap','trend_market','all'])
def test_flat_ma_does_not_pass_strict_trend(mode):
    days, prices, market = fixture()
    prices = {'A':{d:{'close':100} for d in days}}
    market = {d:1000 for d in days}
    assert not TechnicalEntryGate(Revenue(),TechnicalFeatures(prices,{},days,market),mode)('A',days[-1])


def test_cap_boundary_inclusive_and_excess_rejected_without_changing_trend_rule():
    class Features:
        def stock(self, sid, day):
            return dict(close=110,ma20=100,prior_ma20=99,stock_reason='available')
    f = Features()
    assert TechnicalEntryGate(Revenue(),f,'trend_cap')('A','2020-01-01')
    f.stock = lambda s,d:dict(close=110.01,ma20=100,prior_ma20=99,stock_reason='available')
    assert TechnicalEntryGate(Revenue(),f,'trend')('A','2020-01-01')
    assert not TechnicalEntryGate(Revenue(),f,'trend_cap')('A','2020-01-01')


def test_base_and_revenue_failure_never_require_technical_data():
    assert TechnicalEntryGate(Revenue(),None,'base')('A','2020-01-01')
    for mode in ('base','trend','market','trend_cap','trend_market','all'):
        assert not TechnicalEntryGate(Revenue(False),None,mode)('A','2020-01-01')


def test_insufficient_warmup_rejects_without_shorter_ma():
    days, prices, market = fixture(24)
    f = TechnicalFeatures(prices,{},days,market)
    assert f.stock('A',days[-1])['stock_reason']=='stock_ma_unavailable'
    assert f.market(days[-1])['market_reason']=='market_ma_unavailable'
