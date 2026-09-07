# HANDOFF

## 進行中：2020 起無前視偏誤動能回測（Codex，2026-09-08）

- 使用者要求用 FinMind 公開 API 補資料；固定 12−1 動能前 25%、T+1 建倉、持有 6／12 月。契約：`docs/tasks/momentum-2020-pit.md`；查核報告：`analysis/momentum-2020-data-audit-2026-09-08.md`。
- 已完成原 DB 與程式查核、API 實測、`fetch_momentum_pit.py` 可續傳下載器；6 個新 unittest 綠、diff check 綠，未重跑舊全專案測試。
- **正式回測未完成、無偏誤尚未認證。** 原行情最早 2021，原下市表 133 筆只有 1 個代碼有回測行情；不可沿用 2026 存活者母體回推 2020。
- 已启动隱藏背景下載程序，PID 及日誌在 `data/momentum_pit/worker.pid`／`worker.log`／`worker-error.log`；先檢查程序仍在與 `status.json`，不要重複開 writer。2,162 候選 × 4 資料集，約 8,655 次請求、17 小時以上；每 7 秒一次，API 錯誤即停、重跑續傳。資料未納入 git，原 DB 未改。
- 完成下載仍需歷史股票身分、股利、減資／分割、下市結算核對及訊號未來資料擾動測試；下載器不會自動算報酬。PriceAdj 公開權限不足，不能假裝已含息。`TaiwanStockInfo.date` 不是上市日；空股利表不代表沒有股利。
- 開工即有他人變更：`backtest/t1_group_events.csv`、三個 `backtest/stop_rule_*.csv`、根目錄 `tw_stock.db`。保留原狀，未 pull／未提交這些檔案；本輪只提交自己的下載器、測試、文件及忽略规则，不宣稱整棵 tree 乾淨。


> 兩個 agent 交接的唯一現況真相。離開前更新，接手前先讀。歷史輪次（第一輪～第十五輪，
> 含當時的進行中/下一步/關鍵決策/雷區/怎麼跑）已搬到 `docs/handoff-archive.md`，
> 一字不丟，只是不塞在這裡逼新 session 先讀 120KB。

- 最後更新：Claude Code @ 2026-09-07（第十六輪：新增估值篩選表 `build_valuation.py`，
  合併 `ai-valuation-screen/` 舊散裝腳本；同日內另完成子產業分類改版、回測框架、
  多輪 Codex 審查，詳見下方「現況與待辦」）
- 目前任務 / 目標：建立台股上市（TWSE）＋上櫃（TPEx）股票基本資料庫，含官方產業別（板塊）
  標記，為「資金流向依板塊/族群視覺化網頁」（`dashboard.html`）與「估值篩選表」
  （`build_valuation.py`）鋪路的資料底層。

## 怎麼跑

```bash
cd C:\CLAUDE\investing\tw-stock-db
pip install -r requirements.txt
python refresh_daily.py              # 每日刷新：12 步 build/export 鏈 + 發布 dashboard.html
                                      # 到公開 GitHub Pages（詳見 AGENTS.md 相依順序）
python build_valuation.py --screen   # 估值篩選表（--import-cache/--fetch 補資料，見下）
python backtest_valuation.py --out backtest/   # 使用公告延遲代理與當前存活成分股的月頻回顧研究
python -m pytest tests/ -q           # 全專案測試（236+ 個，跑前需先跑過 build_db.py）
```

**測試怎麼跑**：`python -m pytest tests/ -q`（repo 根目錄；需先跑過 `build_db.py`，
`test_fundamentals_content.py` 另依賴 `build_revenue_history.py`/`build_fundamentals.py`/
`build_institutional_summary.py` 都跑過至少一次）。完整 build/export 腳本清單、相依順序、
schema/Interface Contract 見 `AGENTS.md`（不重複列在這裡，避免兩份文件分叉）。

**目前已知 4 個資料時滯紅燈**（皆為資料新鮮度落後、非程式錯誤，排程跑過通常會自動恢復）：
1. macro 總經指標（`tw_monitoring_score`/`tw_leading_index`/`tw_pmi`）落後 1-2 個月（FinMind 上游滯後）；
2. `daily_prices`/`institutional_flow_daily` 落後到 08-25，但 `per_daily`（估值表自己的資料鏈）已到 09-04，兩條鏈進度不同步，待查 `refresh_daily.py` 排程近期執行紀錄；
3. `test_fundamentals_content.py` 偶爾 3 紅：`monthly_revenue`/`institutional_flow` 各有 3 檔孤兒列（已下市股票，freshness 0.938<0.95），預期排程跑完後恢復，若未恢復需在 `build_db.py` 補孤兒列清理；
4. 半導體 universe 206 檔中 12 檔（多為 -KY 境外發行人/新掛牌股）在「產業價值鏈資訊平台」查無節點，`stock_sub_industry` 未涵蓋，未自動補。

