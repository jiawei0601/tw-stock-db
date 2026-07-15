# AGENTS.md — 專案統一規則（Claude Code 與 Antigravity 共用）

> Claude Code 透過 CLAUDE.md（內含 @AGENTS.md）讀本檔；Antigravity 原生讀本檔。
> 一份規則，兩邊共用，不分叉。

## 專案定位

台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）標記、族群/概念股
人工標記（`stock_groups`），以及針對 `stock_groups` 名單股票的月營收/籌碼集中度/三大
法人動態三種延伸資料。個人投資用途，為未來「資金流向依板塊/族群視覺化網頁」鋪路的
資料底層。**視覺化網頁本身不在任何一輪任務範圍內**，見 HANDOFF.md 下一步。

## 專案慣例

- 語言 / 框架：Python 3.11+、requests、sqlite3（標準庫）、pytest。**不用 pandas**
  （資料量小、只需一次性批次寫入，沒有 pandas 帶來的價值，保持依賴精簡）。
- 風格 / 命名：函式 `snake_case`；SQLite 欄位一律小寫 snake；日期一律 `YYYY-MM-DD` 字串。
  文件（README/AGENTS/HANDOFF）與 log 訊息一律繁體中文；程式碼註解可中英混用。
- 測試怎麼跑：`python -m pytest tests/ -q`（repo 根目錄執行；`conftest.py` 已把根目錄加入
  路徑）。**測試讀既有的 `data/tw_stocks.db`，不重新打網路 API** —— 跑測試前必須先跑過
  一次 `python build_db.py`，否則測試會直接失敗並提示要先建置。
- build / run：
  - `python build_db.py [--db-path PATH]`（`stocks` + `stock_groups` 表整批刷新，
    idempotent，重跑安全，預設寫入 `data/tw_stocks.db`）。
  - `python build_fundamentals.py [--db-path PATH]`（`monthly_revenue` +
    `shareholding_concentration` 表，只針對 `stock_groups` 名單股票，動態查詢不寫死
    清單，整批快照覆蓋，idempotent）。
  - `python build_institutional_summary.py [--db-path PATH] [--institutional-db PATH]`
    （`institutional_flow_summary` + `institutional_flow_daily` 表，讀取
    `C:\CLAUDE\tw_cache\institutional.db` 唯讀共用資料源彙總，只針對 `stock_groups`
    名單股票，整批快照覆蓋，idempotent）。
  - 三個 build 腳本彼此獨立、互不覆寫對方的表，可任意順序重跑，但 `build_fundamentals.py`
    與 `build_institutional_summary.py` 都依賴 `stock_groups` 已有資料，須先跑過
    `build_db.py`。

## 架構

```
collectors/isin.py            -> 證交所 ISIN 頁面，股票清單 + 官方產業別文字（主要來源）
collectors/company_info.py    -> TWSE/TPEx 公司基本資料 API，補齊官方產業別數字代碼
collectors/revenue.py         -> TWSE/TPEx 月營收 opendata（最新一期全量，含年增率）
collectors/shareholding.py    -> TDCC 集保結算所股權分散表 opendata（全市場，週更快照）
collectors/_http.py           -> 共用節流 + 重試 + 統一錯誤（比照 tw-momentum-scanner 設計）
models.py                     -> CollectorError（唯一錯誤型別）
build_db.py                   -> orchestrate：stocks + stock_groups -> 整批寫入 SQLite -> 印摘要
build_fundamentals.py         -> orchestrate：monthly_revenue + shareholding_concentration
                                  （只處理 stock_groups 名單股票）-> 整批寫入 -> 印摘要
build_institutional_summary.py -> orchestrate：讀取 tw_cache/institutional.db（唯讀）
                                  彙總近 5/20/60 日法人買賣超 + streak -> 整批寫入 -> 印摘要
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
   `monthly_revenue` / `shareholding_concentration` / `institutional_flow_summary` PK 皆為
   `stock_id`（單一最新快照，覆蓋式，不是時序表）；`institutional_flow_daily` PK 是
   `(stock_id, date)`，是唯一保留短期歷史（近 60 交易日，明確時間窗）的表，其餘皆為
   快照覆蓋，理由見 HANDOFF.md 關鍵決策。

## 資料源

見 `docs/data-sources.md`（endpoint 實測結果：欄位、編碼、陷阱、過濾邏輯）。

## 跨 agent 交接紀律
- repo 是唯一真相來源；交接資訊一律寫進 repo，不可只留私有記憶（Claude memory / Antigravity KI）。
- 交出前：測試綠 → commit 乾淨（絕不交髒工作區）→ 更新 HANDOFF.md → 更新 issue。
- 接手前：clean tree + pull → 讀 HANDOFF.md / issue / git log / 本檔 → 先複述現況與下一步再動手。
- 架構決策寫 docs/adr/；任務狀態走 issues。
