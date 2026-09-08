"""第三輪：只補 5305 原價與分割還原价；不查其他股票。"""
import json
import sqlite3
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from build_valuation import build_adj_prices_from_events


def main():
    result = {"stock_id": "5305", "dataset": "TaiwanStockPrice"}
    with sqlite3.connect("data/tw_stocks.db", timeout=5) as conn:
        result["raw_before"] = conn.execute("SELECT count(*) FROM fm_price_daily WHERE stock_id='5305'").fetchone()[0]
        result["adj_before"] = conn.execute("SELECT count(*) FROM fm_price_adj_daily WHERE stock_id='5305'").fetchone()[0]
        if not result["raw_before"]:
            env = {}
            for line in Path('.env').read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    env[key.strip()] = value.strip().strip(chr(34)).strip(chr(39))
            token = env.get('FINMIND_TOKEN') or env.get('FINMIND_API_TOKEN')
            if not token:
                result['reason'] = '.env 未找到 FinMind token；未發出請求'
            else:
                params = dict(dataset='TaiwanStockPrice', data_id='5305', start_date='2020-01-01', token=token)
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen('https://api.finmindtrade.com/api/v4/data?' + urllib.parse.urlencode(params), timeout=30) as resp:
                        payload = json.loads(resp.read().decode('utf-8-sig'))
                    result['status'] = payload.get('status')
                    rows = payload.get('data', []) if payload.get('status') == 200 else []
                    conn.executemany('INSERT OR REPLACE INTO fm_price_daily(stock_id,date,close) VALUES (?,?,?)', [('5305', r['date'], r['close']) for r in rows if r.get('stock_id') == '5305' and r.get('date') and r.get('close') is not None])
                    result['reason'] = '回應有資料' if rows else 'API 回應無資料或權限拒絕；未重試'
                except urllib.error.HTTPError as exc:
                    result['status'] = exc.code
                    result['reason'] = 'HTTP 錯誤，停止；未重試'
                except Exception as exc:
                    result['reason'] = type(exc).__name__ + '；停止，未輸出含 token 的例外內容'
        result['written'] = build_adj_prices_from_events(conn, stock_id='5305')
        result['raw_after'] = conn.execute("SELECT count(*) FROM fm_price_daily WHERE stock_id='5305'").fetchone()[0]
        result['adj_after'] = conn.execute("SELECT count(*) FROM fm_price_adj_daily WHERE stock_id='5305'").fetchone()[0]
        result['range'] = conn.execute("SELECT min(date),max(date) FROM fm_price_daily WHERE stock_id='5305'").fetchone()
    Path('backtest/validity_5305.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__ == '__main__':
    main()
