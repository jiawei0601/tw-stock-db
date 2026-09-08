"""富邦唯讀快照；與策略帳本隔離，不推定成交歸屬或費稅。"""
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'live_data' / 'fubon'
FILL_FIELDS = ('date', 'order_no', 'stock_no', 'buy_sell', 'filled_no',
               'filled_avg_price', 'filled_qty', 'filled_price', 'order_type', 'filled_time')
INVENTORY_FIELDS = ('date', 'stock_no', 'order_type', 'lastday_qty', 'today_qty',
                    'tradable_qty', 'buy_filled_qty', 'sell_filled_qty')
ODD_FIELDS = ('lastday_qty', 'today_qty', 'tradable_qty', 'buy_filled_qty', 'sell_filled_qty')


def public_fields(row, fields):
    """只序列化文件列出的欄位，不對 SDK 物件做 repr／全欄位 dump。"""
    result = {}
    for key in fields:
        value = getattr(row, key, None)
        if value is None or isinstance(value, (str, bool, int)):
            result[key] = value
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError('nonfinite_broker_value')
            result[key] = value
        elif key in ('buy_sell', 'order_type'):
            result[key] = str(value).split('.')[-1]
        else:
            raise ValueError('unsupported_broker_value')
    return result


def collect_snapshot(sdk, accounts, start: date, end: date, pause=time.sleep):
    """已登入 SDK -> 30 日內唯讀快照；不含完整帳號，未知費稅為 None。"""
    today = datetime.now(timezone(timedelta(hours=8))).date()
    if start > end or (end - start).days >= 30 or end > today:
        raise ValueError('invalid_date_range')
    output = dict(schema_version=1, fetched_at=datetime.now(timezone.utc).isoformat(),
                  start=start.isoformat(), end=end.isoformat(), status='complete',
                  strategy_imported=False, accounts=[], errors=[])
    for account in accounts:
        if getattr(account, 'account_type', None) != 'stock':
            continue
        branch = str(getattr(account, 'branch_no', ''))
        number = str(getattr(account, 'account', ''))
        if not branch or not number:
            raise ValueError('missing_account_identity')
        alias = hashlib.sha256((branch + ':' + number).encode()).hexdigest()[:16]
        entry = dict(account_ref=alias, inventories=[], fills=[], status='complete')
        for kind, operation in (
            ('inventories', lambda: sdk.accounting.inventories(account)),
            ('fills', lambda: sdk.stock.filled_history(account, start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))),
        ):
            pause(0.3)
            try:
                reply = operation()
                if not reply.is_success:
                    raise ValueError('broker_query_rejected')
                if reply.data is None or not isinstance(reply.data, (list, tuple)):
                    raise ValueError('missing_response_data')
                rows = []
                for row in reply.data:
                    if kind == 'inventories':
                        item = public_fields(row, INVENTORY_FIELDS)
                        odd = getattr(row, 'odd', None)
                        item['odd'] = public_fields(odd, ODD_FIELDS) if odd is not None else None
                    else:
                        item = public_fields(row, FILL_FIELDS)
                        item.update(fees=None, assignment='unassigned')
                    rows.append(item)
                entry[kind] = rows
            except Exception as exc:
                entry['status'] = 'partial'
                output['status'] = 'partial'
                output['errors'].append(dict(account_ref=alias, query=kind, error_type=type(exc).__name__))
        output['accounts'].append(entry)
    if not output['accounts']:
        output['status'] = 'no_stock_accounts'
    return output


def save_snapshot(snapshot, directory=DATA):
    """每次獨立快照，失敗不覆蓋成功資料，無 account.json 寫入路徑。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
    path = directory / (stem + '.json')
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return path
