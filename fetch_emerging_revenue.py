"""補ROTC月營收封存至獨立DB；當前封存數值不冒充歷史發布版本。"""
import hashlib
import json
import sqlite3
import time
from datetime import datetime,timezone
from pathlib import Path
import requests
from backfill_revenue_archive import init_db,store_page
from revenue_filter import month_shift


def main():
    root=Path('data/momentum_pit/emerging_universe')
    root.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(root/'revenue.db');init_db(conn)
    session=requests.Session()
    months=[];month='2018-01'
    while month<='2026-08':months.append(month);month=month_shift(month,1)
    completed=0
    for month in months:
        year,number=map(int,month.split('-'))
        for variant in (0,1):
            url=f'https://mopsov.twse.com.tw/nas/t21/rotc/t21sc03_{year-1911}_{number}_{variant}.html'
            cached=conn.execute('SELECT row_count,error,body,fetched_at FROM pages WHERE url=?',(url,)).fetchone()
            if cached and cached[1] is None:completed+=1;continue
            expected_title=f'興櫃公司{year-1911}年{number}月份'
            def official_empty(body):
                text=body.decode('cp950',errors='replace')
                return expected_title in text and '查無資料' in text and '</html>' in text.lower()
            if cached and cached[1] and official_empty(cached[2]):
                with conn:conn.execute('UPDATE pages SET error=NULL WHERE url=?',(url,))
                completed+=1;continue
            if cached and cached[1]:raise RuntimeError('previous_failed_page_requires_review')
            stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
            response=None
            try:
                response=session.get(url,timeout=30);response.raise_for_status()
                if official_empty(response.content):
                    with conn:conn.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,0,NULL)',
                        (url,'EMERGING',month,stamp,hashlib.sha256(response.content).hexdigest(),response.content))
                    count=0
                else:count=store_page(conn,url,'EMERGING',month,response.content,stamp)
            except (requests.RequestException,ValueError) as exc:
                body=response.content if response is not None else b''
                with conn:conn.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,0,?)',
                    (url,'EMERGING',month,stamp,hashlib.sha256(body).hexdigest(),body,type(exc).__name__))
                raise RuntimeError(f'revenue_fetch_failed:{month}:{variant}:{type(exc).__name__}') from None
            completed+=1
            print(month,variant,count,flush=True)
            (root/'revenue_status.json').write_text(json.dumps(dict(state='running',completed=completed,total=len(months)*2)),encoding='utf-8')
            time.sleep(1.7)
    (root/'revenue_status.json').write_text(json.dumps(dict(state='complete',completed=completed,total=len(months)*2)),encoding='utf-8')
    conn.close()


if __name__=='__main__':main()
