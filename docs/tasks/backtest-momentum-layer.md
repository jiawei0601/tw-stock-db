# 工單：回測加入動能層（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。

## 背景
`backtest_valuation.py`（你上一張工單 `docs/tasks/backtest-harness.md` 的產物，已通過審查並 commit）跑出的結論：估值層 L0/L1/L2 在 2022–2026 相對等權 universe 都是負超額、按月勝率 40–49%，只有 2024 年為正。假說：便宜股要漲需要「動能開始輪到它」，所以要測估值加相對動能的組合能否抓到 2024 那種輪動起點。月營收表現在已回填（`fm_revenue_monthly` 254 檔，2019-12 起，`revenue_last_year` 已填）。

## 要加的指標（全部 point-in-time，只能用 date ≤ 訊號日的價格）
- `mom_3m`：訊號日收盤 / 訊號日前 3 個月最近交易日收盤 − 1
- `mom_1m`：同上，1 個月
- `sub_mom_3m`、`sub_mom_1m`：同月同 sub 所有有價格股票的等權 mom
- `rel_mom_3m = mom_3m − sub_mom_3m`、`rel_mom_1m = mom_1m − sub_mom_1m`
- `rel_mom_rank`：rel_mom_3m 在同月全 universe 的百分位（0–1）

## 新增層（不要改動 L0/L1/L2 的定義與輸出）
- **M1**：L1 且 rel_mom_3m > 0（便宜、穩定、已開始相對跑贏）
- **M2**：L1 且 rel_mom_3m > 0 且 rel_mom_1m > 0（近月也在跑贏，輪動確認）
- **M3**：L0 且 rel_mom_3m > 0（不要品質層，看動能單獨加在便宜上的效果）
- **C1 對照組**：rel_mom_rank ≥ 0.75，不看估值（純動能，用來判斷估值有沒有額外貢獻）
- **C2 對照組**：rel_mom_rank ≥ 0.75 且位置 < 0（動能前四分之一裡的便宜股）

## 產出
1. `backtest_valuation.py`：加上述指標與層，CLI 不變。`signals.csv` 多出 mom_3m、mom_1m、rel_mom_3m、rel_mom_1m、rel_mom_rank、M1/M2/M3/C1/C2 旗標欄位。
2. `backtest/summary.md`：
   - 保留原 1–5 段，**刪掉第 2 段末尾「fm_revenue_monthly 目前為空」那條過時註腳**（改成依實際列數動態產生，為 0 才顯示）。
   - 新增第 6 段「動能層」：M1/M2/M3/C1/C2 各一張表（欄位同 L 層）、按年拆分表（至少 M1 與 C1）。
   - 新增第 7 段「比較矩陣」：一張表，列 = L1/L2/M1/M2/M3/C1/C2，欄 = 6 個月持有的月份數、平均選股、按月勝率（universe）、按月勝率（sub）、超額中位、超額平均、最差月。再一段文字回答三個問題：(a) 動能加在估值上有沒有把勝率推過 50%；(b) C1 對照組是否本來就贏，若是則估值層有沒有在 C2 帶來額外貢獻；(c) 2024 年 M1 是否比 L1 更早或更準地抓到輪動。
3. `tests/test_backtest.py` 新增測試：(a) mom 只用 ≤ 訊號日的價格（假資料在訊號日後放一根暴漲，mom 不得改變）；(b) rel_mom 減的是同 sub 等權而非全 universe；(c) rel_mom_rank 在同月內計算、範圍 0–1；(d) M2 蘊含 M1、M1 蘊含 L1。既有 8 個測試不得改動且必須仍綠。`python -m pytest tests/test_backtest.py -q` 全綠。
4. `docs/tasks/reports/backtest-momentum-report.md`：做了什麼、比較矩陣原文、三個問題的回答、你不確定的地方。

## 禁止
- 不打 FinMind、不寫 DB、不動 `build_valuation.py`／`build_*.py`／HANDOFF.md／README.md／既有測試。
- 不 git commit / push。
- 既有測試若因別人半成品失敗，只回報不修。
