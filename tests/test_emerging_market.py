from copy import deepcopy

import pytest

from emerging_market import (
    MarketExecution,
    MarketUniverse,
    additional_price_ids,
    prepare_prices,
)
from tests.test_dynamic_momentum import row
from weekly_hermes_momentum import simulate_weekly


def interval(stock_id, market, start, end=None):
    return {
        "stock_id": stock_id,
        "market": market,
        "start": start,
        "end": end,
        "source": "official-test-fixture",
    }


def test_market_intervals_are_start_inclusive_end_exclusive_and_transfer_continuously():
    universe = MarketUniverse(
        [
            interval("A", "EMERGING", "2020-01-02", "2020-01-10"),
            interval("A", "TWSE", "2020-01-10"),
        ]
    )

    assert universe.market_at("A", "2020-01-01") is None
    assert universe.market_at("A", "2020-01-02") == "EMERGING"
    assert universe.market_at("A", "2020-01-09") == "EMERGING"
    assert universe.market_at("A", "2020-01-10") == "TWSE"


def test_overlapping_market_intervals_are_rejected():
    with pytest.raises(ValueError, match="overlapping market intervals"):
        MarketUniverse(
            [
                interval("A", "EMERGING", "2020-01-02", "2020-01-11"),
                interval("A", "TWSE", "2020-01-10"),
            ]
        )


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError, match="require evidenced"):
        MarketUniverse([interval("A", "UNKNOWN", "2020-01-02")])


def test_additional_price_ids_uses_200_day_boundary_and_sums_market_intervals():
    from datetime import date, timedelta

    first = date(2020, 1, 1)
    calendar = [(first + timedelta(days=offset)).isoformat() for offset in range(210)]
    payload = {
        "records": [
            interval("A", "EMERGING", calendar[0], calendar[200]),
            interval("B", "EMERGING", calendar[0], calendar[100]),
            interval("B", "TWSE", calendar[105], calendar[205]),
            interval("C", "EMERGING", calendar[0], calendar[199]),
            interval("D", "TPEX", calendar[0]),
            interval("OUTSIDE", "EMERGING", "2021-01-01"),
        ]
    }

    required, skipped = additional_price_ids(
        payload,
        {"D"},
        calendar,
        calendar[0],
        calendar[-1],
    )

    assert required == ["A", "B"]
    assert skipped == {"C": 199}


def test_prepare_prices_does_not_mutate_input_and_clears_only_emerging_open():
    universe = MarketUniverse(
        [
            interval("E", "EMERGING", "2020-01-02"),
            interval("L", "TPEX", "2020-01-02"),
        ]
    )
    raw = {
        "E": {
            "2020-01-02": {
                "open": 150.0,
                "close": 101.0,
                "min": 99.0,
                "max": 102.0,
                "Trading_Volume": 10,
                "Trading_money": 1_005.0,
            }
        },
        "L": {"2020-01-02": {"open": 88.0, "close": 89.0}},
        "UNKNOWN": {"2020-01-02": {"open": 77.0, "close": 78.0}},
    }
    original = deepcopy(raw)

    prepared = prepare_prices(raw, universe)

    assert raw == original
    assert prepared["E"]["2020-01-02"]["open"] == 0
    assert prepared["E"]["2020-01-02"]["max"] == 102.0
    assert prepared["L"]["2020-01-02"]["open"] == 88.0
    assert prepared.get("UNKNOWN", {}) == {}


def emerging_execution(*, slippage=0.0, allow_emerging=True, participation=0.01):
    universe = MarketUniverse([interval("E", "EMERGING", "2020-01-02")])
    return MarketExecution(
        universe,
        allow_emerging=allow_emerging,
        slippage=slippage,
        participation=participation,
    )


def test_emerging_execution_uses_turnover_vwap_and_never_open():
    execution = emerging_execution()
    market_row = {
        "open": 999.0,
        "min": 100.0,
        "max": 110.0,
        "Trading_Volume": 10,
        "Trading_money": 1_050.0,
    }

    assert execution("E", "2020-01-02", market_row, "buy") == 105.0
    assert execution.audit[-1]["reason"] == "available"


