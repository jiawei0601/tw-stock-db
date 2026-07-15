# HANDOFF

> 兩個 agent 交接的唯一現況真相。離開前更新，接手前先讀。

- 最後更新：Claude Code (Backend Architect) @ commit ad13d96（本次任務首次 commit）/ 2026-07-16
- 目前任務 / 目標：建立台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）
  標記，為未來「資金流向依板塊/族群視覺化網頁」鋪路的資料底層。
- 已完成：
  - `collectors/isin.py`：解析證交所 ISIN 頁面（`isin.twse.com.tw/isin/C_public.jsp`），
    取得上市/上櫃股票清單＋官方產業別文字。
  - `collectors/company_info.py`：解析 TWSE/TPEx 公司基本資料 OpenAPI，取得官方產業別
    數字代碼，與 isin.py 的結果依 stock_id 合併。
  - `build_db.py`：整批建置/刷新 `data/tw_stocks.db`（idempotent，重跑安全）。
  - `tests/test_db_content.py`：10 個測試全綠（市場檔數區間、2330/2454/2882 產業別核對、
    無重複 stock_id、industry_name 缺漏率 <1%、stock_groups 空表結構驗證、索引存在）。
  - `docs/data-sources.md`：記錄所有實測過的 endpoint 行為、編碼陷阱、過濾邏輯。
  - 資料庫實測結果：TWSE 1080 檔（1052 一般股票 + 28 創新板）、TPEx 891 檔，共 1971 檔，
    產業別缺碼僅 2 檔（皆為 2026-07-16 剛掛牌、官方公司基本資料 API 尚未收錄的新股）。
- 進行中（做到哪一步）：無，本次任務範圍內的項目已全部完成。
- 下一步（下一個任務，非本次範圍）：
  1. **族群/概念股標記**：`stock_groups` 表已建好結構（`stock_id`, `group_name`,
     `group_type`, `source`, `created_at`，PK 為 `(stock_id, group_name)`），但**尚未填
     任何資料**。這類資料沒有官方來源，需要另外設計資料來源（例如第三方概念股清單、
     新聞關鍵字聚類、或人工維護），建議另開任務處理，不要臨時塞進本專案的 build_db.py。
  2. **視覺化網頁**：讀 `data/tw_stocks.db` 的 `stocks`（未來還有 `stock_groups`）做
     「資金流向依板塊/族群」儀表板。本次任務完全沒有動這塊。
  3. （可選）**定期刷新排程**：目前資料庫是單次快照，若要保持最新，需要排程重跑
     `python build_db.py`（比照 tw-momentum-scanner 用 Windows 排程或 cron）。本次未設定。
- 關鍵決策 + 為什麼：
  - **主要產業別來源用 ISIN HTML 頁面而非 TWSE OpenAPI JSON**：OpenAPI
    (`t187ap03_L`/`mopsfin_t187ap03_O`) 的產業別欄位只有兩碼數字代碼（如 "24"），
    ISIN 頁面直接給文字（"半導體業"）。兩者交叉比對 1:1 完全一致、零衝突，所以資料庫
    同時保留兩者（`industry_code` + `industry_name`），數字代碼查 OpenAPI 補齊、
    文字用 ISIN 頁面（較即時，新股會先出現在這裡）。
  - **只取 ISIN 頁面的「股票」＋「創新板」區塊**：ISIN 頁面把 ETF/權證/特別股/TDR/REITs
    全部混在同一份清單，這些區塊的「產業別」欄位本身就是空的，且跟本專案「依產業別做
    資金流向分析」的目的不相容，故排除。「創新板」雖是獨立區塊，但實測後確認是真實普通股、
    有真實產業別分類、代號不與主板重複，故納入並標記 `market='TWSE'`（不新增第三種
    market 值，避免下游查詢邏輯多一種特例）。完整理由見 `docs/data-sources.md` 最後一節。
  - **`data/tw_stocks.db` track 進 git，不放進 .gitignore**：檔案只有約 300KB
    （純基本資料，無價量歷史），且之後的視覺化網頁預計直接讀這個檔案，track 進 repo
    比每次都要求使用者先跑 build_db.py 方便，之後刷新只要重新 commit 覆蓋即可。
  - **build_db.py 是整批刷新（DELETE + 整批 INSERT），不是逐筆 upsert**：因為兩個資料源
    都只提供「當下最新」快照、沒有增量更新語意，逐筆比對只會增加複雜度沒有實質好處。
  - **不用 pandas**：資料量小（不到 2000 列）、只需一次性批次寫入，直接用 `sqlite3`
    標準庫 + list of dict 就夠，保持依賴精簡（比照 tw-momentum-scanner 但更輕量）。
- 雷區 / 別碰：
  - ISIN 頁面編碼**必須**手動指定 `resp.encoding = "big5"`，requests 自動偵測或用預設
    utf-8 都會亂碼（頁面宣告 `charset=MS950`，等同 Big5 的微軟變體）。
  - ISIN 頁面「代號及名稱」欄位用**全形空格**（U+3000）分隔，不是半形空格，`.split(" ")`
    會切壞。
  - TPEx 系列 API 一律要瀏覽器 UA（`collectors/_http.py` 的 `BROWSER_UA` 已內建），
    不帶會被擋。
  - 兩個 OpenAPI（TWSE `t187ap03_L`、TPEx `mopsfin_t187ap03_O`）只回「最新一期全量」，
    沒有歷史或單檔查詢參數，也**不保證涵蓋所有 ISIN 頁面上的股票**（剛掛牌的新股可能
    暫時查不到，這種情況 `industry_code` 就是 NULL，屬預期行為，不是 bug）。
  - `python -m pytest` 前必須先跑過 `python build_db.py`，測試不會自己去打網路 API。
- 怎麼跑 / 怎麼測：
  ```bash
  cd C:\CLAUDE\investing\tw-stock-db
  pip install -r requirements.txt
  python build_db.py            # 整批刷新 data/tw_stocks.db
  python -m pytest tests/ -q    # 驗證資料庫內容
  ```
