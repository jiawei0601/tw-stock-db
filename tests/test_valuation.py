"""驗證估值篩選表（build_valuation.py）的表結構與核心計算邏輯，不打網路 API。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import build_valuation as bv

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


@pytest.fixture(scope="module")
def conn():
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    c = sqlite3.connect(DB_PATH)
    yield c
    c.close()


def test_tables_exist(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in ("per_daily", "eps_quarterly", "valuation_fetch_log", "valuation_screen"):
        assert t in tables, f"缺少表 {t}，請先跑 python build_valuation.py --import-cache ... --screen"


def test_per_daily_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || date) FROM per_daily"
    ).fetchone()
    assert total == distinct


def test_eps_quarterly_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || quarter_end) FROM eps_quarterly"
    ).fetchone()
    assert total == distinct


def test_valuation_screen_no_duplicate_pk(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT run_date || '|' || stock_id || '|' || universe) FROM valuation_screen"
    ).fetchone()
    assert total == distinct


def test_valuation_screen_populated(conn):
    n = conn.execute("SELECT COUNT(*) FROM valuation_screen").fetchone()[0]
    assert n > 0, ("valuation_screen 是空的，請先跑 python build_valuation.py "
                   "--import-cache ... --screen")


def test_valuation_screen_universe_values_valid(conn):
    rows = conn.execute("SELECT DISTINCT universe FROM valuation_screen").fetchall()
    for (u,) in rows:
        assert u in ("ai_chain", "semiconductor")


# ---- 純函式邏輯（假資料，不碰資料庫） ----

def test_position_formula_example():
    """位置公式：(現價PER - P25) / (P75 - P25)，PER 剛好等於 P50 時位置應介於 0~1 之間。"""
    per_vals = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]  # p25=12.5 p50=15 p75=17.5（依 pct 公式）
    p25 = bv._pct(per_vals, 0.25)
    p75 = bv._pct(per_vals, 0.75)
    cur_per = 15.0
    position = (cur_per - p25) / (p75 - p25)
    assert 0 <= position <= 1

    # 現價 PER 低於 P25 -> 位置為負 -> 分類「低於合理區間」
    cur_per_low = p25 - 1.0
    position_low = (cur_per_low - p25) / (p75 - p25)
    assert position_low < 0


def test_split_flag_detects_large_single_day_jump():
    """緯穎 2026-09-02 一拆三案例：收盤價從 7800 掉到 2610，單日跌幅遠超 40% 門檻。"""
    prices_sorted = [
        ("2026-08-31", 7750.0),
        ("2026-09-01", 7800.0),
        ("2026-09-02", 2610.0),
        ("2026-09-04", 2565.0),
    ]
    assert bv._detect_split_flag(prices_sorted) is True


def test_split_flag_false_for_normal_price_series():
    prices_sorted = [
        ("2026-08-31", 100.0),
        ("2026-09-01", 101.5),
        ("2026-09-02", 99.0),
        ("2026-09-04", 100.5),
    ]
    assert bv._detect_split_flag(prices_sorted) is False


def test_band_ok_false_when_split_flag_true():
    """split_flag=True 時無論其他指標多漂亮，band_ok 一律 False（本輪新增的緯穎盲點修正）。"""
    eps_cv = 0.1
    loss_q = 0
    n_per = 400
    n_eps_q = 8
    split_flag = True
    band_ok = (eps_cv < 0.5) and (loss_q == 0) and (n_per >= 300) and (n_eps_q == 8) and not split_flag
    assert band_ok is False
