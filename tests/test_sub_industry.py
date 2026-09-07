"""驗證產業鏈子產業分類表（build_sub_industry.py / stock_sub_industry）不打網路 API。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import build_sub_industry as bsi

DB_PATH = Path(__file__).parent.parent / "data" / "tw_stocks.db"


@pytest.fixture(scope="module")
def conn():
    if not DB_PATH.exists():
        pytest.fail(f"{DB_PATH} 不存在，請先跑: python build_db.py")
    c = sqlite3.connect(DB_PATH)
    tables = {row[0] for row in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "stock_sub_industry" not in tables:
        pytest.fail("stock_sub_industry 不存在，請先跑: python build_sub_industry.py")
    yield c
    c.close()


def test_table_exists(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "stock_sub_industry" in tables


def test_pk_unique(conn):
    total, distinct = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT stock_id || '|' || node || '|' || source) FROM stock_sub_industry"
    ).fetchone()
    assert total == distinct


def test_semiconductor_sub_within_12_categories(conn):
    """半導體鏈（align_sub=True）的 sub 一律要落在固定 12 類之內，不可出現分類外字串。"""
    rows = conn.execute(
        "SELECT DISTINCT sub FROM stock_sub_industry WHERE chain = '半導體'"
    ).fetchall()
    subs = {r[0] for r in rows}
    assert subs, "半導體鏈完全沒有資料，請先跑 python build_sub_industry.py"
    assert subs.issubset(bsi.VALID_SUBS), f"出現不在 12 類固定分類內的 sub: {subs - bsi.VALID_SUBS}"


def test_known_stock_spot_check(conn):
    """抽查已知標的的子產業歸類：2330 台積電=晶圓代工、2454 聯發科=IC設計、
    3711 日月光投控=封測（比對 https://ic.tpex.org.tw/ 半導體產業鏈頁面實測結果）。"""
    def subs_of(sid: str) -> set[str]:
        rows = conn.execute(
            "SELECT sub FROM stock_sub_industry WHERE chain = '半導體' AND stock_id = ?",
            (sid,),
        ).fetchall()
        return {r[0] for r in rows}

    assert "晶圓代工" in subs_of("2330"), "2330 台積電應歸類為晶圓代工"
    assert "IC設計" in subs_of("2454"), "2454 聯發科應歸類為 IC設計"
    assert "封測" in subs_of("3711"), "3711 日月光投控應歸類為封測"


def test_node_to_sub_map_values_all_valid(conn):
    """NODE_TO_SUB 對照表本身的值也必須落在固定 12 類之內（防呆：對照表寫錯字）。"""
    assert set(bsi.NODE_TO_SUB.values()).issubset(bsi.VALID_SUBS)
