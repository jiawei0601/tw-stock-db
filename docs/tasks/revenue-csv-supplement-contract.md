# 官方CSV補洞合約（Codex，2026-09-09）

實際來源：MOPS HTML的「另存CSV」form POST到 `https://mopsov.twse.com.tw/server-java/FileDownLoad`，欄位step=9,functionName=show_file2,filePath=/t21/otc/,fileName=t21sc03_111_2.csv。

已下載body及request headers在 `backtest/momentum_rerun_20260909_revenue_audit/official_csv/2022-02-otc.raw`、`request.json`；UTF8 CSV含824列、國內外合併，與截斷HTML可解析405列交集營收完全相符。

主線已完成可執行 `revenue_archive_csv.parse_csv(body: bytes, month: str) -> list[dict]`，輸出stock_id/month/revenue_twd/page_date/raw。仟元轉元，月份驗證、代號去重；page_date為出表日，不能叫known_on。

委派責任：只修改 `revenue_archive_csv.py`、新增 `tests/test_revenue_archive_csv.py`，不得修改主線backfill程式或其他檔案，不得commit。

新增 `store_csv(conn: sqlite3.Connection, body: bytes, month: str, market: str, request: dict, fetched_at: str) -> int`：建立獨立csv_sources與csv_revenues兩表，不改HTML pages/revenue_snapshots；來源key以request的url/form組合可重現雜湊識別，保留實際POST參數、原始body、body SHA256、fetched_at、market/month。csv_revenues存source_key/stock_id/month/revenue_twd/page_date/raw_json，主鍵source_key+stock_id。單一transaction原子寫入，同來源重複匯入idempotent；同來源替換清除舊列但不碰其他來源。不得把PDF或近似公告時間加入此API。

錯誤：缺必要欄位、空資料、HTML冒充CSV、月份錯誤、非整數營收或重複代號要ValueError，寫庫前驗證；負值照實保留且缺值不可填0。請改善parse_csv的錯誤一致性。

驗收：真實824列本機樣本可解析；測試千元換算、重複/缺月/錯欄位、source hash與raw保留、重跑不增列、錯誤不污染DB。只在測試in-memory DB寫入，主線之後負責真實匯入。
