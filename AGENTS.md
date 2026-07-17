# AGENTS.md — 專案統一規則（Claude Code 與 Antigravity 共用）

> Claude Code 透過 CLAUDE.md（內含 @AGENTS.md）讀本檔；Antigravity 原生讀本檔。
> 一份規則，兩邊共用，不分叉。

## 專案定位

台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）標記、族群/概念股
人工標記（`stock_groups`），以及**【第七輪起】全市場**（`stocks` 表全部，目前 1971 檔）
股票的月營收/籌碼集中度/三大法人動態三種延伸資料，**【第九輪起】** 三大法人板塊資金
流向新增「金額（新台幣元，股數 x 收盤價換算）」口徑，跟原有「股數」口徑並存（股數不能
直接跟市值加權的 TAIEX 指數比較，金額口徑才能對齊指數漲跌方向）。**【第十輪起】** 三大
法人資金流向新增「族群（概念股）層級」彙總（`group_flow_*`），是板塊層級（`sector_
flow_*`）的族群版，股數/金額兩口徑、日/週兩粒度皆有，共四張表。個人投資用途，為未來
「資金流向依板塊/族群視覺化網頁」鋪路的資料底層。`stock_groups`（91 檔概念股標記）
**【第十輪起】** 除了原有「篩選出概念股子集」的查詢用途，也是 `group_flow_*` 四張表的
聚合依據，但**族群成分重疊（一檔股票可屬多個族群），`group_flow_*` 的數字不可跨族群
相加**，跟 `sector_flow_*`（每檔股票唯一歸屬一個官方產業別）的加總語意不同，見下方
build/run 段落與 Interface Contract。**【第十一輪，本專案主產出物】** `export_dashboard.py`
匯出 `dashboard.html`（repo 根目錄）—— 完全獨立、免伺服器、瀏覽器雙擊即開的靜態台股
資金流向儀表板，整合前十輪累積的板塊/族群金額流、TAIEX、個股彙總（月營收年增率/籌碼
集中度）資料。**定位是資金結構的觀察工具，不是訊號產生器**——`analysis/` 下五份「法人
資金流向預測力系列」報告已確立「描述性有效、預測性全部失敗」的結論，頁面底部「使用
須知」區塊誠實陳述並列出五份報告檔名，見下方 build/run 段落。

## 專案慣例

- 語言 / 框架：Python 3.11+、requests、sqlite3（標準庫）、pytest。**不用 pandas**
  （資料量小、只需一次性批次寫入，沒有 pandas 帶來的價值，保持依賴精簡）。
- 風格 / 命名：函式 `snake_case`；SQLite 欄位一律小寫 snake；日期一律 `YYYY-MM-DD` 字串。
  文件（README/AGENTS/HANDOFF）與 log 訊息一律繁體中文；程式碼註解可中英混用。
- 測試怎麼跑：`python -m pytest tests/ -q`（repo 根目錄執行；`conftest.py` 已把根目錄加入
  路徑）。**測試讀既有的 `data/tw_stocks.db`，不重新打網路 API** —— 跑測試前必須先跑過
  一次 `python build_db.py`，否則測試會直接失敗並提示要先建置（`test_fundamentals_content.py`
  另外還依賴 `build_revenue_history.py` / `build_fundamentals.py` /
  `build_institutional_summary.py` 都跑過至少一次）。
