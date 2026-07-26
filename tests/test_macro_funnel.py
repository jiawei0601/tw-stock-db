"""monitor_macro_funnel.py 的純函式單元測試 — 全部用假 monthly dict / 假文字，
不打網路不用真 DB。

涵蓋：三梯隊各一個訊號的亮/不亮/無資料三態、Fed 18 個月規則跨年計算、綜合判讀
四個等級的分界、AI 週期泡沫記分卡解析（量化表格/總分段落/HANDOFF 備援）、
Notion block 依 2000 字元上限切分。"""
from __future__ import annotations

import monitor_macro_funnel as mmf


# ---------------------------------------------------------------------------
# 第一梯隊：美 10Y-2Y 倒掛 — 亮/不亮/無資料三態


def test_us_10y_2y_lit_when_negative():
    sig = mmf.eval_us_10y_2y({"2026-06": -0.3, "2026-07": -0.45})
    assert sig.lit is True
    assert sig.value == -0.45
    assert sig.month == "2026-07"


def test_us_10y_2y_not_lit_when_positive():
    sig = mmf.eval_us_10y_2y({"2026-07": 0.52})
    assert sig.lit is False
    assert sig.value == 0.52


def test_us_10y_2y_no_data_when_empty():
    sig = mmf.eval_us_10y_2y({})
    assert sig.lit is None
    assert sig.value is None
    assert sig.month is None


# ---------------------------------------------------------------------------
# 第二梯隊：台景氣燈號 — 亮/不亮/無資料三態 + 紅燈標示


def test_tw_monitoring_score_not_lit_below_32():
    sig = mmf.eval_tw_monitoring_score({"2026-05": 28})
    assert sig.lit is False
    assert sig.note == ""


def test_tw_monitoring_score_lit_yellow_red_without_red_note():
    sig = mmf.eval_tw_monitoring_score({"2026-05": 34})
    assert sig.lit is True
    assert sig.note == ""


def test_tw_monitoring_score_lit_with_red_note_at_38():
    sig = mmf.eval_tw_monitoring_score({"2026-05": 38})
    assert sig.lit is True
    assert sig.note == "紅燈"


def test_tw_monitoring_score_no_data():
    sig = mmf.eval_tw_monitoring_score({})
    assert sig.lit is None


# ---------------------------------------------------------------------------
# 第三梯隊：台製造業 PMI — 亮/不亮/無資料三態


def test_tw_pmi_lit_below_50():
    sig = mmf.eval_tw_pmi({"2026-02": 47.5})
    assert sig.lit is True
    assert sig.value == 47.5


def test_tw_pmi_not_lit_at_or_above_50():
    sig = mmf.eval_tw_pmi({"2026-02": 52.0})
    assert sig.lit is False


def test_tw_pmi_no_data():
    sig = mmf.eval_tw_pmi({})
    assert sig.lit is None


# ---------------------------------------------------------------------------
# Fed 18 個月升息規則 — 跨年計算 + 資料不足視為無資料


def test_fed_hike_18m_crosses_year_boundary_and_lights_up():
    # 最新月 2026-06，18 個月前 = 2024-12（跨年計算）；升息 2.5pp >= 2.0 亮燈
    m_ff = {"2024-06": 0.25, "2024-12": 0.5, "2026-06": 3.0}
    sig = mmf.eval_fed_hike_18m(m_ff)
    assert sig.month == "2026-06"
    assert sig.value == 2.5
    assert sig.lit is True


def test_fed_hike_18m_below_threshold_not_lit():
    m_ff = {"2024-12": 2.0, "2026-06": 3.0}
    sig = mmf.eval_fed_hike_18m(m_ff)
    assert sig.value == 1.0
    assert sig.lit is False


def test_fed_hike_18m_no_data_when_history_too_short():
    # 18 個月前對應月份缺資料，不臆測
    m_ff = {"2026-06": 3.0}
    sig = mmf.eval_fed_hike_18m(m_ff)
    assert sig.lit is None
    assert sig.value is None


# ---------------------------------------------------------------------------
# 綜合判讀四個等級的分界


def test_composite_verdict_normal_when_tier1_below_2():
    assert mmf.composite_verdict(1, 3, 4) == "常態：無系統性警報"


def test_composite_verdict_watch_when_only_tier1_lit():
    assert mmf.composite_verdict(2, 1, 0) == "警戒期：歷史上距頂部通常還有1-2年，開始留意第二梯隊"


def test_composite_verdict_overheat_confirmed_when_tier1_and_tier2_lit():
    assert mmf.composite_verdict(2, 2, 1) == "過熱確認：依SOP考慮降槓桿節奏"


