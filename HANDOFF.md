# HANDOFF

> 兩個 agent 交接的唯一現況真相。離開前更新，接手前先讀。

- 最後更新：Claude Code @ 2026-07-16（第四輪：三大法人動態改用官方 API 直抓，取代
  `tw_cache/institutional.db` 依賴，91 檔全數涵蓋）
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
  - **【第四輪】三大法人動態改用官方 API 直抓，取代 `tw_cache/institutional.db` 依賴**：
    新增 `collectors/institutional_official.py`（TWSE `fund/T86` + TPEx
    `3itrade_hedge_result.php`，皆為「依日期查詢、當日全市場」endpoint），改寫
    `build_institutional_summary.py` 為逐日往回查詢、TWSE/TPEx 各自湊滿 60 個交易日
    （不再讀取 `tw_cache/institutional.db`，該共用資料源只涵蓋 tw-momentum-scanner
    篩選過的 696 檔動能股清單，導致第三輪版本 91 檔中只有 71 檔有資料，且新鮮度停留
    在該資料源上次更新的舊日期）。
    - **實測結果：91/91 全數涵蓋**（含第三輪缺漏的全部 20 檔），`latest_date` 集中在
      2026-07-15（88 檔）/ 2026-07-14（3 檔，個別股票當日停牌所致，非 bug），落差
      在 1 個交易日內，資料新鮮度一致，達成本輪任務目的。
    - 兩個 endpoint 的欄位對應皆用「同日交叉核對已知來源數字」的方式實測確認（TWSE
      用 2330 台積電驗算三大法人合計欄位是否等於三個子欄位加總；TPEx 用 3105 穩懋
      核對 `tpex_3insti_daily_trading` OpenAPI 的英文鍵名數字），不是用猜的，過程與
      精確欄位索引記錄在 `docs/data-sources.md` 第 9-10 節。
    - `tests/test_fundamentals_content.py` 更新三大法人動態相關測試：
      `test_institutional_flow_summary_covers_most_target_stocks`（取代舊版「只要求
      >0」的寬鬆測試，改要求 >=90% 覆蓋率）、新增
      `test_institutional_flow_summary_freshness_consistent`（MIN/MAX latest_date
      落差 <=5 天，直接驗證本輪任務的核心目的）、`test_institutional_flow_known_stock_2330`
      加強為量級合理性檢查（單日均量 <2 億股，防欄位錯位這類 bug）。全專案測試共
      25 個，全綠。
    - **順帶修正 `collectors/_http.py` 的一個既有缺口**：原本只有 `requests.Timeout`
      會走退避重試，`requests.ConnectionError`（涵蓋 DNS 解析失敗等暫時性網路問題）
      會直接被歸類成不可重試而整個中止。本輪任務因為要在單次執行內對 TWSE/TPEx
      各發送 60~90 次請求，實測真的觸發過一次 DNS 解析失敗（`getaddrinfo failed`）
      導致長迴圈中途整個腳本失敗，修正後 `ConnectionError` 與 `Timeout` 一樣視為
      retriable、走相同的 5/20/60 秒退避重試。這是本輪任務發現的真實 bug，不是
      臆測性修改。
