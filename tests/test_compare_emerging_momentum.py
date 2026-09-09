import json
import sqlite3
from datetime import date, timedelta

import pytest

import compare_emerging_momentum as comparison
from compare_emerging_momentum import make_tables, run_case
from emerging_market import MarketUniverse
from listed_universe import ListedUniverse
from momentum_entry_filters import TechnicalFeatures
from tests.test_dynamic_momentum import row


def interval(stock_id, market, start, end=None):
    return {
        "stock_id": stock_id,
        "market": market,
        "start": start,
        "end": end,
        "source": "official-test-fixture",
    }


def weekdays(start, end):
    current = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    result = []
    while current <= stop:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def ranking_fixture():
    calendar = weekdays("2019-01-01", "2020-02-28")
    prices = {
        sid: {
            day: {
                "open": 100.0,
                "close": 100.0,
                "Trading_Volume": 1_000,
                "Trading_money": 200_000_000,
            }
            for day in calendar
        }
        for sid in ("E", "T", "L")
    }
    for sid, close in (("E", 150.0), ("T", 120.0), ("L", 110.0)):
        prices[sid]["2019-12-31"]["close"] = close
    for sid in prices:
        for day in calendar:
            if day > "2020-01-08":
                prices[sid][day]["close"] = 10_000.0
    universe = MarketUniverse(
        [
            interval("E", "EMERGING", "2019-01-01"),
            interval("T", "EMERGING", "2019-01-01", "2020-01-01"),
            interval("T", "TWSE", "2020-01-01"),
            interval("L", "TPEX", "2019-01-01"),
        ]
    )
    return prices, calendar, universe


def test_b_ranks_only_listed_stocks_but_uses_their_verified_emerging_history():
    prices, calendar, universe = ranking_fixture()
    signal_day = "2020-01-08"

    b_tables, _ = make_tables(
        prices, {}, calendar, universe, "B", scheduled_days={signal_day}
    )
    c_tables, _ = make_tables(
        prices, {}, calendar, universe, "C", scheduled_days={signal_day}
    )
    listed = ListedUniverse(
        [
            interval("T", "TWSE", "2020-01-01"),
            interval("L", "TPEX", "2019-01-01"),
        ]
    )
    a_prices = listed.restrict_prices(prices)
    a_tables, _ = make_tables(
        a_prices, {}, calendar, universe, "A", scheduled_days={signal_day}
    )

    assert list(c_tables[signal_day]) == ["E", "T", "L"]
    assert list(b_tables[signal_day]) == ["T", "L"]
    assert b_tables[signal_day]["T"]["rank"] == 1
    assert b_tables[signal_day]["T"]["top"] is True
    assert "T" not in a_tables[signal_day]


def test_table_at_signal_day_is_invariant_to_future_prices_and_calendar():
    prices, calendar, universe = ranking_fixture()
    signal_day = "2020-01-08"
    full, _ = make_tables(
        prices, {}, calendar, universe, "B", scheduled_days={signal_day}
    )
    prefix_calendar = [day for day in calendar if day <= signal_day]
    prefix_prices = {
        sid: {day: market_row for day, market_row in rows.items() if day <= signal_day}
        for sid, rows in prices.items()
    }
    prefix, _ = make_tables(
        prefix_prices,
        {},
        prefix_calendar,
        universe,
        "B",
        scheduled_days={signal_day},
    )

    assert full == prefix


def test_missing_revenue_coverage_fails_closed_before_order_creation():
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
    ]
    prices = {
        "A": {
            day: {
                "open": 100.0,
                "close": 100.0,
                "max": 100.0,
                "Trading_Volume": 1_000_000,
            }
            for day in calendar
        }
    }
    tables = {
        "2019-11-29": {"A": row(0.1)},
        "2019-12-31": {"A": row(0.1)},
        "2020-01-08": {"A": row(0.2)},
    }
    universe = MarketUniverse([interval("A", "TWSE", "2019-01-01")])
    features = TechnicalFeatures(prices, {}, calendar, {})
    settings = {
        "trailing": False,
        "equity_allocation": True,
        "position_weight": 0.05,
        "signal_weekday": 2,
        "roll_holidays": True,
    }

    result, summary, execution, gate_audit = run_case(
        prices,
        {},
        calendar,
        tables,
        features,
        {},
        universe,
        "B",
        "base",
        15,
        0.0,
        settings,
    )

    assert not result[3] and not result[4]
    assert summary["closed_roundtrips"] == 0
    assert not execution
    assert gate_audit[0]["reason"] == "missing_or_conflicting_month"
    assert gate_audit[0]["passed"] is False


