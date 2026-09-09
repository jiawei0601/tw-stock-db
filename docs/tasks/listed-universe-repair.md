# 回測限定歷史上市上櫃（2026-09-09）

使用者明示僅限上市上櫃。股票池須依當日有效資格，不能把現在上市身分套到興櫃時段。TWSE創新板仍是上市；已核實上櫃轉上市可以保留轉板前上櫃期間。原200日暖機只累計當時已上市／上櫃的有效行情。

## 本輪結果與狀態

- 已完成13組限定版研究：純動能＋原12組營收/進場條件。基準營收15／30天期末422.59／468.47萬；三項合併347.35／382.78萬。先前三項合併561.90／577.90萬的收益優勢不再成立，仍有較低全期MDD但收益更低。
- 全部成交逐筆核對訊號日、成交日有效資格與200日有效暖機，13組前綴及5006筆技術特徵截斷比較通過；期末資格外未平倉0檔。7610沒有任何買入成交，原2月訊號上市後僅101日，200th上市有效價格日為2026-07-09。
- 來源範圍2002候選有有效區間，160未知（75檔原始行情非空）；歷史24檔TWSE起迄補回。42檔轉板已核實但原TPEX起日未知，prior區間保守排除。原始bulk及覆蓋限制在ignored artifact，不聲稱完整PIT。原始價格排除233,866列後有效3,310,695列。
- 這次只改市場資格，不新增「已上市TDR必須排除」的資產類別規則；官方TWSE current master中的已上市TDR仍按有效日期處理，不能僅因本機ordinary-stock snapshot缺列就刪除。
- `compare_momentum_entry_filters.py --allow-snapshot-research`預設限定版，產物 `backtest/momentum_rerun_20260909_entry_filters_listed/`；legacy僅用`--legacy-unrestricted`重現。舊HTML與四份近期研究分析加更正標記，不刪舊結果。
- 限定版報告 `analysis/momentum-entry-listed-2026-09-09.md`；`old_scope_violations.csv`列出舊交易因未核實區間／未知股票／暖機不足而不符新口徑，前者含缺轉板史，不全等於興櫃。

## 主線已建立的可執行合約

`listed_universe.py`：`ListedUniverse(records)`、`from_json(Path)`、`eligible(stock_id, day)->bool`、`restrict_prices(prices)->dict`。records每筆有stock_id、market(TWSE/TPEX)、start(含)、end(不含或null)、source，額外欄位保留。未知sid/區間不通過。restricted prices送入排名、動能、MA及交易模擬；不只最後買入關卡。

JSON根 `universe_kind: "TWSE_TPEX_EFFECTIVE_INTERVALS"`、`records`、`coverage`、`sources`、`limitations`。不宣稱資格完整；缺起點與轉板史明列，未知禁止回填。資料來源保留raw/hash/取得時間，effective日期不是杜撰known_on。

## 獨立資料worker責任（主線→Sol）

僅擁有 `backfill_listed_universe.py`、`tests/test_backfill_listed_universe.py`、ignored `data/momentum_pit/listed_universe/`。不修改主線listed_universe.py、引擎、原DB或其他agent檔案，不commit。

可執行 `python backfill_listed_universe.py` 產出 `data/momentum_pit/listed_universe/intervals.json`，遵守上方schema，涵蓋manifest候選2162股票。以官方上市／上櫃起迄與轉板名單為主；已有本機stocks(stock_id,name,market,listed_date,cfi_code)約1971檔及fm_delisting，官方crosscheck資料有下市清單可讀。先查本機可用資料，官方查詢需快取，禁止以FinMind Info.date或首次有價格日期猜上市日。

- 日期要真的市場資格開始，終止上市／櫃日作end exclusive。起日未知不能猜1900年；下市股不可全漏掉而宣称完整。
- 至少核實7610上市2025-09-09；對其他新上市候選應全量套規則，不能只修單股。
- 轉上市公司的TWSE listed_date不必然等於首次上櫃日。收集可核實轉板史／終止上櫃名單；若有缺口明列，不把缺口当不存在或把最新TWSE日期之前全当興櫃。
- 需要回報：有資料候選数、未知数、下市股涵蓋、转板區間证據／缺口、7610區間、来源及验证。遇來源阻碍及时告知，不无限搜索。
- 測試parse日期、起迄界限、轉板多區間、未知拒絕、raw可重現。依需要重用既有collectors，但勿修改其邏輯。

## 主線責任與驗收

將研究比較新增明示上市上櫃選項，輸出獨立目錄，保留先前報告當作被修正的舊結果。重跑12組（營收15/30×6種entry），另跑無營收動能基準，以檢查資料池的影響。確認所有買入訊號及成交日符合資格、200日暖機不含興櫃、7610早期成交消失、前綴一致。歷史終止資格後不假造賣出價或用未來日期提前出清，缺清算仍列未解決，不認證PIT。

本輪不擴充即時券商／Hetzner部署；以修正研究回測為範圍，實盤若需要部署應以已驗證資料再同步，不在目前自動觸發交易。
