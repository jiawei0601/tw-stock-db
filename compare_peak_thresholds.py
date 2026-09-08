"""比較已執行的15/20/25/30%高點停損診斷；核對輸入、交易與現金帳。"""
import csv
import json
from pathlib import Path
from dynamic_momentum import metrics


def compare(thresholds=(.15,.20,.25,.30)):
    results=[];reference=None
    for threshold in thresholds:
        root=Path(f'backtest/momentum_weekly_hermes_equity_momentum_exit_weight0.05_peak{threshold:g}_diagnostic')
        summary=json.loads((root/'result.json').read_text(encoding='utf-8'))
        options={k:v for k,v in summary['exit_options'].items() if k!='peak_stop'}
        identity=(summary['fingerprint'],options,summary['high_price_policy'],summary['high_price_adjusted_rows'],summary['excluded_dates'])
        if reference is None:reference=identity
        assert identity==reference and summary['prefix_invariance']
        assert summary['exit_options']['peak_stop']==threshold
        def read(name):
            with (root/(name+'.csv')).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
        nav=[{**r,'nav_stale':float(r['nav_stale'])} for r in read('nav')]
        legs=read('sell_legs');orders=read('orders')
        holdings=json.loads((root/'holdings.json').read_text(encoding='utf-8'))
        for leg in legs:
            assert leg['exit']>leg['signal_date']
            if leg['reason']=='trailing_stop':
                assert float(leg['signal_close'])<float(leg['signal_stop_level'])
                assert abs(float(leg['signal_stop_level'])-float(leg['signal_peak'])*(1-threshold))<1e-7
        cash=1e6-sum(float(r['budget']) for r in orders if r['filled']=='True')+sum(float(r['proceeds']) for r in legs)
        value=sum(r['original_shares']*r['remaining']*r['mark'] for r in holdings.values())
        assert abs(cash-float(nav[-1]['cash']))<1e-5
        assert abs(cash+value-nav[-1]['nav_stale'])<1e-5
        stats=summary['weekly_momentum_exit'];m=stats['stale_scenario']
        early=[r for r in nav if r['date']<'2024-01-01']
        # Carry the actual portfolio across the boundary; these are descriptive slices, not unseen tests.
        late=[early[-1]]+[r for r in nav if r['date']>early[-1]['date']]
        slices={}
        for name,rows,initial in [('2020-2023',early,1e6),('2024-2026',late,early[-1]['nav_stale'])]:
            part=metrics(rows,'nav_stale',initial)
            part['calmar']=part['cagr']/abs(part['max_drawdown']) if part['max_drawdown'] else None
            slices[name]=part
        results.append(dict(threshold=threshold,ending_equity=stats['ending_equity'],**m,
                            calmar=m['cagr']/abs(m['max_drawdown']),trades=stats['closed_roundtrips'],
                            win_rate=stats['win_rate'],holding_days=stats['mean_holding_days'],
                            yearly=stats['yearly'],slices=slices,exit_reasons=stats['exit_legs_by_reason']))
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return results


if __name__=='__main__':compare()
