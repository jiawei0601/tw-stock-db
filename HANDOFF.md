# HANDOFF

> 兩個 agent 交接的唯一現況真相。離開前更新，接手前先讀。

- 最後更新：Claude Code @ 2026-07-17（第十二輪：`refresh_daily.py` 每日刷新腳本
  —— 把十一個 build/export 腳本串成嚴格串行的更新鏈，詳見下方第十二輪紀錄；排程本身
  尚未註冊，需使用者授權後由主對話另行處理）
- 目前任務 / 目標：建立台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）
  標記，為未來「資金流向依板塊/族群視覺化網頁」鋪路的資料底層。
- 已完成：
  - **【第十二輪】`refresh_daily.py` —— 每日刷新腳本**：把前十一輪累積的十一個
    build/export 腳本（`build_institutional_summary.py` → `build_daily_prices.py` →
    `build_taiex.py` → `build_revenue_history.py` → `build_fundamentals.py` →
    `build_sector_flow.py` → `build_sector_flow_weekly.py` → `build_sector_flow_
    value.py` → `build_group_flow.py` → `export_sector_flow_animation.py` →
    `export_dashboard.py`）串成一支**嚴格串行**執行的每日刷新腳本（SQLite 單寫入者，
    AGENTS.md 早已明訂「不可同時併發跑」，串行是唯一安全設計）。
    - 每步用 `subprocess.run([sys.executable, script], cwd=repo根)` 執行，開始/結束/
      耗時/成功失敗記錄到 `data/refresh.log`（append 模式；`rotate_log()` 在超過
      5MB 時砍掉前半只保留後半，截斷點對齊到換行後，避免行被腰斬）。
    - **單步失敗不中止**：記錄後繼續跑後續步驟（增量抓取類腳本失敗，隔天重跑會自動
      續傳補上；彙總/匯出類腳本失敗，用當下既有資料照跑不影響其他步驟）。全部跑完後
      若有任何失敗，透過 `C:\CLAUDE\tools\telegram\notify.py` 的 `send()` 發一則
      失敗摘要通知——用法比照 `tw-momentum-scanner/notifier/telegram.py`（`sys.path`
      加入 `C:\CLAUDE\tools\telegram` 後 `import notify`，讀 `notify.load_env()`
      取 token/chat_id），**Telegram 發送失敗一律 try/except 吞掉，不可讓 refresh
      當掉**。全部成功時安靜結束、不發通知（避免每日都收到「一切正常」的雜訊）。
    - `--dry-run`：只列印 11 步清單，不執行、不寫 log、不呼叫 `subprocess.run`。
    - exit code：全成功 = 0，有任何失敗 = 1（供排程系統/Windows Task Scheduler
      判斷失敗狀態並可選擇性重試或告警）。
    - **log 位置**：`data/refresh.log`（repo 內，已加進 `.gitignore`，理由：跟
      `data/tw_stocks.db` 不同，log 是純執行紀錄非資料本體，且會持續增長，不需要
      track 進 git）。
    - **本輪任務範圍明確不含排程註冊**：使用者已明確指示「不要執行任何 schtasks
      指令，排程註冊由主對話另行處理」，本輪只交付腳本本身；README.md 附上建議的
      `schtasks` 範例指令（週一至五 18:30，比照 tw-momentum-scanner 排程時段慣例），
      但**尚未實際執行**，等待使用者在主對話授權後另行註冊。
    - `tests/test_refresh_daily.py`（11 個測試，**全程 mock `subprocess.run`，不真的
      執行任何 build/export 腳本**）：dry-run 輸出包含全部 11 步且順序正確且與
      `STEPS` 常數一致、dry-run 不觸碰 `subprocess.run`、`rotate_log()` 未超限不動作
      /不存在不丟例外/超限時砍前半保留後半且截斷對齊換行/找不到換行時保底砍到
      門檻內、單步失敗（mock 讓 `build_taiex.py` 回傳非 0）不中斷後續步驟且結尾正確
      彙總通知、多步失敗全部列入摘要、`run_step()` 對 `subprocess.run` 拋例外的情況
      也視為該步失敗（不中止整條鏈）、全部成功時不呼叫通知且 exit code 為 0、
      `send_failure_notification()` 在 `import notify` 失敗時吞例外不外拋。全專案
      `python -m pytest tests/ -q` 共 **145 個測試，全綠**（134 個既有 + 11 個新增）。
    - `AGENTS.md`（build/run 新增 `refresh_daily.py` 說明段落＋架構圖新增一行）／
      `README.md`（新增「每日更新」段落，含手動執行方式＋建議排程範例指令，範例寫出
      但註明由主對話另行註冊）同步更新。
    - **無偏離**：規格要求的行為（串行執行、log 輪替、單步失敗不中斷、結尾失敗摘要
      通知、`--dry-run`、exit code、不含排程註冊）全數如實實作，未發現需要臨場調整
      判斷的模糊點。
  - **【第十一輪】`dashboard.html`（repo 根目錄）—— 台股資金流向儀表板 v1，本專案主
    產出物**：新增 `export_dashboard.py`，唯讀彙整前十輪累積的資料表，匯出一份完全
    獨立、免伺服器、瀏覽器雙擊即開的靜態 HTML（比照 `export_sector_flow_animation.py`
    的模式：資料內嵌 JSON、Chart.js 走 CDN），不寫入資料庫、不對外發送任何請求。
    - **產品定位（寫進頁面本身）**：這是資金結構的觀察工具，不是訊號產生器。頁面
      底部「使用須知」區塊如實引用 `analysis/` 下五份「法人資金流向預測力系列」
      報告（`taiex-flow-correlation-2026-07-16.md`／`flow-persistence-seasonality-
      2026-07-16.md`／`trust-streak-price-impact-2026-07-17.md`／`trust-streak-
      taiex-2026-07-17.md`／`sector-flow-entry-signal-2026-07-17.md`）的核心結論
      ——「描述性有效、預測性全部失敗」，並列出五份檔名。
    - **五大區塊**：(1) 總覽：TAIEX 3 年週線（用 `taiex_daily` 依週取區間內最後
      收盤點）+ 全市場三大法人週度淨買賣超金額柱狀圖（`sector_flow_value_weekly`
      跨 34 板塊 `SUM`，因每檔股票只屬一個板塊，加總語意合法），兩圖獨立畫布垂直
      堆疊、共用同一組週別 x 軸標籤（不是雙 y 軸擠一張圖），柱狀圖可用按鈕切換
      外資/投信/自營商/合計四個 dataset。(2) 板塊熱力圖：34 板塊（依 3 年總金額
      活動量排序）x 150 週，用 HTML `<table>` + 逐格背景色實作（不用 canvas），
      色階依全部格子絕對值的 95 百分位動態定尺度、白=近零/藍=淨流入/橘紅=淨流出，
      `title` 屬性提供 hover 顯示板塊/週期間/金額。(3) 板塊排行與下鑽：近 4/12 週
      淨流入排行前十/後十（點名稱可直接跳去下鑽），下拉選單選任一板塊看 150 週
      長條圖 + 成分股表（現算 `institutional_flow_daily` JOIN `daily_prices` 近
      20 交易日的個股法人淨額金額，取前 10 大，附最新月營收 YoY%／籌碼集中度
      `pct_gt_400zhang`，只嵌彙總數字不嵌時序）。(4) 族群視圖：19 族群的縮小版
      （同款熱力圖/排行/下鑽），頁首用醒目的警示框重申「族群成分重疊，數字不可
      跨族群加總」。(5) 投信特寫：全市場投信週度金額圖 + 目前連買/連賣天數（從
      `sector_flow_value_daily` 跨板塊 `SUM(trust_value)` 逐日往回數同號天數）+
      距下個季底日曆天倒數（純日期算術，不依賴未來交易日資料）+ 季底作帳統計
      一句話（引用 `flow-persistence-seasonality-2026-07-16.md` 的實際數字：
      季底 5 日均買超 +4,314 萬元 vs 其他日 +1,676 萬元，高約 157%，p=0.024）+
      投信目前連買中的板塊前 5 名。
    - **實測結果**：`dashboard.html` 檔案大小約 **105KB**（500KB 預算內，遠低於
      預期，因為個股彙總只嵌 top10、不嵌全市場 1970 檔）；34 板塊/19 族群/150 週
      皆與資料庫一致；用 headless Edge（`msedge.exe --headless --screenshot`）
      實際渲染截圖檢查過全頁版面（總覽/熱力圖/排行/族群/投信特寫/使用須知六個
      區塊皆正常顯示、中文字型正常、light 配色未被深色模式反轉）。
    - `tests/test_dashboard_export.py`（42 個測試）：檔案產出、JSON 可解析、無
      未替換的模板 placeholder、34 板塊/19 族群/150 週與 DB 一致、抽查板塊/族群
      熱力圖各一格數字等於 DB 值、全市場週度合計等於跨板塊加總、TAIEX 週線無缺口、
      下鑽 top10 排序正確且涵蓋全部板塊/族群、投信 streak 與季底倒數欄位存在、
      使用須知文字與五份報告檔名存在（且五個檔案確實存在於 `analysis/`）、關鍵
      DOM id 存在、light color-scheme 鎖定存在。全專案測試共 **133 個，全綠**。
    - `AGENTS.md`（架構圖新增一行、build/run 新增 `export_dashboard.py` 說明、
      專案定位段落）／`README.md`（新增「怎麼打開儀表板」段落）同步更新。
    - **設計偏離**：規格要求「族群成分重疊…UI 呈現時務必遵守語意」用醒目警示框
      而非純文字段落呈現（比規格文字描述更視覺化，判斷更符合「誠實揭露」的目的）；
      投信季底統計數字採用報告的實際數字（+157%／p=0.024）而非規格草稿裡的示意
      數字（規格原文寫「較平日高 57%」，經核對 `flow-persistence-seasonality-
      2026-07-16.md` 實際數字後採用報告真實結論 157%，不照抄規格草稿的示意值）。
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
  - **【第五輪】月營收 + 三大法人動態從「只有最新一期」擴充為「近 3 年歷史」**（範圍：
    僅 `SELECT DISTINCT stock_id FROM stock_groups` 這 91 檔，動態查詢不寫死清單）：
    - **月營收近 3 年歷史**（`monthly_revenue` 表）：新增 `collectors/revenue_history.py`
      （MOPS 歷史封存頁面 `mopsov.twse.com.tw/nas/t21/{sii,otc}/t21sc03_{roc_year}_{month}_0.html`，
      HTML 格式，須 `cp950` 解碼）+ `build_revenue_history.py`（新腳本，取代
      `build_fundamentals.py` 原本的月營收部分）。**Schema 破壞性異動**：PK 從
      `stock_id`（單列快照）改為 `(stock_id, ym)`（時序表），首次執行自動偵測舊 schema
      並 DROP 重建（backfill 本來就會把最新一期含在 36 個月範圍內，不遺失資訊）。
      可續傳設計：`revenue_fetch_log` 表（PK `(source_market, ym)`）記錄已抓過的
      「市場+年月」，重跑跳過已抓月份（最近 2 個月每次強制重抓），單月失敗跳過繼續、
      不中止整體 backfill，每抓完一個月立即寫入（不是等 72 頁全部成功才寫入）。
      **實測結果：36 個月目標，實際涵蓋 35 個不同年月**（`2023-08` ~ `2026-07`，
      2026-07 尚未公告屬預期），**91 檔目標中 88 檔有資料**（3 檔 `-KY` 外國發行人
      股票 `3665`／`3673`／`4977` 在這個封存系列裡系統性查無資料，經跨 6 個不同月份
      交叉驗證確認不是 parsing bug，見 `docs/data-sources.md` 第 13 節），
      2330 台積電 34/35 個月有資料。首次全量 backfill 實測耗時 2.0 分鐘（72 次請求），
      零失敗。
    - **三大法人動態近 3 年歷史**（`institutional_flow_daily` 表）：沿用第四輪的
      `collectors/institutional_official.py`（不用改），改寫 `build_institutional_summary.py`
      的抓取策略：從「每次整批重抓近 60 個交易日」改為「累積近 3 年（750 個交易日）+
      增量刷新」。新增 `institutional_fetch_log` 表（PK `(market, date)`）記錄已查過的
      「市場+日期」（含非交易日的空結果），讓程式精確判斷該不該重抓，不必靠
      `institutional_flow_daily` 有沒有那一列去猜測。`institutional_flow_summary`
      （5/20/60 日彙總 + streak）計算邏輯完全不變，但資料來源改成讀本地
      `institutional_flow_daily`（不再對外重複發送請求）。每湊滿 20 個交易日 commit
      一次、單日失敗跳過繼續不中止整體 backfill。**實測結果：TWSE/TPEx 各 750/750
      個交易日全數抓齊，`institutional_flow_daily` 共 67,764 列，涵蓋期間
      `2023-06-12` ~ `2026-07-15`（約 3.1 年），`institutional_flow_summary` 91/91
      全數涵蓋，零失敗**。首次全量 backfill 實測耗時 66.8 分鐘（750 交易日 x 2 市場
      節流過的請求，符合預期的 45-90 分鐘長跑範圍）；驗證過重跑時的增量行為
      （只抓新交易日，數秒內完成）與中途擴大 target 的續傳行為（從上次停下的地方繼續
      往回補，不重複抓已有範圍）皆正確。
    - **過程中發現並修正兩個真實 bug**（皆記錄在 `docs/data-sources.md` 第 13 節）：
      (1) MOPS 封存頁面 row-parsing regex 若在 `[^<]*` 前多加一個 `\s*` 去吃數字前導
      空白，會在近 44 萬字元的單行 HTML 上觸發**災難性回溯**，直接掛住（不是效能慢，
      是實質上跑不完）；(2) 該頁面「備註」欄位的 `<td>` alignment 在備註為空時是
      `align=center`、有實際文字時是 `align=left`，只比對 `align=center` 會讓所有
      「有備註的公司」整列被靜默漏掉——第一次實測時 TWSE 2026-06 頁面因此漏掉 233 筆，
      其中包含 2330 台積電本人，直到拿已知標的核對才發現。
    - `tests/test_fundamentals_content.py` 更新：monthly_revenue 相關測試改為驗證
      「涵蓋月數」「2330 筆數」「PK 為複合鍵」而非「總列數」；institutional_flow_daily
      新增「涵蓋期間約 3 年」「單檔交易日數落在合理區間（100~800）」測試，取代舊版
      「不超過 60 天」的測試（語意已改變）。全專案測試共 28 個，全綠。
    - `AGENTS.md` 更新架構圖與 Interface Contract 反映 schema 異動；新增
      `build_revenue_history.py` 使用說明；`build_institutional_summary.py` 說明改為
      「增量 + 回補式」而非「單次抓 60 天」。
  - **【第六輪】`sector_flow_daily` 板塊資金流彙總表 + 板塊間資金流動關係分析**：
    新增 `build_sector_flow.py`，從 `institutional_flow_daily` JOIN `stocks.industry_name`
    聚合出「每個板塊每日三大法人買賣超合計」，PK 為 `(industry_name, date)`，idempotent
    整批刷新。**範圍限定在 `stock_groups` 名單（91 檔）橫跨的 13 個板塊**，不是全市場
    34 個板塊（原因見下方關鍵決策，使用者已確認接受此範圍）。實測 9750 列（13 板塊 ×
    750 交易日），涵蓋 2023-06-12 ~ 2026-07-15。
    - `tests/test_sector_flow.py` 新增 5 個測試（表存在且非空、板塊數與 universe 一致、
      `total_net` 等於三分量加總、抽查某日聚合數字與逐股加總一致、日期範圍約 3 年）。
      全專案測試共 33 個，全綠。
    - 用 pandas 做一次性分析（相關係數矩陣、lead-lag 交叉相關、3 年累計 vs 近 60 日
      排行），結果與方法論寫成報告：`analysis/sector-flow-2026-07-16.md`。**這份報告
      本身不是可重跑的 build script，是一次性分析輸出**，之後要更新結論需要重新執行
      分析邏輯（目前只存在於這次的對話過程，未沉澱成腳本，見下方下一步）。
    - 核心發現：半導體業/電子零組件業/其他電子業三年來是主要資金匯集板塊；電機機械
      （重電/機器人相關）三年累計是最大淨流出板塊且近期仍持續流出，與市場熱度形成
      落差；電子零組件業出現「三年累計第二大流入 → 近60日轉為第二大流出」的訊號，
      可能反映資金從零組件供應鏈往半導體本業集中的輪動。板塊間相關性整體偏弱
      （多數 \|r\|<0.35），沒有找到強烈可操作的領先落後規律，報告中誠實記錄此結果。
  - **【第七輪】三種延伸資料（月營收/籌碼集中度/三大法人動態）從「只涵蓋 stock_groups
    91 檔概念股」擴大為「涵蓋 stocks 全市場 1971 檔」**，`sector_flow_daily` 也同步從
    91 檔橫跨的 13 個板塊擴大為全市場 34 個板塊：
    - 修改 4 個 build 腳本的篩選白名單（`build_revenue_history.py`／
      `build_fundamentals.py`／`build_institutional_summary.py` 的 `_target_stock_
      ids`/`_target_stocks`，`build_sector_flow.py` 拿掉 `WHERE ... IN (SELECT ...
      FROM stock_groups)` 子句），全部改查 `stocks` 表；collectors 本身（`revenue_
      history.py`／`shareholding.py`／`institutional_official.py`）完全沒有改動
      ——篩選邏輯原本就只存在於 build 腳本，不在 collector 裡，複查後確認不需要改。
    - **實測踩到並修正一個真實 bug**：`build_revenue_history.py`／`build_institutional_
      summary.py` 都用 fetch-log 表（`revenue_fetch_log`／`institutional_fetch_log`）
      做「已抓過的日期/年月跳過」的可續傳設計，但這兩個 log 表只記錄「有沒有抓過」，
      不記錄「當時用的是哪個篩選範圍」。改完篩選邏輯後直接重跑 `build_revenue_
      history.py`，結果只有「強制重抓的最近 2 個月」變成全市場範圍，其餘 34 個舊月份
      仍停留在舊版 87~88 檔（因為 log 顯示「已抓過」而被跳過，但當初寫入的資料是
      篩選後的 91 檔子集，放寬篩選不會生出新資料）。修法：在兩個 build 腳本的 `build()`
      開頭都加上 `_needs_full_rebuild()` 偵測（比對『扣掉最近強制重抓範圍外的舊資料
      涵蓋股票數』是否遠低於本次目標股票數），判定為舊範圍殘留就整個清空對應表
      （`monthly_revenue`／`revenue_fetch_log`，或 `institutional_flow_daily`／
      `institutional_fetch_log`／`institutional_flow_summary`）後重新全量 backfill，
      不嘗試「補洞」（部分月份/日期是舊範圍、部分是新範圍的資料庫是不可信的半殘狀態）。
      詳見 `docs/data-sources.md` 第 14 節。
    - **實測涵蓋率**：
      - `monthly_revenue`：**1846/1971 檔（93.7%）**，35 個不同年月，62,633 列。
        125 檔缺資料拆解：20 檔是 backfill 視窗（2023-08 起）後才掛牌的新股（預期內）；
        **105 檔已掛牌但查無歷史，其中 104 檔是 `-KY` 境外發行人**（驗證第五輪僅用
        91 檔樣本觀察到的「3 檔 -KY 系統性缺席」其實是全市場性的模式，MOPS 月營收
        封存頁完全不含任何 `-KY` 發行人，見 `docs/data-sources.md` 第 15 節）；
        剩 1 檔（3717 聯嘉投控）原因未查證，且不屬於「投控公司普遍缺席」模式（全市場
        9 檔投控公司中 8 檔都有正常資料），如實記錄為單一未解釋缺口。
      - `shareholding_concentration`：**1971/1971 檔（100%）**，TDCC 全市場一次性下載
        天然涵蓋全部，無缺口。
      - `institutional_flow_daily`：TWSE/TPEx 各 750/750 個交易日全數抓齊（backfill
        期間偵測到舊版 91 檔殘留資料，先清空三張相關表才重新全量 backfill），共
        **1,298,500 列**，涵蓋 `2023-06-13` ~ `2026-07-16`（約 3.1 年）。
        `institutional_flow_summary`：**1970/1971 檔（99.9%）**，僅 `7814` 台亞半導
        查無資料。
      - `sector_flow_daily`：**34/34 個官方產業別板塊全數涵蓋**（上一輪 91 檔範圍下
        只有 13 個），25,500 列（34 板塊 x 750 交易日），涵蓋期間與 `institutional_
        flow_daily` 相同。
    - **實測耗時**：`build_revenue_history.py`（含一次因舊範圍殘留而觸發的重跑）約
      2.0 分鐘（72 次節流過的請求，MOPS 封存頁本身就是全市場下載，請求數不變）；
      `build_fundamentals.py` 數秒（TDCC 全量下載，秒級完成）；`build_institutional_
      summary.py` **64.8 分鐘**（750 交易日 x 2 市場節流過的請求，跟第五輪 91 檔版本
      的 66.8 分鐘幾乎相同，證實請求數不變的預期，差異只是資料量從 6.8 萬列增加到
      129.9 萬列）；`build_sector_flow.py` 秒級完成（純本地聚合）。全部四個 build
      腳本合計約 67 分鐘。
    - **全市場實測後出現過去 91 檔樣本從未觀察到的合法極端值**：
      - `yoy_pct`/`mom_pct` 最高衝到 2526 萬%（`2528` 2024-12，去年同期營收基期僅
        21 千元，微小波動被放大成天文數字，數學上完全自洽，非 bug）。
      - 部分營建類股採比例完工法認列營收，當月營收本身可能是負數（`6024` 2026-05
        `revenue=-244,632` 千元，備註明確說明會計原因），導致 `yoy_pct`/`mom_pct`
        可以低於 -100%（91 檔樣本從未踩過這個情況）。
      - 約 1%（20/1970 檔）股票的 `institutional_flow_summary.latest_date` 明顯落後
        （例如 `3629` 地球磁力停留在 2025-07-08），合理推測是長期停牌/警示股，真實的
        個股交易狀態，不是抓取邏輯 bug。
      詳見 `docs/data-sources.md` 第 16-17 節。
    - `tests/test_fundamentals_content.py`／`tests/test_sector_flow.py` 更新：覆蓋率
      斷言的比較基準從 `stock_groups` 改為 `stocks`；`test_monthly_revenue_yoy_mom_
      sane_range` 上下界從 ±2000% 放寬到 ±5000 萬%（容忍上述極端值，仍遠低於「營收
      金額誤植進百分比欄位」的量級，足以攔住真正的欄位錯位）；
      `test_institutional_flow_summary_freshness_consistent` 從「全部股票 <=5 天落差」
      改為「至少 95% 股票落在 MAX(latest_date) 5 天內」；`test_sector_flow_covers_
      all_industries_in_universe` 改成動態比對「stocks JOIN institutional_flow_daily
      實際涵蓋的板塊數」（不寫死 34，因為個別板塊理論上可能剛好沒有任何交易日資料）。
      全專案測試共 33 個（題數不變，只改斷言邏輯），全綠。
    - `AGENTS.md`／`docs/data-sources.md` 同步更新（Interface Contract、架構圖、
      build/run 說明），`stock_groups` 表本身未受影響、未被清空或修改，仍是有效的
      概念股標記，只是不再是這三張延伸資料表的篩選依據。
  - **【第八輪】板塊資金流動週度動畫**：使用者要求把全市場資料「每 5 個交易日切分
    彙整」做成週度動畫。新增 `build_sector_flow_weekly.py`（`sector_flow_weekly` 表，
    PK `(industry_name, week_index)`，從 `sector_flow_daily` 依交易日序列每 5 筆切一組
    彙總，不對齊日曆週——直接按交易日序列切，遇到假日/停市自然順延，不會出現「這組只有
    3 天」的情況，除了最後一組可能不足 5 天）。實測：全市場 34 板塊 × 150 週 = 5100 列，
    2023-06-13 ~ 2026-07-16。
    - 新增 `export_sector_flow_animation.py`：從 `sector_flow_weekly` 匯出**獨立、免伺服器
      的靜態 HTML 動畫**（`analysis/sector-flow-weekly-animation.html`），橫向長條圖依
      板塊固定順序（不是動態重新排序的「賽跑」樣式，理由見下方關鍵決策）播放每週的
      三大法人淨買賣超（藍=淨流入／橘紅=淨流出），含播放/暫停、速度切換、時間軸拖曳。
      **只取活動量最大的 20 個板塊**（`SUM(ABS(total_net))` 排序取前 20），不是全部
      34 個，避免長條圖塞太多板塊看不清楚——理由與取捨見下方關鍵決策；`--top-n` 參數
      可調整。
    - 也在對話中用 `mcp__visualize` 產生了同一份動畫的互動版本給使用者即時預覽（非
      repo 內產出物，純聊天介面展示，權威版本是上述匯出的靜態 HTML 檔）。
    - `tests/test_sector_flow_weekly.py`（6 個測試：表非空、板塊數與 daily 一致、
      非最後一組皆為 5 個交易日、`total_net` 加總正確、抽查一組數字與 daily 加總一致、
      週數與交易日數換算一致）、`tests/test_sector_flow_animation_export.py`
      （4 個測試：top-N 篩選正確、週數與資料庫一致、每週數值長度與板塊數對齊、
      驗證半導體業/金融保險業確實在活動量最大的 20 個板塊內）。全專案測試共 43 個，
      全綠。
  - **【第八輪後續】動畫三項優化**（使用者陸續在對話中提出）：
    1. **固定 x 軸範圍**：原本長條圖 x 軸沒設 min/max，每週切換時 Chart.js 會依當週
       數值自動縮放，導致 0 軸（正負分界）的像素位置每週左右跑動、難以判讀。改用
       全部 150 週、20 個板塊的實際極值取整數留 1 的餘裕，寫死 `min`/`max`，0 軸
       整場動畫固定在同一個位置。
    2. **底部新增淨流入/淨流出/淨額合計列**：即時依當週圖上顯示的 20 個板塊算出
       三個數字，隨播放/拖曳同步更新，並註明「合計為圖上顯示的 20 個板塊加總，
       非全市場 34 個板塊」避免誤讀。
    3. **新增當週 TAIEX（大盤加權指數）5 日走勢圖**：使用者要求加指數走勢對照。
       目前資料庫裡完全沒有任何指數價格資料，這是全新的資料維度，用
       `AskUserQuestion` 確認過是要「大盤加權指數」而非「各板塊自己的類股子指數」
       （後者需要另外抓 20 個子指數的 3 年歷史，工作量大得多，使用者選擇前者）。
       新增 `collectors/taiex.py` + `build_taiex.py`：TWSE `FMTQIK` endpoint
       （`https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=YYYYMMDD&response=json`），
       依日期查一整個月份，38 個月請求即可涵蓋 3 年（比照 MOPS 月營收封存頁「查一個
       月、拿整月」的模式），新表 `taiex_daily`（PK `date`）。實測 758 筆，
       2023-06-01 ~ 2026-07-16，零缺口；抽查台股加權指數三年間從約 17,200 漲到約
       47,700（尖峰在 2026-06-22），符合本專案觀察到的 AI/半導體資金大量湧入現象，
       非資料錯誤（詳見下方關鍵決策與 `tests/test_taiex.py` 的合理值上限說明）。
       `export_sector_flow_animation.py` 為每週附上該週日期區間內實際存在的 TAIEX
       每日收盤點（通常 5 筆），新增第二個 Chart.js 折線圖呈現，**y 軸刻意不固定**
       （跟長條圖的固定 x 軸邏輯相反，理由見下方關鍵決策）。
    - `tests/test_taiex.py`（4 個測試：表非空、日期範圍涵蓋 `sector_flow_daily`、
      收盤值落在合理量級、無重複日期）、`test_sector_flow_animation_export.py`
      新增 3 個測試（x 軸固定不自動縮放、每週都有 TAIEX 資料點、底部合計元素存在）。
      全專案測試共 50 個，全綠。
  - **【第九輪】三大法人板塊資金流「金額口徑」（股數 x 收盤價換算）＋五份法人流向
    預測力分析報告**：股數口徑不能直接跟市值加權的 TAIEX 比較（賣 1 億股 10 元小型股
    跟賣 1 億股千元台積電對指數影響天差地遠），本輪補上金額維度，跟既有股數口徑並存、
    不取代：
    - **`daily_prices` 個股歷史收盤價**（新表，PK `(stock_id, date)`）：新增
      `collectors/prices.py`（TWSE `afterTrading/MI_INDEX` + TPEx `www/zh-tw/
      afterTrading/dailyQuotes`，兩者皆已實測確認真正支援歷史查詢，排查過程與陷阱
      endpoint 見 `docs/data-sources.md` 第 20-21 節）+ `build_daily_prices.py`
      （比照三大法人 backfill 的可續傳設計：`price_fetch_log` 記錄已查「市場+日期」、
      每 20 個日曆日 commit、單日失敗跳過）。範圍刻意比全市場窄：只抓
      `institutional_flow_daily` 實際出現過的股票（1970 檔）x 其完整日期範圍。
      **實測結果：1,377,667 列**，涵蓋 2023-06-13 ~ 2026-07-16，對齊
      `institutional_flow_daily` 的 (stock_id, date) 逐筆涵蓋率 **99.97%**
      （TWSE 99.99% / TPEx 99.95%，缺 372 筆為停牌/全額交割等無收盤價情況，
      金額換算時如實排除、不臆測價格）。
    - **`sector_flow_value_daily`（25,500 列 = 34 板塊 x 750 交易日）+
      `sector_flow_value_weekly`（5,100 列 = 34 板塊 x 150 週）**：新增
      `build_sector_flow_value.py`，股數 JOIN 收盤價換算「約略金額」（新台幣元，
      用當日收盤價換算全部買賣超股數，是近似值不是精確成交金額）後依板塊聚合，
      純本地聚合秒級完成；`week_index` 刻意對齊 `sector_flow_weekly` 同一套日期
      序列，股數/金額兩版可互相對照。金額欄位 NULL 語意=「完全無成分股可換算」，
      與 0 不同語意（見 AGENTS.md Interface Contract）。目前 0 列 NULL。
    - **`export_sector_flow_animation.py` 改版**：預設 `--mode value`（金額口徑，
      單位億元，輸出 `analysis/sector-flow-weekly-animation.html`），`--mode shares`
      保留第八輪股數口徑舊行為（輸出 `...-shares.html`）；金額模式偵測到收盤價
      覆蓋率 <99.95% 會在頁面加誠實揭露文字。
    - **五份「法人資金流向預測力系列」分析報告**（`analysis/` 下，一次性分析輸出、
      非可重跑腳本，性質同第六輪報告）：
      1. `taiex-flow-correlation-2026-07-16.md` — TAIEX 與三大法人流向關聯
      2. `flow-persistence-seasonality-2026-07-16.md` — 流向持續性與季節性
      3. `trust-streak-price-impact-2026-07-17.md` — 投信連買對個股股價的影響
      4. `trust-streak-taiex-2026-07-17.md` — 投信連買與大盤的關係
      5. `sector-flow-entry-signal-2026-07-17.md` — 板塊資金流作為進場訊號的回測
      **系列總結論：描述性有效、預測性全部失敗**——法人流向資料能如實描述「錢流去
      哪了」（與同期漲跌高度相關，部分是機械性的：外資買權值股≈指數漲本身），但
      所有嘗試過的「用流向預測未來報酬」訊號（連買 streak、板塊輪動進場等）在
      考慮交易成本與基準比較後都沒有可操作的優勢，細節與方法論限制見各報告。
    - **收尾過程兩段實情（如實記錄，供後續 agent 判讀資料可信度）**：
      1. **收尾進程曾崩潰**：第一次執行 `build_sector_flow_value.py` 時進程崩潰
         （原因未能確定，腳本本身複查無 bug，第二次執行 7.6 秒正常完成），SQLite
         hot journal 在下次開啟時自動回滾未提交交易，`PRAGMA integrity_check` 通過，
         `daily_prices` 等既有表無損。
      2. **TPEx 收盤價回補缺口被測試攔截後補完**：崩潰後接手收尾時，
         `test_daily_prices_tpex_coverage_high` 亮紅燈（TPEx 涵蓋率僅 82.2%），
         追查 `price_fetch_log` 發現 TPEx backfill 其實只跑到 2026-01-09（940/1130
         個日曆日）就中斷、且中斷未被察覺，前次交接誤報「回補完整」（127 萬列的
         絕對數字掩蓋了 TPEx 尾端缺 190 個日曆日/123 個交易日）。利用
         `build_daily_prices.py` 既有可續傳機制補完（5.5 分鐘），涵蓋率升至
         99.97%，`sector_flow_value_*` 重建後 NULL 列從 246（日）/48（週）降為 0，
         測試轉綠。**教訓：長跑 backfill 腳本「有跑完 log」不等於「範圍跑完」，
         交接前要用涵蓋率查詢驗收，不能只看列數量級。**
    - 新增 `tests/test_daily_prices.py`（7 個測試：非空、PK 無重複、收盤價量級、
      2330 已知值交叉核對、日期範圍對齊、TWSE/TPEx 涵蓋率——正是涵蓋率測試攔下
      上述缺口）、`tests/test_sector_flow_value.py`（13 個測試：兩表列數/板塊數/
      週次對齊、金額=股數x收盤價抽查、NULL 語意等）；
      `tests/test_sector_flow_animation_export.py` 更新支援雙模式。
      全專案測試共 **70 個，全綠**。
    - `AGENTS.md`／`docs/data-sources.md`（第 20-21 節）同步更新。
  - **【第十輪】族群（概念股）層級資金流彙總表 —— `sector_flow_*` 系列的族群版**：
    使用者指出板塊層級（`sector_flow_*`）跟族群層級（`stock_groups`，19 個族群、
    91 檔標的）的聚合資料都已齊備，只差聚合這一步。新增 `build_group_flow.py`，從
    `institutional_flow_daily` JOIN `stock_groups` 一次產出四張表：
    - `group_flow_daily`（股數口徑，PK `(group_name, date)`）／`group_flow_weekly`
      （股數口徑，週切分，PK `(group_name, week_index)`）：schema 與聚合邏輯完全比照
      `sector_flow_daily`／`sector_flow_weekly`，只是 `industry_name` 換成
      `group_name`。
    - `group_flow_value_daily`（金額口徑，PK `(group_name, date)`）／
      `group_flow_value_weekly`（金額口徑，PK `(group_name, week_index)`）：JOIN
      `daily_prices` 換算約略金額，schema／NULL 語意／涵蓋率欄位完全比照
      `sector_flow_value_daily`／`sector_flow_value_weekly`。
    - `week_index` 沿用與 `sector_flow_weekly` 完全相同的交易日序列切分邏輯（同一份
      `dates` 列表、同一種 `i // chunk_size` 編號），實測抽查對齊 100% 一致，族群版
      跟板塊版可互相對照同一個「第 N 週」。
    - **核心語意差異（跟 `sector_flow_*` 不同，是本輪任務最重要的設計決策）**：
      `stock_groups` 的 `(stock_id, group_name)` 是多對多關係——一檔股票可以同時屬於
      多個族群（實測 91 檔中確有多檔橫跨 2 個以上族群，例如 2330 台積電同時屬於
      「CoWoS先進封裝供應鏈」與另一個族群），而 `sector_flow_*` 依賴的
      `stocks.industry_name` 是每檔股票唯一一個官方產業別（多對一）。**因此
      `group_flow_*` 四張表每一列的數字只在該族群自己內部成立，絕不可跨族群相加**
      （例如把 19 個族群某天的 `total_net` 全部 `SUM` 起來，會重複計算橫跨多族群的
      股票，結果比全市場/全概念股去重後的真實合計還大，沒有實質意義）——這點已寫進
      腳本 docstring 開頭最顯眼的位置與 `AGENTS.md` 的 build/run 段落＋Interface
      Contract，並用一條測試（`test_group_flow_daily_cross_group_stock_count_
      exceeds_distinct_stock_count`）直接在資料上證明「跨族群加總 ≠ 全市場合計」，
      不只是口頭警語。
    - **實測結果**：`group_flow_daily`／`group_flow_value_daily` 各 **14,250 列**
      （19 族群 x 750 交易日），`group_flow_weekly`／`group_flow_value_weekly` 各
      **2,850 列**（19 族群 x 150 週），日期範圍 `2023-06-13` ~ `2026-07-16`（與
      `sector_flow_daily` 完全一致，因為都是從同一份 `institutional_flow_daily` 衍生）。
      金額口徑兩表**目前 0 列 NULL**（`stock_groups` 91 檔全數已被 `daily_prices`
      涵蓋，收盤價缺口在族群範圍內剛好沒有造成任何一天/一週完全無法換算）。純本地
      聚合，執行耗時數秒（四張表一次跑完，不需要分開執行四個腳本）。
    - `tests/test_group_flow.py` 新增 21 個測試，比照 `test_sector_flow.py`／
      `test_sector_flow_value.py` 的驗證模式（表非空、19 族群涵蓋、`total_net`/
      `total_value` 等於分量和、抽查一天/一週數字與成分股逐筆加總一致、週切分與
      `sector_flow_weekly` 對齊、金額表 NULL 語意），額外新增 2 個測試專門驗證族群
      成分重疊的核心語意（`test_stock_groups_has_overlapping_membership` 確認 91 檔
      中確實有股票橫跨多族群、`test_group_flow_daily_cross_group_stock_count_
      exceeds_distinct_stock_count` 用 `stock_count` 結構性計數而非金額/股數淨額
      驗證跨族群加總必然重複計算，避免用淨額做斷言可能因數字巧合抵銷而失效）。
      全專案測試共 **91 個，全綠**。
    - `AGENTS.md` 更新：專案定位段落、build/run 新增 `build_group_flow.py` 說明、
      架構圖新增一行、Interface Contract 新增族群表 schema 與跨族群加總語意警語。