@pytest.mark.parametrize(
    ("market_row", "reason"),
    [
        (
            {
                "open": 100.0,
                "min": 90.0,
                "max": 110.0,
                "Trading_Volume": 0,
                "Trading_money": 1_000.0,
            },
            "no_turnover",
        ),
        (
            {
                "open": 100.0,
                "min": 90.0,
                "max": 110.0,
                "Trading_Volume": 10,
                "Trading_money": 2_000.0,
            },
            "vwap_outside_range",
        ),
    ],
)
def test_emerging_execution_rejects_missing_turnover_and_impossible_vwap(
    market_row, reason
):
    execution = emerging_execution()

    assert execution("E", "2020-01-02", market_row, "sell") == 0
    assert execution.audit[-1]["reason"] == reason


def test_emerging_slippage_has_opposite_buy_and_sell_signs():
    execution = emerging_execution(slippage=0.01)
    market_row = {
        "open": 999.0,
        "min": 90.0,
        "max": 110.0,
        "Trading_Volume": 10,
        "Trading_money": 1_000.0,
    }

    assert execution("E", "2020-01-02", market_row, "buy") == 101.0
    assert execution("E", "2020-01-02", market_row, "sell") == 99.0


@pytest.mark.parametrize("market", ["TWSE", "TPEX"])
def test_listed_execution_preserves_open_price(market):
    universe = MarketUniverse([interval("L", market, "2020-01-02")])
    execution = MarketExecution(universe, slippage=0.5)

    assert execution("L", "2020-01-02", {"open": 87.5}, "buy") == 87.5
    assert execution("L", "2020-01-02", {"open": 87.5}, "sell") == 87.5


def test_allow_emerging_false_rejects_otherwise_valid_vwap():
    execution = emerging_execution(allow_emerging=False)
    market_row = {
        "open": 999.0,
        "min": 90.0,
        "max": 110.0,
        "Trading_Volume": 10,
        "Trading_money": 1_000.0,
    }

    assert execution("E", "2020-01-02", market_row, "buy") == 0
    assert execution.audit[-1]["reason"] == "ineligible_market"


@pytest.mark.parametrize("participation", [0, -0.01, 1.01, float("nan")])
def test_invalid_emerging_participation_is_rejected(participation):
    with pytest.raises(ValueError, match="invalid participation"):
        emerging_execution(participation=participation)


def test_emerging_capacity_rejects_orders_above_one_percent_daily_volume():
    execution = emerging_execution()
    market_row = {"Trading_Volume": 1_000}

    assert execution.capacity("E", "2020-01-02", market_row, "buy", 10)
    assert not execution.capacity("E", "2020-01-02", market_row, "buy", 10.001)
    assert execution.capacity_audit[-1]["passed"] is False


@pytest.mark.parametrize("market", ["TWSE", "TPEX"])
def test_listed_capacity_keeps_existing_unlimited_execution_assumption(market):
    universe = MarketUniverse([interval("L", market, "2020-01-02")])
    execution = MarketExecution(universe)

    assert execution.capacity(
        "L", "2020-01-02", {"Trading_Volume": 1}, "sell", 1_000_000
    )


def execution_scenario(calendar, execution_capacity=None):
    prices = {
        "A": {
            day: {
                "open": 500.0,
                "close": 70.0 if day == "2020-01-09" else 100.0,
                "Trading_Volume": 1,
            }
            for day in calendar
        }
    }
    tables = {
        "2019-11-29": {"A": row(0.1)},
        "2019-12-31": {"A": row(0.1)},
        "2020-01-08": {"A": row(0.2)},
    }
    calls = []

    def execution_price(stock_id, day, market_row, side):
        calls.append((stock_id, day, side))
        return 80.0 if side == "buy" else 120.0

    result = simulate_weekly(
        prices,
        {},
        calendar,
        tables,
        False,
        execution_price=execution_price,
        execution_capacity=execution_capacity,
    )
    return result, calls


