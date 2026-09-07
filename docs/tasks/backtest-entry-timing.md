# 工單：D5 動能策略的進場價格測試（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。

## 背景
D5 策略（`backtest_valuation.py` 的 D5 層：相對動能 rank ≥ 0.75、非分割、無背離、獲利穩定、近 3 月營收年增 > 0）目前假設「訊號日（月底）收盤價買進、持有 3 個月」。本工單測三種進場價格法，並同時回報**成交率**與**成交部位的報酬**。參考 `backtest/stop_rule_test.py`（已驗證與 signals.csv 的 ret_3m 對齊方式：3 個月 = 訊號月月底往後三個月的月底交易日，不是日曆 90 天）。

## 資料（唯讀）
- `backtest/signals.csv`：signal_date、stock_id、D5、rel_mom_rank、ret_3m、bench_3m
- `data/tw_stocks.db`：`fm_price_daily(stock_id,date,close)`；只有收盤價，沒有高低價與成交量
- 不打 FinMind、不寫 DB。

## 部位集合
每個 D5==True 且 ret_3m 非空的訊號月（約 40 個），兩個集合分別跑：(a) rank 前 5 檔；(b) 全部 D5 檔。

## 進場法（出場一律固定在原 3 個月到期日的收盤，讓比較只差在進場）
- **E0 基準**：訊號日收盤買。
- **E1 等拉回**：訊號日後第 1 到第 5 個交易日內，任一日收盤 < 該日 5 日均線（含當日的近 5 日收盤均）→ 當日收盤買；5 天內沒發生 → 該檔不成交。
- **E2 分批**：訊號日後第 1、5、10 個交易日各買三分之一，成本 = 三次收盤平均；持有到到期日。
- **E3 突破確認**：訊號日後 20 個交易日內，任一日收盤創「含當日的近 20 日收盤」新高 → 當日收盤買；沒發生 → 不成交。
- **E1b／E3b 變體**：等待窗口放寬到 10 與 30 個交易日。

## 每種進場法要報
- 成交率（成交檔數 / 應買檔數）與平均進場延遲天數。
- **成交部位**：平均報酬、中位、勝率、對 bench_3m 的超額中位（基準用 E0 同期 bench，不重算）。
- **含未成交的組合報酬**：未成交視為 0%（現金）；這是真實可執行的數字。
- 與 E0 的逐月配對比較：規則版減 E0 的差，平均、中位、贏的月份比例；差異最大的正負各 3 個月。
- 進場價相對 E0 的平均折價／溢價（%），確認「等拉回」到底買得有沒有比較便宜、「突破確認」貴了多少。

## 產出
- `backtest/entry_timing_test.py`（CLI `--out backtest/`）、`backtest/entry_timing_summary.md`、`backtest/entry_timing_detail.csv`（每檔每法的進場日、進場價、是否成交、報酬）。
- `tests/test_entry_timing.py`：(a) E1 的 5 日均只用 ≤ 當日資料；(b) 5 天內未跌破 → 不成交；(c) E2 成本為三次平均；(d) E3 新高判定含當日。全綠。
- `docs/tasks/reports/backtest-entry-timing-report.md`：兩張比較表（前 5 檔、全 D5）、三句話結論：哪種進場法「含未成交」的組合報酬最好、成交率代價、拉回或突破買到的價格差多少。

## 禁止
- 不動 `backtest_valuation.py`、`backtest_t1.py`、既有測試、HANDOFF.md、README.md。
- 不 git commit / push。