- 進行中（做到哪一步）：無，第十一輪任務範圍內的項目已全部完成。
- 下一步（下一個任務，非本次範圍）：
  0. **【第十一輪起，原「視覺化網頁」項目已完成】** `dashboard.html` 已上線（見上方
     第十一輪紀錄），下方第 3 點原始描述保留供歷史考證，但已被取代——若之後想擴充
     儀表板（例如加個股層級的獨立下鑽頁、串接 `institutional_flow_summary` 的
     streak 欄位、把「近 4 週」改成可調參數），可以直接在 `export_dashboard.py`
     上疊加，不需要重新設計架構。
  1. **持續補充族群/概念股**：`stock_groups` 是人工整理/使用者提供資料，非官方來源，
     之後有新的族群清單可比照同樣模式（核對代號後 `INSERT OR REPLACE`）繼續累積，
     不需改 schema。**【第七輪起】不再需要重新 backfill**：`build_revenue_history.py`
     /`build_fundamentals.py`/`build_institutional_summary.py`/`build_sector_flow.py`
     現在都是動態查詢 `stocks` 全市場（不再是 `stock_groups`），族群名單擴充只是替
     既有的全市場資料多加一個「概念股標記」，只要新股票代號本身已經在 `stocks` 表裡，
     它的月營收/籌碼集中度/三大法人動態早就已經被涵蓋，不需要為 `stock_groups` 擴充
     再跑一次 backfill（這是第七輪帶來的附加好處，`stock_groups` 從此變成純標記表，
     跟延伸資料表的 backfill 範圍完全解耦）。
  2. **把一次性板塊流動分析（相關係數/lead-lag）沉澱成可重跑腳本**：
     `analysis/sector-flow-2026-07-16.md` 的相關係數/lead-lag/累計排行計算仍是第六輪
     分析當下跑的 pandas script，沒有存成 repo 裡的腳本，而且分析範圍還停留在第六輪的
     91 檔/13 板塊（**第八輪的週度動畫是不同產出物，只做了「切分彙整+動畫呈現」，
     沒有重跑相關係數/lead-lag 統計分析**）。若要更新這份統計分析報告，需要把分析邏輯
     寫成 `analyze_sector_flow.py` 之類的腳本，且應該用第七輪起的全市場 34 板塊資料
     重新跑一次（91 檔/13 板塊版本的結論只代表電子供應鏈內部）。這次沒有做，超出
     本輪任務範圍。
  3. **視覺化網頁**：讀 `data/tw_stocks.db` 的 `stocks` + `stock_groups` +
     `monthly_revenue`（近 3 年時序，全市場）+ `shareholding_concentration`
     （全市場）+ `institutional_flow_summary`/`institutional_flow_daily`（近 3 年
     時序，全市場）+ `sector_flow_daily`/`sector_flow_weekly`（全市場板塊彙總）+
     `group_flow_daily`/`group_flow_weekly`（族群彙總，**第十輪起資料已備妥**，但
     UI 呈現時務必遵守「族群數字不可跨族群相加」的語意，見 AGENTS.md）做
     「資金流向依板塊/族群」儀表板。第八輪的週度動畫（`analysis/
     sector-flow-weekly-animation.html`）算是這個方向的第一個可用產出物，但只是
     單一靜態 HTML 檔、只涵蓋板塊維度的 top-20，還不是完整的互動儀表板（沒有族群
     維度、沒有個股鑽取、沒有跟月營收/籌碼集中度串接；`export_sector_flow_
     animation.py` 目前只讀 `sector_flow_*`，若要做族群版動畫需要另外處理「族群重疊
     不能簡單取 top-N 加總」的呈現方式，尚未實作）。
  4. **【第十二輪起，腳本已完成，只差排程註冊】定期刷新排程**：`refresh_daily.py`
     已把十一個 build/export 腳本串成嚴格串行的每日刷新腳本（見上方第十二輪紀錄），
     `build_revenue_history.py`／`build_institutional_summary.py` 都是增量式，重跑
     成本低（前者最近 2 個月強制重抓 + 新月份自動涵蓋、後者只抓比本地最新日期更新的
     新交易日），每日增量成本：月營收約數秒、三大法人約數十秒~1 分鐘（1~2 個新交易日
     x 2 市場）、其餘板塊/族群彙總與匯出步驟純本地聚合皆秒級完成，整條鏈預期數分鐘
     內跑完。**尚未執行的最後一步是排程註冊本身**——使用者已明確要求本輪不要跑
     `schtasks`，需在主對話另行授權後註冊（README.md 已附建議指令，建議時段週一至
     五 18:30，比照 tw-momentum-scanner 排程時段慣例）。第七輪的全市場歷史 backfill
     已經做完，之後不需要再手動觸發長跑，除非未來想拉長歷史視窗（改
     `--target-trading-days`/`--months` 參數）。
