"""將最新5%動能回測轉成離線、可篩選的HTML操作紀錄。"""
import csv
import json
import sqlite3
from pathlib import Path

BASE=Path('backtest/momentum_weekly_hermes_equity_momentum_exit_weight0.05')


def read_csv(name):
    with (BASE/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def main():
    meta=json.loads((BASE/'result.json').read_text(encoding='utf-8'))['weekly_momentum_exit']
    trades=read_csv('roundtrips.csv');legs=read_csv('sell_legs.csv');nav=read_csv('nav.csv')
    holdings=json.loads((BASE/'holdings.json').read_text(encoding='utf-8'))
    ids={t['stock_id'] for t in trades}|set(holdings);names={};opens={}
    needed={(t['stock_id'],t['entry']) for t in trades}|{(s,p['entry']) for s,p in holdings.items()}
    conn=sqlite3.connect('file:data/momentum_pit/finmind_raw.db?mode=ro',uri=True)
    for q,b in conn.execute('select query,body from responses where status=200'):
        q=json.loads(q)
        if q['dataset']=='TaiwanStockInfo':
            for r in json.loads(b)['data']:
                if r['stock_id'] in ids:names[r['stock_id']]=r.get('stock_name','')
        elif q['dataset']=='TaiwanStockPrice' and q.get('data_id') in ids:
            for r in json.loads(b)['data']:
                if (q['data_id'],r['date']) in needed:opens[(q['data_id'],r['date'])]=r['open']
    conn.close()
    exits={(t['stock_id'],t['entry']):t for t in legs}
    closed=[]
    for i,t in enumerate(trades):
        sid=t['stock_id'];leg=exits[(sid,t['entry'])]
        closed.append(dict(id=i,code=sid,name=names.get(sid,''),entry=t['entry'],signal=leg['signal_date'],exit=t['exit'],buy=opens.get((sid,t['entry'])),sell=float(leg['exit_open']),cost=float(t['cost']),value=float(t['proceeds']),pnl=float(t['proceeds'])-float(t['cost']),ret=float(t['return_net']),days=int(t['days']),reason=t['last_reason']))
    opened=[]
    from datetime import date
    for i,(sid,p) in enumerate(holdings.items()):
        value=p['original_shares']*p['remaining']*p['mark'];cost=p['remaining']*p['cost']
        opened.append(dict(id=i,code=sid,name=names.get(sid,''),entry=p['entry'],signal=meta['pending'].get(sid,{}).get('signal',''),exit=meta['end'],buy=opens.get((sid,p['entry'])),sell=p['mark'],cost=cost,value=value,pnl=value-cost,ret=value/cost-1,days=(date.fromisoformat(meta['end'])-date.fromisoformat(p['entry'])).days,reason='pending_stop' if sid in meta['pending'] else 'holding'))
    assert len(closed)==meta['closed_roundtrips'] and len(opened)==meta['open_positions']
    assert abs(sum(t['pnl'] for t in closed+opened)+1_000_000-meta['ending_equity'])<.01
    assert abs(sum(t['value'] for t in opened)+meta['ending_cash']-meta['ending_equity'])<.01
    data=dict(meta=meta,closed=closed,opened=opened,nav=[dict(date=t['date'],value=float(t['nav_stale'])) for t in nav])
    template=Path('docs/templates/momentum-trades.html').read_text(encoding='utf-8')
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    output=template.replace('__DATA__',payload)
    (BASE/'交易紀錄.html').write_text(output,encoding='utf-8')
    print(f'HTML 已產生：{len(closed)} 筆平倉、{len(opened)} 檔持股，損益與現金核對通過。')

if __name__=='__main__':main()
