# 免費營收資料補齊（2026-09-09）

使用者授權先補齊免費資料。主線負責FinMind逐股下載與來源合併，subagent `free_revenue_gap_inventory`（GPT-5.6 Sol）負責只讀缺月清單，兩者不改同一檔案。

## 本輪範圍

- 先補2020起有成交卻完全缺營收的82檔，優先1701/2456/3454/4141/5371/6457/9103。
- 檢查已有營收股票的需要月份：每個回測有交易月份s，可能使用s−1/−2/−3及s−13/−14/−15，比較範圍2018-01至2026-07；2026-08另列尚未齊全，不稱歷史遺漏。
- 有完整當月值才算數字補到；不把無資料回應改成0。負營收照實保存。與官方數值不符要保留來源衝突。
- 低優先106檔在回測期間無成交，不直接把它們視為影響2020起策略的缺股；有餘裕時一併查詢。

## 下載合約

`backfill_finmind_revenue.py --targets PATH --end 2026-09-09`，targets可為代號list或含target_ids的JSON。公開API TaiwanStockMonthRevenue，start_date=2018-01-01；同查詢成功快取含空資料不重抓，失敗預設不重試，可明示--retry-failed。HTTP/業務限額或連續失敗即停止，不繞限額。

新資料庫 `data/momentum_pit/revenue_archive/free_revenue.db` 的responses保留查詢、回應JSON、SHA256、抓取時間、狀態與錯誤；observations保留所屬月、元單位營收、原始date/create_time與逐列JSON。date只當API原欄，不改成known_on；create_time空值照實保存。

成功下載不是PIT認證，不自動餵給正式歷史回測或實盤。不得將官方出表日、FinMind月份date或法定公告期限填成原始公告日。

## 狀態

使用者中途要求先用現有資料測試，故首批82檔完成後停止擴大下載。82檔查詢全部成功，66檔有營收、16檔空資料；加先前probe快取共84個查詢、4,255原始列。空資料不是零營收。

subagent列出原始312檔/6550個核心缺月份（另有2026-08pending）。首批補洞後，既有官方＋FinMind合併194,993個無衝突股票月份，核心缺月減為3129、分布261檔；補了3421個需要月份，但整體免費補齊工作尚未完成。差額下載暫未啟動，沒有背景程序。

缺月盤點 `free-revenue-gap-inventory.md`；6檔特殊缺口盤點 `free-revenue-fallback.md`。9103有交易但兩來源無營收，不能推定TDR免報；3718是2026-09新代號，不能套5371營收。保存缺值。

本輪後續探索回測見 `analysis/momentum-revenue-research-2026-09-09.md`，無法據此認證PIT。135相關測試通過。
