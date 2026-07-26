"""build_macro.py 的純函式單元測試 — 全部用假資料，不打網路。

涵蓋：YoY 計算正確性（月頻/季頻）、diff/spread 計算正確性、schema 建立、
INSERT OR REPLACE 冪等性。"""
from __future__ import annotations

import sqlite3

import build_macro


# ==================== YoY / diff / spread 計算正確性 ====================

def test_compute_yoy_monthly_basic():
    obs = [
        {"date": "2024-01-01", "value": 100.0},
        {"date": "2024-06-01", "value": 105.0},
        {"date": "2025-01-01", "value": 109.0},  # 對 2024-01-01：(109/100-1)*100 = 9.00
        {"date": "2025-06-01", "value": 110.25},  # 對 2024-06-01：(110.25/105-1)*100 = 5.00
    ]
    result = {o["date"]: o["value"] for o in build_macro.compute_yoy_monthly(obs)}
    # 前 12 個月沒有更早的資料可比對，不應出現在結果中
    assert "2024-01-01" not in result
    assert "2024-06-01" not in result
    assert result["2025-01-01"] == 9.0
    assert result["2025-06-01"] == 5.0


def test_compute_yoy_monthly_skips_zero_or_missing_prev():
    obs = [
        {"date": "2024-01-01", "value": 0.0},
        {"date": "2024-03-01", "value": 50.0},
        {"date": "2025-01-01", "value": 10.0},  # 前期是 0，跳過
        {"date": "2025-03-01", "value": 55.0},  # 前期 50 存在，應計算
        {"date": "2025-04-01", "value": 60.0},  # 前期 2024-04-01 不存在，跳過
    ]
    result = {o["date"]: o["value"] for o in build_macro.compute_yoy_monthly(obs)}
    assert "2025-01-01" not in result
    assert "2025-04-01" not in result
    assert result["2025-03-01"] == 10.0


def test_compute_yoy_quarterly_basic():
    # 季頻日期固定在每季第一個月（01/04/07/10）的第一天，往前推 4 季 = 往前推 12 個月
    obs = [
        {"date": "2023-01-01", "value": 200.0},
        {"date": "2023-04-01", "value": 202.0},
        {"date": "2024-01-01", "value": 210.0},  # (210/200-1)*100 = 5.00
        {"date": "2024-04-01", "value": 204.02},  # (204.02/202-1)*100 = 1.00
    ]
    result = {o["date"]: o["value"] for o in build_macro.compute_yoy_quarterly(obs)}
    assert "2023-01-01" not in result
    assert result["2024-01-01"] == 5.0
    assert result["2024-04-01"] == 1.0


def test_compute_diff():
    obs = [
        {"date": "2024-01-01", "value": 1000.0},
        {"date": "2024-02-01", "value": 1015.0},
        {"date": "2024-03-01", "value": 1010.0},
    ]
    result = {o["date"]: o["value"] for o in build_macro.compute_diff(obs)}
    assert "2024-01-01" not in result  # 第一筆沒有前期可比對
    assert result["2024-02-01"] == 15.0
    assert result["2024-03-01"] == -5.0


def test_compute_diff_handles_unsorted_input():
    # collector 理論上都回傳已排序資料，但 compute_diff 本身應自行排序，不依賴呼叫端
    obs = [
        {"date": "2024-03-01", "value": 1010.0},
        {"date": "2024-01-01", "value": 1000.0},
        {"date": "2024-02-01", "value": 1015.0},
    ]
    result = {o["date"]: o["value"] for o in build_macro.compute_diff(obs)}
    assert result["2024-02-01"] == 15.0
    assert result["2024-03-01"] == -5.0


def test_compute_spread():
    a = [
        {"date": "2024-01-01", "value": 10.0},
        {"date": "2024-02-01", "value": 12.0},
        {"date": "2024-03-01", "value": 14.0},
    ]
    b = [
        {"date": "2024-01-01", "value": 6.0},
        {"date": "2024-02-01", "value": 6.5},
        # 2024-03-01 缺，應被排除
    ]
    result = {o["date"]: o["value"] for o in build_macro.compute_spread(a, b)}
    assert result["2024-01-01"] == 4.0
    assert result["2024-02-01"] == 5.5
    assert "2024-03-01" not in result


