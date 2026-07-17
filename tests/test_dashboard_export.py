"""驗證 export_dashboard.py 匯出的靜態儀表板 HTML 內嵌資料正確。

比照 test_sector_flow_animation_export.py 的驗證模式：從匯出的 HTML 抽出
`const DATA = {...}` JSON 區塊解析後跟資料庫內容交叉核對，而不是只驗證「有沒有噴例外」。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import export_dashboard as export_mod

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


@pytest.fixture(scope="module")
def exported_html(tmp_path_factory):
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    out_path = tmp_path_factory.mktemp("dashboard") / "dashboard.html"
    export_mod.export(DB_PATH, out_path)
    return out_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def data(exported_html):
    m = re.search(r"const DATA = (\{.*?\});\nconst REPORTS", exported_html, re.S)
    assert m is not None, "HTML 內找不到 DATA 變數"
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def conn():
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


# ---- 檔案產出 ----

def test_export_creates_file(exported_html):
    assert len(exported_html) > 1000


def test_export_file_size_under_budget(tmp_path_factory):
    out_path = tmp_path_factory.mktemp("dashboard_size") / "dashboard.html"
    export_mod.export(DB_PATH, out_path)
    size_kb = out_path.stat().st_size / 1024
    assert size_kb < 500, f"檔案大小 {size_kb:.1f} KB 超過 500KB 預算"


def test_export_no_format_placeholder_artifacts(exported_html):
    """確認沒有殘留 f-string/.format() 的雙大括號格式化陷阱（本腳本改用 .replace()，
    但仍要確認模板裡沒有不小心留下未替換的 __DATA_JSON__/__REPORTS_JSON__ token）。"""
    assert "__DATA_JSON__" not in exported_html
    assert "__REPORTS_JSON__" not in exported_html


def test_data_json_is_parsable(data):
    assert "meta" in data and "weeks" in data


# ---- 34 板塊 / 19 族群 / 週數與 DB 一致 ----

def test_sector_count_matches_db(data, conn):
    expected = conn.execute("SELECT COUNT(DISTINCT industry_name) FROM sector_flow_value_weekly").fetchone()[0]
    assert len(data["sector_heat"]["labels"]) == expected == 34


def test_group_count_matches_db(data, conn):
    expected = conn.execute("SELECT COUNT(DISTINCT group_name) FROM group_flow_value_weekly").fetchone()[0]
    assert len(data["group_heat"]["labels"]) == expected == 19


def test_week_count_matches_db(data, conn):
    expected = conn.execute("SELECT COUNT(DISTINCT week_index) FROM sector_flow_value_weekly").fetchone()[0]
    assert len(data["weeks"]) == expected == data["meta"]["n_weeks"]


def test_sector_heatmap_matrix_shape(data):
    for row in data["sector_heat"]["matrix"]:
        assert len(row) == len(data["weeks"])
    assert len(data["sector_heat"]["matrix"]) == len(data["sector_heat"]["labels"])


def test_group_heatmap_matrix_shape(data):
    for row in data["group_heat"]["matrix"]:
        assert len(row) == len(data["weeks"])
    assert len(data["group_heat"]["matrix"]) == len(data["group_heat"]["labels"])


# ---- 抽查一格熱力圖數字等於 DB 值 ----

def test_sector_heatmap_cell_matches_db(data, conn):
    label = data["sector_heat"]["labels"][0]
    wk = data["weeks"][10]
    expected = conn.execute(
        "SELECT total_value FROM sector_flow_value_weekly WHERE industry_name=? AND week_index=?",
        (label, wk["i"]),
    ).fetchone()[0]
    expected_yi = round((expected or 0) / export_mod.YI, 1)
    got = data["sector_heat"]["matrix"][0][10]
    assert got == expected_yi


def test_group_heatmap_cell_matches_db(data, conn):
    label = data["group_heat"]["labels"][0]
    wk = data["weeks"][20]
    expected = conn.execute(
        "SELECT total_value FROM group_flow_value_weekly WHERE group_name=? AND week_index=?",
        (label, wk["i"]),
    ).fetchone()[0]
    expected_yi = round((expected or 0) / export_mod.YI, 1)
    got = data["group_heat"]["matrix"][0][20]
    assert got == expected_yi


def test_market_weekly_total_matches_cross_sector_sum(data, conn):
    """全市場週度合計 = 34 板塊當週 total_value 加總（板塊彼此互斥、加總語意合法，
    跟 group_flow_* 不可跨族群相加不同，見 AGENTS.md）。"""
    wk = data["weeks"][5]
    expected = conn.execute(
        "SELECT SUM(COALESCE(total_value,0)) FROM sector_flow_value_weekly WHERE week_index=?",
        (wk["i"],),
    ).fetchone()[0]
    expected_yi = round(expected / export_mod.YI, 1)
    assert data["market_weekly"]["total"][5] == expected_yi


def test_taiex_weekly_has_no_gaps(data):
    """150 週的 TAIEX 收盤點理論上應全數涵蓋（taiex_daily 範圍比 sector_flow 更寬），
    不應該出現 None。"""
    assert all(v is not None for v in data["taiex_weekly"])


# ---- 下鑽資料 ----

def test_sector_drill_top10_sorted_desc(data):
    for label, entries in data["sector_drill"].items():
        assert len(entries) <= 10
        values = [e["net4w"] for e in entries]
        assert values == sorted(values, reverse=True)


def test_group_drill_top10_sorted_desc(data):
    for label, entries in data["group_drill"].items():
        assert len(entries) <= 10
        values = [e["net4w"] for e in entries]
        assert values == sorted(values, reverse=True)


def test_sector_drill_covers_all_sectors(data):
    assert set(data["sector_drill"].keys()) == set(data["sector_heat"]["labels"])


def test_group_drill_covers_all_groups(data):
    assert set(data["group_drill"].keys()) == set(data["group_heat"]["labels"])


# ---- 投信特寫 ----

def test_trust_streak_present(data):
    assert data["trust"]["streak"]["sign"] in ("buy", "sell", "flat", "none")


def test_trust_next_quarter_end_in_future(data):
    from datetime import date
    qend = data["trust"]["next_quarter_end"]
    assert qend["date"] is not None
    assert qend["calendar_days"] > 0
    assert qend["date"].endswith(("-03-31", "-06-30", "-09-30", "-12-31"))


def test_trust_seasonality_note_cites_report(data):
    assert "flow-persistence-seasonality" in data["trust"]["seasonality_note"]


def test_trust_seasonality_note_uses_share_unit_not_currency(data):
    """來源報告 B3 表的季底作帳統計是**股數口徑（萬股）**，報告方法限制第 1 點
    明確警告不能直接換算成新台幣金額。這裡防止再度把它標成「萬元」（單位混淆是
    本專案一路在防的錯誤類型）。"""
    note = data["trust"]["seasonality_note"]
    assert "萬股" in note
    assert "股數口徑" in note
    assert "萬元" not in note


# ---- 使用須知 ----

def test_notice_section_present(exported_html):
    assert 'id="notice"' in exported_html
    assert "訊號產生器" in exported_html
    assert "觀察工具" in exported_html


def test_notice_lists_five_reports(exported_html, data):
    assert len(export_mod.ANALYSIS_REPORTS) == 5
    for report in export_mod.ANALYSIS_REPORTS:
        assert report in exported_html


def test_notice_reports_exist_on_disk():
    analysis_dir = Path(__file__).parent.parent / "analysis"
    for report in export_mod.ANALYSIS_REPORTS:
        assert (analysis_dir / report).exists(), f"{report} 不存在於 analysis/"


# ---- 關鍵 DOM id 存在 ----

@pytest.mark.parametrize("dom_id", [
    "taiexChart", "marketChart", "marketToggle",
    "sectorHeatTable", "groupHeatTable",
    "sectorDrillSelect", "groupDrillSelect", "sectorDrillChart", "groupDrillChart",
    "sectorDrillTable", "groupDrillTable", "sectorRankGrid", "groupRankGrid",
    "trustChart", "trustKpis", "trustSectorTable", "reportList", "latestDate",
])
def test_key_dom_ids_present(exported_html, dom_id):
    assert f'id="{dom_id}"' in exported_html


def test_light_color_scheme_locked(exported_html):
    """避免瀏覽器深色模式反轉，見既有教訓：明確鎖 light + 白底黑字。"""
    assert "color-scheme: light" in exported_html


# ---- 【第十三輪】板塊排序自訂 ----

@pytest.mark.parametrize("dom_id", [
    "sectorSortMode", "sectorCustomOrderPanel", "sectorOrderList", "sectorOrderResetBtn",
])
def test_sector_sort_dom_ids_present(exported_html, dom_id):
    assert f'id="{dom_id}"' in exported_html


def test_sector_sort_mode_options_present(exported_html):
    """三種排序模式：活動量（預設）/名稱筆劃/自訂。"""
    assert 'value="activity"' in exported_html
    assert 'value="stroke"' in exported_html
    assert 'value="custom"' in exported_html
    assert "活動量（預設）" in exported_html
    assert "名稱筆劃" in exported_html
    assert "自訂" in exported_html


def test_sector_order_localstorage_key_present(exported_html):
    assert "twstockdb.sectorOrder.v1" in exported_html


def test_sector_order_drag_and_move_functions_present(exported_html):
    """拖放（HTML5 drag and drop）+ 上/下移按鈕的無障礙備援，函式名須存在。"""
    for fn_name in [
        "handleSectorDragStart",
        "handleSectorDragOver",
        "handleSectorDrop",
        "moveSectorItemUp",
        "moveSectorItemDown",
    ]:
        assert fn_name in exported_html


def test_sector_order_reset_button_label_present(exported_html):
    assert "重設為預設" in exported_html


def test_sector_order_localstorage_graceful_degradation(exported_html):
    """localStorage 不可用（例如隱私模式）時必須用 try/catch 包住，靜默降級，
    不可讓例外中斷整頁渲染。"""
    assert "function loadSectorOrderPref" in exported_html
    assert "function saveSectorOrderPref" in exported_html
    load_fn = re.search(r"function loadSectorOrderPref\(\).*?\n\}", exported_html, re.S).group(0)
    save_fn = re.search(r"function saveSectorOrderPref\([^)]*\).*?\n\}", exported_html, re.S).group(0)
    assert "try" in load_fn and "catch" in load_fn
    assert "try" in save_fn and "catch" in save_fn


def test_sector_order_applies_to_drill_select_not_ranking(exported_html):
    """排序結果套用到熱力圖 + 下鑽選單，排行榜（rank grid）不受影響，
    確認 applySectorOrder 有動到 drill select 而非 rank grid。"""
    m = re.search(r"function applySectorOrder\(\).*?\n\}", exported_html, re.S)
    assert m is not None
    body = m.group(0)
    assert "sectorDrillSelectEl" in body
    assert "sectorHeatTableEl" in body
    assert "RankGrid" not in body
