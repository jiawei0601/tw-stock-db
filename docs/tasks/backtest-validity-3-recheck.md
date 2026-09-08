# 工單：第二輪有效性修復數字重核＋殘項修復（第三輪）（執行方：Codex exec）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源，不要讀對話、不要猜測未寫明的意圖。
工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。**只准用 `python`（repo 規定不用 pandas）。**
第二輪修復（`docs/tasks/backtest-validity-repair-2.md`，commit 9a27a0b，agy 實作、sonnet 驗收）已納入，
產出 `backtest/validity_summary.md`。之後發現兩件事使其數字需要重核：
1. **FinMind `TaiwanStockPriceAdj` 免費帳號打不到（400，需 Sponsor）**。`fm_price_adj_daily` 實際上是
   「原價＋已確認分割事件還原」，抽查 2330/2454/3596 與原價逐日相同。**它沒有還原現金股利。**
   但 `validity_summary.md` §0 寫「取得除權息歷史事件」「真實還原價格表」「FinMind 官方除權息調整後序列」，
   措辭不實。
2. sonnet 驗收留下四個未修項：(a) 5305 敦南無還原價；(b) bootstrap 可重現只做了「程式碼確定性論證」、
   未雙跑實測；(c) `fm_corporate_events` 抓取端曾寫入 event_type 亂碼重複列，只在 DB 去重、抓取端未修；
   (d) `compute_holding_return_adjusted` 被架空但仍在程式裡。

本輪目標＝**讓 summary 每個數字都能由 repo 重算出來、措辭與資料實況一致、殘項修完**。不新增策略、不調門檻。

## A. 數字獨立重算（核心）
新增 `verify_validity_numbers.py`（獨立腳本，**不 import `backtest_validity.py` 的聚合函式**，只共用 DB 讀取；
目的是用第二條路徑重算，若必須共用函式，在報告中逐一列出共用了哪些並說明為何無法獨立）：
- 讀同一份 DB／CSV 輸入，重算 `validity_summary.md` §1 表（8 個策略 × 有效月份／勝率／平均超額／中位超額）
  與 §2 表（每組父子配對的 N／平均配對差／中位配對差／勝率／6m 與 12m 區塊 CI）。
- 輸出 `backtest/validity_recheck.csv` 與 `backtest/validity_recheck.md`：每個數字三欄「summary 所載／重算值／差異」。
  差異絕對值 > 0.05 個百分點（或 N 不同）的列標 `MISMATCH`，並在報告解釋原因；解釋不出來就寫「未解」，
  不得改 summary 的數字去對齊重算值，也不得改重算腳本去對齊 summary。
- **bootstrap 雙跑實測**：同 seed 跑兩次比對所有 CI 端點完全相同；換 seed（43）跑一次，列出 CI 端點漂移幅度。
  寫進 `tests/test_validity.py` 為實跑測試（用小型合成資料即可，但主流程也要實際雙跑一次並把結果列在報告）。
- 結論重判：對 HANDOFF 已引用的三個判定逐一重核並給「維持／推翻／存疑」：
  D2→D3 品質排雷（原判：CI 跨 0，不成立）；D3→D5 營收為正（原判：成立）；C1→MOM_12_1（原判：成立）。

## B. 股利未還原的影響量化
- 在 summary §0 加「資料實況更正」段：`fm_price_adj_daily` = 原價＋分割還原，**未含現金股利**；PriceAdj 不可得的原因；
  之後全文凡「還原價」一律改為「分割還原價」，刪去「除權息」「官方調整後」等不實描述。
- 股利敏感度：對 §1 每個策略、§2 每組配對，用殖利率代理（DB 已有的殖利率欄位；若無，寫明代理來源）
  加回持有期股利，並列「無股利／殖利率代理」兩欄。逐一標出哪個判定在兩口徑下**結論翻轉**。
  summary 現有「含股利口徑超額趨同，見 summary.md」這句要不是能指到具體表格，就刪掉。
- 基準：檢查 summary 或程式是否任何地方用 0050 原價當基準；0050 於 2025 一拆四，原價不可直接用。
  若有，改為加權報酬指數（含息）或分割還原後的 0050，並在報告列出改動前後基準報酬差異。

## C. 殘項修復
1. **5305 敦南**：用與其他 252 檔相同的方法（原價＋確認事件）補建還原價；若原價本身缺，
   准打 FinMind `TaiwanStockPrice` **僅此一檔**（token 讀 `.env`，sleep 0.5 秒，402/403 即停）。仍補不到就寫明原因，
   並在 summary 標出受影響的月份與配對。
2. **`fm_corporate_events` 抓取端編碼**：在 `build_valuation.py` 的 `--fetch-events` 路徑修 event_type 編碼
   （UTF-8 讀寫、不經 cp950），加 idempotent 去重鍵（stock_id, date, event_type, before_price, after_price）。
   補測試：以含中文 event_type 的假回應寫入兩次，表內只有一列且字串無亂碼。**不重抓全市場事件**，只用假回應測。
3. **`compute_holding_return_adjusted`**：從主流程移除所有呼叫；函式本體刪除，或搬到 `legacy/` 並確認無呼叫點
   （`grep` 結果貼進報告）。相關舊測試若只為它存在，改為測試還原價路徑，不得單純刪測試降覆蓋。
4. 以上每項在 `validity_summary.md` §0 的「審查補充」四點各回一句「已修／未修＋原因」。

## D. 測試與產出
- `python -m pytest tests/test_validity.py -q` 必須全綠；`python -m pytest tests/ -q` 跑一次並把通過／失敗數寫進報告。
  **既有 6 個失敗（族群數 20/19、DB 孤兒列／法人新鮮度）是別人半成品，只回報不修、不刪、不 skip。**
- 報告：`docs/tasks/reports/backtest-validity-3-recheck-report.md`，內容依序：
  (1) 改了哪些檔案；(2) 重算對照表摘要＋所有 MISMATCH 列；(3) 三個判定的重判結果；(4) 股利敏感度翻轉清單；
  (5) 殘項四點狀態；(6) 測試數字（前／後）；(7) 實際執行的指令清單；(8) 你做不到或不確定的事。
  **不要寫「已驗證」「可信」一類結論性形容，只寫事實與數字。**

## 禁止（違反任一項即視為工單失敗）
- 不動：`backtest/t1_group_events.csv`、`backtest/stop_rule_*.csv`、根目錄 `tw_stock.db`、`data/momentum_pit/**`、
  `momentum_*.py`、`backtest_momentum.py`、`fetch_momentum_pit.py`、`HANDOFF.md`、`README.md`、`build_db.py`、
  `docs/momentum-*`。這些是另一個並行 session 的檔案。
- 不殺任何背景程序（有下載 worker 在跑，PID 在 `data/momentum_pit/worker.pid`）。
- 除 C.1 明定的一檔外不打 FinMind。
- 不 git commit、不 git push、不 git stash、不 checkout。
- 不改任何門檻、策略定義、母體規則。
