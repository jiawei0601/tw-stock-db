# 第二輪有效性修復數字重核＋殘項修復（第三輪）執行報告

執行日期：2026-09-08；執行方：Codex。範圍依 `docs/tasks/backtest-validity-3-recheck.md`。數字單位除 N 外為百分比／百分點，差異＝重算−原摘要；判斷門檻沿用工單的 0.05 百分點。

## (1) 改了哪些檔案

- `verify_validity_numbers.py`：獨立重算、股利敏感度、6m/12m bootstrap 雙跑與 seed=43、逐格比較及產出報告。使用標準庫與 NumPy，不 import 回測模組，不用 pandas。
- `split_price_returns.py`：分割還原價首尾比的持有報酬，缺到期價標示最後可得價。
- `backtest_validity.py`：刪除舊事件連乘函式，組合路徑改用分割還原價持有報酬。
- `backtest_valuation.py`、`backtest_t1.py`：移除舊函式匯入，未重跑兩支主程式。
- `build_valuation.py`：事件 HTTP 回應指定 UTF-8 解碼，中文字串直接寫 SQLite；新增含 NULL 價格的事件內容唯一索引；事件路徑的 survivors CSV 改用 csv 標準庫；分割價格建置函式新增可選單一 stock_id，預設行為不變。
- `repair_validity_5305.py`：僅補 5305；token 讀 `.env`，請求前 sleep 0.5 秒，錯誤不重試，不輸出 token。
- `tests/test_validity.py`：保留原 15 項測試，原舊函式測試改走分割價格建置／持有報酬；新增中文雙寫、NULL 去重、bootstrap 雙跑換 seed、名次同分、缺價與設限等共 6 個測試案例，合計 21。
- `backtest/validity_summary.md`：§0 資料實況更正、四個殘項回覆、移除股利未還原的錯誤描述；§1、§2 原數字逐格保留，加入第三輪更正連結。
- `data/tw_stocks.db`：只對 5305 新增 `fm_price_daily` 217 列、`fm_price_adj_daily` 217 列。沒有修改根目錄 `tw_stock.db`。
- 新產出：`backtest/validity_recheck.csv`、`validity_recheck.md`、`validity_recheck_monthly.csv`、`validity_dividend_sensitivity.csv`、`validity_bootstrap_runs.csv`、`validity_recheck_audit.json`、`validity_5305.json`、`validity_code_search.txt`、`validity_tests_before.txt`、`validity_tests_after.txt`，以及本報告。

## (2) 重算對照表摘要＋所有 MISMATCH 列

§1：8 個策略×4 個指標＝32 格，全部在 0.05 百分點內，N 全相同。§2：6 組×8 個指標＝48 格，其中 34 格 MISMATCH、14 格在容許差異內。合計 80 格，46 格 MATCH、34 格 MISMATCH。

| 策略 | 有效月份 | 勝率% | 平均超額% | 中位超額% |
| --- | --- | --- | --- | --- |
| D5 | 36 | 91.66666667 | 9.38576266 | 5.69423972 |
| C1 | 50 | 60.00000000 | 3.41340448 | 1.90994387 |
| C1_PER | 50 | 58.00000000 | 4.26302620 | 3.00251861 |
| L1 | 36 | 41.66666667 | -7.31647049 | -1.78294429 |
| D0 | 50 | 60.00000000 | 3.41340448 | 1.90994387 |
| D2 | 50 | 62.00000000 | 3.64524400 | 1.91216671 |
| D3 | 36 | 83.33333333 | 5.46399844 | 3.65822157 |
| MOM_12_1 | 50 | 68.00000000 | 6.96495159 | 4.01109569 |

方法與輸入：