# ==================== schema 建立 ====================

def test_schema_creates_expected_tables():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(build_macro.SCHEMA)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "macro_series" in tables
        assert "macro_observations" in tables
    finally:
        conn.close()


def test_schema_is_idempotent_to_rerun():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(build_macro.SCHEMA)
        conn.executescript(build_macro.SCHEMA)  # 重跑不該報錯（CREATE TABLE IF NOT EXISTS）
    finally:
        conn.close()


# ==================== write_series 冪等性（INSERT OR REPLACE） ====================

def _fresh_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(build_macro.SCHEMA)
    return conn


def test_write_series_inserts_metadata_and_observations():
    conn = _fresh_conn()
    try:
        obs = [{"date": "2024-01-01", "value": 1.0}, {"date": "2024-02-01", "value": 2.0}]
        n = build_macro.write_series(
            conn, series_id="test_series", name="測試序列", region="US", frequency="M",
            units="%", source="TEST", source_detail="unit-test", observations=obs, now="2026-01-01T00:00:00+00:00",
        )
        assert n == 2
        series_row = conn.execute(
            "SELECT name, region, frequency, units, source FROM macro_series WHERE series_id='test_series'"
        ).fetchone()
        assert series_row == ("測試序列", "US", "M", "%", "TEST")
        obs_count = conn.execute(
            "SELECT COUNT(*) FROM macro_observations WHERE series_id='test_series'"
        ).fetchone()[0]
        assert obs_count == 2
    finally:
        conn.close()


def test_write_series_rerun_is_idempotent_no_duplicates():
    conn = _fresh_conn()
    try:
        obs = [{"date": "2024-01-01", "value": 1.0}, {"date": "2024-02-01", "value": 2.0}]
        build_macro.write_series(
            conn, series_id="test_series", name="測試序列", region="US", frequency="M",
            units="%", source="TEST", source_detail="unit-test", observations=obs, now="2026-01-01T00:00:00+00:00",
        )
        # 重跑一次一模一樣的資料，列數不應該變多（INSERT OR REPLACE by PK）
        build_macro.write_series(
            conn, series_id="test_series", name="測試序列", region="US", frequency="M",
            units="%", source="TEST", source_detail="unit-test", observations=obs, now="2026-01-02T00:00:00+00:00",
        )
        series_count = conn.execute("SELECT COUNT(*) FROM macro_series WHERE series_id='test_series'").fetchone()[0]
        assert series_count == 1
        obs_count = conn.execute("SELECT COUNT(*) FROM macro_observations WHERE series_id='test_series'").fetchone()[0]
        assert obs_count == 2
    finally:
        conn.close()


def test_write_series_rerun_overwrites_changed_values():
    conn = _fresh_conn()
    try:
        obs_v1 = [{"date": "2024-01-01", "value": 1.0}]
        build_macro.write_series(
            conn, series_id="test_series", name="舊名稱", region="US", frequency="M",
            units="%", source="TEST", source_detail="v1", observations=obs_v1, now="2026-01-01T00:00:00+00:00",
        )
        obs_v2 = [{"date": "2024-01-01", "value": 99.0}]  # 同一天，數值改變
        build_macro.write_series(
            conn, series_id="test_series", name="新名稱", region="US", frequency="M",
            units="%", source="TEST", source_detail="v2", observations=obs_v2, now="2026-01-02T00:00:00+00:00",
        )
        value = conn.execute(
            "SELECT value FROM macro_observations WHERE series_id='test_series' AND date='2024-01-01'"
        ).fetchone()[0]
        assert value == 99.0
        name = conn.execute("SELECT name FROM macro_series WHERE series_id='test_series'").fetchone()[0]
        assert name == "新名稱"
    finally:
        conn.close()
