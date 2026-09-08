# 富邦唯讀同步合約（2026-09-08）

使用者確認帳戶另有非策略持股，先分開對帳。第一階段只蒐集本機券商快照並產生 HTML，不能改策略 account.json、推定持股歸屬、下單或傳全部持股到 Hetzner。實際登入一次、查完登出；密碼僅本機記憶體。現有 50 萬策略現金與帳本保持隔離。

## 可執行核心與分工

- 主代理：`fubon_readonly.py` 已可在注入 SDK 後執行 `collect_snapshot`、`save_snapshot`；負責登入 GUI 與整合。
- 子代理：僅負責 `fubon_readonly_report.py`、`tests/test_fubon_readonly.py`。不要改核心、GUI、其他 agent 工作，不 commit。
- `collect_snapshot(sdk, accounts, start: date, end: date, pause=time.sleep) -> dict`：只處理 account_type=stock，最多 30 個日曆日、禁止未來日。只呼叫 accounting.inventories(account)、stock.filled_history(account, YYYYMMDD, YYYYMMDD)，每次查詢前節流 0.3 秒。失敗為 partial；無證券帳戶為 no_stock_accounts。錯誤僅類型，不存 broker message。
- `save_snapshot(snapshot: dict, directory: Path=DATA) -> Path`：UUID 檔名 JSON；不覆蓋前次資料。
- `render_report(snapshot: dict) -> str`：子代理實作獨立 HTML（無 CDN、無網路、無操作帳本按鈕），HTML escaping 全外部字串。繁中、清楚標題日期／查詢範圍／完整性狀態／整股與零股分列／成交表。待分類、費稅未知、不是策略持股結論、庫存缺欄為未知不能當零。提供簡易純前端表格搜尋佳，但不必要。

## Schema v1

root: schema_version=1, fetched_at UTC ISO, start/end ISO日期, status complete|partial|no_stock_accounts, strategy_imported=false, accounts list, errors list。
account: account_ref 穩定 SHA256分公司+帳號前16hex（去識別代碼仍視為本機私密資料）, status complete|partial, inventories list, fills list。
inventory: date,stock_no,order_type,lastday_qty,today_qty,tradable_qty,buy_filled_qty,sell_filled_qty; odd 同樣數量欄位（無date/symbol/type），缺 odd = null。
fill: date,order_no,stock_no,buy_sell,filled_no,filled_avg_price,filled_qty,filled_price,order_type,filled_time; fees=null,assignment=unassigned。SDK欄位未知為null。不將部分成交轉成現有策略帳本；現有帳本不支援同股加碼，仍需後續明確成交歸屬/分批合併與費稅對帳契約。

## 驗收／邊界案例

使用 fake SDK，不登入真券商：兩帳戶含stock/futopt只查stock；兩個stock不得混合。整股+odd保存；空list成功與None失敗區別。一查詢拒絕或例外保留另一查詢，partial明示。私密name/account/branch_no/token不出現在snapshot/report。任何下單/改單方法都不應呼叫。30天可、31天/逆序/未來不可。fee不能補0。存檔不覆蓋、account.json不變。報表外部惡意HTML escaping；空值為未知；partial不是成功。

## 官方證據

- https://www.fbs.com.tw/TradeAPI/docs/trading/library/python/trade/FilledHistory/ ：最大30天、逐筆欄位未含費稅。
- https://www.fbs.com.tw/TradeAPI/docs/trading/guide/account_example/ ：inventories整股及odd巢狀股數。
- https://www.fbs.com.tw/TradeAPI/docs/trading/library/python/EnumMatrix/ ：account_type=stock/futopt。

後續狀態：先等待唯讀真實結果，再確認使用者指定的策略成交；逐筆費稅與成交範圍不能自動推定。未配置 unattended 認證或遠端券商同步。
