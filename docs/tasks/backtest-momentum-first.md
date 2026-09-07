# 工單：動能為主、估值排雷的策略回測（執行方：agy / Gemini）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。

## 背景
前兩張工單（`docs/tasks/backtest-harness.md`、`docs/tasks/backtest-momentum-layer.md`）的結論：估值層 L0–L2 無預測力；純相對動能 C1（rel_mom_rank ≥ 0.75）6 個月按月勝率 67%、超額中位 +2.4%；動能裡再挑便宜的 C2 反而最差。估值層唯一證實有用的是「排雷」（eps_cv、虧損季、營收與 EPS 背離、分割）。本工單把角色反過來：動能選股，估值與品質只做排除，並補上股利與交易成本兩個先前沒算的項目。

## 資料與前置
同 `backtest_valuation.py` 現況（point-in-time 規則全部沿用，不得放寬）。`per_daily.dividend_yield` 為 FinMind 當日殖利率（年化，%）。

## 策略層（在 C1 基礎上逐層排除，每層都是前一層的子集）
- **D0** = C1（rel_mom_rank ≥ 0.75），對照基準。
- **D1** = D0 排除 split_flag。
- **D2** = D1 排除背離（rev_yoy_3m < −0.10 且 eps_ttm_growth > 0.20）。
- **D3** = D2 排除品質差（eps_cv ≥ 0.5 或 loss_q > 0，或 EPS 季數不足 8）。
- **D4** = D3 排除極端貴（位置 > 2.0，即 PER 超過自身 P75 加一個區間寬度）。
- **D5** = D3 排除 rev_yoy_3m ≤ 0（動能股要有營收支撐）。
另外做 **rank 門檻敏感度**：D3 在 rank ≥ 0.6 / 0.75 / 0.9 三種門檻各跑一次。

## 新增兩個報酬口徑（每層都算）
- **含股利近似**：`ret_x_tr = ret_x + dividend_yield_at_signal/100 × (持有月數/12)`，dividend_yield 取訊號日 per_daily 值，缺值當 0。summary 必須註明這是近似（假設殖利率在持有期內均勻實現）。
- **扣交易成本**：每次進出扣 0.6%（台股手續費 0.1425% × 2 加證交稅 0.3%，取整），`ret_x_net = ret_x_tr − 0.006`。基準也同樣扣（基準每月換股視同一次進出）。

## 組合層指標（新增）
對 D3 與 D0 與 universe 基準三者，用 3 個月持有、每月等權換股（三個重疊子組合平均，即 1/3 資金每月換一次）算：
- 月報酬序列 → 累積淨值曲線（起點 1.0）、年化報酬、年化波動、最大回撤（含發生日期）、Sharpe（無風險利率 0）。
- 輸出 `backtest/equity_curve.csv`（date, D0, D3, bench）。

## 產出
1. `backtest_valuation.py`：加上述層與指標，CLI 不變；signals.csv 多出 D0–D5 旗標、ret_*_tr、ret_*_net、dividend_yield_at_signal。既有 L/M/C 層定義與數字不得改動（如需修 bug 必須在 summary 頂部揭露並說明數字變動）。
2. `backtest/summary.md` 新增第 8 段「動能為主策略」：
   - 比較矩陣：列 = universe 基準 / D0–D5 / D3@0.6 / D3@0.9，欄 = 6 個月持有月份數、平均選股、按月勝率（universe、sub）、超額中位、超額平均、最差月，**三個口徑各一張表**（純價格、含股利近似、扣成本）。
   - 按年拆分表（D0 與 D3）。
   - 組合層指標表（D0 / D3 / bench）。
   - 文字回答：(a) 哪一道排除最有貢獻、哪一道沒有；(b) 扣成本與含股利後 D3 是否仍贏基準；(c) rank 門檻敏感度是否單調；(d) 最大回撤發生在哪個月、當時發生什麼（只從資料描述，不要猜新聞）。
3. `tests/test_backtest.py` 新增：(a) D 層蘊含關係 D5⊆D3⊆D2⊆D1⊆D0；(b) ret_tr 公式一例（殖利率 4%、6 個月 → 加 2 個百分點）；(c) ret_net 扣 0.6%；(d) 累積淨值與最大回撤用假序列驗證（例如 [+10%, −20%, +5%] → 最大回撤 20%）。既有 12 個測試不得改動且仍綠。
4. `docs/tasks/reports/backtest-momentum-first-report.md`：做了什麼、第 8 段三張矩陣原文、四個問題的回答、你不確定的地方。

## 禁止
- 不打 FinMind、不寫 DB、不動 `build_valuation.py`／`build_*.py`／HANDOFF.md／README.md／既有測試。
- 不 git commit / push。
- 既有測試若因別人半成品失敗，只回報不修。
