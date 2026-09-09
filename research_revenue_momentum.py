"""現有營收快照的探索回測：明示公告延遲假設，不是PIT認證。"""
import argparse
import csv
import hashlib
import json
import math
import sqlite3
import statistics
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from dynamic_momentum import load_data, signal_table
from revenue_filter import month_shift
from weekly_hermes_momentum import diagnostic_high_envelope, simulate_weekly, summarize, weekly_signal_days


def load_snapshot_values(root):
    sources = {}
    def add(sid, month, value, source):
        if value is not None and '2018-01' <= month <= '2026-08':
            sources.setdefault((sid, month), []).append((int(value), source))
    c = sqlite3.connect(f'file:{(root / "snapshots.db").as_posix()}?mode=ro', uri=True)
    for sid, month, value in c.execute('SELECT stock_id,month,revenue_twd FROM revenue_snapshots WHERE url IN (SELECT url FROM pages WHERE error IS NULL)'):
        add(sid, month, value, 'official_html')
    for sid, month, value in c.execute('SELECT stock_id,month,revenue_twd FROM csv_revenues'):
        add(sid, month, value, 'official_csv')
    c.close()
    c = sqlite3.connect(f'file:{(root / "free_revenue.db").as_posix()}?mode=ro', uri=True)
    for sid, month, value in c.execute('SELECT stock_id,month,revenue_twd FROM observations WHERE query IN (SELECT query FROM responses WHERE error IS NULL)'):
        add(sid, month, value, 'finmind')
    c.close()
    values = {}; conflicts = []
    for (sid, month), observed in sources.items():
        if len({value for value, _ in observed}) > 1:
            conflicts.append(dict(stock_id=sid, month=month, observations=observed))
        else:
            values[sid, month] = observed[0][0]
    return values, conflicts


class SnapshotGate:
    """只供研究：將營收視為月底後lag_days天可知，再隔日可用。

    此假設無法消除修訂偏誤。與RevenueFilter的真實known_on路徑分開。
    """
    def __init__(self, values, lag_days, require_growth=True):
        self.values = values
        self.lag_days = lag_days
        self.require_growth = require_growth
        self.audit = []

    def evaluate(self, sid, day):
        signal = date.fromisoformat(day)
        month = month_shift(day[:7], -1)
        while date.fromisoformat(month_shift(month, 1) + '-01') + timedelta(days=self.lag_days - 1) >= signal:
            month = month_shift(month, -1)
        months = [month_shift(month, k) for k in (0, -1, -12, -13)]
        result = dict(stock_id=sid, signal_day=day, revenue_month=month, coverage=False,
                      passed=False, yoy=None, previous_yoy=None, reason='missing_or_conflicting_month')
        if any((sid, m) not in self.values for m in months):return result
        current, previous, year_ago, previous_year_ago = [self.values[sid, m] for m in months]
        if year_ago <= 0 or previous_year_ago <= 0:
            return {**result, 'reason': 'nonpositive_comparison_base'}
        yoy, prior = current / year_ago - 1, previous / previous_year_ago - 1
        passed = not self.require_growth or (yoy > 0 and yoy > prior)
        return {**result, 'coverage': True, 'passed': passed, 'yoy': yoy, 'previous_yoy': prior,
                'reason': 'pass' if passed else 'not_positive_or_accelerating'}

    def __call__(self, sid, day):
        row = self.evaluate(sid, day)
        self.audit.append(row)
        return row['passed']


