# 提高勝率的進場條件比較（2026-09-09）

狀態：12組探索回測完成。使用者要求分別測試前述個股趨勢、大盤趨勢、避免追高。

## 結果（2026-09-09）

- 所有新增條件的勝率皆未超過相同延遲的現有營收基準，先前提高勝率的假設未獲支持。
- 個股趨勢15天期末600.27萬，但勝率42.70%低於44.84%、MDD−47.88%更差；30天收益亦不如基準。不可只挑15天收益下結論。
- 單獨大盤條件兩種延遲都降低勝率／收益；平均現金升到約21%。
- 三項合併15／30天期末561.90／577.90萬，MDD−34.67%／−39.75%，PF2.214／2.272；勝率43.97%／45.54%仍低於基準。平均現金約22%、交易423／426筆，是收益／回撤取捨而非提高勝率的證據。
- 兩個基準期末與591筆交易精確重現；12組截斷模擬與5,243筆技術特徵前綴檢查一致。報告與HTML見 `analysis/momentum-entry-filters-2026-09-09.md`、ignored `backtest/momentum_rerun_20260909_entry_filters/`。
- 官方價格指數94個月、1886日，本機790筆重疊全一致。原calendar中的2026-07-10已有load_data排除（無個股及報酬指數成交觀測），本輪沿用excluded_dates，未新增修日／補值。實際有效日曆下市場MA完整。
- 未更動實盤、原歷史價格、嚴格RevenueFilter；快照時點／公司行動／樣本反覆使用等限制延續。合約中worker及主線責任保留下方供重現。
- 最終137相關測試通過；獨立唯讀審查無實質問題。官方日曆coverage元資料最後調整後，指數值fingerprint完全相同，已刷新結果來源檔hash及HTML。兩圖12條NAV線結構確認完成。

## 本輪固定規則

基準為3−1動能＋三日均成交金額>1億＋三個已可用月份營收金額嚴格遞增且最新YoY>0。研究營收月底後15／30日曆天假設可知、次日才使用；5%NAV、100萬元、週三休市順延、次交易日開盤、30%高點回落、月底動能出場不變。

- trend：訊號日close > SMA20(t)，且SMA20(t) > SMA20(t−5個市場交易日)。
- market：訊號日加權價格指數close > SMA60(t)。不可代用TaiwanStockTotalReturnIndex。
- trend_cap：trend且close <= 1.10*SMA20(t)。10%為本輪預先固定研究門檻，未做網格挑選。
- trend_market：trend且market。
- all：trend、market及10%乖離上限，作三項合併檢查。
- 每種15／30天各跑base及上列5組，共12組。僅篩新倉、不改出場。
- 個股MA用當日以前市場日曆連續25日收盤；缺任一所需價格拒絕。分割／面額變更只用截至訊號日已發生事件，把窗內歷史價換為訊號日單位，再算兩個均線；不使用未來事件。原始股利／公司行動缺口仍沿用研究限制。

## 獨立大盤資料模組合約（主線→資料worker）

所有權：worker僅負責 `backfill_entry_market_index.py`、`tests/test_entry_market_index.py` 與 ignored `data/momentum_pit/entry_market_index/`。其他檔案由主線負責；不得回退其他agent修改。

- `load_market_prices(path: pathlib.Path) -> dict[str,float]`：讀下列JSON，僅接受finite positive close、唯一YYYY-MM-DD，不符合時ValueError，不可默默覆寫重複日期。不得連網。
- 可執行CLI `python backfill_entry_market_index.py`：官方TWSE加權價格指數，2018-12-01至2026-09-07（含MA暖機），輸出 `data/momentum_pit/entry_market_index/market_prices.json`。
- JSON根：`index_kind: "TAIEX_PRICE"`、`source`字串、`start`、`end`、`fetched_at`、`records: [{date: str, close: float},...]`、`coverage`。保存raw回應或可核實來源fingerprint至相同目錄；不修改tw_stocks.db或原finmind_raw.db。
- 可重用collectors/taiex.py官方FMTQIK解析；必要時以官方歷史index頁替代。已有本機taiex_daily可作交叉查核；不可誤用含息指數。節流、快取，連續兩次同命令失敗換診斷，不做無限重試。
- coverage須對照原FinMind有效回測calendar，確認2020-01-02起訊號日之前有完整60市場日資料，輸出缺日；發現差異向主線回報，不自行填值。參考原calendar可能含非交易日期，以實際官方交易紀錄／原observed日曆查核。
- 驗收：真實下載完成、來源明確、日期/數值驗證tests、與本機2023-06..2026-08重疊值一致或報告差異。無法完整下載時回報障礙，不改市場定義。worker不commit，由主線整合。

## 主線驗收

新TechnicalEntryGate需可重現前綴：以截斷的價格、事件、日曆、指數重新建構後，先前所有技術特徵相同；全期與2022年底截斷模擬NAV／賣出一致。兩個基準與上一輪精確相同，營收與價格fingerprint不變。報告含勝率、PF、單筆報酬、總收益、MDD、交易數、平均現金及年度，HTML圖100萬元一格。不可稱為PIT認證或獨立樣本外驗證。
