"""歷史上市／上櫃／興櫃的獨立日線回測，含VWAP成交與滑價敏感度。"""
import argparse
import csv
import hashlib
import html
import json
import math
import sqlite3
from bisect import bisect_left,bisect_right
from collections import Counter
from pathlib import Path

from dynamic_momentum import load_data,signal_table
from emerging_market import MarketUniverse,MarketExecution,prepare_prices,additional_price_ids
from listed_universe import ListedUniverse
from momentum_entry_filters import TechnicalFeatures,TechnicalEntryGate
from research_revenue_momentum import load_snapshot_values,SnapshotGate,save_csv
from weekly_hermes_momentum import diagnostic_high_envelope,simulate_weekly,summarize,weekly_signal_days

ROOT=Path('data/momentum_pit/emerging_universe')
OUT=Path('backtest/momentum_rerun_20260909_emerging')
LABELS={'A':'僅上市上櫃','B':'上市櫃買入＋興櫃歷史','C':'上市上櫃＋興櫃買入'}
MODE_LABELS={'momentum':'純動能','base':'營收基準','all':'三項合併'}


def price_coverage(raw,universe,calendar,skipped):
    result={m:dict(expected_known_sessions=0,present_rows=0,positive_trade_rows=0) for m in ('TWSE','TPEX','EMERGING')}
    for sid,intervals in universe.intervals.items():
        if sid in skipped:continue
        rows=raw.get(sid,{})
        for record in intervals:
            start=bisect_left(calendar,record['start'])
            end=bisect_left(calendar,record['end']) if record.get('end') else len(calendar)
            target=result[record['market']]
            target['expected_known_sessions']+=end-start
            for day in calendar[start:end]:
                row=rows.get(day)
                if row is not None:
                    target['present_rows']+=1
                    target['positive_trade_rows']+=row.get('close',0)>0 and row.get('Trading_Volume',0)>0
    for target in result.values():
        target['row_coverage']=target['present_rows']/target['expected_known_sessions'] if target['expected_known_sessions'] else None
    return result


def load_inputs():
    raw,events,calendar,index,base_hash,excluded=load_data()
    payload=json.loads((ROOT/'intervals.json').read_text(encoding='utf-8'))
    if payload['universe_kind']!='TWSE_TPEX_EMERGING_EFFECTIVE_INTERVALS':raise ValueError('wrong universe kind')
    universe=MarketUniverse(payload['records'])
    manifest=json.loads(Path('data/momentum_pit/manifest.json').read_text(encoding='utf-8'))
    needed,skipped=additional_price_ids(payload,set(raw),calendar,manifest['start'],manifest['end'])
    raw.update({sid:{} for sid in skipped})
    conn=sqlite3.connect(f'file:{(ROOT/"finmind_extra.db").as_posix()}?mode=ro',uri=True)
    fingerprints=[];missing=[]
    for sid in sorted(needed):
        query=json.dumps(dict(dataset='TaiwanStockPrice',data_id=sid,start_date=manifest['start'],end_date=manifest['end']),sort_keys=True,separators=(',',':'))
        found=conn.execute('SELECT body,sha256 FROM responses WHERE query=? AND status=200',(query,)).fetchone()
        if not found:missing.append(sid);continue
        rows=json.loads(found[0])['data']
        if len({r['date'] for r in rows})!=len(rows):raise ValueError(f'duplicate price day:{sid}')
        raw[sid]={r['date']:r for r in rows}
        fingerprints.append((sid,found[1]))
    conn.close()
    if missing:raise ValueError(f'Price downloads incomplete: {len(missing)}: {missing[:20]}')
    status=json.loads((ROOT/'revenue_status.json').read_text(encoding='utf-8'))
    if status['state']!='complete':raise ValueError('emerging revenue download incomplete')
    values,conflicts=load_snapshot_values(Path('data/momentum_pit/revenue_archive'))
    blocked={(r['stock_id'],r['month']) for r in conflicts}
    original_values=dict(values)
    conn=sqlite3.connect(f'file:{(ROOT/"revenue.db").as_posix()}?mode=ro',uri=True)
    new_conflicts=[];added=0
    for sid,month,value in conn.execute('SELECT stock_id,month,revenue_twd FROM revenue_snapshots WHERE url IN (SELECT url FROM pages WHERE error IS NULL)'):
        if value is None or (sid,month) in blocked:continue
        key=(sid,month)
        if key in values and values[key]!=value:
            new_conflicts.append(dict(stock_id=sid,month=month,original=values.pop(key),emerging=value))
            blocked.add(key)
        elif key not in values:values[key]=value;added+=1
    conn.close()
    audit=dict(base_price_fingerprint=base_hash,extra_price_fingerprints=fingerprints,
               extra_price_stock_count=len(needed),extra_empty_price_ids=sorted(s for s in needed if not raw[s]),
               skipped_proven_insufficient_warmup=skipped,
               revenue_added=added,revenue_conflicts=new_conflicts,
               original_revenue_conflict_count=len(conflicts),revenue_stock_months=len(values),
               revenue_fingerprint=hashlib.sha256(json.dumps(sorted((s,m,v) for (s,m),v in values.items())).encode()).hexdigest(),
               universe_sha256=hashlib.sha256((ROOT/'intervals.json').read_bytes()).hexdigest(),excluded_calendar_dates=excluded)
    audit['known_interval_price_coverage']=price_coverage(raw,universe,calendar,skipped)
    return raw,events,calendar,universe,payload,values,original_values,audit


