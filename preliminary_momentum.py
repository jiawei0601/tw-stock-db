"""未認證的真實行情探索：不使用正式引擎的驗收旗標，不產生含息績效。"""
import argparse
from bisect import bisect_left, bisect_right
import csv
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics

from momentum_engine import shift_month


def rank(prices, splits, calendar, signal):
    past=[d for d in calendar if d<=signal]
    months={d[:7]:d for d in past}
    lower,upper=months.get(shift_month(signal,-12)),months.get(shift_month(signal,-1))
    if not lower or not upper:
        return []
    out=[]
    for sid, rows in prices.items():
        if any(rows.get(d,{}).get('close',0)<=0 for d in (lower,upper,signal)):
            continue
        if sum(lower<=d<=signal and r['close']>0 and r.get('Trading_Volume',0)>0 for d,r in rows.items())<200:
            continue
        factor=math.prod(ratio for d,ratio in splits.get(sid,[]) if lower<d<=upper)
        score=rows[upper]['close']*factor/rows[lower]['close']-1
        out.append((sid,score))
    return sorted(out,key=lambda x:(-x[1],x[0]))


def forward(rows, splits, entry, target, fee=.003):
    buy=rows.get(entry,{})
    if buy.get('open',0)<=0 or buy.get('Trading_Volume',0)<=0:
        return 0.,'unfilled_cash'
    sell=rows.get(target,{})
    if sell.get('open',0)<=0 or sell.get('Trading_Volume',0)<=0:
        return None,'missing_exit'
    ratio=math.prod(r for d,r in splits if entry<d<=target)
    return sell['open']*ratio/buy['open']*(1-fee)/(1+fee)-1,'price_estimate'


def aggregate(values):
    """不刪掉缺報酬成員。兩種未解決情境並非嚴格的報酬上下界。"""
    missing=sum(v is None for v in values)
    return {'missing':missing,'n':len(values),
            'return':statistics.mean(values) if values and not missing else None,
            'unresolved_zero_value':statistics.mean(-1 if v is None else v for v in values) if values else 0,
            'unresolved_flat':statistics.mean(0 if v is None else v for v in values) if values else 0}


