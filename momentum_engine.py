"""時點隔離的動能研究引擎；標準庫、固定批次、股數及現金流記帳。"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date
import math
import random
import statistics


def shift_month(day, months):
    y, m = map(int, day[:7].split('-'))
    n = y * 12 + m - 1 + months
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


def validate(data):
    for key in ('universe', 'events', 'execution', 'coverage'):
        source=data.get('evidence', {}).get(key)
        if data.get('verified', {}).get(key) is not True or not isinstance(source,str) or not source.strip():
            raise ValueError(f'缺少已核對證據：{key}；拒絕發布部分資料績效')
    if not data.get('securities') or not data.get('prices'):
        raise ValueError('歷史股票池或行情不可為空')
    days = data.get('calendar', [])
    if not days or days != sorted(set(days)):
        raise ValueError('獨立市場日曆必須唯一且排序')
    for d in days:
        date.fromisoformat(d)
    for security in data.get('securities', []):
        if not all(security.get(k) for k in ('stock_id', 'start', 'known_at', 'source')):
            raise ValueError('歷史股票身分缺少日期或來源')
        for k in ('start','known_at'):
            date.fromisoformat(security[k])
        if security.get('end'):
            date.fromisoformat(security['end'])
            if security['end']<=security['start']:
                raise ValueError('歷史股票資格期間無效')
    seen_events = set()
    for event in data.get('events', []):
        if not all(event.get(k) for k in ('stock_id', 'date', 'known_at', 'source')):
            raise ValueError('事件缺少日期或來源')
        date.fromisoformat(event['date'])
        date.fromisoformat(event['known_at'])
        if event.get('pay_date'):
            date.fromisoformat(event['pay_date'])
        if event['kind'] not in ('split', 'distribution', 'delist'):
            raise ValueError('不支援的事件類型')
        if event['kind']=='split' and 'ratio' not in event:
            raise ValueError('分割事件缺少已確認的股份比')
        key = (event['stock_id'], event['date'])
        if key in seen_events or event['date'] not in days:
            raise ValueError('同日事件須先核對合併，事件生效日須在交易日曆')
        seen_events.add(key)
        ratio = event.get('ratio', 1)
        cash = event.get('cash', 0)
        if not math.isfinite(ratio) or ratio <= 0 or not math.isfinite(cash) or cash < 0:
            raise ValueError('事件股份比或現金無效')
        if event['kind'] == 'delist' and 'cash' not in event:
            raise ValueError('下市結算不可猜測')
        if cash and (not event.get('pay_date') or event['pay_date'] < event['date']):
            raise ValueError('現金事件必須有有效付款日')
        if event['kind']=='distribution' and ratio!=1:
            available=event.get('shares_available_date')
            if not available or available<event['date']:
                raise ValueError('股份分配／減資必須提供可交易交付日')
            date.fromisoformat(available)
            if ratio<1 and available!=event['date']:
                raise ValueError('減資事件需對齊恢復交易日，不支援猜測停牌期估值')
    for rows in data.get('prices', {}).values():
        for d, row in rows.items():
            for k in ('open', 'close', 'volume'):
                if k not in row or not math.isfinite(row[k]) or row[k] < 0:
                    raise ValueError('OHLCV 欄位缺漏或無效')
            if not isinstance(row.get('buyable'), bool) or not isinstance(row.get('sellable'), bool):
                raise ValueError('缺少明確的成交可行性')


def select_stocks(data, as_of, fraction=0.25):
    """只讀 as_of 以前的行情／當時已知事件；不讀遠期報酬或成交結果。"""
    days = [d for d in data['calendar'] if d <= as_of]
    month_ends = {}
    for d in days:
        month_ends[d[:7]] = d
    lower = month_ends.get(shift_month(as_of, -12))
    upper = month_ends.get(shift_month(as_of, -1))
    if lower is None or upper is None:
        return [], []
    eligible = {s['stock_id'] for s in data['securities']
                if s['known_at'] <= as_of and s['start'] <= lower
                and (not s.get('end') or as_of < s['end'])}
    by_event = defaultdict(lambda: defaultdict(list))
    for event in data.get('events', []):
        if lower < event['date'] <= upper and event['known_at'] <= as_of:
            by_event[event['stock_id']][event['date']].append(event)
    records = []
    for sid in sorted(eligible):
        prices = data['prices'].get(sid, {})
        dates = [d for d in days if lower <= d <= upper]
        if any(d not in prices or prices[d]['close'] <= 0 for d in (lower, upper, as_of)):
            continue
        if sum(d in prices and prices[d]['close'] > 0 and prices[d]['volume'] > 0
               for d in days if lower <= d <= as_of) < 200:
            continue
        wealth, previous, valid = 1.0, prices[lower]['close'], True
        for d in dates[1:]:
            evs = by_event[sid].get(d, [])
            if any(e['kind'] == 'delist' for e in evs):
                valid = False
                break
            row = prices.get(d)
            if not row or row['close'] <= 0:
                if evs:
                    valid = False
                    break
                continue
            ratio, cash = 1.0, 0.0
            for e in evs:
                cash += ratio * e.get('cash', 0.0)
                ratio *= e.get('ratio', 1.0)
            wealth *= (ratio * row['close'] + cash) / previous
            previous = row['close']
        if valid:
            records.append({'stock_id': sid, 'momentum': wealth - 1})
    records.sort(key=lambda r: (-r['momentum'], r['stock_id']))
    for i, r in enumerate(records):
        r['rank'] = i + 1
    n = math.ceil(len(records) * fraction)
    return records[:n], records


class HoldingPath(dict):
    """每日估值及到期仍應收的現金；後者交由資金梯隊按付款日入帳。"""
    def __init__(self):
        super().__init__()
        self.pending = []


def holding_path(data, sid, entry, target, end, buy_cost, sell_cost):
    """每一單位初始資金的每日 NAV；應收股利列資產，未解決出場不冒充現金。"""
    limit = min(target or end, end)
    days = data['calendar'][bisect_left(data['calendar'], entry):bisect_right(data['calendar'], limit)]
    prices = data['prices'].get(sid, {})
    row = prices.get(entry, {})
    periods = data.get('_securities_by_stock', {}).get(sid)
    if periods is None:
        periods = [s for s in data['securities'] if s['stock_id'] == sid]
    identity_at_entry = any(s['start'] <= entry and (not s.get('end') or entry < s['end']) for s in periods)
    filled = bool(identity_at_entry and row.get('buyable') and row.get('volume', 0) > 0 and row.get('open', 0) > 0)
    if not filled:
        return {d: (1., 1., 1.) for d in days}, 'unfilled_cash', [], True
    shares = 1 / row['open']
    cash, receivable, price_cash, pending_shares = 0.0, [], 0.0, []
    factor = 1 / (1 + buy_cost)
    nav, trades = HoldingPath(), [{'stock_id': sid, 'date': entry, 'side': 'buy',
                        'price': row['open'], 'shares_per_unit': shares * factor,
                        'cost_per_unit': buy_cost / (1 + buy_cost)}]
    events = defaultdict(list)
    stock_events = data.get('_events_by_stock', {}).get(sid)
    if stock_events is None:
        stock_events = [e for e in data.get('events', []) if e['stock_id']==sid]
    for ev in stock_events:
        if ev['stock_id'] == sid and entry < ev['date'] <= end:
            events[ev['date']].append(ev)
    last = row['open']
    exited = False
    unresolved = False
    terminal_fee = 0.0
    state = 'right_censored'
    for d in days:
        delivered=sum(qty for available,qty in pending_shares if available<=d)
        shares+=delivered
        pending_shares=[(available,qty) for available,qty in pending_shares if available>d]
        if delivered:
            trades.append({'stock_id':sid,'date':d,'side':'stock_delivery','shares_per_unit':delivered*factor})
        for ev in events.get(d, []):
            if shares == 0:
                continue
            old = shares
            if pending_shares:
                unresolved=True  # 多個未交付權利重疊須另核對權利基數。
            amount = old * ev.get('cash', 0.0)
            if ev['kind'] == 'delist':
                shares = 0.0
                price_cash += amount
                state, exited = 'settled_delist', True
            else:
                ratio=ev.get('ratio',1.)
                available=ev.get('shares_available_date',d)
                if ratio>1 and available>d:
                    pending_shares.append((available,old*(ratio-1)))
                else:
                    shares *= ratio
            if amount:
                receivable.append((ev['pay_date'], amount))
            trades.append({'stock_id': sid, 'date': d, 'side': ev['kind'],
                           'shares_per_unit': shares * factor, 'cash_entitlement': amount * factor,
                           'ratio':ev.get('ratio',1),'shares_available_date':ev.get('shares_available_date'),
                           'pay_date': ev.get('pay_date'), 'source': ev['source']})
        paid = sum(v for pay, v in receivable if pay <= d)
        cash += paid
        receivable = [(pay, v) for pay, v in receivable if pay > d]
        current = prices.get(d, {})
        active_identity = any(s['start'] <= d and (not s.get('end') or d < s['end']) for s in periods)
        if shares and not active_identity:
            unresolved = True
        # 固定到期日開盤出場；不能拿到期日收盤決定是否在開盤賣。
        if target and d == target and shares:
            if active_identity and current.get('sellable') and current.get('volume', 0) > 0 and current.get('open', 0) > 0:
                proceeds = shares * current['open']
                terminal_fee = proceeds * sell_cost
                cash += proceeds
                price_cash += proceeds
                trades.append({'stock_id': sid, 'date': d, 'side': 'sell',
                               'price': current['open'], 'shares_per_unit': shares * factor,
                               'cost_per_unit': terminal_fee * factor})
                shares = 0.
                state, exited = 'realized', True
            else:
                state, unresolved = 'unresolved_exit', True
        if target and d==target and pending_shares:
            state,unresolved='unresolved_exit',True
        if current.get('close', 0) > 0:
            last = current['close']
        elif shares:
            unresolved = True  # 沒有完整每日估值，保留限制而不刪股票。
        stock_value = (shares+sum(qty for _,qty in pending_shares)) * last
        total = stock_value + cash + sum(v for _, v in receivable)
        nav[d] = ((None, None, None) if unresolved else
                  (stock_value + price_cash, total, (total - terminal_fee) * factor))
        if target and d >= target:
            break
    if target and target > end:
        state = 'right_censored'
    if receivable and exited:
        state = 'pending_cash'
    nav.pending = [(pay,amount*factor) for pay,amount in receivable]
    reusable = exited and not unresolved
    if unresolved and state != 'unresolved_exit':
        state = 'unresolved_valuation'
    return nav, state, trades, reusable


def block_ci(values, block, samples=1000):
    if len(values) < 2 * block:
        return None
    rng, means = random.Random(42), []
    for _ in range(samples):
        sample = []
        while len(sample) < len(values):
            start = rng.randrange(len(values))
            sample.extend(values[(start + k) % len(values)] for k in range(block))
        means.append(statistics.mean(sample[:len(values)]))
    means.sort()
    return [means[int(samples * .025)], means[int(samples * .975)]]


def run_backtest(data, start='2020-01-01', end=None, fraction=.25, buy_cost=.003, sell_cost=.003):
    validate(data)
    # 內部索引不信任輸入提供的快取，亦不修改呼叫端資料。
    data = dict(data)
    for name, records in (('_events_by_stock',data.get('events',[])),
                          ('_securities_by_stock',data['securities'])):
        grouped=defaultdict(list)
        for record in records:
            grouped[record['stock_id']].append(record)
        data[name]=grouped
    if not 0 < fraction <= 1 or not 0 <= buy_cost < 1 or not 0 <= sell_cost < 1:
        raise ValueError('排名比例或交易成本超出範圍')
    end = end or data['calendar'][-1]
    if end > data['calendar'][-1] or end < start:
        raise ValueError('回測結束日超出日曆或早於起日')
    days = [d for d in data['calendar'] if d <= end]
    trading = [d for d in days if d >= start]
    if not trading:
        raise ValueError('沒有回測交易日')
    first_by_month = {}
    for d in days:
        first_by_month.setdefault(d[:7], d)
    entries = [(days[i - 1], d) for i, d in enumerate(days)
               if i and d >= start and days[i - 1][:7] != d[:7]]
    result = {'certification': 'SYNTHETIC_DEMO' if data.get('synthetic') else 'EVIDENCE_ATTESTED',
              'signals': [], 'trades': [], 'cohorts': [], 'equity': [], 'summary': {}, 'issues': [],
              'assumptions': {'buy_cost': buy_cost, 'sell_cost': sell_cost, 'fraction': fraction,
                              'execution': 'next session open, externally verified feasibility',
                              'dividends': 'receivable on ex-date, cash on pay-date; post-exit cash waits until slot rollover',
                              'position_size': 'fractional shares, no market impact or lot-size constraints',
                              'certification': 'input evidence attestation, not independent proof'}}
    paths = {}
    reusable_map = {}
    pending_map = {}
    for sig, entry in entries:
        picked, pool = select_stocks(data, sig, fraction)
        for strategy, members in (('momentum', picked), ('universe', pool)):
            for r in members:
                result['signals'].append(dict(r, signal_date=sig, entry_date=entry,
                                               strategy=strategy, weight=1 / len(members)))
            for horizon in (6, 12):
                target = first_by_month.get(shift_month(entry, horizon))
                path_end = min(target or end, end)
                cohort_days = [d for d in trading if entry <= d <= path_end]
                total_path = {d: [0., 0., 0.] for d in cohort_days}
                statuses, reusable = [], True
                pending = []
                for r in members:
                    p, state, trades, can_reuse = holding_path(data, r['stock_id'], entry, target, end, buy_cost, sell_cost)
                    reusable &= can_reuse
                    statuses.append(state)
                    pending.extend((pay,amount/len(members)) for pay,amount in getattr(p,'pending',[]))
                    for d in cohort_days:
                        for k in range(3):
                            if p[d][k] is None or total_path[d][k] is None:
                                total_path[d][k] = None
                            else:
                                total_path[d][k] += p[d][k] / len(members)
                    for trade in trades:
                        result['trades'].append(dict(trade, signal_date=sig, strategy=strategy, horizon=horizon,
                                                     weight=1 / len(members)))
                if not members:
                    total_path = {d: [1., 1., 1.] for d in cohort_days}
                    reusable = True
                mature = target is not None and target <= end
                resolved = all(s in ('realized', 'settled_delist', 'unfilled_cash','pending_cash') for s in statuses)
                state = ('right_censored' if not mature else
                         'resolved' if resolved else 'unresolved')
                final = total_path[cohort_days[-1]]
                key = (strategy, horizon, entry)
                paths[key], reusable_map[key] = total_path, reusable
                pending_map[key] = pending
                result['cohorts'].append({'signal_date': sig, 'entry_date': entry, 'exit_date': target,
                    'horizon': horizon, 'strategy': strategy, 'n_stocks': len(members), 'status': state,
                    'return_price': final[0] - 1 if mature and resolved else None,
                    'return_total': final[1] - 1 if mature and resolved else None,
                    'return_net': final[2] - 1 if mature and resolved else None,
                    'marked_return_net': final[2] - 1 if final[2] is not None else None,
                    'pending_cash_net':sum(v for _,v in pending),
                    'unresolved_stocks': sum(s not in ('realized','settled_delist','unfilled_cash','right_censored','pending_cash') for s in statuses)})
    # h 個獨立資金梯隊，每月只更新輪到的一格；未投入部分保持現金。
    for strategy in ('momentum', 'universe'):
        for h in (6, 12):
            slots = [{'capital': 1 / h, 'key': None,'reserve':0.,'pending':[]} for _ in range(h)]
            entry_index = {entry: i for i, (_, entry) in enumerate(entries)}
            peak, curve, blocked = 1., [], False
            for d in trading:
                for slot in slots:
                    paid=sum(v for pay,v in slot['pending'] if pay<=d)
                    slot['reserve']+=paid
                    slot['pending']=[(pay,v) for pay,v in slot['pending'] if pay>d]
                    if paid:
                        result['trades'].append({'date':d,'strategy':strategy,'horizon':h,
                            'side':'post_exit_cash_payment','cash_fund_units':paid})
                if d in entry_index:
                    slot = slots[entry_index[d] % h]
                    old = slot['key']
                    if old:
                        old_path = paths[old]
                        last_date = max(x for x in old_path if x <= d)
                        multiplier=old_path[last_date][2]
                        outstanding=pending_map[old]
                        old_capital=slot['capital']
                        slot['pending'].extend((pay,v*old_capital) for pay,v in outstanding)
                        slot['capital'] *= (multiplier-sum(v for _,v in outstanding)) if multiplier is not None else 1
                        if not reusable_map[old]:
                            blocked = True
                            result['issues'].append(f'{strategy}/{h}/{d}: 到期資金未完整回收；梯隊曲線自此無法認證')
                        slot['key'] = None
                    slot['capital']+=slot['reserve']
                    slot['reserve']=0.
                    slot['key'] = (strategy, h, d)
                value = 0.
                for slot in slots:
                    key = slot['key']
                    multiplier=1
                    if key:
                        p=paths[key]
                        multiplier=(p[d] if d in p else p[next(reversed(p))])[2]
                    if multiplier is None:
                        if not blocked:
                            result['issues'].append(f'{strategy}/{h}/{d}: 缺少可靠每日估值')
                        blocked=True
                    value += slot['capital'] * (multiplier if multiplier is not None else 1)
                    value += slot['reserve'] + sum(v for _,v in slot['pending'])
                peak = max(peak, value)
                row = {'date': d, 'strategy': strategy, 'horizon': h,
                       'nav': None if blocked else value, 'drawdown': None if blocked else value / peak - 1}
                result['equity'].append(row)
                curve.append(row)
            cohorts = [c for c in result['cohorts'] if c['strategy'] == strategy and c['horizon'] == h]
            vals = [c['return_net'] for c in cohorts if c['return_net'] is not None]
            complete = all(c['status'] != 'unresolved' for c in cohorts)
            years = (date.fromisoformat(trading[-1]) - date.fromisoformat(trading[0])).days / 365.25
            valid_curve = [r['nav'] for r in curve if r['nav'] is not None]
            daily = [b / a - 1 for a, b in zip(valid_curve, valid_curve[1:]) if a > 0]
            result['summary'][f'{strategy}_{h}'] = {
                'n': len(vals), 'unresolved_cohorts': sum(c['status']=='unresolved' for c in cohorts),
                'mean': statistics.mean(vals) if vals and complete else None,
                'median': statistics.median(vals) if vals and complete else None,
                'positive_rate': statistics.mean(v > 0 for v in vals) if vals and complete else None,
                'mean_ci95': block_ci(vals, h) if complete else None,
                'CAGR': curve[-1]['nav'] ** (1 / years) - 1 if years and not blocked else None,
                'MDD': min(r['drawdown'] for r in curve) if not blocked else None,
                'annual_volatility': statistics.stdev(daily) * math.sqrt(252) if len(daily)>1 and not blocked else None}
    # 比較持有期時只用共同成熟且無未解決部位的入場日。
    for strategy in ('momentum', 'universe'):
        cs = [c for c in result['cohorts'] if c['strategy'] == strategy]
        maps = {h: {c['entry_date']: c for c in cs if c['horizon']==h} for h in (6,12)}
        common = sorted(d for d in maps[6].keys() & maps[12].keys()
                        if maps[6][d]['status'] != 'right_censored' and maps[12][d]['status'] != 'right_censored')
        valid = all(maps[h][d]['return_net'] is not None for d in common for h in (6,12))
        result['summary'][f'{strategy}_common_entries'] = {'n': len(common), 'entries': common,
            'mean_6m': statistics.mean(maps[6][d]['return_net'] for d in common) if common and valid else None,
            'mean_12m': statistics.mean(maps[12][d]['return_net'] for d in common) if common and valid else None}
    for strategy in ('momentum', 'universe'):
        for h in (6, 12):
            cohorts = [c for c in result['cohorts'] if c['strategy']==strategy and c['horizon']==h]
            for year in sorted({c['entry_date'][:4] for c in cohorts}):
                batch = [c for c in cohorts if c['entry_date'].startswith(year) and c['status']!='right_censored']
                values = [c['return_net'] for c in batch]
                good = bool(values) and all(v is not None for v in values)
                result['summary'][f'{strategy}_{h}_entry_year_{year}'] = {
                    'n': len(values), 'mean': statistics.mean(values) if good else None,
                    'median': statistics.median(values) if good else None,
                    'worst_cohort': min(values) if good else None}
            selected = {c['entry_date']:c for c in cohorts if c['status']!='right_censored'}
            baseline = {c['entry_date']:c for c in result['cohorts']
                        if c['strategy']=='universe' and c['horizon']==h and c['status']!='right_censored'}
            dates = sorted(selected.keys() & baseline.keys())
            good = all(selected[d]['return_net'] is not None and baseline[d]['return_net'] is not None for d in dates)
            diff = [selected[d]['return_net'] - baseline[d]['return_net'] for d in dates] if good else []
            result['summary'][f'{strategy}_{h}_vs_universe'] = {
                'n': len(dates), 'mean_excess': statistics.mean(diff) if diff else None,
                'beat_rate': statistics.mean(v>0 for v in diff) if diff else None,
                'excess_ci95': block_ci(diff,h) if diff else None}
    # 指數只提供收盤，獨立列參考曲線，不冒充下一日開盤成交的超額基準。
    for name, values in data.get('benchmarks', {}).items():
        reference_days = [d for d in trading if values.get(d,0)>0]
        if not reference_days:
            continue
        base, peak = values[reference_days[0]], 1.
        for d in reference_days:
            nav = values[d] / base
            peak = max(peak,nav)
            result['equity'].append({'date':d,'strategy':name+'_close_reference','horizon':0,
                                     'nav':nav,'drawdown':nav/peak-1})
    if result['issues'] or any(c['status']=='unresolved' for c in result['cohorts']):
        if not data.get('synthetic'):
            result['certification'] = 'NOT_CERTIFIED'
    return result
