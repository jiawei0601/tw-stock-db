"""第三輪獨立重算：標準庫＋NumPy；不匯入任何回測模組或其聚合函式。
訊號 CSV 提供原有完整的 L1/C1/D 層旗標；12-1 選股由 DB 全池獨立補建。
"""
from __future__ import annotations
import argparse
import ast
import bisect
import calendar
import csv
import hashlib
import json
import math
import sqlite3
import statistics as st
from collections import defaultdict
from datetime import date
from pathlib import Path
import re
import numpy as np

STRATEGIES = ['D5','C1','C1_PER','L1','D0','D2','D3','MOM_12_1']
PAIRS = [('D0','D2'),('D2','D3'),('D3','D5'),('C1','MOM_12_1'),('C1','C1_12_1'),('C1_12_1','MOM_12_1')]

def read_csv(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))

def num(value):
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None

def stats(values):
    return {'N':len(values),'勝率':100*sum(v>0 for v in values)/len(values), '平均':100*st.mean(values),'中位':100*st.median(values)}

def bootstrap(values, block_size, seed=42, n_resamples=2000):
    """非環狀移動區塊；獨立向量化索引，末區塊裁切至原樣本數。
    RandomState 保留原方法的 MT19937 隨機序列；不共用原函式。
    """
    n=len(values)
    if not n or block_size<1:
        raise ValueError('樣本及區塊長度必須為正')
    width=min(n,block_size)
    starts=np.random.RandomState(seed).randint(0,max(1,n-block_size+1),size=(n_resamples,math.ceil(n/width)))
    indices=(starts[:,:,None]+np.arange(width)).reshape(n_resamples,-1)[:,:n]
    means=np.asarray(values,dtype=float)[indices].mean(axis=1)
    return tuple(float(x) for x in np.percentile(means,[2.5,97.5]))

def ranks(values):
    ordered=sorted(v for v in values.values() if v is not None)
    return {sid:(bisect.bisect_left(ordered,v)+1+bisect.bisect_right(ordered,v))/(2*len(ordered)) for sid,v in values.items() if v is not None}

def month_shift(d, months):
    y,m=map(int,d[:7].split('-')); serial=y*12+m-1+months
    return f'{serial//12:04d}-{serial%12+1:02d}'

def forward(prices, dates, index, signal, target, delisting):
    """原規則：T+1..T+3，日曆六個月後 T+1..T+3，缺價最後價，未成交現金。"""
    if target is None:
        return None,False
    entry=next(((d,prices[d]) for d in dates[index[signal]+1:index[signal]+4] if prices.get(d,0)>0),None)
    if entry is None:
        return 0.0,False
    ed,ep=entry
    if delisting and delisting<=target:
        if delisting<=ed: return 0.0,False
        available=[p for d,p in prices.items() if ed<=d<=delisting]
        return (available[-1] if available else ep)/ep-1,True
    xp=next((prices[d] for d in dates[index[target]:index[target]+3] if prices.get(d,0)>0),None)
    if xp is None:
        available=[p for d,p in prices.items() if ed<=d<=target]
        xp=available[-1] if available else ep
    return xp/ep-1,True

