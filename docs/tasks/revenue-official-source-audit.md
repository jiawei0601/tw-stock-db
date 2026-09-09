# 月營收官方公告日期與更正版本來源稽核

日期：2026-09-09；範圍：2018–2026 官方公開來源的實際 HTTP 探測。HTTP 請求共 19 次，單次逾時上限 30 秒；未繞過封鎖、未使用登入或非公開憑證。原始回應與 headers 存於 `backtest/momentum_rerun_20260909_revenue_audit/official/`。

## 結論

**可以建立「更正 ledger」，但官方公開來源不足以建立全市場「初次申報時點 ledger」。因此目前不能把 MOPS 歷史月營收做成符合 `availability_basis: version_evidence` 的 2020 起 production point-in-time collector。**

MOPS 單一公司月營收查詢 `ajax_t05st10_ifrs` 可讀指定公司、指定年月的營收數字，但回傳查詢當下的單一最新版本，沒有逐公司申報日期、申報時間、版本號或更正歷程。MOPS 歷史重大訊息 `ajax_t05st01` 則能對已發布更正重訊的案例提供精確發言日、發言時間、更正前值與更正後值。兩者可驗證更正發生後的新值，仍無法證明更正前版本最早在何日可取得，也無法證明沒有更正的普通月營收最早在何日可取得。

證券交易法第 36 條與交易所申報規則的「次月十日前」是申報期限，不是每家公司實際公告日。把所有公司一律設為次月 10 日或 11 日，是固定延遲假設，不是逐筆版本證據；特殊延期、假日順延、遲報與期限後更正也使它不能冒充真實 release date。

## 已驗證的官方路徑

### 1. 單一公司指定年月的最新月營收

可匿名 GET：

```text
https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs
  ?TYPEK=all
  &co_id={stock_id}
  &encodeURIComponent=1
  &firstin=1
  &inpuType=co_id
  &isnew=false
  &month={MM}
  &off=1
  &step=1
  &year={ROC_YEAR}
```

實測 2330、民國 113 年 3 月（2024-03）HTTP 200，頁面回：

- 公司與市場：上市公司台積電；
- 資料年月與單位：民國 113 年 03 月、新台幣仟元；
- 本月營收 195,210,804；去年同期 145,408,332；
- 增減金額、增減百分比、本年累計、去年累計、備註。

原始回應：`03_mopsov_ajax_2330_2024m03.html`，SHA-256 `036069E47992DC4CE3A4EA0A6E5AE3496BCCACEAC3E6BEA2DB9F2FF8DBE32B1D`。

**缺少欄位**：申報日期、申報時間、版本號、原始值、修改時間、撤銷／更正旗標。HTTP `Date` 是本次查詢的伺服器回應時間，不能當歷史申報時間。

### 2. 指定公司、年度的歷史重大訊息清單

可匿名 GET：

```text
https://mopsov.twse.com.tw/mops/web/ajax_t05st01
  ?TYPEK=all
  &co_id={stock_id}
  &encodeURIComponent=1
  &firstin=1
  &inpuType=co_id
  &off=1
  &step=1
  &year={ROC_YEAR}
```

每列有公司代號、簡稱、發言日期、發言時間、主旨，詳細資料按鈕另帶 `seq_no`、`spoke_date`、`spoke_time`、`co_id`、`TYPEK`。這些鍵可唯一定位同公司同日多則重訊。

### 3. 更正重訊詳細內容

對 `/mops/web/ajax_t05st01` POST；已驗證的必要定位欄位為：

```text
TYPEK={sii|otc|rotc|pub}
co_id={stock_id}
spoke_date={YYYYMMDD}
spoke_time={HMMSS|HHMMSS}
seq_no={integer}
step=2
off=1
```

回傳詳細頁可含發言日期、發言時間、主旨、事實發生日、發生緣由、報表名稱、更正前金額／內容、更正後金額／內容及因應措施。內容是半結構文字，不是固定 JSON 欄位，parser 必須保留 raw body 並把無法可靠解析的案件標成人工檢核，不能猜數字所屬月份。

## 可核對樣本

### 2018：上市公司 3002 歐格

重大訊息清單回傳：2018-10-09 17:50:34，主旨「更正本公司107年度9月合併營收公告」。詳細頁回傳：

- 更正資訊：107 年 9 月每月營收公告；
- 更正前本月營收：33,958 仟元；累計 243,308 仟元；
- 更正後本月營收：54,960 仟元；累計 462,641 仟元；
- 原因：誤植為單一母公司營收金額。

清單 URL：

```text
https://mopsov.twse.com.tw/mops/web/ajax_t05st01?TYPEK=all&co_id=3002&encodeURIComponent=1&firstin=1&inpuType=co_id&off=1&step=1&year=107
```

