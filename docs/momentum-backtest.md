# 動能回測程式使用說明

此程式可在資料下載完成前，以合成資料驗證選股、交易記帳及報表。真實資料必須完成下載與歷史證據核對後才可使用；下載器持續運作時，只讀盤點不會修改其資料庫。

## 執行

在 repo 根目錄執行，Python 3.11+；三個新模組僅使用標準庫。

```powershell
# 合成示範：完整跑 6／12 個月及成本敏感度
python backtest_momentum.py --demo --out backtest/momentum_demo

# 盤點目前下載快取，不計算收益
python backtest_momentum.py --audit --raw-db data/momentum_pit/finmind_raw.db --manifest data/momentum_pit/manifest.json --out backtest/momentum_audit

# 已核對的正規化資料
python backtest_momentum.py --input data/momentum_pit/normalized.json --out backtest/momentum_pit

# 完整 FinMind 快取，加上已核對歷史股票身分、公司行動、成交限制的證據檔
python backtest_momentum.py --raw-db data/momentum_pit/finmind_raw.db --manifest data/momentum_pit/manifest.json --evidence data/momentum_pit/evidence.json --start 2020-01-01 --end 2026-09-07 --cost-sensitivity --out backtest/momentum_pit

# 本輪所有測試
python -m pytest tests/test_momentum_engine.py tests/test_momentum_data.py tests/test_momentum_report.py tests/test_momentum_pit_fetch.py -q
```

若 `python` 不在 PATH，本輪可用的標準庫 runtime 是
`C:\Users\chang\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`。
該 bundled runtime 沒有 pytest；核心／資料／下載測試也可用 `-m unittest tests.test_momentum_engine tests.test_momentum_data tests.test_momentum_pit_fetch -q`。

## 輸出

- `report.md`：繁體中文摘要，批次報酬與資金梯隊年化分開，含依進場年度、共同成熟月份、同期等權比較。
- `result.json`：完整結果、假設、未解決狀態與信賴區間。
- `signals.csv`：每批股票、分數、排名、權重。
- `trades.csv`：每單位投入資金的股數、成本、權利及付款事件；`weight` 為該批權重，`post_exit_cash_payment` 列為實際梯隊資金單位。
- `cohorts.csv`：每月買進批次的價格、含息及淨報酬；`pending_cash_net` 顯示期末應收款。未成熟或未解決的報酬不會填 0。
- `equity.csv`、`equity.svg`、`drawdown.svg`：逐日資產與回撤。
- `cost_sensitivity.json`：固定選股規則，買賣各 0.15%／0.30%／0.60% 的敏感度摘要；不是事後挑最佳策略。
- `--audit` 只產生 `audit.json`，列缺查詢、失敗、空值及截止日期，不產生收益率。

合成輸出 CSV／SVG／敏感度檔名加 `synthetic_` 前綴，報表與 JSON 同樣標記合成，不能拿來當台股績效。

## 計算口徑

主策略固定 12−1 動能前 25%，以當時具歷史資格、12 個月資料及至少 200 個有效交易日的股票排名，並列以股票代碼決定，選取數為候選數 × 25% 無條件進位。訊號月末確定，次月第一個市場交易日開盤成交；到期月份第一交易日開盤賣出。

訊號使用事件調整的日總報酬連乘；訊號日以後的資料不進入選股函式。持有期間不再平衡，現金股利入應收款、付款日轉現金；分割／股票分配直接改股數，減資用已核對的 `distribution` 比例與現金合併事件表示。持有期總報酬採含已確定應收款的資產口徑，不能把應收款視為當天可投入現金。

資金分 6／12 格，每月輪到一格重新投入。賣出後才付款的股利留在原格應收款，付款日入該格備用現金，等該格下次輪替才再投入；不提前挪用。若缺可靠估值，從該日起曲線與年化指標標空值；若到期股票無法賣出，不假設可以取回本金重投。

採可分割股數與無市場衝擊的研究模型，尚不代表零股、整張限制或大額資金容量測試。`buyable`／`sellable` 是外部核對的成交事實，單憑有收盤價不會自動認定可成交。

TAIEX／TPEx 只有含息收盤資料，列為收盤參考曲線；與股票開盤執行有時間口徑差，不計成精確開盤超額。同池等權組合則使用完全相同的成交及成本規則。

6／12 月批次重疊，不連乘為年化；CAGR、MDD、年化波動來自資金梯隊。區塊 bootstrap 固定 seed=42，區塊長度為持有期，至少兩個區塊才報信賴區間。這是已探索資料上的研究，不宣稱真正樣本外。

## 證據檔

`docs/momentum-evidence-template.json` 是故意不能直接通過驗收的空模板。資料下載完後由本任務核對來源並填寫，不是要求使用者將旗標全部改 true 來繞過驗證。

- `securities`：歷史普通股資格期間，`end` 為 exclusive，可有多個期間；`known_at` 與來源必填。不得使用 Info.date 冒充上市日。
- `events`：每股票每生效日一筆已核對的合併事件。`split` 必填實際 `ratio`；`distribution` 的 ratio 為每舊股新股數、cash 為每舊股現金；有 cash 必填 pay_date；`delist` 必填實際結算 cash（確定零清算可填 0）。不能以價差推算未知股份比例。
- 股份分配或減資的 `distribution.ratio` 非1時，必須提供 `shares_available_date`。股票股利在可交易交付日前只列權利；若持有期到期仍未交付，標示未解決，不把這些股份當成當天可賣出的股票。減資需對齊恢復交易日，不猜測停牌期間的股價；多個未交付權利重疊須再核對。
- `execution.default` 及按股票／日期的 overrides 均需來源；defaults 是明確的外部核對聲明，不是程式推測。
- `verified` 四項與 `evidence` 四個非空來源，是核對記錄。程式會檢查完整性與結構，**不會自行證明來源敘述是真的**；輸出 `EVIDENCE_ATTESTED` 僅表示有提供核對聲明，不等於獨立認證無偏誤。

正式快取接線拒絕部分下載、較短日期查詢或缺少證據。不會自動把 FinMind 空股利表當成零股利，也不會自動把未核對事件轉成認證事件。

## 本輪驗證狀態（2026-09-08）

本輪四組測試 **42 通過**；包含未來行情改寫／資料截斷／未來 IPO 不改舊排名、跳過最近月、分割中性、成本、未成交現金、股利付款、下市結算、未解決部位、梯隊啟動現金、快取失敗與不足拒絕、報表及合成標記。

全專案測試 **334 通過、6 失敗**：3 項為現有族群 20 與舊預期 19 不符；另 3 項為原資料庫營收／法人孤兒資料及法人摘要新鮮度（93.95% < 95%）。本輪未修改相關既有模組或正式資料庫，未刪除下市歷史資料以讓舊測試轉綠。此為程式階段檢查點，**不宣稱全專案驗收完成**。
