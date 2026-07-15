# HANDOFF

> 兩個 agent 交接的唯一現況真相。離開前更新，接手前先讀。

- 最後更新：Claude Code @ 2026-07-16（第三輪：91 檔概念股的月營收/籌碼集中度/三大法人動態）
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
  - **【第三輪】91 檔概念股的三種延伸資料**：新增 `collectors/revenue.py`、
    `collectors/shareholding.py`、`build_fundamentals.py`、`build_institutional_summary.py`，
    只針對 `SELECT DISTINCT stock_id FROM stock_groups`（動態查詢，不寫死清單）這批股票：
    - **月營收年增率與趨勢**（`monthly_revenue` 表）：TWSE opendata `t187ap05_L` +
      TPEx opendata `mopsfin_t187ap05_O`，91/91 檔全數涵蓋（官方 opendata 全量下載後
      本地過濾，不逐檔打 API）。含當月營收、月增率、年增率（`yoy_pct`，即使用者所稱
      「年增率」）、累計營收年增率、備註。只有「最新一期」，無歷史序列（來源限制）。
    - **籌碼集中度**（`shareholding_concentration` 表）：TDCC 集保結算所股權分散表
      opendata `id=1-5`，91/91 檔全數涵蓋。15 級距明細存 `levels_json`，另計算
      `pct_gt_400zhang`／`pct_gt_1000zhang` 兩個代理集中度指標（>400張／>1000張持股人
      合計占集保庫存比例）。**每週更新一次、只有當週最新一期快照，無歷史**（來源限制）。
    - **三大法人近期買賣超動態**（`institutional_flow_summary` + `institutional_flow_daily`
      表）：讀取（唯讀）`C:\CLAUDE\tw_cache\institutional.db`（tw-momentum-scanner 專案
      既有共用資產，未複製/搬動），彙總近 5/20/60 日外資/投信/自營商買賣超累計 + 外資
      連續買/賣超天數（streak）。**91 檔中僅 71 檔涵蓋**（institutional.db 本身只涵蓋
      tw-momentum-scanner 篩選過的 696 檔動能股歷史清單，不是全市場，本專案 91 檔中有
      20 檔從未進過那份清單，查無資料，如實留空不臆測，缺漏清單見下方雷區）。
      `institutional_flow_daily` 額外保留近 60 個交易日逐日明細（明確時間窗，不無限
      累積），供未來視覺化畫走勢/sparkline 用。
    - 三個測試維度合計新增 `tests/test_fundamentals_content.py`（14 個測試），全綠；
      全專案測試 `python -m pytest tests/ -q` 共 24 個測試全綠。
    - `docs/data-sources.md` 新增第 6-9 節記錄新 endpoint 實測結果（欄位、陷阱、限制）。
