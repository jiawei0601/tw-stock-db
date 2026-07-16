# tw-stock-db

台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）標記。
為「資金流向依板塊/族群視覺化」鋪路的資料底層 —— 本次任務範圍只到**資料庫建立＋官方產業別標記**，
不含視覺化網頁、不含族群/概念股標記（那是留給未來任務的擴充點，見 [HANDOFF.md](HANDOFF.md)）。

## 快速開始

```bash
pip install -r requirements.txt
python build_db.py            # 建置/刷新資料庫（idempotent，重跑安全）
python -m pytest tests/ -q    # 驗證資料庫內容合理
```

資料庫檔案：`data/tw_stocks.db`（SQLite，已納入版本控制，理由見 HANDOFF.md）。

## 資料表

- `stocks`：每檔股票一列，`stock_id` 為主鍵。欄位：`name`、`market`（'TWSE'/'TPEx'）、
  `isin`、`listed_date`、`industry_code`（官方兩碼數字代碼，少數新股可能為 NULL）、
  `industry_name`（官方產業別文字）、`cfi_code`、`updated_at`。
- `stock_groups`：族群/概念股標記表，**本次任務只建表結構、不填資料**（沒有官方資料來源，
  留給未來任務）。欄位：`stock_id`、`group_name`、`group_type`、`source`、`created_at`。

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

若資料庫更新後想重新產生：

```bash
python export_dashboard.py    # 讀 data/tw_stocks.db -> 覆寫 dashboard.html
```

## 專案定位與慣例

見 [AGENTS.md](AGENTS.md)。現況與下一步見 [HANDOFF.md](HANDOFF.md)。