原始檔 `04_mopsov_major_3002_2018.html`，SHA-256 `2E1563EA3B262B1789CB861E64E3B62F02E8CF4E7AD7F10272F1698B02D0A657`；詳細 POST 回應 `06_mopsov_correction_3002_20181009_post.html`，SHA-256 `EF4E92A2D48C50DC874D06FAE941AEE4F23F551EE1D8E694295488333189353C`。

2018-10-09 是此更正版本的 `known_on` 證據。它不能反向證明 33,958 的最早 `known_on`；若沒有初次申報時點的另一份證據，舊值只能留在更正 ledger 中，不能直接輸入 `RevenueFilter`。

### 2019：公開發行公司 3369 鐵研

重大訊息清單回傳：2019-06-14 11:40:03，主旨「本公司更正申報108年05月合併營收公告」。詳細頁回傳：

- 更正前本月營收：51,210 仟元；累計 239,003 仟元；
- 更正後本月營收：48,906 仟元；累計 236,699 仟元。

同一公司、同一月份的 `ajax_t05st10_ifrs` 現在只回本月營收 48,906，沒有申報日期或前值；這直接驗證月營收頁是最新狀態，而非版本歷史。

清單 URL：

```text
https://mopsov.twse.com.tw/mops/web/ajax_t05st01?TYPEK=all&co_id=3369&encodeURIComponent=1&firstin=1&inpuType=co_id&off=1&step=1&year=108
```

最新月營收 URL：

```text
https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs?TYPEK=all&co_id=3369&encodeURIComponent=1&firstin=1&inpuType=co_id&isnew=false&month=05&off=1&step=1&year=108
```

原始檔與 SHA-256：

- `13_mopsov_major_3369_2019.html`: `2E498A21AFC4C9A8650FABA494BB97B12C6116DA6D725AC8A6A83BAFCD7BDD0B`；
- `14_mopsov_correction_3369_20190614_post.html`: `51462B2118FD1290B165FBAC1995C18D38D5E1733DFE7A9B93EDE67EACB114A2`；
- `15_mopsov_monthly_3369_2019m05.html`: `816E2BD607E18841C14AB2BB4AAF5A8735422456BE031AB1DD7B3DEE76D1EFA6`。

2019-06-14 是 48,906 這個更正版本的可用日證據。51,210 的初次可用日仍未知。

## 更正處理規則與資料模型

若日後取得初次申報事件 feed，可用下列合併方式；在取得前只能生產 corrections-only ledger：

1. `t05st10_ifrs` 或主線 `nas/t21` snapshot 提供查詢當下最終值，保留 raw body、抓取時間與 SHA；不可由頁面出表日產生歷史 `known_on`。
2. `t05st01` 主旨先以「更正」與「營收／營業收入」產生候選；詳細頁必須確認 `更正資訊項目/報表名稱` 指向月營收。
3. 一則重訊可能更正單月、累計、去年同期或備註，也可能同時更正多月。只有能從原文唯一解析出 `(stock_id, month, old_revenue, new_revenue)` 的案件才自動入帳。
4. 對數字更正，新增版本 `revenue=new_revenue`、`known_on=spoke_date`、`source` 指向重訊唯一鍵；不要覆寫舊版本。由於策略規定 `known_on < signal_day`，不必把盤中時間擬造成日期。
5. `old_revenue` 只證明「在更正之前存在過」，不證明其最早公布日。沒有初次申報證據時，不能自行建立舊版本的 `known_on`。
6. 文字備註更正但金額不變時，可存稽核事件，不新增營收數值版本。
7. 若多次更正，依 `(spoke_date, spoke_time, seq_no)` 排序串成版本鏈；相鄰事件的 `old_revenue` 應等於前一事件的 `new_revenue`，不一致即隔離檢查。

建議 corrections-only schema：

```text
stock_id, revenue_month, correction_known_on, correction_time,
seq_no, market, old_revenue, new_revenue,
subject, raw_text, source_url, raw_sha256, parse_status
```

這張表不能直接宣告 `availability_basis=version_evidence`；必須再 join 真實初次申報事件才完整。

## 覆蓋與生產限制

