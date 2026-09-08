"""50萬元動能帳戶：官方日線更新、固定訊號、實際成交帳本；不代客下單。"""
import argparse
import gzip
import json
import math
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from dynamic_momentum import signal_table, decisions
from momentum_engine import shift_month

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'live_data'
TZ=ZoneInfo('Asia/Taipei')


def atomic(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)


@contextmanager
def locked():
    DATA.mkdir(exist_ok=True)
    lock=DATA/'account.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:yield
    finally:os.close(fd);lock.unlink()


def get_json(url,params):
    r=requests.get(url,params=params,headers={'User-Agent':'Mozilla/5.0'},timeout=35)
    r.raise_for_status()
    return json.loads(r.content.decode('utf-8'))


def holidays(year):
    path=DATA/f'holidays-{year}.json'
    if path.exists():return set(json.loads(path.read_text()))
    p=get_json('https://www.twse.com.tw/holidaySchedule/holidaySchedule',{'response':'json','queryYear':str(year-1911)})
    if p.get('queryYear')!=year or len(p.get('data',[]))<10:raise ValueError('官方年度交易日曆未取得')
    days=[r[0] for r in p['data'] if '開始交易' not in r[1] and '最後交易' not in r[1]]
    atomic(path,days)
    return set(days)


def trading(d):return d.weekday()<5 and d.isoformat() not in holidays(d.year)


def next_session(d):
    d+=timedelta(days=1)
    while not trading(d):d+=timedelta(days=1)
    return d


def number(v):
    try:n=float(str(v).replace(',','').strip())
    except (ValueError,TypeError):return None
    return n if math.isfinite(n) else None


def parse_quotes(payload,day,market):
    if payload.get('date')!=day.replace('-',''):raise ValueError(f'{market}回傳日期不符')
    labels=({'stock_id':'證券代號','name':'證券名稱','open':'開盤價','max':'最高價','min':'最低價','close':'收盤價','Trading_Volume':'成交股數','Trading_money':'成交金額'} if market=='twse' else
            {'stock_id':'代號','name':'名稱','open':'開盤','max':'最高','min':'最低','close':'收盤','Trading_Volume':'成交股數','Trading_money':'成交金額(元)'})
    table=next((t for t in payload.get('tables',[]) if all(v in t.get('fields',[]) for v in labels.values())),None)
    if table is None or len(table.get('data',[]))<500:raise ValueError(f'{market}行情缺表或覆蓋不足')
    indexes={k:table['fields'].index(v) for k,v in labels.items()}
    result={}
    for row in table['data']:
        sid=str(row[indexes['stock_id']]).strip()
        if not (len(sid)==4 and sid.isdigit()):continue
        out={k:(str(row[i]).strip() if k in ('stock_id','name') else number(row[i])) for k,i in indexes.items()}
        if out['close'] is None or out['Trading_Volume'] is None or out['Trading_Volume']<=0:continue
        if any(out[k] is None for k in ['open','max','min','Trading_money']) or not 0<out['min']<=min(out['open'],out['close'])<=max(out['open'],out['close'])<=out['max']:
            raise ValueError(f'{market}/{sid}/{day}官方OHLC矛盾，停止本日操作清單')
        result[sid]=out
    if len(result)<500:raise ValueError(f'{market}有效股票不足')
    return result


def daily_quotes(day):
    path=DATA/f'quotes-{day}.json'
    if path.exists():return json.loads(path.read_text(encoding='utf-8'))
    merged={}
    for market,url,params in [('twse','https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',dict(date=day.replace('-',''),type='ALLBUT0999',response='json')),('tpex','https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes',dict(date=day.replace('-','/'),response='json'))]:
        p=get_json(url,params)
        atomic(DATA/f'raw-{market}-{day}.json',p)
        merged.update(parse_quotes(p,day,market))
    atomic(path,merged)
    return merged


def load_live(day):
    with gzip.open(DATA/'seed.json.gz','rt',encoding='utf-8') as f:seed=json.load(f)
    prices=seed['prices'];calendar=seed['calendar'];names=seed['names']
    events={(s,d):v for s,d,v in seed['events']}
    cursor=date.fromisoformat(seed['asof'])+timedelta(days=1)
    if (day-cursor).days>45:raise ValueError('更新落後超過45天，需補資料後恢復')
    while cursor<=day:
        if trading(cursor):
            ds=cursor.isoformat();quotes=daily_quotes(ds)
            calendar.append(ds)
            for sid,r in quotes.items():
                prices.setdefault(sid,{})[ds]=r;names[sid]=r['name']
        cursor+=timedelta(days=1)
    if calendar[-1]!=day.isoformat():raise ValueError('資料不是指定交易日完整收盤')
    # Persist only after every required market/day passed validation; incremental updates stay bounded.
    updated={**seed,'asof':day.isoformat(),'prices':prices,'calendar':calendar,'names':names}
    tmp=DATA/'seed.json.gz.tmp'
    with gzip.open(tmp,'wt',encoding='utf-8',compresslevel=3) as f:json.dump(updated,f,ensure_ascii=False,separators=(',',':'))
    os.replace(tmp,DATA/'seed.json.gz')
    return prices,events,calendar,names,seed


