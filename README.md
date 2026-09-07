# tw-stock-db — 台股資金流向儀表板

**線上版：<https://jiawei0601.github.io/tw-stock-db/>**（每個交易日傍晚更新）

台股上市（TWSE）＋上櫃（TPEx）全市場 1,971 檔 × 近 3 年的三大法人資金流向資料庫與
視覺化儀表板：官方產業別（34 板塊）＋概念股族群（19 族群）雙維度、股數＋金額雙口徑、
TAIEX 對照、月營收年增率與籌碼集中度個股彙總。

**定位聲明：這是資金結構的觀察工具，不是訊號產生器。** 本 repo 的 `analysis/` 下有五份
「法人資金流向預測力」系列分析，結論一致：法人流向**描述當下有效（同日相關 ~0.54）、
預測未來全部失敗**（隔日大盤、指標擇時、個股連買事件、大盤連買事件、板塊輪動回測皆無
可交易 alpha）。本專案所有內容皆非投資建議。

資料來源：TWSE / TPEx / TDCC / MOPS 官方公開 API 與頁面（詳見
[docs/data-sources.md](docs/data-sources.md)），資料為官方公開資訊之彙整。

## 快速開始

```bash
pip install -r requirements.txt
python build_db.py            # 建置股票基本資料（idempotent，重跑安全）
python build_institutional_summary.py   # 三大法人 3 年回補（首次約 60-70 分鐘）
python build_daily_prices.py            # 收盤價 3 年回補（首次約 60-90 分鐘）
python build_taiex.py && python build_revenue_history.py && python build_fundamentals.py
python build_sector_flow.py && python build_sector_flow_weekly.py && python build_sector_flow_value.py
python build_group_flow.py
python export_dashboard.py    # 產出 dashboard.html
python -m pytest tests/ -q    # 驗證
```

資料庫檔案：`data/tw_stocks.db`（SQLite，~240MB，**不納入版本控制**——超過 GitHub 單檔
上限，用上列 build 腳本可完整重建；歷史細節見 HANDOFF.md）。

## 資料表

- `stocks`：每檔股票一列，`stock_id` 為主鍵。欄位：`name`、`market`（'TWSE'/'TPEx'）、
  `isin`、`listed_date`、`industry_code`（官方兩碼數字代碼，少數新股可能為 NULL）、
  `industry_name`（官方產業別文字）、`cfi_code`、`updated_at`。
- `stock_groups`：族群/概念股標記表，**本次任務只建表結構、不填資料**（沒有官方資料來源，
  留給未來任務）。欄位：`stock_id`、`group_name`、`group_type`、`source`、`created_at`。
- `per_daily` / `eps_quarterly` / `valuation_fetch_log` / `valuation_screen`：估值篩選表
  （AI 供應鏈 83 檔 + 半導體全市場），詳見下方「估值篩選（build_valuation.py）」小節。

## 資料來源

見 [docs/data-sources.md](docs/data-sources.md)，記錄了實測過的 endpoint 行為、編碼陷阱、
過濾邏輯（為何排除 ETF/權證/TDR/REITs，為何保留創新板）。

## 怎麼打開儀表板

**【第十一輪】本專案主產出物**：`dashboard.html`（repo 根目錄）—— 完全獨立、免伺服器的
台股資金流向儀表板，直接用瀏覽器雙擊打開即可（不需要 `python -m http.server` 或任何
安裝步驟），資料已全部內嵌成 JSON，不會對外發送任何請求。內容涵蓋：TAIEX 週線 + 全市場
三大法人週度金額、34 板塊熱力圖、板塊排行與下鑽（成分股表）、19 族群縮小版視圖、投信
特寫（連買/連賣天數、季底作帳統計）。底部「使用須知」誠實陳述：**這是資金結構的觀察
工具，不是訊號產生器**，並列出五份法人流向預測力分析報告的檔名（`analysis/` 下）。
**【第十三輪】板塊熱力圖區塊新增排序自訂**：可切換「活動量（預設）／名稱筆劃／自訂」
三種排序，自訂模式支援拖放（HTML5 drag and drop）＋每列 ↑/↓ 按鈕重排，排序結果同時
套用到熱力圖列順序與下鑽選單選項順序（排行榜依資料排序不受影響）；自訂排序存在瀏覽器
`localStorage`（跟資料庫/repo 無關，是使用者端各自的個人化設定，每日重產頁面不會清掉）。
**【第十四輪】六大區塊（總覽/板塊熱力圖/板塊排行與下鑽/族群視圖/投信特寫/使用須知）
全部可收折**：點區塊標題整行切換收合/展開、標題左側 ▼/▶ 指示符，導覽列尾端加「全部
展開／全部收合」按鈕，錨點連結點擊時若目標區塊收合中會自動展開再捲動；預設全部展開，
收合狀態存 `localStorage`（跟排序偏好同一套機制，各自獨立），方便使用者「收一次、之後
每次打開只看自己要對照的區塊」。

