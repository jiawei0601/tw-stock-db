"""研究用進場確認；只使用訊號日及之前資料，不更改出場。"""
import math


MODES = {
    'base': (False, False, False),
    'trend': (True, False, False),
    'market': (False, True, False),
    'trend_cap': (True, False, True),
    'trend_market': (True, True, False),
    'all': (True, True, True),
}
LABELS = {
    'base': '現有營收濾網基準', 'trend': '加個股趨勢', 'market': '加大盤條件',
    'trend_cap': '個股趨勢＋乖離≤10%', 'trend_market': '個股趨勢＋大盤',
    'all': '三項合併',
}


class TechnicalFeatures:
    def __init__(self, prices, events, calendar, market_prices):
        self.prices = prices
        self.events = events
        self.calendar = calendar
        self.positions = {day:i for i,day in enumerate(calendar)}
        self.market_prices = market_prices
        self.stock_cache = {}
        self.market_cache = {}

    def window(self, day, n):
        i = self.positions.get(day, -1)
        return self.calendar[i-n+1:i+1] if i >= n-1 else []

    def stock(self, sid, day):
        key = (sid, day)
        if key in self.stock_cache:
            return self.stock_cache[key]
        result = dict(close=None, ma20=None, prior_ma20=None, stock_reason='stock_ma_unavailable')
        window = self.window(day, 25)
        rows = self.prices.get(sid, {})
        closes = [rows.get(d, {}).get('close', 0) for d in window]
        if len(window) == 25 and all(math.isfinite(c) and c > 0 for c in closes):
            adjusted = []
            factor = 1.0
            for d, close in reversed(list(zip(window, closes))):
                adjusted.append(close / factor)
                event = self.events.get((sid, d), 1.0)
                if not math.isfinite(event) or event <= 0:
                    raise ValueError(f'invalid corporate action: {sid} {d}')
                factor *= event
            adjusted.reverse()
            result = dict(close=closes[-1], ma20=sum(adjusted[-20:])/20,
                          prior_ma20=sum(adjusted[:20])/20, stock_reason='available')
        self.stock_cache[key] = result
        return result

    def market(self, day):
        if day in self.market_cache:
            return self.market_cache[day]
        window = self.window(day, 60)
        closes = [self.market_prices.get(d, 0) for d in window]
        result = dict(market_close=None, market_ma60=None, market_reason='market_ma_unavailable')
        if len(window) == 60 and all(math.isfinite(c) and c > 0 for c in closes):
            result = dict(market_close=closes[-1], market_ma60=sum(closes)/60, market_reason='available')
        self.market_cache[day] = result
        return result


class TechnicalEntryGate:
    def __init__(self, revenue_gate, features, mode):
        if mode not in MODES:
            raise ValueError(f'unknown entry mode: {mode}')
        self.revenue_gate = revenue_gate
        self.features = features
        self.mode = mode
        self.audit = []

    def evaluate(self, sid, day):
        revenue = self.revenue_gate.evaluate(sid, day)
        row = {**revenue, 'revenue_reason': revenue['reason'], 'entry_mode': self.mode,
               'close': None, 'ma20': None, 'prior_ma20': None, 'deviation': None,
               'market_close': None, 'market_ma60': None}
        if not revenue['passed']:
            return row
        trend, market, cap = MODES[self.mode]
        if trend:
            s = self.features.stock(sid, day)
            row.update({k:s[k] for k in ('close','ma20','prior_ma20')})
            if s['stock_reason'] != 'available':
                return {**row, 'passed': False, 'reason': s['stock_reason']}
            row['deviation'] = s['close']/s['ma20']-1
            if not (s['close'] > s['ma20'] and s['ma20'] > s['prior_ma20']):
                return {**row, 'passed': False, 'reason': 'stock_trend_failed'}
            if cap and s['close'] > s['ma20']*1.10:
                return {**row, 'passed': False, 'reason': 'above_10pct_ma20'}
        if market:
            m = self.features.market(day)
            row.update({k:m[k] for k in ('market_close','market_ma60')})
            if m['market_reason'] != 'available':
                return {**row, 'passed': False, 'reason': m['market_reason']}
            if not m['market_close'] > m['market_ma60']:
                return {**row, 'passed': False, 'reason': 'market_trend_failed'}
        return row

    def __call__(self, sid, day):
        row = self.evaluate(sid, day)
        self.audit.append(row)
        return row['passed']
