"""3-1 動態動能與三日成交金額：未認證價格回測，標準庫、唯讀資料。"""
import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path
from momentum_engine import shift_month


def load_data():
    manifest=json.loads(Path('data/momentum_pit/manifest.json').read_text(encoding='utf-8'))
    conn=sqlite3.connect('file:data/momentum_pit/finmind_raw.db?mode=ro',uri=True)
    digest=hashlib.sha256()
    def fetch(dataset,sid=None,ranged=True):
        q={'dataset':dataset}
        if ranged:q.update(start_date=manifest['start'],end_date=manifest['end'])
        if sid is not None:q['data_id']=sid
        key=json.dumps(q,sort_keys=True,separators=(',',':'))
        row=conn.execute('select body,status from responses where query=?',(key,)).fetchone()
        if not row or row[1]!=200:raise ValueError(key)
        digest.update(key.encode());digest.update(row[0].encode())
        return json.loads(row[0])['data']
    calendar=sorted({r['date'] for r in fetch('TaiwanStockTradingDate',ranged=False) if manifest['start']<=r['date']<=manifest['end']})
    prices={sid:{r['date']:r for r in fetch('TaiwanStockPrice',sid)} for sid in manifest['candidate_ids']}
    events={}
    for dataset,before,after in [('TaiwanStockSplitPrice','before_price','after_price'),('TaiwanStockParValueChange','before_close','after_ref_close')]:
        for r in fetch(dataset):
            if r.get(before,0)>0 and r.get(after,0)>0:events[(r['stock_id'],r['date'])]=r[before]/r[after]
    indices={r['date']:r['price'] for r in fetch('TaiwanStockTotalReturnIndex','TAIEX')}
    conn.close()
    observed={d for rows in prices.values() for d,r in rows.items() if r.get('Trading_Volume',0)>0}
    excluded=[d for d in calendar if d not in observed and d not in indices]
    calendar=[d for d in calendar if d not in excluded]
    return prices,events,calendar,indices,digest.hexdigest(),excluded


def signal_table(prices,events,calendar,day):
    past=[d for d in calendar if d<=day]
    ends={d[:7]:d for d in past}
    a,b=ends.get(shift_month(day,-3)),ends.get(shift_month(day,-1))
    history=past[max(0,len(past)-252)] if len(past)>=200 else None
    if not a or not b or not history:return {}
    scores=[]
    for sid,rows in prices.items():
        if any(rows.get(d,{}).get('close',0)<=0 for d in (a,b,day)):continue
        if sum(history<=d<=day and r.get('close',0)>0 and r.get('Trading_Volume',0)>0 for d,r in rows.items())<200:continue
        ratio=math.prod(v for (s,d),v in events.items() if s==sid and a<d<=b)
        score=rows[b]['close']*ratio/rows[a]['close']-1
        recent=[rows.get(d,{}) for d in past[-3:]]
        liquid=len(recent)==3 and all(r.get('Trading_Volume',0)>0 and 'Trading_money' in r for r in recent)
        money=sum(r.get('Trading_money',0) for r in recent)/3
        scores.append((sid,score,liquid and money>100_000_000,money))
    scores.sort(key=lambda x:(-x[1],x[0]))
    return {sid:dict(score=score,rank=i,top=i<=math.ceil(len(scores)*.25),outside=i>math.ceil(len(scores)*.40),liquid=liquid,money=money) for i,(sid,score,liquid,money) in enumerate(scores,1)}


def decisions(current,previous,older,held):
    exits={}
    for sid in held:
        c,p,o=current.get(sid),previous.get(sid),older.get(sid)
        if c is None:exits[sid]='missing_signal'
        elif c['score']<=0:exits[sid]='nonpositive'
        elif c['outside']:exits[sid]='rank_below_40pct'
        elif p and o and c['score']<p['score']<o['score']:exits[sid]='two_declines'
    buys=[sid for sid,c in current.items() if sid not in held and c['top'] and c['liquid'] and c['score']>0 and sid in previous and c['score']>previous[sid]['score']]
    buys.sort(key=lambda sid:(-current[sid]['score'],sid))
    return buys,exits


