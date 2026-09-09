"""分別比較個股趨勢、大盤趨勢與追高限制；固定營收快照研究。"""
import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from bisect import bisect_left, bisect_right
from pathlib import Path

from dynamic_momentum import load_data, signal_table
from research_revenue_momentum import SnapshotGate, load_snapshot_values, save_csv
from weekly_hermes_momentum import diagnostic_high_envelope, simulate_weekly, summarize, weekly_signal_days
from momentum_entry_filters import TechnicalFeatures, TechnicalEntryGate, MODES, LABELS


OUTPUT = Path('backtest/momentum_rerun_20260909_entry_filters')


def mark_legacy_reports():
    marker='<!-- listed-universe-correction -->'
    note=marker+'\n> **股票池更正（2026-09-09）：以下為舊版追溯結果，曾包含未核實上市／上櫃區間的交易或暖機資料，不符合目前股票池規則。績效與結論請以 [限定版重跑報告](momentum-entry-listed-2026-09-09.md) 為準；舊數字保留供核對。**\n\n'
    for filename in ('momentum-entry-filters-2026-09-09.md','momentum-entry-return-attribution-2026-09-09.md',
                     'momentum-revenue-three-month-2026-09-09.md','momentum-revenue-research-2026-09-09.md'):
        path=Path('analysis')/filename
        if path.exists():
            content=path.read_text(encoding='utf-8')
            if marker not in content:path.write_text(note+content,encoding='utf-8')
    banner=marker+'<p style="background:#ffe4db;color:#70230d;padding:18px;border:2px solid #c15d40"><b>股票池更正：本頁為舊結果，不符合目前歷史上市／上櫃限定；含未核實區間的交易或暖機資料。</b> <a href="../momentum_rerun_20260909_entry_filters_listed/比較報告.html">請看修正後重跑報告</a>。</p>'
    for folder in ('momentum_rerun_20260909_entry_filters','momentum_rerun_20260909_revenue_three_month','momentum_rerun_20260909_revenue_research'):
        path=Path('backtest')/folder/'比較報告.html'
        if path.exists():
            content=path.read_text(encoding='utf-8')
            if marker not in content:path.write_text(content.replace('<main>','<main>'+banner,1),encoding='utf-8')


