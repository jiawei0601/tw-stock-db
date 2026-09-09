"""已核實興櫃區間與日線成交代理；禁止用FinMind前日均價成交。"""
import math
from listed_universe import ListedUniverse


class MarketUniverse(ListedUniverse):
    def __init__(self, records):
        super().__init__(records, allowed_markets=('TWSE','TPEX','EMERGING'))
        for sid, intervals in self.intervals.items():
            ordered=sorted(intervals,key=lambda r:r['start'])
            for left,right in zip(ordered,ordered[1:]):
                if left.get('end') is None or left['end']>right['start']:
                    raise ValueError(f'overlapping market intervals: {sid}')

    def market_at(self,sid,day):
        return next((r['market'] for r in self.intervals.get(sid,[])
                     if r['start']<=day and (r.get('end') is None or day<r['end'])),None)


def additional_price_ids(payload,original_ids,calendar,start,end):
    """少於200個可能有效日的股票不可能過暖機；可省略下載且保留證明。"""
    universe=MarketUniverse(payload['records'])
    candidates={r['stock_id'] for r in payload['records'] if r['start']<=end and
                (not r.get('end') or r['end']>start)}-set(original_ids)
    days=[d for d in calendar if start<=d<=end]
    required=[];skipped={}
    for sid in sorted(candidates):
        possible=sum(universe.eligible(sid,day) for day in days)
        if possible<200:skipped[sid]=possible
        else:required.append(sid)
    return required,skipped


def prepare_prices(raw,universe):
    """未知資格排除；興櫃open歸零，前日均價不可抬高當日高點。"""
    result=universe.restrict_prices(raw)
    return {sid:{day:dict(row,open=0.) if universe.market_at(sid,day)=='EMERGING' else row
                 for day,row in rows.items()} for sid,rows in result.items()}


class MarketExecution:
    """成交日才讀取VWAP；滑價每邊計，現有0.3%每邊成本仍由引擎計。"""
    def __init__(self,universe,*,allow_emerging=True,slippage=0.,participation=.01):
        if not 0<=slippage<1:raise ValueError('invalid slippage')
        if not 0<participation<=1:raise ValueError('invalid participation')
        self.universe=universe
        self.allow_emerging=allow_emerging
        self.slippage=slippage
        self.participation=participation
        self.audit=[]
        self.capacity_audit=[]

    def capacity(self,sid,day,row,side,shares):
        market=self.universe.market_at(sid,day)
        passed=market in ('TWSE','TPEX') or (market=='EMERGING' and self.allow_emerging and
                0<shares<=row.get('Trading_Volume',0)*self.participation)
        self.capacity_audit.append(dict(stock_id=sid,date=day,side=side,shares=shares,
                                       daily_volume=row.get('Trading_Volume',0),passed=passed))
        return passed

    def __call__(self,sid,day,row,side):
        if side not in ('buy','sell'):raise ValueError('invalid side')
        market=self.universe.market_at(sid,day)
        reason='available'
        price=0.
        if market in ('TWSE','TPEX'):
            price=row.get('open',0.)
        elif market=='EMERGING' and self.allow_emerging:
            volume=row.get('Trading_Volume',0)
            money=row.get('Trading_money',0)
            if volume>0 and money>0:
                price=money/volume
                if not row.get('min',0)>0 or not row.get('min',0)-.01<=price<=row.get('max',0)+.01:
                    price=0.;reason='vwap_outside_range'
                else:
                    price*=1+self.slippage if side=='buy' else 1-self.slippage
            else:reason='no_turnover'
        else:reason='ineligible_market'
        if not math.isfinite(price) or price<=0:
            price=0.
            if reason=='available':reason='missing_price'
        self.audit.append(dict(stock_id=sid,date=day,side=side,market=market,price=price,reason=reason))
        return price
