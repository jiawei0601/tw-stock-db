# Hetzner每日富邦庫存合約

使用者授權 Hetzner 每日持倉監控自動抓券商庫存。既有14:00台股日報 ad2b97bcefc8 插入前置腳本；保留其他排程（美股、50萬動能30策略）。券商提供當天數量，Notion只供監控規則、成本、日期與已執行動作；差異/新持股需人工核對，不把訊號當成交寫入Notion。

## 分工

主代理已實作可執行核心 `fubon_daily_inventory.py`；負責部署、設定GUI、Hermes更新。子代理僅擁有 `tests/test_fubon_daily_inventory.py`，以fakeSDK驗證合約並訊息回報核心bug，不改其他檔或commit。指定Spark不可用，使用Sol。

## API

- normalize_inventory(rows:list SDK Inventory, today:date)->list[dict]：保留inventory欄及odd；日期需今日YYYY/MM/DD或ISO；odd必須存在；today_qty與odd.today_qty必須非負int（不接受bool），total_shares=兩者相加。
- query_inventory(sdk, accounts, today:date, pause=time.sleep)->list[dict]：只stock，逐次0.3秒，呼叫accounting.inventories。成功data=[]是空庫存；拒絕/None/日期舊/無stock raises ValueError；任何一個帳戶失敗不得當全成功。回傳每帳戶account_label（序號代稱）+ holdings。
- format_context(snapshot)->str：日期與持股JSON列（僅代號、order_type、shares、regular_shares、odd_shares），不含身分/券商帳號；正餘額顯示，零股納入。說明與50萬策略隔離。
- run(directory:Path)->int：directory/private/credentials.json 必須只含 personal_id,api_key,cert_password；certificate.p12另檔。Linux須0600。apikey_login→inventories→logout/shutdown，成功保存state/日期.json與latest.json，印資料context回傳0；失敗last-run.json回傳1且不覆寫latest成功資料，印固定告警不印例外文字。關閉連線finally。無下單呼叫。

## 測試

正確零股總數、bool/負數/缺odd/舊日期拒絕；空回應區分拒絕；多帳戶隔離/跳futopt；format欄位不含帳號、零部位不當持有；假的run測試login或query失敗登出/錯誤不可泄漏秘密、舊latest保持；成功APIkey登入且不存在下單呼叫。以tmp_path跑不讀真credentials。報告測試結果，若核心有邏輯不足回報主代理。
