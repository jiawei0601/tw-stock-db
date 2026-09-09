"""單月營收年增率為正且加速；以可追溯的資料版本做時點查詢。"""
import json
import math
from datetime import date
from pathlib import Path


def month_shift(month, offset):
    parsed = date.fromisoformat(month + '-01')
    n = parsed.year * 12 + parsed.month - 1 + offset
    return f'{n // 12:04d}-{n % 12 + 1:02d}'


class RevenueFilter:
    """known_on 是該版本值有證據已可取得的日期，非營收所屬月。

    日資料不知盤前或盤後，保守使用 known_on < signal_day。
    修訂值保留新版本，不可回填舊日期。呼叫端負責提供真實來源證據。
    """

    def __init__(self, records):
        self.records = {}
        seen = {}
        for raw in records:
            row = dict(raw)
            sid, month, known = row['stock_id'], row['month'], row['known_on']
            date.fromisoformat(month + '-01')
            date.fromisoformat(known)
            if not sid or not row.get('source') or known <= month + '-01':
                raise ValueError('營收版本需來源與合理的已知日期')
            revenue = row['revenue']
            if isinstance(revenue, bool) or not isinstance(revenue, (int, float)) or not math.isfinite(revenue) or revenue < 0:
                raise ValueError('營收必須是非負有限數值，缺漏不能填零')
            key = sid, month, known
            if key in seen and seen[key] != revenue:
                raise ValueError('同日同月份版本衝突，需人工核對')
            seen[key] = revenue
            self.records.setdefault(sid, []).append(row)
        for rows in self.records.values():
            rows.sort(key=lambda r: (r['known_on'], r['month']))

    @classmethod
    def from_json(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('availability_basis') != 'version_evidence':
            raise ValueError('需逐版本時點證據；不能使用FinMind月份日期或MOPS出表日替代')
        return cls(data['records'])

    def evaluate(self, stock_id, signal_day):
        date.fromisoformat(signal_day)
        visible = {}
        for row in self.records.get(stock_id, []):
            if row['known_on'] >= signal_day:
                break
            if row['month'] < signal_day[:7]:
                visible[row['month']] = row
        result = dict(passed=False, reason='missing_revenue', month=None, yoy=None, previous_yoy=None)
        if not visible:
            return result
        latest = max(visible)
        result['month'] = latest
        # 允許前月尚未公告，但不無限沿用過期的好營收。
        if latest < month_shift(signal_day[:7], -2):
            return {**result, 'reason': 'stale_revenue'}
        months = [latest, month_shift(latest, -1), month_shift(latest, -12), month_shift(latest, -13)]
        if any(m not in visible for m in months):
            return {**result, 'reason': 'missing_comparison_month'}
        current, previous, year_ago, previous_year_ago = [visible[m]['revenue'] for m in months]
        if year_ago <= 0 or previous_year_ago <= 0:
            return {**result, 'reason': 'invalid_year_ago_base'}
        yoy, prior_yoy = current / year_ago - 1, previous / previous_year_ago - 1
        passed = yoy > 0 and yoy > prior_yoy
        return dict(passed=passed, reason='pass' if passed else 'not_positive_or_accelerating', month=latest, yoy=yoy, previous_yoy=prior_yoy,
                    evidence=[{k: visible[m][k] for k in ('month', 'revenue', 'known_on', 'source')} for m in months])

    def __call__(self, stock_id, signal_day):
        return self.evaluate(stock_id, signal_day)['passed']