def account():
    path=DATA/'account.json'
    if not path.exists():atomic(path,dict(initial_cash=500000.,fills=[],adjustments=[]))
    return json.loads(path.read_text(encoding='utf-8'))


def positions(ledger):
    cash=ledger['initial_cash'];held={}
    for r in ledger['fills']:
        sid=r['stock_id'];n=r['shares'];value=n*r['price']
        if r['side']=='buy':
            if sid in held:raise ValueError('不支援重複加碼；請先核对帳本')
            cash-=value+r['fees']
            held[sid]=dict(shares=n,entry=r['date'],cost=value+r['fees'],price=r['price'])
        else:
            if sid not in held or held[sid]['shares']<n:raise ValueError('賣出超過持股')
            cash+=value-r['fees'];held[sid]['shares']-=n
            if held[sid]['shares']==0:del held[sid]
        if cash<-.01:raise ValueError('成交超過可用現金')
    return cash,held


def record_fill(args):
    ledger=account()
    if any(r['id']==args.id for r in ledger['fills']):raise ValueError('成交ID已存在；不重複入帳')
    if args.shares<=0 or not math.isfinite(args.price) or not math.isfinite(args.fees) or args.price<=0 or args.fees<0:raise ValueError('成交數值無效')
    if date.fromisoformat(args.date)>datetime.now(TZ).date():raise ValueError('不可記錄未來成交')
    if ledger['fills'] and args.date<ledger['fills'][-1]['date']:raise ValueError('補登較早成交需人工核對順序')
    ledger['fills'].append(dict(id=args.id,stock_id=args.stock_id,side=args.side,shares=args.shares,price=args.price,fees=args.fees,date=args.date))
    positions(ledger)
    backup=DATA/'account-backups';backup.mkdir(exist_ok=True)
    atomic(backup/(datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')+'.json'),account())
    atomic(DATA/'account.json',ledger)
    print('實際成交已入帳；建議不會自動當成交。')


def report(day,weekly=False):
    ds=day.isoformat();p,e,cal,names,seed=load_live(day)
    cash,held=positions(account());ends={d[:7]:d for d in cal}
    valid={s:sorted(d for d,r in rows.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for s,rows in p.items()}
    table=signal_table(p,e,cal,ds,valid)
    prevday=ends.get(shift_month(ds,-1));olderday=ends.get(shift_month(ds,-2))
    previous=signal_table(p,e,cal,prevday,valid);older=signal_table(p,e,cal,olderday,valid)
    buys,momentum=decisions(table,previous,older,held)
    nextday=next_session(day);monthend=nextday.month!=day.month
    exits={};issues=[];nav=cash;historic_tables={}
    for sid,h in held.items():
        rows=p.get(sid,{});r=rows.get(ds)
        if not r:
            issues.append(f'{sid}缺當日收盤，需人工核對，暫停新增配置');continue
        nav+=h['shares']*r['close']
        peak=h['price'];first_exit=None
        # 10:00進場不能使用當日上午買進前最高價；首日僅納入成交價與收盤。
        for d in cal:
            if d<h['entry']:continue
            q=rows.get(d)
            if q is None:issues.append(f'{sid}/{d}持有期間缺价，需核對高點');continue
            if (sid,d) in e:issues.append(f'{sid}/{d}股份事件需回報實際股數；暫停自動判斷');break
            before=next((rows[z]['close'] for z in reversed(cal[:cal.index(d)]) if z in rows),None)
            if before and abs(q['open']/before-1)>.15:
                issues.append(f'{sid}/{d}異常跳空或除權需核實');break
            peak=max(peak,q['close'] if d==h['entry'] else q['max'])
            if first_exit is None and q['close']<peak*.7:
                first_exit=dict(signal=d,reason=f'高點{peak:g}回落{1-q["close"]/peak:.1%}（30%線{peak*.7:g}）')
            # Replay missed month-end signals too; a recovered close must not erase an exit.
            if first_exit is None and d==ends[d[:7]] and (d<ds or monthend):
                dates=[d,ends.get(shift_month(d,-1)),ends.get(shift_month(d,-2))]
                if all(dates):
                    for t in dates:
                        if t not in historic_tables:historic_tables[t]=signal_table(p,e,cal,t,valid)
                    _,past_exits=decisions(*(historic_tables[t] for t in dates),[sid])
                    if sid in past_exits:first_exit=dict(signal=d,reason='月底動能：'+past_exits[sid])
        else:
            if first_exit:exits[sid]=first_exit
    statepath=DATA/'pending.json'
    pending=json.loads(statepath.read_text(encoding='utf-8')) if statepath.exists() else {}
    pending={s:r for s,r in pending.items() if s in held and r['signal']>=held[s]['entry']}
    for s,order in exits.items():pending.setdefault(s,order)
    atomic(statepath,pending)
    valuation='淨值待核對' if issues else f'估計淨值{nav:,.0f}元'
    lines=[f'【動能30%策略】{ds}收盤',f'現金{cash:,.0f}元；已回報持股{len(held)}檔；{valuation}。']
    for s,r in pending.items():lines.append(f'賣出待執行：{s} {names.get(s,s)} {held[s]["shares"]}股；{r["reason"]}；訊號{r["signal"]}，下一交易日10:00，成交後回報。')
    if not held:lines.append('尚未回報持股，沒有可判定的賣出標的。')
    if issues:lines+=['資料需核對：']+sorted(set(issues))
    if weekly:
        budget=nav*.05;available=cash;count=0
        lines+=[f'下一交易日{nextday} 10:00買進候選；每筆目標約{budget:,.0f}元含成本。']
        if issues:lines+=['帳戶估值或持股事件未核對，本輪不產生買進股數。']
        else:
            for sid in buys:
                if available+1e-8<budget:break
                r=p[sid][ds];shares=math.floor(budget/(r['close']*1.003))
                if shares<=0:continue
                estimate=shares*r['close']*1.003
                count+=1;available-=budget
                gap=f'，僅達目標{estimate/budget:.1%}，需留意取整差距' if estimate<budget*.9 else ''
                lines.append(f'{count}. {sid} {names.get(sid,sid)}｜動能{table[sid]["score"]:.1%}｜成交額{table[sid]["money"]/1e8:.2f}億｜約{shares}股（收盤{r["close"]:g}估算，含成本約{estimate:,.0f}元{gap}）')
            if not count:lines.append('無可用現金配置的新候選；沒有買進要求。')
        lines+=['股數僅估算，10:00按實價與券商費用調整、總額勿超過現金；零股成交不等於回測開盤價。未計入尚未成交的賣出款，賣單成交回報後才釋出可用資金。',
                '訊號採既定3−1價格模型；歷史公司行動尚未完整驗證。除權／分割或異常跳空請先核對，不把候選當成保證績效。',
                '成交後回覆：動能30 成交ID 買/賣 代號 股數 成交價 手續費及稅合計 成交日期；本帳戶不自動下單。']
    text='\n'.join(lines)
    atomic(DATA/f'report-{ds}.json',dict(date=ds,text=text,issues=issues,pending=pending,weekly=weekly,seed_fingerprint=seed['fingerprint']))
    return text if weekly or pending or issues else ''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',default=None);parser.add_argument('--preview',action='store_true')
    sub=parser.add_subparsers(dest='command')
    f=sub.add_parser('fill')
    for name in ['id','stock-id','side','date']:f.add_argument('--'+name,required=True,choices=['buy','sell'] if name=='side' else None)
    f.add_argument('--shares',required=True,type=int);f.add_argument('--price',required=True,type=float);f.add_argument('--fees',required=True,type=float)
    args=parser.parse_args()
    with locked():
        if args.command=='fill':record_fill(args);return
        now=datetime.now(TZ);day=date.fromisoformat(args.date) if args.date else now.date()
        if day>now.date() or (day==now.date() and now.hour<14):raise ValueError('收盤資料尚未形成')
        if not trading(day):
            if day.weekday()==2:print(f'【動能30%】{day}週三休市，本週不順延選股。')
            return
        text=report(day,weekly=day.weekday()==2 or args.preview)
        if args.preview:text='【測試預覽，非正式操作清單；只在週三收盤選股】\n'+text
        if text:print(text)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('【動能30%資料／帳戶警報】'+str(exc)+'。本輪未確認完整操作，請勿沿用舊買單。')
        raise SystemExit(1)
