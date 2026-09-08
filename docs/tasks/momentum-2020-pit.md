# 2020 起動能回測：資料補齊與時點契約

使用者要求：FinMind 公開 API；自 2020 年開始選股，衡量持有 6、12 個月收益，避免偷看未來。

## 已凍結研究規格

- 主策略：每月底收盤後，以截至前一月底的 11 個月總報酬（12−1 動能）排序，取當時合格股票前 25%，等額買入。並列依代碼排序，不使用今日產業分類、估值、營收或事後收益篩選。
- 股價暖機自 2018-12-01 抓取，使 2019-12 月末訊號、2020-01 首個交易日建倉可計算；須有 12 個月交易歷史及至少 200 個有效交易日。正式開始時間以建倉日而非訊號日為準。
- 月末訊號固定後，下一市場交易日成交；需 OHLC、成交量與價格限制／停牌資訊判斷交易可行性。未成交部位維持現金，不事後換成下一名。
- 固定原始成員，分別持有 6／12 個日曆月；用對應月份首個市場交易日出場。未到期列 right_censored，不能當成 0 或當成完整期間回報。
- 每月一批的持有期報酬是重疊 cohort 統計，不能連乘成年化。另建 6／12 個等額資金梯隊，逐日記帳後才計算 CAGR、MDD、波動；暖機中的未投資資金列現金。
- 分別報告價格報酬、實際股利現金流總報酬、交易成本後報酬。基準為同一歷史股票池等權及 FinMind 加權／櫃買報酬指數。預設成本作研究假設：買賣各 0.3%，另報敏感度，並非聲稱適用所有股票的法定費率。
- 不將目前殖利率乘持有年數當實際股利；分割改股數，減資須保留現金對價。下市最後價格僅能估值，不能假定可以賣出；缺實際結算時同報未解決曝險與損失情境，不可發布為已實現績效。
- 已有 2022–2026 策略探索，不將重新選定的規則宣稱全樣本外。按年、相同成熟月份比較；6／12 月報酬用至少相应持有期長度的區塊重抽。

## 資料介面與信任邊界

### 程式模組契約（2026-09-08，先實作、後接完整資料）

- `momentum_engine.py`：標準庫；`run_backtest(data: dict, start: str='2020-01-01', end: str|None=None, fraction: float=0.25, buy_cost: float=0.003, sell_cost: float=0.003) -> dict`。
- 正規化輸入 JSON：`calendar: list[str]`（獨立市場日曆）、`prices: {sid: {date: {open,close,volume,buyable:bool,sellable:bool}}}`、`securities: list[{stock_id,start,end:null|str,known_at,source}]`（可有多個資格區間，end exclusive）、`events: list[{stock_id,date,known_at,kind,ratio?,cash?,pay_date?,source}]`。kind = split / distribution / delist；ratio 為每舊股取得新股數（distribution 預設 1），cash 為每舊股應收現金，pay_date 為入帳日，delist 現金為實際結算金額。securities 的 end 是事後有效期間資訊，只在到期時適用，不可預先從訊號剔除。
- `verified: {universe,events,execution,coverage}: bool` 與 `evidence: {各同名鍵: 非空來源字串}` 四項都通過才允許正式績效；這是人工驗收記錄，不是程式自行證明。
- 輸出含 `certification, signals, trades, cohorts, equity, summary, issues`；不完整輸入拒絕正式運算。`synthetic:true` 為合成展示，永不宣稱真實投資績效。
- `momentum_data.py`：`audit_cache(db_path: Path, manifest_path: Path) -> dict` 唯讀盤點下載；`load_cache(db_path: Path, manifest_path: Path, evidence_path: Path) -> dict` 從完整快取與已核對的 securities/events/execution 證據 JSON 正規化。不得猜股份比／股利，缺證據直接拒絕。
- `backtest_momentum.py`：CLI／報表／合成展示；`--audit` 不算收益，`--demo` 合成資料，`--input` 正規化 JSON，或 `--raw-db --manifest --evidence` 真資料接線。輸出 JSON、CSV、Markdown 與獨立 SVG 資產／回撤圖。
- 各模組分工只改自己檔案，避免覆寫正在執行的下載器。