若資料庫更新後想重新產生：

```bash
python export_dashboard.py    # 讀 data/tw_stocks.db -> 覆寫 dashboard.html
```

## 每日更新

**【第十二輪，第 12 步 publish 為後續追加】** `refresh_daily.py` 把完整更新鏈（三大
法人 → 收盤價 → TAIEX → 月營收 → 籌碼集中度 → 板塊/族群彙總 → 動畫/儀表板匯出，共
11 步 → **第 12 步 publish：把 `dashboard.html`/`analysis/*.html` 的變更自動 commit +
push 到這個公開 repo，讓 GitHub Pages 內容跟著更新**）串成一支腳本，依序嚴格串行執行
（SQLite 單寫入者，不可併發跑）。單步失敗不中止，記錄後繼續跑後續步驟；全部跑完後若有
任何失敗會透過 Telegram 發一則失敗摘要（全部成功則安靜結束、不通知）。publish 步驟只
`git add` 白名單路徑（絕不 `git add -A`），無變更就安靜跳過。

手動執行：

```bash
python refresh_daily.py                # 依序跑完 12 步，記錄到 data/refresh.log
python refresh_daily.py --dry-run      # 只列印步驟清單，不執行
python refresh_daily.py --no-publish   # 前 11 步照跑，跳過第 12 步 publish（本機測試用）
```

本機排程 `TwStockDbDaily`（週一至五 18:30）已註冊，細節與直譯器依賴警告見
[HANDOFF.md](HANDOFF.md) 第十二輪紀錄。停用：`schtasks /delete /tn TwStockDbDaily /f`。

## 估值篩選（build_valuation.py）

合併自舊 `ai-valuation-screen/` 兩支散裝腳本（`ai_valuation_v2.py` AI 供應鏈 83 檔、
`semi_screen.py` 半導體全市場）的 PER/PBR 三年分位 + 八季 EPS 品質篩選，統一寫進本
repo 的 SQLite，脫離手動 CSV/JSON cache 流程：

```
python build_valuation.py --import-cache <cache.json> [--import-cache <another.json> ...]
                           --screen
```

- `--import-cache`：把舊腳本留下的 JSON cache（key 格式 `股號:dataset:起日`）灌入
  `per_daily`/`eps_quarterly`；`daily_prices` 只補缺，且會過濾掉 close<=0 或超出
  `institutional_flow_daily` 涵蓋範圍的異常列（維持既有 invariant）。可重複執行、
  可疊加多份 cache。
- `--fetch`：對 universe（AI 供應鏈 + `stocks` 表 industry_name 含「半導體」的全市場）
  中仍缺資料的股票才打 FinMind API 補抓，每檔請求間隔 0.6 秒。**FinMind 免費額度有限
  且耗盡後 IP 會暫時被 403 封鎖**——遇 402/403 會立刻停止並把進度寫進
  `valuation_fetch_log`，下次重跑：
  ```
  python build_valuation.py --fetch
  ```
  會自動跳過已有資料的股票、從中斷處續抓，不需要額外參數。
- `--screen`：純本地運算（PER 位置、合理價區間、PBR 分位、eps_cv/loss_q/eps_ttm_growth、
  `split_flag`、`band_ok`），寫入 `valuation_screen`。**`split_flag=True` 時 `band_ok`
  一律強制 `False`**——緯穎（6669）2026-09-02 一拆三（收盤價 7800→2610）是本輪新增的
  已知盲點修正，原 `ai_valuation_v2.py` 完全沒有分割偵測、`semi_screen.py` 雖有偵測但
  只套用在半導體 universe，本腳本統一套用到兩個 universe。

## 專案定位與慣例

見 [AGENTS.md](AGENTS.md)。現況與下一步見 [HANDOFF.md](HANDOFF.md)。
