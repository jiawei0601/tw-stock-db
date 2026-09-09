"""獨立可續傳興櫃補價快取；沿用FinMind公開API節流，不覆寫既有raw DB。"""
import argparse
import json
import sqlite3
from pathlib import Path
from fetch_momentum_pit import Client, FetchStopped, init_db, token_from_env, query_for
from emerging_market import additional_price_ids


ROOT=Path('data/momentum_pit/emerging_universe')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-requests',type=int,default=600)
    args=parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    base=json.loads(Path('data/momentum_pit/manifest.json').read_text(encoding='utf-8'))
    conn=init_db(ROOT/'finmind_extra.db')
    client=Client(conn,token_from_env(),args.max_requests)
    # 目前加上曾轉板的興櫃候選只用於抓取，不當作歷史資格。
    info=client.fetch({'dataset':'TaiwanStockInfo'})
    ids={str(r['stock_id']) for r in info if r.get('type')=='emerging'}
    history=ROOT/'intervals.json'
    original=set(base['candidate_ids'])
    ids=sorted(s for s in ids-original if len(s)==4 and s.isascii() and s.isdigit() and not s.startswith('0'))
    skipped={}
    if history.exists():
        with sqlite3.connect('file:data/momentum_pit/finmind_raw.db?mode=ro',uri=True) as source:
            body=source.execute('SELECT body FROM responses WHERE query=?',('{"dataset":"TaiwanStockTradingDate"}',)).fetchone()[0]
        calendar=sorted({r['date'] for r in json.loads(body)['data']})
        ids,skipped=additional_price_ids(json.loads(history.read_text(encoding='utf-8')),original,calendar,base['start'],base['end'])
    (ROOT/'price_manifest.json').write_text(json.dumps(dict(start=base['start'],end=base['end'],extra_ids=ids,skipped_warmup_ids=skipped),indent=2),encoding='utf-8')
    try:
        for i,sid in enumerate(ids):
            rows=client.fetch(query_for('TaiwanStockPrice',base['start'],base['end'],sid))
            status=dict(state='running',completed=i+1,total=len(ids),stock_id=sid,rows=len(rows))
            (ROOT/'price_status.json').write_text(json.dumps(status),encoding='utf-8')
        status=dict(state='complete',completed=len(ids),total=len(ids))
    except FetchStopped as exc:
        status=dict(state='stopped',reason=str(exc),total=len(ids),requests_used=client.used)
    (ROOT/'price_status.json').write_text(json.dumps(status),encoding='utf-8')
    print(json.dumps(status),flush=True)
    conn.close()
    if status['state']!='complete':raise SystemExit(1)


if __name__=='__main__':main()
