# 工單：用 TWSE／TPEx 官方端點交叉驗證 FinMind 歷史資料並補洞（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。
**前提：`data/momentum_pit/status.json` 的 state 不再是 `downloading:*`（FinMind 下載已結束或暫停）才可開始；執行期間不得呼叫 FinMind。**

## 背景
`fetch_momentum_pit.py` 從 FinMind 抓 2,162 檔（含下市）2018-12 起的日行情、除權息結果、股利政策、減資參考價到 `data/momentum_pit/finmind_raw.db`（`responses` 表：query JSON、body、row_count）。契約 `docs/tasks/momentum-2020-pit.md` 要求「事件可追溯、空回應不等於證明沒有」。本工單用官方來源做兩件事：抽樣驗證 FinMind 沒抓錯，以及確認 FinMind 回空的項目到底是真空還是缺漏。**不重抓全量。**

## 可用的官方端點（repo 內已有的 collector 寫法優先重用：`collectors/_http.py`、`collectors/prices.py`、`collectors/institutional_official.py` 的重試與編碼處理）
- 上市個股月行情：`https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=XXXX`（一次一檔一個月，西元年）
- 上櫃個股月行情：`https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d=民國年/MM&stkno=XXXX`（民國年；若端點已改版，改用 `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=XXXX&date=YYYY/MM/DD&response=json`，以實測為準並記錄）
- 上市除權息結果：`https://www.twse.com.tw/exchangeReport/TWT49U?response=json&strDate=YYYYMMDD&endDate=YYYYMMDD`（區間）
- 上櫃除權息：`https://www.tpex.org.tw/web/stock/exright/dailyquo/exDailyQ_result.php?l=zh-tw&d=民國年/MM/DD&ed=民國年/MM/DD`（區間；同樣以實測為準）
- 上市減資參考價：`https://www.twse.com.tw/exchangeReport/TWTAUU?response=json&strDate=&endDate=`
- 節奏：每個 host 每 5 秒最多 3 次請求，遇 HTTP 4xx／5xx 等 30 秒重試一次，再失敗記錄後跳過；**全程請求總數控制在 600 次以內**。

## 任務
### 1. 行情抽樣驗證（約 120 次請求）
- 從 finmind_raw.db 的 TaiwanStockPrice 成功回應中隨機抽 40 檔（固定 seed=20260908；上市 25、上櫃 15；至少 5 檔為下市股）、每檔隨機抽一個 2019–2025 的月份。
- 用官方端點抓該月，比對每日 收盤／成交量／開高低：完全一致、僅小數位差、不一致（列出）。輸出 `data/momentum_pit/crosscheck_prices.csv`（stock_id, market, ym, days_finmind, days_official, close_mismatch, volume_mismatch, note）。
- 通過標準：收盤價不一致的日數 ≤ 0.5%；否則在報告標紅並列前 20 筆。

### 2. 除權息抽樣驗證（約 30 次請求）
- 從 TaiwanStockDividendResult 成功回應中隨機抽 25 個事件（含現金股利與股票股利各至少 8 個），用 TWT49U／櫃買區間端點（以事件日前後 3 天為區間）比對：除息日、現金股利、股票股利、除權息參考價。輸出 `crosscheck_dividends.csv`。

### 3. FinMind 空回應補洞（約 350 次請求）
- 從 status.json／responses 找出 `TaiwanStockPrice` 回空的 92 檔：用官方端點各抓 2020-06 與 2023-06 兩個月（下市股改抓下市前一年的一個月，下市日查 `TaiwanStockDelisting` 回應）。判定三類：`official_has_data`（FinMind 缺漏，寫入 `pit_gap_prices.csv` 並把官方資料存成 `data/momentum_pit/official_fill/prices_<stock>.json`）、`no_data_both`（可能非普通股或期間未上市）、`endpoint_error`。
- 除權息回空的 286 檔：用區間端點一次拉 2019-01-01 至 2026-09-07 的**全市場**除權息表（上市按月分 92 次區間、上櫃同樣按月），本地過濾這 170 檔，判定 `official_has_events`／`no_events_both`。把全市場表存成 `data/momentum_pit/official_fill/exright_twse.csv` 與 `exright_tpex.csv`（這兩張表本身就是第二來源，之後可整批比對）。
- 股利政策與減資回空的檔數若也大量（看 status.json），只報數量與抽 10 檔查證，不逐檔補。

### 4. 產出
- `backtest/twse_crosscheck.py`（CLI `--part 1|2|3|all`，可續跑，請求紀錄寫 `data/momentum_pit/official_requests.log`）
- `analysis/twse-crosscheck-2026-09-08.md`：抽樣結果表、不一致清單、補洞判定統計（三類各幾檔）、對契約驗收條件 1 與 2 的影響判斷、總請求數與遇到的端點變更。
- `tests/test_twse_crosscheck.py`：民國年轉換、比對函式對小數位差的容忍、區間切月不重疊。全綠。
- `docs/tasks/reports/twse-crosscheck-report.md`：做了什麼、不確定處。

## 禁止
- 不呼叫 FinMind；不寫 `finmind_raw.db`、`tw_stocks.db`；官方資料只寫 `data/momentum_pit/official_fill/` 與 CSV。
- 不動 `fetch_momentum_pit.py`、既有測試、HANDOFF.md。不 git commit / push。
- 總請求數超過 600 或連續 5 次被拒（403／429）即停止並回報，不要換 IP 或降低間隔硬衝。
