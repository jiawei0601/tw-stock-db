# 動能選股加入單月營收轉強濾網

## 最新研究定義（2026-09-09）

使用者改選最近三個月營收金額依序增加、最新月YoY為正：`R(m)>R(m−1)>R(m−2)`且`YoY(m)>0`，明示為三個觀察月份。已在快照研究路徑SnapshotGate新增可選rule，不改嚴格版本證據RevenueFilter或實盤。

`compare_revenue_three_month.py --allow-snapshot-research`完成九組新舊／共同資料／15與30天延遲比較；新規則100萬至445.22／547.23萬元、MDD−43.77%／−42.10%，共同資料控制後亦優於舊規則全期收益及回撤，勝率略降且年度有取捨。完整結果與限制見 `analysis/momentum-revenue-three-month-2026-09-09.md`。108相關測試與九組前綴驗證通過；尚非無前視認證或樣本外驗證。以下原單月定義為舊規則及嚴格路徑的歷史合約。

日期：2026-09-09；負責：Codex。狀態：程式與測試完成；歷史時點資料待補，未完成2020起真實績效比較，未部署實盤。

## 已確認需求

使用者選擇單月營收年增率為正且加速，不採三個月合計。

以訊號日已可取得的最近一期營收月份 m 計算：

- `YoY(m) = Revenue(m) / Revenue(m−12) − 1`
- `YoY(m−1) = Revenue(m−1) / Revenue(m−13) − 1`
- 同時滿足 `YoY(m) > 0` 與 `YoY(m) > YoY(m−1)` 才通過。

例如前月年增8%、本月15%通過；前月25%、本月15%不通過；前月−20%、本月−5%也不通過。加速是年增率上升，不是營收月增率。

此濾網在既有動能＋三日平均成交金額大於1億元之後，只控制新倉。不另改排名股票池，也不因營收轉弱新增賣出條件。5%NAV新倉目標、30%高點回落與月底動能出場維持既有行為。五種星期比較皆採休市順延，尚未決定將實盤由週三改成週二。

## 程式合約

- `revenue_filter.py`：`RevenueFilter(records)`、`evaluate(stock_id, signal_day) -> dict`，以及可呼叫布林濾網。
- 每筆版本必須有 `stock_id`、`month` (YYYY-MM)、非負有限數 `revenue`、`known_on` (YYYY-MM-DD)、`source`。
- `known_on` 指該版本值有來源證據已可取得的日期，不能用月份標籤、目前頁面出表日或假設每月10日回填。程式不會自行查證來源文字；資料提供端必須留存證據。
- `known_on < signal_day` 才可使用，以免無盤中時刻的同日公告被提前使用；修訂保留多版本，依訊號日選當時最近版本，不以新值覆寫舊歷史。
- 四個比較月份缺任一期、去年同期基數不為正、資料過期均不通過。最近營收不得早於訊號月份前兩個月；此為明示的缺資料保護，非公告期限推定。
- `evaluate` 回傳是否通過、原因、使用月份、兩期年增率與來源證據。最新月份缺比較值時，不回退到更舊的好月份。
- `simulate_weekly(..., entry_filter=None)` 保持既有預設；提供 `RevenueFilter` 後只篩新倉。
- `compare_momentum_weekdays.py --revenue-file PATH` 使用營收JSON跑五種星期，輸出獨立 `_revenue` 資料夾，不覆寫無濾網結果。缺2020年前版本時直接報錯，不輸出全現金假績效。
- JSON根層要求 `availability_basis: "version_evidence"` 及 `records`；標籤本身不是時點認證。

## 真實資料查核

本機 `monthly_revenue` 目前65,325筆、1,850檔，月份2023-08至2026-08。collector明載announce_date是頁面出表日期，不是各公司歷史公告日；目前該欄範圍2026-07-14至2026-09-09。現有FinMind回測快取沒有TaiwanStockMonthRevenue。

實際公開API探測2330（2018-01-01至2026-09-09）成功HTTP/API 200，104筆，其中98筆create_time為空。原始回應存本機 `backtest/momentum_rerun_20260909_revenue_audit/finmind_probe.json`。date範例2018-02-01對應2018年1月營收，不能當正式公告日。

[FinMind官方說明](https://finmind.github.io/tutor/TaiwanMarket/Fundamental/)指出create_time自2026-04-21起才有值、代表進入FinMind資料庫時間，不等於公司正式公告時間，啟用當日的初始值亦非公告日。歷史修訂版本完整性仍需另行核實。公開API能提供數字，但目前無法據此認證2020年起無未來資訊的績效。

下一步需補齊可核實的歷史公告／版本資料（至少2018年底起，含年增暖機）。若另作固定延遲假設的探索回測，必須另名與明示假設，不得稱為已排除公告及修訂偏誤。未經使用者選擇，不把假設當已確認規則。

## 驗證

`python -m pytest tests/test_revenue_filter.py tests/test_weekly_hermes_momentum.py tests/test_dynamic_momentum.py tests/test_momentum_data.py tests/test_momentum_engine.py -q`：88項通過。

涵蓋正成長加速、減速／持平／負成長、零基數、缺月、同日公告、未來修訂、過期、版本衝突、拒絕非版本時點JSON、濾網只影響新倉及全通過時與原策略一致。這些是程式驗證，不代表資料已認證，也不是改善績效的證據。


## 2026-09-09後續補資料

已另開subagent完成官方公告／版本查核及主線全市場頁面補抓。独立archive合併190,905筆、1,975代號、2018-01至2026-08，含KY外國發行人及CSV補洞。仍缺原候選188檔（其中92有下市紀錄）、8月僅921檔、全市場初始公告及修訂時點未齊，尚不能跑認證無前視營收績效。詳見 `revenue-backfill.md`；TSMC2019兩份原始新聞稿的四筆樣本已實測通過濾網，但不是全市場績效證據。
