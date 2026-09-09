"""以既有交易明細拆解進場濾網收益；不更改或重跑策略。"""
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


ROOT = Path('backtest/momentum_rerun_20260909_entry_filters')


def read_trades(name):
    with (ROOT/name/'roundtrips.csv').open(encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for key in ('cost','proceeds','return_net','days'):
            r[key] = float(r[key])
        r['pnl'] = r['proceeds']-r['cost']
        assert math.isclose(r['pnl']/r['cost'],r['return_net'],abs_tol=1e-12)
    return rows


def trade_stats(rows):
    wins = [r for r in rows if r['return_net']>0]
    losses = [r for r in rows if r['return_net']<0]
    mean_win = statistics.mean(r['return_net'] for r in wins)
    mean_loss = statistics.mean(r['return_net'] for r in losses)
    expected = (len(wins)*mean_win + len(losses)*mean_loss)/len(rows)
    assert math.isclose(expected,statistics.mean(r['return_net'] for r in rows),abs_tol=1e-12)
    gross_profit = sum(r['pnl'] for r in wins)
    gross_loss = -sum(r['pnl'] for r in losses)
    sorted_wins = sorted(wins,key=lambda r:r['pnl'],reverse=True)
    return dict(count=len(rows),wins=len(wins),losses=len(losses),zero=len(rows)-len(wins)-len(losses),
                win_rate=len(wins)/len(rows),mean_win_return=mean_win,mean_loss_return=mean_loss,
                payoff_ratio=mean_win/-mean_loss,expected_trade_return=expected,
                median_trade_return=statistics.median(r['return_net'] for r in rows),
                mean_win_days=statistics.mean(r['days'] for r in wins),
                mean_loss_days=statistics.mean(r['days'] for r in losses),
                gross_profit=gross_profit,gross_loss=gross_loss,realized_pnl=gross_profit-gross_loss,
                profit_factor=gross_profit/gross_loss,
                top5_profit_fraction=sum(r['pnl'] for r in sorted_wins[:5])/gross_profit,
                top10_profit_fraction=sum(r['pnl'] for r in sorted_wins[:10])/gross_profit,
                winners_above_50pct=sum(r['return_net']>.5 for r in rows),
                winners_above_100pct=sum(r['return_net']>1 for r in rows),
                top10_winning_trades=sorted_wins[:10])


def account_bridge(name, rows, summary):
    with (ROOT/name/'orders.csv').open(encoding='utf-8-sig') as f:
        buys = [r for r in csv.DictReader(f) if r['filled']=='True']
    closed_keys = {(r['stock_id'],r['entry']) for r in rows}
    buy_keys = {(r['stock_id'],r['date']) for r in buys}
    assert len(closed_keys)==len(rows) and len(buy_keys)==len(buys)
    assert closed_keys <= buy_keys
    open_buys = [r for r in buys if (r['stock_id'],r['date']) not in closed_keys]
    assert len(open_buys)==summary['open_positions']
    open_cost = sum(float(r['budget']) for r in open_buys)
    market_value = summary['ending_equity']-summary['ending_cash']
    realized = sum(r['pnl'] for r in rows)
    unrealized = market_value-open_cost
    assert math.isclose(summary['ending_equity']-1e6,realized+unrealized,abs_tol=1e-6)
    by_year = defaultdict(float)
    for r in rows:
        by_year[r['exit'][:4]] += r['pnl']
    return dict(realized_pnl=realized,open_cost=open_cost,open_market_value=market_value,
                unrealized_pnl=unrealized,ending_cash=summary['ending_cash'],
                ending_equity=summary['ending_equity'],realized_by_exit_year=dict(sorted(by_year.items())))


def compare_trades(base, new):
    def key(r):return r['stock_id'],r['entry'],r['exit']
    a,b = {key(r):r for r in base},{key(r):r for r in new}
    common = a.keys() & b.keys()
    base_only = a.keys()-b.keys()
    new_only = b.keys()-a.keys()
    assert all(math.isclose(a[k]['return_net'],b[k]['return_net'],abs_tol=1e-12) for k in common)
    common_capital = sum((b[k]['cost']-a[k]['cost'])*a[k]['return_net'] for k in common)
    base_only_pnl = sum(a[k]['pnl'] for k in base_only)
    new_only_pnl = sum(b[k]['pnl'] for k in new_only)
    delta = sum(r['pnl'] for r in new)-sum(r['pnl'] for r in base)
    assert math.isclose(delta,common_capital+new_only_pnl-base_only_pnl,abs_tol=1e-6)
    stock_delta = defaultdict(float)
    for r in new:stock_delta[r['stock_id']]+=r['pnl']
    for r in base:stock_delta[r['stock_id']]-=r['pnl']
    return dict(common_roundtrips=len(common),base_only_roundtrips=len(base_only),
                new_only_roundtrips=len(new_only),common_capital_pnl_delta=common_capital,
                base_only_pnl=base_only_pnl,new_only_pnl=new_only_pnl,realized_pnl_delta=delta,
                base_only_stats=trade_stats([a[k] for k in base_only]),
                new_only_stats=trade_stats([b[k] for k in new_only]),
                stock_realized_pnl_delta=sorted(stock_delta.items(),key=lambda kv:kv[1],reverse=True))


def capacity_case(name, sid='7610', signal='2026-02-04', fill='2026-02-05'):
    def read(filename):
        with (ROOT/name/filename).open(encoding='utf-8-sig') as f:
            return list(csv.DictReader(f))
    nav = next(r for r in read('nav.csv') if r['date']==signal)
    orders = [r for r in read('orders.csv') if r['date']==fill]
    i = next(i for i,r in enumerate(orders) if r['stock_id']==sid)
    sales = sum(float(r['proceeds']) for r in read('sell_legs.csv') if r['exit']==fill)
    spent = sum(float(r['budget']) for r in orders[:i] if r['filled']=='True')
    available = float(nav['cash'])+sales-spent
    budget = float(orders[i]['budget'])
    assert (available+1e-8>=budget)==(orders[i]['filled']=='True')
    gate = next(r for r in read('gate_audit.csv') if r['signal_day']==signal and r['stock_id']==sid)
    assert gate['passed']=='True'
    return dict(stock_id=sid,signal=signal,fill=fill,signal_cash=float(nav['cash']),
                sale_proceeds_before_buys=sales,queue_position=i+1,earlier_filled_spend=spent,
                cash_before_target=available,required_budget=budget,filled=orders[i]['filled']=='True',
                revenue_and_entry_filter_passed=True)


def main():
    source = json.loads((ROOT/'comparison.json').read_text(encoding='utf-8'))
    rows = {name:read_trades(name) for name in source['scenarios']}
    report = {'scenarios':{},'comparisons':{},'methodology':[
        '已完成交易按買入成本計算含原引擎費用的單筆報酬；勝負平均為等權百分比，不等同帳戶複利報酬。',
        '期末淨利=已實現損益+未實現損益；未實現損益按原NAV標價，未扣未發生的賣出費用。',
        '共同交易以股票/買入日/賣出日配對；報酬率相同時，損益差額由投入金額差造成。',
        '非共同交易包括進場時間改變、現金順位及持倉路徑影響，不能全稱為濾掉的壞股票。',
        '每筆固定金額的期望值只是去除金額權重的描述，並非另一個可交易組合回測。',
    ]}
    for name,trades in rows.items():
        stats = trade_stats(trades)
        summary = source['scenarios'][name]
        assert math.isclose(stats['win_rate'],summary['win_rate'],abs_tol=1e-12)
        assert math.isclose(stats['profit_factor'],summary['profit_factor'],abs_tol=1e-12)
        report['scenarios'][name] = dict(stats=stats,account=account_bridge(name,trades,summary))
    for lag in (15,30):
        base,new=f'base_{lag}',f'all_{lag}'
        comp=compare_trades(rows[base],rows[new])
        a,b=report['scenarios'][base]['account'],report['scenarios'][new]['account']
        comp.update(unrealized_pnl_delta=b['unrealized_pnl']-a['unrealized_pnl'],
                    ending_equity_delta=b['ending_equity']-a['ending_equity'])
        assert math.isclose(comp['ending_equity_delta'],comp['realized_pnl_delta']+comp['unrealized_pnl_delta'],abs_tol=1e-6)
        report['comparisons'][str(lag)] = comp
    (ROOT/'return_attribution.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for name in ('base_15','all_15','base_30','all_30'):
        r=report['scenarios'][name]
        print(name,json.dumps({k:v for k,v in r['stats'].items() if k!='top10_winning_trades'},ensure_ascii=False))
        print('account',r['account'])
    for lag,c in report['comparisons'].items():
        print('compare',lag,{k:v for k,v in c.items() if k not in ('base_only_stats','new_only_stats','stock_realized_pnl_delta')})
        print('top_stock_deltas',c['stock_realized_pnl_delta'][:8],c['stock_realized_pnl_delta'][-5:])
    report['capacity_case_7610'] = {name:capacity_case(name) for name in ('base_15','all_15','base_30','all_30')}
    (ROOT/'return_attribution.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('CAPACITY',report['capacity_case_7610'])


if __name__=='__main__':
    main()