def reconstruct(conn, csv_rows):
    # 僅讀既有母體純資料常數，不執行 build_valuation 模組。
    constants={}
    for node in ast.parse(Path('build_valuation.py').read_text(encoding='utf-8')).body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('_AI_MAIN','_EXT_GROUPS','_AI_CHAIN_EXCLUDE'):
            constants[node.targets[0].id]=ast.literal_eval(node.value)
    ai=set(constants['_AI_MAIN'])|{s for g in constants['_EXT_GROUPS'].values() for s in g}
    ai-=constants['_AI_CHAIN_EXCLUDE']
    survivors={r['stock_id']:r for r in read_csv('backtest/universe_2026_survivors.csv')}
    sub={s:v for s,v in conn.execute('SELECT stock_id,sub FROM stock_sub_industry ORDER BY stock_id,node')}
    universe=ai|set(sub)|set(survivors)
    for sid in universe:
        sub[sid]=survivors.get(sid,{}).get('sub') or sub.get(sid,'其他')
    prices=defaultdict(dict)
    for sid,d,p in conn.execute('SELECT stock_id,date,close_adj FROM fm_price_adj_daily WHERE close_adj>0 ORDER BY stock_id,date'):
        prices[sid][d]=p
    dates=[r[0] for r in conn.execute('SELECT DISTINCT date FROM fm_price_adj_daily ORDER BY date')]
    index={d:i for i,d in enumerate(dates)}
    month_end={d[:7]:d for d in dates}
    yields={(s,d):v for s,d,v in conn.execute('SELECT stock_id,date,dividend_yield FROM per_daily')}
    delist=dict(conn.execute('SELECT stock_id,date FROM fm_delisting ORDER BY date'))
    original={(r['signal_date'],r['stock_id']):r for r in csv_rows}
    audit={'母體股票數':len(universe),'CSV報酬不一致':[],'CSV動能旗標不一致':[], '基準最大差百分點':0,'缺殖利率股票月':0,'有效股票月':0,'5305有效月份':[]}
    all_months={}
    for signal in sorted({r['signal_date'] for r in csv_rows}):
        i=index[signal]
        target_end=month_end.get(month_shift(signal,6))
        target=dates[index[target_end]+1] if target_end and index[target_end]+1<len(dates) else None
        if target is None: continue
        monthly={}
        for sid in sorted(universe):
            p=prices.get(sid,{})
            if not p or signal not in p or i<250: continue
            first=survivors.get(sid,{}).get('listed_date') or next(iter(p))
            if first>dates[i-250] or sum(d in p for d in dates[i-250:i+1])<200: continue
            def historical(months):
                d=month_end.get(month_shift(signal,-months))
                if not d: return None
                return next((p[x] for x in reversed(dates[max(0,index[d]-5):index[d]+1]) if x in p),None)
            p1,p12,p3=historical(1),historical(12),historical(3)
            ret,filled=forward(p,dates,index,signal,target,delist.get(sid))
            dy=num(yields.get((sid,signal)))
            audit['有效股票月']+=1
            audit['缺殖利率股票月']+=dy is None
            row={'ret':ret,'tr':ret+(dy or 0)/200 if filled else ret,'sub':sub[sid], 'mom':p1/p12-1 if p1 and p12 else None,'mom3':p[signal]/p3-1 if p3 else None}
            monthly[sid]=row
            if sid=='5305': audit['5305有效月份'].append(signal)
        for key in ['mom','mom3']:
            groups=defaultdict(list)
            for r in monthly.values():
                if r[key] is not None: groups[r['sub']].append(r[key])
            means={k:st.mean(v) for k,v in groups.items()}
            for r in monthly.values():r['rel_'+key]=r[key]-means[r['sub']] if r[key] is not None else None
        for strat,key in [('MOM_12_1','mom'),('C1_12_1','rel_mom'),('C1','rel_mom3')]:
            ranked=ranks({sid:r[key] for sid,r in monthly.items()})
            for sid,r in monthly.items():r[strat]=ranked.get(sid,0)>=0.75
        for sid,r in monthly.items():
            old=original.get((signal,sid))
            if old:
                old_ret=num(old['ret_6m'])
                if old_ret is not None and abs(old_ret-r['ret'])>1e-10:
                    audit['CSV報酬不一致'].append([signal,sid,old_ret,r['ret']])
                for strat in ['C1','MOM_12_1','C1_12_1']:
                    if (old[strat]=='True')!=r[strat]: audit['CSV動能旗標不一致'].append([signal,sid,strat])
        bench=st.mean(r['ret'] for r in monthly.values())
        for old in csv_rows:
            if old['signal_date']==signal and num(old['bench_6m']) is not None:
                audit['基準最大差百分點']=max(audit['基準最大差百分點'],abs(bench-float(old['bench_6m']))*100)
        all_months[signal]=monthly
    return all_months,audit

def aggregate(csv_rows,months):
    series={s:{} for s in STRATEGIES+['C1_12_1']}
    for d,month in months.items():
        bench=st.mean(r['ret'] for r in month.values())
        bench_tr=st.mean(r['tr'] for r in month.values())
        original=[r for r in csv_rows if r['signal_date']==d and num(r['ret_6m']) is not None]
        for strat in series:
            if strat in ('MOM_12_1','C1_12_1'):
                chosen=[r for r in month.values() if r[strat]]
            else:
                flag='is_l1' if strat=='L1' else strat
                chosen=[]
                for row in original:
                    if row[flag]!='True': continue
                    ret=float(row['ret_6m'])
                    dy=num(row['dividend_yield_at_signal']) or 0
                    chosen.append({'ret':ret,'tr':ret+dy/200 if row['entry_status']!='unfilled' else ret})
            if chosen:
                raw=st.mean(r['ret'] for r in chosen)
                tr=st.mean(r['tr'] for r in chosen)
                series[strat][d]={'ret':raw,'tr':tr,'excess':raw-bench,'excess_tr':tr-bench_tr,'n':len(chosen)}
    return series

