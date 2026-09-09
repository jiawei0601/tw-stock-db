import pytest

from revenue_filter import RevenueFilter
from tests.test_weekly_hermes_momentum import scenario
from weekly_hermes_momentum import simulate_weekly


def observation(month, revenue, known):
    return dict(stock_id='A', month=month, revenue=revenue, known_on=known, source='synthetic-test')


def records(current=120):
    return [observation('2018-12', 100, '2019-01-10'),
            observation('2019-01', 100, '2019-02-10'),
            observation('2019-12', 110, '2020-01-10'),
            observation('2020-01', current, '2020-02-10')]


def test_positive_and_accelerating():
    r = RevenueFilter(records()).evaluate('A', '2020-02-11')
    assert r['passed']
    assert r['yoy'] == pytest.approx(.2)
    assert r['previous_yoy'] == pytest.approx(.1)
    assert len(r['evidence']) == 4


@pytest.mark.parametrize('current', [110, 105, 100, 90])
def test_equal_decelerating_zero_and_negative_fail(current):
    assert not RevenueFilter(records(current))('A', '2020-02-11')


def test_same_day_disclosure_and_future_revision_are_not_visible():
    original = RevenueFilter(records())
    assert original.evaluate('A', '2020-02-10')['month'] == '2019-12'
    revised = RevenueFilter(records() + [observation('2020-01', 80, '2020-02-20')])
    for day in ['2020-02-11', '2020-02-19', '2020-02-20']:
        assert revised.evaluate('A', day) == original.evaluate('A', day)
    assert not revised('A', '2020-02-21')


def test_missing_base_zero_base_and_stale_fail_closed():
    assert RevenueFilter(records()[1:]).evaluate('A', '2020-02-11')['reason'] == 'missing_comparison_month'
    rows = records()
    rows[1]['revenue'] = 0
    assert RevenueFilter(rows).evaluate('A', '2020-02-11')['reason'] == 'invalid_year_ago_base'
    assert RevenueFilter(records()).evaluate('A', '2020-04-01')['reason'] == 'stale_revenue'
    assert not RevenueFilter(records())('MISSING', '2020-02-11')


def test_latest_incomplete_month_does_not_fall_back_to_good_month():
    f = RevenueFilter(records() + [observation('2020-02', 150, '2020-03-10')])
    assert f.evaluate('A', '2020-03-11')['reason'] == 'missing_comparison_month'


def test_conflicting_values_and_invalid_revenue_rejected():
    with pytest.raises(ValueError):
        RevenueFilter(records() + [observation('2020-01', 90, '2020-02-10')])
    for value in [float('nan'), float('inf'), -1, True]:
        with pytest.raises(ValueError):
            RevenueFilter([observation('2020-01', value, '2020-02-10')])


def test_json_requires_version_evidence(tmp_path):
    p = tmp_path / 'revenues.json'
    p.write_text('{"availability_basis":"month_date","records":[]}', encoding='utf-8')
    with pytest.raises(ValueError, match='FinMind'):
        RevenueFilter.from_json(p)


def test_filter_only_controls_new_entries_and_preserves_baseline_when_passing():
    prices, calendar, tables = scenario()
    baseline = simulate_weekly(prices, {}, calendar, tables, False)
    assert simulate_weekly(prices, {}, calendar, tables, False, entry_filter=lambda sid, day: True) == baseline
    rejected = simulate_weekly(prices, {}, calendar, tables, False, entry_filter=lambda sid, day: False)
    assert not rejected[3] and not rejected[4]
    assert all(r['nav_stale'] == 1000000 for r in rejected[0])
    # 後續不再通過濾網，不會因此賣出已買進股票。
    assert simulate_weekly(prices, {}, calendar, tables, False,
                           entry_filter=lambda sid, day: day == '2020-01-08') == baseline
