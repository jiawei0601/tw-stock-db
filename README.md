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

## 專案定位與慣例

見 [AGENTS.md](AGENTS.md)。現況與下一步見 [HANDOFF.md](HANDOFF.md)。
