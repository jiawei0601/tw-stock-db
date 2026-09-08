# 富邦API本機安裝查核

2026-09-08，Codex。使用者要求檢查是否安裝，若無才下載安裝。

## 結果

- Python 3.10.11：`C:\Users\chang\AppData\Local\Programs\Python\Python310\python.exe` 已安裝 `fubon_neo 2.2.6`；`from fubon_neo.sdk import FubonSDK` 載入成功。
- Anaconda Python 3.13.9：`C:\ProgramData\anaconda3\python.exe` 同樣安裝2.2.6，SDK載入成功。
- 預設命令 `python` 指向本機Hermes虛擬環境，該環境沒有SDK；Python3.12亦無SDK。不能因此推論整台未安裝。
- `C:\CAFubon`下找到一份 `.pfx`，未顯示內容、未複製上傳、未驗證有效期或密碼，也未登入券商。個人子目錄及憑證檔名不寫入repo。
- 2.2.6可用傳統login，但尚無新版API Key登入支援（官方要求>=2.2.7）。本次沒有更新／覆蓋現有SDK。

官方2.3.0 Windows安裝包在並行檢查時已下載至 `C:\Users\chang\Downloads\FubonNeo-2.3.0\`，並擷取whl，尚未安裝。zip SHA256：`7afeb4e028e185b62f431039c5cf08c4adaba06f448bc41e27c4bd54dbbfeabd`；此為下載檔案的本地摘要，不是供應商簽章驗證。

來源：https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk/

## 下一步

僅確認SDK可載入，不代表API帳戶權限、憑證有效性、登入或成交同步已通過。需要串接時應使用独立環境；若採新版API Key登入，使用官方新版SDK。不要在聊天、git或log記錄密碼、金鑰、身分識別碼及憑證內容。Hetzner現有策略仍未串接券商，成交仍須回報。

## 本機登入與PFX驗證工具

使用者已授權設定登入及檢查有效性。`fubon_login_check.py`為Tk本機遮蔽輸入視窗，使用已安裝2.2.6的Python3.10；啟動：

```powershell
py -3.10 fubon_login_check.py
```

- 憑證與密碼不傳Hetzner、不寫git或狀態檔；密碼只留程序記憶體。登入只做一次，不下單、完成後登出。
- 結果寫入被git忽略的 `live_data/fubon-login-check.json`，只含驗證日期、狀態、憑證有效期與帳戶數量等，不含身分／帳號或密碼。
- Windows CurrentUser憑證庫找到相符身分的多份憑證，其中有效期2026-01-07～2027-01-07；這不等於目前PFX已解密、未撤銷或可登入。
- 第一次使用者輸入後，Python PFX讀取回ValueError，尚未呼叫券商登入。不能直接斷定密碼錯或憑證過期。
- 已增加Windows原生讀取備援 `deploy/check_fubon_pfx.ps1`：密碼經stdin匿名管道傳遞、暫存金鑰採EphemeralKeySet，不出現在命令列。兩種讀取都失敗才提示原生HRESULT；保留缺檔／損毀／密碼與格式等可能性。
- 103項相關測試通過，含人工產生加密PFX的原生讀取、備援與密碼不洩漏測試。本輪尚待使用者於新版視窗輸入，未證實原PFX問題已解決，未證實券商登入成功。
