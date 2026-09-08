from weekly_hermes_momentum import hermes_stages,simulate_weekly,moving_averages
from dynamic_momentum import signal_table
from tests.test_dynamic_momentum import row


def test_promotion_is_permanent():
    p=dict(original_shares=1000,cost=100_000*1.003,done=set(),ever20=True)
    assert hermes_stages(p,121,{20:125,60:122,120:130})==[20,60,120]
    assert hermes_stages(p,119,{20:125,60:122,120:130})==[20,60,120]
    p['ever20']=False
    assert hermes_stages(p,130,{20:140})==[]
    p['ever20']=True
    p['done']={20}
    assert hermes_stages(p,121,{20:125,60:122,120:130})==[60,120]


def scenario():
    ds=['2019-11-29','2019-12-31','2020-01-08','2020-01-09','2020-01-10','2020-01-13','2020-01-14']
    prices={'A':{d:dict(open=130 if d>='2020-01-10' else 100,close=130 if d>='2020-01-09' else 100,Trading_Volume=1) for d in ds}}
    tables={'2019-11-29':{'A':row(.1)},'2019-12-31':{'A':row(.1)},'2020-01-08':{'A':row(.2)}}
    return prices,ds,tables


def test_wednesday_next_day_and_partial_once():
    prices,ds,tables=scenario()
    cache={('A',d):{20:140,60:100,120:100} for d in ds}
    nav,trips,legs,orders,held,pending=simulate_weekly(prices,{},ds,tables,True,cache)
    assert orders[0]['date']=='2020-01-09'
    assert len(legs)==1 and legs[0]['exit']=='2020-01-10' and legs[0]['fraction_original']==.5
    assert not trips and held['A']['remaining']==.5 and nav[-1]['positions']==1
    assert abs(nav[-1]['cash']-(900_000+legs[0]['proceeds']))<1e-8


def test_stages_are_original_fractions_and_roundtrip_reconciles():
    prices,ds,tables=scenario()
    cache={('A',d):{20:140,60:100,120:100} for d in ds}
    cache[('A','2020-01-10')]={20:140,60:140,120:100}
    cache[('A','2020-01-13')]={20:140,60:140,120:140}
    nav,trips,legs,orders,held,pending=simulate_weekly(prices,{},ds,tables,True,cache)
    assert [r['fraction_original'] for r in legs]==[.5,.25,.25]
    assert len(trips)==1 and not held
    assert abs(trips[0]['proceeds']-sum(r['proceeds'] for r in legs))<1e-8
    assert abs(nav[-1]['cash']-(1_000_000+trips[0]['proceeds']-100_000))<1e-8


def test_simultaneous_stages_do_not_oversell():
    prices,ds,tables=scenario()
    cache={('A',d):{20:140,60:140,120:140} for d in ds}
    nav,trips,legs,orders,held,pending=simulate_weekly(prices,{},ds,tables,True,cache)
    assert len(legs)==1 and legs[0]['fraction_original']==1 and len(trips)==1


def test_missing_open_locks_partial_order():
    prices,ds,tables=scenario()
    prices['A']['2020-01-10']['open']=0
    cache={('A',d):{20:140,60:100,120:100} for d in ds}
    nav,trips,legs,orders,held,pending=simulate_weekly(prices,{},ds,tables,True,cache)
    assert legs[0]['signal_date']=='2020-01-09' and legs[0]['exit']=='2020-01-13'
    assert next(r for r in nav if r['date']=='2020-01-10')['cash']==900_000


def test_calendar_missing_ma_and_split_are_point_in_time():
    ds=[f'2020-01-{i:02}' for i in range(1,22)]
    prices={'A':{d:dict(close=100 if i<20 else 50,Trading_Volume=1) for i,d in enumerate(ds)}}
    event={('A',ds[-1]):2,('A','2021-01-01'):100}
    assert moving_averages(prices,event,ds,ds[-1],'A')[20]==50
    del prices['A'][ds[-2]]
    assert 20 not in moving_averages(prices,event,ds,ds[-1],'A')


def observation_scenario(last='2020-04-13',peak=False):
    from datetime import date,timedelta
    ds=[];d=date(2019,11,1)
    while d<=date.fromisoformat(last):
        if d.weekday()<5:ds.append(d.isoformat())
        d+=timedelta(days=1)
    prices={'A':{d:dict(open=100,close=100,Trading_Volume=1) for d in ds}}
    if peak:prices['A']['2020-02-03']['close']=121
    tables={'2019-11-29':{'A':row(.1)},'2019-12-31':{'A':row(.1)},'2020-01-08':{'A':row(.2)}}
    return prices,ds,tables


def test_90_calendar_days_exit_next_open():
    prices,ds,tables=observation_scenario()
    result=simulate_weekly(prices,{},ds,tables,False,observation_days=90)
    assert result[1][0]['entry']=='2020-01-09'
    assert result[2][0]['signal_date']=='2020-04-08'
    assert result[2][0]['exit']=='2020-04-09' and result[2][0]['reason']=='observation_90'


