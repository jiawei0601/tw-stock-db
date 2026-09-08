from argparse import Namespace
from datetime import date
import json
import pytest
import live_momentum as live


def test_official_quotes_reject_wrong_day_and_invalid_high():
    fields=['證券代號','證券名稱','開盤價','最高價','最低價','收盤價','成交股數','成交金額']
    rows=[[str(1000+i),'股票','100','110','90','105','10000','1000000'] for i in range(501)]
    p=dict(date='20260908',tables=[dict(fields=fields,data=rows)])
    assert len(live.parse_quotes(p,'2026-09-08','twse'))==501
    with pytest.raises(ValueError,match='日期'):live.parse_quotes(p,'2026-09-09','twse')
    rows[0][3]='99'
    with pytest.raises(ValueError,match='OHLC'):live.parse_quotes(p,'2026-09-08','twse')


def test_ledger_requires_real_fills_and_never_borrows():
    a=dict(initial_cash=500000,fills=[])
    assert live.positions(a)==(500000,{})
    a['fills']=[dict(stock_id='2330',side='buy',shares=20,price=1000,fees=30,date='2026-09-08')]
    cash,held=live.positions(a)
    assert cash==479970 and held['2330']['shares']==20
    a['fills'].append(dict(stock_id='2330',side='sell',shares=20,price=1100,fees=100,date='2026-09-09'))
    assert live.positions(a)==(501870,{})
    a['fills'][0]['shares']=501
    with pytest.raises(ValueError,match='現金'):live.positions(a)


def test_duplicate_fill_and_nan_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'DATA',tmp_path)
    args=Namespace(id='broker-1',stock_id='2330',side='buy',shares=20,price=1000.,fees=30.,date='2026-09-01')
    live.record_fill(args)
    with pytest.raises(ValueError,match='ID'):live.record_fill(args)
    args.id='broker-2';args.price=float('nan')
    with pytest.raises(ValueError,match='數值'):live.record_fill(args)
    assert len(live.account()['fills'])==1


def test_next_session_skips_weekend_and_holiday(monkeypatch):
    monkeypatch.setattr(live,'holidays',lambda year:{'2026-09-28'})
    assert live.next_session(date(2026,9,25))==date(2026,9,29)


def test_entry_day_does_not_look_at_pre_10am_high(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'DATA',tmp_path)
    ledger=dict(initial_cash=500000,fills=[dict(stock_id='A',side='buy',shares=100,price=100,fees=0,date='2026-09-08')])
    live.atomic(tmp_path/'account.json',ledger)
    cal=['2026-07-31','2026-08-31','2026-09-08','2026-09-09']
    prices={'A':{d:dict(open=100,close=100,max=100,Trading_Volume=10) for d in cal}}
    prices['A']['2026-09-08']['max']=200
    monkeypatch.setattr(live,'load_live',lambda d:(prices,{},cal,{},dict(fingerprint='test')))
    monkeypatch.setattr(live,'signal_table',lambda *a:{})
    monkeypatch.setattr(live,'decisions',lambda *a:([],{}))
    monkeypatch.setattr(live,'next_session',lambda d:date(2026,9,10))
    assert live.report(date(2026,9,9))==''
    prices['A']['2026-09-09'].update(max=150,close=104)
    assert '賣出待執行' in live.report(date(2026,9,9))
    prices['A']['2026-09-09'].update(max=100,close=100)
    assert '賣出待執行' in live.report(date(2026,9,9))  # pending does not vanish on recovery


def test_missed_day_breach_is_replayed_after_recovery(tmp_path,monkeypatch):
    monkeypatch.setattr(live,'DATA',tmp_path)
    live.atomic(tmp_path/'account.json',dict(initial_cash=500000,fills=[dict(stock_id='A',side='buy',shares=100,price=100,fees=0,date='2026-09-07')]))
    cal=['2026-07-31','2026-08-31','2026-09-07','2026-09-08','2026-09-09']
    prices={'A':{d:dict(open=100,close=100,max=100,Trading_Volume=10) for d in cal}}
    prices['A']['2026-09-08'].update(max=150,close=104)
    prices['A']['2026-09-09'].update(open=104,max=120,close=120)
    monkeypatch.setattr(live,'load_live',lambda d:(prices,{},cal,{},dict(fingerprint='test')))
    monkeypatch.setattr(live,'signal_table',lambda *a:{})
    monkeypatch.setattr(live,'decisions',lambda *a:([],{}))
    monkeypatch.setattr(live,'next_session',lambda d:date(2026,9,10))
    assert '訊號2026-09-08' in live.report(date(2026,9,9))