- 關鍵決策 + 為什麼：
  - **【第八輪後續】長條圖 x 軸固定、TAIEX 折線圖 y 軸刻意不固定，兩者邏輯相反**：
    長條圖的 0 軸是「淨流入/淨流出的方向錨點」，每週亂跳會讓人以為板塊變了方向；
    固定 x 軸範圍解決了這個問題。但 TAIEX 折線圖畫的是**絕對價位**，3 年間指數從
    約 17,200 漲到約 47,700（超過 2.7 倍），若把 y 軸固定成全域範圍，單週正常的
    幾百點漲跌會被壓成一條幾乎看不出起伏的平線——這正是「當週走勢」最重要的資訊
    反而被抹掉。所以刻意讓 Chart.js 依每週實際區間自動縮放，兩張圖的軸固定策略
    表面上不一致，但各自服務不同的判讀需求，不是疏漏。
  - **【第八輪後續】TAIEX 選「大盤加權指數」而非「各板塊子指數」**：後者理論上跟
    20 個板塊的資金流更直接對應，但需要另外找/驗證 20 個子指數各自的 3 年歷史
    endpoint、抓取量是大盤指數的 20 倍，且 TWSE 是否有現成的子指數歷史封存頁面
    也還沒驗證過。已用 `AskUserQuestion` 明確詢問過，使用者選擇範圍小、實作快的
    大盤指數版本。未來如果要換成子指數，`taiex_daily` 這張表的設計不需要改，
    另開一張 `sector_index_daily`（`industry_name`, `date`, `close`）即可平行存在。
  - **【第八輪後續】TAIEX 47,700 的高點沒有被當成資料錯誤處理**：第一次寫測試時
    上限抓 40,000，實測直接測試失敗；沒有直接放寬上限了事，而是先查證這個數字
    出現在哪些日期（2026-06 附近連續多天都在 44,000~47,700 區間，不是單日尖峰），
    確認是真實資料且與本專案全程觀察到的「AI/半導體資金瘋狂湧入」現象吻合
    （2330 台積電月營收年增率 67%、半導體業三年法人淨流入全板塊最高等），才放寬
    測試上限到 100,000。這是「先查證再改測試」而不是「測試擋路就調鬆」的示範。
  - **【第八輪】週度動畫用「固定順序長條圖」而非「動態重排的賽跑動畫」**：真正的
    bar-chart-race（每幀依當週數值重新排序、板塊上下滑動換位）視覺效果更炫，但 20 個
    板塊每 600ms 就整批換位置會很難追蹤「特定板塊的長期趨勢」，而且 Chart.js 對
    reorder 動畫支援有限（label 順序變動會被當成新類別，不會平滑滑動，需要 D3 才能做
    到真正平滑的位置過渡）。改用固定順序（依三年活動量排序，最活躍的板塊固定在最上面）
    + 長條隨週次變長/縮短/變色（藍=流入、橘紅=流出），使用者可以穩定盯著某一列
    （例如「金融保險業」那一行）看它三年來的資金流入流出波動，可讀性更好，也不需要
    引入 D3 這個額外依賴。
  - **【第八輪】動畫只取活動量最大的 20 個板塊，不是全部 34 個**：34 個橫向長條在
    560px 高的畫布裡會擠到看不清楚標籤與數值，且水泥工業/玻璃陶瓷/農業科技業這類
    樣本數極小（5~7 檔）或波動幅度小的板塊，畫在同一張圖裡幾乎是貼著 0 的細條，
    對「觀察資金流動狀況」的核心目的沒什麼資訊量。用
    `SUM(ABS(total_net))`（三年活動量絕對值加總）排序取前 20，剛好把半導體業/
    金融保險業/電子零組件業等真正資金進出劇烈的板塊留下來。`--top-n` 參數可調整，
    若使用者想看全部 34 個或换一組板塊，重跑腳本即可，不需要改程式碼。
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
  - **【第六輪，已隨第七輪擴大，僅供歷史考證】板塊資金流分析範圍維持 91 檔 universe，
    沒有先擴大到全市場**：一開始發現 91 檔只橫跨全市場 34 個板塊中的 13 個、且高度
    集中在電子供應鏈，曾用 AskUserQuestion 詢問使用者是否要先花 60-70 分鐘擴大到
    全市場 1971 檔再分析，使用者當時選擇「先用現有 91 檔分析，範圍限定在電子/半導體
    供應鏈內部板塊」。**第七輪已完成全市場擴大**（`sector_flow_daily` 現在涵蓋
    34/34 個官方產業別板塊），`analysis/sector-flow-2026-07-16.md` 報告本身仍是
    91 檔/13 板塊版本的舊分析、尚未重新產出（見上方下一步第 2 點），使用報告結論時
    仍要注意這個限制，但**資料底層本身已經是全市場範圍**。
  - **【第七輪】三種延伸資料 + 板塊彙總從 `stock_groups` 擴大為 `stocks` 全市場**：
    使用者明確指示這是「改既有邏輯的篩選範圍，不是研究新資料源」，collector 本身
    （`revenue_history.py`／`shareholding.py`／`institutional_official.py`）都已在
    先前輪次驗證過可用、不需重新摸索欄位格式，本輪複查後確認篩選邏輯完全只存在於
    4 個 build 腳本的 `_target_stock_ids`/`_target_stocks` 函式與 `build_sector_
    flow.py` 的 SQL WHERE 子句，collector 完全不用改，縮小了本輪的改動面。
  - **【第七輪】三大法人／月營收 backfill 選擇「清空重建」而非「補洞式續傳」**：
    fetch-log 式可續傳表（`revenue_fetch_log`／`institutional_fetch_log`）只記錄
    「日期/年月是否已查過」，不記錄「當時篩選範圍」，擴大範圍後若試圖只補「舊範圍
    以外」的資料會需要對同一天/同一月重複判斷兩種不同語意的「已抓過」，複雜度不划算
    （對照組：全部重新 backfill 只需要多花跟第一次 backfill 相同的時間，91→1971 檔
    版本實測 64.8 分鐘 vs 第五輪 91 檔版本 66.8 分鐘，幾乎沒有額外時間成本，因為
    API 請求次數本來就不隨篩選範圍變化）。因此選擇「偵測到舊範圍殘留就整個清空重建」
    這個更簡單、更不容易留下半殘資料的做法，取代「嘗試只補洞」。
  - **【第七輪】測試斷言的極端值容忍上限刻意設得很寬（±5000 萬%），不是縮緊**：
    全市場實測後出現的極端 `yoy_pct`/`mom_pct`（最高 2526 萬%）都經過交叉核對確認是
    真實資料（基期極小/會計調整所致），不是 parsing bug。測試的核心目的是「防欄位
    錯位」（例如營收金額誤植進百分比欄位），revenue 欄位量級是 10^8~10^9，只要上限
    設在遠低於這個量級但高於實際觀察到的極端值（5000 萬 = 5x10^7）就仍能攔住真正的
    bug，同時不會因為全市場的合法極端值而產生假警報。
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
  - **【第四輪，已隨第五輪擴大範圍，僅供歷史考證】** `build_institutional_summary.py`
    第四輪版本單次執行約需 4-6 分鐘（只抓近 60 交易日）；**第五輪起改成近 3 年
    backfill，首次全量執行約需 60-70 分鐘**（見下方第五輪雷區），日常增量重跑則回到
    數十秒等級。
  - **【第五輪】MOPS 歷史封存頁面 row-parsing regex 絕對不要用 `\s*` 去吃數字前導
    空白**：`\s*` 跟緊接在後、同樣會吃空白的 `[^<]*` 對同一段空白有歧義的多種切法，
    在近 44 萬字元的單行 HTML 上會觸發災難性回溯（catastrophic backtracking），
    實測直接掛住、`timeout 20s` 都跑不完，且沒有任何錯誤訊息（看起來像網路卡住，
    其實是 CPU 在原地回溯）。修法：`[^<]*` 本身就會吃空白，取值後 `.strip()` 即可。
    見 `docs/data-sources.md` 第 13 節、`collectors/revenue_history.py` 的註解。
  - **【第五輪】MOPS 歷史封存頁面「備註」欄位的 `<td>` alignment 會隨備註內容改變**：
    備註為空（`-`）時是 `align=center`，有實際文字時變成 `align=left`。只比對
    `align=center` 會讓「有備註的公司」整列被靜默漏掉且不報錯——第一次實測時 TWSE
    2026-06 頁面因此漏掉 233 筆（含 2330 台積電本人），直到拿已知標的核對數字才發現。
    這是本專案目前為止最隱蔽的一個 bug：regex 語法完全正確、能執行、有輸出，只是
    悄悄漏資料。教訓：**新的 HTML parser 寫完後務必用至少一個已知標的（例如 2330）
    交叉核對抓到的資料是否存在、數字是否合理，不能只看「有沒有噴例外」**。
  - **【第五輪】`institutional_flow_daily`/`institutional_fetch_log` 長跑 backfill
    期間不要同時對同一個 `data/tw_stocks.db` 跑其他 build 腳本**：Python `sqlite3`
    對 DML 語句預設開隱式 transaction 直到明確 `commit()`，本專案 backfill 每湊滿
    20 個交易日才 commit 一次，這段期間內其他腳本嘗試寫入同一個 db 檔案會拿到
    `sqlite3.OperationalError: database is locked`（本專案實測時真的撞到，是在
    backfill 跑到一半手動測試 `build_fundamentals.py` 時發現的）。不是 bug，是
    SQLite 單寫入者限制，行為上排隊等待即可。
  - **【第五輪】3 檔 `-KY` 外國發行人股票（`3665`／`3673`／`4977`）在 MOPS 歷史封存
    頁面系列裡系統性查無資料**，但 `collectors/revenue.py` 用的 opendata
    （`t187ap05_L`）同一時間點卻查得到這 3 檔的最新一期。合理推測是 MOPS 對外國發行人
    另有獨立的月營收彙總報表系列（IFRS 申報路徑不同），本專案未進一步深究替代 URL，
    如實記錄成已知缺口，見 `docs/data-sources.md` 第 13 節。
  - **【第五輪】TWSE 2025-10（114年10月）archive 頁面回應 HTTP 200 但 body 完全是
    0 bytes**，跟其他「查無資料」月份（會回約 871 bytes、內文含「查無資料」字樣）
    不同，重試 3 次結果一致，判斷是該站台這個月資料的已知缺口，不是暫時性網路問題，
    也不是 parsing bug。`monthly_revenue` 因此只涵蓋 35/36 個目標月份，屬預期落差。
  - **【第七輪】fetch-log 式可續傳腳本擴大篩選範圍後，直接重跑不會自動涵蓋全部**：
    `revenue_fetch_log`／`institutional_fetch_log` 只記錄「日期/年月是否已抓過」，
    不記錄「當時的篩選範圍」。改完 `_target_stock_ids`/`_target_stocks` 後如果沒有
    同步處理這個問題就直接重跑，結果只有「強制重抓範圍」（月營收最近 2 個月/三大
    法人新增交易日）會是全市場範圍，其餘舊資料仍停留在舊範圍，且不會有任何錯誤訊息
    （本專案第七輪實測真的踩到：`build_revenue_history.py` 改完篩選邏輯重跑一次後，
    35 個月裡有 34 個月仍是 87~88 檔的舊範圍）。修法見上方「關鍵決策」第七輪
    「清空重建」條目與 `docs/data-sources.md` 第 14 節；**這類「篩選範圍變動 + 
    fetch-log 可續傳設計」組合以後如果再出現，預設就要清空重建，不要嘗試補洞**。
  - **【第七輪】MOPS 月營收封存頁面系統性缺席清單從 3 檔擴大確認為 104 檔 `-KY` 股**：
    第五輪只用 91 檔樣本觀察到 3 檔（`3665`／`3673`／`4977`），全市場實測後確認這是
    **系統性模式，不是這 3 檔特例**：全市場 105 檔「已掛牌但查無月營收歷史」的股票中
    104 檔都是 `-KY` 境外發行人（唯一例外是 `3717` 聯嘉投控，原因未查證），MOPS 封存
    頁面系列完全不含任何 `-KY` 發行人。見 `docs/data-sources.md` 第 15 節。
  - **【第七輪】全市場 `yoy_pct`/`mom_pct` 可能出現天文數字量級的極端值，且可能低於
    -100%**：91 檔概念股樣本從未出現過的兩種真實情況：(1) 去年同期營收基期接近 0
    的小型/殼公司，微小波動被放大成幾百萬%甚至幾千萬% YoY（例如 `2528` 實測
    25,266,600%）；(2) 部分營建類股採比例完工法認列營收，會計調整可能讓當月營收本身
    變成負數，導致 `yoy_pct`/`mom_pct` 低於 -100%（例如 `6024` 實測 -200.5%）。兩者
    都經過交叉核對（revenue 原始值 + 官方公告備註）確認是真實資料，不是 bug。日後
    若在這兩個欄位上寫新的驗證邏輯，**不能假設 -100% 是下界、也不能用低上限卡數值**，
    見 `docs/data-sources.md` 第 16 節。