- build / run：
  - `python build_db.py [--db-path PATH]`（`stocks` + `stock_groups` 表整批刷新，
    idempotent，重跑安全，預設寫入 `data/tw_stocks.db`）。
  - `python build_revenue_history.py [--db-path PATH] [--months N]`（**【第七輪起】
    篩選範圍改為 `stocks` 全市場**，目前 1971 檔，動態查詢不寫死清單；MOPS 歷史封存
    頁面逐月逐市場抓取近 3 年（預設 36 個月），PK 為 `(stock_id, ym)` 時序表；可續傳：
    `revenue_fetch_log` 表記錄已抓過的「市場+年月」，重跑會跳過已抓月份（最近 2 個月
    每次強制重抓），單月頁面失敗跳過繼續、不中止整體 backfill。首次全量 backfill 約需
    2 分鐘（72 次節流過的請求：36 個月 x 2 市場）。**內建舊範圍偵測**：若偵測到非最近
    幾個月的資料仍是舊版窄範圍（第七輪擴大前只涵蓋 91 檔），會自動清空
    `monthly_revenue`／`revenue_fetch_log` 後整個重新 backfill，不會誤判成「已抓過」
    而跳過（這是第七輪實測時真的踩到的坑，見 HANDOFF.md 第七輪紀錄）。
  - `python build_fundamentals.py [--db-path PATH]`（**【第七輪起】篩選範圍改為
    `stocks` 全市場**，目前 1971 檔，動態查詢不寫死清單，`shareholding_concentration`
    表整批快照覆蓋，idempotent；月營收已搬到 `build_revenue_history.py`，見上）。
  - `python build_institutional_summary.py [--db-path PATH] [--target-trading-days N]`
    （**【第七輪起】篩選範圍改為 `stocks` 全市場**，目前 1971 檔；用 TWSE `fund/T86`
    ＋ TPEx `3itrade_hedge_result.php` 官方按日期查詢 endpoint，`institutional_flow_daily`
    是近 3 年（預設 750 個交易日）累積式時序表 + 增量刷新；`institutional_
    flow_summary`（5/20/60 日彙總 + streak）計算邏輯不變，但改讀本地
    `institutional_flow_daily`，不再對外重複發送請求。可續傳：`institutional_fetch_log`
    表記錄已查過的「市場+日期」，每湊滿 20 個交易日就 commit 一次，單日失敗跳過繼續、
    不中止整體 backfill；首次全量 backfill 約需 60-70 分鐘（750 交易日 x 2 市場節流過
    的請求），之後重跑只抓增量新交易日，通常數十秒內完成。**不讀取**
    `C:\CLAUDE\tw_cache\institutional.db`，見下方第四輪決策。**內建舊範圍偵測**：若
    偵測到 `institutional_flow_daily` 涵蓋的相異股票數遠低於目標股票數（判定為第七輪
    擴大前只涵蓋 91 檔的殘留資料），會自動清空 `institutional_fetch_log`／
    `institutional_flow_daily`／`institutional_flow_summary` 後整個重新 backfill。
    **注意：backfill 進行中請勿同時對同一個 `data/tw_stocks.db` 跑其他 build 腳本**
    （SQLite 單寫入者限制，長跑期間持有未 commit 的交易會讓其他寫入直接
    `database is locked`，見 `docs/data-sources.md` 第 12 節）。
  - `python build_sector_flow.py [--db-path PATH]`（**【第七輪起】** `sector_flow_daily`
    表，從本地 `institutional_flow_daily` JOIN `stocks.industry_name` 聚合每個板塊每日
    三大法人買賣超合計，**涵蓋全市場**（不再限定 `stock_groups` 91 檔橫跨的板塊），
    不對外發送任何請求，秒級完成；整批快照覆蓋，idempotent；依賴
    `build_institutional_summary.py` 已跑過。
  - `python build_sector_flow_weekly.py [--db-path PATH] [--chunk-size 5]`（**【第八輪】**
    `sector_flow_weekly` 表，把 `sector_flow_daily` 依交易日序列每 5 筆切一組彙總
    （不對齊日曆週，避開假日造成的週期不整問題），純本地聚合、秒級完成；整批快照覆蓋，
    idempotent；依賴 `build_sector_flow.py` 已跑過。
  - `python build_taiex.py [--db-path PATH] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]`
    （**【第八輪後續】** `taiex_daily` 表，大盤加權指數（TAIEX）歷史日收盤，用 TWSE
    `FMTQIK` endpoint 依月份查詢（一次查一整月），日期範圍預設對齊
    `sector_flow_daily` 的範圍；`INSERT OR REPLACE by date`，可重跑覆蓋；首次全量約
    38 次節流過的請求（月份數），數十秒內完成。
  - `python build_daily_prices.py [--db-path PATH]`（**【第九輪】** `daily_prices` 表，
    TWSE `afterTrading/MI_INDEX` + TPEx `www/zh-tw/afterTrading/dailyQuotes` 官方按
    日期查詢 endpoint，逐日回補 `institutional_flow_daily` 實際出現過的股票 x 完整
    日期範圍的歷史收盤價，供「股數 x 收盤價 ≈ 金額」換算使用。範圍刻意比全市場窄
    （只抓三大法人資料實際出現過的股票，不是 `stocks` 全部 1971 檔）。可續傳：
    `price_fetch_log` 表記錄已查過的「市場+日期」，每湊滿 20 個已檢查日曆日就 commit
    一次，單日失敗跳過繼續、不中止整體 backfill；首次全量 backfill 約需 60-90 分鐘
    （TWSE/TPEx 各約 1130 個日曆日節流過的請求，量級比照三大法人 backfill）。
  - `python build_sector_flow_value.py [--db-path PATH] [--chunk-size 5]`（**【第九輪】**
    `sector_flow_value_daily` + `sector_flow_value_weekly` 表，把 `institutional_
    flow_daily` 的股數 JOIN `daily_prices` 的收盤價換算成「約略金額」（股數 x 收盤價，
    新台幣元，近似值不是精確成交金額）後依 `stocks.industry_name` 聚合，跟既有的
    `sector_flow_daily`/`sector_flow_weekly`（股數口徑）並存、不取代；週次編號
    （`week_index`）刻意對齊 `sector_flow_weekly` 同一套日期序列，兩者可互相對照。
    沒有收盤價的個股當天股數流向會被排除在金額加總之外（不臆測價格），`priced_
    stock_count`/`priced_days` 欄位記錄實際涵蓋率；純本地聚合，不對外發送請求，
    秒級完成；依賴 `build_daily_prices.py`／`build_sector_flow.py` 都已跑過。
  - `python export_sector_flow_animation.py [--db-path PATH] [--top-n 20] [--out PATH]
    [--mode {value,shares}]`（**【第八輪】+【第九輪改版】** 從 `sector_flow_value_
    weekly`（預設 `--mode value`，金額口徑）或 `sector_flow_weekly`（`--mode shares`，
    第八輪股數口徑舊行為，完整保留不刪除）匯出獨立靜態 HTML 動畫，預設輸出路徑依
    mode 不同（`analysis/sector-flow-weekly-animation.html` / `analysis/sector-flow-
    weekly-animation-shares.html`），+ `taiex_daily` 附上每週 TAIEX 走勢，只取活動量
    最大的 N 個板塊（預設 20；金額模式依金額活動量排序，股數模式依股數活動量排序，
    兩者排序依據不同、選出的板塊可能不同），瀏覽器直接開啟即可播放，不需要伺服器；
    金額模式若偵測到收盤價覆蓋率 <99.95% 會在頁面加一行誠實揭露的說明文字；依賴
    `build_sector_flow_weekly.py`／`build_sector_flow_value.py`／`build_taiex.py`
    都已跑過。
  - `python build_group_flow.py [--db-path PATH] [--chunk-size 5]`（**【第十輪】**
    `group_flow_daily`／`group_flow_weekly`（股數口徑）＋ `group_flow_value_daily`／
    `group_flow_value_weekly`（金額口徑）四張表，從本地 `institutional_flow_daily`
    JOIN `stock_groups`（19 個族群、91 檔標的，一檔可屬多個族群）聚合每個族群每日/每週
    三大法人買賣超合計，`group_flow_value_*` 另外 JOIN `daily_prices` 換算金額，schema
    與聚合邏輯完全比照 `sector_flow_daily`／`sector_flow_weekly`／`sector_flow_value_
    daily`／`sector_flow_value_weekly`（只是 `industry_name` 換成 `group_name`），
    `week_index` 沿用與 `sector_flow_weekly` 完全相同的交易日序列切分，可互相對照。
    純本地聚合，不對外發送任何請求，秒級完成；整批快照覆蓋，idempotent；依賴
    `build_institutional_summary.py`／`build_daily_prices.py`／`build_sector_flow.py`
    都已跑過（`build_sector_flow.py` 只是用來取得交易日序列，非資料依賴本身）。
    **【重要語意，跟 `sector_flow_*` 不同】** `stock_groups` 的 `stock_id` 可以同時
    對應多個 `group_name`（一檔股票屬於多個概念股族群），所以 `group_flow_*` 四張表
    的數字**不可跨族群相加**（例如把 19 個族群的 `total_net` 加總沒有意義，會重複
    計算橫跨多族群的股票，跟全市場/全概念股合計對不上）——這跟 `sector_flow_*`（每檔
    股票在 `stocks.industry_name` 裡只有唯一一個官方產業別，跨板塊加總才有意義）的
    加總語意完全不同，使用時務必只在「單一族群自己內部」的語意下解讀數字。
  - `python export_dashboard.py [--db-path PATH] [--out PATH]`（**【第十一輪，本專案
    主產出物】** 唯讀彙整 `sector_flow_value_weekly`/`_daily`、`group_flow_value_
    weekly`/`_daily`、`taiex_daily`、`institutional_flow_daily` JOIN `daily_prices`
    （現算個股近 20 交易日/近 4 週法人淨額金額，不依賴既有彙總表）、`monthly_
    revenue`、`shareholding_concentration`、`stocks`、`stock_groups`，匯出單一獨立
    HTML `dashboard.html`（repo 根目錄，資料內嵌 JSON、Chart.js 走 CDN，瀏覽器雙擊
    即開不需伺服器，比照 `export_sector_flow_animation.py` 的模式）。內容五大區塊：
    總覽（TAIEX 週線 + 全市場三大法人週度金額，可切換外資/投信/自營商/合計）、板塊
    熱力圖（34 板塊 x 150 週，HTML table 實作，藍=淨流入/橘紅=淨流出/白=近零）、板塊
    排行與下鑽（近 4/12 週前十後十 + 選板塊看週度走勢與成分股 top10）、族群視圖
    （19 族群縮小版，同樣的熱力圖/排行/下鑽，頁首強調族群數字不可跨族群加總）、投信
    特寫（全市場投信週度金額 + 目前連買/連賣天數 + 距下個季底倒數 + 季底作帳統計
    + 投信目前連買中的板塊前 5 名）。底部「使用須知」誠實陳述「這是資金結構的觀察
    工具、不是訊號產生器」，列出 `analysis/` 下五份「法人資金流向預測力系列」報告
    檔名（`taiex-flow-correlation-2026-07-16.md`／`flow-persistence-seasonality-
    2026-07-16.md`／`trust-streak-price-impact-2026-07-17.md`／`trust-streak-
    taiex-2026-07-17.md`／`sector-flow-entry-signal-2026-07-17.md`）。純本地聚合，
    不對外發送任何請求，數秒內完成；輸出檔案實測約 105KB（500KB 預算內）；依賴
    `build_sector_flow_value.py`／`build_group_flow.py`／`build_taiex.py`／
    `build_revenue_history.py`／`build_fundamentals.py` 都已跑過。
  - 十三個 build/export 腳本彼此獨立、互不覆寫對方的表，可任意順序重跑（但不可同時
    併發跑，見上），`build_revenue_history.py`／`build_fundamentals.py`／
    `build_institutional_summary.py`／`build_sector_flow.py`／`build_taiex.py` 都依賴
    `stocks`/`sector_flow_daily` 已有資料，須先跑過 `build_db.py`；`build_sector_flow.py`
    另外依賴 `institutional_flow_daily` 已有資料；`build_sector_flow_weekly.py` 依賴
    `build_sector_flow.py` 已跑過；`build_taiex.py` 依賴 `sector_flow_daily` 已有資料
    （用來推斷預設日期範圍，也可用 `--start-date`/`--end-date` 手動指定跳過此依賴）；
    `build_daily_prices.py` 依賴 `institutional_flow_daily` 已有資料（用來決定目標
    股票與日期範圍）；`build_sector_flow_value.py` 依賴 `build_daily_prices.py`／
    `build_sector_flow.py` 都已跑過；`export_sector_flow_animation.py` 依賴
    `build_sector_flow_weekly.py`／`build_sector_flow_value.py`／`build_taiex.py`
    都已跑過；`build_group_flow.py` 依賴 `build_institutional_summary.py`／
    `build_daily_prices.py`／`build_sector_flow.py` 都已跑過。
  - `python refresh_daily.py [--dry-run] [--no-publish]`（**【第十二輪，第 12 步 publish
    為本輪新增】每日刷新腳本**，把上述十一個 build/export 腳本 + 第 12 步 `publish`
    依相依順序串成一條**嚴格串行**的更新鏈：`build_institutional_summary.py` →
    `build_daily_prices.py` → `build_taiex.py` → `build_revenue_history.py` →
    `build_fundamentals.py` → `build_sector_flow.py` → `build_sector_flow_weekly.py` →
    `build_sector_flow_value.py` → `build_group_flow.py` →
    `export_sector_flow_animation.py` → `export_dashboard.py` → **`publish`**。前 11
    步每步用 `subprocess.run` 執行，開始/結束/耗時/成功失敗記錄到 `data/refresh.log`
    （append；超過 5MB 自動砍掉前半只保留後半，避免無限增長）。**第 12 步 publish**
    把匯出物發布到公開 GitHub repo：`git add` **只加入白名單路徑**
    `dashboard.html` 與 `analysis/*.html`（**絕不 `git add -A`**，防止任何意外檔案
    被自動推上公開 repo，見 HANDOFF.md「公開化紀律」）；`git diff --cached --quiet` 判斷無
    變更就跳過（log 記「無變更，跳過發布」，視為成功不計入失敗）；有變更則
    `git commit -m "每日自動更新 YYYY-MM-DD"` 後 `git push origin master`。**單步失敗
    不中止**——記錄後繼續跑後續步驟（增量抓取失敗隔天會自動補上，彙總/匯出步驟用現有
    資料照跑；publish push 失敗同理，記 log、計入失敗、不中止，隔天成功的 push 會涵蓋
    今天的變更），全部跑完後若有任何失敗，透過 `C:\CLAUDE\tools\telegram\notify.py` 的
    `send()` 發一則失敗摘要通知（比照 tw-momentum-scanner 的 `notifier/telegram.py`
    用法：sys.path 加入該目錄後 `import notify`；**通知失敗一律吞掉，不可讓 refresh
    當掉**）；全部成功時安靜結束不通知。`--dry-run` 只列印 12 步清單、不執行、不寫
    log。`--no-publish` 可跳過第 12 步 publish（本機測試/演練用，不影響前 11 步）。
    exit code：全成功 0、有失敗 1（供排程系統判斷）。**排程本身沿用既有
    `TwStockDbDaily`（週一至五 18:30），publish 步驟隨排程自動生效，不需要另外註冊**，
    見 HANDOFF.md 第十二輪紀錄。
  - 十三個 build/export 腳本（不含 `refresh_daily.py` 本身與 `build_db.py`）彼此獨立、
    互不覆寫對方的表，可任意順序重跑，但**不可同時併發跑**（SQLite 單寫入者限制）——
    這正是 `refresh_daily.py` 選擇嚴格串行、不做平行化的理由。