def test_engine_uses_callback_for_buy_cost_and_sell_proceeds_on_later_sessions():
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
        "2020-01-10",
    ]
    result, calls = execution_scenario(calendar)
    nav, trips, legs, orders, held, pending = result

    assert calls == [
        ("A", "2020-01-09", "buy"),
        ("A", "2020-01-10", "sell"),
    ]
    assert orders == [
        {"date": "2020-01-09", "stock_id": "A", "filled": True, "budget": 100_000.0}
    ]
    assert not held and not pending
    assert legs[0]["signal_date"] == "2020-01-09"
    assert legs[0]["exit"] == "2020-01-10"
    assert legs[0]["exit_open"] == 120.0
    expected_proceeds = (100_000.0 / (80.0 * 1.003)) * 120.0 * 0.997
    assert trips[0]["cost"] == 100_000.0
    assert trips[0]["proceeds"] == pytest.approx(expected_proceeds)
    assert nav[-1]["cash"] == pytest.approx(900_000.0 + expected_proceeds)


def test_future_sell_does_not_change_signal_day_prefix_or_fill_on_signal_day():
    prefix = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
    ]
    full_result, _ = execution_scenario(prefix + ["2020-01-10"])
    prefix_result, prefix_calls = execution_scenario(prefix)

    assert prefix_calls == [("A", "2020-01-09", "buy")]
    assert not prefix_result[2]
    assert prefix_result[5]["A"]["signal"] == "2020-01-09"
    assert full_result[0][:-1] == prefix_result[0]


def test_capacity_rejection_cancels_buy_instead_of_partially_filling():
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
        "2020-01-10",
    ]
    capacity_calls = []

    def no_capacity(stock_id, day, market_row, side, shares):
        capacity_calls.append((stock_id, day, side, shares))
        return False

    result, _ = execution_scenario(calendar, no_capacity)

    assert len(capacity_calls) == 1
    assert capacity_calls[0][:3] == ("A", "2020-01-09", "buy")
    assert result[3][0]["filled"] is False
    assert not result[4] and not result[5]


def test_capacity_rejected_sell_stays_pending_and_retries_next_session():
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
        "2020-01-10",
        "2020-01-13",
    ]
    capacity_calls = []

    def capacity(stock_id, day, market_row, side, shares):
        capacity_calls.append((stock_id, day, side))
        return side == "buy" or day == "2020-01-13"

    result, _ = execution_scenario(calendar, capacity)

    assert capacity_calls == [
        ("A", "2020-01-09", "buy"),
        ("A", "2020-01-10", "sell"),
        ("A", "2020-01-13", "sell"),
    ]
    assert result[2][0]["signal_date"] == "2020-01-09"
    assert result[2][0]["exit"] == "2020-01-13"
    assert not result[4] and not result[5]


def cash_chronology_scenario(sale_price, *, sale_at_close):
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
                "open": 100.0,
                "close": 80.0 if sid == "E" and day >= "2020-01-29" else 100.0,
                "Trading_Volume": 1_000_000,
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

    def execution_price(stock_id, day, market_row, side):
        if stock_id == "E" and side == "sell":
            return sale_price
        return 100.0

    result = simulate_weekly(
        prices,
        {},
        calendar,
        tables,
        False,
        equity_allocation=True,
        execution_price=execution_price,
        sale_cash_at_close=lambda stock_id, day: sale_at_close and stock_id == "E",
    )
    return result


def test_close_vwap_sale_cannot_fund_same_day_open_buy_but_enters_closing_nav():
    result = cash_chronology_scenario(120.0, sale_at_close=True)
    nav, trips, legs, orders, held, pending = result
    new_buy = next(order for order in orders if order["stock_id"] == "L")
    proceeds = legs[0]["proceeds"]

    assert new_buy["date"] == "2020-01-30"
    assert new_buy["filled"] is False
    assert not held and not pending
    assert trips[0]["proceeds"] == pytest.approx(proceeds)
    assert nav[-1]["cash"] == pytest.approx(proceeds)
    assert nav[-1]["nav_stale"] == pytest.approx(proceeds)


