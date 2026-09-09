"""研究股票池的歷史上市／上櫃資格；未知區間拒絕，不以目前身分回填。"""
import json
from datetime import date
from pathlib import Path


class ListedUniverse:
    def __init__(self, records, *, allowed_markets=('TWSE','TPEX')):
        self.intervals = {}
        for r in records:
            sid, start, end = r['stock_id'], r['start'], r.get('end')
            if r['market'] not in allowed_markets or not r.get('source'):
                raise ValueError('require evidenced TWSE/TPEX interval')
            if date.fromisoformat(start).isoformat()!=start:
                raise ValueError('invalid start date')
            if end is not None and (date.fromisoformat(end).isoformat()!=end or end<=start):
                raise ValueError('invalid exclusive end date')
            self.intervals.setdefault(sid,[]).append(dict(r))

    @classmethod
    def from_json(cls, path: Path):
        payload=json.loads(path.read_text(encoding='utf-8'))
        if payload.get('universe_kind')!='TWSE_TPEX_EFFECTIVE_INTERVALS':
            raise ValueError('wrong universe kind')
        return cls(payload['records'])

    def eligible(self, sid, day):
        return any(r['start']<=day and (r.get('end') is None or day<r['end'])
                   for r in self.intervals.get(sid,[]))

    def restrict_prices(self, prices):
        """同時限制交易、排名、動能暖機及MA資料，禁止興櫃價混入。"""
        return {sid:{day:r for day,r in rows.items() if self.eligible(sid,day)}
                for sid,rows in prices.items()}