- 沿用 `signals.csv` 的原有 L1／C1／D 層旗標、個股六個月報酬。CSV 只匯出 L0 或 C1，故不能用其中的 MOM_12_1／C1_12_1 子集當完整策略；本輪從 DB 全母體重建這兩組。
- 母體 447 檔，沿用 build_valuation 的 `_AI_MAIN`、`_EXT_GROUPS`、`_AI_CHAIN_EXCLUDE` 三個**純資料常數**（AST literal_eval，不執行模組）、`stock_sub_industry`、survivors CSV 聯集；資格、名次同分、75% 門檻、T+1..T+3、六個日曆月到期及最後價結算均照既有定義，未變更。
- 重建後與 CSV 可對照個股報酬不一致 0 筆、動能旗標不一致 0 筆；獨立 Universe 基準與 CSV 基準最大差 0.000000000000005551115123125783 百分點。
- 同一訊號月先等權，再跨月平均、中位與嚴格 >0 勝率；父子只取兩者都有有效報酬的共同月份。§1 有效月份跨度為 2022-01-26～2026-02-26。逐月數字列於 `validity_recheck_monthly.csv`。
- 未共用任何 `backtest_validity.py` 或其他回測函式；共用的只有原資料、策略常數與 NumPy 的 MT19937 RNG／percentile 數值原語。bootstrap 以向量化索引獨立實作，非環狀移動區塊，尾部裁切至 N。

所有超門檻差異如下（原摘要未改）：

| 項目 | 指標 | summary所載 | 重算值 | 差異 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| D0→D2 | 勝率 | 52.00000000 | 48.00000000 | -4.00000000 | MISMATCH |
| D0→D2 | 中位 | 0.07000000 | 0.00000000 | -0.07000000 | MISMATCH |
| D2→D3 | 勝率 | 47.20000000 | 55.55555556 | 8.35555556 | MISMATCH |
| D2→D3 | 平均 | -0.60000000 | -0.28988762 | 0.31011238 | MISMATCH |
| D2→D3 | 中位 | -0.19000000 | 0.51129651 | 0.70129651 | MISMATCH |
| D2→D3 | 6m下界 | -5.29000000 | -4.87976182 | 0.41023818 | MISMATCH |
| D2→D3 | 6m上界 | 2.19000000 | 2.50455474 | 0.31455474 | MISMATCH |
| D2→D3 | 12m下界 | -5.28000000 | -3.59869228 | 1.68130772 | MISMATCH |
| D2→D3 | 12m上界 | 1.77000000 | 1.30390068 | -0.46609932 | MISMATCH |
| D3→D5 | 平均 | 2.84000000 | 3.92176422 | 1.08176422 | MISMATCH |
| D3→D5 | 中位 | 2.86000000 | 1.60450640 | -1.25549360 | MISMATCH |
| D3→D5 | 6m下界 | 1.13000000 | 1.47325212 | 0.34325212 | MISMATCH |
| D3→D5 | 6m上界 | 4.05000000 | 5.14278077 | 1.09278077 | MISMATCH |
| D3→D5 | 12m上界 | 4.01000000 | 4.43773335 | 0.42773335 | MISMATCH |
| C1→MOM_12_1 | 勝率 | 66.00000000 | 60.00000000 | -6.00000000 | MISMATCH |
| C1→MOM_12_1 | 中位 | 3.54000000 | 2.12911490 | -1.41088510 | MISMATCH |
| C1→MOM_12_1 | 6m下界 | 0.74000000 | 0.02874967 | -0.71125033 | MISMATCH |
| C1→MOM_12_1 | 6m上界 | 6.55000000 | 7.40198961 | 0.85198961 | MISMATCH |
| C1→MOM_12_1 | 12m下界 | 0.79000000 | 0.23338633 | -0.55661367 | MISMATCH |
| C1→MOM_12_1 | 12m上界 | 6.48000000 | 7.10831755 | 0.62831755 | MISMATCH |
| C1→C1_12_1 | 勝率 | 52.00000000 | 56.00000000 | 4.00000000 | MISMATCH |
| C1→C1_12_1 | 平均 | -0.08000000 | 0.90433460 | 0.98433460 | MISMATCH |
| C1→C1_12_1 | 中位 | 0.44000000 | 0.57484023 | 0.13484023 | MISMATCH |
| C1→C1_12_1 | 6m下界 | -2.33000000 | -1.31290758 | 1.01709242 | MISMATCH |
| C1→C1_12_1 | 6m上界 | 2.05000000 | 3.23205884 | 1.18205884 | MISMATCH |
| C1→C1_12_1 | 12m下界 | -2.30000000 | -1.18274496 | 1.11725504 | MISMATCH |
| C1→C1_12_1 | 12m上界 | 2.03000000 | 3.41055343 | 1.38055343 | MISMATCH |
| C1_12_1→MOM_12_1 | 勝率 | 76.00000000 | 64.00000000 | -12.00000000 | MISMATCH |
| C1_12_1→MOM_12_1 | 平均 | 3.63000000 | 2.64721251 | -0.98278749 | MISMATCH |
| C1_12_1→MOM_12_1 | 中位 | 3.12000000 | 2.25804422 | -0.86195578 | MISMATCH |
| C1_12_1→MOM_12_1 | 6m下界 | 1.84000000 | 0.47028916 | -1.36971084 | MISMATCH |
| C1_12_1→MOM_12_1 | 6m上界 | 5.72000000 | 5.63593751 | -0.08406249 | MISMATCH |
| C1_12_1→MOM_12_1 | 12m下界 | 1.87000000 | 0.40546555 | -1.46453445 | MISMATCH |
| C1_12_1→MOM_12_1 | 12m上界 | 5.65000000 | 5.73667899 | 0.08667899 | MISMATCH |

