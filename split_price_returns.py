"""分割還原價的持有報酬；價格建置時已套用事件，不再次調整。"""

def holding_return_from_split_prices(price_rows_sorted, entry_date, exit_date):
    prices = [(d, p) for d, p in price_rows_sorted if entry_date <= d <= exit_date and p is not None and p > 0]
    if len(prices) < 2:
        return None, "insufficient_data"
    reason = "normal" if prices[-1][0] == exit_date else "last_available"
    return prices[-1][1] / prices[0][1] - 1.0, reason