def simulate(prices,events,calendar,tables,fee=.003,initial_capital=1.,ticket=None,max_positions=None,stop_loss=None):
    cash=initial_capital;held={};trades=[];nav=[];orders=[];pending={};buy_plan=[]
    months=sorted(tables);prev={m:months[i-1] if i else None for i,m in enumerate(months)}
    warmup=[m for m in months if m<'2020-01-01']
    if warmup:
        m=warmup[-1];pm=prev[m];om=prev.get(pm)
        buy_plan,_=decisions(tables[m],tables.get(pm,{}),tables.get(om,{}),[])
    for day in calendar:
        if day<'2020-01-01':continue
        for sid,p in held.items():
            ratio=events.get((sid,day),1)
            p['shares']*=ratio;p['mark']/=ratio
        for sid,reason in list(pending.items()):
            r=prices[sid].get(day,{})
            if r.get('open',0)>0 and r.get('Trading_Volume',0)>0:
                p=held.pop(sid);proceeds=p['shares']*r['open']*(1-fee);cash+=proceeds
                trades.append(dict(stock_id=sid,entry=p['entry'],exit=day,reason=reason,return_net=proceeds/p['cost']-1,days=(date.fromisoformat(day)-date.fromisoformat(p['entry'])).days,cost=p['cost'],proceeds=proceeds,stop_signal_date=p.get('stop_signal_date','')))
                del pending[sid]
        budget=ticket if ticket is not None else (cash/len(buy_plan) if buy_plan else 0)
        for sid in buy_plan:
            r=prices[sid].get(day,{})
            capacity=max_positions is None or len(held)<max_positions
            affordable=cash+1e-8>=budget
            filled=capacity and affordable and budget>1e-12 and r.get('open',0)>0 and r.get('Trading_Volume',0)>0
            orders.append(dict(day=day,stock_id=sid,filled=filled,budget=budget,capacity=capacity,affordable=affordable))
            if filled:
                held[sid]=dict(shares=budget/(r['open']*(1+fee)),mark=r['open'],cost=budget,entry=day)
                cash-=budget
        cash=max(cash,0.)
        buy_plan=[]
        missing=0;value=cash;zero=cash
        for sid,p in held.items():
            r=prices[sid].get(day,{})
            valid=r.get('close',0)>0 and r.get('Trading_Volume',0)>0
            if valid:p['mark']=r['close']
            else:missing+=1
            value+=p['shares']*p['mark']
            if valid:zero+=p['shares']*p['mark']
        nav.append(dict(date=day,nav_stale=value,nav_missing_zero=zero,cash=cash,positions=len(held),missing_marks=missing))
        if day in tables:
            pm=prev[day];om=prev.get(pm)
            buy_plan,sell=decisions(tables[day],tables.get(pm,{}),tables.get(om,{}),held)
            for sid,reason in sell.items():pending.setdefault(sid,reason)
        if stop_loss is not None:
            for sid,p in held.items():
                r=prices[sid].get(day,{})
                if r.get('close',0)>0 and r.get('Trading_Volume',0)>0:
                    liquidation_value=p['shares']*r['close']*(1-fee)
                    if liquidation_value < p['cost']*(1-stop_loss):
                        pending[sid]='stop_loss'
                        p.setdefault('stop_signal_date',day)
    return nav,trades,orders,held,pending