- 怎麼跑 / 怎麼測：
  ```bash
  cd C:\CLAUDE\investing\tw-stock-db
  pip install -r requirements.txt
  python build_db.py                       # 整批刷新 stocks + stock_groups
  python build_revenue_history.py          # 【第七輪起全市場 1971 檔】月營收近 3 年歷史
                                            # （首次全量/偵測到舊範圍殘留時約 2 分鐘，
                                            # 之後日常重跑增量，數秒~數十秒）
  python build_fundamentals.py             # 【第七輪起全市場 1971 檔】籌碼集中度快照
                                            # （TDCC 全量下載，秒級完成）
  python build_institutional_summary.py    # 【第七輪起全市場 1971 檔】三大法人近 3 年
                                            # 歷史 + 近期彙總（TWSE T86 + TPEx
                                            # hedge_result.php 官方 API，首次全量/偵測到
                                            # 舊範圍殘留時約 60-70 分鐘（實測 64.8 分鐘），
                                            # 之後日常重跑增量，數十秒~1 分鐘內完成；
                                            # 中途中斷可直接重跑，會從資料庫現有進度接續）
  python build_sector_flow.py              # 【第七輪起全市場】板塊資金流彙總（純本地
                                            # 聚合，秒級完成，依賴上一步已跑過）
  python build_sector_flow_weekly.py       # 【第八輪】週度彙總（純本地聚合，秒級）
  python build_taiex.py                    # 【第八輪後續】TAIEX 大盤日收盤（38 次
                                            # 月份請求，數十秒）
  python build_daily_prices.py             # 【第九輪】個股歷史收盤價（可續傳，首次
                                            # 全量約 60-90 分鐘；中斷可直接重跑續傳，
                                            # 跑完務必用涵蓋率查詢驗收，見第九輪教訓）
  python build_sector_flow_value.py        # 【第九輪】金額口徑板塊資金流日/週表
                                            # （純本地聚合，秒級）
  python export_sector_flow_animation.py   # 【第九輪起預設金額模式】週度動畫 HTML
  python build_group_flow.py               # 【第十輪】族群（概念股）層級資金流彙總，
                                            # 股數+金額口徑、日+週粒度共四張表（純本地
                                            # 聚合，秒級；依賴 build_institutional_
                                            # summary.py/build_daily_prices.py/
                                            # build_sector_flow.py 都已跑過）
  python export_dashboard.py               # 【第十一輪，本專案主產出物】台股資金流向
                                            # 儀表板，唯讀彙整 -> dashboard.html（repo
                                            # 根目錄，數秒完成，~105KB）
  python -m pytest tests/ -q               # 驗證資料庫內容 + 匯出檔案（133 個測試）
  ```
  **注意**：`build_revenue_history.py` 與 `build_institutional_summary.py` 都不要跟
  其他 build 腳本同時併發執行（見上方 SQLite 單寫入者雷區）；五個 build 腳本本身
  彼此獨立，依序（或任意非併發順序）重跑都安全，但 `build_sector_flow.py` 依賴
  `build_institutional_summary.py` 已有資料，`build_group_flow.py` 依賴
  `build_institutional_summary.py`／`build_daily_prices.py`／`build_sector_flow.py`
  都已跑過。