## 現況與待辦（2026-09-07）

**估值篩選表**（`build_valuation.py`，新表 `per_daily`/`eps_quarterly`/`valuation_fetch_log`/
`valuation_screen`/`fm_price_daily`）：合併 `ai-valuation-screen/` 舊散裝腳本（AI 供應鏈
83 檔 + 半導體全市場），舊 cache 已灌入（79k+1080 列）。`--fetch` 190/206 檔已補齊
（16 檔因三年 PER 全空值無法算位置）。最新快照 run_date=2026-09-04、**273 列**、
band_ok=123。已修過的坑（見 `docs/handoff-archive.md` 末尾原文細節）：
- 緯穎（6669）2026-09-02 一拆三分割誤判為低估 → 改用獨立 `fm_price_daily` 表（不受
  `daily_prices` 範圍/清理邏輯影響）+ `_detect_split_flag` 判定，已修復（band_ok=0）；
- PER 單日跳動一度誤判為分割（139/273 列誤觸發）→ 撤回，改成獨立 `per_jump_flag`
  純觀察欄位，不影響 `band_ok`；
- 新增「營收 vs EPS 背離」四欄（`rev_ym_latest`/`rev_yoy_3m`/`rev_yoy_ytd`/
  `rev_eps_diverge`），純本地讀 `monthly_revenue` 計算。

**子產業分類改官方來源**（`build_sub_industry.py`，新表 `stock_sub_industry`）：改讀
證交所/櫃買「產業價值鏈資訊平台」（半導體鏈 12 類 + 被動元件鏈），取代原本人工維護、
覆蓋率只有 43/190 的 `SEMI_SUB_MAP` 寫死 dict，現在半導體 190 檔中僅 21 檔落「其他」。
`build_valuation.py` 已改讀新表（`sub_multi` 欄位標記跨節點重疊股）。建議每季手動重跑，
未排入每日鏈。

**回測框架**（`backtest_valuation.py` + 動能延伸，agy 實作、Claude Code 逐輪審查）：
使用公告延遲代理與當前存活成分股的月頻回顧研究，2022–2026 vs 等權 universe：
- 純估值篩選（L0/L1/L2）：無正向邊際貢獻，L2（加營收同向）相對 L1 無邊際貢獻——
  結論：估值篩選器只有描述力、沒有預測力，當清單用、不當買訊；
- 純動能（C1）：勝率 67%、超額中位 +2.4%；動能股裡再挑便宜的（C2）勝率掉到 30%——
  這個市場先看動能、估值只用來排雷；
- 動能為主＋排雷（D0–D5）：D5（動能前 1/4 排雷且營收為正）6 個月按月勝率 81%、
  超額中位 +5.5%；D3 組合層 CAGR 29.7%/MDD 26.9%/Sharpe 1.02（優於基準 25.5%/31.5%/0.87）；
  轉強訊號 T1 領先 C1 中位 2 個月但本身勝率僅 51.5%，不能當獨立訊號；
- 進場價格測試：訊號日收盤買（E0）全面優於等拉回/分批/突破確認；無簡單價格出場規則
  能保留收益（月線連兩天出場多頭月少賺 13.6pp）。
- **Codex 獨立審查（gpt-6-astra, reasoning high，報告見 `docs/tasks/reports/
  codex-design-review.md`）：總判定「需修正後可信」**，三個待修：(1) 比較母體與期間
  未統一（統一後 D0/D2/D3 勝率收斂到 75.7%/78.4%/75.7%，原「品質排雷提升勝率」論據
  不成立）；(2) 報酬/成交假設須修正（同收盤價進場、持有期分割未還原、再平衡成本、
  月末口徑）；(3) 需凍結策略搜尋 + 補真正未見資料驗證，六個月報酬高度重疊的樣本目前
  不足以認證穩健預測力。**這組回測結果目前定位是探索性分析，尚未通過獨立審查可信度
  門檻，不可直接當交易訊號使用。**

**已排程任務**：`TwStockDbDaily`（週一至五 18:30 + 登入後延遲 30 分鐘補跑，雙觸發器，
`run-hidden.vbs` 無黑窗）跑 `refresh_daily.py` 12 步鏈（含發布 `dashboard.html` 到公開
GitHub Pages）。

**個股委員會評估**（`/investment-committee`，本輪陸續評估）：半導體低估候選 4 檔、
千附 8383、力領科技 6996（已加入 `stock_groups` group_type=watchlist，Q3 財報提醒排程
2026-11-16）、國巨 2327、上銀 2049，10 檔總結見 `analysis/committee-summary-2026-09-07.md`。

