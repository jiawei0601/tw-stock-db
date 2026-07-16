# AGENTS.md — 專案統一規則（Claude Code 與 Antigravity 共用）

> Claude Code 透過 CLAUDE.md（內含 @AGENTS.md）讀本檔；Antigravity 原生讀本檔。
> 一份規則，兩邊共用，不分叉。

## 專案定位

台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）標記、族群/概念股
人工標記（`stock_groups`），以及**【第七輪起】全市場**（`stocks` 表全部，目前 1971 檔）
股票的月營收/籌碼集中度/三大法人動態三種延伸資料。個人投資用途，為未來「資金流向依
板塊/族群視覺化網頁」鋪路的資料底層。**視覺化網頁本身不在任何一輪任務範圍內**，見
HANDOFF.md 下一步。`stock_groups`（91 檔概念股標記）仍是有效資料，可用於未來「篩選出
概念股子集」的查詢用途，但不再是這三張延伸資料表的篩選範圍。

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
  - `python export_sector_flow_animation.py [--db-path PATH] [--top-n 20] [--out PATH]`
    （**【第八輪】** 從 `sector_flow_weekly`（+ **【第八輪後續】** `taiex_daily`）匯出
    一份獨立靜態 HTML 動畫 `analysis/sector-flow-weekly-animation.html`，只取活動量
    最大的 N 個板塊（預設 20，避免長條圖塞不下全部板塊看不清楚），每週附上該週
    TAIEX 每日收盤走勢，瀏覽器直接開啟即可播放，不需要伺服器；依賴
    `build_sector_flow_weekly.py` 與 `build_taiex.py` 都已跑過。
  - 八個 build/export 腳本彼此獨立、互不覆寫對方的表，可任意順序重跑（但不可同時併發跑，
    見上），`build_revenue_history.py`／`build_fundamentals.py`／
    `build_institutional_summary.py`／`build_sector_flow.py`／`build_taiex.py` 都依賴
    `stocks`/`sector_flow_daily` 已有資料，須先跑過 `build_db.py`；`build_sector_flow.py`
    另外依賴 `institutional_flow_daily` 已有資料；`build_sector_flow_weekly.py` 依賴
    `build_sector_flow.py` 已跑過；`build_taiex.py` 依賴 `sector_flow_daily` 已有資料
    （用來推斷預設日期範圍，也可用 `--start-date`/`--end-date` 手動指定跳過此依賴）；
    `export_sector_flow_animation.py` 依賴 `build_sector_flow_weekly.py` 與
    `build_taiex.py` 都已跑過。

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
export_sector_flow_animation.py    -> 【第八輪＋後續】orchestrate：sector_flow_weekly 取
                                  活動量最大的 top-N 板塊 + taiex_daily 依週切出每週指數點
                                  -> 匯出獨立靜態 HTML 動畫檔（長條圖+TAIEX走勢圖+合計列）
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
   `sector_flow_weekly` + `taiex_daily` 匯出獨立 HTML 檔，不屬於本節 schema 範圍。

## 資料源

見 `docs/data-sources.md`（endpoint 實測結果：欄位、編碼、陷阱、過濾邏輯）。

## 跨 agent 交接紀律
- repo 是唯一真相來源；交接資訊一律寫進 repo，不可只留私有記憶（Claude memory / Antigravity KI）。
- 交出前：測試綠 → commit 乾淨（絕不交髒工作區）→ 更新 HANDOFF.md → 更新 issue。
- 接手前：clean tree + pull → 讀 HANDOFF.md / issue / git log / 本檔 → 先複述現況與下一步再動手。
- 架構決策寫 docs/adr/；任務狀態走 issues。