- 進行中（做到哪一步）：無，第三輪任務範圍內的項目已全部完成。
- 下一步（下一個任務，非本次範圍）：
  1. **持續補充族群/概念股**：`stock_groups` 是人工整理/使用者提供資料，非官方來源，
     之後有新的族群清單可比照同樣模式（核對代號後 `INSERT OR REPLACE`）繼續累積，
     不需改 schema。`build_fundamentals.py`/`build_institutional_summary.py` 都是動態
     查詢 `stock_groups`，族群名單擴充後直接重跑即可自動涵蓋新股票，不需改程式。
  2. **視覺化網頁**：讀 `data/tw_stocks.db` 的 `stocks` + `stock_groups` +
     `monthly_revenue` + `shareholding_concentration` + `institutional_flow_summary`/
     `institutional_flow_daily` 做「資金流向依板塊/族群」儀表板。三輪任務都完全沒有
     動這塊。
  3. **補齊 institutional.db 缺漏的 20 檔法人資料**（若使用者需要）：這 20 檔從未進過
     tw-momentum-scanner 的動能股篩選清單，若要補齊，需另外對 TWSE/TPEx 官方三大法人
     買賣超 API（例如 TWSE `fund/T86`）抓歷史，寫一個新 collector，不能靠共用資料庫。
  4. （可選）**定期刷新排程**：目前三個資料庫產出都是單次快照，若要保持最新，需要排程
     重跑 `python build_db.py` + `python build_fundamentals.py` +
     `python build_institutional_summary.py`（比照 tw-momentum-scanner 用 Windows
     排程或 cron，且 `build_institutional_summary.py` 的新鮮度上限取決於
     `tw_cache/institutional.db` 本身多久更新一次，不受本專案排程頻率控制）。本次未設定。
     注意：三個 build 腳本互不覆寫彼此的表，可任意順序/頻率重跑。
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
  - **【第三輪】月營收/籌碼集中度做成快照表（覆蓋式），三大法人動態也做成快照表**：
    三個來源本身都只提供「當下最新」（月營收=最新一期、籌碼集中度=當週最新一期、
    institutional.db 的近 5/20/60 日彙總=以查詢當下的 MAX(date) 為基準往回算），沒有
    「使用者自己截取歷史區間」的語意，做成 append-only 時序表只會白白累積、卻沒有
    真正可比較的歷史序列（因為來源本身沒給歷史）。若未來要長期追蹤趨勢，正確做法是
    **排程定期執行 build 腳本、每次都存一份新快照到獨立檔案或加時間戳記分區**，而不是
    現在就把這幾張表改成 append-only（那樣做只是徒增複雜度、沒有實質效益）。
    唯一例外是 `institutional_flow_daily`：因為 institutional.db 本身有逐日歷史，這裡
    不是「來源只給最新」而是「本專案主動選擇只保留近 60 日」，所以它是「有明確時間窗
    的覆蓋式快照」，不是無限累積的時序表，跟前述兩種快照的差別在於它保留了一小段
    歷史窗口而非單一時間點。
  - **`institutional_flow_summary` 是「有資料才寫一列」，不是「91 列、缺資料留 NULL」**：
    91 檔中查無資料的 20 檔，`institutional_flow_summary` 裡完全沒有那一列（不是一整列
    NULL）。這個設計選擇是因為「查無資料」跟「有資料但欄位是 0」是不同語意——如果留一整列
    NULL，容易被誤讀成「近期買賣超為 0」；不寫入該列，查詢端用 LEFT JOIN 就能自然分辨
    「這檔沒有法人動態資料可看」，比留 NULL 列更誠實。
  - **籌碼集中度指標選擇「留 15 級距原始明細 + 算 2 個代理指標」，不是只留代理指標**：
    只留 `pct_gt_400zhang`／`pct_gt_1000zhang` 兩個數字最省空間，但未來如果想換一個
    門檻（例如改看 >600張）就要重新抓資料；`levels_json` 把 15 級距完整明細都留著，
    换門檻只要重新查詢 JSON 就好，不必重新打 TDCC API（反正 CSV 本身就是全市場一次
    下載，多存幾個欄位幾乎不增加成本）。
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
  - **【第三輪】TDCC CSV 的證券代號欄位是固定 6 碼、右側補半形空格**（`"2330  "`），
    比對前一定要 `.strip()`，忘記會導致 91 檔全部查詢回 0 筆（本專案實測時真的踩過，
    見 `docs/data-sources.md` 第 8 節）。
  - **【第三輪】institutional.db 的 stock_id 帶 yfinance 風格市場後綴**
    （TWSE 股票 `.TW`、TPEx 股票 `.TWO`，例如 `2330.TW`），不是純 4 碼代號，用純代號查詢
    一樣會 100% 落空（本專案實測時也踩過，見 `docs/data-sources.md` 第 9 節）。
  - **【第三輪】institutional.db 的「最新日期」不等於「今天」**：實測 2026-07-16 執行
    `build_institutional_summary.py` 時，`institutional.db` 的 `MAX(date)` 是
    `2026-05-28`（約 1.5 個月前），所以「近 5/20/60 日」是以 2026-05-28 往回算，不是
    以執行當下的日曆日往回算。每次重跑都要用 `SELECT MAX(date)` 查詢實際值，程式已經
    這樣做，但**看到 `institutional_flow_summary.latest_date` 是舊日期不要當成 bug**，
    先去查 `tw_cache/institutional.db` 本身上次更新是什麼時候。
  - **【第三輪】`institutional.db` 只准讀取**：`build_institutional_summary.py` 用
    `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` 開唯讀連線，任何修改這個腳本
    的人都不可以把它改成可寫連線，也不可以把這個檔案複製進本 repo 的 `data/` 目錄
    （它是跨專案共用資產，屬於 `tw-momentum-scanner`）。
- 怎麼跑 / 怎麼測：
  ```bash
  cd C:\CLAUDE\investing\tw-stock-db
  pip install -r requirements.txt
  python build_db.py                       # 整批刷新 stocks + stock_groups
  python build_fundamentals.py             # 91 檔月營收 + 籌碼集中度快照
  python build_institutional_summary.py    # 91 檔三大法人近期動態快照（讀 tw_cache/institutional.db）
  python -m pytest tests/ -q               # 驗證資料庫內容（24 個測試）
  ```