差異原因：目前 `backtest/summary.md`「6m 持有期父子配對差」表的平均、中位與勝率，與本輪重算的四捨五入結果一致，卻與 `validity_summary.md` §2 不一致。例如 D2→D3 為 −0.29%、55.6%；D3→D5 為 +3.92%、中位 +1.60%；C1→MOM_12_1 為中位 +2.13%、勝率60%。原摘要 D3→D5 的 +2.84% 也與其 §1 同為36共同月的 D5 +9.39% 減 D3 +5.46% 不相容。

另外 `summary.md` 六個月持有表實際使用 **3m／6m 區塊**，原摘要卻標 **6m／12m**。本輪按工單實跑6m／12m，不能把3m欄直接當6m。這說明文件之間有數字及參數紀錄不一致；原摘要各差異數字究竟由哪次運算／抄錄產生，**未解**。沒有修改腳本去追隨原摘要數字。

## (3) 三個判定的重判結果

| 配對 | 重判 | N | 平均配對差% | 6m CI% | 12m CI% |
| --- | --- | --- | --- | --- | --- |
| D2→D3 | 維持 | 36 | -0.28988762 | [-4.87976182, 2.50455474] | [-3.59869228, 1.30390068] |
| D3→D5 | 維持 | 36 | 3.92176422 | [1.47325212, 5.14278077] | [1.09429970, 4.43773335] |
| C1→MOM_12_1 | 存疑 | 50 | 3.55154711 | [0.02874967, 7.40198961] | [0.23338633, 7.10831755] |

- D2→D3：「維持」原有不成立判定；平均仍為負，兩個區塊 CI 均跨0，但原摘要平均／中位／勝率不能沿用。
- D3→D5：「維持」原有成立判定；均值 +3.92176422%，6m／12m CI 均在0以上；股利代理及 seed43 亦如此。
- C1→MOM_12_1：「存疑」。限定無股利、seed42、6m／12m時，仍符合原有CI全正判定；但6m下界只有 +0.02874967%，股利代理後成 −0.10390049%；換 seed43 後6m下界 −0.12337872%、12m下界 −0.11624240%。三項均未修改策略定義或門檻。

Bootstrap 主流程實測：6組×2區塊×2端點＝24個端點，seed42第一次與第二次全部完全相同，2000次重抽；seed43另跑一次。最大絕對漂移 **0.34962874百分點**，發生於 C1→MOM_12_1 的12m下界。24個端點的兩次值與漂移完整列在 `validity_bootstrap_runs.csv` 及重核 Markdown。

## (4) 股利敏感度翻轉清單

殖利率取 DB `per_daily.dividend_yield` 訊號當日，CSV對應 `dividend_yield_at_signal`；年殖利率×6/12加回持有報酬。未成交部位現金0、不加股利；基準亦對全部合資格初始成員加回代理。10,774個有效股票月中，10個沒有殖利率，沿用原程式缺值當0。這是均勻實現的殖利率代理，未使用未來實際股利或宣稱已含實際配息。

