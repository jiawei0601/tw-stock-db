# 歷史月營收時點版本來源查核

查核日：2026-09-09（Asia/Taipei）。範圍是確認 2020–2026 回測能否以免費公開來源建立「某日當時已可得的月營收版本」，不是再下載一份今日最新的歷史月營收表。

## 結論

FinMind 公開 API 和官方 GitHub 無法還原 2020–2025 歷史月營收版本。API 可提供今日值，但當時可得日與過往修訂版本沒有公開序列。MOPS `t21sc03` 歷史月份頁也是今日取得的當前快照，不能自我證明這些值在當年的哪一天已公開。

可行的免費 PIT 路徑是逐家使用公司原始投資人新聞稿，但公司覆蓋不一，無法據此自動補齊全市場。台積電官方新聞稿已證明至少可建立一組可驗算版本資料：2019-12-11 訊號可以當時已公開的 2019-11、2019-10、2018-11、2018-10 四個值計算，結果通過「年增為正且加速」。可機器讀取樣本在 `backtest/momentum_rerun_20260909_revenue_audit/vintages/tsmc_2019_press_release_sample.json`。

## FinMind 證據

FinMind 的[月營收官方文件](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)明載：

- `create_time` 自 2026-04-21 才開始記錄，以前是空字串。
- 它是該筆資料進入 FinMind 資料庫的日期，不是公司在 MOPS 的正式公告時間。
- 2026-04-21 啟用當日初始寫入的列全都是該日，不代表那些營收在當日公告。