def test_c_runner_reserves_rejected_emerging_budget_past_same_day_listed_buy():
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
    ]
    prices = {
        sid: {
            day: {
                "open": 0.0 if sid == "E" else 100.0,
                "close": 100.0,
                "min": 99.0,
                "max": 101.0,
                "Trading_Volume": 1_000 if sid == "E" else 1,
                "Trading_money": 100_000.0 if sid == "E" else 100.0,
            }
            for day in calendar
        }
        for sid in ("E", "L")
    }
    tables = {
        "2019-11-29": {"E": row(0.1), "L": row(0.1)},
        "2019-12-31": {"E": row(0.1), "L": row(0.1)},
        "2020-01-08": {"E": row(0.2), "L": row(0.15)},
    }
    universe = MarketUniverse(
        [
            interval("E", "EMERGING", "2019-01-01"),
            interval("L", "TWSE", "2019-01-01"),
        ]
    )
    settings = {
        "trailing": False,
        "equity_allocation": True,
        "position_weight": 1.0,
        "signal_weekday": 2,
        "roll_holidays": True,
    }

    result, summary, execution, gate_audit = run_case(
        prices,
        {},
        calendar,
        tables,
        TechnicalFeatures(prices, {}, calendar, {}),
        {},
        universe,
        "C",
        "momentum",
        None,
        0.0,
        settings,
    )

    assert [(order["stock_id"], order["filled"]) for order in result[3]] == [
        ("E", False),
        ("L", False),
    ]
    assert not result[4]
    assert result[0][-1]["cash"] == 1_000_000
    assert execution[0]["price"] == 100.0
    assert gate_audit == []
    assert summary["emerging_max_daily_volume_participation"] == 0.01
    assert len(summary["capacity_rejections"]) == 1
    assert summary["capacity_rejections"][0]["side"] == "buy"


def test_c_runner_does_not_fund_open_buy_with_same_day_emerging_vwap_sale():
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
        "2020-01-29",
        "2020-01-30",
    ]
    prices = {
        sid: {
            day: {
                "open": 0.0 if sid == "E" else 100.0,
                "close": 80.0 if sid == "E" and day >= "2020-01-29" else 100.0,
                "min": 70.0,
                "max": 130.0,
                "Trading_Volume": 2_000_000,
                "Trading_money": 240_000_000.0
                if sid == "E" and day == "2020-01-30"
                else 200_000_000.0,
            }
            for day in calendar
        }
        for sid in ("E", "L")
    }
    tables = {
        "2019-11-29": {"E": row(0.1), "L": row(0.1)},
        "2019-12-31": {"E": row(0.1), "L": row(0.1)},
        "2020-01-08": {"E": row(0.2)},
        "2020-01-29": {"E": row(0.2), "L": row(0.3)},
    }
    universe = MarketUniverse(
        [
            interval("E", "EMERGING", "2019-01-01"),
            interval("L", "TWSE", "2019-01-01"),
        ]
    )
    settings = {
        "trailing": False,
        "equity_allocation": True,
        "signal_weekday": 2,
        "roll_holidays": True,
    }

    result, summary, execution, gate_audit = run_case(
        prices,
        {},
        calendar,
        tables,
        TechnicalFeatures(prices, {}, calendar, {}),
        {},
        universe,
        "C",
        "momentum",
        None,
        0.0,
        settings,
    )
    nav, trips, legs, orders, held, pending = result
    listed_buy = next(order for order in orders if order["stock_id"] == "L")

    assert listed_buy["date"] == "2020-01-30"
    assert listed_buy["filled"] is False
    assert not held and not pending
    assert legs[0]["exit_open"] == 120.0
    assert nav[-1]["cash"] == pytest.approx(legs[0]["proceeds"])
    assert nav[-1]["nav_stale"] == pytest.approx(legs[0]["proceeds"])
    assert summary["buy_market_counts"] == {"EMERGING": 1}
    assert gate_audit == []


def test_load_inputs_rejects_a_missing_required_price_download(tmp_path, monkeypatch):
    calendar = weekdays("2019-01-01", "2020-01-31")
    root = tmp_path / "emerging_universe"
    root.mkdir()
    payload = {
        "universe_kind": "TWSE_TPEX_EMERGING_EFFECTIVE_INTERVALS",
        "records": [interval("E", "EMERGING", calendar[0])],
    }
    (root / "intervals.json").write_text(json.dumps(payload), encoding="utf-8")
    with sqlite3.connect(root / "finmind_extra.db") as connection:
        connection.execute(
            "CREATE TABLE responses "
            "(query TEXT PRIMARY KEY, body TEXT, sha256 TEXT, status INTEGER)"
        )
    data_root = tmp_path / "data" / "momentum_pit"
    data_root.mkdir(parents=True)
    (data_root / "manifest.json").write_text(
        json.dumps(
            {"start": calendar[0], "end": calendar[-1], "candidate_ids": []}
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(comparison, "ROOT", root)
    monkeypatch.setattr(
        comparison,
        "load_data",
        lambda: ({}, {}, calendar, {}, "base-fingerprint", []),
    )

    with pytest.raises(ValueError, match=r"Price downloads incomplete: 1: \['E'\]"):
        comparison.load_inputs()