def test_once_20_exempts_observation_even_after_falling_back():
    prices,ds,tables=observation_scenario(peak=True)
    result=simulate_weekly(prices,{},ds,tables,False,observation_days=90)
    assert not result[1] and result[4]['A']['ever20']


def test_threshold_reached_on_deadline_exempts_expiry():
    prices,ds,tables=observation_scenario()
    prices['A']['2020-04-08']['close']=121
    result=simulate_weekly(prices,{},ds,tables,False,observation_days=90)
    assert not result[1] and result[4]['A']['ever20']


def test_nontrading_deadline_executes_first_open_after_it():
    prices,ds,tables=observation_scenario()
    ds.remove('2020-04-08')
    result=simulate_weekly(prices,{},ds,tables,False,observation_days=90)
    assert result[2][0]['signal_date']=='2020-04-08' and result[2][0]['exit']=='2020-04-09'


def test_future_peak_does_not_rescue_expired_position():
    prices,ds,tables=observation_scenario()
    prices['A']['2020-04-09']['close']=200
    result=simulate_weekly(prices,{},ds,tables,False,observation_days=90)
    assert result[1][0]['exit']=='2020-04-09'


def test_promoted_position_exits_at_cost_even_below20():
    prices,ds,tables=scenario()
    prices['A']['2020-01-10']['close']=99
    cache={('A',d):{20:80,60:80,120:80} for d in ds}
    nav,trips,legs,orders,held,pending=simulate_weekly(prices,{},ds,tables,True,cache,90)
    assert trips[0]['exit']=='2020-01-13' and trips[0]['last_reason']=='cost_floor'


def test_sop_observation_stop_is_price_not_net_cost_threshold():
    prices,ds,tables=scenario()
    prices['A']['2020-01-09']['close']=100
    prices['A']['2020-01-10']['close']=90.2
    prices['A']['2020-01-13']['close']=89
    cache={('A',d):{} for d in ds}
    nav,trips,legs,orders,held,pending=simulate_weekly(prices,{},ds,tables,True,cache,90)
    assert legs[0]['signal_date']=='2020-01-13' and legs[0]['exit']=='2020-01-14'


def test_sop_ignores_old_monthly_momentum_exit():
    prices,ds,tables=observation_scenario(last='2020-02-04')
    tables['2020-01-31']={'A':row(-.1)}
    result=simulate_weekly(prices,{},ds,tables,True,observation_days=90)
    assert not result[1] and 'A' in result[4]


def test_fast_signal_matches_original():
    from tests.test_momentum_engine import fixture
    data=fixture();prices=data['prices'];calendar=data['calendar']
    for rs in prices.values():
        for r in rs.values():r.update(Trading_Volume=1,Trading_money=100_000_001)
    valid={s:sorted(rs) for s,rs in prices.items()}
    assert signal_table(prices,{},calendar,'2019-12-31',valid)==signal_table(prices,{},calendar,'2019-12-31')


def test_executed_later_ma_never_rechecks_earlier_ma():
    p=dict(ever20=True,done={60})
    assert hermes_stages(p,130,{20:150,60:150,120:150})==[120]


def test_diagnostic_momentum_switch_retains_old_exit_with_sop():
    prices,ds,tables=observation_scenario(last='2020-02-04')
    tables['2020-01-31']={'A':row(-.1)}
    result=simulate_weekly(prices,{},ds,tables,True,observation_days=90,momentum_exit=True)
    assert result[1][0]['exit']=='2020-02-03' and result[1][0]['last_reason']=='nonpositive'


def test_diagnostic_ma_disable_leaves_promoted_position_intact():
    prices,ds,tables=scenario()
    cache={('A',d):{20:140,60:140,120:140} for d in ds}
    result=simulate_weekly(prices,{},ds,tables,True,cache,90,ma_exit=False)
    assert not result[1] and not result[2] and result[4]['A']['remaining']==1


def test_equity_allocation_has_no_ten_stock_or_ticket_limit():
    prices,ds,tables=scenario()
    prices={str(i):{d:dict(r,close=100,open=100) for d,r in prices['A'].items()} for i in range(20)}
    tables={d:{s:row(.1 if d<'2020-01-01' else .2) for s in prices} for d in ['2019-11-29','2019-12-31','2020-01-08']}
    result=simulate_weekly(prices,{},ds,tables,True,observation_days=90,equity_allocation=True)
    assert len(result[4])==20
    assert all(abs(p['cost']-50_000)<1e-8 for p in result[4].values())
    assert min(r['cash'] for r in result[0])>=0