程式已完成第一版，操作見 `docs/momentum-backtest.md`。補充事件規格：`distribution` 的 ratio 非1時必填 `shares_available_date`；股票股利先列權利、交付後才可出售。到期仍有未交付股份時，部位為 unresolved，不假裝可賣出。減資比例事件需對齊恢復交易日。到期後才收的現金股利由資金梯隊持續入帳，留在原格直到下次輪替再投入。

`fetch_momentum_pit.py` 負責下載，僅寫 `data/momentum_pit/`，不改既有 `data/tw_stocks.db`。

- SQLite `responses`：key = 正規化 query JSON；query、retrieved_at、status、HTTP status、body、sha256。成功空資料保留，失敗不視為完成。認證 header 不落盤。
- `TaiwanStockInfo` 聯集 `TaiwanStockDelisting` 作抓取候選，不是已證明完整的歷史可投資母體。Info.date 是更新日期，不可冒充上市日。四碼且不以 0 開頭僅是候選代碼過濾，歷史普通股身分／市場轉換仍需核對。
- 每候選下載 `TaiwanStockPrice`（OHLCV）、`TaiwanStockDividend`（實際現金與股票分配）、`TaiwanStockCapitalReductionReferencePrice`；另抓全市場分割、面額變動、下市、交易日與報酬指數。
- 完整清單固定在 manifest；任一候選下載失敗不得從分母刪除。完整下載也不等於通過無偏誤驗收。
- 公開 API 實測未提供 PriceAdj 權限。價格、事件各自保存，禁止將分割還原表標記成實際含息總報酬。

訊號計算器只能接收 `as_of` 以前的行情與當時生效事件；結果計算器才可讀取持有期未來資料。未來事件不能影響歷史股票排名。單純後向乘法還原在價格比率可抵消，不必然構成前視；但四捨五入、絕對價格濾網與錯誤事件比例可能破壞此性質，所以應直接用截至訊號日的事件重建。

## 驗收門檻

1. 2018-12 至截止日行情及當時存續普通股證據完整；包含已下市、合併、轉板、改名／代碼更用，不靠 2026 存活名單。
2. 成交／股利／分割／減資／下市結算事件可追溯，必要欄位缺漏有明確未解決狀態。
3. 刪除或任意改寫訊號日以後的資料，該日選股及權重完全不變；加入未來 IPO 不能改變舊排名。
4. 價格跳動不直接推算股份比；股利代理不得混入總报酬。
5. 期末缺價不刪除初始成員；交易日曆獨立於已下载的股票子集。
6. 完整資料核對後才產生正式收益表。資料下載中或缺歷史身分證據時，狀態保持 NOT_CERTIFIED。

## 2026-09-08 實查

- 原 `fm_price_daily` 與 `fm_price_adj_daily` 均 324,612 筆，2021-01-04 至 2026-09-04，缺 2019–2020。原 `daily_prices` 從 2023-06-13 才開始。
- 原下市表 133 筆，只有 1 個下市代碼在 `fm_price_daily` 有行情。
- FinMind 公開股價 API 可抓 2330 的 2019 行情；2456 已下市行情可抓（初步探測 729 筆）。全市場 Info 普通股候選 2,149 個代碼，更新日期不可用於上市判定。
- 本次為資料修復階段；2020 起正式收益率尚未產生。既有回測結果不能通過上述驗收。

參考：[FinMind 技術資料](https://finmind.github.io/tutor/TaiwanMarket/Technical/)、[FinMind 公開額度](https://finmind.github.io/en/quickstart/)、[傳統 2–12 月動能定義](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor_daily.html)。
