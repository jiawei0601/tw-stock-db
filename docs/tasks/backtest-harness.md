# 工單：估值篩選器回測框架（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。可自行拆分子任務並行，但檔案集合只限本工單列出的那些。

## 背景
篩選器 `build_valuation.py --screen` 用三年本益比位置、八季 EPS 變異係數、營收與 EPS 背離挑「低估」股。要驗證它有沒有預測力，需要一個**嚴格 point-in-time** 的月頻回測。歷史資料正由另一個程序回填到 2021-01-01（表可能只填了一部分），框架必須在任何覆蓋範圍下都能跑並回報覆蓋率。

## 資料（`data/tw_stocks.db`，唯讀）
- `per_daily(stock_id, date, per, pbr, dividend_yield)`
- `fm_price_daily(stock_id, date, close)`
- `eps_quarterly(stock_id, quarter_end, eps)`
- `fm_revenue_monthly(stock_id, ym, revenue, revenue_last_year)`（可能為空，空則營收層跳過並註明）
- `stock_sub_industry(stock_id, chain, node, sub, ...)`：sub 用第一列
- `valuation_screen`：只拿 `distinct stock_id, universe` 當 universe，不拿其他欄位
- **絕對不可打 FinMind API、不可寫 DB。**

## Point-in-time 規則（這是工單的核心，違反即失敗）
- 訊號日 = 每月最後一個有價格的交易日，範圍 = 資料可支援的所有月份。
- PER 視窗：訊號日往回 3 年（最少 1 年，不足 1 年該檔跳過），只用 date ≤ 訊號日的列。P25/P50/P75、位置 = (當日 PER − P25)/(P75 − P25)，剔除 PER ≤ 0 或 > 300。
- EPS 可見日：quarter_end 為 3/31、6/30、9/30 → quarter_end + 45 天；12/31 → + 75 天。訊號日只能用可見日 ≤ 訊號日的季資料。取最近 8 季算 eps_cv（std/|mean|）、loss_q、eps_ttm_growth（近 4 季合計 / 前 4 季合計 − 1）。
- 月營收可見日：該月營收在次月 10 日可見。rev_yoy_3m = 最近可見 3 個月 revenue 合計 / revenue_last_year 合計 − 1。
- 分割偵測：訊號日前 60 個交易日內 |close/prev − 1| > 0.4 → 排除。
- 前瞻報酬：訊號日收盤到之後 3 / 6 / 12 個月最近交易日收盤，不含股利（註明）。資料不足的期間記 NA。

## 三層濾網（逐層累加）
- L0：位置 < 0
- L1：L0 且 eps_cv < 0.5 且 loss_q = 0 且 PER 視窗點數 ≥ 250 且非分割
- L2：L1 且 rev_yoy_3m > 0 且 eps_ttm_growth > 0 且非背離（背離 = rev_yoy_3m < −0.10 且 eps_ttm_growth > 0.20）
- 基準：同月所有可算位置的 universe 股票等權；另算「同 sub 等權」基準。

## 產出
- `backtest_valuation.py`：CLI `python backtest_valuation.py --out backtest/`，純 Python 標準庫 + pandas/numpy（repo requirements 已有）。
- `backtest/signals.csv`：每列 = (訊號日, 股號, sub, 層級旗標 L0/L1/L2, 位置, eps_cv, rev_yoy_3m, ret_3m, ret_6m, ret_12m, bench_3m/6m/12m, sub_bench_3m/6m/12m)。
- `backtest/summary.md`：
  1. 覆蓋率：訊號月份數、每月平均可評估股票數、各表最早日期。
  2. 每層一張表：月份數、平均選股數、**按月**算的勝率（該月選股等權超額報酬 > 0 的月份比例，對 universe 基準與 sub 基準各一欄）、超額報酬中位數與平均、最差月份與其日期、3/6/12 個月各一列。
  3. 按年拆分的同表（至少 L2）。
  4. 一段「濾網邊際貢獻」文字：L1 相對 L0、L2 相對 L1 的勝率變化。
  5. 誠實限制：不含股利、存活者偏誤、樣本期間、橫斷面相關（勝率按月算的理由）。
- `tests/test_backtest.py`：用臨時 SQLite 建假資料，至少驗證 (a) EPS 在可見日之前不會被用到；(b) 月營收在次月 10 日前不可見；(c) PER 視窗不包含訊號日之後的資料；(d) 分割排除生效；(e) 前瞻報酬取對日期。`python -m pytest tests/test_backtest.py -q` 必須全綠。
- `docs/tasks/reports/backtest-harness-report.md`：做了什麼、跑出的覆蓋率、summary 的 L2 主表原文、已知限制、你不確定的地方。

## 禁止
- 不動 `build_valuation.py`、`build_*.py`、既有 tests、HANDOFF.md、README.md。
- 不 git commit / push。
- 若既有測試因別人的半成品失敗，只回報不修。
