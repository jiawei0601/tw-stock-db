"""固定相同輸入，逐項拆解兩版績效落差；不是參數最佳化。"""
import csv,json
from collections import defaultdict
from datetime import date
from pathlib import Path
from dynamic_momentum import load_data,signal_table,simulate,metrics
from weekly_hermes_momentum import simulate_weekly,summarize


def main():
    prices,events,calendar,index,fingerprint,excluded=load_data()
    ends={d[:7]:d for d in calendar}
    valid={s:sorted(d for d,r in rs.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for s,rs in prices.items()}
    tables={d:signal_table(prices,events,calendar,d,valid) for d in calendar if d>='2019-10-01' and (date.fromisoformat(d).weekday()==2 or (d==ends[d[:7]] and d[:7]<calendar[-1][:7]))}
    monthly={d:t for d,t in tables.items() if d==ends[d[:7]] and d[:7]<calendar[-1][:7]}
    old=simulate(prices,events,calendar,monthly,initial_capital=1_000_000,ticket=100_000,max_positions=10,stop_loss=.1)
    configs=[('weekly_old',dict(trailing=False)),
        ('price_stop',dict(trailing=False,net_stop=False)),
        ('remove_momentum_exit',dict(trailing=False,net_stop=False,momentum_exit=False)),
        ('add_cost_floor',dict(trailing=False,net_stop=False,momentum_exit=False,cost_floor=True)),
        ('add_ma',dict(trailing=True)),
        ('final_90',dict(trailing=True,observation_days=90)),
        ('final_keep_momentum',dict(trailing=True,observation_days=90,momentum_exit=True)),
        ('final_without_ma',dict(trailing=True,observation_days=90,ma_exit=False)),
        ('final_without_cost_floor',dict(trailing=True,observation_days=90,cost_floor=False)),
        ('final_net_stop',dict(trailing=True,observation_days=90,net_stop=True))]
    cache={};results={};summaries={}
    for name,config in configs:
        result=simulate_weekly(prices,events,calendar,tables,ma_cache=cache,**config)
        results[name]=result;summaries[name]=summarize(result)
        print(name,summaries[name]['ending_equity'],flush=True)
    final=results['final_90']
    stored=json.loads(Path('backtest/momentum_weekly_hermes/result.json').read_text(encoding='utf-8'))
    oldstored=json.loads(Path('backtest/momentum_dynamic_fixed10_stop0.1/result.json').read_text(encoding='utf-8'))
    assert abs(final[0][-1]['nav_stale']-stored['weekly_hermes90']['ending_equity'])<.01
    assert abs(old[0][-1]['nav_stale']-oldstored['ending_equity'])<.01
    assert fingerprint==stored['fingerprint']==oldstored['fingerprint']
    oldpnl=defaultdict(float);newpnl=defaultdict(float)
    for t in old[1]:oldpnl[t['stock_id']]+=t['proceeds']-t['cost']
    for sid,p in old[3].items():oldpnl[sid]+=p['shares']*p['mark']-p['cost']
    for t in final[2]:newpnl[t['stock_id']]+=t['pnl']
    for sid,p in final[4].items():newpnl[sid]+=p['remaining']*(p['original_shares']*p['mark']-p['cost'])
    assert abs(sum(oldpnl.values())-(old[0][-1]['nav_stale']-1_000_000))<.01
    assert abs(sum(newpnl.values())-(final[0][-1]['nav_stale']-1_000_000))<.01
    stockdiff=sorted([dict(stock_id=s,old_pnl=oldpnl[s],new_pnl=newpnl[s],difference=newpnl[s]-oldpnl[s]) for s in oldpnl.keys()|newpnl.keys()],key=lambda r:r['difference'])
    years=[];oldbase=newbase=1_000_000
    for y in sorted({t['date'][:4] for t in old[0]}):
        oldend=[t for t in old[0] if t['date'].startswith(y)][-1]['nav_stale']
        newend=[t for t in final[0] if t['date'].startswith(y)][-1]['nav_stale']
        years.append(dict(year=y,old_profit=oldend-oldbase,new_profit=newend-newbase,difference=(newend-newbase)-(oldend-oldbase)))
        oldbase,newbase=oldend,newend
    # Position-days with only a small remainder still occupying a slot.
    daily={};partial_stats={}
    for name in ('weekly_old','final_90'):
        res=results[name];changes=defaultdict(list)
        for t in res[3]:
            if t['filled']:changes[t['date']].append((t['stock_id'],1.))
        for t in res[2]:changes[t['exit']].append((t['stock_id'],-t['fraction_original']))
        fractions=defaultdict(float);total=partial=quarter=0;avgoriginal=[]
        for t in res[0]:
            # Sum deltas is sufficient for end-of-day holdings, including same-day replacement.
            for sid,delta in changes[t['date']]:fractions[sid]+=delta
            active=[v for v in fractions.values() if v>1e-8]
            total+=len(active);partial+=sum(v<1-1e-8 for v in active);quarter+=sum(v<=.25+1e-8 for v in active)
            avgoriginal.append(sum(active)*100_000)
        partial_stats[name]=dict(position_days=total,partial_position_days=partial,quarter_position_days=quarter,mean_remaining_original_cost=sum(avgoriginal)/len(avgoriginal))
    out=Path('backtest/momentum_gap');out.mkdir(exist_ok=True)
    for name,rows in [('stock_difference',stockdiff),('year_difference',years)]:
        with (out/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    for name,result in results.items():
        with (out/(name+'_legs.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(result[2][0]));w.writeheader();w.writerows(result[2])
    result=dict(fingerprint=fingerprint,old_monthly=dict(ending_equity=old[0][-1]['nav_stale'],metrics=metrics(old[0],'nav_stale',1_000_000)),variants=summaries,stock_difference=stockdiff,year_difference=years,partial_stats=partial_stats)
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(years=years,worst_stocks=stockdiff[:12],best_stocks=stockdiff[-5:],partial_stats=partial_stats),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
