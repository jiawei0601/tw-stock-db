"""本機遮蔽輸入，透過既有SSH安全傳遞Hetzner自動查詢設定。"""
import argparse
import base64
import json
import queue
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog

from fubon_login_check import verify_pfx

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / 'live_data' / 'fubon' / 'remote-setup-status.json'
SSH = ['ssh', '-i', str(Path.home() / '.ssh' / 'hetzner_civilrag'),
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'chang@94.130.24.11']


def setup(user, api_key, cert_path, cert_password):
    verify_pfx(cert_path, cert_password)
    payload = dict(personal_id=user, api_key=api_key, cert_password=cert_password,
                   certificate_base64=base64.b64encode(Path(cert_path).read_bytes()).decode('ascii'))
    result = subprocess.run(SSH + ['python3 /home/chang/fubon-monitor/fubon_install_credentials.py'],
                            input=json.dumps(payload), text=True, capture_output=True, timeout=40,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    payload.clear()
    if result.returncode or result.stdout.strip() != 'credentials_installed':
        raise ValueError('remote_install_failed')
    result = subprocess.run(SSH + ['/home/chang/fubon-monitor/.venv/bin/python /home/chang/fubon-monitor/fubon_daily_inventory.py'],
                            text=True, capture_output=True, timeout=110,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not result.stdout.startswith('【富邦券商庫存查詢成功】'):
        return dict(status='remote_validation_failed', credentials_installed=True)
    return dict(status='remote_validation_success', credentials_installed=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cert-path', required=True)
    args = parser.parse_args()
    root = tk.Tk()
    root.title('富邦｜Hetzner每日庫存設定')
    root.geometry('830x460')
    box = ttk.Frame(root, padding=20)
    box.pack(fill='both', expand=True)
    ttk.Label(box, text='設定將經SSH傳到你的 Hetzner（94.130.24.11），供平日14:00庫存監控。\n伺服器保存 API Key、登入ID、憑證及憑證密碼，僅帳戶擁有者可讀（0600）。\n請使用富邦「查詢帳務」權限的專用Key，不開下單權限；IP白名單設 94.130.24.11。\n不需要電子交易密碼。不在本機、對話或TG保存密碼／Key。', wraplength=780).grid(row=0, column=0, columnspan=3, sticky='w', pady=10)
    match = re.match(r'^([A-Z][12][0-9]{8})', Path(args.cert_path).name)
    user = tk.StringVar(value=match.group(1) if match else '')
    key = tk.StringVar()
    cert = tk.StringVar(value=args.cert_path)
    password = tk.StringVar()
    for index, (label, var, masked) in enumerate([
        ('登入ID', user, True), ('API Key（Secret）', key, True),
        ('新憑證p12', cert, False), ('憑證密碼', password, True)], 1):
        ttk.Label(box, text=label).grid(row=index, column=0, sticky='w', pady=8)
        ttk.Entry(box, textvariable=var, width=66, show='*' if masked else '').grid(row=index, column=1)
    ttk.Button(box, text='選檔', command=lambda: cert.set(filedialog.askopenfilename(filetypes=[('憑證', '*.p12 *.pfx')]) or cert.get())).grid(row=3, column=2, padx=4)
    status = tk.StringVar(value='請填寫後按下方按鈕，確認將設定保存於指定Hetzner主機並驗證。')
    ttk.Label(box, textvariable=status, wraplength=780).grid(row=6, column=0, columnspan=3, sticky='w', pady=15)
    mailbox = queue.Queue()

    def poll():
        try:
            result = mailbox.get_nowait()
        except queue.Empty:
            root.after(200, poll)
            return
        STATUS.write_text(json.dumps(result), encoding='utf-8')
        if result['status'] == 'remote_validation_success':
            status.set('Hetzner已成功使用API Key取得今日券商庫存並登出。請回到對話，接著完成既有日報串接。')
        else:
            status.set('設定或驗證未完成：' + result['status'] + '。請回到對話，不自動重試。')

    def submit():
        if not user.get().strip() or not key.get().strip() or not Path(cert.get()).is_file():
            status.set('請輸入登入ID、API Key並選擇存在的憑證。')
            return
        values = user.get().strip(), key.get().strip(), cert.get(), password.get()
        key.set('')
        password.set('')
        button.configure(state='disabled')
        status.set('正在透過SSH安全傳遞並從Hetzner唯讀驗證，請稍候。')
        STATUS.write_text('{"status":"running"}', encoding='utf-8')
        def work():
            try:
                mailbox.put(setup(*values))
            except Exception as exc:
                mailbox.put(dict(status='setup_failed', error_type=type(exc).__name__))
        threading.Thread(target=work, daemon=True).start()
        root.after(200, poll)
    button = ttk.Button(box, text='保存到Hetzner並唯讀驗證一次', command=submit)
    button.grid(row=5, column=1, sticky='w', pady=12)
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text('{"status":"awaiting_local_input"}', encoding='utf-8')
    root.mainloop()


if __name__ == '__main__':
    main()
