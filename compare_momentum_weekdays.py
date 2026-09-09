"""比較每週訊號日；沿用未認證價格路徑，月底動能出場不變。"""
import argparse
import csv
import json
from pathlib import Path

from dynamic_momentum import load_data, signal_table
from weekly_hermes_momentum import diagnostic_high_envelope, simulate_weekly, summarize, weekly_signal_days


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revenue-file', help='具備逐版本known_on與source的營收JSON')
    args = parser.parse_args()
    revenue = None
    if args.revenue_file:
        from revenue_filter import RevenueFilter
        revenue = RevenueFilter.from_json(args.revenue_file)
        if not any(r['known_on'] < '2020-01-01' for rows in revenue.records.values() for r in rows):
            parser.error('缺少2020年前可取得的營收版本，不能把全數缺資料當成營收濾網回測')
    out = Path('backtest/momentum_rerun_20260909_weekdays')
    if revenue is not None:out = out.with_name(out.name + '_revenue')
    out.mkdir(exist_ok=True)
    prices, events, calendar, _, fingerprint, excluded = load_data()
    prices, audit = diagnostic_high_envelope(prices)
    ends = {d[:7]: d for d in calendar}
    valid = {s: sorted(d for d, r in rows.items() if r.get('close', 0) > 0 and r.get('Trading_Volume', 0) > 0) for s, rows in prices.items()}
    # Month-end reference tables are shared; weekday tables are kept separate so
    # changing entry frequency does not introduce additional monthly exit checks.
    refs = {d: signal_table(prices, events, calendar, d, valid) for d in ends.values() if '2019-10-01' <= d and d[:7] < calendar[-1][:7]}
    cutoff = '2022-12-30'
    prefix_prices = {s: {d: r for d, r in rows.items() if d <= cutoff} for s, rows in prices.items()}
    prefix_events = {k: v for k, v in events.items() if k[1] <= cutoff}
    prefix_calendar = [d for d in calendar if d <= cutoff]
    summaries = {}
    for weekday in range(5):
        signal_days = weekly_signal_days(calendar, weekday)
        tables = dict(refs)
        for day in calendar:
            if day >= '2019-10-01' and day in signal_days and day not in tables:
                tables[day] = signal_table(prices, events, calendar, day, valid)
        options = dict(trailing=False, observation_days=None, equity_allocation=True, position_weight=.05, peak_stop=.30, signal_weekday=weekday, roll_holidays=True, entry_filter=revenue)
        result = simulate_weekly(prices, events, calendar, tables, **options)
        prefix_tables = {d: t for d, t in tables.items() if d <= cutoff}
        # Recompute the cutoff signal using truncated inputs, then compare the
        # full NAV and exit path before the cutoff for every tested weekday.
        assert tables[cutoff] == signal_table(prefix_prices, prefix_events, prefix_calendar, cutoff)
        prefix = simulate_weekly(prefix_prices, prefix_events, prefix_calendar, prefix_tables, **options)
        assert prefix[0] == [r for r in result[0] if r['date'] <= cutoff]
        assert prefix[2] == [r for r in result[2] if r['exit'] <= cutoff]
        summary = summarize(result)
        summary['prefix_invariance'] = True
        summary['signal_days'] = sum(d >= '2020-01-01' and d in signal_days for d in tables)
        summaries[str(weekday)] = summary
        folder = out / str(weekday)
        folder.mkdir(exist_ok=True)
        for name, rows in zip(['nav', 'roundtrips', 'sell_legs', 'orders'], result[:4]):
            if rows:
                with (folder / (name + '.csv')).open('w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
        (out / 'comparison.json').write_text(json.dumps(dict(fingerprint=fingerprint, excluded_dates=excluded, adjusted_high_rows=len(audit), certification='PRICE_PATH_DIAGNOSTIC_ONLY', settings=dict(revenue_filter='monthly_yoy_positive_accelerating' if revenue is not None else None, revenue_file=args.revenue_file, initial_capital=1000000, position_weight=.05, peak_stop=.30, roll_holidays=True, execution='next_session_open', momentum_exit='month_end', observation_days=None, ma_exit=False), weekdays=summaries), ensure_ascii=False, indent=2), encoding='utf-8')
        print(weekday, json.dumps(summary['stale_scenario']), summary['ending_equity'], flush=True)
    original = Path('backtest/momentum_rerun_20260909/result.json')
    if original.exists():
        baseline = json.loads(original.read_text(encoding='utf-8'))['weekly_momentum_exit']
        print('Prior Wednesday (skip holidays):', baseline['ending_equity'], flush=True)


if __name__ == '__main__':
    main()
