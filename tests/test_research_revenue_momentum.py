import pytest

from research_revenue_momentum import SnapshotGate


def values():
    return {('A','2019-12'):110,('A','2018-12'):100,('A','2020-01'):120,('A','2019-01'):100}


def test_assumed_availability_is_explicit_and_strictly_before_signal():
    gate=SnapshotGate(values(),15)
    assert gate.evaluate('A','2020-02-15')['revenue_month']=='2019-12'
    assert gate.evaluate('A','2020-02-16')['passed']
    assert SnapshotGate(values(),30).evaluate('A','2020-02-16')['revenue_month']=='2019-12'


def test_coverage_control_and_growth_use_identical_required_data():
    rows=values();rows['A','2020-01']=105
    control=SnapshotGate(rows,15,False).evaluate('A','2020-02-16')
    filtered=SnapshotGate(rows,15,True).evaluate('A','2020-02-16')
    assert control['passed'] and not filtered['passed']
    assert control['coverage'] and filtered['coverage']
    assert control['yoy']==filtered['yoy']==pytest.approx(.05)


def test_missing_latest_month_does_not_reuse_stale_favorable_month():
    rows=values();del rows['A','2020-01']
    assert not SnapshotGate(rows,15,False)('A','2020-02-16')


def test_future_month_values_do_not_change_past_decision():
    rows=values();rows['A','2020-03']=10000000
    assert SnapshotGate(rows,15).evaluate('A','2020-02-16')==SnapshotGate(values(),15).evaluate('A','2020-02-16')


def test_nonpositive_year_ago_base_is_not_eligible_for_either_arm():
    rows=values();rows['A','2019-01']=0
    assert not SnapshotGate(rows,15,False)('A','2020-02-16')
    assert not SnapshotGate(rows,15,True)('A','2020-02-16')


def three_values():
    return {**values(), ('A','2019-11'):100}


@pytest.mark.parametrize('amounts,year_ago,expected', [
    ((100,110,120),100,True),
    ((100,110,110),100,False),
    ((110,110,120),100,False),
    ((110,100,120),100,False),
    ((100,110,120),120,False),
    ((100,110,120),130,False),
    ((100,110,120),0,False),
])
def test_three_observations_strictly_increase_and_latest_yoy_positive(amounts, year_ago, expected):
    rows = three_values()
    for month, amount in zip(('2019-11','2019-12','2020-01'), amounts):
        rows['A',month] = amount
    rows['A','2019-01'] = year_ago
    assert SnapshotGate(rows,15,rule='three_month_revenue')('A','2020-02-16') is expected


def test_revenue_rising_can_pass_while_yoy_decelerates():
    rows = three_values(); rows['A','2018-12'] = 50
    assert SnapshotGate(rows,15,rule='three_month_revenue')('A','2020-02-16')
    assert not SnapshotGate(rows,15)('A','2020-02-16')


def test_three_month_rule_does_not_need_fourth_month_or_previous_year_base():
    rows = three_values(); del rows['A','2018-12']
    assert SnapshotGate(rows,15,rule='three_month_revenue')('A','2020-02-16')
    assert not SnapshotGate(rows,15,rule='three_month_revenue',common_coverage=True)('A','2020-02-16')


@pytest.mark.parametrize('missing', ['2019-11','2019-12','2020-01','2018-12','2019-01'])
def test_common_coverage_both_rules_reject_any_missing_required_month(missing):
    rows = three_values(); del rows['A',missing]
    for rule in ('three_month_revenue','yoy_acceleration'):
        assert not SnapshotGate(rows,15,False,rule=rule,common_coverage=True)('A','2020-02-16')


def test_three_month_lag_and_future_values_do_not_leak():
    rows = three_values()
    gate = SnapshotGate(rows,15,rule='three_month_revenue')
    assert gate.evaluate('A','2020-02-15')['revenue_month'] == '2019-12'
    before = gate.evaluate('A','2020-02-16')
    rows['A','2020-02'] = 99999
    assert gate.evaluate('A','2020-02-16') == before
    assert SnapshotGate(rows,30,rule='three_month_revenue').evaluate('A','2020-03-01')['revenue_month'] == '2019-12'
    assert SnapshotGate(rows,30,rule='three_month_revenue').evaluate('A','2020-03-02')['passed']
