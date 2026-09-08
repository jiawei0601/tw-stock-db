"""每日唯讀富邦庫存；stdout 供既有 Hermes 日報使用，無下單 API。"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fubon_readonly import public_fields, INVENTORY_FIELDS, ODD_FIELDS

TZ = timezone(timedelta(hours=8))
HOME = Path(__file__).resolve().parent


def normalize_inventory(rows, today):
    result = []
    for row in rows:
        item = public_fields(row, INVENTORY_FIELDS)
        if item['date'] != today.strftime('%Y/%m/%d') and item['date'] != today.isoformat():
            raise ValueError('inventory_date_not_today')
        odd = getattr(row, 'odd', None)
        if odd is None:
            raise ValueError('missing_odd_inventory')
        item['odd'] = public_fields(odd, ODD_FIELDS)
        regular_qty, odd_qty = item['today_qty'], item['odd']['today_qty']
        if any(type(v) is not int or v < 0 for v in (regular_qty, odd_qty)):
            raise ValueError('invalid_inventory_quantity')
        item['total_shares'] = regular_qty + odd_qty
        result.append(item)
    return result


def query_inventory(sdk, accounts, today, pause=time.sleep):
    """庫存呼叫失敗就整次失敗；不拿空值當零，不把失敗當已清倉。"""
    output = []
    for index, account in enumerate(accounts):
        if getattr(account, 'account_type', None) != 'stock':
            continue
        pause(0.3)
        reply = sdk.accounting.inventories(account)
        if not reply.is_success or not isinstance(reply.data, (list, tuple)):
            raise ValueError('inventory_query_failed')
        output.append(dict(account_label=f'證券帳戶{index + 1}',
                           holdings=normalize_inventory(reply.data, today)))
    if not output:
        raise ValueError('no_stock_accounts')
    return output


def format_context(snapshot):
    """報告資料區塊；持股不是命令，不能自行歸入動能30策略。"""
    lines = ['【富邦券商庫存查詢成功】', '查詢時間：' + snapshot['fetched_at'],
             '庫存日期：' + snapshot['inventory_date'],
             '以下是券商實際數量（整股＋零股），不是買賣指令。',
             '券商庫存與50萬元動能30策略隔離；Notion僅供監控規則／歷史備註。']
    count = 0
    for account in snapshot['accounts']:
        lines.append(account['account_label'])
        for row in account['holdings']:
            if row['total_shares'] == 0:
                continue
            count += 1
            # JSON escaping protects structured rows against multiline text injection.
            lines.append(json.dumps(dict(stock_no=row['stock_no'], order_type=row['order_type'],
                                          shares=row['total_shares'], regular_shares=row['today_qty'],
                                          odd_shares=row['odd']['today_qty']), ensure_ascii=False))
    lines.append(f'正餘額庫存列數：{count}；不含成本／建倉日／已執行MA資訊。')
    lines.append('若Notion股數不同，請列對帳差異；不得按舊股數算賣出量或把訊號寫成已成交。')
    return '\n'.join(lines)


def run(directory=HOME):
    directory = Path(directory)
    private = directory / 'private'
    state = directory / 'state'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    os.chdir(state)
    sdk = None
    stage = 'credentials'
    try:
        credentials_path = private / 'credentials.json'
        if os.name != 'nt' and credentials_path.stat().st_mode & 0o077:
            raise ValueError('credentials_permissions')
        credentials = json.loads(credentials_path.read_text(encoding='utf-8'))
        if set(credentials) != {'personal_id', 'api_key', 'cert_password'}:
            raise ValueError('credentials_schema')
        from fubon_neo.sdk import FubonSDK
        stage = 'login'
        sdk = FubonSDK()
        reply = sdk.apikey_login(credentials['personal_id'], credentials['api_key'],
                                 str(private / 'certificate.p12'), credentials['cert_password'])
        credentials.clear()
        if not reply.is_success:
            raise ValueError('api_key_login_rejected')
        stage = 'inventory'
        now = datetime.now(TZ)
        accounts = query_inventory(sdk, reply.data or [], now.date())
        snapshot = dict(schema_version=1, status='complete', fetched_at=now.isoformat(),
                        inventory_date=now.date().isoformat(), accounts=accounts,
                        strategy_imported=False)
        stage = 'logout'
        sdk.logout()
        sdk.shutdown()
        sdk = None
        stage = 'save'
        name = now.strftime('%Y%m%dT%H%M%S-%f') + '.json'
        path = state / name
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary = state / 'latest.tmp'
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(state / 'latest.json')
        (state / 'last-run.json').write_text(json.dumps(dict(status='complete', fetched_at=now.isoformat(), snapshot=name)), encoding='utf-8')
        print(format_context(snapshot))
        return 0
    except Exception as exc:
        status = dict(status='failed', stage=stage, error_type=type(exc).__name__,
                      checked_at=datetime.now(TZ).isoformat())
        (state / 'last-run.json').write_text(json.dumps(status), encoding='utf-8')
        print(f'【富邦庫存同步失敗】階段={stage}，類型={type(exc).__name__}。不得用舊庫存或Notion股數冒充今日券商資料；本次停止部位賣出量計算，請通知使用者。')
        return 1
    finally:
        if sdk is not None:
            try:
                sdk.logout()
            except Exception:
                pass
            try:
                sdk.shutdown()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(run())