def run(db_path,manifest_path,out_dir):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    conn=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)
    def fetch(dataset,sid=None,ranged=True):
        query={'dataset':dataset}
        if ranged:query.update(start_date=manifest['start'],end_date=manifest['end'])
        if sid is not None:query['data_id']=sid
        key=json.dumps(query,sort_keys=True,separators=(',',':'))
        row=conn.execute('select body,status from responses where query=?',(key,)).fetchone()
        if not row or row[1]!=200:raise ValueError(f'缺少成功查詢：{dataset}/{sid}')
        return json.loads(row[0])['data']
    calendar=sorted({r['date'] for r in fetch('TaiwanStockTradingDate',ranged=False)
                     if manifest['start']<=r['date']<=manifest['end']})
    prices={}
    digest=hashlib.sha256()
    for sid in manifest['candidate_ids']:
        raw=fetch('TaiwanStockPrice',sid)
        digest.update(json.dumps(raw,sort_keys=True).encode())
        prices[sid]={r['date']:r for r in raw if manifest['start']<=r['date']<=manifest['end']}
    events={}
    for dataset,before,after in [('TaiwanStockSplitPrice','before_price','after_price'),
                                  ('TaiwanStockParValueChange','before_close','after_ref_close')]:
        for r in fetch(dataset):
            if r.get(before,0)>0 and r.get(after,0)>0:
                events[(r['stock_id'],r['date'])]=r[before]/r[after]
    splits=defaultdict(list)
    for (sid,d),ratio in events.items():splits[sid].append((d,ratio))
    indices={sid:{r['date']:r['price'] for r in fetch('TaiwanStockTotalReturnIndex',sid)} for sid in ('TAIEX','TPEx')}
    conn.close()
    first={}
    for d in calendar:first.setdefault(d[:7],d)
    cohorts,signals,details=[],[],[]
    for i,entry in enumerate(calendar):
        if not i or entry<'2020-01-01' or calendar[i-1][:7]==entry[:7]:continue
        signal=calendar[i-1]
        ranked=rank(prices,splits,calendar,signal)
        chosen=ranked[:math.ceil(len(ranked)*.25)]
        if not chosen:continue
        selected={sid for sid,_ in chosen}
        for n,(sid,score) in enumerate(ranked,1):
            signals.append({'signal_date':signal,'entry_date':entry,'stock_id':sid,'score':score,
                            'rank':n,'selected':sid in selected,'weight':1/len(chosen) if sid in selected else 0})
        for horizon in (6,12):
            target=first.get(shift_month(entry,horizon))
            if target is None:continue
            values={}
            for sid,_ in ranked:
                value,status=forward(prices[sid],splits[sid],entry,target)
                values[sid]=value
                details.append({'entry_date':entry,'exit_date':target,'horizon':horizon,'stock_id':sid,
                                'selected':sid in selected,'return_price_net_estimate':value,'status':status})
            for strategy,members in [('momentum',chosen),('universe',ranked)]:
                stat=aggregate([values[sid] for sid,_ in members])
                row=dict(stat,entry_date=entry,signal_date=signal,exit_date=target,horizon=horizon,strategy=strategy)
                for idx,prices_index in indices.items():
                    row[idx+'_close_total_return_reference']=prices_index[target]/prices_index[entry]-1 if entry in prices_index and target in prices_index else None
                cohorts.append(row)
        print(entry,len(ranked),len(chosen),flush=True)
    summary=[]
    for horizon in (6,12):
        for year in ['all']+sorted({r['entry_date'][:4] for r in cohorts}):
            for strategy in ('momentum','universe'):
                rs=[r for r in cohorts if r['horizon']==horizon and r['strategy']==strategy and (year=='all' or r['entry_date'].startswith(year))]
                if not rs:continue
                summary.append({'horizon':horizon,'year':year,'strategy':strategy,'months':len(rs),
                    'unresolved_positions':sum(r['missing'] for r in rs),
                    'mean_zero_value':statistics.mean(r['unresolved_zero_value'] for r in rs),
                    'mean_flat':statistics.mean(r['unresolved_flat'] for r in rs),
                    'mean_complete':statistics.mean(r['return'] for r in rs) if all(r['return'] is not None for r in rs) else None,
                    'reference_TAIEX':statistics.mean(r['TAIEX_close_total_return_reference'] for r in rs)})
    # 時點不變性：取中期訊號，截斷其後所有行情與事件，再逐項比較排名。
    test_signal='2022-12-30'
    full=rank(prices,splits,calendar,test_signal)
    prefix_prices={sid:{d:r for d,r in rs.items() if d<=test_signal} for sid,rs in prices.items()}
    prefix_splits={sid:[e for e in es if e[0]<=test_signal] for sid,es in splits.items()}
    invariant=full==rank(prefix_prices,prefix_splits,[d for d in calendar if d<=test_signal],test_signal)
    if not invariant:raise AssertionError('時點不變性失敗')
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    for name,rows in [('signals',signals),('positions',details),('cohorts',cohorts),('summary',summary)]:
        with (out/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    meta={'certification':'UNVERIFIED_PRICE_EXPLORATION','price_data_sha256':digest.hexdigest(),
          'nonempty_price_ids':sum(bool(r) for r in prices.values()),'candidate_ids':len(prices),
          'prefix_invariance':invariant,'test_signal':test_signal,'summary':summary}
    (out/'result.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    lines=['# 真實行情初步驗證（未認證）','','**不是正式含息回測；不能據此宣稱具有超額報酬能力。**','',
      '以已完成行情查詢的歷史候選，用當時有行情與200日有效交易的條件建立研究池；未驗證全部歷史上市身分及代碼沿革。12−1動能前25%，下一月首交易日開盤，持有6／12個月，買賣成本各0.3%。',
      '只以官方分割／面額變動參考價格比例近似股份調整，沒有含入現金股利、股票股利或完整減資；成交僅按開盤價與當日有成交量假設，沒有漲跌停成交保證。比例不視為已核對實際股份比。',
      '缺到期行情的成員保留原權重，分別假設未解決部位價值歸零／本金不變；這是兩個情境，不是嚴格上下界。沒有按未來缺報酬刪除股票或批次。',
      'TAIEX是未扣成本的含息收盤參考，與策略未含息開盤口徑不同，不作精確超額比較。月批次重疊，表內都是平均持有期報酬，不是年化。','',
      '|持有月數|進場年|策略|成熟批次|未解決部位|價值歸零情境|本金不變情境|大盤含息收盤參考|',
      '|---|---|---|---:|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"|{r['horizon']}|{r['year']}|{r['strategy']}|{r['months']}|{r['unresolved_positions']}|{r['mean_zero_value']:.2%}|{r['mean_flat']:.2%}|{r['reference_TAIEX']:.2%}|")
    lines+=['',f'實際行情截斷測試：{test_signal} 排名在移除未來資料後保持一致：{invariant}。此測試不代表歷史母體完整或事件資料無修訂。']
    (out/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return meta


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',default='data/momentum_pit/finmind_raw.db')
    parser.add_argument('--manifest',default='data/momentum_pit/manifest.json')
    parser.add_argument('--out',default='backtest/momentum_preliminary')
    args=parser.parse_args()
    run(args.db,args.manifest,args.out)