## 架構

```
collectors/isin.py                 -> 證交所 ISIN 頁面，股票清單 + 官方產業別文字（主要來源）
collectors/company_info.py         -> TWSE/TPEx 公司基本資料 API，補齊官方產業別數字代碼
collectors/revenue.py              -> TWSE/TPEx 月營收 opendata（最新一期全量，含年增率，
                                  現已不再被任何 build 腳本使用，僅保留供未來參考）
collectors/revenue_history.py      -> 【第五輪】MOPS 歷史封存頁面，逐月逐市場月營收（近 3 年）
collectors/shareholding.py         -> TDCC 集保結算所股權分散表 opendata（全市場，週更快照）
collectors/institutional_official.py -> TWSE T86 + TPEx 3itrade_hedge_result.php，
                                  依日期查詢三大法人買賣超（當日全市場，需逐日呼叫湊歷史）
collectors/_http.py                -> 共用節流 + 重試 + 統一錯誤（比照 tw-momentum-scanner 設計）
models.py                          -> CollectorError（唯一錯誤型別）
build_db.py                        -> orchestrate：stocks + stock_groups -> 整批寫入 SQLite -> 印摘要
build_revenue_history.py           -> 【第七輪起全市場】orchestrate：monthly_revenue 近 3 年歷史
                                  （處理 stocks 全市場股票）-> 逐月逐市場可續傳寫入 -> 印摘要
build_fundamentals.py              -> 【第七輪起全市場】orchestrate：shareholding_concentration
                                  （處理 stocks 全市場股票）-> 整批寫入 -> 印摘要
build_institutional_summary.py     -> 【第七輪起全市場】orchestrate：對 institutional_official.py
                                  增量 + 回補式查詢 TWSE/TPEx 近 3 年（約 750 個交易日）
                                  -> 從本地 institutional_flow_daily 彙總近 5/20/60 日
                                  法人買賣超 + streak -> 印摘要（處理 stocks 全市場股票）
build_sector_flow.py               -> 【第七輪起全市場】orchestrate：institutional_flow_daily
                                  JOIN stocks.industry_name -> 每板塊每日買賣超合計
                                  -> sector_flow_daily（純本地聚合，不對外發送請求，
                                  涵蓋全市場板塊）
build_sector_flow_weekly.py        -> 【第八輪】orchestrate：sector_flow_daily 依交易日
                                  序列每 5 筆切一組彙總 -> sector_flow_weekly（純本地聚合）
collectors/taiex.py                -> 【第八輪後續】TWSE FMTQIK，依月份查詢 TAIEX 每日收盤
build_taiex.py                     -> 【第八輪後續】orchestrate：collectors/taiex.py 逐月
                                  backfill -> taiex_daily
collectors/prices.py               -> 【第九輪】TWSE MI_INDEX + TPEx www/zh-tw/
                                  afterTrading/dailyQuotes，依日期查詢個股歷史收盤價
                                  （當日全市場，需逐日呼叫湊歷史，兩者皆已排查確認
                                  真正支援歷史查詢，非「日期參數被忽略、永遠回最新」
                                  的陷阱端點）
build_daily_prices.py              -> 【第九輪】orchestrate：對 collectors/prices.py
                                  增量 + 回補式查詢 institutional_flow_daily 涵蓋範圍
                                  的股票 x 日期 -> daily_prices（可續傳，範圍比全市場窄）
build_sector_flow_value.py         -> 【第九輪】orchestrate：institutional_flow_daily
                                  的股數 JOIN daily_prices 的收盤價 -> 約略金額
                                  -> 依 stocks.industry_name 聚合 -> sector_flow_
                                  value_daily + sector_flow_value_weekly（純本地
                                  聚合，跟股數口徑的 sector_flow_daily/weekly 並存）
export_sector_flow_animation.py    -> 【第八輪＋後續＋第九輪改版】orchestrate：
                                  sector_flow_value_weekly（預設，金額口徑）或
                                  sector_flow_weekly（--mode shares，股數口徑）取
                                  活動量最大的 top-N 板塊 + taiex_daily 依週切出每週指數點
                                  -> 匯出獨立靜態 HTML 動畫檔（長條圖+TAIEX走勢圖+合計列）
build_group_flow.py                -> 【第十輪】orchestrate：institutional_flow_daily
                                  JOIN stock_groups（19 族群/91 檔，一檔可屬多族群）
                                  -> group_flow_daily/weekly（股數口徑）；institutional_
                                  flow_daily 的股數 JOIN daily_prices 的收盤價 -> 約略
                                  金額 -> 依 stock_groups 聚合 -> group_flow_value_
                                  daily/weekly（金額口徑）；週切分沿用與 sector_flow_
                                  weekly 相同的交易日序列（純本地聚合，不對外發送請求，
                                  數字不可跨族群相加，見下方 Interface Contract）
export_dashboard.py                -> 【第十一輪，本專案主產出物】orchestrate：唯讀
                                  彙整 sector_flow_value_*/group_flow_value_*/
                                  taiex_daily/institutional_flow_daily JOIN
                                  daily_prices（現算個股近 4 週法人淨額）/monthly_
                                  revenue/shareholding_concentration/stocks/
                                  stock_groups -> 匯出獨立靜態 HTML 儀表板
                                  dashboard.html（總覽+板塊熱力圖+排行下鑽+族群
                                  視圖+投信特寫+使用須知，不寫入資料庫）
refresh_daily.py                   -> 【第十二輪，第 12 步 publish 為本輪新增】
                                  orchestrate：依序 subprocess.run 上述十一個
                                  build/export 腳本 + 第 12 步 publish（嚴格串行，
                                  SQLite 單寫入者），開始/結束/耗時/成功失敗記錄
                                  data/refresh.log（超過 5MB 自動輪替）；第 12 步
                                  publish 只 git add 白名單（dashboard.html +
                                  analysis/*.html，絕不 git add -A）-> 無變更跳過
                                  /有變更 commit+push origin master；單步失敗
                                  不中止、繼續跑後續步驟，結尾若有失敗才透過
                                  C:\CLAUDE\tools\telegram\notify.py 發摘要通知
                                  （通知失敗不擋 refresh）；--dry-run 只列印步驟
                                  （12 步）；--no-publish 跳過第 12 步；exit code
                                  反映整體成敗；不寫入資料庫本身
```

