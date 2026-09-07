# 審查請求二：有效性修復工單、集中度分析與當前結論（審查方：agy／Gemini）

你是獨立審查方，以乾淨 session 啟動，只看 repo。全程繁體中文。**不要修改任何檔案、不要網路請求、不要 commit**；可讀檔、可跑 `python backtest/concentration_mc.py --n 5` 與 `--n 10`、可跑 pytest。

## 讀取預算（重要：前兩次審查都因讀太多而在寫報告前撞額度）
- **禁止讀任何 `.csv`**（`backtest/signals.csv` 2.4 MB、`t1_signals.csv`、`entry_timing_detail.csv`）；要看數字只准跑上面列的腳本看 stdout，或讀 `.md` 摘要。
- `HANDOFF.md` 已瘦身為現況摘要；歷史在 `docs/handoff-archive.md`，**不要讀歸檔檔**。
- `backtest_valuation.py` 只讀這些函式：`compute_d_tiers`、`generate_portfolio_equity_curve`、`get_close_price`、`run_backtest` 的 D 層與前瞻報酬段；`backtest_t1.py` 不用讀（本輪不審 T1）。
- 先寫報告骨架與總判定，再逐項補；每完成一項就存檔一次，額度不足時至少留下已完成的部分。

## 背景
你（或同儕）先前的審查在 `docs/tasks/reports/codex-design-review.md`，總判定「需修正後可信」。之後我們：
1. 依該審查開了修復工單 `docs/tasks/backtest-validity-repair.md`（尚未執行）。
2. 在**修復前的舊口徑**上做了三個執行面測試與集中度分析，並向使用者給了結論。

## 請審四件事
1. **修復工單是否足以回應前次審查的【嚴重】項**：A 時點分離、B 公司行動、C 缺值、D 統一母體、E 組合層、F 配對差與區塊 bootstrap、12−1 對照。逐項標「足夠／不足／方向錯」與具體修法。特別檢查：B 用「單日跳動 >40% 視為分割並把事件日報酬記 0」有沒有把真實暴跌也當分割；F 的區塊 bootstrap 設計（6 個月區塊、2000 次、共用索引）是否合理；「進場改次日收盤、出場同樣延一天」是否保持了可比性；凍結名單取 `valuation_screen` 2026-09-04 那一次是否仍是存活者母體、有沒有更好的低成本做法。
2. **集中度與蒙地卡羅**（`backtest/concentration_mc.py`，讀 `backtest/signals.csv`）：對 D5 依 rel_mom_rank 取前 5／10 檔等權、隨機重抽訊號月 100 次。請指出這種「在 35 個重疊月份上重抽 100 次」的推論限制、前 5 與前 10 的差異在統計上能否區分、以及排名集中選股是否引入了第二層的搜尋偏誤。
3. **三個執行面測試的設計**（`backtest/stop_rule_test.py`、`backtest/entry_timing_test.py`、`backtest/entry_timing_summary.md`）：出場規則（月線連兩天等）與進場法（等拉回／分批／突破）的比較是否公平、是否受到「訊號與成交同一收盤價」漏洞的影響而系統性偏向 E0／不出場、結論「訊號日收盤買最好、固定到期換股最好」在修復後可能怎麼變。
4. **給使用者的當前結論**（`HANDOFF.md` 最後 10 條與 `analysis/committee-summary-2026-09-07.md`）：哪些說法在你看來說過頭了、應改成什麼措辭；使用者資金約 150 萬新台幣、考慮縮到 10 檔，從研究設計角度（不是投資建議）有沒有他該先知道的事。

## 輸出
寫到 `docs/tasks/reports/agy-review-2.md`：總判定（工單可執行／需改後執行）、工單必改清單（附段落與修法）、對三個分析的意見、對結論措辭的修改建議、你沒時間驗證的地方。