def test_listed_open_sale_cash_remains_available_to_same_day_buy():
    result = cash_chronology_scenario(120.0, sale_at_close=False)
    nav, trips, legs, orders, held, pending = result
    new_buy = next(order for order in orders if order["stock_id"] == "L")

    assert new_buy["filled"] is True
    assert set(held) == {"L"}
    assert not pending
    assert nav[-1]["cash"] == pytest.approx(legs[0]["proceeds"] - new_buy["budget"])


def test_future_whole_day_vwap_cannot_flip_morning_buy_fill_decision():
    low_sale = cash_chronology_scenario(50.0, sale_at_close=True)
    high_sale = cash_chronology_scenario(500.0, sale_at_close=True)
    low_buy = next(order for order in low_sale[3] if order["stock_id"] == "L")
    high_buy = next(order for order in high_sale[3] if order["stock_id"] == "L")

    assert low_buy == high_buy
    assert low_buy["filled"] is False
    assert low_sale[0][-1]["cash"] < high_sale[0][-1]["cash"]


def buy_reservation_scenario(emerging_volume, *, reserve):
    calendar = [
        "2019-11-29",
        "2019-12-31",
        "2020-01-08",
        "2020-01-09",
        "2020-01-15",
        "2020-01-16",
        "2020-01-17",
    ]
    prices = {
        sid: {
            day: {
                "open": 0.0 if sid == "E" else 100.0,
                "close": 100.3
                if sid == "H" and day >= "2020-01-09"
                else 100.0,
                "min": 99.0,
                "max": 101.0,
                "Trading_Volume": emerging_volume if sid == "E" else 1,
                "Trading_money": emerging_volume * 100.0
                if sid == "E"
                else 100.0,
            }
            for day in calendar
        }
        for sid in ("H", "E", "L")
    }
    tables = {
        "2019-11-29": {sid: row(0.1) for sid in prices},
        "2019-12-31": {sid: row(0.1) for sid in prices},
        "2020-01-08": {"H": row(0.2)},
        "2020-01-15": {
            "E": row(0.4),
            "L": row(0.3),
            "H": row(0.2),
        },
    }
    universe = MarketUniverse(
        [
            interval("H", "TWSE", "2019-01-01"),
            interval("E", "EMERGING", "2019-01-01"),
            interval("L", "TPEX", "2019-01-01"),
        ]
    )
    execution = MarketExecution(universe)
    result = simulate_weekly(
        prices,
        {},
        calendar,
        tables,
        False,
        equity_allocation=True,
        position_weight=0.5,
        execution_price=execution,
        execution_capacity=execution.capacity,
        reserve_buy_cash_until_close=(
            (lambda stock_id, day: universe.market_at(stock_id, day) == "EMERGING")
            if reserve
            else None
        ),
    )
    return result


def test_rejected_emerging_buy_keeps_priority_cash_reserved_until_close():
    accepted = buy_reservation_scenario(1_000_000, reserve=True)
    rejected = buy_reservation_scenario(100_000, reserve=True)
    accepted_orders = {
        order["stock_id"]: order for order in accepted[3] if order["date"] == "2020-01-16"
    }
    rejected_orders = {
        order["stock_id"]: order for order in rejected[3] if order["date"] == "2020-01-16"
    }

    assert accepted_orders["E"]["filled"] is True
    assert rejected_orders["E"]["filled"] is False
    assert accepted_orders["L"]["filled"] is False
    assert rejected_orders["L"]["filled"] is False
    assert accepted[0][-2]["cash"] == 0
    assert rejected[0][-2]["cash"] == 500_000
    assert rejected[0][-1]["cash"] == 500_000
    assert set(rejected[4]) == {"H"}


def test_no_reservation_hook_preserves_existing_same_day_cash_behavior():
    result = buy_reservation_scenario(100_000, reserve=False)
    orders = {
        order["stock_id"]: order for order in result[3] if order["date"] == "2020-01-16"
    }

    assert orders["E"]["filled"] is False
    assert orders["L"]["filled"] is True
    assert result[0][-1]["cash"] == 0
    assert set(result[4]) == {"H", "L"}
