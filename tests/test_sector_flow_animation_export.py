"""驗證 export_sector_flow_animation.py 匯出的靜態 HTML 動畫內嵌資料正確。"""
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
    export_mod.export(DB_PATH, out_path, top_n=20)
    return out_path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def exported(exported_html):
    m = re.search(r"const DATA = (\{.*?\});", exported_html, re.S)
    assert m is not None, "HTML 內找不到 DATA 變數"
    return json.loads(m.group(1))


def test_export_has_top_n_sectors(exported):
    assert len(exported["sectors"]) == 20


def test_export_covers_all_weeks(exported):
    conn = sqlite3.connect(DB_PATH)
    n_weeks = conn.execute("SELECT COUNT(DISTINCT week_index) FROM sector_flow_weekly").fetchone()[0]
    conn.close()
    assert len(exported["weeks"]) == n_weeks


def test_export_week_values_align_with_sectors(exported):
    for wk in exported["weeks"]:
        assert len(wk["v"]) == len(exported["sectors"])


def test_export_selects_most_active_sectors(exported):
    """驗證匯出的板塊確實是活動量最大的 20 個（半導體業/金融保險業等應在其中）。"""
    assert "半導體業" in exported["sectors"]
    assert "金融保險業" in exported["sectors"]


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
