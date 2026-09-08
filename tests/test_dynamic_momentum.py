from dynamic_momentum import decisions,simulate,signal_table
from tests.test_momentum_engine import fixture


def row(score,liquid=True,top=True,outside=False):
    return dict(score=score,liquid=liquid,top=top,outside=outside)


def test_entry_requires_acceleration_and_liquidity():
    current={'A':row(.2),'B':row(.2,False),'C':row(.1),'D':row(.3,top=False)}
    previous={s:row(.1) for s in current}
    assert decisions(current,previous,{},[])[0]==['A']


def test_two_declines_and_other_exit_conditions():
    current={'A':row(.1),'B':row(0),'C':row(.2,outside=True),'D':row(.1)}
    previous={'A':row(.2),'D':row(.2)}
    older={'A':row(.3),'D':row(.1)}
    buys,exits=decisions(current,previous,older,['A','B','C','D','E'])
    assert exits=={'A':'two_declines','B':'nonpositive','C':'rank_below_40pct','E':'missing_signal'}


def test_next_session_execution_and_costs():
    days=['2020-01-02','2020-01-03','2020-01-06','2020-01-07']
    prices={'A':{d:dict(open=100,close=100,Trading_Volume=1) for d in days}}
    tables={'2019-12-31':{'A':row(.1)},days[0]:{'A':row(.2)},days[2]:{'A':row(-.1)}}
    nav,trades,orders,held,pending=simulate(prices,{},days,tables)
    assert nav[0]['positions']==0
    assert orders[0]['day']==days[1]
    assert trades[0]['exit']==days[3]
    assert abs(nav[-1]['nav_stale']-.997/1.003)<1e-12


def test_missing_exit_cash_stays_locked():
    days=['2020-01-02','2020-01-03','2020-01-06','2020-01-07']
    prices={'A':{d:dict(open=100,close=100,Trading_Volume=1) for d in days[:-1]}}
    tables={'2019-12-31':{'A':row(.1)},days[0]:{'A':row(.2)},days[2]:{'A':row(-.1)}}
    nav,trades,orders,held,pending=simulate(prices,{},days,tables)
    assert not trades and 'A' in pending and 'A' in held
    assert nav[-1]['cash']==0 and nav[-1]['nav_missing_zero']==0


def test_money_threshold_and_future_invariance():
    data=fixture();prices=data['prices'];calendar=data['calendar'];day='2019-12-31'
    for rs in prices.values():
        for r in rs.values():r.update(Trading_Volume=1,Trading_money=100_000_000)
    table=signal_table(prices,{},calendar,day)
    assert table and all(not r['liquid'] for r in table.values())
    for rs in prices.values():
        for d,r in rs.items():
            if d>day:r.update(close=99999,Trading_money=999999999)
    assert signal_table(prices,{},calendar,day)==table
    recent=[d for d in calendar if d<=day][-3:]
    sid=next(iter(table))
    prices[sid][recent[-1]]['Trading_money']+=1
    assert signal_table(prices,{},calendar,day)[sid]['liquid']
    del prices[sid][recent[-2]]
    assert not signal_table(prices,{},calendar,day)[sid]['liquid']


def test_warmup_signal_can_buy_first_day_of_2020():
    days=['2020-01-02']
    prices={'A':{days[0]:dict(open=100,close=100,Trading_Volume=1)}}
    tables={'2019-11-29':{'A':row(.1)},'2019-12-31':{'A':row(.2)}}
    nav,trades,orders,held,pending=simulate(prices,{},days,tables)
    assert orders[0]['day']=='2020-01-02' and held['A']['entry']=='2020-01-02'


def test_split_preserves_nav_and_realized_return():
    days=['2020-01-02','2020-01-03','2020-01-06']
    prices={'A':{days[0]:dict(open=100,close=100,Trading_Volume=1),days[1]:dict(open=50,close=50,Trading_Volume=1),days[2]:dict(open=50,close=50,Trading_Volume=1)}}
    tables={'2019-11-29':{'A':row(.1)},'2019-12-31':{'A':row(.2)},days[1]:{'A':row(-.1)}}
    nav,trades,orders,held,pending=simulate(prices,{('A',days[1]):2},days,tables)
    assert abs(nav[0]['nav_stale']-nav[1]['nav_stale'])<1e-12
    assert abs(trades[0]['return_net']-(.997/1.003-1))<1e-12


def test_fixed_tickets_max_positions_and_priority():
    days=['2020-01-02']
    prices={str(i):{days[0]:dict(open=100,close=100,Trading_Volume=1)} for i in range(12)}
    tables={'2019-11-29':{s:row(.01) for s in prices},'2019-12-31':{s:row(.1+int(s)/100) for s in prices}}
    nav,trades,orders,held,pending=simulate(prices,{},days,tables,initial_capital=1_000_000,ticket=100_000,max_positions=10)
    assert len(held)==10 and set(held)=={str(i) for i in range(2,12)}
    assert all(p['cost']==100_000 for p in held.values()) and nav[-1]['cash']==0


def test_insufficient_cash_does_not_create_smaller_ticket():
    days=['2020-01-02']
    prices={'A':{days[0]:dict(open=100,close=100,Trading_Volume=1)}}
    tables={'2019-11-29':{'A':row(.1)},'2019-12-31':{'A':row(.2)}}
    nav,trades,orders,held,pending=simulate(prices,{},days,tables,initial_capital=99_999,ticket=100_000,max_positions=10)
    assert not held and nav[-1]['cash']==99_999


def test_currency_metrics_use_initial_capital():
    from dynamic_momentum import metrics
    nav=[dict(date='2020-01-02',nav_stale=900_000),dict(date='2021-01-02',nav_stale=1_100_000)]
    result=metrics(nav,'nav_stale',1_000_000)
    assert abs(result['total']-.1)<1e-12 and abs(result['max_drawdown']+.1)<1e-12


def test_locked_holding_counts_against_limit_and_profit_ticket_stays_fixed():
    days=['2020-01-02','2020-01-03','2020-01-06']
    prices={s:{d:dict(open=100,close=100,Trading_Volume=1) for d in days} for s in ['A','B']}
    del prices['A'][days[-1]]
    tables={'2019-11-29':{'A':row(.1),'B':row(.1)},'2019-12-31':{'A':row(.2),'B':row(.1)},days[1]:{'A':row(-.1),'B':row(.3)}}
    nav,trades,orders,held,pending=simulate(prices,{},days,tables,initial_capital=200_000,ticket=100_000,max_positions=1)
    assert set(held)=={'A'} and pending and nav[-1]['cash']==100_000
    prices['A'][days[-1]]=dict(open=200,close=200,Trading_Volume=1)
    nav,trades,orders,held,pending=simulate(prices,{},days,tables,initial_capital=200_000,ticket=100_000,max_positions=1)
    assert set(held)=={'B'} and held['B']['cost']==100_000 and nav[-1]['cash']>190_000
