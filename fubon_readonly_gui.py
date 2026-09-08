"""一次性本機唯讀登入；憑證與交易密碼不持久化。"""
import argparse
import json
import os
import queue
import re
import threading
import tkinter as tk
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, ttk
from uuid import uuid4

from fubon_login_check import verify_pfx
from fubon_readonly import DATA, collect_snapshot, save_snapshot
from fubon_readonly_report import render_report

TAIPEI = timezone(timedelta(hours=8))


def run_readonly(user, password, certificate_path, certificate_password, start, end):
    """只回傳本機快照路徑與摘要；拒絕登入後不重試。"""
    certificate = verify_pfx(certificate_path, certificate_password)
    now = datetime.now(timezone.utc)
    if not (datetime.fromisoformat(certificate['not_before'].replace('Z', '+00:00')) <= now
            < datetime.fromisoformat(certificate['not_after'].replace('Z', '+00:00'))):
        raise ValueError('certificate_out_of_date')
    from fubon_neo.sdk import FubonSDK
    sdk = FubonSDK()
    snapshot = None
    logout_ok = False
    try:
        reply = sdk.login(user, password, certificate_path, certificate_password)
        if not reply.is_success:
            raise ValueError('broker_login_rejected')
        snapshot = collect_snapshot(sdk, reply.data or [], start, end)
    finally:
        try:
            sdk.logout()
            logout_ok = True
        except Exception:
            logout_ok = False
        try:
            sdk.shutdown()
        except Exception:
            pass
    snapshot['logout_completed'] = logout_ok
    path = save_snapshot(snapshot)
    report = path.with_suffix('.html')
    report.write_text(render_report(snapshot), encoding='utf-8')
    return dict(status=snapshot['status'], logout_completed=logout_ok,
                accounts=len(snapshot['accounts']),
                inventories=sum(len(a['inventories']) for a in snapshot['accounts']),
                fills=sum(len(a['fills']) for a in snapshot['accounts']),
                snapshot_path=str(path), report_path=str(report))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cert-path', default='')
    args = parser.parse_args()
    # SDK 原生日誌可能含帳戶資料；工作目錄置於 git 忽略的本機私密資料夾。
    runtime = DATA / 'sdk-runtime' / uuid4().hex
    runtime.mkdir(parents=True, exist_ok=True)
    os.chdir(runtime)
    root = tk.Tk()
    root.title('富邦｜唯讀成交與庫存對帳')
    root.geometry('800x540')
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='查詢本機證券帳戶的成交與庫存，與動能策略分開對帳。\n不下單、不自動入策略帳本；密碼不儲存，查詢後登出。',
              wraplength=730).grid(row=0, column=0, columnspan=3, sticky='w', pady=10)
    candidate = re.match(r'^([A-Z][12][0-9]{8})', Path(args.cert_path).name)
    user = tk.StringVar(value=candidate.group(1) if candidate else '')
    password = tk.StringVar()
    cert = tk.StringVar(value=args.cert_path)
    cert_password = tk.StringVar()
    today = datetime.now(TAIPEI).date()
    start = tk.StringVar(value=(today - timedelta(days=29)).isoformat())
    end = tk.StringVar(value=today.isoformat())
    rows = [('登入識別碼', user, True), ('電子交易密碼', password, True),
            ('新憑證 p12／pfx', cert, False), ('憑證密碼', cert_password, True),
            ('成交起日 YYYY-MM-DD', start, False), ('成交迄日 YYYY-MM-DD', end, False)]
    for index, (label, variable, masked) in enumerate(rows, 1):
        ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', pady=6)
        ttk.Entry(frame, textvariable=variable, show='*' if masked else '', width=60).grid(row=index, column=1, sticky='ew')
    ttk.Button(frame, text='選檔', command=lambda: cert.set(filedialog.askopenfilename(
        filetypes=[('憑證', '*.p12 *.pfx')]) or cert.get())).grid(row=3, column=2, padx=5)
    status = tk.StringVar(value='新憑證路徑已預填；請輸入密碼，查詢最近30個日曆日成交及目前庫存。')
    ttk.Label(frame, textvariable=status, wraplength=730).grid(row=8, column=0, columnspan=3, sticky='w', pady=14)
    results = queue.Queue()

    def finish_poll():
        try:
            result = results.get_nowait()
        except queue.Empty:
            root.after(200, finish_poll)
            return
        (DATA / 'latest-status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if result.get('report_path'):
            label = '查詢完成' if result['status'] == 'complete' else '查詢不完整，請查看報告'
            status.set(f"{label}：{result['accounts']} 個證券帳戶、{result['inventories']} 筆庫存、{result['fills']} 筆成交。\n"
                       + ('已登出。' if result['logout_completed'] else '未確認登出完成，請關閉本程式。')
                       + '\n報表已儲存在本機；所有成交尚待分類及費稅確認，未入策略帳本。')
        else:
            status.set('查詢未完成：' + result['error_type'] + '。未重試、未入策略帳本；請回報此畫面。')

    def execute():
        try:
            begin = date.fromisoformat(start.get())
            finish = date.fromisoformat(end.get())
            if begin > finish or (finish - begin).days >= 30 or finish > datetime.now(TAIPEI).date():
                raise ValueError()
        except ValueError:
            status.set('日期需有效、最多30個日曆日且不可在未來。')
            return
        if not user.get().strip() or not password.get() or not Path(cert.get()).is_file():
            status.set('請填入登入識別碼、電子交易密碼，並選擇存在的憑證檔案。')
            return
        values = (user.get().strip(), password.get(), cert.get(), cert_password.get(), begin, finish)
        password.set('')
        cert_password.set('')
        button.configure(state='disabled')
        status.set('正在驗證並進行唯讀查詢，請等候完成。')
        (DATA / 'latest-status.json').write_text('{"status":"running"}', encoding='utf-8')

        def work():
            try:
                results.put(run_readonly(*values))
            except Exception as exc:
                # 不將券商例外文字／帳號／密碼記錄到狀態。
                results.put(dict(status='failed', error_type=type(exc).__name__))
        threading.Thread(target=work, daemon=True).start()
        root.after(200, finish_poll)

    button = ttk.Button(frame, text='登入並唯讀查詢一次', command=execute)
    button.grid(row=7, column=1, sticky='w', pady=12)
    ttk.Button(frame, text='關閉', command=root.destroy).grid(row=9, column=1, sticky='w')
    (DATA / 'latest-status.json').write_text(
        json.dumps(dict(status='awaiting_local_input', opened_at=datetime.now(timezone.utc).isoformat())),
        encoding='utf-8')
    root.mainloop()


if __name__ == '__main__':
    main()
