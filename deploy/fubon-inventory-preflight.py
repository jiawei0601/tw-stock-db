"""Hermes日報前置腳本：執行失敗也將明確告警交給日報，禁止舊數據回退。"""
import subprocess

try:
    result = subprocess.run(
        ['/home/chang/fubon-monitor/.venv/bin/python', '/home/chang/fubon-monitor/fubon_daily_inventory.py'],
        cwd='/home/chang/fubon-monitor', capture_output=True, text=True, timeout=90)
    if result.returncode == 0 and result.stdout.startswith('【富邦券商庫存查詢成功】'):
        print(result.stdout)
    else:
        print('【富邦庫存同步失敗】今天未取得可驗證的券商庫存。請在日報開頭通知使用者，停止持倉數量、部位損益與賣出量計算；不得用Notion或前日庫存冒充今天數據。不要讀取憑證或秘密設定檔。')
except Exception:
    print('【富邦庫存同步失敗】查詢逾時或執行失敗。請在日報開頭通知使用者，不得將舊股數作為今日資料，不執行持股狀態更新。')
