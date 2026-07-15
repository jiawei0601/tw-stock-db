# HANDOFF

> 兩個 agent 交接的唯一現況真相。離開前更新，接手前先讀。

- 最後更新：Claude Code @ 2026-07-16（第二輪：encoding 修 bug + 概念股族群首批資料）
- 目前任務 / 目標：建立台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）
  標記，為未來「資金流向依板塊/族群視覺化網頁」鋪路的資料底層。
- 已完成：
  - `collectors/isin.py`：解析證交所 ISIN 頁面（`isin.twse.com.tw/isin/C_public.jsp`），
    取得上市/上櫃股票清單＋官方產業別文字。
  - `collectors/company_info.py`：解析 TWSE/TPEx 公司基本資料 OpenAPI，取得官方產業別
    數字代碼，與 isin.py 的結果依 stock_id 合併。
  - `build_db.py`：整批建置/刷新 `data/tw_stocks.db`（idempotent，重跑安全）。
  - `tests/test_db_content.py`：10 個測試全綠（市場檔數區間、2330/2454/2882 產業別核對、
    無重複 stock_id、industry_name 缺漏率 <1%、stock_groups 孤兒列檢查、索引存在）。
  - `docs/data-sources.md`：記錄所有實測過的 endpoint 行為、編碼陷阱、過濾邏輯。
  - 資料庫實測結果：TWSE 1080 檔（1052 一般股票 + 28 創新板）、TPEx 891 檔，共 1971 檔，
    產業別缺碼僅 2 檔（皆為 2026-07-16 剛掛牌、官方公司基本資料 API 尚未收錄的新股）。
  - **【第二輪】修正 `collectors/isin.py` 編碼 bug**：原本用 Python 標準 `'big5'` codec
    解碼 ISIN 頁面，但該 codec 不含「碁」等擴充字集字元，導致 13 檔股票（宏碁/建碁/啟碁/
    展碁國際/倚天酷碁-創/宏碁遊戲-創/安碁/安碁資訊/宏碁資訊/宏碁智新/洛碁/立碁/恒耀）
    名稱寫入資料庫時變成亂碼（U+FFFD replacement char）。改用 `'cp950'`（MS950 的正確
    Python codec 名稱）後重新 `build_db.py`，全庫掃描確認零亂碼殘留。
  - **【第二輪】`stock_groups` 首批資料**：使用者提供一份「2026 AI供應鏈概念股」整理
    （19 個族群、原始約 91 檔標的，含 CPO/先進封裝/半導體設備/散熱/機器人/低軌衛星/記憶體/
    車用電子/電動車/被動元件/CoWoS/CoPoS/水資源/能源重電等主題），已核對代號並寫入
    `stock_groups`（`group_type='concept'`, `source='使用者提供-2026 AI供應鏈概念股整理'`），
    目前 98 筆 (stock_id, group_name) 對應、涵蓋 91 檔不重複股票。
    核對時發現並修正原始清單 2 處代號錯誤（見下方關鍵決策）。
- 進行中（做到哪一步）：無，本次任務範圍內的項目已全部完成。
- 下一步（下一個任務，非本次範圍）：
  1. **持續補充族群/概念股**：`stock_groups` 是人工整理/使用者提供資料，非官方來源，
     之後有新的族群清單可比照同樣模式（核對代號後 `INSERT OR REPLACE`）繼續累積，
     不需改 schema。
  2. **視覺化網頁**：讀 `data/tw_stocks.db` 的 `stocks` + `stock_groups` 做
     「資金流向依板塊/族群」儀表板。本次任務完全沒有動這塊。
  3. **個股基本面/籌碼面資料整理**（使用者已提出但尚未執行，範圍待釐清）：使用者想針對
     `stock_groups` 名單內特定股票整理營收佔比、籌碼集中度、法人進出動態，需先確認
     範圍（全部 91 檔還是子集）、輸出形式（寫進 DB 新表還是報告文件）再動工。
  4. （可選）**定期刷新排程**：目前資料庫是單次快照，若要保持最新，需要排程重跑
     `python build_db.py`（比照 tw-momentum-scanner 用 Windows 排程或 cron）。本次未設定。
     注意：重跑 `build_db.py` 只會刷新 `stocks` 表，不會動 `stock_groups`（見 Interface
     Contract），概念股標記資料是安全的。
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
  - **概念股族群清單代號核對後才寫入，不照單全收**：使用者提供的整理文字中，「力麒」
    標成代號 5536（實際 5536 是聖暉，力麒正確代號是 5512）、「山林水」標成代號 5412
    （查無此代號，正確是 8473）。兩處都先用 `stocks` 表反查、WebSearch 核實正確代號後
    才寫入，並保留 `stocks` 表的官方名稱為準（使用者提供的名稱僅供比對參考）。
- 雷區 / 別碰：
  - ISIN 頁面編碼**必須**手動指定 `resp.encoding = "cp950"`（不是 `"big5"`——Python
    標準 `'big5'` codec 不含「碁」等擴充字集字元，會 decode 失敗或被 requests 靜默轉成
    U+FFFD 亂碼；`'cp950'` 才是 ISIN 頁面實際宣告的 MS950 對應的正確 Python codec 名稱，
    這是本專案第一輪曾踩過的真實 bug，見上方「已完成」第二輪修正紀錄）。
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
