"""驗證 export_sector_flow_animation.py 匯出的靜態 HTML 動畫內嵌資料正確。

【第九輪】export() 新增 mode 參數：預設 'value'（金額口徑，股數 x 收盤價），
'shares' 為第八輪的股數口徑舊行為（保留不刪除）。兩種模式都要驗證。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

import export_sector_flow_animation as export_mod

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


@pytest.fixture(scope="module")
def exported_html(tmp_path_factory):
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    out_path = tmp_path_factory.mktemp("export") / "animation.html"
    export_mod.export(DB_PATH, out_path, top_n=20, mode="value")
    return out_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exported(exported_html):
    m = re.search(r"const DATA = (\{.*?\});", exported_html, re.S)
    assert m is not None, "HTML 內找不到 DATA 變數"
    return json.loads(m.group(1))


@pytest.fixture(scope="module")
def exported_shares_html(tmp_path_factory):
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    out_path = tmp_path_factory.mktemp("export_shares") / "animation_shares.html"
    export_mod.export(DB_PATH, out_path, top_n=20, mode="shares")
    return out_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exported_shares(exported_shares_html):
    m = re.search(r"const DATA = (\{.*?\});", exported_shares_html, re.S)
    assert m is not None, "HTML 內找不到 DATA 變數"
    return json.loads(m.group(1))


# ---- 金額口徑（預設，mode='value'）----

def test_export_has_top_n_sectors(exported):
    assert len(exported["sectors"]) == 20


def test_export_covers_all_weeks(exported):
    conn = sqlite3.connect(DB_PATH)
    n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM sector_flow_value_weekly").fetchone()[0]
    conn.close()
    assert len(exported["weeks"]) == n_weeks


def test_export_week_values_align_with_sectors(exported):
    for wk in exported["weeks"]:
        assert len(wk["v"]) == len(exported["sectors"])


def test_export_selects_most_active_sectors(exported):
    """驗證匯出的板塊確實是金額活動量最大的 20 個（半導體業/金融保險業應在其中，
    這兩個板塊在股數口徑下也是活動量最大的板塊，換算成金額後預期仍名列前茅）。"""
    assert "半導體業" in exported["sectors"]
    assert "金融保險業" in exported["sectors"]


def test_export_has_taiex_index_per_week(exported):
    """每週都要附上 TAIEX 走勢資料點，且不能是空陣列（會讓走勢圖整週空白）。"""
    for wk in exported["weeks"]:
        assert "idx" in wk
        assert len(wk["idx"]) > 0, f"week {wk['s']}~{wk['e']} 沒有任何 TAIEX 資料點"
        for point in wk["idx"]:
            assert "d" in point and "c" in point
            assert 5000 < point["c"] < 100000


def test_export_has_inflow_outflow_summary_elements(exported_html):
    """底部淨流入/淨流出/淨額合計列必須存在，且有註明合計範圍只是圖上的 top-N 板塊
    （不是全市場），避免使用者誤以為是全市場總計。"""
    assert 'id="sumIn"' in exported_html
    assert 'id="sumOut"' in exported_html
    assert 'id="sumNet"' in exported_html
    assert "非全市場" in exported_html


def test_export_x_axis_is_fixed_not_auto_scaling(exported_html, exported):
    """x 軸 min/max 必須是固定數字寫死在 Chart.js options 裡，不能讓 Chart.js 每週
    自動依當週資料縮放——否則 0 軸的像素位置會隨每週數值大小左右跑動，動畫會很難看懂。
    """
    m = re.search(r"x:\s*\{\s*min:\s*(-?\d+),\s*max:\s*(-?\d+)", exported_html)
    assert m is not None, "HTML 內找不到固定的 x 軸 min/max 設定"
    x_min, x_max = int(m.group(1)), int(m.group(2))

    all_values = [v for wk in exported["weeks"] for v in wk["v"]]
    assert x_min <= min(all_values), "x_min 沒有涵蓋實際資料的最小值，長條會被裁切"
    assert x_max >= max(all_values), "x_max 沒有涵蓋實際資料的最大值，長條會被裁切"


def test_export_value_mode_unit_is_yi_yuan(exported_html):
    """金額口徑的長條圖單位應標示「億元」，不是股數口徑的「億股」。"""
    assert "億元" in exported_html


def test_export_value_mode_coverage_note_reflects_actual_gap(exported_html):
    """收盤價覆蓋率若不是 100%，動畫頁面必須有一行誠實揭露的說明文字；若剛好是
    100%（TWSE/TPEx 皆全數涵蓋），則不強制要求這行文字存在（沒有缺口就不用特別提）。
    這裡只驗證『若頁面聲稱有缺口，用字要包含關鍵字』，涵蓋率本身由
    test_daily_prices.py 驗證。"""
    if "收盤價缺口" in exported_html:
        assert "未能完整換算金額" in exported_html


# ---- 股數口徑（--mode shares，第八輪舊行為回歸測試）----

def test_export_shares_mode_still_works(exported_shares):
    assert len(exported_shares["sectors"]) == 20
    assert "半導體業" in exported_shares["sectors"]


def test_export_shares_mode_unit_is_yi_gu(exported_shares_html):
    assert "億股" in exported_shares_html


def test_export_shares_mode_values_match_legacy_source_table(exported_shares):
    """股數口徑的數字必須跟 sector_flow_weekly 一致（回歸驗證：改成預設金額口徑後，
    舊的股數口徑邏輯本身完全沒有被動到）。"""
    conn = sqlite3.connect(DB_PATH)
    week0 = exported_shares["weeks"][0]
    sector = exported_shares["sectors"][0]
    idx = exported_shares["sectors"].index(sector)
    expected = conn.execute(
        "SELECT total_net FROM sector_flow_weekly WHERE industry_name=? AND week_index=0",
        (sector,),
    ).fetchone()[0]
    conn.close()
    assert week0["v"][idx] == round(expected / 100_000_000, 1)
