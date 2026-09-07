# 2020 起動能選股：資料查核與執行狀態

**判定：尚未完成正式回測。不得把既有結果標示為「2020 起、無前視偏誤」。**

本輪實際完成 repo／資料庫查核、FinMind 公開 API 可用性驗證、下載器及測試，並啟動全候選歷史資料下載。策略規格與驗收門檻見 [任務契約](../docs/tasks/momentum-2020-pit.md)。

## 原資料庫實查

| 表 | 筆數 | 期間 | 對本題的限制 |
|---|---:|---|---|
| fm_price_daily | 324,612 | 2021-01-04–2026-09-04 | 缺 2019–2020 暖機與投資行情 |
| fm_price_adj_daily | 324,612 | 2021-01-04–2026-09-04 | 實際為分割還原，未完整處理股利 |
| daily_prices | 1,432,100 | 2023-06-13–2026-08-25 | 起點更晚，僅有收盤價 |
| taiex_daily | 790 | 2023-06-01–2026-08-31 | 期間不足，且是價格指數 |
| fm_delisting | 133 | 2020-01-09–2026-09-03 | 僅 1 個下市代碼能連到 fm_price_daily |

`backtest_valuation.py` 從今日 AI 清單、子產業表及 `universe_2026_survivors.csv` 組母體，無法透過單純把起始日往前移，轉成歷史全市場股票池。

`backtest_validity.py::compute_stock_forward_returns` 以訊號日殖利率乘持有年數近似股利；下市與缺價時採最後價格估值。前者不是實際現金流，後者也不能視為保證可成交的已實現收益。現有函式名稱與報表措辭不能取代資料來源驗證。

## 舊 CSV 只供對帳，不能拿來回答本題

本輪另讀取現有 `backtest/signals.csv`，不重新選股、不修改舊檔。對 `MOM_12_1=True` 按訊號月份等權聚合，只有同批所有股票都有該持有期報酬才計算；未扣成本、不含實際股利：

| 持有期 | 成熟批次 | 訊號期間 | 平均批次價格報酬 | 中位數 | 正報酬批次比例 |
|---|---:|---|---:|---:|---:|
| 6 個月 | 50 | 2022-01-26–2026-02-26 | 25.00% | 21.18% | 76.00% |
| 12 個月 | 44 | 2022-01-26–2025-08-29 | 53.35% | 50.88% | 79.55% |

**這些是存活者／特定產業條件下的舊 CSV 描述統計，不是新回測結果，也不是可期待收益或可比的年化績效。** 6／12 月樣本期間不同且批次重疊，不能由此判定持有 12 月更好，也不能將平均收益連乘成年化。舊 CSV 與既有摘要的超額數字另有不一致，故不將舊摘要當成此輪實測證據。

## FinMind 公開 API 實測與下載

- 公開 `TaiwanStockPrice` 可取得 2019 年行情，且 2456 奇力新有已下市歷史行情（初步探測 2019 起 729 筆）。
- `TaiwanStockPriceAdj` 公開無認證實测回 status 400，提示需要升級；不以原價冒充含息還原。
- `TaiwanStockInfo` 4,319 列，其中普通股代碼格式、曾屬 twse/tpex 的候選 2,149 個。聯集 2018-12 起下市表 154 列，凍結下載候選 **2,162 個**。
- Info 的 `date` 是更新日期，不是證實的歷史上市日。候選聯集可減少明顯存活者遺漏，但**不能保證沒有遺漏全部歷史標的或錯納其他市場期間**。
- 加權與櫃買報酬指數各取得 1,886 筆；股票交易日曆、分割、面額變動均成功。
- 1101 的除權息結果與股利政策各 8 筆，減資表成功空資料。2456 的股利政策初步探測為空，須與除權息結果及獨立事件證據核對；「API 成功空值」與「已證明零股利」不同。
- 使用 repo 既有 FinMind token，僅放認證 header、不落盤；每 7 秒一請求，約 514 次／小時。2,162 × 4 個個股資料集加全域查詢約 **8,655 次、17 小時以上**；API 錯誤、額度或斷網可使程序提早停止。

## 操作與交接

下載器：`fetch_momentum_pit.py`，只寫 `data/momentum_pit/`，不改原資料庫。

```powershell
# 有一般 Python 的環境
python fetch_momentum_pit.py
# 檢查進度（約每 25 個候選及停止時更新）
Get-Content data/momentum_pit/status.json
# 檢查程序／日誌
Get-Content data/momentum_pit/worker.pid
Get-Content data/momentum_pit/worker.log -Tail 5
# 溫和停止：下一次迴圈退出，保留成功快取
New-Item data/momentum_pit/STOP -ItemType File
# 續傳前刪除自己建立的 STOP，然後再次跑同一命令
```

本機 `python` 不在 PATH、`py` 未登錄直譯器；本輪實測使用：
`C:\Users\chang\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`。

資料庫 `finmind_raw.db` 保存 query／取得時間／原始 body／SHA-256／HTTP 與 API 狀態／筆數。`manifest.json` 固定候選及 2018-12-01–2026-09-07 期間。失敗結果不可當成功快取；成功但空資料保留待核對。`status.json` 即使顯示下載完成，仍為 `NOT_CERTIFIED`；**下載器不會自動執行或認證回測**。

下載完成後仍須做：歷史身分／轉板核對、公司行動与下市結算、獨立訊號／報酬計算器、未來資料擾動不變性測試、6／12 月 cohort 與資金梯隊回測。必須通過任務契約驗收才可回答正式收益率。

驗證：`python -m unittest tests.test_momentum_pit_fetch -q`，6 個測試通過；`git diff --check` 通過。此次沒有重跑依賴原資料庫的全專案測試，也未修原專案的既有資料紅燈。

來源：[FinMind 官方 API／額度](https://finmind.github.io/en/quickstart/)、[官方技術資料（含更新日期語意與付費端點）](https://finmind.github.io/tutor/TaiwanMarket/Technical/)。
