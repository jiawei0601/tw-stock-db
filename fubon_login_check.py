"""本機互動登入檢查；密碼只在記憶體，不下單、不把帳號或密碼寫進結果。"""
import json
import threading
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

ROOT=Path(__file__).resolve().parent
RESULT=ROOT/'live_data'/'fubon-login-check.json'


def verify_pfx(path,password):
    from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
    try:
        key,cert,_=load_key_and_certificates(Path(path).read_bytes(),password.encode() if password else None)
        if key is None or cert is None:raise ValueError('no_private_key')
        return dict(reader='python',not_before=cert.not_valid_before_utc.isoformat(),not_after=cert.not_valid_after_utc.isoformat())
    except Exception:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(ROOT/'deploy'/'check_fubon_pfx.ps1')],
                              input=json.dumps(dict(path=str(path),password=password),ensure_ascii=True),text=True,capture_output=True,
                              timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        native=json.loads(result.stdout)
        if result.returncode or not native.get('ok') or not native.get('has_private_key'):
            raise ValueError('native_pfx_failure:'+str(native.get('hresult','no_private_key'))) from None
        return dict(reader='windows',not_before=native['not_before'],not_after=native['not_after'])


def write_result(result):
    RESULT.parent.mkdir(exist_ok=True)
    temp=RESULT.with_suffix('.tmp')
    temp.write_text(json.dumps({**result,'checked_at':datetime.now(timezone.utc).isoformat()},ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(RESULT)


def main():
    root=tk.Tk();root.title('富邦API｜本機憑證與登入驗證');root.geometry('670x420')
    box=ttk.Frame(root,padding=20);box.pack(fill='both',expand=True)
    ttk.Label(box,text='密碼只用於本次檢查，不儲存、不傳到Hetzner、不下單。',wraplength=620).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,15))
    certs=list(Path('C:/CAFubon').rglob('*.pfx'))
    cert_path=tk.StringVar(value=str(certs[0]) if len(certs)==1 else '')
    identity=tk.StringVar(value=certs[0].stem if len(certs)==1 else '')
    login_password=tk.StringVar();cert_password=tk.StringVar()
    for i,(label,var,mask) in enumerate([('登入身分識別碼',identity,True),('富邦電子交易密碼',login_password,True),('憑證PFX路徑',cert_path,False),('憑證密碼',cert_password,True)],1):
        ttk.Label(box,text=label).grid(row=i,column=0,sticky='w',pady=6)
        ttk.Entry(box,textvariable=var,show='*' if mask else '',width=52).grid(row=i,column=1,sticky='ew',pady=6)
    ttk.Button(box,text='選檔',command=lambda:cert_path.set(filedialog.askopenfilename(filetypes=[('PFX certificate','*.pfx *.p12')]) or cert_path.get())).grid(row=3,column=2,padx=6)
    status=tk.StringVar(value='請確認識別碼與憑證路徑，輸入密碼後按驗證。不要把密碼貼到聊天。')
    ttk.Label(box,textvariable=status,wraplength=610).grid(row=6,column=0,columnspan=3,sticky='w',pady=16)
    state={'running':False,'attempted':False}

    def finish(result,message):
        write_result(result);state['running']=False;status.set(message)
        login_password.set('');cert_password.set('')
        if not state['attempted']:button.configure(state='normal')

    def verify():
        if state['running'] or state['attempted']:return
        user=identity.get().strip();pwd=login_password.get();cpwd=cert_password.get();path=cert_path.get()
        if not user or not pwd or not path:
            status.set('請填寫登入識別碼、電子交易密碼及憑證路徑。');return
        state['running']=True;button.configure(state='disabled');status.set('正在檢查PFX並嘗試登入；不會送出交易委託。')
        login_password.set('');cert_password.set('')
        write_result(dict(status='running',login_success=False))

        def work():
            result={'login_success':False};sdk=None
            try:
                certificate=verify_pfx(path,cpwd)
                start=datetime.fromisoformat(certificate['not_before'].replace('Z','+00:00'));end=datetime.fromisoformat(certificate['not_after'].replace('Z','+00:00'))
                valid=start<=datetime.now(timezone.utc)<end
                result.update(cert_not_before=start.isoformat(),cert_not_after=end.isoformat(),cert_time_valid=valid,pfx_decrypted=True,certificate_reader=certificate['reader'])
                if not valid:
                    root.after(0,finish,{**result,'status':'certificate_out_of_date'},f'憑證不在有效期內：{start:%Y-%m-%d}～{end:%Y-%m-%d}。未嘗試券商登入。');return
                from fubon_neo.sdk import FubonSDK
                sdk=FubonSDK();state['attempted']=True
                reply=sdk.login(user,pwd,path,cpwd)
                result['login_success']=bool(reply.is_success)
                if reply.is_success:
                    result.update(status='login_success',accounts_returned=len(reply.data or []))
                    message=f'登入成功！PFX可解密、有效至{end:%Y-%m-%d}，券商已接受本次登入。已取得{len(reply.data or [])}個帳戶資訊；登出中。未下單、未同步成交。'
                else:
                    result['status']='broker_rejected'
                    message='PFX有效，但券商拒絕登入。券商訊息（僅顯示於本機）：'+str(reply.message)+'。本次不自動重試。'
                try:sdk.logout();result['logout_completed']=True
                except Exception:result['logout_completed']=False
                root.after(0,finish,result,message.replace('登出中','已結束本次驗證'))
            except Exception as exc:
                if sdk:
                    try:sdk.logout()
                    except Exception:pass
                category='login_exception' if state['attempted'] else 'pfx_unreadable'
                result.update(status=category,error_type=type(exc).__name__)
                native_code=str(exc).split(':')[-1] if str(exc).startswith('native_pfx_failure:') else 'reader_error'
                if not state['attempted']:result['native_code']=native_code
                root.after(0,finish,result,'檢查未完成：'+('登入連線或SDK發生錯誤，本次不自動重試。' if state['attempted'] else f'Python與Windows均未能讀取PFX（代碼{native_code}）。尚未登入券商，請確認憑證密碼／檔案；憑證密碼未必等於電子交易密碼。'))
        threading.Thread(target=work,daemon=True).start()

    button=ttk.Button(box,text='驗證憑證並登入一次',command=verify);button.grid(row=5,column=1,sticky='w',pady=12)
    ttk.Button(box,text='關閉',command=root.destroy).grid(row=7,column=1,sticky='w')
    write_result(dict(status='awaiting_local_input',login_success=False))
    root.mainloop()


if __name__=='__main__':main()
