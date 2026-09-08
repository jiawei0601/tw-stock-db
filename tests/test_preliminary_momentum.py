import unittest
from preliminary_momentum import rank,forward,aggregate
from tests.test_momentum_engine import fixture


class PreliminaryTests(unittest.TestCase):
    def test_missing_member_keeps_weight(self):
        out=aggregate([.2,None])
        self.assertEqual(out['missing'],1)
        self.assertIsNone(out['return'])
        self.assertAlmostEqual(out['unresolved_zero_value'],-.4)
        self.assertAlmostEqual(out['unresolved_flat'],.1)

    def test_missing_exit_not_dropped_or_zeroed(self):
        rows={'2020-01-02':{'open':100,'Trading_Volume':1}}
        self.assertEqual(forward(rows,[],'2020-01-02','2020-07-01'),(None,'missing_exit'))

    def test_split_and_cost(self):
        rows={'2020-01-02':{'open':100,'Trading_Volume':1},
              '2020-07-01':{'open':55,'Trading_Volume':1}}
        value,_=forward(rows,[('2020-06-01',2)],'2020-01-02','2020-07-01')
        self.assertAlmostEqual(value,1.1*.997/1.003-1)

    def test_future_raw_prices_cannot_change_rank(self):
        data=fixture()
        for rows in data['prices'].values():
            for row in rows.values():row['Trading_Volume']=row['volume']
        baseline=rank(data['prices'],{},data['calendar'],'2019-12-31')
        self.assertTrue(baseline)
        for rows in data['prices'].values():
            for day,row in rows.items():
                if day>'2019-12-31':row['close']*=1000
        self.assertEqual(baseline,rank(data['prices'],{'A':[('2021-01-01',100)]},data['calendar'],'2019-12-31'))


if __name__=='__main__':unittest.main()