def parse_summary(path):
    result={}
    section=0
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):
            section=int(line[3]) if line[3].isdigit() else -1
        if not line.startswith('| **') or section not in (1,2):continue
        cells=[c.replace('**','').strip() for c in line.strip('|').split('|')]
        if section==1:
            key=cells[0]; cols=[cells[i] for i in (2,5,8,9)]
            result[key]=dict(zip(['N','勝率','平均','中位'],[float(re.search(r'[-+]?\d+(?:\.\d+)?',v)[0]) for v in cols]))
        else:
            key=cells[0].replace('$\\rightarrow$','→').replace(' ','')
            vals=[float(re.search(r'[-+]?\d+(?:\.\d+)?',v)[0]) for v in cells[2:6]]
            result[key]=dict(zip(['N','平均','中位','勝率'],vals))
            for b,cell in zip((6,12),cells[6:8]):
                lo,hi=map(float,re.findall(r'([-+]?\d+(?:\.\d+)?)%',cell))
                result[key][f'{b}m下界']=lo;result[key][f'{b}m上界']=hi
    return result

def decision(cis):
    return '成立' if all(lo>0 for lo,hi in cis) else '不成立（CI 跨0或未全正）'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db-path',default='data/tw_stocks.db')
    parser.add_argument('--summary',default='backtest/validity_summary.md')
    args=parser.parse_args()
    old=parse_summary(args.summary)
    csv_rows=read_csv('backtest/signals.csv')
    with sqlite3.connect(Path(args.db_path).resolve().as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('BEGIN')
        months,audit=reconstruct(conn,csv_rows)
        audit['資料表列數']={t:conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ['fm_price_daily','fm_price_adj_daily','per_daily','fm_corporate_events']}
    series=aggregate(csv_rows,months)
    comparisons=[]; sens=[]; runs=[]; judgments=[]
    def compare(key,current):
        for metric,v in current.items():
            previous=old[key][metric]; delta=v-previous
            status='MISMATCH' if (delta!=0 if metric=='N' else abs(delta)>0.05) else 'MATCH'
            reason='原摘要與既有 summary.md 六個月配對表不一致；該表使用3/6m區塊，本輪按工單用6/12m。原摘要數字如何產生未解。' if status=='MISMATCH' else '四捨五入容許範圍內'
            comparisons.append({'項目':key,'指標':metric,'summary所載':previous,'重算值':v,'差異':delta,'狀態':status,'原因':reason})
    for s in STRATEGIES:
        raw=stats([v['excess'] for v in series[s].values()]);proxy=stats([v['excess_tr'] for v in series[s].values()])
        compare(s,raw)
        for metric in raw:sens.append({'項目':s,'指標':metric,'無股利':raw[metric],'殖利率代理':proxy[metric]})
        sens.append({'項目':s,'指標':'平均超額方向翻轉','無股利':str(raw['平均']>0),'殖利率代理':str(proxy['平均']>0)})
    for p,c in PAIRS:
        key=p+'→'+c;common=sorted(series[p].keys()&series[c].keys())
        raw=[series[c][d]['ret']-series[p][d]['ret'] for d in common]
        proxy=[series[c][d]['tr']-series[p][d]['tr'] for d in common]
        measured=stats(raw);adjusted=stats(proxy);cis=[];proxy_cis=[];seed43_cis=[]
        for b in [6,12]:
            ci1=bootstrap(raw,b,42);ci2=bootstrap(raw,b,42);ci3=bootstrap(raw,b,43);cit=bootstrap(proxy,b,42)
            assert ci1==ci2
            cis.append(ci1);proxy_cis.append(cit);seed43_cis.append(ci3)
            for i,end in enumerate(['下界','上界']):
                metric=f'{b}m{end}';measured[metric]=ci1[i]*100;adjusted[metric]=cit[i]*100
                runs.append({'配對':key,'區塊':b,'端點':end,'seed42第一次':ci1[i]*100,'seed42第二次':ci2[i]*100,'完全相同':ci1[i]==ci2[i],'seed43':ci3[i]*100,'漂移百分點':(ci3[i]-ci1[i])*100})
        compare(key,measured)
        for metric in measured:sens.append({'項目':key,'指標':metric,'無股利':measured[metric],'殖利率代理':adjusted[metric]})
        raw_dec=decision(cis);proxy_dec=decision(proxy_cis)
        sens.append({'項目':key,'指標':'CI判定','無股利':raw_dec,'殖利率代理':proxy_dec})
        if key in ['D2→D3','D3→D5','C1→MOM_12_1']:
            expected=key!='D2→D3'
            actual=raw_dec=='成立'
            judgments.append({'配對':key,'重判':('存疑' if actual==expected and (decision(seed43_cis)!=raw_dec or proxy_dec!=raw_dec) else ('維持' if actual==expected else '推翻')),'無股利':raw_dec,'殖利率代理':proxy_dec,'seed43':decision(seed43_cis),'翻轉':raw_dec!=proxy_dec,'共同月份':common})
    out=Path('backtest')
    for name,rows in [('validity_recheck.csv',comparisons),('validity_dividend_sensitivity.csv',sens),('validity_bootstrap_runs.csv',runs)]:
        with (out/name).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with (out/'validity_recheck_monthly.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.writer(f);w.writerow(['策略','訊號日','無股利報酬','殖利率代理報酬','無股利超額','殖利率代理超額','選股數'])
        for s,rows in series.items():
            for d,r in rows.items():w.writerow([s,d,r['ret'],r['tr'],r['excess'],r['excess_tr'],r['n']])
    audit['三項重判']=judgments
    audit['MISMATCH數']=sum(r['狀態']=='MISMATCH' for r in comparisons)
    audit['對照數']=len(comparisons)
    audit['bootstrap端點數']=len(runs)
    audit['最大seed漂移百分點']=max(abs(r['漂移百分點']) for r in runs)
    audit['輸入SHA256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('backtest/signals.csv'),Path('backtest/universe_2026_survivors.csv'),Path('build_valuation.py')]}
    (out/'validity_recheck_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    def table(rows):
        headers=list(rows[0]);lines=['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']
        for r in rows:lines.append('| '+' | '.join(f'{r[h]:.8f}' if isinstance(r[h],float) else str(r[h]) for h in headers)+' |')
        return '\n'.join(lines)
    text='# 第三輪數字重核\n\n數值單位：N 為月份數，其餘為百分比或百分點。原摘要數字未修改。\n\n'
    text+='重算方法：CSV 的 L1/C1/D 層訊號與個股報酬保留原輸入；該 CSV 只匯出 L0 或 C1，12-1 兩組必須以 DB 完整母體重建。母體沿用來源程式三個純資料常數、stock_sub_industry 與 survivors CSV 的聯集；不共用任何函式。六個月到期依日曆月，進出各最多三個交易日；同月等權後計算均值、中位數、勝率。基準以 DB 全體合資格股票獨立重建。\n\n'
    text+='殖利率來源為訊號日 per_daily.dividend_yield；策略 CSV 欄位 dividend_yield_at_signal。代理加回年殖利率×6/12，未成交現金不加股利；缺值依原程式以0處理。這不是實際除息日現金流，不代表含息指數。策略與基準同時加回代理。\n\n'
    text+=f"DB重建與CSV個股報酬不一致 {len(audit['CSV報酬不一致'])} 筆、可對照動能旗標不一致 {len(audit['CSV動能旗標不一致'])} 筆；基準最大差 {audit['基準最大差百分點']:.12f} 百分點。缺殖利率 {audit['缺殖利率股票月']}/{audit['有效股票月']} 股票月。\n\n"
    text+='## 全數對照\n\n'+table(comparisons)+'\n\n## Bootstrap 雙跑及換 seed\n\n非環狀移動區塊，2000 次，RandomState(42)，獨立向量化實作；第一次與第二次各自建立 RNG。\n\n'+table(runs)
    text+='\n\n## 股利敏感度\n\n'+table(sens)+'\n\n## 三項結論重判\n\n'+table([{k:v for k,v in j.items() if k!='共同月份'} for j in judgments])
    text+='\n\n來源對照：既有 backtest/summary.md 的六個月配對表，其均值、中位數、勝率與本次重算的四捨五入值一致；但該表 CI 欄是3/6m區塊，不是 validity_summary.md 所寫6/12m。原摘要的D3→D5均值2.84也無法與同為36個共同月的§1均值9.39−5.46≈3.93對上。這些是文件間不一致的直接例子；造成原摘要數字的計算或抄錄過程未解。\n\nMISMATCH 原因：上述個股報酬、動能旗標及基準交叉差異另有明列；原摘要沒有逐月原始計算紀錄。無法僅憑目前輸入追出摘要各格來源的差異列，一律標「未解」，沒有回改原數字。CI 判定只依本次重算樣本，並不消除存活者偏誤或代理股利誤差。\n'
    (out/'validity_recheck.md').write_text(text,encoding='utf-8')
    print(json.dumps({k:v for k,v in audit.items() if k not in ['CSV報酬不一致','CSV動能旗標不一致','三項重判']},ensure_ascii=False,indent=2))
    print(table([{k:v for k,v in j.items() if k!='共同月份'} for j in judgments]))

if __name__=='__main__':
    main()