- 進行中（做到哪一步）：無，第四輪任務範圍內的項目已全部完成。
- 下一步（下一個任務，非本次範圍）：
  1. **持續補充族群/概念股**：`stock_groups` 是人工整理/使用者提供資料，非官方來源，
     之後有新的族群清單可比照同樣模式（核對代號後 `INSERT OR REPLACE`）繼續累積，
     不需改 schema。`build_fundamentals.py`/`build_institutional_summary.py` 都是動態
     查詢 `stock_groups`，族群名單擴充後直接重跑即可自動涵蓋新股票，不需改程式。
  2. **視覺化網頁**：讀 `data/tw_stocks.db` 的 `stocks` + `stock_groups` +
     `monthly_revenue` + `shareholding_concentration` + `institutional_flow_summary`/
     `institutional_flow_daily` 做「資金流向依板塊/族群」儀表板。四輪任務都完全沒有
     動這塊。
  3. （可選）**定期刷新排程**：目前三個資料庫產出都是單次快照，若要保持最新，需要排程
     重跑 `python build_db.py` + `python build_fundamentals.py` +
     `python build_institutional_summary.py`（比照 tw-momentum-scanner 用 Windows
     排程或 cron）。本次未設定。注意：三個 build 腳本互不覆寫彼此的表，可任意順序/
     頻率重跑；但 `build_institutional_summary.py` 第四輪起改成官方 API 直抓，單次
     執行約需 4-6 分鐘（每次重跑都是完整的 60 交易日 x 2 市場逐日查詢，沒有增量抓取，
     若排程頻率提高到每日一次，屬合理用量，不建議抓更頻繁避免對官方站台造成不必要負擔）。
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
    查無資料的股票，`institutional_flow_summary` 裡完全沒有那一列（不是一整列
    NULL）。這個設計選擇是因為「查無資料」跟「有資料但欄位是 0」是不同語意——如果留一整列
    NULL，容易被誤讀成「近期買賣超為 0」；不寫入該列，查詢端用 LEFT JOIN 就能自然分辨
    「這檔沒有法人動態資料可看」，比留 NULL 列更誠實。**【第四輪】改用官方 API 後這個
    設計理由變成理論性保障**（實測 91/91 全數涵蓋，目前沒有任何一檔真的觸發這個分支），
    但保留這個行為是因為官方 API 未來仍可能對個別股票（例如剛終止上市、興櫃轉上市初期）
    查無資料，不能假設永遠 91/91。
  - **【第四輪】改用官方 API 後，法人動態抓取視窗從「90 日 lookback 緩衝 + 60 日儲存」
    簡化為「直接抓 60 個交易日」**：第三輪讀 `institutional.db`（本地 SQLite 查詢
    幾乎零成本）時，用 90 日緩衝抓取範圍是為了讓 streak 計算不會恰好卡在 60 日視窗邊界
    低估連續天數。第四輪改成逐日打官方 API（每個交易日一次網路請求，有節流成本），
    抓 90 日會比使用者要求的「~60 個交易日」多出 50% 請求量，故簡化為直接抓 60 日，
    streak 若剛好在第 60 天仍是買超/賣超方向，`foreign_streak_truncated=1` 如實標記
    「這是下界值、真實連續天數可能更長」，不用多抓 30 天去換一個更精確但使用者沒要求
    的數字。
  - **【第四輪】`foreign_net` 定義兩個市場算法不同，但語意一致**：TWSE T86 沒有現成的
    「外資合計」欄位，需要 collector 自己把「外陸資買賣超(不含外資自營商)」+「外資
    自營商買賣超」相加；TPEx hedge_result.php 則已經內建「外資合計」欄位（index 10），
    不需要、也不可以再手動加總子欄位（會重複計算，經實測驗證 index10 本身就等於
    index4+index7 的加總結果）。兩個市場最終寫入 `foreign_net` 的語意相同（業界慣例
    「外資買賣超」= 外資陸資本體 + 外資自營商），只是計算路徑不同，見
    `docs/data-sources.md` 第 9-10 節。
  - **【第四輪】修正 `collectors/_http.py`：`requests.ConnectionError` 併入 retriable
    重試邏輯**：原本只有 `requests.Timeout` 會退避重試，`ConnectionError`（含 DNS
    解析失敗）直接判定不可重試、整個中止。本輪任務單次執行要對 TWSE/TPEx 各發送
    60~90 次請求，長迴圈中途真的實測觸發過一次 DNS 解析失敗導致腳本中止，因此把
    `ConnectionError` 併入跟 `Timeout` 同樣的重試分支。這個改動影響全部 collector
    （`_http.py` 是共用模組），但屬於修正既有缺口、不是新增行為，對其餘 collector
    只有變得更健壯、沒有破壞性影響。
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
  - **【第三輪，已隨第四輪改版失效，僅供歷史考證】** 以下三點是第三輪讀
    `tw_cache/institutional.db` 時期的雷區，**`build_institutional_summary.py` 自
    第四輪起已完全不讀這個檔案**，這三點目前對本專案不再適用（但檔案本身仍是
    `tw-momentum-scanner` 的共用資產，該專案自己讀寫時依然適用）：institutional.db
    的 stock_id 帶 yfinance 風格市場後綴（`.TW`/`.TWO`）；「最新日期」不等於「今天」
    （取決於該共用庫上次更新時間，第三輪實測時停留在 2026-05-28）；只准用 `mode=ro`
    唯讀連線開啟，不可寫入或搬動。細節見 `docs/data-sources.md` 第 11 節。
  - **【第四輪】TWSE `fund/T86` 的 JSON 回應不能靠 `resp.json()` 或 requests 自動編碼
    偵測**：`Content-Type` 宣告 `charset=UTF-8`，但實測用 `resp.json()` 或依賴
    `r.encoding` 自動偵測都會得到亂碼中文欄位名（即使 `r.encoding` 顯示 `'UTF-8'` 也
    一樣）。必須改用 `resp.content.decode('utf-8')` 手動解碼後再 `json.loads()`，
    `collectors/institutional_official.py::fetch_twse_t86` 已這樣處理，之後若照抄
    `_http.get()` 回傳的 `resp.json()` 慣例會踩到這個坑。
  - **【第四輪】TPEx `3itrade_hedge_result.php` 的頂層 `stat` 欄位無法用來判斷是否為
    交易日**：實測非交易日（週日）查詢，`stat` 仍固定回 `'ok'`，只有 `tables[0]['data']`
    是空 list 才代表「當天無交易」。這跟同一輪任務用到的 TWSE T86（`stat != 'OK'`
    才代表無交易）行為不同，兩個 endpoint 的「非交易日」判斷邏輯**不能共用同一套邏輯**，
    `collectors/institutional_official.py` 的兩個 fetch 函式分別用各自正確的判斷方式。
  - **【第四輪】`build_institutional_summary.py` 單次執行約需 4-6 分鐘**（TWSE/TPEx
    各約 60~90 次節流過的請求，`MIN_INTERVAL_SEC=1.7` 秒/請求），比第三輪讀本地
    SQLite（幾乎瞬間完成）慢很多，這是官方 API 直抓的必然代價，不要誤以為卡住了；
    執行時會即時印出「TWSE/TPEx 已抓 N/60 個交易日」進度，可用來確認沒有卡死。
- 怎麼跑 / 怎麼測：
  ```bash
  cd C:\CLAUDE\investing\tw-stock-db
  pip install -r requirements.txt
  python build_db.py                       # 整批刷新 stocks + stock_groups
  python build_fundamentals.py             # 91 檔月營收 + 籌碼集中度快照
  python build_institutional_summary.py    # 91 檔三大法人近期動態快照（TWSE T86 + TPEx
                                            # hedge_result.php 官方 API 逐日直抓，約 4-6 分鐘）
  python -m pytest tests/ -q               # 驗證資料庫內容（25 個測試）
  ```
