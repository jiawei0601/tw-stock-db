"""比較三個月營收金額遞增與單月YoY加速；僅供快照假設研究。"""
import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from research_revenue_momentum import (
    SnapshotGate, load_snapshot_values, load_data, signal_table, diagnostic_high_envelope,
    simulate_weekly, summarize, weekly_signal_days, save_csv,
)


LABELS = {'baseline': '原動能（無營收濾網）'}
for _lag in (15, 30):
    for _name, _label in [('old', '舊：YoY正且加速'), ('new', '新：三月營收遞增'),
                          ('old_common', '共同資料／舊規則'), ('new_common', '共同資料／新規則')]:
        LABELS[f'{_name}_{_lag}'] = f'{_label}／{_lag}天'


def render_html(report, out):
    scenarios = report['scenarios']
    rows = ''
    for key, s in scenarios.items():
        m = s['stale_scenario']
        rows += f'<tr><th>{LABELS[key]}</th><td>{s["ending_equity"]/10000:,.2f}</td><td>{m["total"]:.2%}</td><td>{m["cagr"]:.2%}</td><td>{m["max_drawdown"]:.2%}</td><td>{s["win_rate"]:.2%}</td><td>{s["profit_factor"]:.3f}</td><td>{s["closed_roundtrips"]}</td><td>{s["mean_cash_fraction"]:.2%}</td></tr>'
    keys = ('baseline', 'old_15', 'new_15', 'old_30', 'new_30')
    colors = ('#65748b', '#ab741d', '#087f6a')
    charts = ''
    for lag in (15, 30):
        chart = ''
        series = {}
        for k in ('baseline', f'old_{lag}', f'new_{lag}'):
            with (out/k/'nav.csv').open(encoding='utf-8-sig') as f:
                series[k] = list(csv.DictReader(f))
        top = math.ceil(max(float(r['nav_stale']) for rs in series.values() for r in rs)*1.04/1e6)*1000000
        for value in range(0, top+1, 1000000):
            y = 250-value/top*220
            chart += f'<line x1="70" y1="{y}" x2="1140" y2="{y}" stroke="#dce3ec"/><text x="60" y="{y+4}" text-anchor="end" font-size="12">{value/10000:.0f}萬</text>'
        for (k, rs), color in zip(series.items(), colors):
            points = ' '.join(f'{70+i/(len(rs)-1)*1070:.1f},{250-float(r["nav_stale"])/top*220:.1f}' for i, r in enumerate(rs))
            chart += f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
        charts += f'<h3>延遲{lag}天</h3><svg viewBox="0 0 1180 285" role="img" aria-label="延遲{lag}天的新舊策略淨值"><title>灰色原動能、棕色舊濾網、綠色新濾網</title>{chart}<text x="70" y="277">2020/01</text><text x="1140" y="277" text-anchor="end">2026/09</text></svg>'
    years = ''
    for i, row in enumerate(scenarios['baseline']['yearly']):
        years += f'<tr><th>{row["year"]}</th>'+''.join(f'<td>{scenarios[k]["yearly"][i]["return_net"]:.2%}</td>' for k in keys)+'</tr>'
    deltas = ''
    for lag in (15, 30):
        for prefix, name in [('', '各自所需資料'), ('_common', '共同五月份資料')]:
            old, new = scenarios[f'old{prefix}_{lag}'], scenarios[f'new{prefix}_{lag}']
            a, b = old['stale_scenario'], new['stale_scenario']
            deltas += f'<li>{lag}天，{name}：新規則期末差額{(new["ending_equity"]-old["ending_equity"])/10000:+.2f}萬元；累積報酬差{(b["total"]-a["total"])*100:+.2f}百分點；最大回撤差{(b["max_drawdown"]-a["max_drawdown"])*100:+.2f}百分點（正值表示回撤縮小）；勝率差{(new["win_rate"]-old["win_rate"])*100:+.2f}百分點。</li>'
    document = f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三個月營收遞增濾網比較</title>
