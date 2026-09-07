# 工單：動能「轉強」訊號 T1 回測（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。
**另一個 agy 正在同一工作樹執行 `docs/tasks/backtest-momentum-first.md`，它會改 `backtest_valuation.py`、`tests/test_backtest.py`、`backtest/summary.md`。你不得碰這三個檔案，本工單全部寫在新檔案裡。**

## 背景
前面工單證實：已經很強的相對動能（C1，rank ≥ 0.75）有預測力；但「已強」在 2024 輪動時晚了七個月才進場。本工單測「轉強」：從弱變不弱的那一刻，用二階訊號（翻正、斜率）加台股獨有的法人流向。

## 資料（唯讀）
- 價格：`fm_price_daily(stock_id, date, close)`
- 子產業：`stock_sub_industry`（sub 取第一列）
- 法人：`institutional_flow_daily(stock_id, date, foreign_net, trust_net, dealer_net)`，單位股數，涵蓋 2023-06-13 至 2026-08-25。**2023-07 之前與 2026-08-25 之後的訊號日法人條件為 NA**，含法人條件的層在這些月份不產生訊號，summary 要註明各層有效月份數。
- universe：`SELECT DISTINCT stock_id FROM valuation_screen`
- 估值排雷用的 eps_cv / loss_q / 背離 / 分割 / 位置：**直接 import `backtest_valuation.py` 的函式重用**（read-only import，不修改它）。若 import 介面不穩，可複製必要函式到你的模組並註明來源行號。
- 不打 FinMind、不寫 DB。

## Point-in-time 規則
沿用 `docs/tasks/backtest-harness.md` 全部規則。法人 20 日累計只用 date ≤ 訊號日的列。「前一個月」的值一律指前一個訊號日算出的值，不得用同月內任何未來資料。

## 指標（每檔每訊號日）
- `rel_mom_1m`、`rel_mom_3m`：同 `backtest_valuation.py` 定義（個股減同 sub 等權）。
- `flip_1m`：本月 rel_mom_1m > 0 且上月 rel_mom_1m ≤ 0。
- `slope_3m`：本月 rel_mom_3m − 上月 rel_mom_3m > 0。
- `foreign_20d`：訊號日往回 20 個交易日 foreign_net 合計；`trust_20d` 同理。
- `chips_ok`：foreign_20d > 0 或 trust_20d > 0（任一為正）。
- `above_ma60`：訊號日收盤 > 近 60 個交易日收盤均值；`ma20_up`：20 日均值 > 上月訊號日的 20 日均值。

## 訊號層
- **T1a** = flip_1m 且 slope_3m（純價格二階）
- **T1b** = T1a 且 chips_ok
- **T1** = T1b 且 above_ma60（工單正本定義）
- **T1x** = T1 排除估值陷阱（eps_cv ≥ 0.5、loss_q > 0、背離、分割）
- **對照**：C1（rank ≥ 0.75，重算或從 signals.csv 讀）與 universe 等權。

## 族群層（另一張表）
每月每 sub 算廣度：`breadth_pos` = sub 內 rel_mom_1m 對大盤（universe 等權）為正的比例；`breadth_ma60` = sub 內 above_ma60 比例。定義 **G1**：breadth_ma60 由 < 0.4 升到 ≥ 0.5 的那個月。列出所有 G1 事件（月份、sub、事件後 sub 等權 3/6 個月報酬對 universe 的超額），並回答「G1 之後 6 個月 sub 跑贏 universe 的比例」。

## 產出
1. `backtest_t1.py`：CLI `python backtest_t1.py --out backtest/`，輸出 `backtest/t1_signals.csv`、`backtest/t1_group_events.csv`、`backtest/t1_summary.md`。
2. `t1_summary.md`：
   - 覆蓋率（各層有效月份數、法人資料涵蓋期間）。
   - 比較矩陣：列 = universe / C1 / T1a / T1b / T1 / T1x，欄 = 3 與 6 個月持有各自的月份數、平均選股、按月勝率（universe、sub）、超額中位、超額平均、最差月。
   - **進場時點分析**：對每個曾進入 C1 的 (股票, 月份)，往回找最近一次 T1 觸發，算「T1 領先 C1 幾個月」的分布（中位數、四分位）；以及 T1 觸發後 6 個月內進入 C1 的比例（轉強有沒有變成真強）。
   - 2024 年逐月：T1 與 C1 各選出幾檔、當月選股後 6 個月超額。
   - 族群層 G1 事件表與結論。
   - 文字回答：(a) 二階訊號單獨（T1a）有沒有預測力；(b) 法人條件是加分還是只縮小樣本；(c) T1 相對 C1 是「更早但更不準」還是「更早且不差」；(d) 族群廣度 G1 能否當輪動偵測器。
3. `tests/test_backtest_t1.py`：(a) flip 用前一訊號日的值，同月內未來價格不影響；(b) foreign_20d 只含 ≤ 訊號日；(c) 法人缺資料時 T1b/T1 為 NA 而非 False；(d) 層蘊含 T1x⊆T1⊆T1b⊆T1a；(e) 廣度計算一例。`python -m pytest tests/test_backtest_t1.py -q` 全綠。
4. `docs/tasks/reports/backtest-t1-report.md`：做了什麼、比較矩陣與進場時點分布原文、四個問題的回答、不確定處。

## 禁止
- 不動 `backtest_valuation.py`、`tests/test_backtest.py`、`backtest/summary.md`、`build_*.py`、HANDOFF.md、README.md。
- 不 git commit / push。
- 別人的半成品讓既有測試失敗時只回報不修。
