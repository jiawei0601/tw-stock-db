# 富邦唯讀成交與庫存對帳

使用者確認富邦帳戶有其他持股，先分開對帳。現階段是本機券商快照，不是策略入帳或無人值守同步。

2026-09-08 真實查詢已完成且登出：1個證券帳戶、12筆庫存、23筆成交，查詢區間2026-08-10～2026-09-08。庫存日期9/8，12檔均有正餘額，其中4檔僅零股；最新成交日期9/2。個股及成交內容僅保存在 ignored 本機檔案，不加入 repo。報表能取得真實資料，仍需使用者核對 App 完整性及策略歸屬。22項相關離線測試通過。

## 操作

以 `C:\Users\chang\AppData\Local\FubonApiCheck\Scripts\pythonw.exe` 執行 repo 的 `fubon_readonly_gui.py --cert-path <新憑證路徑>`。密碼在 Tk 本機視窗輸入；登入一次後查詢所有 account_type=stock 的帳戶並登出。期貨帳戶跳過。SDK 日誌工作目錄在 `live_data/fubon/sdk-runtime/`，不要加入版本控制或傳 Telegram。

預設查詢含今日在內 30 個日曆日成交，日期可以修改但一次不得超過 30 天。每個帳戶查 inventories 與 filled_history；查詢節流 0.3 秒。最新狀態在 `live_data/fubon/latest-status.json`，每次結果另存唯一名稱 JSON／HTML，不覆蓋既有成功快照。狀態 complete 只代表兩個查詢回應成功，不是完整歷史、費稅、策略歸屬或公司行動都已核對。

## 策略隔離

使用者後續已確認首次快照12檔全為原有非策略持股，動能策略尚未建倉。分類結果另存新快照，原始快照保留；external-baseline.json 只記錄本次確認，未來新成交仍待分類，不能永久排除同代號股票。

- 所有新成交先標 unassigned（待分類），fees=null（未知）；不能補零或用估計費率假裝實際費稅。
- 其他持股不併入動能策略，不據此觸發任何策略賣出。
- 庫存顯示整股與零股各自的 today_qty／tradable_qty，未知值不是零。不能用可交易股數取代所有權股數。
- 不讀寫遠端帳本、不修改本機 account.json、不下單、不上傳憑證／密碼／完整帳戶庫存到 Hetzner。
- 未取得策略成交的明確歸屬、費稅及完整時間序列前，不產生可入帳檔。相同股票可能同時有策略與其他部位，不能只憑代號歸類。現有 live_momentum.positions 不支援同股多筆買入，部分成交及重新入帳需另定合約後處理。

## 待完成的整合

1. 完成真實唯讀查詢，核對 App／手動下單與零股成交是否涵蓋、帳戶與庫存日期是否符合預期。
2. 使用者指定策略成交歸屬；確認實際費稅來源，處理逐筆／整張委託分攤與日後費用修正。
3. 實作穩定成交 ID、重複／衝突檢測、分批成交及既有人工登帳對帳；只將確認的策略資料提交遠端帳本。
4. 無人值守登入需另確認安全憑證儲存與執行主機；目前沒有排程自動登入。Hetzner TG 現有策略繼續用人工成交回報。

## 驗證

`python -m pytest tests/test_fubon_readonly.py tests/test_fubon_readonly_gui.py tests/test_fubon_login_check.py -q`

合約及來源見 [唯讀同步合約](tasks/fubon-readonly-contract.md)。離線測試以 fake SDK 驗證，不會登入券商。
