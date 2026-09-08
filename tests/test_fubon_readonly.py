from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from fubon_readonly import collect_snapshot, save_snapshot
from fubon_readonly_report import render_report


class Reply:
    def __init__(self, data, is_success=True):
        self.data = data
        self.is_success = is_success


class FakeAccounting:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def inventories(self, account):
        self.calls.append(account.account)
        reply = self.replies[account.account]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeStock:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def filled_history(self, account, start, end):
        self.calls.append((account.account, start, end))
        return self.replies[account.account]


def account(kind, number):
    return SimpleNamespace(account_type=kind, branch_no="001", account=number, name="秘密姓名")


def test_collect_keeps_stock_accounts_separate_and_preserves_odd_and_unknown_fees():
    a1, a2, future = account("stock", "A1"), account("stock", "A2"), account("futopt", "F1")
    inventory = SimpleNamespace(date="2026-09-01", stock_no="2330", order_type="Stock",
        lastday_qty=1000, today_qty=1000, tradable_qty=1000, buy_filled_qty=0,
        sell_filled_qty=0, odd=SimpleNamespace(lastday_qty=3, today_qty=3,
        tradable_qty=3, buy_filled_qty=0, sell_filled_qty=0))
    fill = SimpleNamespace(date="20260901", order_no="O1", stock_no="2330", buy_sell="Buy",
        filled_no="F1", filled_avg_price=600, filled_qty=1000, filled_price=600,
        order_type="Stock", filled_time="09:01:02")
    sdk = SimpleNamespace(
        accounting=FakeAccounting({"A1": Reply([inventory]), "A2": Reply([])}),
        stock=FakeStock({"A1": Reply([fill]), "A2": Reply([])}),
    )
    pauses = []
    result = collect_snapshot(sdk, [a1, future, a2], date.today() - timedelta(days=1), date.today(), pauses.append)
    assert result["status"] == "complete"
    assert len(result["accounts"]) == 2
    assert result["accounts"][0]["inventories"][0]["odd"]["today_qty"] == 3
    assert result["accounts"][0]["fills"][0]["fees"] is None
    assert result["accounts"][0]["fills"][0]["assignment"] == "unassigned"
    assert result["accounts"][1]["inventories"] == []
    assert sdk.accounting.calls == ["A1", "A2"]
    assert len(pauses) == 4 and pauses == [0.3] * 4
    serialized = str(result)
    assert "秘密姓名" not in serialized and "A1" not in serialized and "001" not in serialized


def test_collect_marks_partial_but_keeps_other_query_and_sanitizes_error():
    acc = account("stock", "A1")
    sdk = SimpleNamespace(
        accounting=FakeAccounting({"A1": RuntimeError("token=SECRET")}),
        stock=FakeStock({"A1": Reply([])}),
    )
    result = collect_snapshot(sdk, [acc], date.today(), date.today(), lambda _: None)
    assert result["status"] == result["accounts"][0]["status"] == "partial"
    assert result["accounts"][0]["fills"] == []
    assert result["errors"][0]["error_type"] == "RuntimeError"
    assert "SECRET" not in str(result)


def test_none_response_is_partial_but_empty_list_is_success():
    acc = account("stock", "A1")
    sdk = SimpleNamespace(accounting=FakeAccounting({"A1": Reply(None)}), stock=FakeStock({"A1": Reply([])}))
    result = collect_snapshot(sdk, [acc], date.today(), date.today(), lambda _: None)
    assert result["status"] == "partial"
    assert result["accounts"][0]["fills"] == []


@pytest.mark.parametrize("start,end", [
    (date.today() - timedelta(days=30), date.today()),
    (date.today(), date.today() - timedelta(days=1)),
    (date.today(), date.today() + timedelta(days=1)),
])
def test_invalid_ranges_are_rejected(start, end):
    with pytest.raises(ValueError, match="invalid_date_range"):
        collect_snapshot(SimpleNamespace(), [], start, end)


def test_thirty_calendar_days_are_allowed_and_no_stock_account_is_explicit():
    result = collect_snapshot(SimpleNamespace(), [account("futopt", "F1")],
                              date.today() - timedelta(days=29), date.today(), lambda _: None)
    assert result["status"] == "no_stock_accounts"


def test_save_snapshot_never_overwrites(tmp_path: Path):
    snapshot = {"schema_version": 1}
    first = save_snapshot(snapshot, tmp_path)
    second = save_snapshot(snapshot, tmp_path)
    assert first != second and first.exists() and second.exists()


def test_report_is_standalone_escaped_and_marks_unknown_partial_and_unassigned():
    attack = '<script>alert("x")</script>'
    snapshot = {
        "schema_version": 1, "fetched_at": attack, "start": "2026-09-01", "end": "2026-09-02",
        "status": "partial", "strategy_imported": False,
        "accounts": [{"account_ref": attack, "status": "partial", "inventories": [{
            "date": None, "stock_no": attack, "order_type": None, "lastday_qty": None,
            "today_qty": 0, "tradable_qty": None, "buy_filled_qty": None,
            "sell_filled_qty": None, "odd": None}], "fills": [{
            "date": None, "order_no": attack, "stock_no": "2330", "buy_sell": "Buy",
            "filled_no": None, "filled_avg_price": None, "filled_qty": 1,
            "filled_price": 600, "order_type": None, "filled_time": None,
            "fees": None, "assignment": "unassigned"}]}],
        "errors": [{"account_ref": attack, "query": attack, "error_type": attack}],
    }
    html = render_report(snapshot)
    assert "<!doctype html>" in html and "zh-Hant" in html
    assert attack not in html and "&lt;script&gt;" in html
    assert "部分完成（不可視為成功）" in html
    assert "未知" in html and "待分類" in html and "費用與交易稅未知" in html
    assert "https://" not in html and "http://" not in html
    assert "account.json" not in html and "button" not in html.lower()


def test_report_complete_wording_and_mixed_missing_odd_remains_visible_as_unknown():
    snapshot = {
        "status": "complete",
        "accounts": [{
            "account_ref": "local-ref", "status": "complete", "fills": [],
            "inventories": [
                {"stock_no": "2330", "odd": {"today_qty": 7}},
                {"stock_no": "0050", "odd": None},
            ],
        }],
    }
    html = render_report(snapshot)
    assert html.count("查詢完成") == 2
    assert ">完整<" not in html
    odd_section = html.split("<h3>零股庫存</h3>", 1)[1].split("<h3>成交紀錄</h3>", 1)[0]
    assert "2330" in odd_section and ">7<" in odd_section
    assert "0050" in odd_section and "未知" in odd_section