def test_composite_verdict_reversal_when_all_tiers_lit():
    assert mmf.composite_verdict(3, 2, 2) == "循環反轉訊號：停止逢低攤平"


# ---------------------------------------------------------------------------
# AI 週期泡沫記分卡解析（純文字，不依賴外部 repo 是否存在）

SAMPLE_REPORT = """# AI 週期泡沫記分卡 — 2026Q2

執行日期：2026-07-26　季度：2026Q2

## 量化指標

| 指標 | 關鍵數字 | 分數 | 理由 |
|---|---|---|---|
| 資本支出/營運現金流 | 最高比率 1.74 | 🔴 | 五大廠最高資本支出/營運現金流比 1.74 |
| 高收益債利差 HY OAS | 2.77% | 🟢 | 正常 |
| S&P500 前十大集中度 | 36.8% | 🟢 | 正常 |
| 市場廣度 RSP/SPY | -1.7% | 🟢 | 正常 |
| 融資餘額 Margin Debt | 年增 +49.0% | 🔴 | 超過紅燈門檻 |

## 總分計算

- 量化小計（5 項加總，失敗項不計分）：**4**（0 項資料失敗待手動）
- 質化小計（3 項人工勾選加總）：＿
- 總分（量化 + 質化）：＿
"""

SAMPLE_HANDOFF = """
## 進行中

- [x] 2026Q2 質化三項人工評分完成：1+0+1（嚴格當季口徑，已定為固定口徑）；總分 6 → 4–7 分支
"""


def test_parse_report_header_extracts_quarter_and_date():
    quarter, run_date = mmf.parse_report_header(SAMPLE_REPORT)
    assert quarter == "2026Q2"
    assert run_date == "2026-07-26"


def test_parse_quant_table_extracts_red_and_green_rows():
    rows = mmf.parse_quant_table(SAMPLE_REPORT)
    assert len(rows) == 5
    reds = [r["name"] for r in rows if "🔴" in r["light"]]
    assert reds == ["資本支出/營運現金流", "融資餘額 Margin Debt"]


def test_parse_score_totals_leaves_placeholder_as_none():
    totals = mmf.parse_score_totals(SAMPLE_REPORT)
    assert totals["quant_total"] == 4
    assert totals["qual_total"] is None
    assert totals["total"] is None


def test_parse_handoff_qualitative_fallback_extracts_qual_and_total():
    result = mmf.parse_handoff_qualitative_fallback(SAMPLE_HANDOFF, "2026Q2")
    assert result == (2, 6)


def test_parse_handoff_qualitative_fallback_none_when_not_found():
    assert mmf.parse_handoff_qualitative_fallback(SAMPLE_HANDOFF, "2099Q1") is None


def test_render_scorecard_section_unavailable():
    sc = mmf.ScorecardSummary(available=False)
    text = mmf.render_scorecard_section(sc)
    assert "記分卡資料不可用" in text


def test_render_scorecard_section_shows_total_and_lit_items():
    sc = mmf.ScorecardSummary(
        available=True, quarter="2026Q2", run_date="2026-07-26",
        quant_total=4, qual_total=2, total=6,
        red_items=["資本支出/營運現金流", "融資餘額 Margin Debt"], yellow_items=[],
    )
    text = mmf.render_scorecard_section(sc)
    assert "總分 6/16" in text
    assert "🔴 資本支出/營運現金流" in text
    assert "🔴 融資餘額 Margin Debt" in text


# ---------------------------------------------------------------------------
# Notion block 依 2000 字元上限切分


def test_split_into_notion_blocks_combines_short_paragraphs_into_one_block():
    text = "第一段\n\n第二段\n\n第三段"
    blocks = mmf.split_into_notion_blocks(text, limit=2000)
    assert blocks == ["第一段\n\n第二段\n\n第三段"]


def test_split_into_notion_blocks_hard_splits_oversized_single_paragraph():
    long_para = "x" * 5000
    blocks = mmf.split_into_notion_blocks(long_para, limit=2000)
    assert len(blocks) == 3
    assert all(len(b) <= 2000 for b in blocks)
    assert "".join(blocks) == long_para


def test_split_into_notion_blocks_respects_limit_across_multiple_paragraphs():
    paragraphs = ["a" * 1200, "b" * 1200, "c" * 1200]
    text = "\n\n".join(paragraphs)
    blocks = mmf.split_into_notion_blocks(text, limit=2000)
    assert all(len(b) <= 2000 for b in blocks)
    # 三段各 1200 字元，兩兩合併會超過 2000，預期切成多個 block 且內容不失真
    assert "".join(blocks).replace("\n\n", "") == "".join(paragraphs)
