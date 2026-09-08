"""Hermes 均線分批出場 + 每週三選股；未認證價格研究。"""
import argparse
import csv
import json
import math
import statistics
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path
from dynamic_momentum import load_data, signal_table, decisions, metrics
from momentum_engine import shift_month

STAGES={20:.5,60:.25,120:.25}


def moving_averages(prices,events,calendar,day,sid):
    """只用截至當日120個市場交易日；分割換算為當日股份單位。"""
    stop=bisect_right(calendar,day)
    ds=calendar[max(0,stop-120):stop]
    values=[]
    for d in ds:
        r=prices[sid].get(d,{})
        if r.get('close',0)<=0 or r.get('Trading_Volume',0)<=0:values.append(None)
        else:
            ratio=math.prod(v for (s,e),v in events.items() if s==sid and d<e<=day)
            values.append(r['close']/ratio)
    return {n:statistics.mean(values[-n:]) for n in STAGES if len(values)>=n and all(v is not None for v in values[-n:])}


def hermes_stages(position,close,averages):
    # Hetzner SOP v3: promotion is permanent, unlike the obsolete local template.
    if not position['ever20']:return []
    return [n for n in STAGES if n>max(position['done'],default=0) and n in averages and close<averages[n]]


def simulate_weekly(prices,events,calendar,tables,trailing=True,ma_cache=None,observation_days=None,*,momentum_exit=None,net_stop=None,cost_floor=None,ma_exit=None,equity_allocation=False,position_weight=None):
    momentum_exit=(not trailing) if momentum_exit is None else momentum_exit
    net_stop=(not trailing) if net_stop is None else net_stop
    cost_floor=trailing if cost_floor is None else cost_floor
    ma_exit=trailing if ma_exit is None else ma_exit
    cash=1_000_000.;held={};pending={};buy_plan=[];plan_target=0.;nav=[];legs=[];roundtrips=[];orders=[]
    ends={d[:7]:d for d in calendar}
    ma_cache={} if ma_cache is None else ma_cache
    for day in calendar:
        if day<'2020-01-01':continue
        for sid,p in held.items():
            ratio=events.get((sid,day),1.)
            p['original_shares']*=ratio;p['mark']/=ratio
        if observation_days is not None:
            for sid,p in held.items():
                deadline=(date.fromisoformat(p['entry'])+timedelta(days=observation_days)).isoformat()
                if day>deadline and not p['ever20'] and not pending.get(sid,{}).get('full'):
                    pending[sid]=dict(full=True,stages=[],reason='observation_90',signal=deadline)
        for sid,order in list(pending.items()):
            r=prices[sid].get(day,{})
            if r.get('open',0)<=0 or r.get('Trading_Volume',0)<=0:continue
            p=held[sid]
            fraction=p['remaining'] if order['full'] else min(p['remaining'],sum(STAGES[n] for n in order['stages']))
            proceeds=p['original_shares']*fraction*r['open']*.997
            cash+=proceeds;p['proceeds']+=proceeds;p['remaining']-=fraction
            p['done'].update(order['stages'])
            legs.append(dict(stock_id=sid,entry=p['entry'],signal_date=order['signal'],exit=day,reason=order['reason'],fraction_original=fraction,cost=p['cost']*fraction,proceeds=proceeds,pnl=proceeds-p['cost']*fraction,exit_open=r['open']))
            del pending[sid]
            if p['remaining']<1e-9:
                roundtrips.append(dict(stock_id=sid,entry=p['entry'],exit=day,cost=p['cost'],proceeds=p['proceeds'],return_net=p['proceeds']/p['cost']-1,days=(date.fromisoformat(day)-date.fromisoformat(p['entry'])).days,last_reason=order['reason']))
                del held[sid]
        budget=plan_target if position_weight is not None else (min(plan_target,cash/len(buy_plan)) if equity_allocation and buy_plan else 100_000.)
        for sid in buy_plan:
            r=prices[sid].get(day,{})
            filled=(equity_allocation or position_weight is not None or len(held)<10) and budget>1e-8 and cash+1e-8>=budget and r.get('open',0)>0 and r.get('Trading_Volume',0)>0
            orders.append(dict(date=day,stock_id=sid,filled=filled,budget=budget))
            if filled:
                cash-=budget
                held[sid]=dict(entry=day,cost=budget,original_shares=budget/(r['open']*1.003),remaining=1.,done=set(),mark=r['open'],proceeds=0.,ever20=False)
        buy_plan=[];cash=max(cash,0.)
        value=zero=cash;missing=0
        for sid,p in held.items():
            r=prices[sid].get(day,{})
            valid=r.get('close',0)>0 and r.get('Trading_Volume',0)>0
            if valid:p['mark']=r['close']
            else:missing+=1
            marked=p['original_shares']*p['remaining']*p['mark']
            value+=marked
            if valid:zero+=marked
        nav.append(dict(date=day,nav_stale=value,nav_missing_zero=zero,cash=cash,positions=len(held),missing_marks=missing))
        prior=ends.get(shift_month(day,-1));older=ends.get(shift_month(day,-2))
        current=tables.get(day,{})
        if day in tables:
            buys,exits=decisions(current,tables.get(prior,{}),tables.get(older,{}),held)
            if date.fromisoformat(day).weekday()==2:
                buy_plan=buys
                plan_target=value*position_weight if position_weight is not None else (value/(len(held)+len(buys)) if buys else 0.)
            if momentum_exit and day==ends[day[:7]]:
                for sid,reason in exits.items():
                    if sid not in pending or not pending[sid]['full']:
                        pending[sid]=dict(full=True,stages=[],reason=reason,signal=day)
        for sid,p in held.items():
            r=prices[sid].get(day,{})
            if r.get('close',0)<=0 or r.get('Trading_Volume',0)<=0:continue
            deadline=(date.fromisoformat(p['entry'])+timedelta(days=observation_days)).isoformat() if observation_days is not None else None
            if (deadline is None or day<=deadline) and p['original_shares']*r['close'] >= (p['cost']/1.003)*1.20:
                p['ever20']=True
            # SOP thresholds use raw entry cost price; transaction costs affect P&L only.
            price_value=p['original_shares']*r['close']
            basis=p['cost']/1.003
            breach=(price_value < basis) if cost_floor and p['ever20'] else ((price_value*.997 < p['cost']*.90) if net_stop else (price_value < basis*.90))
            if breach:
                reason='cost_floor' if cost_floor and p['ever20'] else 'stop_loss'
                if pending.get(sid,{}).get('reason')!=reason:
                    pending[sid]=dict(full=True,stages=[],reason=reason,signal=day)
                continue
            if deadline is not None and day>=deadline and not p['ever20'] and not pending.get(sid,{}).get('full'):
                pending[sid]=dict(full=True,stages=[],reason='observation_90',signal=day)
            if not ma_exit or not p['ever20'] or pending.get(sid,{}).get('full'):continue
            key=(sid,day)
            if key not in ma_cache:ma_cache[key]=moving_averages(prices,events,calendar,day,sid)
            stages=hermes_stages(p,r['close'],ma_cache[key])
            existing=pending.get(sid)
            if existing:stages=sorted(set(stages)|set(existing['stages']))
            if stages:
                pending[sid]=dict(full=False,stages=stages,reason='ma_'+ '_'.join(map(str,stages)),signal=existing['signal'] if existing else day)
    return nav,roundtrips,legs,orders,held,pending


