# 免費營收資料缺口盤點

日期：2026-09-09；盤點快照：2026-09-09T03:05:34Z；全程只讀本機資料，未發出網路請求。

產物是 `data/momentum_pit/revenue_archive/gap_inventory.json`。它提供後續補資料或探索回測可直接讀取的股票優先序與逐月缺口；這不是歷史時點認證，也不代表每個策略所需月份在當時都應已有公開營收。

## 口徑

- 股票池取 `data/momentum_pit/manifest.json` 的 2,162 個 `candidate_ids`。
- 價格只讀 `data/momentum_pit/finmind_raw.db` 的 `responses`；`query` JSON 必須是 `dataset=TaiwanStockPrice` 且 `data_id` 在候選清單。
- 實際 active 定義為 2020-01-01 至 2026-09-07 至少有一列 `Trading_Volume > 0`。每檔的 trade month `s` 是這些列的 distinct `YYYY-MM`。
- 每個 `s` 需要 `shift(s,k)`，`k=-1,-2,-3,-13,-14,-15`。核心月份限制在 2018-01 至 2026-07；2026-08 因封存仍不完整，另列 pending，不納核心缺口與 `target_ids`。
- 既有營收是兩個來源的 union：`revenue_snapshots JOIN pages ON url WHERE pages.error IS NULL`，以及 `csv_revenues`。
- direct `revenue_twd` 非 NULL 即視為存在。0 與負值都是已存在的原始值；NULL 或完全沒有列才是缺口。未展開 `row_json` 的前月／去年同月衍生欄位，避免把間接值冒充 direct 當月列。
- 兩個 SQLite 都以 `mode=ro`、30 秒 timeout 與明確 read transaction 讀取，避免和同時進行的寫入互相污染。本產物仍只是生成時點的快照，後續匯入完成後應重算。

## 結果

- 2,162 個候選中，2,056 檔在回測期實際有正成交量，106 檔沒有；與上一輪的 82 active／106 inactive 結論相容：82 指的是 188 個整檔缺官方封存營收候選中的 active 子集。
- 2,056 檔中，1,744 檔核心所需月份齊全；312 檔至少缺一個核心月份，共缺 6,550 個 stock-month。
- 6,550 個缺口按原始正成交量區間分桶：2,694 個早於 raw interval、3,856 個落在 raw interval 內、0 個晚於 raw interval。
- 312 檔中有 82 檔完全沒有 direct archive records。其餘 230 檔多為局部缺月或新上市股票的比較基期落在 direct archive／raw active interval 之前，因此不能把「312」解讀成 312 檔來源漏抓。
- 2026-08 另有 1,056 檔因 2026-09 trade month 需要、但 direct archive 尚無值；它們只在 `latest_2026_08_pending_ids`，不進核心 312 檔與 6,550 筆統計。
- 優先序前七檔固定為 1701、2456、3454、4141、5371、6457、9103；其餘有核心缺口股票按 stock ID 升序排列。

## JSON 合約

根層主要鍵如下：

- `scope`：資料來源、日期、active／presence 規則、月份 offsets 與核心上下界。
- `summary`：候選、active、完整／缺口股票數、stock-month 缺口與來源列數。
- `priority_seed`：既有七檔實際成交缺股優先清單。
- `target_ids`：312 個唯一股票 ID，先放 `priority_seed` 中實際有缺口者，再放其餘升序 ID。
- `latest_2026_08_pending_ids`：最新月獨立 pending 清單。
- `stocks.<stock_id>`：每個核心缺口股票的逐檔資料。

每個 `stocks.<stock_id>` 至少包含：

- `required_months`：由該檔實際 trade months 與六個 offsets 推得的核心月份，已去重排序。
- `missing_months`：`required_months` 中未找到 direct 非 NULL 值的月份。
- `raw_positive_volume_interval`：該檔整個價格快取內正成交量列的首末日期。
- `backtest_trade_month_interval`：回測期正成交量 trade months 的首月、末月與 distinct 月數。
- `direct_revenue_interval`：該檔 direct archive 首末月與 distinct 月數；完全無資料時為 NULL。
- `missing_by_raw_active_position`：把缺口分成 raw active 之前、之內、之後。
- `missing_by_direct_archive_position`：把缺口分成 direct archive 首月之前、首末月之間、末月之後；完全無 direct 列時另放 `no_direct_records`。
- `latest_2026_08`：該檔是否需要 2026-08、是否已有 direct 值，以及是否 pending。

## 解讀限制

raw 價格查詢從 2018-12-01 開始，所以常見的 2018-10、2018-11 缺口雖早於 raw interval，也不能只靠這份快取判定為上市前月份。較晚上市股票的比較基期同樣可能落在上市／登錄前；這些月份保留為 potential missing，不補 0、不用相鄰月或 `row_json` 衍生欄位虛構。

落在 raw active interval 內只證明「股票已有正成交量、direct archive union 沒有該月」，不單獨證明 MOPS／FinMind 漏抓，也不證明公司當時負有相同的公開申報義務。上市前、登錄或代號身分變更、來源省略與確實無公開值需要後續逐檔來源證據區分。

JSON 已驗證：`target_ids` 唯一、前七優先序正確、後續升序、`stocks` 鍵順序與 target 完全一致、每檔 `missing_months` 都是 `required_months` 子集、逐檔缺口加總為 6,550，且核心最大月份為 2026-07。