def render_reports(report, out):
    scenarios = report['scenarios']
    listed = report.get('listed_only',False)
    verify_text = ('已依有效上市／上櫃区間修正股票池，舊績效不應延用。' if listed else '兩個基準與上一輪精確重現。')
    scope_text = ('僅使用已核實的歷史上市／上櫃價格；興櫃資料不計入200日暖機、排名或均線。資格證據缺口的股票／期間排除，仍有涵蓋偏誤。' if listed else '此為舊版追溯結果，未按歷史上市上櫃資格限制，可能含興櫃交易或暖機資料，不符合目前股票池規則。')
    improved = [f'{LABELS[mode]}／{lag}天' for lag in (15,30) for mode in MODES if mode!='base'
                and scenarios[f'{mode}_{lag}']['win_rate'] > scenarios[f'base_{lag}']['win_rate']]
    conclusions = [('本輪沒有新增條件提高相對同延遲基準的勝率；先前提高勝率的假設未獲支持。'
                    if not improved else '勝率提高的組合：'+'、'.join(improved)+'。')]
    for lag in (15,30):
        base, combined = scenarios[f'base_{lag}'], scenarios[f'all_{lag}']
        conclusions.append(f'三項合併／{lag}天：期末相對基準{(combined["ending_equity"]-base["ending_equity"])/10000:+.2f}萬元；'
                           f'最大回撤差{(combined["stale_scenario"]["max_drawdown"]-base["stale_scenario"]["max_drawdown"])*100:+.2f}百分點（正值表示回撤縮小）；'
                           f'勝率差{(combined["win_rate"]-base["win_rate"])*100:+.2f}百分點；平均現金{base["mean_cash_fraction"]:.2%}→{combined["mean_cash_fraction"]:.2%}。')
    conclusions.append('勝率與收益／回撤是不同目標。三項合併提高現金比例，風險曝險改變是重要取捨；全期回撤縮小不等於每個年度較抗跌。不可用全期結果直接宣稱穩定優勢。')
    header = '|版本|期末萬元|累積報酬|年化|最大回撤|勝率|獲利因子|完成交易|平均持有天|平均現金|'
    markdown = ['# 進場條件提高勝率探索比較', '', '2026-09-09；2020-01-02至2026-09-07；100萬元、5%NAV、週三休市順延、次交易日開盤。', '',
                '基準為動能3−1＋三日均成交金額>1億＋三個月營收金額遞增且最新月YoY>0。只篩新倉，30%高點回落及月底動能出場不變。', '',
                '## 固定條件', '',
                '- 個股趨勢：收盤>20MA，且20MA高於五個市場交易日前；兩段均線用訊號日單位換算已知分割／面額變更。',
                '- 大盤：加權價格指數收盤>60MA。使用獨立保存的官方價格指數，不代用報酬指數。',
                '- 追高限制：先滿足個股趨勢，再要求收盤<=20MA×1.10；10%為預先固定研究設定。',
                '- 合併：另測個股＋大盤，以及三項全部。營收月底後15／30日曆天視為假設可知、次日起可用。', '',
                '## 結果判讀', '', scope_text, '', *[f'- {s}' for s in conclusions], '']
    sections = ''
    charts = ''
    colors = ['#58667a','#137f72','#b76b14','#8461b5','#d1475b','#186cc3']
    for lag in (15,30):
        rows = ''
        markdown += [f'## 延遲{lag}天', '', header, '|---|'+ '---:|'*9]
        series = {}
        for mode in MODES:
            key = f'{mode}_{lag}'
            s = scenarios[key]
            m = s['stale_scenario']
            fields = [LABELS[mode],f'{s["ending_equity"]/10000:,.2f}',f'{m["total"]:.2%}',f'{m["cagr"]:.2%}',
                      f'{m["max_drawdown"]:.2%}',f'{s["win_rate"]:.2%}',f'{s["profit_factor"]:.3f}',
                      str(s['closed_roundtrips']),f'{s["mean_holding_days"]:.1f}',f'{s["mean_cash_fraction"]:.2%}']
            markdown.append('|'+'|'.join(fields)+'|')
            rows += '<tr><th>'+fields[0]+'</th>'+''.join(f'<td>{v}</td>' for v in fields[1:])+'</tr>'
            with (out/key/'nav.csv').open(encoding='utf-8-sig') as f:
                series[mode] = list(csv.DictReader(f))
        markdown += ['', '年度報酬（2026至9/7）：', '', '|年度|'+'|'.join(LABELS.values())+'|', '|---|'+'---:|'*len(MODES)]
        years = ''
        for i,r in enumerate(scenarios[f'base_{lag}']['yearly']):
            fields = [r['year']]+[f'{scenarios[f"{mode}_{lag}"]["yearly"][i]["return_net"]:.2%}' for mode in MODES]
            markdown.append('|'+'|'.join(fields)+'|')
            years += '<tr>'+''.join(f'<td>{x}</td>' for x in fields)+'</tr>'
        markdown.append('')
        th = ''.join(f'<th>{x}</th>' for x in header.strip('|').split('|'))
        sections += f'<section class="scroll"><h2>延遲{lag}天</h2><table><tr>{th}</tr>{rows}</table><h3>年度報酬</h3><table><tr><th>年</th>'+''.join(f'<th>{x}</th>' for x in LABELS.values())+f'</tr>{years}</table></section>'
        top = math.ceil(max(float(r['nav_stale']) for rs in series.values() for r in rs)*1.04/1e6)*1000000
        chart = ''
        for value in range(0, top+1, 1000000):
            y = 250-value/top*220
            chart += f'<line x1="70" y1="{y}" x2="1140" y2="{y}" stroke="#dce3ec"/><text x="60" y="{y+4}" text-anchor="end" font-size="12">{value//10000}萬</text>'
        legend = ''
        for (mode,rs),color in zip(series.items(),colors):
            points = ' '.join(f'{70+i/(len(rs)-1)*1070:.1f},{250-float(r["nav_stale"])/top*220:.1f}' for i,r in enumerate(rs))
            chart += f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
            legend += f'<span style="color:{color};margin-right:18px">● {LABELS[mode]}</span>'
        charts += f'<h3>延遲{lag}天</h3><p>{legend}</p><svg viewBox="0 0 1180 285" role="img" aria-label="延遲{lag}天六組淨值比較">{chart}<text x="70" y="277">2020/01</text><text x="1140" y="277" text-anchor="end">2026/09</text></svg>'
    limitations = scope_text+'現有營收快照、15／30天為假設取得時點，不能排除修訂與實際公告時點偏誤。原股利、公司行動、歷史股票池及矛盾高價診斷限制延續；同一期間反覆用於調整規則，並非獨立樣本外驗證。'
    markdown += ['## 驗證與限制', '', limitations, '',
                 f'{verify_text}{len(scenarios)}組模擬通過2022年底截斷NAV／賣出前綴一致性；另以截斷資料重算技術特徵，共{report["prefix_feature_checks"]:,}筆一致。這是程式在假設下的驗證，不是歷史版本時點認證。', '',
                 '重現：`python compare_momentum_entry_filters.py --allow-snapshot-research`；先完成`python backfill_entry_market_index.py`。', '',
                 f'ignored產物 `{out.as_posix()}/`：比較報告.html、comparison.json、每組nav/roundtrips/sell_legs/orders/gate_audit.csv；市場原始資料獨立保存在`data/momentum_pit/entry_market_index/`。未改實盤。', '']
    scope_html=''
    if listed:
        audit=report['universe_audit']
        transfers=report['universe_metadata']['coverage'].get('confirmed_transfer_with_unknown_prior_start_count',0)
        gaps_text=f'資格缺口：{len(audit["unknown_stock_ids"])}檔候選缺完整起日，其中{len(audit["unknown_with_prices"])}檔有原始行情；另{transfers}檔已確認轉板但未核實原上櫃起日，其較早期間排除，不能一概稱為興櫃。這是保守涵蓋偏誤，並非完整歷史股票池。'
        scope_rows=''
        markdown += ['## 上市上櫃股票池修正前後', '',
                     f'原候選{audit["candidate_count"]}檔。共排除{audit["removed_price_rows"]:,}筆未核實區間行情。未知起日不猜，上市終止後不假造賣出清算。{gaps_text}', '',
                     '|版本|修正前期末萬元|修正後期末萬元|差額萬元|修正後交易|', '|---|---:|---:|---:|---:|']
        for name in ('base_15','all_15','base_30','all_30'):
            old=report['previous_unrestricted'][name]
            s=scenarios[name]
            fields=[name,f'{old["ending_equity"]/10000:.2f}',f'{s["ending_equity"]/10000:.2f}',
                    f'{(s["ending_equity"]-old["ending_equity"])/10000:+.2f}',str(s['closed_roundtrips'])]
            markdown.append('|'+'|'.join(fields)+'|')
            scope_rows+='<tr>'+''.join(f'<td>{x}</td>' for x in fields)+'</tr>'
        pure=scenarios['momentum']
        pure_text=f'修正後無營收／技術濾網的純動能：期末{pure["ending_equity"]/10000:.2f}萬元，MDD{pure["stale_scenario"]["max_drawdown"]:.2%}，勝率{pure["win_rate"]:.2%}，完成{pure["closed_roundtrips"]}筆。'
        markdown += ['',pure_text,'',f'股票池來源coverage與未知名單詳見comparison.json；資格結束但尚未平倉的部位逐組記錄held_outside_eligible，維持未解決標價，不當作已變現現金。','']
        scope_html=f'<section class="scroll"><h2>股票池修正前後</h2><p>{scope_text}排除{audit["removed_price_rows"]:,}筆未核實區間行情。{gaps_text}</p><table><tr><th>版本</th><th>舊期末萬元</th><th>新期末萬元</th><th>差額萬元</th><th>新交易數</th></tr>{scope_rows}</table><p>{pure_text}</p><p>資格結束後的未平倉部位仍按原引擎列未解決標價，不能當作已變現現金，詳見JSON。</p></section>'
    analysis_path=Path('analysis/momentum-entry-listed-2026-09-09.md' if listed else 'analysis/momentum-entry-filters-2026-09-09.md')
    analysis_path.write_text('\n'.join(markdown),encoding='utf-8')
    html = f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>進場條件勝率比較</title>
