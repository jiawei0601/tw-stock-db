from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from backfill_entry_market_index import _coverage, load_market_prices, parse_fmtqik


def _artifact(records):
    return {
        "index_kind": "TAIEX_PRICE",
        "source": "TWSE FMTQIK",
        "start": "2020-01-01",
        "end": "2020-01-31",
        "fetched_at": "2026-09-09T00:00:00+00:00",
        "records": records,
        "coverage": {},
    }


def _write(path: Path, records) -> Path:
    path.write_text(json.dumps(_artifact(records)), encoding="utf-8")
    return path


def test_load_market_prices_accepts_valid_sorted_unique_records(tmp_path):
    path = _write(tmp_path / "prices.json", [
        {"date": "2020-01-02", "close": 12000},
        {"date": "2020-01-03", "close": 12010.5},
    ])
    assert load_market_prices(path) == {
        "2020-01-02": 12000.0,
        "2020-01-03": 12010.5,
    }


@pytest.mark.parametrize("bad", [0, -1, True, "12000", None, math.nan, math.inf])
def test_load_market_prices_rejects_non_positive_non_numeric_or_non_finite_close(tmp_path, bad):
    path = _write(tmp_path / "prices.json", [{"date": "2020-01-02", "close": bad}])
    with pytest.raises(ValueError, match="close"):
        load_market_prices(path)


def test_load_market_prices_rejects_duplicate_dates_instead_of_overwriting(tmp_path):
    path = _write(tmp_path / "prices.json", [
        {"date": "2020-01-02", "close": 12000},
        {"date": "2020-01-02", "close": 12001},
    ])
    with pytest.raises(ValueError, match="duplicate"):
        load_market_prices(path)


@pytest.mark.parametrize("date_value", ["2020-1-2", "2020/01/02", "not-a-date"])
def test_load_market_prices_rejects_noncanonical_dates(tmp_path, date_value):
    path = _write(tmp_path / "prices.json", [{"date": date_value, "close": 12000}])
    with pytest.raises(ValueError, match="date"):
        load_market_prices(path)


def test_load_market_prices_rejects_wrong_index_kind_and_unsorted_records(tmp_path):
    path = _write(tmp_path / "prices.json", [
        {"date": "2020-01-03", "close": 12001},
        {"date": "2020-01-02", "close": 12000},
    ])
    with pytest.raises(ValueError, match="sorted"):
        load_market_prices(path)
    payload = _artifact([])
    payload["index_kind"] = "TaiwanStockTotalReturnIndex"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="TAIEX_PRICE"):
        load_market_prices(path)


def test_parse_fmtqik_uses_weighted_price_index_field():
    payload = {
        "stat": "OK",
        "fields": ["日期", "成交股數", "發行量加權股價指數", "漲跌點數"],
        "data": [["109/01/02", "1,000", "12,100.48", "10.00"]],
    }
    assert parse_fmtqik(payload) == [{"date": "2020-01-02", "close": 12100.48}]


def test_parse_fmtqik_rejects_missing_price_index_field():
    payload = {"stat": "OK", "fields": ["日期", "報酬指數"], "data": []}
    with pytest.raises(ValueError, match="fields"):
        parse_fmtqik(payload)


def test_coverage_distinguishes_reference_ghost_date_from_effective_calendar():
    prices = {f"2020-01-{day:02d}": 100.0 + day for day in range(1, 31)}
    evidence = {
        "reference": [*prices, "2020-01-31"],
        "effective": list(prices),
        "excluded": ["2020-01-31"],
    }
    result = _coverage(prices, evidence, "2020-01-01", "2020-01-31", [], {})
    assert result["finmind_reference_dates_missing_official_price"] == ["2020-01-31"]
    assert result["effective_dates_missing_official_price"] == []
    assert result["effective_calendar_excluded_dates"] == ["2020-01-31"]