## Interface Contract（違反視為 bug）

1. **collectors/**：失敗一律拋 `models.CollectorError(source, msg, http_status, retriable)`；
   403/429/timeout/5xx → `retriable=True`；HTTP 200 但空資料或查無對應欄位 → 回空
   （`[]`/`{}`/`None`），**絕不臆測或造假資料**（例如查無 industry_code 就留 NULL，
   不要用同公司名稱猜測代碼）。TWSE/TPEx 皆需瀏覽器 UA（`_http.BROWSER_UA`）。
2. **build_db.py**：`collect_all()` 任一來源回空清單就整體中止（不寫入部分資料，避免
   資料庫出現「上市有、上櫃缺」的半殘狀態）；`write_db()` 是整批刷新（先清空 `stocks`
   表再整批寫入），不是逐筆 upsert —— 因為資料來源本身只提供「當下最新」快照。
3. **schema**：`stocks.stock_id` 為主鍵；`stock_groups` PK 是 `(stock_id, group_name)`。
   `shareholding_concentration` / `institutional_flow_summary` PK 為 `stock_id`（單一
   最新快照，覆蓋式，不是時序表）。**【第五輪起】** `monthly_revenue` PK 改為
   `(stock_id, ym)`——近 3 年月營收時序表，不再是單列快照；`institutional_flow_daily`
   PK 仍是 `(stock_id, date)`，但視窗從「近 60 交易日」擴大為「近 3 年（約 750 交易日）
   累積式時序表」。`institutional_flow_summary` 的 5/20/60 日彙總計算邏輯不變，只是
   資料來源改讀本地 `institutional_flow_daily`。**【第七輪】** `sector_flow_daily` PK
   為 `(industry_name, date)`，**範圍改為全市場**（`stocks` 表全部，不再限定
   `stock_groups` 91 檔橫跨的板塊），理由見 HANDOFF.md 第七輪紀錄。
   **【第七輪】`monthly_revenue`／`shareholding_concentration`／`institutional_
   flow_daily`／`institutional_flow_summary`／`sector_flow_daily` 這五張表的篩選範圍
   從 `stock_groups`（91 檔概念股）擴大為 `stocks` 全市場（目前 1971 檔），schema 本身
   （欄位、PK）不變，純粹是篩選範圍變寬，資料量大幅增加。`stock_groups` 表本身未受
   影響，仍是有效的概念股標記，可用於未來「篩選出概念股子集」的查詢用途。**
   **【第八輪】** `sector_flow_weekly` PK 為 `(industry_name, week_index)`，是
   `sector_flow_daily` 依交易日序列每 5 筆切一組的彙總（週次編號從 0 開始，不對齊
   日曆週），範圍同樣是全市場。**【第八輪後續】** `taiex_daily` PK 為 `date`，
   跟 `stock_groups`/`stock_id` 系列表無關（大盤層級，不分股票），快照式覆蓋
   （`INSERT OR REPLACE`）。`export_sector_flow_animation.py` 不寫入資料庫，只讀取
   `sector_flow_weekly`/`sector_flow_value_weekly` + `taiex_daily` 匯出獨立 HTML 檔，
   不屬於本節 schema 範圍。
   **【第九輪】** `daily_prices` PK 為 `(stock_id, date)`，範圍刻意比 `institutional_
   flow_daily`/`stocks` 全市場窄（只涵蓋三大法人資料實際出現過的股票，見上方 build/run
   說明），`close` 為當日收盤價（新台幣元），查無收盤價的 (stock_id, date) 組合單純
   不存在該列、不留 NULL 列（跟 `institutional_flow_summary`「查無資料就不寫那列」的
   既有設計哲學一致，見上方第三輪決策）。
   **【第九輪】** `sector_flow_value_daily` PK 為 `(industry_name, date)`，
   `sector_flow_value_weekly` PK 為 `(industry_name, week_index)`，跟股數口徑的
   `sector_flow_daily`/`sector_flow_weekly` 是**平行並存的兩張表**（不是同一張表加
   欄位），`week_index` 編號刻意對齊同一套日期序列。金額四欄位（`foreign_value`/
   `trust_value`/`dealer_value`/`total_value`）**允許 NULL**，語意是「當天/當週完全
   沒有任何成分股查得到收盤價、無法換算金額」，跟「換算出來剛好是 0」是不同語意，
   不可混用（呼叫端必須用 `IS NULL` 判斷，不能假設缺資料等於 0）。
   **【第十輪】** `group_flow_daily` PK 為 `(group_name, date)`，`group_flow_weekly`
   PK 為 `(group_name, week_index)`，`group_flow_value_daily` PK 為
   `(group_name, date)`，`group_flow_value_weekly` PK 為 `(group_name, week_index)`，
   四張表的欄位與 NULL 語意分別完全比照 `sector_flow_daily`／`sector_flow_weekly`／
   `sector_flow_value_daily`／`sector_flow_value_weekly`（只是 `industry_name` 換成
   `group_name`），`week_index` 沿用與 `sector_flow_weekly` 完全相同的交易日序列切分。
   **與 `sector_flow_*` 唯一但關鍵的語意差異**：`stock_groups` 的 `(stock_id,
   group_name)` 是多對多關係（一檔股票可同時屬於多個族群），而 `stocks.industry_name`
   是每檔股票唯一一個官方產業別（多對一）。因此 `group_flow_*` 四張表**每一列的數字
   只在該族群自己內部成立，絕不可跨族群相加**（例如對 19 個族群的某日 `total_net`
   做 `SUM` 會重複計算橫跨多族群的股票，結果會大於全市場/全概念股去重後的真實合計），
   跟 `sector_flow_*` 可以安全跨板塊加總（因為每檔股票只算一次）完全不同，呼叫端
   （含未來的視覺化網頁）必須把 `group_flow_*` 當成「單一族群的獨立時序」使用，不能
   當成可加總分解全市場資金流向的分量表。

## 資料源

見 `docs/data-sources.md`（endpoint 實測結果：欄位、編碼、陷阱、過濾邏輯）。

## 跨 agent 交接紀律
- repo 是唯一真相來源；交接資訊一律寫進 repo，不可只留私有記憶（Claude memory / Antigravity KI）。
- 交出前：測試綠 → commit 乾淨（絕不交髒工作區）→ 更新 HANDOFF.md → 更新 issue。
- 接手前：clean tree + pull → 讀 HANDOFF.md / issue / git log / 本檔 → 先複述現況與下一步再動手。
- 架構決策寫 docs/adr/；任務狀態走 issues。
