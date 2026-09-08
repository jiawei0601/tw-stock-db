"""在repo根目錄執行；將研究快取轉為足夠暖機的live種子，不含帳戶資料。"""
import gzip
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dynamic_momentum import load_data


def main():
    prices,events,calendar,_,fingerprint,_=load_data()
    cutoff=(date.fromisoformat(calendar[-1])-timedelta(days=800)).isoformat()
    names={}
    with sqlite3.connect('file:data/momentum_pit/finmind_raw.db?mode=ro',uri=True) as conn:
        for query,body in conn.execute('select query,body from responses where status=200'):
            if json.loads(query).get('dataset')=='TaiwanStockInfo':
                names.update({r['stock_id']:r['stock_name'] for r in json.loads(body)['data']})
    fields=['open','max','min','close','Trading_Volume','Trading_money']
    seed=dict(asof=calendar[-1],fingerprint=fingerprint,names=names,
              calendar=[d for d in calendar if d>=cutoff],events=[[s,d,v] for (s,d),v in events.items() if d>=cutoff],
              prices={s:{d:{k:r.get(k,0) for k in fields} for d,r in rows.items() if d>=cutoff} for s,rows in prices.items()})
    out=Path('live_data');out.mkdir(exist_ok=True)
    target=out/'seed-export.json.gz'
    with gzip.open(target,'wt',encoding='utf-8',compresslevel=3) as f:json.dump(seed,f,ensure_ascii=False,separators=(',',':'))
    print(f'暖機種子已匯出：{target}；請勿覆蓋已有增量更新的遠端seed或成交帳戶。')


if __name__=='__main__':main()