def summarize(result):
    nav,trades,legs,orders,held,pending=result
    yearly=[];base=1_000_000
    for y in sorted({r['date'][:4] for r in nav}):
        end=[r for r in nav if r['date'].startswith(y)][-1]['nav_stale']
        yearly.append(dict(year=y,return_net=end/base-1));base=end
    return dict(start=nav[0]['date'],end=nav[-1]['date'],first_fill=next((r['date'] for r in orders if r['filled']),None),ending_equity=nav[-1]['nav_stale'],ending_cash=nav[-1]['cash'],stale_scenario=metrics(nav,'nav_stale',1_000_000),zero_scenario=metrics(nav,'nav_missing_zero',1_000_000),closed_roundtrips=len(trades),sell_legs=len(legs),win_rate=statistics.mean(t['return_net']>0 for t in trades) if trades else None,mean_holding_days=statistics.mean(t['days'] for t in trades) if trades else None,mean_positions=statistics.mean(r['positions'] for r in nav),max_positions=max(r['positions'] for r in nav),mean_cash_fraction=statistics.mean(r['cash']/r['nav_stale'] for r in nav),missing_mark_days=sum(r['missing_marks']>0 for r in nav),open_positions=len(held),pending=pending,yearly=yearly,exit_legs_by_reason={s:sum(t['reason']==s for t in legs) for s in sorted({t['reason'] for t in legs})})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--equity-allocation',action='store_true',help='無檔數上限，新倉按訊號日淨值等權目標、現金同比縮小')
    parser.add_argument('--momentum-exit',action='store_true',help='月底動能出場＋每日淨虧損10%停損，取消Hermes分批與90天期限')
    parser.add_argument('--position-weight',type=float,default=None,help='每筆新倉占訊號日淨值比例，例如0.05；不足整筆金額不買')
    args=parser.parse_args()
    if args.position_weight is not None and not 0<args.position_weight<=1:parser.error('--position-weight 必須介於0與1')
    exit_options=dict(trailing=not args.momentum_exit,observation_days=None if args.momentum_exit else 90,equity_allocation=args.equity_allocation,position_weight=args.position_weight)
    prices,events,calendar,index,fingerprint,excluded=load_data()
    ends={d[:7]:d for d in calendar}
    valid={s:sorted(d for d,r in rows.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for s,rows in prices.items()}
    tables={}
    for day in calendar:
        if day<'2019-10-01':continue
        if date.fromisoformat(day).weekday()==2 or (day==ends[day[:7]] and day[:7]<calendar[-1][:7]):
            tables[day]=signal_table(prices,events,calendar,day,valid)
    print('訊號準備完成',len(tables),flush=True)
    cache={}
    baseline=simulate_weekly(prices,events,calendar,tables,False,cache)
    result=simulate_weekly(prices,events,calendar,tables,ma_cache=cache,**exit_options)
    check='2022-12-30'
    prefix_prices={s:{d:r for d,r in rs.items() if d<=check} for s,rs in prices.items()}
    prefix_events={k:v for k,v in events.items() if k[1]<=check}
    prefix_calendar=[d for d in calendar if d<=check]
    prefix_tables={d:t for d,t in tables.items() if d<=check}
    assert tables[check]==signal_table(prefix_prices,prefix_events,prefix_calendar,check)
    prefix=simulate_weekly(prefix_prices,prefix_events,prefix_calendar,prefix_tables,**exit_options)
    assert prefix[0]==[r for r in result[0] if r['date']<=check]
    assert prefix[2]==[r for r in result[2] if r['exit']<=check]
    out=Path(('backtest/momentum_weekly_hermes_equity' if args.equity_allocation else 'backtest/momentum_weekly_hermes')+('_momentum_exit' if args.momentum_exit else '')+(f'_weight{args.position_weight:g}' if args.position_weight is not None else ''));out.mkdir(exist_ok=True)
    for name,rows in zip(['nav','roundtrips','sell_legs','orders'],result[:4]):
        with (out/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            if rows:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'holdings.json').write_text(json.dumps(result[4],default=lambda x:sorted(x),indent=2),encoding='utf-8')
    summary=dict(exit_options=exit_options,equity_allocation=args.equity_allocation,certification='UNVERIFIED_PRICE_EXPLORATION',fingerprint=fingerprint,excluded_dates=excluded,prefix_invariance=True,weekly_stop_only=summarize(baseline),**{'weekly_momentum_exit' if args.momentum_exit else 'weekly_hermes90':summarize(result)})
    (out/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
