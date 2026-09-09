# 營收資料補齊與歷史版本查核

2026-09-09，Codex；使用者明確要求開subagent補齊資料。

## 分工

- 主線：`backfill_revenue_archive.py` 與 `tests/test_backfill_revenue_archive.py`，補抓官方月營收全市場封存數值，保存原始頁、雜湊與下載時間，最後彙整覆蓋率。
- `revenue_official_dates`：官方公告／修訂日期來源查核，僅寫 `revenue-official-source-audit.md` 與其原始樣本。
- `revenue_vintage_sources`：FinMind版本語義／公開原始歷史公告查核，僅寫 `revenue-vintage-source-audit.md` 與其樣本。
- subagent使用可用GPT-5.6 Sol；Spark不在本工具可用清單。主線整合與提交，不讓多個agent共同改同一模組。

## 下載與資料合約

執行 `python backfill_revenue_archive.py`，預設2018-01至2026-08（104個月×2市場×國內／國外，共416個HTML頁）。來源是既有collector使用的MOPS `nas/t21/sii` 與 `nas/t21/otc` 頁面；`_0.html`是國內、`_1.html`是KY／外國發行人。原下載器只抓_0造成漏抓，這次已補_1。

獨立資料庫 `data/momentum_pit/revenue_archive/snapshots.db`：

- `pages`：URL、market、month、fetched_at(UTC)、原始body、SHA256、row_count、error。
- `revenue_snapshots`：URL、stock_id、month、revenue_twd、page_date、fetched_at、原始解析列JSON。
- 不套用現有stocks或FinMind候選名單作二次過濾，保留來源頁上的全部公司；但現行歷史封存頁本身未完整保留下市公司，不代表已消除存活偏誤；原表千元換算為元，缺漏維持NULL。
- 有效頁立即commit；已成功URL續跑跳過。空頁、截斷HTML、重複代號不算成功，連續兩頁失敗停止，避免持續請求異常來源。
- `audit.json` 記錄預期頁數、已完成頁數、筆數、股票數、月份及失敗，不將下载完整等同歷史時點認證。
- 頁面出表日期與實際下載時間分欄，**不匯出為revenue_filter的歷史known_on**；需另查到公告及原始數值版本證據。

2022-02 TPEx國內外兩個HTML均截斷，另從官方CSV下載表單取得同月國內外合計824列補洞，原始檔保留；獨立csv_sources/csv_revenues表保存，不假裝修好了原HTML。

資料庫是目前封存頁的快照，不是歷史版本庫。歷史頁面也可能已更正；下載數字完整，仍不能宣稱排除修訂偏誤。

## 驗證

相關測試已通過，最後整合共123項通過：原始頁雜湊、千元換算、保留非現存名單股票、缺值、空頁拒絕、重複代號拒絕，以及營收濾網／既有策略測試。

## 最終狀態

414個HTML完整；另2個截斷頁（2022-02TPEx國內外）由官方CSV824列補洞，已真實寫入同一獨立SQLite的csv_sources/csv_revenues。

- 合併去重190,905個股票月份、1,975個代號、2018-01至2026-08共104個月；213筆負营收原值保留。
- 原FinMind候選2,162檔中涵蓋1,974檔、缺188檔；缺口中92檔有下市紀錄。另包含1檔來源頁的非候選代號912000，未強制套目前名單過濾。
- 2026-07有1,975檔，2026-08目前只有921檔，8月不標成完整月份。
- `data/momentum_pit/revenue_archive/audit.json`是HTML下載稽核，故仍報414/416；`combined_audit.json`另外記錄CSV已補兩個HTML缺頁、合併數量及明確缺口。不得把頁面數補齊視為全股票池或PIT認證。
- 414個有效HTML的股票列集合與解析入庫完全相符；原解析器漏掉的無nowrap成長率欄已修正並重解析。抽驗2330的103個重疊月份，官方封存數值與先前FinMind probe全數一致；不是全股票的全面交叉驗證。
- 營收主庫 `data/tw_stocks.db` 未覆寫；新快照不塞入歷史known_on。Hetzner與原回測结果未改。

CSV重現匯入：以 `revenue_archive_csv.store_csv` 讀本機official_csv的raw與request.json，month='2022-02', market='TPEx'，寫snapshot.db；fetched_at用原始檔存檔時間（UTC）。同來源重跑不增列，SQLite外鍵開啟測試及寫入失敗回滾測試均通過。

重跑HTML：`python backfill_revenue_archive.py`，有效URL快取跳過；先前失敗預設不重試，需經診斷後明示 `--retry-failed`。解析器升級時用 `--reparse` 重讀原始頁。來源失敗頁不進合併有效數據，CSV補洞另有來源證據。

兩個subagent已找到官方更正前後值／時間，以及TSMC同期新聞稿四筆可驗算版本樣本。來源報告分見 `revenue-official-source-audit.md` 與 `revenue-vintage-source-audit.md`。全市場原始公告與完整修訂時點仍未補齊，不能執行認證的2020起營收策略績效。