<style>body{{font:16px/1.65 system-ui;margin:0;background:#f3f6fa;color:#213149}}main{{max-width:1250px;margin:auto;padding:28px}}section{{background:white;padding:22px;margin:20px 0;border-radius:12px}}.warning{{background:#fff1d7;padding:18px;border-left:5px solid #ce8720}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}td,th{{padding:10px;text-align:right;border-bottom:1px solid #dce3ec}}th:first-child{{text-align:left}}.scroll{{overflow-x:auto}}svg{{width:100%;height:auto}}</style><main>
<h1>動能3−1＋三個月營收金額遞增</h1><p>2020/01/02–2026/09/07｜初始100萬元｜每筆5%淨值｜週三訊號、休市順延｜次交易日開盤成交｜30%高點回落與月底動能出場</p>
<p class="warning"><b>現有快照與假設時點的探索回測，尚非PIT認證。</b>延遲15／30個日曆天從營收月份月底起算，假設可知日之後才允許使用。延遲無法排除營收修訂、實際公告時間、歷史股票池與價格公司行動偏誤。2026年為截至9/7的部分年度。</p>
<section><h2>定義與對照</h2><p>新規則：R(m) &gt; R(m−1) &gt; R(m−2)，且 R(m)/R(m−12)−1 &gt; 0。三個月金額、兩次逐月增加；相等不通過。舊規則：最新月YoY &gt; 0且 &gt; 前月YoY。只篩新倉，營收轉弱不新增出場。</p><p>各自資料組只要求其公式所需月份。共同資料組則兩者都要求 m、m−1、m−2、m−12、m−13，且兩個去年同期基數都為正，使資料資格相同。無資料不補零、不沿用更舊好月份。每月營收金額遞增可能受季節性影響；最新月YoY為正並不要求三個月YoY都為正。</p><ul>{deltas}</ul><p>須搭配下方年度報酬與勝率判讀：全期收益及回撤改善，不代表逐年改善；樣本曾反覆用於調整策略，這不是獨立樣本外驗證。平均現金增加也會改變風險曝險，不能把全部差異歸因於選股預測能力。</p></section>
<section class="scroll"><h2>完整比較</h2><table><tr><th>版本</th><th>期末／萬元</th><th>累積報酬</th><th>年化</th><th>最大回撤</th><th>勝率</th><th>獲利因子</th><th>完成交易</th><th>平均現金</th></tr>{rows}</table><p>獲利因子為已完成交易的總獲利除以總虧損絕對值；所有費用與價格處理沿用原引擎。</p></section>
<section><h2>每日淨值（每格100萬元）</h2><p>灰：原動能　棕：舊規則　綠：三個月遞增</p>{charts}</section>
<section class="scroll"><h2>年度報酬</h2><table><tr><th>年</th>{''.join(f'<th>{LABELS[k]}</th>' for k in keys)}</tr>{years}</table></section>
<section><h2>稽核與明細</h2><p>營收數值指紋與上一輪一致；{report['usable_revenue_stock_months']:,}筆股票月份。原動能與兩組舊規則期末精確重現；九組均完成2022年底截斷資料前綴檢查。前綴一致只驗證程式於既定假設下不使用後續資料，無法認證原始快照沒有修訂偏誤。</p><p><a href="comparison.json">完整JSON</a>；各子目錄保留NAV、交易、委託與濾網稽核CSV。</p></section></main></html>'''
    (out/'比較報告.html').write_text(document, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-snapshot-research', action='store_true')
    args = parser.parse_args()
    if not args.allow_snapshot_research:
        parser.error('需明示 --allow-snapshot-research；不能排除營收修訂與公告時間偏誤')
    out = Path('backtest/momentum_rerun_20260909_revenue_three_month')
    out.mkdir(exist_ok=True)
    old_report = json.loads(Path('backtest/momentum_rerun_20260909_revenue_research/comparison.json').read_text(encoding='utf-8'))
    values, conflicts = load_snapshot_values(Path('data/momentum_pit/revenue_archive'))
    revenue_hash = hashlib.sha256(json.dumps(sorted((s,m,v) for (s,m),v in values.items())).encode()).hexdigest()
    assert revenue_hash == old_report['revenue_fingerprint'], '資料已變，不應聲稱使用同一份快照'
    price, events, calendar, _, fingerprint, excluded = load_data()
    assert fingerprint == old_report['price_fingerprint'], '價格來源已變'
    price, high_audit = diagnostic_high_envelope(price)
    ends = {d[:7]:d for d in calendar}
    signal_days = weekly_signal_days(calendar, 2)
    valid = {s:sorted(d for d,r in rows.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for s,rows in price.items()}
    tables = {d:signal_table(price,events,calendar,d,valid) for d in calendar if d >= '2019-10-01' and
              (d in signal_days or (d == ends[d[:7]] and d[:7] < calendar[-1][:7]))}
    options = old_report['settings']
    specs = [('baseline', None)]
    for lag in (15, 30):
        for common in (False, True):
            for name, rule in [('old', 'yoy_acceleration'), ('new', 'three_month_revenue')]:
                specs.append((f'{name}{"_common" if common else ""}_{lag}', dict(lag_days=lag,rule=rule,common_coverage=common)))
    report = dict(certification=old_report['certification'], price_fingerprint=fingerprint,
                  revenue_fingerprint=revenue_hash, usable_revenue_stock_months=len(values),
                  conflicting_revenue_stock_months=len(conflicts), price_high_adjusted_rows=len(high_audit),
                  excluded_dates=excluded, settings=options, limitations=old_report['limitations'],
                  rule='R(m)>R(m-1)>R(m-2) and YoY(m)>0; three observations, two increases', scenarios={})
    cutoff = '2022-12-30'
    prefix_price = {s:{d:r for d,r in rows.items() if d<=cutoff} for s,rows in price.items()}
    prefix_events = {k:v for k,v in events.items() if k[1]<=cutoff}
    prefix_calendar = [d for d in calendar if d<=cutoff]
    prefix_tables = {d:t for d,t in tables.items() if d<=cutoff}
    prefix_values = {k:v for k,v in values.items() if k[1]<=cutoff[:7]}
    assert tables[cutoff] == signal_table(prefix_price,prefix_events,prefix_calendar,cutoff)
    for name, spec in specs:
        gate = SnapshotGate(values, **spec) if spec else None
        result = simulate_weekly(price,events,calendar,tables,entry_filter=gate,**options)
        prefix_gate = SnapshotGate(prefix_values, **spec) if spec else None
        prefix = simulate_weekly(prefix_price,prefix_events,prefix_calendar,prefix_tables,entry_filter=prefix_gate,**options)
        assert prefix[0] == [r for r in result[0] if r['date']<=cutoff]
        assert prefix[2] == [r for r in result[2] if r['exit']<=cutoff]
        summary = summarize(result)
        pnl = [r['proceeds']-r['cost'] for r in result[1]]
        loss = -sum(x for x in pnl if x<0)
        summary.update(profit_factor=sum(x for x in pnl if x>0)/loss if loss else None,
                       mean_trade_return=statistics.mean(r['return_net'] for r in result[1]) if result[1] else None,
                       prefix_invariance_under_assumptions=True, gate_spec=spec)
        if name in ('baseline', 'old_15', 'old_30'):
            old_name = name.replace('old_', 'growth_')
            assert summary['ending_equity'] == old_report['scenarios'][old_name]['ending_equity']
        folder = out/name
        folder.mkdir(exist_ok=True)
        for title, rows in zip(['nav','roundtrips','sell_legs','orders'], result[:4]):
            save_csv(folder/(title+'.csv'), rows)
        if gate:
            summary['gate_decisions'] = dict(Counter(r['reason'] for r in gate.audit))
            save_csv(folder/'gate_audit.csv', gate.audit)
        report['scenarios'][name] = summary
        (out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(name, summary['ending_equity'], summary['stale_scenario'], summary['closed_roundtrips'], flush=True)
    render_html(report, out)
    print('Complete: same data, baseline and old rules reproduced, all nine prefix checks passed', flush=True)


if __name__ == '__main__':
    main()
