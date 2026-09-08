# Hetzner每日富邦庫存串接

## 目標與狀態

使用者授權把券商庫存接入 Hetzner 每日持倉監控。預定更新既有台股日報 `ad2b97bcefc8`（平日14:00，Asia/Taipei），不新增重複排程、不改美股核心倉或獨立50萬元動能30策略。

目前環境與程式已部署，尚未啟用日報串接：API Key 驗證被富邦拒絕，回應「Login Error, API認證,APIKEY尚未申請」。已停止重試，等待使用者確認輸入的是完整 Secret Key／重新取得有效Key。不得把已保存設定當登入成功。

## 遠端環境

- `/home/chang/fubon-monitor/.venv`：獨立 Python3.12.13、Fubon Neo2.3.0，官方 Linux wheel，SDK載入成功。系統Python3.14未變更。
- `fubon_daily_inventory.py` 與 `fubon_readonly.py`：只呼叫 apikey_login、accounting.inventories、logout/shutdown。
- `private/` 0700，credentials.json、certificate.p12 0600；設定經本機 `fubon_remote_setup.py` 遮蔽輸入，再由SSH stdin傳給 `fubon_install_credentials.py`。不記錄完整路徑、ID、Key或密碼到repo；秘密以伺服器檔案權限保護，未宣稱硬體加密或API端已驗證唯讀權限。
- `state/last-run.json`：無秘密的執行狀態；成功快照latest.json與日期檔；SDK日誌也在此私密目錄。失敗不覆寫成功快照、不回退至舊庫存。
- Hermes前置腳本：`/home/chang/.hermes/scripts/fubon-inventory-preflight.py`，90秒逾時，失敗輸出固定告警供日報，不印SDK錯誤或秘密。

## 啟用流程

1. 使用者於本機視窗確認保存APIKey及憑證到Hetzner；建議Key限所需查詢權限，IP白名單94.130.24.11。不需要保存電子交易密碼。
2. 從Hetzner成功查當日庫存（含整股/零股），核對12檔首次已知基準或合理變動，確認登出。
3. 成功後20分鐘內執行 `python3 /home/chang/fubon-monitor/fubon_attach_daily.py`。程式先備份原job，再透過Hermes CLI edit加上script，保留原排程/目的地/其他屬性。
4. 檢查既有job的script、prompt及schedule；用前置腳本驗證輸出，必要時手動跑該job驗證TG交付，不多建自動化。

## 監控行為

- 當日券商股數為數量來源，整股＋零股；Notion保存監控規則／成本／建倉日／MA已执行紀錄。
- 不唯一對應、數量不符或缺成本日期，列對帳差異，不猜精確損益或賣出股數。券商新增部位列待分類，其他長期持有列摘要，不自動套動能30策略。
- 不因停損/停利訊號就把Notion改為已出清或MA已賣；只報待執行。這修正舊job把訊號當成交的行為。
- 庫存拒絕／缺欄／非今日／逾時時，停止此次部位計算並通知；不把失敗當空倉。券商成功回空列表才是空庫存回應。
- 舊日報備份Notion流程保留；不自動改Notion持倉股數或策略帳本。

## 驗證

37項相關離線測試通過（含14項每日庫存fakeSDK測試）。Linux2.3.0 SDK可載入。真實APIKey驗證未通過，所以日報未接線。

來源：[API Key登入](https://www.fbs.com.tw/TradeAPI/docs/trading/library/python/login/loginAPIKey/)、[SDK下載](https://www.fbs.com.tw/TradeAPI/docs/download/download-sdk/)。
