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
