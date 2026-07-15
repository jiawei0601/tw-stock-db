# AGENTS.md — 專案統一規則（Claude Code 與 Antigravity 共用）

> Claude Code 透過 CLAUDE.md（內含 @AGENTS.md）讀本檔；Antigravity 原生讀本檔。
> 一份規則，兩邊共用，不分叉。

## 專案定位

台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）標記。個人投資用途，
為未來「資金流向依板塊/族群視覺化網頁」鋪路的資料底層。**本次任務範圍只到資料庫建立＋
官方產業別標記，不含視覺化網頁、不含族群/概念股標記**（那類資料沒有官方來源，需另開任務，
見 HANDOFF.md 下一步）。

## 專案慣例

- 語言 / 框架：Python 3.11+、requests、sqlite3（標準庫）、pytest。**不用 pandas**
  （資料量小、只需一次性批次寫入，沒有 pandas 帶來的價值，保持依賴精簡）。
- 風格 / 命名：函式 `snake_case`；SQLite 欄位一律小寫 snake；日期一律 `YYYY-MM-DD` 字串。
  文件（README/AGENTS/HANDOFF）與 log 訊息一律繁體中文；程式碼註解可中英混用。
- 測試怎麼跑：`python -m pytest tests/ -q`（repo 根目錄執行；`conftest.py` 已把根目錄加入
  路徑）。**測試讀既有的 `data/tw_stocks.db`，不重新打網路 API** —— 跑測試前必須先跑過
  一次 `python build_db.py`，否則測試會直接失敗並提示要先建置。
- build / run：`python build_db.py [--db-path PATH]`（整批刷新，idempotent，重跑安全，
  預設寫入 `data/tw_stocks.db`）。

## 架構

```
collectors/isin.py          -> 證交所 ISIN 頁面，股票清單 + 官方產業別文字（主要來源）
collectors/company_info.py  -> TWSE/TPEx 公司基本資料 API，補齊官方產業別數字代碼
collectors/_http.py         -> 共用節流 + 重試 + 統一錯誤（比照 tw-momentum-scanner 設計）
models.py                   -> CollectorError（唯一錯誤型別）
build_db.py                 -> orchestrate：收集 -> 合併 -> 整批寫入 SQLite -> 印摘要
```

## Interface Contract（違反視為 bug）

1. **collectors/**：失敗一律拋 `models.CollectorError(source, msg, http_status, retriable)`；
   403/429/timeout/5xx → `retriable=True`；HTTP 200 但空資料或查無對應欄位 → 回空
   （`[]`/`{}`/`None`），**絕不臆測或造假資料**（例如查無 industry_code 就留 NULL，
   不要用同公司名稱猜測代碼）。TWSE/TPEx 皆需瀏覽器 UA（`_http.BROWSER_UA`）。
2. **build_db.py**：`collect_all()` 任一來源回空清單就整體中止（不寫入部分資料，避免
   資料庫出現「上市有、上櫃缺」的半殘狀態）；`write_db()` 是整批刷新（先清空 `stocks`
   表再整批寫入），不是逐筆 upsert —— 因為資料來源本身只提供「當下最新」快照。
3. **schema**：`stocks.stock_id` 為主鍵；`stock_groups` 這次只建表結構不填資料，
   PK 是 `(stock_id, group_name)`，供未來族群/概念股標記任務直接寫入，不必改 schema。

## 資料源

見 `docs/data-sources.md`（endpoint 實測結果：欄位、編碼、陷阱、過濾邏輯）。

## 跨 agent 交接紀律
- repo 是唯一真相來源；交接資訊一律寫進 repo，不可只留私有記憶（Claude memory / Antigravity KI）。
- 交出前：測試綠 → commit 乾淨（絕不交髒工作區）→ 更新 HANDOFF.md → 更新 issue。
- 接手前：clean tree + pull → 讀 HANDOFF.md / issue / git log / 本檔 → 先複述現況與下一步再動手。
- 架構決策寫 docs/adr/；任務狀態走 issues。