官方 FinMind-Doc 的 Git 歷史也能重現此變更：[`2eaec9d3b68e`](https://github.com/FinMind/FinMind-Doc/commit/2eaec9d3b68e) 在 2026-05-22 把 `create_time` 加入文件；[`abb2a604ee6c`](https://github.com/FinMind/FinMind-Doc/commit/abb2a604ee6c) 在 2026-08-21 補上上述起始日和語意。後者 commit message 還列出 2330 實測值：2026-01/02 所屬列為空，2026-03-01/04-01 列的 `create_time` 同為啟用日 2026-04-21，後續才是 2026-05-08、06-10、07-13、08-10。

本專案已保存的 2330 API probe 有 104 列，對應 104 個不重複的所屬月；98 列 `create_time` 為空，只有 6 列非空。公開 schema 只有 `date, stock_id, country, revenue, revenue_month, revenue_year, create_time`，沒有 revision ID、superseded time 或舊值。因此：

1. `date=2020-02-01, revenue_year=2020, revenue_month=1` 只是月份標籤，不是 2020-02-01 已知。
2. 就算 2026-04-21 以後 `create_time` 非空，它也只能當 FinMind 觀察日的近似來源，不是公司公告日證明。
3. 單次 API 回應每個股票／所屬月只有一列，不能從現在回應還原修訂前值。本次查核不知 FinMind 私有後端是否另有 audit log，只能確認公開 API 沒有暴露。

FinMind 官方 GitHub organization 當日列出 8 個 public repositories，沒有月營收資料 dump repository。`FinMind/FinMind` 主 repo 的 master recursive tree 有 108 個 blob，沒有檔名含 `revenue` 的檔案，也沒有 CSV/Parquet/SQLite/SQL 資料檔；它是 API client/library，不是每日資料快照封存。可重現的 GitHub API：

```text
GET https://api.github.com/orgs/FinMind/repos?per_page=100&type=public
GET https://api.github.com/repos/FinMind/FinMind
GET https://api.github.com/repos/FinMind/FinMind/git/trees/master?recursive=1
GET https://api.github.com/repos/FinMind/FinMind-Doc/commits?path=docs/tutor/TaiwanMarket/Fundamental.md&per_page=100
```

GitHub commit 時間只能證明文件／client 在當時存在，不能當成未被 commit 的 API 資料值快照。

## MOPS 靜態歷史頁限制

對 `https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_109_1_0.html` 的 2026-09-09 實測：HTTP 200，內容含 2330 的 2020-01 營收 `103,683,135` 千元，但 HTTP `Last-Modified` 是 `Wed, 09 Sep 2026 01:25:10 GMT`。這證明該 URL 在今日可以查某歷史月份的當前值，同時也證明不能用當前 HTTP 檔案時間作 2020 公告日。頁面層「出表日期」同樣不是逐公司原始申報日，更沒有修訂前後版本序列。

所以主線正在下載的全市場 MOPS 封存值可用來建當前歷史數值表，不能一律補 `known_on`，也不能標成無前視 PIT。

## 可驗算的台積電版本樣本

兩份台積電原始 PDF 各自同時提供發布日、當月值、上月值和去年同月值：

- [TSMC November 2019 Revenue Report](https://pr.tsmc.com/system/files/newspdf/THHKTHPGTH/NEWS_FILE_EN.pdf)：2019-12-10 發布；表格單位為新台幣百萬元；2019-11 = 107,884，2019-10 = 106,040，2018-11 = 98,389。
- [TSMC October 2019 Revenue Report](https://pr.tsmc.com/system/files/newspdf/THWQHIPGTH/NEWS_FILE_EN.pdf)：2019-11-08 發布；表格單位為新台幣百萬元；2019-10 = 106,040，2018-10 = 101,550。

對 2019-12-11 訊號，四個版本的 `known_on` 均嚴格早於訊號日：

```text
YoY(2019-11) = 107,884 / 98,389 - 1 = 9.650469%
YoY(2019-10) = 106,040 / 101,550 - 1 = 4.421467%
9.650469% > 0 且 9.650469% > 4.421467% => pass
```

樣本的 2018 同月基數特別使用這兩份當年新聞稿同頁明載的 comparison value，`known_on` 是該新聞稿日期。它只主張「至遲在該日已知」，不倒填更早的假想日期。

數值以 PDF 表格顯示的百萬元精度換算成元，因此為 `107,884,000,000` 等整數。它們和今日 MOPS/FinMind 精確到千元的值會有小量差異；樣本沒有把後來更精細的數值冒充當年 PDF 值。回測若擴張此路徑，同一檔內應全程使用同一公開精度，並單獨標記近零或近加速邊界的分類敏感度。

## 覆蓋與修訂風險

台積電官方 [News Archives](https://pr.tsmc.com/english/news-archives) 有發布日與按年篩選，查核中可找到 2019 多個月份、2020 全年行事曆的月營收日期，以及 2026 當期新聞稿。所以對 2330，使用新聞封存頁列與各 PDF 表格，向 2019–2026 逐月擴張是具體可行的；每月要保存 URL、發布日、當月與去年同月值，並檢查封存頁是否另有更正稿。本次只測試上述兩個 PDF，沒有宣稱 2330 所有月份已全數驗證。

對全市場，沒有找到同樣統一、免費、兼具發布日與當時值的官方歷史版本來源。各公司 IR 頁面是否有月營收新聞稿、年份深度、PDF 精度和修訂告知機制都不一致。要求 2020–2026 全股票池都有四個比較月份，尚缺的是：

1. 逐股逐月原始發布日與數值。
2. 後續更正／重申報的新值、日期與舊值保留。
3. 下市、合併、更名、-KY 與當時未上市公司的原始公開頁。
4. 可重現的完整性清單，用來區分「當月不需申報」和「來源遺失」。

後來公開的更正稿必須新增同 `stock_id/month`、較晚 `known_on` 的第二版 record，不能覆寫舊 record。若封存只留當前 PDF 或公司原網頁替換舊附件，這份史料本身仍無法排除修訂幸存者偏差。

## 可執行建議

- 全市場主回測：當前仍不能標為「2020 起無前視營收績效」。MOPS/FinMind 當前值只能做有明示假設的探索敏感度版。
- 公開 PIT 小樣本：可先把 2330 擴張為 2019–2026 完整序列，每月從同期新聞稿取值與發布日，再用相鄰新聞稿的 comparison columns 交叉比對。此序列可用來驗證管線和時點邏輯，不代表全市場績效。
- 若一定要做全市場、逐版本、可稽核 PIT，需要另一個當年持續封存原始 MOPS 回應的資料供應商，或內部早已存在的每日快照，並且必須實證其 revision policy。本次在免費 FinMind/MOPS/官方公司 IR 範圍內沒有找到符合全市場要求的來源。