def metrics(nav,key,initial_capital=1.):
    peak=initial_capital;dd=0
    for r in nav:
        peak=max(peak,r[key]);dd=min(dd,r[key]/peak-1)
    years=(date.fromisoformat(nav[-1]['date'])-date.fromisoformat(nav[0]['date'])).days/365.25
    return dict(total=nav[-1][key]/initial_capital-1,cagr=(nav[-1][key]/initial_capital)**(1/years)-1,max_drawdown=dd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixed10',action='store_true',help='100萬元、最多10檔、每筆含成本10萬元；不足10萬元不買')
    parser.add_argument('--stop-loss',type=float,default=None,help='每日收盤淨清算虧損門檻，例如0.10；次交易日開盤出場')
    args=parser.parse_args()
    if args.stop_loss is not None and not 0<args.stop_loss<1:parser.error('--stop-loss 必須介於0與1')
    allocation=dict(initial_capital=1_000_000.,ticket=100_000.,max_positions=10) if args.fixed10 else {}
    if args.stop_loss is not None:allocation['stop_loss']=args.stop_loss
    initial=allocation.get('initial_capital',1.)
    prices,events,calendar,index,fingerprint,excluded=load_data()
    ends={d[:7]:d for d in calendar};tables={}
    # Only completed months; include warmup signals for acceleration and decline checks.
    for month,day in sorted(ends.items()):
        if month<'2019-10' or month>=calendar[-1][:7]:continue
        tables[day]=signal_table(prices,events,calendar,day)
        print(day,len(tables[day]),flush=True)
    check='2022-12-30'
    truncated=signal_table({s:{d:r for d,r in rs.items() if d<=check} for s,rs in prices.items()},{k:v for k,v in events.items() if k[1]<=check},[d for d in calendar if d<=check],check)
    assert tables[check] and tables[check]==truncated
    nav,trades,orders,held,pending=simulate(prices,events,calendar,tables,**allocation)
    prefix_nav=simulate({s:{d:r for d,r in rs.items() if d<=check} for s,rs in prices.items()},{k:v for k,v in events.items() if k[1]<=check},[d for d in calendar if d<=check],{d:t for d,t in tables.items() if d<=check},**allocation)[0]
    assert prefix_nav==[r for r in nav if r['date']<=check]
    sensitivity={str(f):metrics(simulate(prices,events,calendar,tables,fee=f,**allocation)[0],'nav_stale',initial) for f in (.0015,.006)}
    out=Path(('backtest/momentum_dynamic_fixed10' if args.fixed10 else 'backtest/momentum_dynamic')+(f'_stop{args.stop_loss:g}' if args.stop_loss is not None else ''));out.mkdir(exist_ok=True)
    signals=[dict(day=d,stock_id=s,**v) for d,t in tables.items() for s,v in t.items()]
    for name,rows in [('nav',nav),('trades',trades),('orders',orders),('signals',signals)]:
        with (out/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    yearly=[];base=initial
    for year in sorted({r['date'][:4] for r in nav}):
        rows=[r for r in nav if r['date'].startswith(year)];end=rows[-1]['nav_stale']
        yearly.append(dict(year=year,return_net=end/base-1));base=end
    summary=dict(allocation=allocation,ending_equity=nav[-1]['nav_stale'],ending_cash=nav[-1]['cash'],max_positions_observed=max(r['positions'] for r in nav),excluded_unobserved_calendar_dates=excluded,cost_sensitivity=sensitivity,portfolio_prefix_invariance=True,certification='UNVERIFIED_PRICE_EXPLORATION',fingerprint=fingerprint,start=nav[0]['date'],end=nav[-1]['date'],first_fill=next(r['day'] for r in orders if r['filled']),stale_scenario=metrics(nav,'nav_stale',initial),zero_scenario=metrics(nav,'nav_missing_zero',initial),closed_trades=len(trades),win_rate=statistics.mean(t['return_net']>0 for t in trades),median_holding_days=statistics.median(t['days'] for t in trades),mean_holding_days=statistics.mean(t['days'] for t in trades),open_positions=len(held),pending_exits=pending,missing_mark_days=sum(r['missing_marks']>0 for r in nav),max_missing_marks=max(r['missing_marks'] for r in nav),mean_positions=statistics.mean(r['positions'] for r in nav),mean_cash_fraction=statistics.mean(r['cash']/r['nav_stale'] for r in nav),prefix_invariance=True,yearly=yearly,exit_reasons={reason:sum(t['reason']==reason for t in trades) for reason in {t['reason'] for t in trades}},taiex_close_total_return_reference=index[nav[-1]['date']]/index[nav[0]['date']]-1)
    (out/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