def save_csv(path, rows):
    if not rows:return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def render_html(report, out):
    labels = dict(baseline='原動能策略',coverage_15='資料可用控制組／15天',growth_15='營收轉強濾網／15天',
                  coverage_30='資料可用控制組／30天',growth_30='營收轉強濾網／30天')
    rows = ''
    for key,s in report['scenarios'].items():
        m=s['stale_scenario']
        rows += f'<tr><th>{labels[key]}</th><td>{s["ending_equity"]/10000:,.2f}</td><td>{m["total"]:.2%}</td><td>{m["cagr"]:.2%}</td><td>{m["max_drawdown"]:.2%}</td><td>{s["win_rate"]:.2%}</td><td>{s["profit_factor"]:.3f}</td><td>{s["closed_roundtrips"]}</td><td>{s["mean_cash_fraction"]:.2%}</td></tr>'
    series={k:list(csv.DictReader((out/k/'nav.csv').open(encoding='utf-8-sig'))) for k in ('baseline','growth_15','growth_30')}
    top=math.ceil(max(float(r['nav_stale']) for rs in series.values() for r in rs)*1.04/1000000)*1000000
    chart=''
    for value in range(0,top+1,1000000):
        y=250-value/top*220
        chart+=f'<line x1="70" y1="{y}" x2="1140" y2="{y}" stroke="#dce3ec"/><text x="60" y="{y+4}" text-anchor="end" font-size="12">{value/10000:.0f}萬</text>'
    colors=dict(baseline='#245baa',growth_15='#15846c',growth_30='#c5781d')
    for k,rs in series.items():
        points=' '.join(f'{70+i/(len(rs)-1)*1070:.1f},{250-float(r["nav_stale"])/top*220:.1f}' for i,r in enumerate(rs))
        chart+=f'<polyline points="{points}" fill="none" stroke="{colors[k]}" stroke-width="2"/>'
    years=''
    for i in range(len(report['scenarios']['baseline']['yearly'])):
        year=report['scenarios']['baseline']['yearly'][i]['year']
        years+=f'<tr><th>{year}{"（至9/7）" if year=="2026" else ""}</th>'+''.join(f'<td>{report["scenarios"][k]["yearly"][i]["return_net"]:.2%}</td>' for k in ('baseline','growth_15','growth_30'))+'</tr>'
    document=f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>動能3−1＋營收濾網探索比較</title>
    <style>body{{font:16px/1.65 system-ui;margin:0;background:#f3f6fa;color:#213149}}main{{max-width:1200px;margin:auto;padding:30px}}section{{background:white;padding:22px;margin:20px 0;border-radius:12px}}.warning{{background:#fff1d7;padding:18px;border-left:5px solid #ce8720}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}td,th{{padding:10px;text-align:right;border-bottom:1px solid #dce3ec}}th:first-child{{text-align:left}}.scroll{{overflow-x:auto}}svg{{width:100%;height:auto}}a{{color:#245baa}}</style><main>
    <h1>動能3−1＋單月營收轉強</h1><p>2020/01/02–2026/09/07｜初始100萬元｜週三訊號、休市順延｜次交易日開盤成交</p>
    <p class="warning"><b>目前快照＋假設公告延遲的探索回測，未通過PIT認證。</b>15／30天指營收月份結束後等待的日曆天數，假設可知日後才使用。延遲無法消除後來更正值回填的偏誤。價格股利／公司行動／歷史股票池限制仍在。</p>
    <section><h2>判讀</h2><p>兩種延遲設定均呈现勝率略升與最大回撤縮小，但總報酬的改善不穩定。15天濾網落後原策略，30天濾網領先；目前不能認定營收濾網能穩定提升整體決策品質。</p><p>資料可用控制組只要求相同四個月份營收可用、去年同期基數為正；濾網組再要求本月年增率為正且高於前月。不新增營收賣出條件。</p></section>
    <section class="scroll"><h2>五組比較</h2><table><thead><tr><th>版本</th><th>期末淨值／萬元</th><th>累積報酬</th><th>年化</th><th>最大回撤</th><th>勝率</th><th>獲利因子</th><th>完成交易</th><th>平均現金</th></tr></thead><tbody>{rows}</tbody></table><p>獲利因子＝已完成獲利交易總損益÷虧損交易總損失絕對值；不是單筆平均盈虧比。</p></section>
    <section><h2>帳戶淨值（每格100萬元）</h2><p>藍：原動能　綠：15天濾網　橙：30天濾網</p><svg viewBox="0 0 1180 285" role="img" aria-label="三種策略每日淨值比較">{chart}<text x="70" y="277">2020/01</text><text x="1140" y="277" text-anchor="end">2026/09</text></svg></section>
    <section class="scroll"><h2>年度報酬</h2><table><tr><th>年度</th><th>原動能</th><th>15天濾網</th><th>30天濾網</th></tr>{years}</table><p>兩個濾網都錯過部分2020與2026的強勢表現；30天版本在2022年亦沒有較抗跌。全期最大回撤改善不代表每個下跌年度都改善。</p></section>
    <section><h2>驗證與明細</h2><p>原週三基準精確重現。五組在既定假設下通過2022年底前綴一致性；這不能證明營收原始版本沒有修訂偏誤。本次使用{report['usable_revenue_stock_months']:,}筆無來源數值衝突的股票月份資料。</p><p>每個子目錄保留NAV、交易與委託CSV；有濾網者另有每次選股的通過／拒絕原因。<a href="comparison.json">完整結果JSON</a></p></section></main></html>'''
    (out/'比較報告.html').write_text(document,encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-snapshot-research', action='store_true')
    args = parser.parse_args()
    if not args.allow_snapshot_research:parser.error('需明示 --allow-snapshot-research；本程式不能排除營收修訂與公告時間偏誤')
    out = Path('backtest/momentum_rerun_20260909_revenue_research');out.mkdir(exist_ok=True)
    values, conflicts = load_snapshot_values(Path('data/momentum_pit/revenue_archive'))
    (out / 'revenue_conflicts.json').write_text(json.dumps(conflicts, ensure_ascii=False, indent=2), encoding='utf-8')
    price, events, calendar, _, fingerprint, excluded = load_data()
    price, high_audit = diagnostic_high_envelope(price)
    ends = {d[:7]:d for d in calendar}
    signal_days = weekly_signal_days(calendar, 2)
    valid = {s:sorted(d for d,r in rows.items() if r.get('close',0)>0 and r.get('Trading_Volume',0)>0) for s,rows in price.items()}
    tables = {d:signal_table(price,events,calendar,d,valid) for d in calendar if d >= '2019-10-01' and
              (d in signal_days or (d == ends[d[:7]] and d[:7] < calendar[-1][:7]))}
    options = dict(trailing=False, observation_days=None, equity_allocation=True, position_weight=.05,
                   peak_stop=.30, signal_weekday=2, roll_holidays=True)
    scenarios = [('baseline',None)] + [(f'{mode}_{lag}', SnapshotGate(values,lag,mode=='growth')) for lag in (15,30) for mode in ('coverage','growth')]
    report = dict(certification='SNAPSHOT_ASSUMPTION_EXPLORATION_NOT_PIT', price_fingerprint=fingerprint,
                  revenue_fingerprint=hashlib.sha256(json.dumps(sorted((s,m,v) for (s,m),v in values.items())).encode()).hexdigest(),
                  usable_revenue_stock_months=len(values),conflicting_revenue_stock_months=len(conflicts),
                  price_high_adjusted_rows=len(high_audit),excluded_dates=excluded,settings=options,
                  limitations=['營收為目前快照，15/30天延遲是假設，無法排除更正值回填與實際公告時點偏誤',
                               '原價格引擎的股利、公司行動、歷史股票池及高價診斷限制延續'], scenarios={})
    cutoff = '2022-12-30'
    prefix_price = {s:{d:r for d,r in rows.items() if d<=cutoff} for s,rows in price.items()}
    prefix_events = {k:v for k,v in events.items() if k[1]<=cutoff}
    prefix_calendar = [d for d in calendar if d<=cutoff]
    prefix_tables = {d:t for d,t in tables.items() if d<=cutoff}
    assert tables[cutoff] == signal_table(prefix_price,prefix_events,prefix_calendar,cutoff)
    for name, gate in scenarios:
        result = simulate_weekly(price,events,calendar,tables,entry_filter=gate,**options)
        check_gate = None if gate is None else SnapshotGate({k:v for k,v in values.items() if k[1]<=cutoff[:7]},gate.lag_days,gate.require_growth)
        prefix = simulate_weekly(prefix_price,prefix_events,prefix_calendar,prefix_tables,entry_filter=check_gate,**options)
        assert prefix[0] == [r for r in result[0] if r['date']<=cutoff]
        assert prefix[2] == [r for r in result[2] if r['exit']<=cutoff]
        summary = summarize(result)
        pnl = [r['proceeds']-r['cost'] for r in result[1]]
        loss = -sum(x for x in pnl if x<0)
        summary['profit_factor'] = sum(x for x in pnl if x>0)/loss if loss else None
        summary['mean_trade_return'] = statistics.mean(r['return_net'] for r in result[1]) if result[1] else None
        summary['prefix_invariance_under_assumptions'] = True
        folder = out / name;folder.mkdir(exist_ok=True)
        for title, rows in zip(['nav','roundtrips','sell_legs','orders'],result[:4]):save_csv(folder/(title+'.csv'),rows)
        if gate:
            summary['gate_decisions'] = dict(Counter(r['reason'] for r in gate.audit))
            save_csv(folder/'gate_audit.csv',gate.audit)
        report['scenarios'][name] = summary
        (out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(name,summary['ending_equity'],summary['stale_scenario'],summary['closed_roundtrips'],flush=True)
    old = json.loads(Path('backtest/momentum_rerun_20260909_weekdays/comparison.json').read_text(encoding='utf-8'))['weekdays']['2']
    assert report['scenarios']['baseline']['ending_equity'] == old['ending_equity']
    render_html(report,out)
    print('Wednesday baseline reproduced exactly',flush=True)


if __name__ == '__main__':main()