def test_equity_ticket_can_exceed_old_100k_limit():
    prices,ds,tables=scenario()
    cache={('A',d):{} for d in ds}
    result=simulate_weekly(prices,{},ds,tables,True,cache,90,equity_allocation=True)
    assert result[4]['A']['cost']==1_000_000


def test_equity_new_targets_use_signal_nav_and_cash_cap():
    ds=['2019-11-29','2019-12-31','2020-01-08','2020-01-09','2020-01-15','2020-01-16','2020-01-17']
    prices={s:{d:dict(open=100,close=100,Trading_Volume=1) for d in ds} for s in ['A','B','C']}
    # First selection A+B, B cannot fill: unused budget remains cash.
    prices['B']['2020-01-09']['open']=0
    tables={'2019-11-29':{s:row(.1) for s in prices},'2019-12-31':{s:row(.1) for s in prices},'2020-01-08':{'A':row(.2),'B':row(.2)},'2020-01-15':{'A':row(.2),'B':row(.2),'C':row(.3)}}
    result=simulate_weekly(prices,{},ds,tables,True,observation_days=90,equity_allocation=True)
    assert result[4]['A']['cost']==500_000
    assert result[4]['B']['cost']==250_000 and result[4]['C']['cost']==250_000
    assert result[0][-1]['cash']==0


def test_equity_target_uses_wednesday_nav_not_next_open():
    ds=['2019-11-29','2019-12-31','2020-01-08','2020-01-09','2020-01-15','2020-01-16']
    prices={s:{d:dict(open=100,close=100,Trading_Volume=1) for d in ds} for s in ['A','B','C']}
    prices['B']['2020-01-09']['open']=0
    prices['C']['2020-01-16'].update(open=200,close=200)
    tables={'2019-11-29':{s:row(.1) for s in prices},'2019-12-31':{s:row(.1) for s in prices},'2020-01-08':{'A':row(.2),'B':row(.2)},'2020-01-15':{'A':row(.2),'C':row(.3)}}
    result=simulate_weekly(prices,{},ds,tables,True,observation_days=90,equity_allocation=True)
    expected=(500_000+500_000/1.003)/2
    assert abs(result[4]['C']['cost']-expected)<1e-8
    assert result[4]['A']['cost']==500_000 and result[0][-1]['cash']>0


def test_equity_momentum_exit_ignores_observation_and_ma():
    prices,ds,tables=observation_scenario(last='2020-04-15',peak=True)
    cache={('A',d):{20:150,60:150,120:150} for d in ds}
    result=simulate_weekly(prices,{},ds,tables,False,cache,equity_allocation=True)
    assert not result[2] and result[4]['A']['cost']==1_000_000
    tables['2020-03-31']={'A':row(-.1)}
    result=simulate_weekly(prices,{},ds,tables,False,cache,equity_allocation=True)
    assert result[2][0]['reason']=='nonpositive' and result[2][0]['exit']=='2020-04-01'


def test_equity_momentum_daily_net_stop_still_active():
    prices,ds,tables=scenario()
    prices['A']['2020-01-09']['close']=90.5
    result=simulate_weekly(prices,{},ds,tables,False,equity_allocation=True)
    assert result[2][0]['reason']=='stop_loss'
    assert result[2][0]['signal_date']=='2020-01-09' and result[2][0]['exit']=='2020-01-10'


def test_five_percent_initial_tickets_no_borrow_or_partial_ticket():
    prices,ds,tables=scenario()
    prices={str(i):{d:dict(r,open=100,close=100) for d,r in prices['A'].items()} for i in range(25)}
    tables={d:{s:row(.1 if d<'2020-01-01' else .2+int(s)/1000) for s in prices} for d in ['2019-11-29','2019-12-31','2020-01-08']}
    result=simulate_weekly(prices,{},ds,tables,False,equity_allocation=True,position_weight=.05)
    assert len(result[4])==20 and all(p['cost']==50_000 for p in result[4].values())
    assert set(result[4])=={str(i) for i in range(5,25)}
    assert result[0][-1]['cash']==0


def test_weight_uses_new_signal_nav_and_does_not_rebalance_old():
    ds=['2019-11-29','2019-12-31','2020-01-08','2020-01-09','2020-01-15','2020-01-16']
    prices={s:{d:dict(open=100,close=100,Trading_Volume=1) for d in ds} for s in ['A','B']}
    prices['A']['2020-01-15']['close']=200
    prices['B']['2020-01-16'].update(open=300,close=300)
    tables={'2019-11-29':{'A':row(.1),'B':row(.1)},'2019-12-31':{'A':row(.1),'B':row(.1)},'2020-01-08':{'A':row(.2)},'2020-01-15':{'A':row(.2),'B':row(.3)}}
    result=simulate_weekly(prices,{},ds,tables,False,equity_allocation=True,position_weight=.05)
    target=(950_000+50_000/1.003*2)*.05
    assert result[4]['A']['cost']==50_000
    assert abs(result[4]['B']['cost']-target)<1e-8