| 項目 | 指標 | 無股利 | 殖利率代理 |
| --- | --- | --- | --- |
| D0→D2 | CI判定 | 不成立（CI 跨0或未全正） | 不成立（CI 跨0或未全正） |
| D2→D3 | CI判定 | 不成立（CI 跨0或未全正） | 不成立（CI 跨0或未全正） |
| D3→D5 | CI判定 | 成立 | 成立 |
| C1→MOM_12_1 | CI判定 | 成立 | 不成立（CI 跨0或未全正） |
| C1→C1_12_1 | CI判定 | 不成立（CI 跨0或未全正） | 不成立（CI 跨0或未全正） |
| C1_12_1→MOM_12_1 | CI判定 | 成立 | 成立 |

- **CI 成立判定翻轉只有 C1→MOM_12_1 一組**：無股利成立→殖利率代理不成立；代理6m CI下界 −0.10390049%，12m下界仍 +0.10495506%。
- D2→D3 的**平均配對差方向**另由 −0.28988762% 變 +0.08749950%，但兩種口徑 CI 都跨0，因此不是成立判定翻轉。
- 8個單策略的平均超額正負號沒有翻轉。單策略N／勝率／均值／中位及每組配對N／均值／中位／勝率／全部CI端點的兩口徑，完整列於 `validity_dividend_sensitivity.csv`，未只列有翻轉的項目。

基準檢查：根目錄與 `backtest/` 的 Python 程式未找到0050引用；測試中唯一命中為另一工作範圍的 `tests/test_momentum_pit_fetch.py:40` ETF過濾假資料，並非基準。§1、§2 本來採 Universe 等權基準，來自 `fm_price_adj_daily`。`fm_price_daily` 的0050筆數為0。故本輪沒有0050原價基準可替換；改動前後價格基準差為0（浮點交叉重算誤差最大5.55×10^-15百分點）。股利敏感度只在同一Universe基準加代理，不改成另一母體或指數。

## (5) 殘項四點狀態

1. **5305 已修**：唯一一次外部請求是 `TaiwanStockPrice/data_id=5305/start_date=2020-01-01`，HTTP／payload成功200。原始價0→217、分割還原價0→217，期間2020-01-02～2020-11-23。事件表沒有5305事件，沿用其他股票的原價＋確認事件建置法，所以兩種價格相同。兩張價格表目前各324,829列、254檔。就本次原有2022年起的有效月而言，5305有效月份清單為空、六組配對受影響月份皆為空；不把下市後無價補為0元。
2. **bootstrap 已修**：主流程24端點雙跑完全相同；另外新增合成資料6m／12m的實跑測試，原函式與獨立實作均各跑兩次，另跑seed43。
3. **事件抓取端已修**：`json.loads(resp.content.decode('utf-8-sig'))`，原始JSON以 `ensure_ascii=False` 保存；SQLite 接收Python Unicode，不經cp950。去重鍵為stock_id/date/event_type/before_price/after_price，NULL價格亦納入唯一語意。假回應「股票分割」寫兩次後1列、文字相同；不同source但內容相同的NULL價格事件亦只留1列。本輪沒有呼叫真實全市場fetch-events，故沒有對現有事件表再清理或抓取。
4. **舊函式已修**：刪除函式本體及2個匯入點、1個組合內呼叫；改用已分割還原價格首尾比，沒有再次乘事件因子。舊測試改走in-memory SQLite分割價格建置與持有報酬，保留減資日+2%、一拆三0%、缺價最後價、未確認事件不調整等案例。

工單只准Python，以下貼的是Python逐行搜尋的等價grep結果，未另呼叫grep：

```text
搜尋範圍：根目錄 *.py、backtest/*.py、tests/**/*.py；以 Python 逐行搜尋（工單只准 Python，未執行 grep）。
compute_holding_return_adjusted：0 筆

0050：1 筆
tests\test_momentum_pit_fetch.py:40:                {"stock_id": "0050", "type": "twse"}]
```

