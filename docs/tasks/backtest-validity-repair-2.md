# 工單：回測研究有效性修復（第二輪）（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。
第一輪修復（`docs/tasks/backtest-validity-repair.md`，產出 `backtest_validity.py` 等，已以「草稿」commit）經兩位審查者判定 request_changes；審查原文：`docs/tasks/reports/codex-review-2.md` 第一節、以及 sonnet 審查摘要（見本工單末段）。本輪只修審查列出的問題，**不新增策略、不調門檻**。

## 0. 先抓獨立事件證據（本工單唯一准打 FinMind 的段落；token 讀 `.env`，每請求 sleep 0.5 秒，402/403 即停並回報）
在 `build_valuation.py` 加兩個子命令，皆 idempotent、可續跑：
- `--fetch-events`：抓**全市場**（不帶 data_id，各一次請求，start_date=2020-01-01）：`TaiwanStockSplitPrice`（分割／面額變更／反分割：before_price、after_price、type）、`TaiwanStockCapitalReductionReferencePrice`（減資恢復買賣參考價）、`TaiwanStockParValueChange`（變更面額）、`TaiwanStockDelisting`（下市櫃）。寫入新表 `fm_corporate_events(stock_id, date, event_type, before_price, after_price, ratio, raw_json, source)` 與 `fm_delisting(stock_id, date, name)`。ratio = before_price / after_price（分割 >1、反分割 <1）；減資表若只有參考價，ratio 用「停止買賣前收盤／恢復買賣參考價」。
- `--fetch-adj`：對 `backtest/universe_2026_survivors.csv` 的 237 檔抓 `TaiwanStockPriceAdj`（還原股價）2020-12-01 起，寫入新表 `fm_price_adj_daily(stock_id, date, close_adj)`。這是 FinMind 官方除權息調整後序列，**報酬與動能一律改用它**。
- 若 FinMind 5xx，重試一次後跳過該項並記錄；報告列出缺哪些。

## 1. 公司行動（B）改法：事件表為主、價格跳動只做候選
- 報酬、動能（3 月、1 月、12−1 月）、均線：全部改用 `fm_price_adj_daily`。PER 位置仍用原價（PER 是比率，不受影響）。
- 仍保留價格跳動偵測，但輸出改為候選表 `backtest/corporate_action_candidates.csv`，欄位加 `status`：`confirmed`（在 fm_corporate_events 找到同股、日期 ±3 交易日的事件，附 event_type 與 ratio）、`unresolved`（找不到）。**unresolved 的跳動一律視為真實報酬，不做任何還原**。報告列 confirmed／unresolved 各幾筆、unresolved 的清單前 20 筆。
- 第一輪偵測出的 38 筆「減資」候選，逐筆對照事件表，報告列出被確認與被否決的數量。
- 舊的 `compute_holding_return_adjusted` 與比例反推邏輯移除或只留作對照，不再進主流程。
- 動能／PER 視窗跨過 **confirmed** 事件日：用還原價後動能不需 NA；PER 位置若跨事件仍記 NA 並列出被排除的股票月數。

## 2. 成交與估值狀態（A／C）改成一套不矛盾的流程
- 訊號日 = 共同市場日曆（`TaiwanStockTradingDate` 若已在 DB 用之，否則用 fm_price_adj_daily 的日期聯集）的已完成月末；到期用日曆月加 3／6／12 個月的月末，再各延一個共同交易日執行。資料最後日未涵蓋預定執行日的部位標 `right_censored`，整月排除，不得往前補價。
- 每部位三個狀態欄：`entry_status`（filled_T1／filled_T2／filled_T3／unfilled）、`exit_status`（filled／delayed／unresolved）、`valuation_status`（realized／last_available／delisted）。進場 unfilled → 現金 0%；出場 unresolved → 用最後可得還原價估值並標記，**不得**重設為 0%，也不得視為資金已可再投入。
- 刪掉「|close/prev−1| ≥ 9.5% 判鎖死」的確定規則。主分析假設有價日可成交；另跑一個「接近漲跌幅界線一律延一日」的保守情境，兩者並列並標為代理。
- 下市：對照 fm_delisting，下市日之後不再估值，用下市前最後還原價結算並標 `delisted`。

## 3. 母體（D）
- `universe_2026_survivors.csv` 加欄 `listed_date`（用 `TaiwanStockInfo` 的上市日若 DB 有，否則沿用首個收盤日並註明代理）。
- 修正「screen 名單排掉無 PER 股票」：母體改從 `stock_sub_industry` 半導體鏈與 ai_chain 清單的聯集取（不經 valuation_screen 過濾），再套 eligibility；報告列與 237 檔的差集。
- 名稱與報告一律稱「2026 存活成分股回顧回測」。

## 4. 配對推論（F）
- **把 `block_bootstrap_paired_diff` 真正接進主流程**（第一輪定義了但沒有任何呼叫，summary 的 CI 數字來源不明）：所有 CI 必須由程式產生並能重現，在 summary 每張 CI 表下方印出產生它的函式名與參數。
- 共同期間規則改為：每組父子配對只取**該父子兩策略**都有訊號的月份（不是全策略交集）；另列每個策略的完整資金時間軸（含空手月＝現金 0%）。
- 3 月與 6 月區塊各一組 CI、列有效區塊數；12 個月持有期的 CI 只用 6 月以上區塊。
- 12−1 動能對照：明講它是「全池原始動能」而 C1 是「同 sub 相對動能」，兩者差異不只 lookback；另加一個 `C1_12_1`（同 sub 相對 12−1）讓 lookback 效果可以單獨看。這是唯一新增的對照，不是新策略。

## 5. G 段合併與執行面重測
- 把第一輪工單裡兩段同名 G 合併為一：以還原價、統一 T+1（含觸發型進場法：收盤觸發、次日收盤成交）、E2 改等資金分批（`P_exit × mean(1/P_i) − 1`）、未成交與提前出場並列「現金」與「退出後轉入 universe 等權（只算退出後到到期那段）」兩口徑，重跑 stop_rule 與 entry_timing，寫進 `validity_summary.md` 「執行面結論修正」。

## 6. 產出與測試
- 更新 `backtest_validity.py`、`backtest_valuation.py`、`backtest_t1.py`、`backtest/validity_summary.md`（頂部加「資料版本與修復輪次」段，並把第一輪的數字保留為「第一輪草稿」對照欄）、`docs/tasks/reports/backtest-validity-2-report.md`。
- `tests/test_validity.py` 補：(i) 事件表確認才還原、unresolved 不還原；(ii) 還原價與原價在無事件區間報酬相同；(iii) bootstrap 函式被主流程呼叫且 CI 可重現（固定 seed 兩次相同）；(iv) 父子配對只取兩策略共同月；(v) 下市部位用最後價結算並標記；(vi) 等資金分批公式。既有測試不刪改；全綠。

## 禁止
- 除第 0 段外不打 FinMind；不動 `build_db.py` 等其他 build 腳本、HANDOFF.md、README.md。不 git commit / push。

## 附：sonnet 對第一輪產出的審查摘要（2026-09-08）
- B 的向上倍率公式已非 S=1/q，事件日報酬非零（手算三筆），但候選＝確認、無現金對價、無 unresolved 狀態；38 筆減資候選中同一股票重複命中佔一半，強烈懷疑多為真實急拉。
- `block_bootstrap_paired_diff` 無任何呼叫點，summary 第 2 節 CI 數字無法由 repo 重現。
- 共同期間用全策略交集，違反 Codex F.1。
- 9.5% 鎖死仍為確定規則。
- T+1、完整月、250 日資格、既有測試未動：通過。
