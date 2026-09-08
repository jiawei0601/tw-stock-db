import copy
from datetime import date, timedelta
import unittest

from momentum_engine import holding_path, run_backtest, select_stocks, validate


def fixture():
    d, end = date(2018,12,1), date(2022,2,2)
    days=[]
    while d<=end:
        if d.weekday()<5:
            days.append(d.isoformat())
        d+=timedelta(days=1)
    prices={sid:{day:{'open':100*(1+growth)**i, 'close':100*(1+growth)**i,
                     'volume':10000,'buyable':True,'sellable':True}
                 for i,day in enumerate(days)} for sid,growth in [('A',.001),('B',.0002),('C',0),('D',-.0001)]}
    return {'calendar':days,'prices':prices,'events':[],
            'securities':[{'stock_id':sid,'start':days[0],'end':None,'known_at':days[0],'source':'synthetic'} for sid in prices],
            'verified':{k:True for k in ('universe','events','execution','coverage')},
            'evidence':{k:'synthetic test fixture' for k in ('universe','events','execution','coverage')},
            'synthetic':True}


class EngineTests(unittest.TestCase):
    def test_future_mutation_and_ipo_cannot_change_selection(self):
        data=fixture()
        expected=select_stocks(data,'2019-12-31')
        for rows in data['prices'].values():
            for d,row in rows.items():
                if d>'2019-12-31':
                    row['close']*=100000
        data['events']=[{'stock_id':'B','date':'2021-01-04','known_at':'2020-12-01','kind':'split','ratio':100,'source':'future'}]
        data['securities'].append({'stock_id':'FUTURE','start':'2020-01-01','end':None,'known_at':'2019-01-01','source':'IPO'})
        data['prices']['FUTURE']=copy.deepcopy(data['prices']['A'])
        self.assertEqual(expected,select_stocks(data,'2019-12-31'))

    def test_prefix_only_data_matches_full(self):
        data=fixture()
        expected=select_stocks(data,'2019-12-31')
        data['calendar']=[d for d in data['calendar'] if d<='2019-12-31']
        for sid in data['prices']:
            data['prices'][sid]={d:r for d,r in data['prices'][sid].items() if d<='2019-12-31'}
        self.assertEqual(expected,select_stocks(data,'2019-12-31'))

    def test_skip_latest_month(self):
        data=fixture()
        expected=select_stocks(data,'2019-12-31')[0]
        for d,row in data['prices']['D'].items():
            if d.startswith('2019-12'):
                row['close']*=100
        self.assertEqual(expected,select_stocks(data,'2019-12-31')[0])

    def test_split_does_not_create_momentum(self):
        data=fixture()
        for d,row in data['prices']['A'].items():
            if d>='2019-07-01':
                row['open']/=2
                row['close']/=2
        data['events']=[{'stock_id':'A','date':'2019-07-01','known_at':'2019-06-01','kind':'split','ratio':2,'source':'fixture'}]
        actual=select_stocks(data,'2019-12-31')[0][0]['momentum']
        expected=select_stocks(fixture(),'2019-12-31')[0][0]['momentum']
        self.assertAlmostEqual(actual,expected)

    def test_exact_cost_accounting(self):
        data=fixture()
        p,state,_,reuse=holding_path(data,'A','2020-01-01','2020-07-01','2020-07-01',.003,.003)
        ratio=data['prices']['A']['2020-07-01']['open']/data['prices']['A']['2020-01-01']['open']
        self.assertAlmostEqual(p['2020-07-01'][2],ratio*.997/1.003)
        self.assertEqual(state,'realized')
        self.assertTrue(reuse)

    def test_unfilled_cash_preserved(self):
        data=fixture()
        data['prices']['A']['2020-01-01']['buyable']=False
        p,state,trades,_=holding_path(data,'A','2020-01-01','2020-07-01','2020-07-01',.003,.003)
        self.assertEqual(state,'unfilled_cash')
        self.assertEqual(p['2020-07-01'],(1,1,1))
        self.assertFalse(trades)

    def test_dividend_receivable_not_reinvested_or_doubled(self):
        data=fixture()
        for row in data['prices']['C'].values():
            row['open']=row['close']=100
        data['events']=[{'stock_id':'C','date':'2020-02-03','known_at':'2020-01-01','kind':'distribution',
                         'ratio':1,'cash':10,'pay_date':'2020-03-02','source':'fixture'}]
        p,state,_,_=holding_path(data,'C','2020-01-01','2020-07-01','2020-07-01',0,0)
        self.assertAlmostEqual(p['2020-02-03'][1],1.1)
        self.assertAlmostEqual(p['2020-03-02'][1],1.1)
        self.assertEqual(p['2020-07-01'][0],1)
        self.assertEqual(state,'realized')

    def test_exit_missing_not_realized(self):
        data=fixture()
        del data['prices']['A']['2020-07-01']
        _,state,_,reusable=holding_path(data,'A','2020-01-01','2020-07-01','2020-07-01',0,0)
        self.assertEqual(state,'unresolved_exit')
        self.assertFalse(reusable)

    def test_delisting_actual_settlement(self):
        data=fixture()
        data['events']=[{'stock_id':'C','date':'2020-02-03','known_at':'2020-01-01','kind':'delist',
                        'cash':30,'pay_date':'2020-03-02','source':'settlement'}]
        p,state,_,reuse=holding_path(data,'C','2020-01-01','2020-07-01','2020-07-01',0,0)
        self.assertAlmostEqual(p['2020-07-01'][1],.3)
        self.assertEqual(state,'settled_delist')
        self.assertTrue(reuse)

    def test_validation_refuses_missing_evidence(self):
        data=fixture()
        data['verified']['universe']=False
        with self.assertRaises(ValueError):
            run_backtest(data)

    def test_split_requires_explicit_ratio(self):
        data=fixture()
        data['events']=[{'stock_id':'A','date':'2020-02-03','known_at':'2020-01-01',
                        'kind':'split','source':'fixture'}]
        with self.assertRaises(ValueError):
            validate(data)

    def test_unknown_valuation_censored_period_invalidates_curve(self):
        data=fixture()
        data.pop('synthetic')
        del data['prices']['A']['2020-02-03']
        out=run_backtest(data,end='2020-03-02')
        self.assertEqual(out['certification'],'NOT_CERTIFIED')
        self.assertIsNone(out['summary']['momentum_6']['CAGR'])
        self.assertTrue(all(r['nav'] is None for r in out['equity']
                            if r['strategy']=='momentum' and r['date']>='2020-02-03'))

    def test_payment_after_exit_not_reinvested_before_paid(self):
        data=fixture()
        data['prices']={'C':data['prices']['C']}
        data['securities']=[s for s in data['securities'] if s['stock_id']=='C']
        data['events']=[{'stock_id':'C','date':'2020-06-30','known_at':'2020-06-01',
                        'kind':'distribution','cash':10,'pay_date':'2020-07-02','source':'fixture'}]
        for d,row in data['prices']['C'].items():
            if d>='2020-07-02':
                row['open']=row['close']=200
        out=run_backtest(data,end='2020-08-03',buy_cost=0,sell_cost=0)
        nav=next(r['nav'] for r in out['equity'] if r['date']=='2020-07-02' and r['strategy']=='momentum' and r['horizon']==6)
        self.assertAlmostEqual(nav,2.1)
        self.assertTrue(any(t['side']=='post_exit_cash_payment' and t['date']=='2020-07-02' for t in out['trades']))
        self.assertFalse(out['issues'])

    def test_stock_dividend_cannot_be_sold_before_delivery(self):
        data=fixture()
        data['events']=[{'stock_id':'C','date':'2020-06-30','known_at':'2020-06-01',
                        'kind':'distribution','ratio':1.1,'shares_available_date':'2020-07-03','source':'fixture'}]
        validate(data)
        p,state,trades,reuse=holding_path(data,'C','2020-01-01','2020-07-01','2020-07-06',0,0)
        self.assertEqual(state,'unresolved_exit')
        self.assertFalse(reuse)
        self.assertEqual(p['2020-07-01'],(None,None,None))
        sold=next(t for t in trades if t['side']=='sell')
        self.assertAlmostEqual(sold['shares_per_unit'],.01)

    def test_ladder_uses_idle_cash_and_matures_calendar_months(self):
        data=fixture()
        out=run_backtest(data,end='2021-02-01',buy_cost=0,sell_cost=0)
        first=next(r for r in out['cohorts'] if r['strategy']=='momentum' and r['horizon']==6)
        self.assertEqual(first['entry_date'],'2020-01-01')
        self.assertEqual(first['exit_date'],'2020-07-01')
        self.assertEqual(out['certification'],'SYNTHETIC_DEMO')
        nav=next(r['nav'] for r in out['equity'] if r['strategy']=='momentum' and r['horizon']==6 and r['date']=='2020-01-02')
        self.assertAlmostEqual(nav,5/6+1.001/6)
        pending=[r for r in out['cohorts'] if r['status']=='right_censored']
        self.assertTrue(pending)
        self.assertTrue(all(r['return_net'] is None for r in pending))

    def test_unresolved_does_not_disappear_from_summary(self):
        data=fixture()
        data['prices']['A']['2020-07-01']['sellable']=False
        out=run_backtest(data,end='2021-02-01')
        self.assertIsNone(out['summary']['momentum_6']['mean'])
        self.assertIsNone(out['summary']['momentum_6']['CAGR'])


if __name__=='__main__':
    unittest.main()