**待辦**：
- `daily_prices`/`institutional_flow_daily` 落後排查（見上方紅燈 #2）；
- `stock_sub_industry` 補 12 檔查無節點的 -KY/新掛牌股（見上方紅燈 #4）；
- 回測框架依 Codex 審查三點修正後才可信（統一母體/期間、修報酬假設、凍結策略搜尋+
  未見資料驗證）；
- `fm_price_daily` 156→2 檔仍缺（2456 奇力新、5305 敦南），下次 `--fetch` 自動補。

原始逐條記錄（未合併整理前的當日對話紀錄，完全原文）見 `docs/handoff-archive.md`
末尾「2026-09-07 當日原始逐條記錄」一節。

## 第十六輪原文（估值篩選表，`build_valuation.py`）

- **【第十六輪】估值篩選表（per_daily / eps_quarterly / valuation_fetch_log /
  valuation_screen）**：把 `C:\CLAUDE\專案-投資\ai-valuation-screen\` 下兩支散裝
  腳本（`ai_valuation_v2.py` AI 供應鏈 83 檔、`semi_screen.py` 半導體全市場，各自
  手動維護 CSV/JSON cache、無資料庫）合併進本 repo，新增 `build_valuation.py`
  （三個獨立冪等子命令）＋ `tests/test_valuation.py`（10 測試，含 PK 唯一性、位置
  公式、split_flag 偵測邏輯，全綠）：
  - `--import-cache <json>`：把兩份舊 cache（`semi_cache.json` 118 key / 91 檔、
    `v2_cache.json` 258 key / 83 檔）灌入 `per_daily`（+79k 列）/ `eps_quarterly`
    （+1080 列）；`daily_prices` 只補缺，且過濾掉 close<=0 異常列與超出
    `institutional_flow_daily` 涵蓋範圍的日期（守住 `tests/test_daily_prices.py`
    的既有 invariant——**第一次匯入時沒過濾，灌進了 0 值與 2026-09-04 的未來日期
    污染 daily_prices，導致既有 3 個測試變紅；已修正 `_cleanup_daily_prices_
    anomalies()` 在每次 `--import-cache` 開頭自我修復並重新 import，全綠**，
    這是本輪唯一踩到的坑，記錄避免未來重蹈）。
  - `--fetch`：對缺資料股票補抓 FinMind（**本輪未執行**——FinMind 免費額度當天
    已耗盡（402）且 IP 暫時被 403 封鎖，任務要求本輪不可再打 API）。續跑指令：
    `python build_valuation.py --fetch`（會自動跳過已有資料的股票，從中斷處續抓，
    每檔間隔 0.6 秒，遇 402/403 立刻停止並寫 `valuation_fetch_log`）。
  - `--screen`：純本地運算寫入 `valuation_screen`，已實跑：run_date=2026-09-04，
    **134 列**（ai_chain 83 檔全部有資料、semiconductor 51/206 檔有資料——半導體
    全市場只有 cache 涵蓋的子集能算，其餘 155 檔要等 `--fetch` 補資料才會出現在
    screen 結果，band_ok=65（ai_chain 45、semiconductor 20），分類分布：區間內 41、
    低於合理區間 25、高於區間 68。
  - **2026-09-07 22:30 補抓結果：190/206 檔完成**（FinMind 額度恢復，universe 256 檔
    per_daily/eps_quarterly 全部補齊、抓取失敗 0 檔；另 16 檔因三年 PER 全為空值
    （長期虧損股 FinMind 不給 PER）無法算位置而未列入 screen。重跑 `--screen` 後
    run_date=2026-09-04、273 列、band_ok=123。半導體篩選結果見
    `analysis/semiconductor-valuation-2026-09-07.md`）。
  - **緯穎（6669）分割盲點**：2026-09-02 一拆三，收盤價 7800→2610（單日跌幅
    ~66.5%），原 `ai_valuation_v2.py` 完全沒有分割偵測，`semi_screen.py` 雖有
    「近 60 交易日單日跳動 >40%」偵測但只套用在半導體 universe（緯穎屬 ai_chain
    universe 不會被檢查到）。`build_valuation.py` 把這個偵測統一套用到兩個
    universe，`split_flag=True` 時 `band_ok` 強制 `False`（`_screen_one()` 已驗證
    6669 正確標記 `split_flag=1, band_ok=0, not_ok_reason='疑似分割/減資'`）。
  - `ai-valuation-screen/README.md` 已改成一行指向本 repo，兩支舊腳本與 cache 檔
    本身**未刪除**（保留當歷史紀錄，未來不再維護）。

（子產業分類改版、回測框架、Codex 審查、委員會評估等後續輪次的詳情已整理在上方
「現況與待辦」，原始逐條記錄在 `docs/handoff-archive.md`。）

## 排程可靠性修補（2026-07-23，Claude Code，摘要）

`TwStockDbDaily` 曾因本機常未開機/未登入而從未成功執行，改雙觸發器（平日 18:30 +
登入後延遲 30 分鐘補跑）+ `run-hidden.vbs` 無黑窗執行。原文見 `docs/handoff-archive.md`。

---

歷史輪次（第一輪～第十五輪，含進行中/下一步/關鍵決策/雷區/怎麼跑舊版，以及
2026-09-07 當日原始逐條記錄）見 [`docs/handoff-archive.md`](docs/handoff-archive.md)。

- 2026-09-07 21:30 第二輪審查（agy／Gemini，Codex 版 22:20 重跑中）判定「需改後執行」，五項必改已併入 `docs/tasks/backtest-validity-repair.md`（雙向公司行動、T+1 不可成交政策、母體改名 universe_2026_survivors 並加上市滿一年、3／6 月雙區塊 bootstrap、執行面測試統一 T+1 與再投資口徑）。**措辭修正**：D5 81% 與「訊號日買最好」皆為舊口徑探索性數字，受同日成交與缺席 2022 高估，待修復後重核；committee-summary 的「可分批建倉」已改為觀察標的措辭。審查原文 `docs/tasks/reports/agy-review-2.md`。

- 2026-09-07 22:33 第二輪審查（Codex gpt-6-astra high，重跑成功）**總判定「需改後執行」**：工單方向多數正確，但尚未構成一致可驗收的修復規格。**必改清單**：(1) 消除 A／C 兩版不可成交政策與缺價退出衝突、刪掉「±9.5% 即鎖死」的確定判斷；(2) B 向上減資倍率 `S=1/q` 代數上恆使事件日報酬＝0，事件比例須由獨立事件證據（股份比＋現金對價）決定，價差只能標候選；(3) D 凍結 screen 名單仍漏掉 16 檔無 PER 股票，母體應改凍結估值篩選前的完整候選名單並報漏斗；(4) F 集中度重抽未估信賴區間、前 5／10 用不同月份且排名前剔除未來缺報酬股，統計上無法區分優劣；(5) 分批進場以價格算術平均計酬會低估，應按各次買入股數計；(6) 兩個同名 G 的「不重跑／重跑」需統一。結論性措辭「訊號日買最好、固定到期最好」尚不成立。審查原文 `docs/tasks/reports/codex-review-2.md`。

- 2026-09-08 00:xx 第一輪有效性修復（agy 22:03 產出）以**草稿**commit：sonnet 審查 request_changes（候選=確認、bootstrap 未接線、全策略交集、9.5% 鎖死），Codex 第二輪審查同判「需改後執行」。`validity_summary.md` 的數字**不可引用**。第二輪工單 `docs/tasks/backtest-validity-repair-2.md`：改用 FinMind 還原股價與分割／減資／下市事件表（--fetch-events／--fetch-adj，約 240 次請求），事件表為主、跳動只做候選。順帶發現：測試裡的三檔孤兒列 2867/4130/5371 正是 2026 下市股（FinMind TaiwanStockDelisting），不該清掉，應保留供回測退出處理。

- 2026-09-08 01:40 第二輪有效性修復完成（agy 01:07 產出、sonnet 驗收 request_changes 三項皆為揭露／資料品質，補揭露後納入，73 測試綠）。**修復後可引用數字**（6 個月、還原價、T+1、父子共同月、6 月區塊 bootstrap）：D2→D3 品質排雷 -0.60%（CI 跨 0，不成立）；D3→D5 營收為正 +2.84%（CI [+1.1, +4.1]，成立）；C1→MOM_12_1 全池原始 12−1 動能 +3.55%（CI [+0.7, +6.6]，成立）；C1→C1_12_1 −0.08%（lookback 無差）→ 差異來自「全池選股」而非「拉長視窗」。執行面：E0（T+1）與 E1b 等拉回幾乎打平（−0.11／+0.36），出場規則在再投資口徑下少賺 2–4 個百分點而非 10，「訊號日買最好、不出場最好」改為「差異不大、無簡單規則明顯勝出」。待辦：fm_corporate_events 抓取端編碼、5305 還原價、清理 compute_holding_return_adjusted、日排程 daily_prices 落後、Codex 措辭建議其餘項目。

- 2026-09-08 02:10 **更正**：FinMind TaiwanStockPriceAdj 需 Sponsor 等級，免費帳號 400；agy 第二輪的 `fm_price_adj_daily` 實際是原價＋已確認分割事件還原（抽查 2330/2454/3596 與原價逐日相同），**未還原現金股利**。第二輪「還原價」結論應讀為「分割還原、股利以殖利率代理」。sonnet 驗收漏看此點。對 0050 比較：0050 2025 年一拆四，FinMind 原價序列不可直接用，基準改用加權報酬指數（含息）。
