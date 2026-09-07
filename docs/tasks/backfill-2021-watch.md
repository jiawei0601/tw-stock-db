# 工單：看守 FinMind 歷史回填到 2021-01-01（執行方：Codex）

你是這張工單的唯一執行者，這份檔案是你唯一的指令來源。工作目錄 `C:/CLAUDE/專案-投資/tw-stock-db/`。全程繁體中文。可自行拆分子任務，但**同一時間只能有一個程序打 FinMind**。

## 背景
- `build_valuation.py --backfill 2021-01-01` 會把 256 檔股票的 PER/PBR、價格、季 EPS、月營收從 FinMind 回填到 2021-01-01（月營收到 2020-01-01），寫入 `data/tw_stocks.db` 的 per_daily / fm_price_daily / eps_quarterly / fm_revenue_monthly。
- FinMind 免費層每小時約 600 次請求。程式遇 402（額度用盡）或 403（IP 暫封，約 15 分鐘）會 **exit code 3** 結束；全部完成 exit 0。每檔寫完即 commit 到 DB，可隨時續跑。
- 總需求約 1024 個 (股票, dataset) 單位，預計 2 到 3 小時。
- 現在已有一個回填程序在跑（2026-09-07 13:21 啟動，指令列含 `build_valuation.py --backfill`）。**不要在它還活著時再起一個**。

## 步驟
1. 用 PowerShell `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'backfill' }` 確認有無回填程序。有就每 60 秒查一次直到它結束。
2. 迴圈：執行 `python build_valuation.py --backfill 2021-01-01 >> backfill.log 2>&1`，看 exit code：
   - 0 → 結束迴圈。
   - 3 → 等 15 分鐘再跑（403 時 log 會有 retry_after 秒數，照它等）。
   - 其他 → 把 log 最後 30 行寫進報告，停止並回報「異常」。
   最多迴圈 5 小時，超時也停止並回報。
3. 完成後執行 `python build_valuation.py --backfill-status`，並執行 `python -m pytest tests/test_valuation.py -q`。
4. 用 Python sqlite3 查每個表：`count(*)`、`count(distinct stock_id)`、`min(date)`（fm_revenue_monthly 用 ym）、最早日期晚於 2021-01-01 的股票清單（前 20 檔加總數）。

## 報告格式（寫到 `docs/tasks/reports/backfill-2021-report.md`，不要 commit）
```
# 回填報告 <日期時間>
- 結果：done | quota-timeout | error
- 迴圈次數 / 撞額度次數 / 總耗時
- --backfill-status 原文
- 各表：列數 / 股票數 / 最早日期
- 仍未達 2021-01-01 的股票（表、股號、最早日期、可能原因：上市較晚 / FinMind 無資料）
- pytest 結果一行
- 異常時：log 最後 30 行
```

## 禁止
- 不要修改 `build_valuation.py` 或任何程式碼；只能執行。
- 不要 git commit / push。
- 不要同時起兩個回填程序。