## (6) 測試數字（前／後）

| 測試範圍 | 修改前 | 修改後 |
| --- | --- | --- |
| tests/test_validity.py | 15 passed，0 failed | 21 passed，0 failed |
| tests/ | 335 passed，6 failed（13.90秒） | 341 passed，6 failed（15.12秒） |

全套兩次均exit code 1，6個失敗名稱與原因相同，未修、未刪、未skip：

- `test_dashboard_export.py::test_group_count_matches_db`：20 != 19。
- `test_fundamentals_content.py::test_monthly_revenue_no_orphan_rows`：106 != 0。
- `test_fundamentals_content.py::test_institutional_flow_summary_freshness_consistent`：1849/1968=93.95% <95%。
- `test_fundamentals_content.py::test_institutional_flow_tables_no_orphan_rows`：2269 != 0。
- `test_group_flow.py::test_group_flow_daily_covers_19_groups`：20 != 19。
- `test_group_flow.py::test_group_flow_weekly_covers_19_groups`：20 != 19。

完整輸出：`backtest/validity_tests_before.txt`、`backtest/validity_tests_after.txt`。新增測試沒有網路請求，事件回應為假資料。

## (7) 實際執行的指令清單

首次讀取工單時使用 `Get-Content -LiteralPath docs/tasks/backtest-validity-3-recheck.md -Raw`；讀到「只准用python」後，後續檔案／資料操作均使用Python，PowerShell僅承載Python指令或here-string。

```text
python -c <讀檔程式>                         # 首次輸出遇cp950 UnicodeEncodeError
python -X utf8 -c <讀檔程式>                 # 改為UTF-8後讀出工單相關程式與摘要
@'<Python程式>'@ | python -X utf8 -          # 多次：pathlib讀寫、SQLite查詢、CSV比較、搜尋與報告生成
python -X utf8 -m pytest tests/test_validity.py -q   # 修改前15通過
python -X utf8 repair_validity_5305.py               # 只抓5305一次，寫入217列
python -X utf8 verify_validity_numbers.py           # 兩次：先重算，後補齊來源說明與綜合判定
python -X utf8 -m pytest tests/test_validity.py -q   # 修改後21通過
```

全套測試透過Python subprocess執行並留存stdout/stderr，修改前後各一次：

```python
subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', 'tests/', '-q'],
               capture_output=True, text=True, encoding='utf-8')
```

起初另有一次 `subprocess.run(['python', ...])` 找到不同的Python環境，報 `No module named pytest`，未執行任何測試；改用 `sys.executable` 後取得上表前後結果。有一則內嵌雙引號SQL的 `python -c` 在解析時失敗，之後改用here-string。未輸出token。沒有執行 git commit／push／stash／checkout，沒有殺背景程序；未執行會寫入禁動CSV的整套回測。

## (8) 做不到或不確定的事

- 原摘要34個MISMATCH格的產生過程未解。現有CSV、DB及另一份summary.md可提供重算結果，但不能逆推出原摘要如何得到那些數字。
- 價格仍未含實際現金股利。PriceAdj的400／Sponsor限制依工單既有查核，未為重現權限錯誤再打API；本輪只量化殖利率代理。
- CSV是前輪訊號快照，本輪對這份相同輸入重算；沒有重新生成EPS／PER／營收選股訊號或重跑§3–§5所有執行面與年化數字。因此原摘要這些章節明確標為前輪歷史陳述。
- 5305的2020價格使整張價格表的最早日由2021-01-04延長至2020-01-02。**本輪重核凍結於原CSV訊號月份**，5305在其中沒有合資格月份。未執行另一輪完整主程式，不能把「本輪原有月份影響為0」外推為「之後重新掃描整張表的交易日曆／暖機期必然不變」；沒有為配合原數字去修改日曆或母體規則。
- 原程式及既有測試仍有pandas依賴，本輪沒有做全專案依賴重寫；新增獨立腳本與修復函式均不使用pandas，指定測試會載入既有模組。
- 未更新HANDOFF、README或工單列出的其他並行session檔案；未commit，依工單保留修改供後續接手。