<style>body{{font:16px/1.65 system-ui;margin:0;background:#f3f6fa;color:#213149}}main{{max-width:1350px;margin:auto;padding:28px}}section{{background:white;padding:22px;margin:20px 0;border-radius:12px}}.warning{{background:#fff1d7;padding:18px;border-left:5px solid #ce8720}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}td,th{{padding:10px;text-align:right;border-bottom:1px solid #dce3ec}}th:first-child{{text-align:left}}.scroll{{overflow-x:auto}}svg{{width:100%;height:auto}}</style><main>
<h1>進場條件能否提高勝率？</h1><p>2020/01/02–2026/09/07｜100萬元｜每筆5%NAV｜週三休市順延｜次交易日開盤</p><p class="warning">{limitations}</p>
<section><h2>本輪固定定義</h2><p>基準：動能3−1＋三日平均成交金額>1億元＋三個月營收金額遞增且最新YoY>0。維持30%高點回落與月底動能出場。</p><ul><li>個股趨勢：close > SMA20(t) 且 SMA20(t) > SMA20(t−5市場交易日)。</li><li>大盤：加權價格指數close > SMA60(t)。</li><li>追高限制：在個股趨勢成立後，close <= 1.10×SMA20(t)。10%為本輪預設，未做網格挑選。</li><li>分別測試，再比較個股＋大盤及三項合併；僅影響新倉。缺所需均線資料拒絕，不補值。</li></ul><p>15／30天從營收月份月底起算日曆天，假設可知日後才用。完成交易是一買至全部賣出；持有天數為已完成交易平均日曆天數。PF為已完成交易總獲利÷總虧損絕對值。</p></section>
<section><h2>結果判讀</h2><ul>{''.join(f'<li>{s}</li>' for s in conclusions)}</ul></section>
{scope_html}{sections}<section><h2>每日淨值（每格100萬元）</h2>{charts}</section><section><h2>驗證與資料</h2><p>{verify_text}{len(scenarios)}組截斷模擬及{report['prefix_feature_checks']:,}筆技術特徵前綴一致。勝率需要與收益、回撤、平均現金及年度取捨一併判讀。</p><a href="comparison.json">完整結果與資料指紋JSON</a><p>各版本子目錄提供交易、委託、淨值與篩選原因CSV。</p></section></main></html>'''
    (out/'比較報告.html').write_text(html,encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-snapshot-research',action='store_true')
    parser.add_argument('--legacy-unrestricted',action='store_true',help='僅供重現舊研究；明示不符合上市上櫃限定')
    args = parser.parse_args()
    if not args.allow_snapshot_research:
        parser.error('需明示 --allow-snapshot-research，非PIT認證')
    from backfill_entry_market_index import load_market_prices
    old = json.loads(Path('backtest/momentum_rerun_20260909_revenue_three_month/comparison.json').read_text(encoding='utf-8'))
    values, conflicts = load_snapshot_values(Path('data/momentum_pit/revenue_archive'))
    revenue_hash = hashlib.sha256(json.dumps(sorted((s,m,v) for (s,m),v in values.items())).encode()).hexdigest()
    assert revenue_hash == old['revenue_fingerprint'], '營收快照已變'
    prices, events, calendar, _, fingerprint, excluded = load_data()
    raw_prices=prices
    assert fingerprint == old['price_fingerprint'], '原價格資料已變'
    universe=None
    universe_audit=None
    universe_payload=None
    out=OUTPUT if args.legacy_unrestricted else Path(str(OUTPUT)+'_listed')
    if not args.legacy_unrestricted:
        from listed_universe import ListedUniverse
        universe_path=Path('data/momentum_pit/listed_universe/intervals.json')
        universe_payload=json.loads(universe_path.read_text(encoding='utf-8'))
        universe=ListedUniverse.from_json(universe_path)
        masked=universe.restrict_prices(prices)
        unknown=sorted(set(prices)-set(universe.intervals))
        universe_audit=dict(candidate_count=len(prices),unknown_stock_ids=unknown,
                            unknown_with_prices=[sid for sid in unknown if prices[sid]],
                            removed_price_rows=sum(len(prices[s])-len(masked[s]) for s in prices),
                            eligible_price_rows=sum(len(rows) for rows in masked.values()),
                            file_sha256=hashlib.sha256(universe_path.read_bytes()).hexdigest())
        prices=masked
    prices, high_audit = diagnostic_high_envelope(prices)
    market_path = Path('data/momentum_pit/entry_market_index/market_prices.json')
    market = load_market_prices(market_path)
    market_metadata = json.loads(market_path.read_text(encoding='utf-8'))
    features = TechnicalFeatures(prices,events,calendar,market)
    signal_days = weekly_signal_days(calendar,2)
    missing = [d for d in sorted(signal_days) if d>='2020-01-01' and features.market(d)['market_reason']!='available']
    if missing:
        raise ValueError(f'市場MA60資料缺口，不可用錯誤全現金結果替代: {missing}')
    ends = {d[:7]:d for d in calendar}
    valid = {s:sorted(d for d,r in rows.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for s,rows in prices.items()}
    tables = {d:signal_table(prices,events,calendar,d,valid) for d in calendar if d>='2019-10-01' and
              (d in signal_days or (d==ends[d[:7]] and d[:7]<calendar[-1][:7]))}
    cutoff = '2022-12-30'
    pp = {s:{d:r for d,r in rows.items() if d<=cutoff} for s,rows in prices.items()}
    if universe:
        prefix_records=[{**r,'end':r.get('end') if r.get('end') and r['end']<=cutoff else None}
                        for r in universe_payload['records'] if r['start']<=cutoff]
        prefix_universe=ListedUniverse(prefix_records)
        raw_prefix={s:{d:r for d,r in rows.items() if d<=cutoff} for s,rows in raw_prices.items()}
        independent_prefix,_=diagnostic_high_envelope(prefix_universe.restrict_prices(raw_prefix))
        assert independent_prefix==pp
    pe = {k:v for k,v in events.items() if k[1]<=cutoff}
    pc = [d for d in calendar if d<=cutoff]
    pt = {d:t for d,t in tables.items() if d<=cutoff}
    pv = {k:v for k,v in values.items() if k[1]<=cutoff[:7]}
    pf = TechnicalFeatures(pp,pe,pc,{d:v for d,v in market.items() if d<=cutoff})
    assert tables[cutoff] == signal_table(pp,pe,pc,cutoff)
    report = dict(certification=old['certification'],price_fingerprint=fingerprint,revenue_fingerprint=revenue_hash,
                  market_file_sha256=hashlib.sha256(market_path.read_bytes()).hexdigest(),
                  market_values_sha256=hashlib.sha256(json.dumps(sorted(market.items())).encode()).hexdigest(),
                  market_source=market_metadata['source'], market_coverage=market_metadata['coverage'],
                  usable_revenue_stock_months=len(values),conflicts=len(conflicts),excluded_dates=excluded,
                  price_high_adjusted_rows=len(high_audit),settings=old['settings'],deviation_cap=.10,
                  limitations=old['limitations'],listed_only=universe is not None,universe_audit=universe_audit,
                  universe_metadata=universe_payload,scenarios={})
    if universe:
        report['previous_unrestricted']=json.loads((OUTPUT/'comparison.json').read_text(encoding='utf-8'))['scenarios']
    out.mkdir(exist_ok=True)
    calendar_positions={d:i for i,d in enumerate(calendar)}
    if universe:
        old_violations=[]
        for old_name in report['previous_unrestricted']:
            with (OUTPUT/old_name/'orders.csv').open(encoding='utf-8-sig') as f:
                old_orders=list(csv.DictReader(f))
            for order in old_orders:
                if order['filled']!='True':continue
                sid,day=order['stock_id'],order['date']
                signal=calendar[calendar_positions[day]-1]
                window_start=calendar[max(0,calendar_positions[signal]+1-252)]
                count=bisect_right(valid[sid],signal)-bisect_left(valid[sid],window_start)
                reason=('unverified_security' if sid not in universe.intervals else
                        'outside_verified_listing_interval' if not universe.eligible(sid,day) or not universe.eligible(sid,signal) else
                        'listed_history_below_200' if count<200 else None)
                if reason:
                    old_violations.append(dict(scenario=old_name,stock_id=sid,signal_day=signal,entry=day,
                                               listed_price_sessions=count,reason=reason))
        save_csv(out/'old_scope_violations.csv',old_violations)
        report['old_scope_violations_by_scenario']={name:dict(Counter(r['reason'] for r in old_violations if r['scenario']==name))
                                                    for name in report['previous_unrestricted']}
    specs=([(None,None)] if universe else [])+[(lag,mode) for lag in (15,30) for mode in MODES]
    for lag,mode in specs:
        name = f'{mode}_{lag}' if lag else 'momentum'
        gate = TechnicalEntryGate(SnapshotGate(values,lag,rule='three_month_revenue'),features,mode) if lag else None
        result = simulate_weekly(prices,events,calendar,tables,entry_filter=gate,**old['settings'])
        pg = TechnicalEntryGate(SnapshotGate(pv,lag,rule='three_month_revenue'),pf,mode) if lag else None
        prefix = simulate_weekly(pp,pe,pc,pt,entry_filter=pg,**old['settings'])
        assert prefix[0] == [r for r in result[0] if r['date']<=cutoff]
        assert prefix[2] == [r for r in result[2] if r['exit']<=cutoff]
        s = summarize(result)
        pnl = [r['proceeds']-r['cost'] for r in result[1]]
        loss = -sum(x for x in pnl if x<0)
        s.update(profit_factor=sum(x for x in pnl if x>0)/loss if loss else None,
                 mean_trade_return=statistics.mean(r['return_net'] for r in result[1]) if result[1] else None,
                 filled_buys=sum(r['filled'] for r in result[3]),prefix_invariance_under_assumptions=True,
                 gate_decisions=dict(Counter(r['reason'] for r in gate.audit)) if gate else {})
        if mode=='base' and not universe:
            assert s['ending_equity']==old['scenarios'][f'new_{lag}']['ending_equity']
            assert s['closed_roundtrips']==old['scenarios'][f'new_{lag}']['closed_roundtrips']
        if universe:
            for order in result[3]:
                if not order['filled']:continue
                sid,day=order['stock_id'],order['date']
                signal=calendar[calendar_positions[day]-1]
                assert universe.eligible(sid,day) and universe.eligible(sid,signal)
                assert sid in tables[signal]
                window_start=calendar[max(0,calendar_positions[signal]+1-252)]
                count=bisect_right(valid[sid],signal)-bisect_left(valid[sid],window_start)
                assert count>=200
            s['held_outside_eligible']=[sid for sid in result[4] if not universe.eligible(sid,calendar[-1])]
            s['7610_roundtrips']=[r for r in result[1] if r['stock_id']=='7610']
        report['scenarios'][name]=s
        folder=out/name
        folder.mkdir(exist_ok=True)
        for title,rows in zip(['nav','roundtrips','sell_legs','orders'],result[:4]):
            save_csv(folder/(title+'.csv'),rows)
        if gate:save_csv(folder/'gate_audit.csv',gate.audit)
        (out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(name,s['ending_equity'],s['stale_scenario'],s['win_rate'],s['closed_roundtrips'],flush=True)
    checks=0
    for (sid,day),row in features.stock_cache.items():
        if day<=cutoff:
            assert pf.stock(sid,day)==row
            checks+=1
    for day,row in features.market_cache.items():
        if day<=cutoff:
            assert pf.market(day)==row
            checks+=1
    report['prefix_feature_checks']=checks
    (out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    render_reports(report,out)
    if universe:mark_legacy_reports()
    print(f'Complete: {len(specs)} scenarios; {checks} feature prefix checks; listed_only={bool(universe)}',flush=True)


if __name__=='__main__':
    main()