def make_tables(prices,events,calendar,universe,scope,scheduled_days=None):
    weekly=weekly_signal_days(calendar,2)
    ends={d[:7]:d for d in calendar}
    valid={sid:sorted(d for d,r in rows.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for sid,rows in prices.items()}
    tables={}
    for day in calendar:
        scheduled=(day in scheduled_days) if scheduled_days is not None else (day in weekly or day==ends[day[:7]] and day[:7]<calendar[-1][:7])
        if day<'2019-10-01' or not scheduled:continue
        table=signal_table(prices,events,calendar,day,valid)
        if scope=='B':
            # B只讓當時上市櫃股票參與橫斷面排名；興櫃僅貢獻較早歷史。
            allowed=[(sid,row) for sid,row in table.items() if universe.market_at(sid,day) in ('TWSE','TPEX')]
            table={sid:{**row,'rank':i,'top':i<=math.ceil(len(allowed)*.25),'outside':i>math.ceil(len(allowed)*.40)}
                   for i,(sid,row) in enumerate(allowed,1)}
        tables[day]=table
    return tables,valid


def run_case(prices,events,calendar,tables,features,values,universe,scope,mode,lag,slippage,settings):
    gate=TechnicalEntryGate(SnapshotGate(values,lag,rule='three_month_revenue'),features,mode) if mode!='momentum' else None
    def entry(sid,day):
        if scope!='C' and universe.market_at(sid,day) not in ('TWSE','TPEX'):return False
        return gate(sid,day) if gate else True
    execution=MarketExecution(universe,allow_emerging=scope=='C',slippage=slippage)
    result=simulate_weekly(prices,events,calendar,tables,entry_filter=entry,execution_price=execution,
                           execution_capacity=execution.capacity,
                           sale_cash_at_close=lambda sid,day:universe.market_at(sid,day)=='EMERGING',
                           reserve_buy_cash_until_close=lambda sid,day:universe.market_at(sid,day)=='EMERGING',**settings)
    summary=summarize(result)
    pnl=[r['proceeds']-r['cost'] for r in result[1]]
    loss=-sum(p for p in pnl if p<0)
    summary['profit_factor']=sum(p for p in pnl if p>0)/loss if loss else None
    summary['buy_market_counts']=dict(Counter(universe.market_at(r['stock_id'],r['date']) for r in result[3] if r['filled']))
    summary['closed_by_entry_market']={}
    for market in ('TWSE','TPEX','EMERGING'):
        trades=[r for r in result[1] if universe.market_at(r['stock_id'],r['entry'])==market]
        summary['closed_by_entry_market'][market]=dict(count=len(trades),pnl=sum(r['proceeds']-r['cost'] for r in trades))
    summary['held_outside_eligible']=sorted(s for s in result[4] if not universe.eligible(s,calendar[-1]))
    summary['unresolved_stale_value']=sum(p['remaining']*p['original_shares']*p['mark'] for s,p in result[4].items() if not universe.eligible(s,calendar[-1]))
    summary['ending_equity_without_ineligible_holdings']=summary['ending_equity']-summary['unresolved_stale_value']
    summary['7610_roundtrips']=[r for r in result[1] if r['stock_id']=='7610']
    summary['execution_rejections']=dict(Counter(r['reason'] for r in execution.audit if r['reason']!='available'))
    summary['capacity_rejections']=[r for r in execution.capacity_audit if not r['passed']]
    summary['emerging_max_daily_volume_participation']=execution.participation
    return result,summary,execution.audit,gate.audit if gate else []


def render(report,out):
    rows=[];md=['# 含興櫃市場回測','',report['disclosure'],'',
        '2020/1/2–2026/9/7，100萬元，5%淨值配置，週三訊號、休市順延，動能3−1，三日均成交金額>1億，30%高點回落及月底動能出場。',
        '營收：最近三個月金額依序增加，最新月YoY正；月底後15/30日曆天為假設取得日，次日起可用。','',
        '|版本|濾網|延遲|滑價每邊|期末萬元|總報酬|年化|最大回撤|勝率|完成交易|興櫃買入|資格結束未解決估值萬元|',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for key,s in report['scenarios'].items():
        m=s['stale_scenario'];fields=[LABELS[s['scope']],MODE_LABELS[s['mode']],str(s['lag'] or '—'),f"{s['slippage']:.1%}",
            f"{s['ending_equity']/10000:,.2f}",f"{m['total']:.2%}",f"{m['cagr']:.2%}",f"{m['max_drawdown']:.2%}",
            f"{s['win_rate']:.2%}",str(s['closed_roundtrips']),str(s['buy_market_counts'].get('EMERGING',0)),f"{s['unresolved_stale_value']/10000:.2f}"]
        md.append('|'+'|'.join(fields)+'|')
        rows.append('<tr>'+''.join(f'<td>{html.escape(x)}</td>' for x in fields)+f'<td><a href="{key}/交易紀錄.html">交易</a></td></tr>')
    conclusions=[]
    for mode in ('base','all'):
        for lag in (15,30):
            a,b,c=[report['scenarios'][f'{scope}_{mode}_{lag}_s0']['ending_equity'] for scope in 'ABC']
            conclusions.append(f'{mode}／{lag}天：B−A={(b-a)/10000:+.2f}萬元；C−B={(c-b)/10000:+.2f}萬元；C−A={(c-a)/10000:+.2f}萬元。')
    esb_rows=[]
    esb_md=['','## 直接在興櫃買入的完成交易（額外滑價0）','','|版本|交易數|已實現淨損益萬元|','|---|---:|---:|']
    for mode in ('base','all'):
        for lag in (15,30):
            market=report['scenarios'][f'C_{mode}_{lag}_s0']['closed_by_entry_market']['EMERGING']
            fields=[f'{MODE_LABELS[mode]}／{lag}天',str(market['count']),f"{market['pnl']/10000:+.2f}"]
            esb_md.append('|'+'|'.join(fields)+'|')
            esb_rows.append('<tr>'+''.join(f'<td>{x}</td>' for x in fields)+'</tr>')
    esb_md+=['','這是按買入當時市場分類的已實現損益，不是C−B的因果差額。市場加入後也會改變上市櫃排名、買入順位與資金配置。','']
    key_comparisons=[(report['scenarios'][f'B_{mode}_{lag}_s0']['ending_equity']-report['scenarios'][f'A_{mode}_{lag}_s0']['ending_equity'],
                     report['scenarios'][f'C_{mode}_{lag}_s0']['ending_equity']-report['scenarios'][f'B_{mode}_{lag}_s0']['ending_equity']) for mode in ('base','all') for lag in (15,30)]
    conclusions.append(f'四個主要版本中，允許興櫃歷史資料後有{sum(a>0 for a,b in key_comparisons)}個提高收益；再允許興櫃買入後有{sum(b>0 for a,b in key_comparisons)}個提高收益。不能將兩種效果混為一談。')
    for lag in (15,30):
        trades=report['scenarios'][f'B_all_{lag}_s0']['7610_roundtrips']
        if trades:
            t=trades[-1]
            conclusions.append(f'B三項合併／{lag}天的7610：{t["entry"]}→{t["exit"]}，單筆淨報酬{t["return_net"]:.2%}，已實現{(t["proceeds"]-t["cost"])/10000:+.2f}萬元；買入時已上市，較早興櫃資料用於滿足暖機。此大額貢獻須注意集中度，不能推論未來可重複。')
    md+=['','## 差額拆解','',*[f'- {x}' for x in conclusions],*esb_md,'','## 資料限制','',
        'A/B/C使用相同新增營收快照；A另與先前基準對帳。B排名僅限當時上市上櫃，C排名包含已核實興櫃，所以C−B也包含排名及資金配置改變，並非興櫃交易損益加總。',
        '興櫃VWAP是成交日結束後才知道的成交代理，僅用於執行前一日已排定委託；不拿來決定前一天的股票或預算。VWAP賣出所得當日買單處理完才入現金，不能倒流支付同日上午買入。無歷史買賣報價、逐筆可成交量，不能認證可實現收益。',
        '興櫃不使用FinMind open（前日均價）。close為最後成交，用於訊號與收市估值；當日高點用真正max。',
        '興櫃每筆委託股數不得超過當日總成交股數1%；超額買單取消，賣單延後重試，未做部分成交。此為成交容量假設。',
        '興櫃買單按預排順位鎖定預算，未成交部分日終才釋放，不利用當日日終資訊挪用資金至其他當日開盤買單。',
        '新股若沒有200個已核實有效交易日即不入選。無資格的期間、營收衝突或缺值拒絕；終止交易未能出清的部位依引擎保留舊估值，另揭露零估值情境與未解決價值。',
        '公司行動、股利現金流、營收修訂及實際公告時點仍不完整；同一歷史樣本多次調參，不是樣本外績效。',
        '',f"已核實區間的行情涵蓋（排除已證明不足暖機而省略下載者）：{json.dumps(report['data_audit']['known_interval_price_coverage'],ensure_ascii=False)}",'',
        f"資料coverage：{json.dumps(report['universe']['coverage'],ensure_ascii=False)}",'',
        '來源：[FinMind欄位定義](https://finmind.github.io/tutor/TaiwanMarket/Technical/)、[官方興櫃登錄](https://www.tpex.org.tw/zh-tw/esb/listed/ipo.html)。來源原文與雜湊在JSON。','']
    charts=''
    for lag in (15,30):
        series=[]
        for scope in 'ABC':
            with (out/f'{scope}_base_{lag}_s0/nav.csv').open(encoding='utf-8-sig') as f:series.append(list(csv.DictReader(f)))
        top=max(1000000,math.ceil(max(float(r['nav_stale']) for rs in series for r in rs)/1000000)*1000000)
        content=''
        for amount in range(0,top+1,1000000):
            y=260-amount/top*230
            content+=f'<line x1="70" x2="1130" y1="{y}" y2="{y}" stroke="#ddd"/><text x="60" y="{y+4}" text-anchor="end">{amount//10000}萬</text>'
        for scope,rs,color in zip('ABC',series,('#596579','#187b71','#b65236')):
            points=' '.join(f'{70+i/max(1,len(rs)-1)*1060:.1f},{260-float(r["nav_stale"])/top*230:.1f}' for i,r in enumerate(rs))
            content+=f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
        charts+=f'<h3>營收基準／延遲{lag}天（灰A、綠B、橘C）</h3><svg viewBox="0 0 1180 300" role="img" aria-label="每格100萬元淨值比較">{content}<text x="70" y="290">2020/01</text><text x="1050" y="290">2026/09</text></svg>'
    table_head=''.join(f'<th>{x}</th>' for x in ['版本','濾網','延遲','滑價每邊','期末萬元','總報酬','年化','最大回撤','勝率','完成交易','興櫃買入','資格結束未解決估值萬元','明細'])
    page=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>含興櫃市場回測</title>
<style>body{{font:16px/1.65 system-ui;background:#f4f6f8;color:#203149;margin:0}}main{{max-width:1450px;margin:auto;padding:28px}}section{{background:white;padding:20px;margin:20px 0;border-radius:12px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:9px;border-bottom:1px solid #dde3e9;text-align:right}}th:first-child,td:first-child{{text-align:left}}.warning{{background:#fff0d7;padding:18px}}svg{{width:100%}}</style><main><h1>含興櫃市場回測</h1><p>2020/1/2–2026/9/7｜100萬元｜每筆5%NAV｜週三訊號｜30%高點回落</p><p class="warning">{html.escape(report['disclosure'])}</p><section><p>A：嚴格上市櫃；B：只買上市櫃、可用興櫃歷史；C：允許興櫃買入。base＝營收基準，all＝再加個股趨勢、大盤條件及乖離≤10%。</p><p>上市櫃次日開盤代理；興櫃次日VWAP代理，滑價0/0.5%/1%每邊另計，原每邊0.3%成本不變。興櫃完整委託不得超過當日成交股數1%；超額買單取消、賣單延後重試。</p><ul>{''.join('<li>'+x+'</li>' for x in conclusions)}</ul></section><section class="scroll"><table><tr>{table_head}</tr>{''.join(rows)}</table></section><section>{charts}</section><section><h2>稽核與限制</h2><p>歷史股票池與營收涵蓋、缺值／衝突、未解決停牌估值及市場別成交歸因詳見JSON。每日淨值、交易和執行價格稽核均保留CSV。</p><p>營收15/30天是假設可知時點；歷史修訂、股利／公司行動與興櫃實際成交條件尚未完整認證。</p><a href="comparison.json">完整結果與來源JSON</a></section></main></html>'''
    esb_section='<section><h2>直接在興櫃買入的完成交易</h2><p>以下為額外滑價0的模型交易淨損益，按買入當時市场分類；不等於C−B差額。</p><table><tr><th>版本</th><th>完成交易</th><th>已實現淨損益萬元</th></tr>'+''.join(esb_rows)+'</table></section>'
    page=page.replace('<section><h2>稽核與限制</h2>',esb_section+'<section><h2>稽核與限制</h2>',1)
    (out/'比較報告.html').write_text(page,encoding='utf-8')
    Path('analysis/momentum-emerging-2026-09-09.md').write_text('\n'.join(md),encoding='utf-8')


def trade_html(trades,universe,path):
    headings=['股票代號','買入日','賣出日','投入金額','賣出淨額','淨損益','淨報酬率','持有日數','出場原因','買入市場']
    reasons={'trailing_stop':'高點回落30%','nonpositive':'動能不再為正','rank_below_40pct':'動能排名落出前40%',
             'two_declines':'動能連續兩次下降','missing_signal':'缺少有效動能訊號'}
    markets={'TWSE':'上市','TPEX':'上櫃','EMERGING':'興櫃'}
    rows=[]
    for r in trades:
        fields=[r['stock_id'],r['entry'],r['exit'],f"{r['cost']:,.0f}",f"{r['proceeds']:,.0f}",
                f"{r['proceeds']-r['cost']:+,.0f}",f"{r['return_net']:+.2%}",str(r['days']),
                reasons.get(r['last_reason'],r['last_reason']),markets.get(universe.market_at(r['stock_id'],r['entry']),'未知')]
        rows.append('<tr>'+''.join(f'<td>{html.escape(x)}</td>' for x in fields)+'</tr>')
    path.write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>交易紀錄</title><style>body{font:16px/1.6 system-ui;padding:24px;color:#203149}table{border-collapse:collapse;white-space:nowrap}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:right}tr:nth-child(even){background:#f1f5f8}.scroll{overflow:auto}</style><h1>完整買入至賣出紀錄</h1><p><a href="../比較報告.html">返回比較</a>｜金額為新台幣，含模型交易成本；尚未平倉部位不在完成交易表內。興櫃採VWAP代理成交。</p><div class="scroll"><table><tr>'+''.join(f'<th>{k}</th>' for k in headings)+'</tr>'+''.join(rows)+'</table></div></html>',encoding='utf-8')


def mark_legacy_execution():
    marker='<!-- emerging-execution-correction -->'
    note=marker+'\n> **興櫃成交更正（2026-09-09）：舊版將FinMind興櫃open誤當當日開盤價，實際為前日均價，且原high-envelope可能將前日均價抬入當日高點。因此舊版亦不能當成有效的「含興櫃回測」。請改看 [含興櫃重新建模報告](momentum-emerging-2026-09-09.md)，其中保留成交與歷史資料限制。**\n\n'
    for name in ('momentum-entry-filters','momentum-entry-return-attribution','momentum-revenue-three-month','momentum-revenue-research'):
        path=Path('analysis')/(name+'-2026-09-09.md')
        if path.exists():
            text=path.read_text(encoding='utf-8')
            if marker not in text:path.write_text(note+text,encoding='utf-8')
    banner=marker+'<p style="background:#ffe5d9;padding:18px;color:#70230d"><b>興櫃成交更正：舊版open實為前日均價，不能當作有效的含興櫃回測。</b> <a href="../momentum_rerun_20260909_emerging/比較報告.html">新版興櫃成交模型與比較</a></p>'
    for name in ('entry_filters','revenue_three_month','revenue_research'):
        path=Path('backtest')/('momentum_rerun_20260909_'+name)/'比較報告.html'
        if path.exists():
            text=path.read_text(encoding='utf-8')
            if marker not in text:path.write_text(text.replace('<main>','<main>'+banner,1),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-snapshot-research',action='store_true')
    args=parser.parse_args()
    if not args.allow_snapshot_research:parser.error('需要明示 --allow-snapshot-research；非PIT認證')
    raw,events,calendar,universe,payload,values,original_values,audit=load_inputs()
    old=json.loads(Path('backtest/momentum_rerun_20260909_entry_filters_listed/comparison.json').read_text(encoding='utf-8'))
    market_payload=json.loads(Path('data/momentum_pit/entry_market_index/market_prices.json').read_text(encoding='utf-8'))
    from backfill_entry_market_index import load_market_prices
    market=load_market_prices(Path('data/momentum_pit/entry_market_index/market_prices.json'))
    listed=ListedUniverse([r for r in payload['records'] if r['market'] in ('TWSE','TPEX')])
    all_prices=prepare_prices(raw,universe)
    # ESB已清除前日均價，不會被當成當日高點；其他矛盾仍保留舊診斷口徑並揭露。
    all_prices,high_audit=diagnostic_high_envelope(all_prices)
    strict=listed.restrict_prices(all_prices)
    report=dict(disclosure='正式執行的歷史日線研究：興櫃用次日VWAP成交代理，並非完整歷史資訊時點或實際可成交收益認證。',
                universe=payload,data_audit=audit,settings=old['settings'],scenarios={},high_adjusted_rows=len(high_audit),market_source=market_payload['source'])
    OUT.mkdir(parents=True,exist_ok=True)
    cutoff='2022-12-30';pc=[d for d in calendar if d<=cutoff];pe={k:v for k,v in events.items() if k[1]<=cutoff}
    pv={k:v for k,v in values.items() if k[1]<=cutoff[:7]}
    pu=MarketUniverse([{**r,'end':r.get('end') if r.get('end') and r['end']<=cutoff else None} for r in payload['records'] if r['start']<=cutoff])
    raw_prefix={s:{d:r for d,r in rows.items() if d<=cutoff} for s,rows in raw.items()}
    independent_prefix,_=diagnostic_high_envelope(prepare_prices(raw_prefix,pu))
    assert independent_prefix=={s:{d:r for d,r in rows.items() if d<=cutoff} for s,rows in all_prices.items()}
    del raw,raw_prefix,independent_prefix
    for scope in 'ABC':
        prices=strict if scope=='A' else all_prices
        tables,valid=make_tables(prices,events,calendar,universe,scope)
        features=TechnicalFeatures(prices,events,calendar,market)
        pp={s:{d:r for d,r in rs.items() if d<=cutoff} for s,rs in prices.items()}
        # 交易所日曆是已知排程；截斷在完整年底，仍保留該月底訊號。
        pt,pvalid=make_tables(pp,pe,pc,pu,scope,scheduled_days=set(tables))
        assert pt=={d:t for d,t in tables.items() if d<=cutoff}
        pf=TechnicalFeatures(pp,pe,pc,{d:v for d,v in market.items() if d<=cutoff})
        specs=[('momentum',None,0.)]+[(mode,lag,slip) for mode in ('base','all') for lag in (15,30) for slip in ((0.,.005,.01) if scope=='C' else (0.,))]
        for mode,lag,slip in specs:
            key=f'{scope}_{mode}_{lag or 0}_s{int(slip*10000)}'
            result,s,execution,gates=run_case(prices,events,calendar,tables,features,values,universe,scope,mode,lag,slip,old['settings'])
            prefix,_,_,_=run_case(pp,pe,pc,pt,pf,pv,pu,scope,mode,lag,slip,old['settings'])
            assert prefix[0]==[r for r in result[0] if r['date']<=cutoff],key
            assert prefix[2]==[r for r in result[2] if r['exit']<=cutoff],key
            positions={d:i for i,d in enumerate(calendar)}
            for order in result[3]:
                if not order['filled']:continue
                sid,day=order['stock_id'],order['date'];signal=calendar[positions[day]-1]
                assert sid in tables[signal] and universe.eligible(sid,signal) and universe.eligible(sid,day)
                if scope!='C':assert universe.market_at(sid,day) in ('TWSE','TPEX')
                start=calendar[max(0,positions[signal]+1-252)]
                assert bisect_right(valid[sid],signal)-bisect_left(valid[sid],start)>=200
            s.update(scope=scope,mode=mode,lag=lag,slippage=slip,prefix_invariance=True)
            if scope=='A':
                oldkey='momentum' if mode=='momentum' else f'{mode}_{lag}'
                s['previous_A_ending_equity']=old['scenarios'][oldkey]['ending_equity']
                s['previous_A_delta']=s['ending_equity']-s['previous_A_ending_equity']
            report['scenarios'][key]=s
            folder=OUT/key;folder.mkdir(exist_ok=True)
            for title,rows in zip(('nav','roundtrips','sell_legs','orders'),result[:4]):save_csv(folder/(title+'.csv'),rows)
            save_csv(folder/'execution_audit.csv',execution)
            if gates:save_csv(folder/'gate_audit.csv',gates)
            trade_html(result[1],universe,folder/'交易紀錄.html')
            (OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(key,s['ending_equity'],s['stale_scenario']['max_drawdown'],s['buy_market_counts'],flush=True)
        checks=0
        for (sid,day),r in features.stock_cache.items():
            if day<=cutoff:assert pf.stock(sid,day)==r;checks+=1
        for day,r in features.market_cache.items():
            if day<=cutoff:assert pf.market(day)==r;checks+=1
        report.setdefault('feature_prefix_checks',{})[scope]=checks
    (OUT/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    render(report,OUT)
    mark_legacy_execution()
    print('Complete',len(report['scenarios']),flush=True)


if __name__=='__main__':main()