- `t05st01` 指定公司可查整年，但不指定公司時，官方回覆「未指定公司代號時，僅能查詢單日重大訊息」。原始檔 `09_mopsov_major_all_2018.html`，SHA-256 `FFDFE4679C7FF0F9DE9DEEF8E3FF9EA54846BE6CFD73072E4F5B7B2C4C8979BA`。
- 逐日查 2018-01-01 至 2026-09-09 約 3,174 個日曆日，尚需逐筆打詳細頁；逐公司逐年對約 2,000 檔會更高。這不符合本次低請求探測，也不宜直接當每日 collector 的歷史回補方法。
- 搜尋到的舊 RSS 命名 `nas/rss/mopsrss201810.xml` 在 `mops.twse.com.tw` 與 `mopsov.twse.com.tw` 實測均 404，不能用每月一檔枚舉歷史更正。證據檔為 `10_mops_rss_201810.*` 與 `11_mopsov_rss_201810.*`。
- 新主站 `mops.twse.com.tw/mops/web/t05st10_ifrs` 對兩次匿名初始 GET 轉到錯誤頁；舊站 `mopsov.twse.com.tw` 的明確查詢 URL 可成功。這是可用性風險，不應用高頻重試或繞過防護處理。
- 彙總查詢候選 `/mops/web/ajax_t21sc03_ifrs` 以 GET 與表單 POST 查 `TYPEK=otc&year=111&month=02` 均由官方主機空連線回覆（curl 52）；依兩次失敗停止，沒有把它列為 2022-02 截斷 archive 的替代來源。逐公司 `ajax_t05st10_ifrs` 可作已知 stock ID 的小量補洞，但一家公司一請求、沒有歷史市場完整名冊，不能宣稱全市場替代。
- `TYPEK` 包含 `sii`、`otc`、`rotc`、`pub`；只查上市與上櫃會漏掉曾處於興櫃／公開發行階段、之後上市櫃的歷史事件。回測若納入上市前暖機資料，市場身分必須按事件當時值保留。
- `nas/t21` 的 `_0.html` 是國內發行人頁，`_1.html` 另含 KY 等外國發行人；只抓 `_0` 會系統性漏股。即使 union `_0`、`_1`，歷史頁也可能只保留目前仍存在的公司／代號：已下市候選與頁面內容的落差尚未被證明為完整 delisting history。因此 snapshot 的 coverage 應按「該 URL 當下回傳列」報告，不得稱為完整歷史股票池。
- 主線 `nas/t21` snapshot 即使某月頁面本身完整，仍只證明抓取當下頁面內容；頁面出表日或現在的 HTTP `Last-Modified` 都不是 2018–2026 各公司申報日。已觀察到 2022-02 TPEx `_0` 連續兩次 HTTP 200 但 body 截斷、缺 `</html>`，collector 必須以結尾／列完整性檢查拒收 HTTP 200 的殘缺頁。

## 官方規範證據

TWSE 的《公開發行公司資訊申報注意事項》（108 年 3 月）第 13 頁說明：月營收申報期限為次月 10 日以前（例假日與特殊情事另有處理）；更正步驟是先以第 9 款格式 1 發布重大訊息，敘明更正前、後數據，再更正申報。官方 PDF：

```text
https://wwwc.twse.com.tw/staticFiles/news/event/ff80808167ee6fdf0169be45e16c09db.pdf
```

下載檔 `16_twse_2019_filing_guide.pdf`，SHA-256 `7C5502CBCD4EAB7F391C40E24A5375B1B8F9A4CEA8E3AA345C674623D595CD1E`。這支持把重大訊息用作更正證據，但不支持把期限當作每家公司實際 release date。

## 生產判定與下一步

目前判定：

- **月營收最終值 collector：可行**，主線已由 `nas/t21` 做 snapshot 證據庫；
- **更正 ledger：技術上可行，但歷史全量枚舉成本高，且詳細文字需保守 parser 與人工隔離佇列**；
- **2020 起真正 PIT、逐公司初次公布日＋所有修訂版本 collector：官方已探測公開端點下不可行**。

若要解除 blocker，所需的不是再抓更多 `t05st10_ifrs` 或 `nas/t21` 頁，而是取得下列任一資料：

1. 官方提供的歷史申報 log／資料庫匯出，逐筆至少含公司代號、營收年月、申報日期（或日期時間）、申報值與版本／更正序號；或
2. 能證明當時內容的逐日官方 snapshot archive，且涵蓋全市場、可識別當日首次出現與後續變更；或
3. 另一個有可稽核來源鏈的授權資料集，明確保存原始公告時間與歷史版本，而非把 ingestion time 或月份標籤當公告日。

在此之前，若研究仍要往前走，只能另做明確命名的「法定期限延遲假設」敏感度分析；不得把它輸出成 `availability_basis: "version_evidence"`，也不得稱為已排除公告與修訂偏誤。


## 主線後續補充：官方CSV可補數值缺頁

主線從官方HTML內「另存CSV」表單取得合法下載方法：POST `https://mopsov.twse.com.tw/server-java/FileDownLoad`，step=9、functionName=show_file2、filePath=/t21/otc/、fileName=t21sc03_111_2.csv。HTTP200，UTF8資料824筆，含國內外；與截斷頁可讀405筆營收全數一致。這解決該月數值頁截斷，不解決歷史首次公告日期問題。原始CSV/request在 `backtest/momentum_rerun_20260909_revenue_audit/official_csv/`。
