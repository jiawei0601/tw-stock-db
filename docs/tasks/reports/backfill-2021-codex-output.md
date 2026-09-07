已依工單完成執行，報告已寫入 [backfill-2021-report.md](C:/CLAUDE/專案-投資/tw-stock-db/docs/tasks/reports/backfill-2021-report.md)。

- **結果：done**，第 5 次續跑以 exit code 0 結束。
- 接手後遇到 4 次額度限制，每次等待 15 分鐘；看守至回填完成共 **1 小時 10 分 57 秒**。
- 測試結果：**33 passed**。
- 未修改程式碼，未 commit／push；目前沒有回填程序在執行。

| 資料表 | 列數 | 股票數 | 最早日期 |
|---|---:|---:|---|
| per_daily | 310,651 | 256 | 2021-01-04 |
| fm_price_daily | 324,612 | 253 | 2021-01-04 |
| eps_quarterly | 5,231 | 253 | 2021-03-31 |
| fm_revenue_monthly | 18,334 | 254 | 2019-12 |

**程序完成不代表資料全部達標：**`--backfill-status` 仍顯示 **172 個未達標單位**，而結束 log 顯示 `remaining=805`，兩者不一致。報告已保留原文、各表較晚起始股票的前 20 檔與總數，以及可能原因，未擅自修改判定邏輯。